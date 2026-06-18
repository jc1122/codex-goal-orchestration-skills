#!/usr/bin/env python3
"""Create a file-backed goal-plan-amender packet."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import NamedTuple

from amendment_lib import (
    CONTRACT,
    add_if_exists,
    amender_model_policy,
    amender_telemetry_attempts,
    ensure_amendment_id,
    load_json_object,
    normalize_amender_ladder,
    protected_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    source_record,
    validate_amender_model_policy,
    write_json,
)


AMENDER_OUTPUT_NAME = "../{amendment_id}.proposal.json"


def proposal_schema(amendment_id: str, job_id: str) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "amendment_id", "job_id", "rationale", "operations"],
        "properties": {
            "schema_version": {"type": "integer", "const": 1},
            "amendment_id": {"type": "string", "const": amendment_id},
            "job_id": {"type": "string", "const": job_id},
            "rationale": {"type": "string", "minLength": 1},
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["op"],
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": list(CONTRACT.ADAPTATION_ALLOWED_OPERATIONS),
                        }
                    },
                    "additionalProperties": True,
                },
            },
        },
    }


def proposal_example(amendment_id: str, job_id: str) -> dict:
    return {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": job_id,
        "rationale": "Explain why terminal branch evidence requires future-plan adaptation.",
        "operations": [
            {
                "op": "add_branch",
                "branch": {
                    "id": "B99",
                    "title": "Future bounded work",
                    "objective": "Bounded future objective.",
                    "scope": "Future unstarted work only.",
                    "branch_name": f"{job_id}-b99",
                    "worktree_path": f".worktrees/{job_id}-b99",
                    "depends_on": [],
                    "max_active_worker_packets": 1,
                    "worker_serial_reasons": ["Single amendment example packet."],
                    "work_items": [
                        {
                            "id": "W01",
                            "objective": "Worker-sized objective.",
                            "owned_paths": ["README.md"],
                            "context_files": ["README.md"],
                            "verification": ["git diff --check main...HEAD"],
                            "dod": ["Worker DoD is falsifiable."],
                        }
                    ],
                    "tests": ["git diff --check main...HEAD"],
                    "dod": ["Branch DoD is falsifiable."],
                },
            }
        ],
    }


def telemetry_function(
    amendment_id: str,
    manifest: dict,
    manifest_path: Path,
    selected_ladder: list[str],
    *,
    telemetry_debug: bool = False,
) -> str:
    script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    return CONTRACT.telemetry_shell_function(
        script_path=script,
        packet_dir_expr="$packet_dir",
        packet_id=amendment_id,
        role=CONTRACT.AMENDER_ROLE,
        output_name=AMENDER_OUTPUT_NAME.format(amendment_id=amendment_id),
        prompt_name="prompt.md",
        attempts=amender_telemetry_attempts(manifest, manifest_path, selected_ladder),
        debug_output_name=CONTRACT.TELEMETRY_DEBUG_NAME if telemetry_debug else None,
    )


def task_text(
    amendment_id: str,
    manifest_path: Path,
    active: list[str],
    terminal: list[str],
    selected_ladder: list[str],
    selection_reason: str,
) -> str:
    return "\n".join(
        [
            f"# Goal Plan Amendment Packet {amendment_id}",
            "",
            f"Manifest: {manifest_path}",
            f"Active branch ids: {', '.join(active) if active else 'none'}",
            f"Terminal branch ids: {', '.join(terminal) if terminal else 'none'}",
            f"Selected amender ladder: {', '.join(selected_ladder)}",
            f"Route selection reason: {selection_reason}",
            "",
            "Write a proposal JSON at the sibling `Axxx.proposal.json` path.",
            "Copy the route exactly from `route.json`; do not change model aliases, model ids, effort levels, or provider order.",
            "Only propose operations allowed by manifest adaptation_policy.allowed_operations.",
            "Do not change active or terminal branches. Do not inspect active branch internals.",
            "Prefer additive future work. Recovery branches should cite non-pass terminal evidence with `recovers_from` and should not use depends_on on non-pass branches.",
            "Every new or changed branch must contain 1 to 4 worker-sized work items with safe repo-relative owned/context paths and falsifiable verification.",
            "",
        ]
    )


def _attempt_label(attempt: dict) -> str:
    logs = attempt.get("event_logs")
    if isinstance(logs, list) and logs and isinstance(logs[0], str):
        stem = Path(logs[0]).stem
        return stem.removeprefix("events-")
    return str(attempt.get("alias", "amender")).replace("/", "-")


def launch_script(
    amendment_id: str,
    job_id: str,
    repo_root: Path,
    manifest: dict,
    manifest_path: Path,
    selected_ladder: list[str],
    *,
    telemetry_debug: bool = False,
) -> str:
    telemetry = telemetry_function(
        amendment_id, manifest, manifest_path, selected_ladder, telemetry_debug=telemetry_debug
    )
    attempt_lines: list[str] = []
    for attempt in amender_telemetry_attempts(manifest, manifest_path, selected_ladder):
        alias = str(attempt.get("alias") or "")
        label = _attempt_label(attempt)
        kind = attempt.get("harness_kind") or attempt.get("provider")
        if kind == CONTRACT.BRIDGE_HARNESS_KIND:
            model = str(attempt.get("model") or "")
            variant = str(attempt.get("variant") or "max")
            runner = (
                f"run_bridge_model {CONTRACT.shell_quote(label)} {CONTRACT.shell_quote(model)} "
                f"{CONTRACT.shell_quote(variant)}"
            )
        elif kind == "codex":
            model = str(attempt.get("model") or "")
            runner = f"run_codex_model {CONTRACT.shell_quote(label)} {CONTRACT.shell_quote(model)}"
        else:
            logs = attempt.get("event_logs")
            event_name = (
                logs[0] if isinstance(logs, list) and logs and isinstance(logs[0], str) else f"events-{label}.log"
            )
            command = f"unsupported_attempt {CONTRACT.shell_quote(event_name)} {CONTRACT.shell_quote(alias)} {CONTRACT.shell_quote(str(kind or 'unknown'))}"
            runner = f"run_configured_model {CONTRACT.shell_quote(event_name)} {command}"
        attempt_lines.extend(
            [
                f"if {runner} && valid_proposal; then",
                "  write_telemetry",
                "  exit 0",
                "fi",
                "",
                'if [ -s "$proposal_path" ] && valid_proposal; then',
                "  write_telemetry",
                "  exit 1",
                "fi",
                "",
                'rm -f "$proposal_path"',
                "",
            ]
        )
    attempts = "\n".join(attempt_lines)
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

git -C {CONTRACT.shell_quote(repo_root.as_posix())} rev-parse --show-toplevel >/dev/null

packet_dir="$(pwd)"
proposal_path="$packet_dir/../{amendment_id}.proposal.json"
attempt_timeout_seconds={CONTRACT.AMENDER_ATTEMPT_TIMEOUT_SECONDS}
timeout_kill_after_seconds={CONTRACT.TIMEOUT_KILL_AFTER_SECONDS}
bridge_provider={CONTRACT.shell_quote(CONTRACT.BRIDGE_PROVIDER_ID)}
bridge_pool_max_workers=4
repo_root={CONTRACT.shell_quote(repo_root.as_posix())}
rm -f "$proposal_path" "$packet_dir"/events-*.jsonl "$packet_dir/telemetry.json"
rm -rf "$packet_dir/bridge"

resolve_bridge_control() {{
  # Resolve the opencode-worker-bridge control script.
  # Order: env override -> source checkout under repo root -> $CODEX_HOME skills
  # -> $HOME/.agents skills (mirrors the runtime runner resolution).
  local candidates=()
  if [ -n "${{OPENCODE_WORKER_BRIDGE_ROOT:-}}" ]; then
    candidates+=("${{OPENCODE_WORKER_BRIDGE_ROOT}}/scripts/opencode_worker.py")
  fi
  candidates+=("$repo_root/skills/opencode-worker-bridge/scripts/opencode_worker.py")
  candidates+=("${{CODEX_HOME:-$HOME/.codex}}/skills/opencode-worker-bridge/scripts/opencode_worker.py")
  candidates+=("$HOME/.agents/skills/opencode-worker-bridge/scripts/opencode_worker.py")
  local candidate
  for candidate in "${{candidates[@]}}"; do
    if [ -f "$candidate" ]; then
      printf '%s\\n' "$candidate"
      return 0
    fi
  done
  return 1
}}

run_with_timeout() {{
  local seconds="$1"
  shift
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout command not found; refusing unbounded plan-amender attempt." >&2
    return 127
  fi
  timeout --foreground --kill-after="${{timeout_kill_after_seconds}}s" "${{seconds}}s" "$@"
}}

run_codex_model() {{
  local label="$1"
  local model="$2"
  run_with_timeout "$attempt_timeout_seconds" codex exec --ephemeral \\
    -m "$model" \\
    -C {CONTRACT.shell_quote(repo_root.as_posix())} \\
    -s read-only \\
    --json \\
    --output-schema "$packet_dir/proposal.schema.json" \\
    -o "$proposal_path" \\
    - < "$packet_dir/prompt.md" \\
    > "$packet_dir/events-${{label}}.jsonl" 2>&1
}}

extract_stdout_proposal() {{
  local source_path="$1"
  python3 - "$source_path" "$proposal_path" <<'PY'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
proposal = Path(sys.argv[2])
text = source.read_text(encoding="utf-8", errors="replace")
candidates = [text]
try:
    parsed = json.loads(text)
except Exception:
    parsed = None
if isinstance(parsed, dict):
    for key in ("content", "output", "text", "message"):
        value = parsed.get(key)
        if isinstance(value, str):
            candidates.append(value)
    choices = parsed.get("choices")
    if isinstance(choices, list):
        for item in choices:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    candidates.append(value)
for candidate in candidates:
    candidate = candidate.strip()
    if not candidate:
        continue
    try:
        data = json.loads(candidate)
    except Exception:
        start = candidate.find("{{")
        end = candidate.rfind("}}")
        if start < 0 or end <= start:
            continue
        try:
            data = json.loads(candidate[start : end + 1])
        except Exception:
            continue
    if isinstance(data, dict):
        proposal.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
        raise SystemExit(0)
raise SystemExit(1)
PY
}}

run_configured_model() {{
  local event_name="$1"
  shift
  run_with_timeout "$attempt_timeout_seconds" "$@" > "$packet_dir/${{event_name}}" 2>&1
  extract_stdout_proposal "$packet_dir/${{event_name}}"
}}

map_bridge_run() {{
  # Map bridge goal-delegator-* artifacts (job_envelope.json / worker.status.json
  # / supervisor_verdict.json / delegation-report.json) in $run_dir onto a
  # synthetic events-<label>.jsonl (token usage only; NEVER USD) and emit the
  # assistant text on stdout for proposal extraction.
  local run_dir="$1"
  local event_path="$2"
  python3 - "$run_dir" "$event_path" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
event_path = Path(sys.argv[2])
PASS = {{"passed", "completed", "done", "success"}}


def read(name):
    path = run_dir / name
    if not path.exists():
        return {{}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {{}}
    return data if isinstance(data, dict) else {{}}


job = read("job_envelope.json")
worker_status = read("worker.status.json")
verdict = read("supervisor_verdict.json")
report = read("delegation-report.json")

status = "unknown"
for source, key in ((verdict, "status"), (job, "status"), (worker_status, "lifecycle")):
    value = source.get(key)
    if isinstance(value, str) and value.strip():
        status = value.strip()
        break
passed = status.lower() in PASS

route = job.get("route") if isinstance(job.get("route"), dict) else {{}}


def usage():
    for source in (verdict, job, worker_status, report, route):
        if not isinstance(source, dict):
            continue
        found = source.get("usage") or source.get("tokens") or source.get("token_usage")
        if isinstance(found, dict) and found:
            cleaned = {{
                k: v
                for k, v in found.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }}
            if cleaned:
                return cleaned
    return None


def assistant_text():
    for source in (verdict, report, job, worker_status):
        if not isinstance(source, dict):
            continue
        for key in ("assistant_text", "output_text", "summary", "message", "final_message"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


text = assistant_text()
event = {{
    "output_nonempty": bool(text.strip()),
    "usage": usage(),
    "provider": route.get("provider") if isinstance(route.get("provider"), str) else None,
    "model": route.get("model") if isinstance(route.get("model"), str) else None,
    "variant": route.get("variant") if isinstance(route.get("variant"), str) else None,
    "status": status,
}}
event_path.write_text(json.dumps(event, separators=(",", ":")) + "\\n", encoding="utf-8")
sys.stdout.write(text)
sys.exit(0 if passed else 1)
PY
}}

run_bridge_model() {{
  # Read-only deepseek amender route delegated through opencode-worker-bridge.
  # The amender proposal is proposal-only; permission-profile is read-only.
  local label="$1"
  local model="$2"
  local variant="$3"
  local control
  if ! control="$(resolve_bridge_control)"; then
    echo "opencode-worker-bridge control script not found (set OPENCODE_WORKER_BRIDGE_ROOT)." \\
      > "$packet_dir/events-${{label}}.jsonl"
    return 127
  fi
  local run_dir="$packet_dir/bridge/${{label}}"
  local pool_dir="$packet_dir/bridge/pool"
  local event_path="$packet_dir/events-${{label}}.jsonl"
  mkdir -p "$run_dir" "$pool_dir"
  local state_path="$run_dir/opencode-worker-state.json"
  local worker_id={CONTRACT.shell_quote(amendment_id)}
  cp "$packet_dir/prompt.md" "$run_dir/task.md"

  if ! run_with_timeout "$attempt_timeout_seconds" python3 "$control" pool-acquire \\
      --pool-dir "$pool_dir" --max-workers "$bridge_pool_max_workers" --worker-id "$worker_id" \\
      > "$run_dir/pool-acquire.log" 2>&1; then
    echo "bridge pool capacity limit reached; scheduler should refill later." > "$event_path"
    return 1
  fi

  local rc=1
  (
    run_with_timeout "$attempt_timeout_seconds" python3 "$control" start \\
      --state "$state_path" --cwd "$repo_root" \\
      --pool-dir "$pool_dir" --pool-worker-id "$worker_id" \\
      > "$run_dir/start.log" 2>&1 || true
    run_with_timeout "$attempt_timeout_seconds" python3 "$control" delegate \\
      --state "$state_path" --run-dir "$run_dir" --job-id "$worker_id" \\
      --prompt-file "$run_dir/task.md" \\
      --provider "$bridge_provider" --model "$model" --variant "$variant" \\
      --permission-profile read-only \\
      --report "$run_dir/delegation-report.json" \\
      > "$run_dir/delegate.log" 2>&1 || true
    run_with_timeout "$attempt_timeout_seconds" python3 "$control" stop \\
      --state "$state_path" --run-dir "$run_dir" \\
      > "$run_dir/stop.log" 2>&1 || true
  )
  run_with_timeout "$attempt_timeout_seconds" python3 "$control" pool-release \\
    --pool-dir "$pool_dir" --worker-id "$worker_id" \\
    > "$run_dir/pool-release.log" 2>&1 || true

  if map_bridge_run "$run_dir" "$event_path" > "$run_dir/assistant.txt"; then
    rc=0
  else
    rc=1
  fi
  extract_stdout_proposal "$run_dir/assistant.txt" || return 1
  return "$rc"
}}

unsupported_attempt() {{
  local event_name="$1"
  local alias="$2"
  local kind="$3"
  echo "Unsupported configured amender harness for $alias: $kind" > "$packet_dir/${{event_name}}"
  return 127
}}

valid_proposal() {{
  python3 - "$proposal_path" {CONTRACT.shell_quote(amendment_id)} {CONTRACT.shell_quote(job_id)} <<'PY'
import json
import sys

path, amendment_id, job_id = sys.argv[1:4]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("schema_version") != 1:
    raise SystemExit(1)
if data.get("amendment_id") != amendment_id or data.get("job_id") != job_id:
    raise SystemExit(1)
if not isinstance(data.get("rationale"), str) or not data["rationale"].strip():
    raise SystemExit(1)
operations = data.get("operations")
if not isinstance(operations, list) or not operations:
    raise SystemExit(1)
if any(not isinstance(item, dict) or not isinstance(item.get("op"), str) for item in operations):
    raise SystemExit(1)
PY
}}

write_terminal_proposal() {{
  local message="$1"
  python3 - "$proposal_path" {CONTRACT.shell_quote(amendment_id)} {CONTRACT.shell_quote(job_id)} "$message" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
amendment_id = sys.argv[2]
job_id = sys.argv[3]
message = sys.argv[4]
data = {{
    "schema_version": 1,
    "amendment_id": amendment_id,
    "job_id": job_id,
    "rationale": message + " Inspect plan-amender event logs in this packet directory for the underlying CLI or schema error.",
    "operations": [],
}}
output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
}}

{telemetry}

{attempts}

write_terminal_proposal "Plan amender selected route attempts failed without producing a valid proposal."
write_telemetry
exit 1
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--main-prompt", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--amendment-id", required=True)
    parser.add_argument("--prompt-audit")
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument(
        "--amender-route",
        action="append",
        default=[],
        help="Allowed plan-amender model alias; repeat or comma-separate to select an ordered subsequence.",
    )
    parser.add_argument("--selection-reason", help="Required when --amender-route is supplied; recorded in route.json.")
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


class ResolvedInputs(NamedTuple):
    manifest_path: Path
    main_prompt: Path
    repo_root: Path
    amendment_id: str
    manifest: dict
    policy: dict
    selected_ladder: list[str]
    selection_reason: str


def _resolve_inputs(args: argparse.Namespace) -> ResolvedInputs:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    main_prompt = resolve_absolute_path(args.main_prompt, "--main-prompt", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    amendment_id = ensure_amendment_id(args.amendment_id)
    manifest = load_json_object(manifest_path)
    try:
        policy = validate_amender_model_policy(manifest, manifest_path)
        selected_ladder = normalize_amender_ladder(manifest, manifest_path, args.amender_route)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.amender_route and not str(args.selection_reason or "").strip():
        raise SystemExit("--selection-reason is required when --amender-route is supplied")
    selection_reason = (
        str(args.selection_reason or "").strip()
        or "Default deterministic plan-amender model ladder from amender_model_policy."
    )
    return ResolvedInputs(
        manifest_path=manifest_path,
        main_prompt=main_prompt,
        repo_root=repo_root,
        amendment_id=amendment_id,
        manifest=manifest,
        policy=policy,
        selected_ladder=selected_ladder,
        selection_reason=selection_reason,
    )


def _load_launch_decision(decision_path: Path, *, amendment_id: str, manifest_path: Path) -> dict:
    if not decision_path.exists():
        raise SystemExit(f"missing launch decision artifact: {decision_path}")
    decision = load_json_object(decision_path)
    if (
        decision.get("schema_version") != 1
        or decision.get("amendment_id") != amendment_id
        or decision.get("decision") != "launch"
    ):
        raise SystemExit(f"amendment decision must be a launch decision for {amendment_id}: {decision_path}")
    if decision.get("manifest") != manifest_path.as_posix() or decision.get("manifest_sha256") != sha256_file(
        manifest_path
    ):
        raise SystemExit("amendment decision manifest path or sha256 does not match the live manifest")
    if (
        not isinstance(decision.get("reason_code"), str)
        or decision.get("reason_code") not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES
    ):
        raise SystemExit("amendment decision reason_code is not valid for a launch decision")
    return decision


def _prepare_packet_dir(amendments_dir: Path, amendment_id: str, *, replace: bool) -> Path:
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    if packet_dir.exists() and not replace:
        raise SystemExit(f"adaptation packet already exists; pass --replace to recreate: {packet_dir}")
    if packet_dir.exists():
        for child in sorted(packet_dir.iterdir(), reverse=True):
            if child.is_dir():
                raise SystemExit(f"refusing to replace non-empty nested packet directory: {child}")
            child.unlink()
    packet_dir.mkdir(parents=True, exist_ok=True)
    return packet_dir


def _reconcile_protected_ids(
    args: argparse.Namespace, inputs: ResolvedInputs, decision: dict
) -> tuple[list[str], list[str], dict]:
    try:
        active, terminal, terminal_status = protected_ids(
            inputs.manifest_path,
            inputs.manifest,
            active_ids=args.active_branch,
            terminal_ids=args.terminal_branch,
            infer_scheduler=True,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    raw_active = decision.get("active_branch_ids")
    decision_active = sorted(
        item for item in (raw_active if isinstance(raw_active, list) else []) if isinstance(item, str)
    )
    raw_terminal = decision.get("terminal_branch_ids")
    decision_terminal = sorted(
        item for item in (raw_terminal if isinstance(raw_terminal, list) else []) if isinstance(item, str)
    )
    if sorted(active) != decision_active:
        raise SystemExit("amendment decision active_branch_ids do not match packet protected active ids")
    if sorted(terminal) != decision_terminal:
        raise SystemExit("amendment decision terminal_branch_ids do not match packet protected terminal ids")
    decision_terminal_status = decision.get("terminal_branch_statuses")
    if not isinstance(decision_terminal_status, dict) or {
        branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)
    } != {branch_id: decision_terminal_status.get(branch_id) for branch_id in decision_terminal}:
        raise SystemExit("amendment decision terminal_branch_statuses do not match packet protected terminal statuses")
    return active, terminal, terminal_status


def _collect_source_records(
    args: argparse.Namespace,
    inputs: ResolvedInputs,
    *,
    bundle_dir: Path,
    amendments_dir: Path,
    decision_path: Path,
    terminal: list[str],
) -> list[dict]:
    manifest = inputs.manifest
    records: list[dict] = []
    records.append(source_record(inputs.manifest_path, "live manifest"))
    records.append(source_record(decision_path, "amendment launch decision"))
    records.append(source_record(inputs.main_prompt, "main prompt"))
    audit_path = (
        resolve_absolute_path(args.prompt_audit, "--prompt-audit", must_exist=True)
        if args.prompt_audit
        else bundle_dir / "audit" / "prompt-audit.json"
    )
    add_if_exists(records, audit_path, "prompt audit")
    scheduler_path = (
        manifest.get("parallelization", {}).get("scheduler_path")
        if isinstance(manifest.get("parallelization"), dict)
        else None
    )
    if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path"):
        add_if_exists(records, bundle_dir / scheduler_path, "main scheduler")
    manifest_branches = manifest.get("branches")
    for branch in manifest_branches if isinstance(manifest_branches, list) else []:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        branch_id = branch["id"]
        if branch_id not in terminal:
            continue
        for key, label in [("status_path", "terminal branch status"), ("review_path", "terminal branch review")]:
            value = branch.get(key)
            if isinstance(value, str) and not relative_path_defect(value, key):
                add_if_exists(records, bundle_dir / value, f"{label} {branch_id}")
    for accepted in sorted(amendments_dir.glob("*.accepted.json")):
        add_if_exists(records, accepted, "previous accepted amendment")
    return records


def _emit_packet_artifacts(
    inputs: ResolvedInputs,
    *,
    packet_dir: Path,
    amendments_dir: Path,
    decision_path: Path,
    active: list[str],
    terminal: list[str],
    terminal_status: dict,
    records: list[dict],
) -> None:
    manifest = inputs.manifest
    manifest_path = inputs.manifest_path
    amendment_id = inputs.amendment_id
    selected_ladder = inputs.selected_ladder
    selection_reason = inputs.selection_reason
    packet = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "manifest": manifest_path.as_posix(),
        "main_prompt": inputs.main_prompt.as_posix(),
        "repo_root": inputs.repo_root.as_posix(),
        "decision_path": decision_path.as_posix(),
        "proposal_path": (amendments_dir / f"{amendment_id}.proposal.json").as_posix(),
        "validation_path": (amendments_dir / f"{amendment_id}.validation.json").as_posix(),
        "accepted_path": (amendments_dir / f"{amendment_id}.accepted.json").as_posix(),
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "selected_ladder": selected_ladder,
        "selection_reason": selection_reason,
        "route_policy": inputs.policy,
        "source_files": records,
    }
    route = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": CONTRACT.AMENDER_ROLE,
        "selected_ladder": selected_ladder,
        "selection_reason": selection_reason,
        "policy": amender_model_policy(manifest, manifest_path),
    }
    write_json(packet_dir / "input-files.json", packet)
    write_json(packet_dir / "proposal.schema.json", proposal_schema(amendment_id, str(manifest.get("job_id", ""))))
    write_json(packet_dir / "proposal.example.json", proposal_example(amendment_id, str(manifest.get("job_id", ""))))
    write_json(packet_dir / "route.json", route)
    rendered_task = task_text(
        amendment_id, manifest_path, sorted(active), sorted(terminal), selected_ladder, selection_reason
    )
    (packet_dir / "task.md").write_text(rendered_task, encoding="utf-8")
    (packet_dir / "prompt.md").write_text(
        rendered_task
        + "\nUse `proposal.schema.json` as the required output schema, `proposal.example.json` as a shape example, and write only the final proposal JSON.\n",
        encoding="utf-8",
    )
    launch = launch_script(
        amendment_id,
        str(manifest.get("job_id", "")),
        inputs.repo_root,
        manifest,
        manifest_path,
        selected_ladder,
        telemetry_debug=CONTRACT.telemetry_debug_enabled(manifest),
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch, encoding="utf-8")
    os.chmod(launch_path, 0o755)


def main() -> int:
    args = _parse_args()
    inputs = _resolve_inputs(args)
    bundle_dir = inputs.manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{inputs.amendment_id}.decision.json"
    decision = _load_launch_decision(
        decision_path, amendment_id=inputs.amendment_id, manifest_path=inputs.manifest_path
    )
    packet_dir = _prepare_packet_dir(amendments_dir, inputs.amendment_id, replace=args.replace)
    active, terminal, terminal_status = _reconcile_protected_ids(args, inputs, decision)
    records = _collect_source_records(
        args,
        inputs,
        bundle_dir=bundle_dir,
        amendments_dir=amendments_dir,
        decision_path=decision_path,
        terminal=terminal,
    )
    _emit_packet_artifacts(
        inputs,
        packet_dir=packet_dir,
        amendments_dir=amendments_dir,
        decision_path=decision_path,
        active=active,
        terminal=terminal,
        terminal_status=terminal_status,
        records=records,
    )
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
