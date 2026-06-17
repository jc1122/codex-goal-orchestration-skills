#!/usr/bin/env python3
"""List ready worker packets for rolling branch-worker scheduling."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
require_safe_id = PATH_RULES.require_safe_id
require_safe_label = PATH_RULES.require_safe_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
require_relative_path = PATH_RULES.require_relative_path
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
WORK_ITEM_ROLES = set(CONTRACT.WORK_ITEM_ROLES)
TERMINAL_STATUSES = {"pass", "partial", "blocked", "failed"}


def is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"manifest must be a JSON object: {path}")
    return data


def require_string_list(value: object, field: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit(f"{field} must be a list")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    if len(result) < min_items:
        raise SystemExit(f"{field} must contain at least {min_items} item(s)")
    return result


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be a list")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if not matches:
        raise SystemExit(f"unknown branch id: {branch_id}")
    if len(matches) > 1:
        raise SystemExit(f"manifest contains duplicate branch id: {branch_id}")
    return matches[0]


def validate_work_items(branch: dict, branch_id: str) -> tuple[list[dict], int]:
    work_items = branch.get("work_items")
    if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id} work_items must contain 1 to 4 work item objects")
    max_active = branch.get("max_active_worker_packets")
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id} max_active_worker_packets must be an integer from 1 to 4")
    worker_parallelism = branch.get("worker_parallelism")
    if not isinstance(worker_parallelism, dict):
        raise SystemExit(f"branch {branch_id} worker_parallelism must be an object")
    if worker_parallelism.get("parallelism_default") is not True:
        raise SystemExit(f"branch {branch_id} worker_parallelism.parallelism_default must be true")
    if worker_parallelism.get("scheduling_mode") != "rolling":
        raise SystemExit(f"branch {branch_id} worker_parallelism.scheduling_mode must be 'rolling'")
    if worker_parallelism.get("max_active_worker_packets") != max_active:
        raise SystemExit(
            f"branch {branch_id} worker_parallelism.max_active_worker_packets must match branch max_active_worker_packets"
        )
    if worker_parallelism.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id} worker_parallelism.max_worker_packets_per_branch must be 4")
    slot_refill = worker_parallelism.get("slot_refill", "")
    if not isinstance(slot_refill, str) or "launch" not in slot_refill.lower():
        raise SystemExit(f"branch {branch_id} worker_parallelism.slot_refill must describe launching replacements")
    dependency_policy = worker_parallelism.get("dependency_policy", "")
    if not isinstance(dependency_policy, str) or "depends_on" not in dependency_policy:
        raise SystemExit(f"branch {branch_id} worker_parallelism.dependency_policy must mention depends_on")

    seen_ids: set[str] = set()
    validated = []
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            raise SystemExit(f"branch {branch_id} work_items[{index}] must be an object")
        item_id = require_safe_label(str(item.get("id", "")), f"branch {branch_id} work_items[{index}].id")
        if item_id in seen_ids:
            raise SystemExit(f"branch {branch_id} duplicate work item id: {item_id}")
        seen_ids.add(item_id)
        packet_id = require_safe_label(
            str(item.get("packet_id", "")), f"branch {branch_id} work_items[{index}].packet_id"
        )
        expected_packet_id = f"{branch_id}-{item_id}"
        if packet_id != expected_packet_id:
            raise SystemExit(f"branch {branch_id} work_items[{index}].packet_id must be {expected_packet_id!r}")
        worker_type = item.get("worker_type", "worker")
        if worker_type not in WORK_ITEM_ROLES:
            raise SystemExit(
                f"branch {branch_id} work_items[{index}].worker_type must be 'worker' or 'research-worker'"
            )
        for key, min_items in [
            ("owned_paths", 1),
            ("verification", 1),
            ("dod", 1),
            ("context_files", 0),
            ("depends_on", 0),
        ]:
            values = require_string_list(
                item.get(key, []), f"branch {branch_id} work_items[{index}].{key}", min_items=min_items
            )
            if key in {"owned_paths", "context_files"}:
                for value in values:
                    require_relative_path(value, f"branch {branch_id} work_items[{index}].{key}")
        validated.append(item)

    item_order = {item["id"]: index for index, item in enumerate(validated)}
    for index, item in enumerate(validated):
        for dep in item.get("depends_on", []):
            if dep not in item_order:
                raise SystemExit(f"branch {branch_id} work_items[{index}] depends on unknown work item: {dep}")
            if item_order[dep] >= index:
                raise SystemExit(
                    f"branch {branch_id} work_items[{index}] depends_on must reference only prior work item ids: {dep}"
                )
    return validated, max_active


def normalize_packet_ids(values: list[str], known: set[str], field: str) -> set[str]:
    result = set()
    for value in values:
        packet_id = require_safe_label(value, field)
        if packet_id not in known:
            raise SystemExit(f"{field} references unknown worker packet id: {packet_id}")
        result.add(packet_id)
    return result


def worker_status_path(manifest_path: Path, item: dict) -> Path:
    packet_id = str(item["packet_id"])
    if item.get("worker_type", "worker") == "research-worker":
        return manifest_path.parent / "research" / packet_id / "research.json"
    return manifest_path.parent / "workers" / packet_id / "status.json"


def artifact_status(path: Path) -> str | None:
    if not path.exists():
        return None
    data = load_json(path)
    status = data.get("status")
    if status not in TERMINAL_STATUSES:
        raise SystemExit(f"status artifact {path} must contain a terminal status")
    return str(status)


def validate_completed_worker_statuses(
    manifest_path: Path,
    work_items: list[dict],
    completed: set[str],
    scheduler_passed: set[str],
) -> None:
    item_by_packet = {str(item["packet_id"]): item for item in work_items}
    for packet_id in sorted(completed - scheduler_passed):
        status_path = worker_status_path(manifest_path, item_by_packet[packet_id])
        status = artifact_status(status_path)
        if status is None:
            raise SystemExit(
                f"--completed-worker {packet_id} requires a passing status artifact or scheduler pass evidence: {status_path}"
            )
        if status != "pass":
            raise SystemExit(
                f"--completed-worker {packet_id} points to non-pass status artifact {status_path}: {status}"
            )


def scheduler_state_from_ledger(
    manifest_path: Path,
    branch_id: str,
    ordered_packets: list[str],
    max_active: int,
) -> tuple[set[str], set[str], set[str]]:
    scheduler_path = manifest_path.parent / CONTRACT.worker_scheduler_path(branch_id)
    if not scheduler_path.exists():
        return set(), set(), set()
    ledger = load_json(scheduler_path)
    if ledger.get("schema_version") != 2:
        raise SystemExit(f"worker scheduler ledger schema_version must be 2: {scheduler_path}")
    if ledger.get("scheduler_kind") != "branch-worker-pool":
        raise SystemExit(f"worker scheduler ledger kind mismatch: {scheduler_path}")
    if ledger.get("scheduler_path") != CONTRACT.worker_scheduler_path(branch_id):
        raise SystemExit(f"worker scheduler ledger path mismatch: {scheduler_path}")
    if ledger.get("capacity") != max_active:
        raise SystemExit(f"worker scheduler ledger capacity is stale: {scheduler_path}")
    if ledger.get("item_ids") != ordered_packets:
        raise SystemExit(f"worker scheduler ledger item_ids are stale: {scheduler_path}")
    events = ledger.get("events")
    if not isinstance(events, list):
        raise SystemExit(f"worker scheduler ledger events must be an array: {scheduler_path}")

    known = set(ordered_packets)
    active: set[str] = set()
    finished_status: dict[str, str] = {}
    terminal_status: dict[str, str] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise SystemExit(f"worker scheduler events[{index}] must be an object: {scheduler_path}")
        name = event.get("event")
        event_id = event.get("id")
        if name in {"ready", "launch", "finish", "close", "defer", "blocked"} and (
            not isinstance(event_id, str) or event_id not in known
        ):
            raise SystemExit(
                f"worker scheduler events[{index}].id is not a manifest worker packet id: {scheduler_path}"
            )
        if name == "launch":
            if event_id in active:
                raise SystemExit(f"worker scheduler events[{index}] duplicates active launch for {event_id}")
            if len(active) >= max_active:
                raise SystemExit(f"worker scheduler events[{index}] would exceed worker capacity")
            if terminal_status.get(str(event_id)) == "pass":
                raise SystemExit(f"worker scheduler events[{index}] relaunches already-passing packet {event_id}")
            active.add(str(event_id))
            terminal_status.pop(str(event_id), None)
            finished_status.pop(str(event_id), None)
        elif name == "finish":
            status = event.get("status")
            if status not in TERMINAL_STATUSES:
                raise SystemExit(f"worker scheduler events[{index}].status must be terminal")
            if event_id not in active:
                raise SystemExit(f"worker scheduler events[{index}] finishes inactive packet {event_id}")
            finished_status[str(event_id)] = str(status)
        elif name == "close":
            if event_id not in active:
                raise SystemExit(f"worker scheduler events[{index}] closes inactive packet {event_id}")
            if event_id not in finished_status:
                raise SystemExit(f"worker scheduler events[{index}] closes unfinished packet {event_id}")
            active.discard(str(event_id))
            terminal_status[str(event_id)] = finished_status[str(event_id)]
        elif name in {"ready", "defer", "blocked", "under_capacity", "refill"}:
            continue
        else:
            raise SystemExit(f"worker scheduler events[{index}].event is unsupported: {name!r}")

    passed = {packet_id for packet_id, status in terminal_status.items() if status == "pass"}
    non_pass = {packet_id for packet_id, status in terminal_status.items() if status != "pass"}
    return active, passed, non_pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id", required=True)
    parser.add_argument(
        "--completed-worker", action="append", default=[], help="Completed and integrated worker packet id."
    )
    parser.add_argument("--active-worker", action="append", default=[], help="Currently active worker packet id.")
    parser.add_argument(
        "--list-ready", action="store_true", help="Print eligible unstarted worker packet ids, one per line."
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum packet ids to print with --list-ready; values above remaining capacity are clamped.",
    )
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    manifest = load_json(manifest_path)
    branch_id = require_safe_id(args.branch_id.upper(), "--branch-id")
    branch = branch_entry(manifest, branch_id)
    work_items, max_active = validate_work_items(branch, branch_id)
    ordered_packets = [str(item["packet_id"]) for item in work_items]
    packet_to_item = {item["packet_id"]: item["id"] for item in work_items}
    known_packets = set(packet_to_item)

    completed = normalize_packet_ids(args.completed_worker, known_packets, "--completed-worker")
    active = normalize_packet_ids(args.active_worker, known_packets, "--active-worker")
    scheduler_active, scheduler_passed, scheduler_non_pass = scheduler_state_from_ledger(
        manifest_path, branch_id, ordered_packets, max_active
    )
    if completed & scheduler_non_pass:
        raise SystemExit(
            "--completed-worker includes packet ids whose scheduler status is non-pass: "
            + ", ".join(sorted(completed & scheduler_non_pass))
        )
    if completed & scheduler_active:
        raise SystemExit(
            "--completed-worker includes scheduler-active packet ids: "
            + ", ".join(sorted(completed & scheduler_active))
        )
    if active & scheduler_passed:
        raise SystemExit(
            "--active-worker includes scheduler-passed packet ids: " + ", ".join(sorted(active & scheduler_passed))
        )
    if active & scheduler_non_pass:
        raise SystemExit(
            "--active-worker includes non-pass closed packet ids; record a scheduler relaunch first: "
            + ", ".join(sorted(active & scheduler_non_pass))
        )
    validate_completed_worker_statuses(manifest_path, work_items, completed, scheduler_passed)
    completed |= scheduler_passed
    active |= scheduler_active
    if completed & active:
        raise SystemExit("--completed-worker and --active-worker must not overlap")
    if len(active) > max_active:
        raise SystemExit("active workers exceed max_active_worker_packets")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer")

    if not args.list_ready:
        raise SystemExit("pass --list-ready")

    available_capacity = max_active - len(active)
    if available_capacity <= 0:
        return 0
    requested_limit = args.limit if args.limit is not None else available_capacity
    limit = min(requested_limit, available_capacity)

    completed_items = {packet_to_item[packet_id] for packet_id in completed}
    ready = []
    for item in work_items:
        packet_id = item["packet_id"]
        if packet_id in completed or packet_id in active:
            continue
        deps = item.get("depends_on", [])
        if all(dep in completed_items for dep in deps):
            ready.append(packet_id)
    for packet_id in ready[:limit]:
        print(packet_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
