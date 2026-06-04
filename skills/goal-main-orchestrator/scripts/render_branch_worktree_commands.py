#!/usr/bin/env python3
"""Render branch worktree creation commands after prompt audit passes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
from pathlib import Path


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


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
SAFE_LABEL_RE = PATH_RULES.SAFE_LABEL_RE
is_strict_int = PATH_RULES.is_strict_int
resolve_absolute_path = PATH_RULES.resolve_absolute_path
resolve = PATH_RULES.resolve
require_relative_path = PATH_RULES.require_relative_path
safe_branch_name = PATH_RULES.safe_branch_name
shell_quote = CONTRACT.shell_quote
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
MAX_WAVES = CONTRACT.MAX_WAVES
RESEARCH_WORKER_TYPE = CONTRACT.RESEARCH_WORKER_TYPE
WORK_ITEM_ROLES = set(CONTRACT.WORK_ITEM_ROLES)
TERMINAL_STATUSES = {"pass", "partial", "blocked", "failed"}
MODEL_AUDIT_LADDER = ["gpt-5.5", "gpt-5.4"]
DETERMINISTIC_AUDIT_LADDER = ["deterministic-prompt-audit"]
AUDIT_LADDERS = [MODEL_AUDIT_LADDER, DETERMINISTIC_AUDIT_LADDER]
NATIVE_DELEGATION_ENVS = (
    "GOAL_NATIVE_BRANCH_DELEGATION",
    "CODEX_NATIVE_BRANCH_DELEGATION",
    "CODEX_NATIVE_AGENT_DELEGATION",
)


def git_ok(repo_root: Path, *args: str) -> bool:
    return subprocess.run(
        ["git", "-C", repo_root.as_posix(), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def git_output(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{result.stdout.strip()}")
    return result.stdout


def branch_exists(repo_root: Path, name: str) -> bool:
    return git_ok(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")


def branch_checked_out_in_worktree(repo_root: Path, name: str) -> bool:
    target = f"branch refs/heads/{name}"
    return any(line.strip() == target for line in git_output(repo_root, "worktree", "list", "--porcelain").splitlines())


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def env_flag_enabled(name: str) -> bool:
    value = os.environ.get(name)
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "available"}


def native_delegation_available(explicit: bool) -> tuple[bool, str | None]:
    if explicit:
        return True, "explicit_flag"
    for name in NATIVE_DELEGATION_ENVS:
        if env_flag_enabled(name):
            return True, f"env:{name}"
    return False, None


def validate_audit_telemetry(audit_path: Path) -> None:
    telemetry_path = audit_path.parent / "telemetry.json"
    if not telemetry_path.exists():
        raise SystemExit(f"prompt audit telemetry does not exist: {telemetry_path}")
    telemetry = load_json(telemetry_path)
    if telemetry.get("schema_version") != 1:
        raise SystemExit("prompt audit telemetry schema_version must be 1")
    if telemetry.get("packet_id") != "prompt-audit":
        raise SystemExit("prompt audit telemetry packet_id must be 'prompt-audit'")
    if telemetry.get("role") != "prompt-auditor":
        raise SystemExit("prompt audit telemetry role must be 'prompt-auditor'")
    attempts = telemetry.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise SystemExit("prompt audit telemetry attempts must be a non-empty array")
    aliases = [item.get("alias") for item in attempts if isinstance(item, dict)]
    if aliases not in AUDIT_LADDERS:
        raise SystemExit(f"prompt audit telemetry attempts must declare one audit ladder: {AUDIT_LADDERS!r}")
    called = [item.get("alias") for item in attempts if isinstance(item, dict) and item.get("called") is True]
    if not called or not any(called == ladder[: len(called)] for ladder in AUDIT_LADDERS):
        raise SystemExit("prompt audit telemetry called attempts must be a non-empty prefix of the audit ladder")
    accepted = [item.get("alias") for item in attempts if isinstance(item, dict) and item.get("accepted") is True]
    if len(accepted) != 1 or telemetry.get("accepted_alias") != accepted[0]:
        raise SystemExit("passing prompt audit telemetry must identify exactly one accepted model")
    for index, item in enumerate(attempts):
        if not isinstance(item, dict):
            continue
        timeout_seconds = item.get("timeout_seconds")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise SystemExit(f"prompt audit telemetry attempts[{index}].timeout_seconds must be a positive integer")
    for key in ["prompt_chars", "prompt_bytes", "output_chars", "output_bytes", "event_log_chars", "event_log_bytes"]:
        value = telemetry.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SystemExit(f"prompt audit telemetry {key} must be a non-negative integer")


def require_string_list(value: object, field: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < min_items:
        raise SystemExit(f"{field} must contain at least {min_items} item(s)")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def validate_branch_dependencies(branches: list[dict]) -> dict[str, list[str]]:
    order = {branch.get("id"): index for index, branch in enumerate(branches)}
    dependencies: dict[str, list[str]] = {}
    for index, branch in enumerate(branches):
        bid = branch.get("id")
        if not isinstance(bid, str) or not SAFE_LABEL_RE.fullmatch(bid):
            raise SystemExit(f"branch id is not safe: {bid!r}")
        deps = require_string_list(branch.get("depends_on", []), f"branch {bid}.depends_on")
        seen = set()
        normalized = []
        for dep in deps:
            if dep in seen:
                raise SystemExit(f"branch {bid} depends_on repeats branch {dep}")
            if dep not in order:
                raise SystemExit(f"branch {bid} depends on unknown branch: {dep}")
            if dep == bid:
                raise SystemExit(f"branch {bid} cannot depend on itself")
            if order[dep] >= index:
                raise SystemExit(f"branch {bid} depends_on must reference only prior branch ids; invalid dependency: {dep}")
            seen.add(dep)
            normalized.append(dep)
        dependencies[bid] = normalized
    return dependencies


def validate_branch_worker_contract(branch: dict) -> None:
    bid = branch.get("id")
    if "work_items" not in branch:
        raise SystemExit(f"branch {bid} missing work_items")
    work_items = branch.get("work_items")
    if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {bid} work_items must contain 1 to 4 worker packets")
    if any(not isinstance(item, dict) for item in work_items):
        raise SystemExit(f"branch {bid} work_items entries must be objects")
    seen_work_item_ids = set()
    work_item_order = {}
    for index, item in enumerate(work_items):
        item_id = item.get("id")
        if not isinstance(item_id, str) or not SAFE_LABEL_RE.fullmatch(item_id):
            raise SystemExit(f"branch {bid} work_items[{index}].id must match {SAFE_LABEL_RE.pattern}")
        if item_id in seen_work_item_ids:
            raise SystemExit(f"branch {bid} duplicate work item id: {item_id}")
        seen_work_item_ids.add(item_id)
        work_item_order[item_id] = index
        packet_id = item.get("packet_id")
        expected_packet_id = f"{bid}-{item_id}"
        if not isinstance(packet_id, str) or not SAFE_LABEL_RE.fullmatch(packet_id):
            raise SystemExit(f"branch {bid} work_items[{index}].packet_id must match {SAFE_LABEL_RE.pattern}")
        if packet_id != expected_packet_id:
            raise SystemExit(f"branch {bid} work_items[{index}].packet_id must be {expected_packet_id!r}")
        if not isinstance(item.get("objective"), str) or not item.get("objective", "").strip():
            raise SystemExit(f"branch {bid} work_items[{index}].objective must be non-empty")
        worker_type = item.get("worker_type", "worker")
        if worker_type not in WORK_ITEM_ROLES:
            raise SystemExit(f"branch {bid} work_items[{index}].worker_type must be 'worker' or 'research-worker'")
        for key, min_items in [("owned_paths", 1), ("verification", 1), ("dod", 1), ("context_files", 0), ("depends_on", 0)]:
            values = require_string_list(item.get(key, []), f"branch {bid} work_items[{index}].{key}", min_items=min_items)
            if key in {"owned_paths", "context_files"}:
                for value in values:
                    require_relative_path(value, f"branch {bid} work_items[{index}].{key}")
    for index, item in enumerate(work_items):
        for dep in item.get("depends_on", []):
            if dep not in seen_work_item_ids:
                raise SystemExit(f"branch {bid} work_items[{index}] depends on unknown work item: {dep}")
            if work_item_order[dep] >= index:
                raise SystemExit(f"branch {bid} work_items[{index}] depends_on must reference only prior work item ids: {dep}")
    max_workers = branch.get("max_active_worker_packets")
    if not is_strict_int(max_workers) or max_workers < 1 or max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {bid} max_active_worker_packets must be an integer from 1 to 4")
    worker_parallelism = branch.get("worker_parallelism")
    if not isinstance(worker_parallelism, dict):
        raise SystemExit(f"branch {bid} worker_parallelism must be an object")
    if worker_parallelism.get("parallelism_default") is not True:
        raise SystemExit(f"branch {bid} worker_parallelism.parallelism_default must be true")
    if worker_parallelism.get("scheduling_mode") != "rolling":
        raise SystemExit(f"branch {bid} worker_parallelism.scheduling_mode must be 'rolling'")
    if worker_parallelism.get("scheduler_path") != CONTRACT.worker_scheduler_path(str(bid)):
        raise SystemExit(f"branch {bid} worker_parallelism.scheduler_path must be {CONTRACT.worker_scheduler_path(str(bid))!r}")
    if worker_parallelism.get("max_active_worker_packets") != max_workers:
        raise SystemExit(f"branch {bid} worker_parallelism.max_active_worker_packets must match branch max_active_worker_packets")
    if worker_parallelism.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {bid} worker_parallelism.max_worker_packets_per_branch must be 4")
    if "serial_reason" in worker_parallelism:
        raise SystemExit(f"branch {bid} worker_parallelism.serial_reason is obsolete; use serial_reasons")
    serial_reasons = worker_parallelism.get("serial_reasons")
    if not isinstance(serial_reasons, list) or any(not isinstance(item, str) or not item.strip() for item in serial_reasons):
        raise SystemExit(f"branch {bid} worker_parallelism.serial_reasons must be an array of non-empty strings")
    if not isinstance(worker_parallelism.get("parallelization_rationale"), str) or not worker_parallelism["parallelization_rationale"].strip():
        raise SystemExit(f"branch {bid} worker_parallelism.parallelization_rationale must be non-empty")
    dependency_policy = worker_parallelism.get("dependency_policy", "")
    if not isinstance(dependency_policy, str) or "depends_on" not in dependency_policy:
        raise SystemExit(f"branch {bid} worker_parallelism.dependency_policy must mention depends_on")
    slot_refill = worker_parallelism.get("slot_refill", "")
    if not isinstance(slot_refill, str) or "launch" not in slot_refill.lower():
        raise SystemExit(f"branch {bid} worker_parallelism.slot_refill must describe launching replacements")


def validate_research_worker_policy(manifest: dict, branches: list[dict]) -> None:
    has_research_worker = False
    for branch in branches:
        work_items = branch.get("work_items", [])
        if not isinstance(work_items, list):
            continue
        if any(isinstance(item, dict) and item.get("worker_type") == RESEARCH_WORKER_TYPE for item in work_items):
            has_research_worker = True
            break
    if not has_research_worker:
        return
    policy = manifest.get("research_worker_policy")
    if not isinstance(policy, dict):
        raise SystemExit("manifest research_worker_policy is required when any work item uses worker_type='research-worker'")
    if policy.get("enabled") is not True:
        raise SystemExit("manifest research_worker_policy.enabled must be true")
    if policy.get("worker_type") != RESEARCH_WORKER_TYPE:
        raise SystemExit("manifest research_worker_policy.worker_type must be 'research-worker'")
    for key in ["launcher", "network_scope", "local_access"]:
        if not isinstance(policy.get(key), str) or not policy.get(key, "").strip():
            raise SystemExit(f"manifest research_worker_policy.{key} must be non-empty")
    rejected, missing = CONTRACT.research_policy_defects(policy)
    if rejected:
        raise SystemExit(f"manifest research_worker_policy contains obsolete narrow-access phrase(s): {', '.join(rejected)}")
    if missing:
        raise SystemExit(f"manifest research_worker_policy is missing required boundary phrase(s): {', '.join(missing)}")


def branch_status_path(manifest_path: Path, branch: dict) -> Path:
    branch_id = branch.get("id")
    status_path = branch.get("status_path")
    if not isinstance(status_path, str) or not status_path.strip():
        raise SystemExit(f"branch {branch_id} missing status_path")
    return resolve(manifest_path.parent, require_relative_path(status_path, f"branch {branch_id}.status_path"))


def artifact_status(path: Path) -> str | None:
    if not path.exists():
        return None
    data = load_json(path)
    status = data.get("status")
    if status not in TERMINAL_STATUSES:
        raise SystemExit(f"status artifact {path} must contain a terminal status")
    return str(status)


def validate_completed_branch_statuses(
    manifest_path: Path,
    branches: list[dict],
    completed: set[str],
    scheduler_passed: set[str],
) -> None:
    branch_by_id = {str(branch["id"]): branch for branch in branches if isinstance(branch.get("id"), str)}
    for branch_id in sorted(completed - scheduler_passed):
        status_path = branch_status_path(manifest_path, branch_by_id[branch_id])
        status = artifact_status(status_path)
        if status is None:
            raise SystemExit(f"--completed-branch {branch_id} requires a passing status artifact or scheduler pass evidence: {status_path}")
        if status != "pass":
            raise SystemExit(f"--completed-branch {branch_id} points to non-pass status artifact {status_path}: {status}")


def scheduler_state_from_ledger(
    manifest_path: Path,
    ordered_branch_ids: list[str],
    max_active: int,
) -> tuple[set[str], set[str], set[str]]:
    scheduler_path = manifest_path.parent / CONTRACT.MAIN_SCHEDULER_PATH
    if not scheduler_path.exists():
        return set(), set(), set()
    ledger = load_json(scheduler_path)
    if ledger.get("schema_version") != 2:
        raise SystemExit(f"main scheduler ledger schema_version must be 2: {scheduler_path}")
    if ledger.get("scheduler_kind") != "main-branch-pool":
        raise SystemExit(f"main scheduler ledger kind mismatch: {scheduler_path}")
    if ledger.get("scheduler_path") != CONTRACT.MAIN_SCHEDULER_PATH:
        raise SystemExit(f"main scheduler ledger path mismatch: {scheduler_path}")
    if ledger.get("capacity") != max_active:
        raise SystemExit(f"main scheduler ledger capacity is stale: {scheduler_path}")
    ledger_ids = ledger.get("item_ids")
    if not isinstance(ledger_ids, list) or any(not isinstance(item, str) for item in ledger_ids):
        raise SystemExit(f"main scheduler ledger item_ids must be an array of strings: {scheduler_path}")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise SystemExit(f"main scheduler ledger events must be an array: {scheduler_path}")

    known = set(ordered_branch_ids)
    active: set[str] = set()
    finished_status: dict[str, str] = {}
    terminal_status: dict[str, str] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise SystemExit(f"main scheduler events[{index}] must be an object: {scheduler_path}")
        name = event.get("event")
        event_id = event.get("id")
        if name in {"ready", "launch", "finish", "close", "defer", "blocked"}:
            if not isinstance(event_id, str):
                raise SystemExit(f"main scheduler events[{index}].id must be a string: {scheduler_path}")
            if event_id not in known:
                continue
        if name == "launch":
            if event_id in active:
                raise SystemExit(f"main scheduler events[{index}] duplicates active launch for {event_id}")
            if len(active) >= max_active:
                raise SystemExit(f"main scheduler events[{index}] would exceed branch capacity")
            if terminal_status.get(str(event_id)) == "pass":
                raise SystemExit(f"main scheduler events[{index}] relaunches already-passing branch {event_id}")
            active.add(str(event_id))
            terminal_status.pop(str(event_id), None)
            finished_status.pop(str(event_id), None)
        elif name == "finish":
            status = event.get("status")
            if status not in TERMINAL_STATUSES:
                raise SystemExit(f"main scheduler events[{index}].status must be terminal")
            if event_id not in active:
                raise SystemExit(f"main scheduler events[{index}] finishes inactive branch {event_id}")
            finished_status[str(event_id)] = str(status)
        elif name == "close":
            if event_id not in active:
                raise SystemExit(f"main scheduler events[{index}] closes inactive branch {event_id}")
            if event_id not in finished_status:
                raise SystemExit(f"main scheduler events[{index}] closes unfinished branch {event_id}")
            active.discard(str(event_id))
            terminal_status[str(event_id)] = finished_status[str(event_id)]
        elif name in {"ready", "defer", "blocked", "under_capacity", "refill"}:
            continue
        else:
            raise SystemExit(f"main scheduler events[{index}].event is unsupported: {name!r}")

    passed = {branch_id for branch_id, status in terminal_status.items() if status == "pass"}
    non_pass = {branch_id for branch_id, status in terminal_status.items() if status != "pass"}
    return active, passed, non_pass


def require_unique_manifest_values(branches: list[dict]) -> None:
    for field in ["id", "branch_name", "worktree_path"]:
        seen: dict[str, str] = {}
        for branch in branches:
            value = branch.get(field)
            if not isinstance(value, str):
                continue
            owner = seen.get(value)
            if owner is not None:
                raise SystemExit(f"branch {branch.get('id')} {field} duplicates branch {owner}: {value}")
            seen[value] = str(branch.get("id", ""))

    reserved_bundle_paths = {
        "job.manifest.json",
        "main.prompt.md",
        "goal-bootloader.md",
        "PREFLIGHT_REPORT.md",
        "preflight.lint.json",
    }
    seen_paths: dict[str, str] = {}
    for branch in branches:
        for field in ["prompt", "status_path", "review_path"]:
            value = branch.get(field)
            if not isinstance(value, str):
                continue
            label = f"branch {branch.get('id')} {field}"
            if value in reserved_bundle_paths:
                raise SystemExit(f"{label} collides with reserved bundle file: {value}")
            owner = seen_paths.get(value)
            if owner is not None:
                raise SystemExit(f"{label} duplicates {owner}: {value}")
            seen_paths[value] = label


def select_delegation_mode(
    requested_mode: str,
    native_available: bool,
    fallback_reason: str,
) -> tuple[str, str | None]:
    if requested_mode == "native":
        return "native_agent", None
    if requested_mode == "cli":
        return "cli_worktree", "explicit_cli_delegation_mode"
    if native_available:
        return "native_agent", None
    return "cli_worktree", fallback_reason


def worktree_command(branch: dict, repo_root: Path, base_ref: str) -> str:
    name = str(branch["branch_name"])
    worktree_rel = require_relative_path(str(branch["worktree_path"]), "worktree_path")
    worktree_path = resolve(repo_root, worktree_rel)
    return f"git worktree add -b {shell_quote(name)} {shell_quote(worktree_path.as_posix())} {shell_quote(base_ref)}"


def branch_delegation_entry(
    branch: dict,
    manifest_path: Path,
    repo_root: Path,
    base_ref: str,
    selected_mode: str,
    fallback_reason: str | None,
    native_available: bool,
    native_availability_source: str | None,
) -> dict:
    branch_id = str(branch["id"])
    prompt_rel = require_relative_path(str(branch["prompt"]), f"branch {branch_id}.prompt")
    worktree_rel = require_relative_path(str(branch["worktree_path"]), f"branch {branch_id}.worktree_path")
    command = worktree_command(branch, repo_root, base_ref)
    return {
        "branch_id": branch_id,
        "preferred_delegation": "native_agent",
        "selected_delegation": selected_mode,
        "native_agent_available": native_available,
        "native_agent_availability_source": native_availability_source,
        "cli_fallback_reason": fallback_reason,
        "native_agent": {
            "skill": "goal-branch-orchestrator",
            "branch_prompt": prompt_rel,
            "manifest": manifest_path.as_posix(),
            "repo_root": repo_root.as_posix(),
            "worktree_path": resolve(repo_root, worktree_rel).as_posix(),
            "branch_name": str(branch["branch_name"]),
            "status_path": str(branch["status_path"]),
            "review_path": str(branch["review_path"]),
            "pre_review_gate_path": str(branch["pre_review_gate_path"]),
            "instructions": "Spawn a native branch-orchestrator agent for this branch and wait on native agent completion before collecting artifacts.",
        },
        "cli_worktree_fallback": {
            "allowed": selected_mode == "cli_worktree",
            "reason": fallback_reason,
            "command": command,
            "base_ref": base_ref,
        },
    }


def render_delegation_plan(
    branches: list[dict],
    manifest_path: Path,
    repo_root: Path,
    base_ref: str,
    selected_mode: str,
    fallback_reason: str | None,
    native_available: bool,
    native_availability_source: str | None,
) -> dict:
    return {
        "schema_version": 1,
        "kind": "branch-delegation-plan",
        "manifest": manifest_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "preferred_delegation": "native_agent",
        "selected_delegation": selected_mode,
        "native_agent_available": native_available,
        "native_agent_availability_source": native_availability_source,
        "cli_fallback_reason": fallback_reason,
        "branches": [
            branch_delegation_entry(
                branch,
                manifest_path,
                repo_root,
                base_ref,
                selected_mode,
                fallback_reason,
                native_available,
                native_availability_source,
            )
            for branch in branches
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--audit", required=True)
    parser.add_argument("--wave")
    parser.add_argument("--branch", action="append", default=[], help="Render one branch id. Repeat for multiple eligible branches.")
    parser.add_argument("--completed-branch", action="append", default=[], help="Branch id whose status has completed and been accepted.")
    parser.add_argument("--active-branch", action="append", default=[], help="Branch id already active; used only with --list-ready.")
    parser.add_argument("--list-ready", action="store_true", help="Print eligible unstarted branch ids, one per line.")
    parser.add_argument("--limit", type=int, help="Maximum branch ids to print with --list-ready; values above remaining capacity are clamped.")
    parser.add_argument("--list-waves", action="store_true")
    parser.add_argument(
        "--delegation-mode",
        choices=["auto", "native", "cli"],
        default="auto",
        help="For selected branch rendering, prefer native branch-agent delegation when available and record CLI fallback provenance.",
    )
    parser.add_argument(
        "--native-agent-available",
        action="store_true",
        help="Treat native branch-agent delegation as available for this render.",
    )
    parser.add_argument(
        "--native-agent-unavailable-reason",
        default="native_agent_delegation_unavailable",
        help="Fallback reason recorded when --delegation-mode auto selects CLI worktrees.",
    )
    parser.add_argument("--delegation-report", help="Write a JSON branch delegation plan with native/CLI selection provenance.")
    parser.add_argument("--json", action="store_true", help="Print the branch delegation plan as JSON instead of shell/comments.")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    audit_path = resolve_absolute_path(args.audit, "--audit", must_exist=True)
    manifest = load_json(manifest_path)
    audit = load_json(audit_path)

    if not git_ok(repo_root, "rev-parse", "--show-toplevel"):
        raise SystemExit(f"repo root is not a git checkout: {repo_root}")
    if audit.get("manifest") != manifest_path.as_posix():
        raise SystemExit("prompt audit manifest identity does not match --manifest")
    if audit.get("repo_root") != repo_root.as_posix():
        raise SystemExit("prompt audit repo_root identity does not match --repo-root")

    if audit.get("status") != "pass" or audit.get("can_start") is not True:
        raise SystemExit("prompt audit did not pass; refusing to render branch creation commands")
    validate_audit_telemetry(audit_path)
    require_string_list(audit.get("checked_files"), "prompt audit checked_files", min_items=1)
    require_string_list(audit.get("commands_run"), "prompt audit commands_run", min_items=1)
    audit_defects = audit.get("defects", [])
    if not isinstance(audit_defects, list):
        raise SystemExit("prompt audit defects must be an array")
    blocking_audit_defects = []
    for item in audit_defects:
        if not isinstance(item, dict):
            blocking_audit_defects.append("non-object audit defect")
            continue
        severity = item.get("severity")
        if severity not in {"critical", "major", "minor"}:
            blocking_audit_defects.append(str(item.get("message", "invalid audit defect severity")))
            continue
        if not isinstance(item.get("file"), str) or not item["file"].strip():
            blocking_audit_defects.append("audit defect missing file")
            continue
        if not isinstance(item.get("message"), str) or not item["message"].strip():
            blocking_audit_defects.append("audit defect missing message")
            continue
        if severity in {"critical", "major"}:
            blocking_audit_defects.append(str(item.get("message", "audit defect")))
    if blocking_audit_defects:
        raise SystemExit("prompt audit passed with blocking defects; refusing branch creation")
    missing_dod_items = audit.get("missing_dod_items", [])
    if not isinstance(missing_dod_items, list):
        raise SystemExit("prompt audit missing_dod_items must be an array")
    if missing_dod_items:
        raise SystemExit("prompt audit passed with missing DoD items; refusing branch creation")
    for key in ["artifact_policy", "cleanup_policy"]:
        if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip():
            raise SystemExit(f"manifest {key} must be present and non-empty")

    max_active = manifest.get("max_active_branch_agents", MAX_ACTIVE_BRANCH_AGENTS)
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_ACTIVE_BRANCH_AGENTS:
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 4")
    parallelization = manifest.get("parallelization", {})
    if not isinstance(parallelization, dict) or parallelization.get("parallelism_default") is not True:
        raise SystemExit("manifest must declare parallelization.parallelism_default=true")
    if parallelization.get("max_branches_per_wave") != MAX_ACTIVE_BRANCH_AGENTS:
        raise SystemExit("manifest parallelization.max_branches_per_wave must be 4")
    if parallelization.get("max_waves") != MAX_WAVES:
        raise SystemExit("manifest parallelization.max_waves must be 5")
    if parallelization.get("scheduling_mode") != "rolling":
        raise SystemExit("manifest parallelization.scheduling_mode must be 'rolling'")
    if parallelization.get("scheduler_path") != CONTRACT.MAIN_SCHEDULER_PATH:
        raise SystemExit(f"manifest parallelization.scheduler_path must be {CONTRACT.MAIN_SCHEDULER_PATH!r}")
    dependency_policy = parallelization.get("dependency_policy", "")
    if not isinstance(dependency_policy, str) or not dependency_policy.strip():
        raise SystemExit("manifest parallelization.dependency_policy must be non-empty")
    wave_execution = parallelization.get("wave_execution", "")
    if not isinstance(wave_execution, str) or "saturat" not in wave_execution.lower() or "depends_on" not in wave_execution:
        raise SystemExit("manifest parallelization.wave_execution must describe rolling saturation and depends_on deferral")
    if "serial_reason" in parallelization:
        raise SystemExit("manifest parallelization.serial_reason is obsolete; use serial_reasons")
    serial_reasons = parallelization.get("serial_reasons", [])
    if not isinstance(serial_reasons, list) or any(not isinstance(item, str) or not item.strip() for item in serial_reasons):
        raise SystemExit("manifest parallelization.serial_reasons must be an array of non-empty strings")
    parallelization_rationale = parallelization.get("parallelization_rationale", "")
    has_parallelization_reason = (
        bool(serial_reasons)
    ) or (
        isinstance(parallelization_rationale, str)
        and bool(parallelization_rationale.strip())
    )
    if max_active < MAX_ACTIVE_BRANCH_AGENTS and not has_parallelization_reason:
        raise SystemExit("max_active_branch_agents below 4 requires serial_reasons or parallelization_rationale")

    waves = manifest.get("waves") or []
    if len(waves) > MAX_WAVES:
        raise SystemExit("manifest must not contain more than 5 waves")
    manifest_branches = manifest.get("branches", [])
    if not isinstance(manifest_branches, list) or not manifest_branches:
        raise SystemExit("manifest branches must be a non-empty array")
    require_unique_manifest_values([branch for branch in manifest_branches if isinstance(branch, dict)])
    manifest_branch_ids = [branch.get("id") for branch in manifest_branches]
    if len(manifest_branch_ids) > MAX_ACTIVE_BRANCH_AGENTS * MAX_WAVES:
        raise SystemExit("manifest must not contain more than 20 branches")
    if len(manifest_branch_ids) == 1 and not serial_reasons:
        raise SystemExit("single-branch manifests require parallelization.serial_reasons")
    if len(manifest_branch_ids) != len(set(manifest_branch_ids)):
        raise SystemExit("manifest branch ids must be unique")
    wave_branch_ids = []
    for idx, wave in enumerate(waves):
        branch_ids = wave.get("branches", [])
        if not isinstance(branch_ids, list) or not branch_ids:
            raise SystemExit(f"wave {wave.get('id')} must list at least one branch")
        if len(branch_ids) > MAX_ACTIVE_BRANCH_AGENTS:
            raise SystemExit(f"wave {wave.get('id')} has more than 4 branches")
        wave_branch_ids.extend(branch_ids)
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        raise SystemExit("branch ids must not appear in more than one wave")
    if waves and set(wave_branch_ids) != set(manifest_branch_ids):
        raise SystemExit("waves must cover exactly the manifest branch ids")
    branch_dependencies = validate_branch_dependencies([branch for branch in manifest_branches if isinstance(branch, dict)])

    manifest_dir = manifest_path.parent
    for branch in manifest_branches:
        if not isinstance(branch, dict):
            raise SystemExit("manifest branch entries must be objects")
        for key in ["prompt", "status_path", "review_path", "pre_review_gate_path", "worktree_path", "branch_name"]:
            if key not in branch:
                raise SystemExit(f"branch {branch.get('id')} missing {key}")
        require_relative_path(branch["prompt"], "prompt")
        require_relative_path(branch["status_path"], "status_path")
        require_relative_path(branch["review_path"], "review_path")
        require_relative_path(branch["pre_review_gate_path"], "pre_review_gate_path")
        if branch["pre_review_gate_path"] != CONTRACT.pre_review_gate_path(str(branch.get("id", ""))):
            raise SystemExit(f"branch {branch.get('id')} pre_review_gate_path must be {CONTRACT.pre_review_gate_path(str(branch.get('id', '')))!r}")
        require_relative_path(branch["worktree_path"], "worktree_path")
        prompt_path = resolve(manifest_dir, branch["prompt"])
        if not prompt_path.exists():
            raise SystemExit(f"branch prompt does not exist: {prompt_path}")
        validate_branch_worker_contract(branch)
    validate_research_worker_policy(manifest, [branch for branch in manifest_branches if isinstance(branch, dict)])

    if args.list_waves:
        for wave in waves:
            print(f"{wave.get('id')}: {', '.join(wave.get('branches', []))}")
        return 0

    ordered_branch_ids = [branch["id"] for branch in manifest_branches if isinstance(branch, dict) and isinstance(branch.get("id"), str)]
    known_ids = set(ordered_branch_ids)
    completed = set()
    for value in args.completed_branch:
        if value not in known_ids:
            raise SystemExit(f"--completed-branch references unknown branch id: {value}")
        completed.add(value)
    active = set()
    for value in args.active_branch:
        if value not in known_ids:
            raise SystemExit(f"--active-branch references unknown branch id: {value}")
        active.add(value)
    scheduler_active, scheduler_passed, scheduler_non_pass = scheduler_state_from_ledger(manifest_path, ordered_branch_ids, max_active)
    if completed & scheduler_non_pass:
        raise SystemExit("--completed-branch includes branch ids whose scheduler status is non-pass: " + ", ".join(sorted(completed & scheduler_non_pass)))
    if completed & scheduler_active:
        raise SystemExit("--completed-branch includes scheduler-active branch ids: " + ", ".join(sorted(completed & scheduler_active)))
    if active & scheduler_passed:
        raise SystemExit("--active-branch includes scheduler-passed branch ids: " + ", ".join(sorted(active & scheduler_passed)))
    if active & scheduler_non_pass:
        raise SystemExit("--active-branch includes non-pass closed branch ids; record a scheduler relaunch first: " + ", ".join(sorted(active & scheduler_non_pass)))
    validate_completed_branch_statuses(
        manifest_path,
        [branch for branch in manifest_branches if isinstance(branch, dict)],
        completed,
        scheduler_passed,
    )
    completed |= scheduler_passed
    active |= scheduler_active
    if completed & active:
        raise SystemExit("--completed-branch and --active-branch must not overlap")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer")
    if args.list_ready:
        if args.wave or args.branch:
            raise SystemExit("--list-ready cannot be combined with --wave or --branch")
        available_capacity = max_active - len(active)
        if available_capacity <= 0:
            return 0
        requested_limit = args.limit if args.limit is not None else available_capacity
        limit = min(requested_limit, available_capacity)
        ready = []
        for branch in manifest_branches:
            bid = branch.get("id")
            if bid in completed or bid in active or bid in scheduler_non_pass:
                continue
            deps = branch_dependencies.get(bid, [])
            if all(dep in completed for dep in deps):
                ready.append(bid)
        for bid in ready[:limit]:
            print(bid)
        return 0

    selected_ids = None
    if args.branch and args.wave:
        raise SystemExit("--branch cannot be combined with --wave")
    if args.branch:
        selected_ids = set()
        for bid in args.branch:
            if bid not in known_ids:
                raise SystemExit(f"--branch references unknown branch id: {bid}")
            deps = branch_dependencies.get(bid, [])
            unresolved = [dep for dep in deps if dep not in completed]
            if unresolved:
                raise SystemExit(f"branch {bid} is not ready; unresolved depends_on: {', '.join(unresolved)}")
            if bid in completed:
                raise SystemExit(f"branch {bid} already has passing scheduler/status evidence")
            if bid in active:
                raise SystemExit(f"branch {bid} is already scheduler-active")
            if bid in scheduler_non_pass:
                raise SystemExit(f"branch {bid} has non-pass terminal scheduler evidence; do not render a new worktree")
            selected_ids.add(bid)
    elif waves and not args.wave:
        raise SystemExit("manifest has waves; pass --branch <branch-id>, --list-ready, or --wave <wave-id>")
    if waves:
        if args.wave:
            matches = [wave for wave in waves if wave.get("id") == args.wave]
            if not matches:
                raise SystemExit(f"unknown wave: {args.wave}")
            selected_ids = set(matches[0].get("branches", []))
            for bid in sorted(selected_ids):
                deps = branch_dependencies.get(bid, [])
                unresolved = [dep for dep in deps if dep not in completed]
                if unresolved:
                    raise SystemExit(f"branch {bid} is not ready; unresolved depends_on: {', '.join(unresolved)}")
                if bid in completed:
                    raise SystemExit(f"branch {bid} already has passing scheduler/status evidence")
                if bid in active:
                    raise SystemExit(f"branch {bid} is already scheduler-active")
                if bid in scheduler_non_pass:
                    raise SystemExit(f"branch {bid} has non-pass terminal scheduler evidence; do not render a new worktree")
    if selected_ids is not None and len(active) + len(selected_ids) > max_active:
        raise SystemExit("selected branches plus active branches would exceed max_active_branch_agents")

    base_ref = manifest.get("base_ref", "main")
    if not safe_branch_name(base_ref):
        raise SystemExit(f"base_ref is not safe: {base_ref!r}")
    if not git_ok(repo_root, "rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"):
        raise SystemExit(f"base_ref does not resolve to a commit: {base_ref}")
    selected_branches = []
    seen_names = set()
    seen_worktrees = set()
    for branch in manifest_branches:
        if selected_ids is not None and branch.get("id") not in selected_ids:
            continue
        name = branch["branch_name"]
        if name in seen_names:
            raise SystemExit(f"duplicate branch_name: {name}")
        seen_names.add(name)
        if not safe_branch_name(name) or not git_ok(repo_root, "check-ref-format", "--branch", name):
            raise SystemExit(f"branch_name is not safe: {name!r}")
        if branch_exists(repo_root, name):
            if branch_checked_out_in_worktree(repo_root, name):
                raise SystemExit(f"target branch is already checked out in a worktree: {name}")
            raise SystemExit(
                f"target branch already exists and will not be reused for a fresh branch worktree: {name}; "
                "choose a unique branch_name or remove the stale branch explicitly"
            )
        worktree_rel = require_relative_path(branch["worktree_path"], "worktree_path")
        worktree_path = resolve(repo_root, worktree_rel)
        if worktree_path in seen_worktrees:
            raise SystemExit(f"duplicate worktree_path: {worktree_path}")
        seen_worktrees.add(worktree_path)
        if worktree_path.exists():
            raise SystemExit(f"target worktree path already exists: {worktree_path}")
        selected_branches.append(branch)
    native_available, native_source = native_delegation_available(args.native_agent_available)
    selected_mode, fallback_reason = select_delegation_mode(
        args.delegation_mode,
        native_available,
        str(args.native_agent_unavailable_reason or "native_agent_delegation_unavailable"),
    )
    plan = render_delegation_plan(
        selected_branches,
        manifest_path,
        repo_root,
        base_ref,
        selected_mode,
        fallback_reason,
        native_available,
        native_source,
    )
    if args.delegation_report:
        report_path = resolve_absolute_path(args.delegation_report, "--delegation-report", must_exist=False)
        write_json(report_path, plan)
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    elif selected_mode == "native_agent":
        for entry in plan["branches"]:
            native = entry["native_agent"]
            print(f"# native-agent branch delegation: {entry['branch_id']}")
            print(f"# skill: {native['skill']}")
            print(f"# manifest: {native['manifest']}")
            print(f"# branch_prompt: {native['branch_prompt']}")
            print(f"# worktree_path: {native['worktree_path']}")
            print("# cli fallback command, only if native delegation is unavailable:")
            print(f"# {entry['cli_worktree_fallback']['command']}")
    else:
        for entry in plan["branches"]:
            print(entry["cli_worktree_fallback"]["command"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
