#!/usr/bin/env python3
"""Create the mandatory prompt-audit packet for a prepared job bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
shell_quote = CONTRACT.shell_quote
AUDIT_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.5"]
AUDIT_FALLBACK_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.4"]
AUDIT_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.AUDIT_ATTEMPT_TIMEOUT_SECONDS
TIMEOUT_KILL_AFTER_SECONDS = CONTRACT.TIMEOUT_KILL_AFTER_SECONDS


def audit_telemetry_attempts() -> list[dict]:
    return CONTRACT.codex_telemetry_attempts(
        ["gpt-5.5", "gpt-5.4"],
        timeout_seconds=AUDIT_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
        event_labels=["primary", "fallback"],
    )


def telemetry_function() -> str:
    script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    return CONTRACT.telemetry_shell_function(
        script_path=script,
        packet_dir_expr="$(pwd)",
        packet_id="prompt-audit",
        role="prompt-auditor",
        output_name="prompt-audit.json",
        prompt_name="prompt.md",
        attempts=audit_telemetry_attempts(),
    )


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


def archive_existing_packet_dir(packet_dir: Path, *, replace: bool) -> None:
    if not packet_dir.exists():
        return
    if packet_dir.is_dir() and not any(packet_dir.iterdir()):
        return
    if not replace:
        raise SystemExit(f"audit packet already exists; pass --replace to archive and recreate: {packet_dir}")
    attempts_dir = packet_dir / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    next_index = 1
    for child in sorted(attempts_dir.iterdir()):
        if child.is_dir() and child.name.startswith("attempt-"):
            suffix = child.name.removeprefix("attempt-")
            if suffix.isdigit():
                next_index = max(next_index, int(suffix) + 1)
    archive_dir = attempts_dir / f"attempt-{next_index:03d}"
    archive_dir.mkdir()
    for child in sorted(packet_dir.iterdir()):
        if child.name == "attempts":
            continue
        child.rename(archive_dir / child.name)


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
            worker_types = ",".join(
                str(item.get("worker_type", "worker"))
                if isinstance(item, dict)
                else "invalid"
                for item in work_items
            )
        else:
            worker_packet_ids = "invalid"
            worker_types = "invalid"
        branch_lines.append(
            "- {id}: prompt={prompt}, branch={branch_name}, worktree={worktree}, expected_status_output={status}, expected_review_output={review}, depends_on={depends_on}, max_active_worker_packets={max_workers}, worker_packets={worker_packets}, worker_packet_ids={worker_packet_ids}, worker_types={worker_types}".format(
                id=branch.get("id", ""),
                prompt=resolve_bundle_path(base, branch.get("prompt", ""), "prompt").as_posix(),
                branch_name=branch.get("branch_name", ""),
                worktree=resolve_repo_path(repo_root, branch.get("worktree_path", ""), "worktree_path").as_posix(),
                status=resolve_bundle_path(base, branch.get("status_path", ""), "status_path").as_posix(),
                review=resolve_bundle_path(base, branch.get("review_path", ""), "review_path").as_posix(),
                depends_on=",".join(branch.get("depends_on", [])) if isinstance(branch.get("depends_on", []), list) else "invalid",
                max_workers=branch.get("max_active_worker_packets", "missing"),
                worker_packets=len(work_items) if isinstance(work_items, list) else "invalid",
                worker_packet_ids=worker_packet_ids,
                worker_types=worker_types,
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
{json.dumps(waves, indent=2, sort_keys=True)}

Run only non-mutating inspection commands as needed, starting with:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Audit `job.manifest.json`, `main.prompt.md`, and every listed branch prompt before any runtime orchestration starts.

Do not emit the final JSON until after the inspection commands have completed and you have read the
manifest, main prompt, and listed branch prompts. If an intermediate thought or tool issue tempts you
to return early, continue the read-only inspection first; the last assistant message must be the only
final JSON object.

Required checks:

- every audit input file exists and is readable: `job.manifest.json`, `main.prompt.md`, and every branch prompt path;
- status paths, review paths, and worktree paths are expected runtime output/target paths and do not need to exist before prompt audit;
- manifest branch ids, branch names, worktree paths, status paths, and review paths are present;
- branch prompt paths, status paths, review paths, and worktree paths are unique and collision-free;
- every branch declares `max_active_worker_packets` from 1 to 4 and `worker_parallelism.parallelism_default=true`;
- every branch contains 1 to 4 work items with deterministic `packet_id` values in `<branch_id>-<work_item_id>` form, and branch prompts list those packet ids;
- work item `worker_type`, when present, is either `worker` or `research-worker`;
- when any work item is `research-worker`, the manifest includes a research-worker policy requiring `codex --search exec --ephemeral -s read-only` without user-config suppression, broad read-only information retrieval through configured CLI/MCP/connector/browser/search tools plus shell/network inspection commands, and explicit prohibition on file edits or state-changing actions; branch prompts must preserve that boundary;
- branch prompts require parallel worker dispatch by default;
- `max_active_branch_agents` is present and <= 4;
- parallelism is the default, the manifest contains parallelization metadata, and `parallelization.scheduling_mode` is `rolling`;
- manifest artifact and cleanup policies are present, non-empty, and are repeated or honored by `main.prompt.md`;
- waves, when present, cover every branch exactly once, no wave has more than 4 branches, and there are no more than 5 waves;
- branch `depends_on` entries, when present, reference only prior branch ids and are the only reason to defer an otherwise eligible branch;
- `main.prompt.md` requires saturating branch orchestrator slots up to `max_active_branch_agents`, launching the next eligible branch when capacity is freed, and treating waves as scheduling/order groups rather than dependency barriers;
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
    telemetry = telemetry_function()
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {shell_quote(repo_root.as_posix())} rev-parse --show-toplevel >/dev/null

output_path="$(pwd)/prompt-audit.json"
attempt_timeout_seconds={AUDIT_ATTEMPT_TIMEOUT_SECONDS}
timeout_kill_after_seconds={TIMEOUT_KILL_AFTER_SECONDS}
rm -f "$output_path" "$(pwd)/events-primary.jsonl" "$(pwd)/events-fallback.jsonl" "$(pwd)/telemetry.json"

run_with_timeout() {{
  local seconds="$1"
  shift
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout command not found; refusing unbounded prompt-audit attempt." >&2
    return 127
  fi
  timeout --foreground --kill-after="${{timeout_kill_after_seconds}}s" "${{seconds}}s" "$@"
}}

run_model() {{
  local label="$1"
  local model="$2"
  run_with_timeout "$attempt_timeout_seconds" codex exec --ephemeral \\
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

audit_status() {{
  python3 - "$output_path" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("status", ""))
PY
}}

finish_valid_audit() {{
  write_telemetry
  if [ "$(audit_status)" = "pass" ]; then
    exit 0
  fi
  exit 1
}}

recover_audit_from_events() {{
  local label="$1"
  python3 - "$output_path" {shell_quote(manifest_path.as_posix())} {shell_quote(repo_root.as_posix())} "events-${{label}}.jsonl" <<'PY'
import json
import sys
from pathlib import Path

output_path, manifest, repo_root, events_path = sys.argv[1:5]
path = Path(events_path)
if not path.exists():
    raise SystemExit(1)

required = frozenset([
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
])


def valid(data: object) -> bool:
    if not isinstance(data, dict) or not required <= set(data):
        return False
    if data.get("manifest") != manifest or data.get("repo_root") != repo_root:
        return False
    if data.get("status") not in {"pass", "failed", "blocked"}:
        return False
    if not isinstance(data.get("can_start"), bool):
        return False
    for key in ["checked_files", "missing_dod_items", "commands_run"]:
        value = data.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            return False
    if not data["commands_run"]:
        return False
    defects = data.get("defects")
    if not isinstance(defects, list):
        return False
    for item in defects:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("file"), str) or not item["file"].strip():
            return False
        if item.get("severity") not in {"critical", "major", "minor"}:
            return False
        if not isinstance(item.get("message"), str) or not item["message"].strip():
            return False
    for key in ["actionability_verdict", "summary"]:
        if not isinstance(data.get(key), str) or not data[key].strip():
            return False
    return True


messages: list[str] = []
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        continue
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message":
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(text.strip())

for text in reversed(messages):
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        continue
    if valid(data):
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\\n")
        raise SystemExit(0)

raise SystemExit(1)
PY
}}

audit_failure_summary() {{
  python3 - <<'PY'
from pathlib import Path

logs = []
for name in ["events-primary.jsonl", "events-fallback.jsonl"]:
    path = Path(name)
    if not path.exists():
        logs.append(name + ": missing")
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    if "read-only file system" in lowered:
        logs.append(name + ": codex-init-read-only-filesystem")
    elif "unsupported" in lowered and "model" in lowered:
        logs.append(name + ": model-unsupported")
    elif "timed out" in lowered or "timeout" in lowered:
        logs.append(name + ": timeout")
    elif "schema" in lowered:
        logs.append(name + ": schema-or-output-invalid")
    elif text.strip():
        tail = " | ".join(line.strip() for line in text.splitlines()[-5:] if line.strip())
        logs.append(name + ": " + tail[:500])
    else:
        logs.append(name + ": empty")

print("Prompt audit primary and fallback failed without producing a valid prompt-audit.json. failure_fingerprints=" + "; ".join(logs))
PY
}}

{telemetry}

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
    json.dump(data, handle, indent=2, sort_keys=True)
    handle.write("\\n")
PY
}}

run_model primary {shell_quote(AUDIT_MODEL)} || true
recover_audit_from_events primary || true
if [ -s "$output_path" ] && valid_audit; then
  finish_valid_audit
fi

if [ -s "$output_path" ] && valid_audit; then
  finish_valid_audit
fi

rm -f "$output_path"

run_model fallback {shell_quote(AUDIT_FALLBACK_MODEL)} || true
recover_audit_from_events fallback || true
if [ -s "$output_path" ] && valid_audit; then
  finish_valid_audit
fi

if [ -s "$output_path" ] && valid_audit; then
  finish_valid_audit
fi

write_terminal_audit "$(audit_failure_summary)"
write_telemetry
exit 1
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--replace", action="store_true", help="Archive an existing audit packet under attempts/ and recreate it.")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    manifest = load_manifest(manifest_path)

    archive_existing_packet_dir(out_dir, replace=args.replace)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt-audit.schema.json").write_text(
        json.dumps(audit_schema(manifest_path, repo_root), indent=2, sort_keys=True) + "\n",
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
