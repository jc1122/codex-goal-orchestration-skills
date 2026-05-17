#!/usr/bin/env python3
"""Create the mandatory prompt-audit packet for a prepared job bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


AUDIT_MODEL = "gpt-5.5"
AUDIT_FALLBACK_MODEL = "gpt-5.4"


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
resolve_absolute_path = PATH_RULES.resolve_absolute_path
resolve = PATH_RULES.resolve
require_relative_path = PATH_RULES.require_relative_path


def resolve_bundle_path(base: Path, value: object, field: str) -> Path:
    return resolve(base, require_relative_path(value, field))


def resolve_repo_path(repo_root: Path, value: object, field: str) -> Path:
    return resolve(repo_root, require_relative_path(value, field))


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def exact_string_schema(value: str) -> dict:
    return {"type": "string", "const": value}


def audit_schema(manifest_path: Path, repo_root: Path) -> dict:
    nonempty_string = {"type": "string", "minLength": 1}
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
            "manifest": exact_string_schema(manifest_path.as_posix()),
            "repo_root": exact_string_schema(repo_root.as_posix()),
            "status": {"type": "string", "enum": ["pass", "failed", "blocked"]},
            "can_start": {"type": "boolean"},
            "checked_files": {"type": "array", "items": nonempty_string},
            "defects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file", "severity", "message"],
                    "properties": {
                        "file": nonempty_string,
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "message": nonempty_string,
                    },
                },
            },
            "missing_dod_items": {"type": "array", "items": nonempty_string},
            "actionability_verdict": nonempty_string,
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "summary": nonempty_string,
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
        work_items = branch.get("work_items", [])
        if isinstance(work_items, list):
            worker_packet_ids = ",".join(
                str(item.get("packet_id", "missing"))
                if isinstance(item, dict)
                else "invalid"
                for item in work_items
            )
        else:
            worker_packet_ids = "invalid"
        branch_lines.append(
            "- {id}: prompt={prompt}, branch={branch_name}, worktree={worktree}, status={status}, review={review}, max_active_worker_packets={max_workers}, worker_packets={worker_packets}, worker_packet_ids={worker_packet_ids}".format(
                id=branch.get("id", ""),
                prompt=resolve_bundle_path(base, branch.get("prompt", ""), "prompt").as_posix(),
                branch_name=branch.get("branch_name", ""),
                worktree=resolve_repo_path(repo_root, branch.get("worktree_path", ""), "worktree_path").as_posix(),
                status=resolve_bundle_path(base, branch.get("status_path", ""), "status_path").as_posix(),
                review=resolve_bundle_path(base, branch.get("review_path", ""), "review_path").as_posix(),
                max_workers=branch.get("max_active_worker_packets", "missing"),
                worker_packets=len(work_items) if isinstance(work_items, list) else "invalid",
                worker_packet_ids=worker_packet_ids,
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
- branch prompt paths, status paths, review paths, and worktree paths are unique and collision-free;
- every branch declares `max_active_worker_packets` from 1 to 4 and `worker_parallelism.parallelism_default=true`;
- every branch contains 1 to 4 work items with deterministic `packet_id` values in `<branch_id>-<work_item_id>` form, and branch prompts list those packet ids;
- branch prompts require parallel worker dispatch by default;
- `max_active_branch_agents` is present and <= 4;
- parallelism is the default and the manifest contains parallelization metadata;
- manifest artifact and cleanup policies are present, non-empty, and are repeated or honored by `main.prompt.md`;
- waves, when present, cover every branch exactly once, no wave exceeds `max_active_branch_agents`, no wave has more than 4 branches, and there are no more than 5 waves;
- single-branch or otherwise serialized plans include a serial reason or parallelization rationale;
- `main.prompt.md` defines a falsifiable top-level Definition of Done;
- every branch prompt defines bounded branch scope and falsifiable Definition of Done;
- prompts require manifest-bound `validate_branch_status.py` and manifest-bound `validate_main_status.py` before pass;
- branch prompts are actionable without chat history;
- prompt files do not require branch creation before audit;
- merge/cleanup behavior is explicit when expected;
- `main.prompt.md` requires closing finished branch orchestrator agents before launching replacements;
- unsupported, unresolved, negative, or probe-only claim labels are preserved.
- a `pass` audit must have `can_start=true`, no `critical` or `major` defects, and no missing DoD items.

Return only JSON matching `prompt-audit.schema.json`. The JSON must include `manifest` and `repo_root`
exactly as specified by the schema.
"""


def render_launch(repo_root: Path, manifest_path: Path) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(repo_root.as_posix())} rev-parse --show-toplevel >/dev/null

output_path="$(pwd)/prompt-audit.json"
rm -f "$output_path" "$(pwd)/events-primary.jsonl" "$(pwd)/events-fallback.jsonl"

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
def require_string_list(key, *, min_items=0):
    value = data[key]
    if not isinstance(value, list) or len(value) < min_items:
        raise SystemExit(1)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise SystemExit(1)


for key in ["checked_files", "missing_dod_items"]:
    require_string_list(key)
require_string_list("commands_run", min_items=1)
if not isinstance(data["defects"], list):
    raise SystemExit(1)
for item in data["defects"]:
    if not isinstance(item, dict):
        raise SystemExit(1)
    if not isinstance(item.get("file"), str) or not item["file"].strip():
        raise SystemExit(1)
    if item.get("severity") not in {"critical", "major", "minor"}:
        raise SystemExit(1)
    if not isinstance(item.get("message"), str) or not item["message"].strip():
        raise SystemExit(1)
for key in ["actionability_verdict", "summary"]:
    if not isinstance(data[key], str) or not data[key].strip():
        raise SystemExit(1)
if data["status"] == "pass":
    if data["can_start"] is not True or data["missing_dod_items"] or not data["checked_files"]:
        raise SystemExit(1)
    for item in data["defects"]:
        if item.get("severity") in {"critical", "major"}:
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
    "missing_dod_items": [
        "prompt audit did not produce a valid audit artifact",
        "Inspect audit event logs in this packet directory for the underlying CLI or schema error.",
    ],
    "actionability_verdict": "blocked",
    "commands_run": [
        "codex exec --ephemeral -m {AUDIT_MODEL} -C {repo_root.as_posix()} -s read-only --json --output-schema prompt-audit.schema.json -o prompt-audit.json",
        "codex exec --ephemeral -m {AUDIT_FALLBACK_MODEL} -C {repo_root.as_posix()} -s read-only --json --output-schema prompt-audit.schema.json -o prompt-audit.json",
    ],
    "summary": message + " Inspect audit event logs in this packet directory for the underlying CLI or schema error.",
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
