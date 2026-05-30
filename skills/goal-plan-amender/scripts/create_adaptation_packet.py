#!/usr/bin/env python3
"""Create a file-backed goal-plan-amender packet."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from amendment_lib import (
    CONTRACT,
    ensure_amendment_id,
    load_json_object,
    protected_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    write_json,
)


AMENDER_OUTPUT_NAME = "../{amendment_id}.proposal.json"


def source_record(path: Path, label: str) -> dict:
    return {
        "label": label,
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def add_if_exists(records: list[dict], path: Path, label: str) -> None:
    if path.exists() and path.is_file():
        records.append(source_record(path, label))


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


def normalize_amender_ladder(values: list[str]) -> list[str]:
    try:
        return CONTRACT.normalize_route_ladder(
            values,
            default_ladder=CONTRACT.DEFAULT_AMENDER_LADDER,
            allowed_routes=CONTRACT.ALLOWED_AMENDER_ROUTES,
            route_name="amender",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def amender_telemetry_attempts(selected_ladder: list[str]) -> list[dict]:
    return CONTRACT.codex_telemetry_attempts(
        selected_ladder,
        timeout_seconds=CONTRACT.AMENDER_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
    )


def telemetry_function(amendment_id: str, selected_ladder: list[str]) -> str:
    script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    return CONTRACT.telemetry_shell_function(
        script_path=script,
        packet_dir_expr="$packet_dir",
        packet_id=amendment_id,
        role=CONTRACT.AMENDER_ROLE,
        output_name=AMENDER_OUTPUT_NAME.format(amendment_id=amendment_id),
        prompt_name="prompt.md",
        attempts=amender_telemetry_attempts(selected_ladder),
    )


def task_text(amendment_id: str, manifest_path: Path, active: list[str], terminal: list[str], selected_ladder: list[str], selection_reason: str) -> str:
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


def launch_script(amendment_id: str, job_id: str, repo_root: Path, selected_ladder: list[str]) -> str:
    telemetry = telemetry_function(amendment_id, selected_ladder)
    attempt_lines: list[str] = []
    for alias in selected_ladder:
        label = CONTRACT.codex_event_label(alias)
        model = CONTRACT.codex_model(alias)
        attempt_lines.extend(
            [
                f"if run_model {CONTRACT.shell_quote(label)} {CONTRACT.shell_quote(model)} && valid_proposal; then",
                "  write_telemetry",
                "  exit 0",
                "fi",
                "",
                "if [ -s \"$proposal_path\" ] && valid_proposal; then",
                "  write_telemetry",
                "  exit 1",
                "fi",
                "",
                "rm -f \"$proposal_path\"",
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
rm -f "$proposal_path" "$packet_dir"/events-*.jsonl "$packet_dir/telemetry.json"

run_with_timeout() {{
  local seconds="$1"
  shift
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout command not found; refusing unbounded plan-amender attempt." >&2
    return 127
  fi
  timeout --foreground --kill-after="${{timeout_kill_after_seconds}}s" "${{seconds}}s" "$@"
}}

run_model() {{
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--main-prompt", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--amendment-id", required=True)
    parser.add_argument("--prompt-audit")
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument("--amender-route", action="append", default=[], help="Allowed plan-amender model alias; repeat or comma-separate to select an ordered subsequence.")
    parser.add_argument("--selection-reason", help="Required when --amender-route is supplied; recorded in route.json.")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    main_prompt = resolve_absolute_path(args.main_prompt, "--main-prompt", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    amendment_id = ensure_amendment_id(args.amendment_id)
    manifest = load_json_object(manifest_path)
    if manifest.get("amender_model_policy") != CONTRACT.AMENDER_MODEL_POLICY:
        raise SystemExit("manifest amender_model_policy does not match the shared deterministic plan-amender router policy")
    selected_ladder = normalize_amender_ladder(args.amender_route)
    if args.amender_route and not str(args.selection_reason or "").strip():
        raise SystemExit("--selection-reason is required when --amender-route is supplied")
    selection_reason = str(args.selection_reason or "").strip() or "Default deterministic plan-amender model ladder from amender_model_policy."
    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    if not decision_path.exists():
        raise SystemExit(f"missing launch decision artifact: {decision_path}")
    decision = load_json_object(decision_path)
    if decision.get("schema_version") != 1 or decision.get("amendment_id") != amendment_id or decision.get("decision") != "launch":
        raise SystemExit(f"amendment decision must be a launch decision for {amendment_id}: {decision_path}")
    if decision.get("manifest") != manifest_path.as_posix() or decision.get("manifest_sha256") != sha256_file(manifest_path):
        raise SystemExit("amendment decision manifest path or sha256 does not match the live manifest")
    if decision.get("reason_code") not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("amendment decision reason_code is not valid for a launch decision")
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    if packet_dir.exists() and not args.replace:
        raise SystemExit(f"adaptation packet already exists; pass --replace to recreate: {packet_dir}")
    if packet_dir.exists():
        for child in sorted(packet_dir.iterdir(), reverse=True):
            if child.is_dir():
                raise SystemExit(f"refusing to replace non-empty nested packet directory: {child}")
            child.unlink()
    packet_dir.mkdir(parents=True, exist_ok=True)

    active, terminal, terminal_status = protected_ids(
        manifest_path,
        manifest,
        active_ids=args.active_branch,
        terminal_ids=args.terminal_branch,
        infer_scheduler=True,
    )
    decision_active = sorted(item for item in decision.get("active_branch_ids", []) if isinstance(item, str))
    decision_terminal = sorted(item for item in decision.get("terminal_branch_ids", []) if isinstance(item, str))
    if sorted(active) != decision_active:
        raise SystemExit("amendment decision active_branch_ids do not match packet protected active ids")
    if sorted(terminal) != decision_terminal:
        raise SystemExit("amendment decision terminal_branch_ids do not match packet protected terminal ids")
    decision_terminal_status = decision.get("terminal_branch_statuses")
    if not isinstance(decision_terminal_status, dict) or {
        branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)
    } != {branch_id: decision_terminal_status.get(branch_id) for branch_id in decision_terminal}:
        raise SystemExit("amendment decision terminal_branch_statuses do not match packet protected terminal statuses")
    records: list[dict] = []
    records.append(source_record(manifest_path, "live manifest"))
    records.append(source_record(decision_path, "amendment launch decision"))
    records.append(source_record(main_prompt, "main prompt"))
    audit_path = resolve_absolute_path(args.prompt_audit, "--prompt-audit", must_exist=True) if args.prompt_audit else bundle_dir / "audit" / "prompt-audit.json"
    add_if_exists(records, audit_path, "prompt audit")
    scheduler_path = manifest.get("parallelization", {}).get("scheduler_path") if isinstance(manifest.get("parallelization"), dict) else None
    if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path"):
        add_if_exists(records, bundle_dir / scheduler_path, "main scheduler")
    for branch in manifest.get("branches", []):
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

    packet = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "manifest": manifest_path.as_posix(),
        "main_prompt": main_prompt.as_posix(),
        "repo_root": repo_root.as_posix(),
        "decision_path": decision_path.as_posix(),
        "proposal_path": (amendments_dir / f"{amendment_id}.proposal.json").as_posix(),
        "validation_path": (amendments_dir / f"{amendment_id}.validation.json").as_posix(),
        "accepted_path": (amendments_dir / f"{amendment_id}.accepted.json").as_posix(),
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "selected_ladder": selected_ladder,
        "selection_reason": selection_reason,
        "source_files": records,
    }
    route = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": CONTRACT.AMENDER_ROLE,
        "selected_ladder": selected_ladder,
        "selection_reason": selection_reason,
        "policy": CONTRACT.AMENDER_MODEL_POLICY,
    }
    write_json(packet_dir / "input-files.json", packet)
    write_json(packet_dir / "proposal.schema.json", proposal_schema(amendment_id, str(manifest.get("job_id", ""))))
    write_json(packet_dir / "proposal.example.json", proposal_example(amendment_id, str(manifest.get("job_id", ""))))
    write_json(packet_dir / "route.json", route)
    rendered_task = task_text(amendment_id, manifest_path, sorted(active), sorted(terminal), selected_ladder, selection_reason)
    (packet_dir / "task.md").write_text(rendered_task, encoding="utf-8")
    (packet_dir / "prompt.md").write_text(
        rendered_task
        + "\nUse `proposal.schema.json` as the required output schema, `proposal.example.json` as a shape example, and write only the final proposal JSON.\n",
        encoding="utf-8",
    )
    launch = launch_script(amendment_id, str(manifest.get("job_id", "")), repo_root, selected_ladder)
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch, encoding="utf-8")
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
