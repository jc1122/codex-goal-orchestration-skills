#!/usr/bin/env python3
"""Create the mandatory prompt-audit packet for a prepared job bundle."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


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


def audit_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
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

Return only JSON matching `prompt-audit.schema.json`.
"""


def render_launch(repo_root: Path, primary_model: str, fallback_model: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(repo_root.as_posix())} rev-parse --show-toplevel >/dev/null

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

if run_model primary {shell_quote(primary_model)}; then
  exit 0
fi

if [ -s "$(pwd)/prompt-audit.json" ]; then
  exit 1
fi

run_model fallback {shell_quote(fallback_model)}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--fallback-model", default="gpt-5.4")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest = load_manifest(manifest_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt-audit.schema.json").write_text(
        json.dumps(audit_schema(), indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prompt.md").write_text(
        render_prompt(manifest_path, repo_root, manifest),
        encoding="utf-8",
    )
    launch = out_dir / "launch.sh"
    launch.write_text(
        render_launch(repo_root, args.model, args.fallback_model),
        encoding="utf-8",
    )
    os.chmod(launch, 0o755)
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
