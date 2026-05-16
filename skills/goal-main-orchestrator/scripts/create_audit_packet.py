#!/usr/bin/env python3
"""Create the mandatory prompt-audit packet for a prepared job bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath


AUDIT_MODEL = "gpt-5.5"
AUDIT_FALLBACK_MODEL = "gpt-5.4"


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def resolve_absolute_path(value: str, field: str, *, must_exist: bool) -> Path:
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators: {value!r}")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise SystemExit(f"{field} must be an absolute path: {value!r}")
    if ".." in expanded.parts:
        raise SystemExit(f"{field} must not contain '..' traversal: {value!r}")
    if must_exist and not expanded.exists():
        raise SystemExit(f"{field} does not exist: {expanded}")
    return expanded.resolve(strict=must_exist)


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def require_relative_path(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{field} must be a non-empty relative path")
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators, not backslashes: {value!r}")
    if "//" in value:
        raise SystemExit(f"{field} must not contain empty path segments: {value!r}")
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        raise SystemExit(f"{field} must not contain '.' path segments: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise SystemExit(f"{field} must be relative, not absolute: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"{field} must not contain empty, '.', or '..' segments: {value!r}")
    return path.as_posix()


def resolve_bundle_path(base: Path, value: object, field: str) -> Path:
    return resolve(base, require_relative_path(value, field))


def resolve_repo_path(repo_root: Path, value: object, field: str) -> Path:
    return resolve(repo_root, require_relative_path(value, field))


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def audit_schema(manifest_path: Path, repo_root: Path) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "manifest",
            "repo_root",
            "status",
            "can_start",
            "checked_files",
            "defects",
            "missing_dod_items",
            "actionability_verdict",
            "commands_run",
            "summary",
        ],
        "properties": {
            "manifest": {"const": manifest_path.as_posix()},
            "repo_root": {"const": repo_root.as_posix()},
            "status": {"enum": ["pass", "failed", "blocked"]},
            "can_start": {"type": "boolean"},
            "checked_files": {"type": "array", "items": {"type": "string"}},
            "defects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file", "severity", "message"],
                    "properties": {
                        "file": {"type": "string"},
                        "severity": {"enum": ["critical", "major", "minor"]},
                        "message": {"type": "string"},
                    },
                },
            },
            "missing_dod_items": {"type": "array", "items": {"type": "string"}},
            "actionability_verdict": {"type": "string"},
            "commands_run": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    }


def render_prompt(manifest_path: Path, repo_root: Path, manifest: dict) -> str:
    base = manifest_path.parent
    main_prompt = resolve_bundle_path(base, manifest["main_prompt"], "main_prompt")
    branches = manifest.get("branches", [])
    max_active = manifest.get("max_active_branch_agents", "missing")
    waves = manifest.get("waves", [])
    branch_lines = []
    for branch in branches:
        branch_lines.append(
            "- {id}: prompt={prompt}, branch={branch_name}, worktree={worktree}, status={status}, review={review}".format(
                id=branch.get("id", ""),
                prompt=resolve_bundle_path(base, branch.get("prompt", ""), "prompt").as_posix(),
                branch_name=branch.get("branch_name", ""),
                worktree=resolve_repo_path(repo_root, branch.get("worktree_path", ""), "worktree_path").as_posix(),
                status=resolve_bundle_path(base, branch.get("status_path", ""), "status_path").as_posix(),
                review=resolve_bundle_path(base, branch.get("review_path", ""), "review_path").as_posix(),
            )
        )

    return f"""# Prompt Audit Packet

You are a read-only prompt auditor. Do not edit files. Do not create branches or worktrees.

Repository root: {repo_root}
Manifest: {manifest_path}
Main prompt: {main_prompt}

Branch prompt entries:
{os.linesep.join(branch_lines) if branch_lines else "- none"}

Max active branch agents: {max_active}
Waves:
{json.dumps(waves, indent=2)}

Run only non-mutating inspection commands as needed, starting with:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Audit `job.manifest.json`, `main.prompt.md`, and every listed branch prompt before any runtime orchestration starts.

Required checks:

- every listed file exists and is readable;
- manifest branch ids, branch names, worktree paths, status paths, and review paths are present;
- `max_active_branch_agents` is present and <= 5;
- waves, when present, cover every branch exactly once and no wave exceeds `max_active_branch_agents`;
- `main.prompt.md` defines a falsifiable top-level Definition of Done;
- every branch prompt defines bounded branch scope and falsifiable Definition of Done;
- branch prompts are actionable without chat history;
- prompt files do not require branch creation before audit;
- merge/cleanup behavior is explicit when expected;
- `main.prompt.md` requires closing finished branch orchestrator agents before launching replacements;
- unsupported, unresolved, negative, or probe-only claim labels are preserved.

Return only JSON matching `prompt-audit.schema.json`. The JSON must include `manifest` and `repo_root`
exactly as specified by the schema.
"""


def render_launch(repo_root: Path, manifest_path: Path) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(repo_root.as_posix())} rev-parse --show-toplevel >/dev/null

output_path="$(pwd)/prompt-audit.json"
rm -f "$output_path"

run_model() {{
  local label="$1"
  local model="$2"
  codex exec --ephemeral \\
    -m "$model" \\
    -C {shell_quote(repo_root.as_posix())} \\
    -s read-only \\
    --json \\
    --output-schema "$(pwd)/prompt-audit.schema.json" \\
    -o "$(pwd)/prompt-audit.json" \\
    - < "$(pwd)/prompt.md" \\
    > "$(pwd)/events-${{label}}.jsonl" 2>&1
}}

valid_audit() {{
  python3 - "$output_path" {shell_quote(manifest_path.as_posix())} {shell_quote(repo_root.as_posix())} <<'PY'
import json
import sys

path, manifest, repo_root = sys.argv[1:4]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    raise SystemExit(1)

required = [
    "manifest",
    "repo_root",
    "status",
    "can_start",
    "checked_files",
    "defects",
    "missing_dod_items",
    "actionability_verdict",
    "commands_run",
    "summary",
]
if any(key not in data for key in required):
    raise SystemExit(1)
if data["manifest"] != manifest or data["repo_root"] != repo_root:
    raise SystemExit(1)
if data["status"] not in {{"pass", "failed", "blocked"}}:
    raise SystemExit(1)
if not isinstance(data["can_start"], bool):
    raise SystemExit(1)
for key in ["checked_files", "defects", "missing_dod_items", "commands_run"]:
    if not isinstance(data[key], list):
        raise SystemExit(1)
for key in ["actionability_verdict", "summary"]:
    if not isinstance(data[key], str):
        raise SystemExit(1)
PY
}}

write_terminal_audit() {{
  local message="$1"
  python3 - "$output_path" {shell_quote(manifest_path.as_posix())} {shell_quote(repo_root.as_posix())} "$message" <<'PY'
import json
import sys

output_path, manifest, repo_root, message = sys.argv[1:5]
data = {{
    "manifest": manifest,
    "repo_root": repo_root,
    "status": "blocked",
    "can_start": False,
    "checked_files": [],
    "defects": [
        {{
            "file": "prompt-audit",
            "severity": "critical",
            "message": message,
        }}
    ],
    "missing_dod_items": ["prompt audit did not produce a valid audit artifact"],
    "actionability_verdict": "blocked",
    "commands_run": [
        "codex exec --ephemeral -m {AUDIT_MODEL} -C {repo_root.as_posix()} -s read-only --json --output-schema prompt-audit.schema.json -o prompt-audit.json",
        "codex exec --ephemeral -m {AUDIT_FALLBACK_MODEL} -C {repo_root.as_posix()} -s read-only --json --output-schema prompt-audit.schema.json -o prompt-audit.json",
    ],
    "summary": message,
}}
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\\n")
PY
}}

if run_model primary {shell_quote(AUDIT_MODEL)} && valid_audit; then
  exit 0
fi

if [ -s "$output_path" ] && valid_audit; then
  exit 1
fi

rm -f "$output_path"

if run_model fallback {shell_quote(AUDIT_FALLBACK_MODEL)} && valid_audit; then
  exit 0
fi

if [ -s "$output_path" ] && valid_audit; then
  exit 1
fi

write_terminal_audit "Prompt audit primary and fallback failed without producing a valid prompt-audit.json."
exit 1
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    manifest = load_manifest(manifest_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt-audit.schema.json").write_text(
        json.dumps(audit_schema(manifest_path, repo_root), indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prompt.md").write_text(
        render_prompt(manifest_path, repo_root, manifest),
        encoding="utf-8",
    )
    launch = out_dir / "launch.sh"
    launch.write_text(
        render_launch(repo_root, manifest_path),
        encoding="utf-8",
    )
    os.chmod(launch, 0o755)
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
