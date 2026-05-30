#!/usr/bin/env python3
"""Deterministically update a schema v2 scheduler ledger from manifest state."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load required module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT = _load_module("goal_shared_orchestration_contract", SCRIPT_DIR / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SCRIPT_DIR / "path_rules.py")
STATUS_VALIDATION = _load_module("goal_shared_status_validation", SCRIPT_DIR / "status_validation.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
require_safe_id = PATH_RULES.require_safe_id
is_repo_relative_path = PATH_RULES.is_repo_relative_path

TERMINAL_STATUSES = {"pass", "partial", "blocked", "failed"}
REASON_CODES = {
    "artifact_invalid",
    "capacity_limit",
    "contention",
    "dependency_failed",
    "dependency_pending",
    "launcher_failed",
    "native_agent_unreachable",
    "no_ready_work",
    "operator_requested",
    "process_exited_blocked",
    "stale_active",
    "timeout",
}


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp_name = handle.name
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_name, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def deterministic_timestamp(seq: int) -> str:
    return (datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=seq)).isoformat(timespec="seconds").replace("+00:00", "Z")


def require_string_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit(f"{field} must be an array")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def manifest_branch(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be an array")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest must contain exactly one branch {branch_id!r}")
    return matches[0]


def branch_dependencies(manifest: dict) -> dict[str, list[str]]:
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not branches:
        raise SystemExit("manifest branches must be a non-empty array")
    known = [str(branch.get("id")) for branch in branches if isinstance(branch, dict) and isinstance(branch.get("id"), str)]
    known_set = set(known)
    dependencies: dict[str, list[str]] = {}
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        branch_id = branch.get("id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise SystemExit("manifest branch id must be a non-empty string")
        require_safe_id(branch_id, "manifest branch id")
        deps = require_string_list(branch.get("depends_on", []), f"branch {branch_id}.depends_on")
        for dep in deps:
            if dep not in known_set:
                raise SystemExit(f"branch {branch_id} depends on unknown branch id: {dep}")
        dependencies[branch_id] = deps
    return dependencies


def worker_dependencies(branch: dict, branch_id: str) -> tuple[list[str], dict[str, list[str]]]:
    work_items = branch.get("work_items")
    if not isinstance(work_items, list) or not work_items:
        raise SystemExit(f"branch {branch_id} work_items must be a non-empty array")
    if len(work_items) > CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id} must not declare more than {CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH} work items")
    packet_ids: list[str] = []
    item_to_packet: dict[str, str] = {}
    dependencies: dict[str, list[str]] = {}
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            raise SystemExit(f"branch {branch_id} work_items[{index}] must be an object")
        item_id = item.get("id")
        packet_id = item.get("packet_id")
        if not isinstance(item_id, str) or not item_id.strip():
            raise SystemExit(f"branch {branch_id} work_items[{index}].id must be a non-empty string")
        if not isinstance(packet_id, str) or not packet_id.strip():
            raise SystemExit(f"branch {branch_id} work_items[{index}].packet_id must be a non-empty string")
        require_safe_id(item_id, f"branch {branch_id} work_items[{index}].id")
        require_safe_id(packet_id, f"branch {branch_id} work_items[{index}].packet_id")
        expected = f"{branch_id}-{item_id}"
        if packet_id != expected:
            raise SystemExit(f"branch {branch_id} work_items[{index}].packet_id must be {expected!r}")
        if item_id in item_to_packet:
            raise SystemExit(f"branch {branch_id} duplicates work item id: {item_id}")
        if packet_id in packet_ids:
            raise SystemExit(f"branch {branch_id} duplicates packet id: {packet_id}")
        deps = require_string_list(item.get("depends_on", []), f"branch {branch_id} work_items[{index}].depends_on")
        packet_deps = []
        for dep in deps:
            if dep not in item_to_packet:
                raise SystemExit(f"branch {branch_id} work item {item_id} depends on unknown or later item: {dep}")
            packet_deps.append(item_to_packet[dep])
        item_to_packet[item_id] = packet_id
        packet_ids.append(packet_id)
        dependencies[packet_id] = packet_deps
    return packet_ids, dependencies


def scheduler_spec(manifest_path: Path, manifest: dict, scope: str, branch_id: str | None) -> dict:
    if scope == "main":
        branches = manifest.get("branches")
        if not isinstance(branches, list) or not branches:
            raise SystemExit("manifest branches must be a non-empty array")
        item_ids = [branch.get("id") for branch in branches if isinstance(branch, dict)]
        if any(not isinstance(item, str) or not item.strip() for item in item_ids):
            raise SystemExit("manifest branches must all have non-empty string ids")
        max_active = manifest.get("max_active_branch_agents", CONTRACT.MAX_ACTIVE_BRANCH_AGENTS)
        if not isinstance(max_active, int) or isinstance(max_active, bool) or max_active < 1 or max_active > CONTRACT.MAX_ACTIVE_BRANCH_AGENTS:
            raise SystemExit("manifest max_active_branch_agents must be an integer from 1 to 4")
        parallelization = manifest.get("parallelization")
        scheduler_path = CONTRACT.MAIN_SCHEDULER_PATH
        if isinstance(parallelization, dict) and isinstance(parallelization.get("scheduler_path"), str):
            scheduler_path = parallelization["scheduler_path"]
        if not is_repo_relative_path(scheduler_path):
            raise SystemExit("manifest parallelization.scheduler_path must be bundle-relative")
        return {
            "kind": "main-branch-pool",
            "path": scheduler_path,
            "capacity": max_active,
            "item_ids": item_ids,
            "dependencies": branch_dependencies(manifest),
        }

    if branch_id is None:
        raise SystemExit("--branch-id is required for --scope worker")
    branch_id = require_safe_id(branch_id, "--branch-id")
    branch = manifest_branch(manifest, branch_id)
    item_ids, dependencies = worker_dependencies(branch, branch_id)
    max_active = branch.get("max_active_worker_packets", CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH)
    if not isinstance(max_active, int) or isinstance(max_active, bool) or max_active < 1 or max_active > CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id} max_active_worker_packets must be an integer from 1 to 4")
    return {
        "kind": "branch-worker-pool",
        "path": CONTRACT.worker_scheduler_path(branch_id),
        "capacity": max_active,
        "item_ids": item_ids,
        "dependencies": dependencies,
    }


def load_or_create_ledger(path: Path, spec: dict, manifest_path: Path, *, create: bool) -> dict:
    expected = {
        "schema_version": 2,
        "scheduler_kind": spec["kind"],
        "scheduler_path": spec["path"],
        "manifest_sha256": sha256_file(manifest_path),
        "capacity": spec["capacity"],
        "item_ids": spec["item_ids"],
        "events": [],
    }
    if not path.exists():
        if not create:
            raise SystemExit(f"scheduler ledger does not exist; pass --init to create: {path}")
        return expected
    ledger = read_json(path)
    for key in ["schema_version", "scheduler_kind", "scheduler_path", "capacity", "item_ids"]:
        if ledger.get(key) != expected[key]:
            raise SystemExit(f"scheduler ledger {key} does not match manifest-derived value")
    if ledger.get("manifest_sha256") != expected["manifest_sha256"]:
        raise SystemExit("scheduler ledger manifest_sha256 is stale; regenerate or start a new ledger")
    if not isinstance(ledger.get("events"), list):
        raise SystemExit("scheduler ledger events must be an array")
    return ledger


def replay(ledger: dict, item_ids: list[str], dependencies: dict[str, list[str]], capacity: int, *, allow_relaunch: bool = False) -> dict:
    active: set[str] = set()
    ready: set[str] = set()
    launched: set[str] = set()
    finished: set[str] = set()
    closed: set[str] = set()
    blocked: set[str] = set()
    deferred: set[str] = set()
    under_capacity: set[str] = set()
    blocked_excuses: set[str] = set()
    deferred_excuses: set[str] = set()
    under_capacity_excuses: set[str] = set()
    finished_status: dict[str, str] = {}
    known = set(item_ids)
    for index, event in enumerate(ledger.get("events", [])):
        if not isinstance(event, dict):
            raise SystemExit(f"scheduler events[{index}] must be an object")
        name = event.get("event")
        event_id = event.get("id")
        if name in {"ready", "launch", "finish", "close", "defer", "blocked"}:
            if not isinstance(event_id, str) or event_id not in known:
                raise SystemExit(f"scheduler events[{index}].id must be a manifest scheduler id")
        if name == "ready":
            ready.add(str(event_id))
        elif name == "launch":
            if len(active) >= capacity:
                raise SystemExit(f"scheduler events[{index}] would exceed capacity")
            if event_id in active:
                raise SystemExit(f"scheduler events[{index}] duplicates active launch for {event_id}")
            launched.add(str(event_id))
            active.add(str(event_id))
            finished.discard(str(event_id))
            closed.discard(str(event_id))
            finished_status.pop(str(event_id), None)
            blocked.discard(str(event_id))
            deferred.discard(str(event_id))
            under_capacity.discard(str(event_id))
            blocked_excuses.discard(str(event_id))
            deferred_excuses.discard(str(event_id))
            under_capacity_excuses.discard(str(event_id))
        elif name == "finish":
            status = event.get("status")
            if status not in TERMINAL_STATUSES:
                raise SystemExit(f"scheduler events[{index}].status must be terminal")
            if event_id not in active:
                raise SystemExit(f"scheduler events[{index}] finishes inactive id {event_id}")
            finished.add(str(event_id))
            finished_status[str(event_id)] = str(status)
        elif name == "close":
            if event_id not in active:
                raise SystemExit(f"scheduler events[{index}] closes inactive id {event_id}")
            if event_id not in finished:
                raise SystemExit(f"scheduler events[{index}] closes unfinished id {event_id}")
            active.discard(str(event_id))
            closed.add(str(event_id))
            if finished_status.get(str(event_id)) != "pass":
                blocked_excuses.discard(str(event_id))
                deferred_excuses.discard(str(event_id))
                under_capacity_excuses.discard(str(event_id))
        elif name == "defer":
            deferred.add(str(event_id))
            deferred_excuses.add(str(event_id))
        elif name == "blocked":
            blocked.add(str(event_id))
            blocked_excuses.add(str(event_id))
        elif name == "under_capacity":
            values = event.get("eligible_ids")
            if isinstance(values, list):
                valid_values = {str(value) for value in values if isinstance(value, str) and value in known}
                under_capacity.update(valid_values)
                under_capacity_excuses.update(valid_values)
        elif name == "refill":
            pass
        else:
            raise SystemExit(f"scheduler events[{index}].event is unsupported: {name!r}")

    def is_repair_relaunch(item_id: str) -> bool:
        return allow_relaunch and item_id in launched and item_id in closed and finished_status.get(item_id) != "pass"

    def eligible_ids() -> list[str]:
        eligible = []
        for item_id in item_ids:
            if item_id in active:
                continue
            if item_id in launched:
                if item_id not in closed or finished_status.get(item_id) == "pass" or not allow_relaunch:
                    continue
            deps = dependencies.get(item_id, [])
            if all(dep in closed and finished_status.get(dep) == "pass" for dep in deps):
                eligible.append(item_id)
        return eligible

    eligible = eligible_ids()
    excused = blocked_excuses | deferred_excuses | under_capacity_excuses
    unexcused = [
        item_id
        for item_id in eligible
        if item_id not in excused
    ]
    launchable = [item_id for item_id in unexcused if item_id not in launched or is_repair_relaunch(item_id)]
    return {
        "active": [item_id for item_id in item_ids if item_id in active],
        "ready": [item_id for item_id in item_ids if item_id in ready],
        "launched": [item_id for item_id in item_ids if item_id in launched],
        "finished": [item_id for item_id in item_ids if item_id in finished],
        "closed": [item_id for item_id in item_ids if item_id in closed],
        "blocked": [item_id for item_id in item_ids if item_id in blocked],
        "deferred": [item_id for item_id in item_ids if item_id in deferred or item_id in under_capacity],
        "finished_status": finished_status,
        "eligible": eligible,
        "unexcused": unexcused,
        "launchable": launchable,
        "remaining_capacity": max(0, capacity - len(active)),
    }


def append_event(ledger: dict, *, event: str, runtime_ref: str, timestamp_value: str | None, **fields: object) -> dict:
    seq = len(ledger["events"]) + 1
    data = {
        "seq": seq,
        "timestamp": timestamp_value or deterministic_timestamp(seq),
        "runtime_ref": runtime_ref,
        "event": event,
    }
    data.update(fields)
    ledger["events"].append(data)
    return data


def require_reason(args: argparse.Namespace, event_name: str) -> tuple[str, str]:
    if args.reason_code not in REASON_CODES:
        raise SystemExit(f"{event_name} requires --reason-code in {sorted(REASON_CODES)}")
    if not args.reason:
        raise SystemExit(f"{event_name} requires --reason")
    return args.reason_code, args.reason


def validate_ids(ids: list[str], known: set[str], field: str) -> list[str]:
    result = []
    for value in ids:
        item_id = require_safe_id(value, field)
        if item_id not in known:
            raise SystemExit(f"{field} references unknown scheduler id: {item_id}")
        result.append(item_id)
    return result


def apply_actions(args: argparse.Namespace, ledger: dict, spec: dict, timestamp_value: str) -> tuple[list[dict], dict]:
    item_ids = list(spec["item_ids"])
    dependencies = dict(spec["dependencies"])
    capacity = int(spec["capacity"])
    allow_relaunch = spec.get("kind") == "branch-worker-pool"
    known = set(item_ids)
    appended: list[dict] = []

    if args.record_ready:
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        already_ready = set(state["ready"])
        for item_id in state["eligible"]:
            if item_id not in already_ready:
                appended.append(append_event(ledger, event="ready", runtime_ref=args.runtime_ref, timestamp_value=timestamp_value, id=item_id))

    for item_id in validate_ids(args.launch, known, "--launch"):
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        if item_id not in state["launchable"]:
            raise SystemExit(f"--launch {item_id} is not currently eligible")
        if state["remaining_capacity"] <= 0:
            raise SystemExit(f"--launch {item_id} would exceed scheduler capacity")
        appended.append(append_event(ledger, event="launch", runtime_ref=args.runtime_ref, timestamp_value=timestamp_value, id=item_id))

    if args.finish:
        if not args.status:
            raise SystemExit("--finish requires --status")
        for item_id in validate_ids(args.finish, known, "--finish"):
            state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
            if item_id not in state["active"]:
                raise SystemExit(f"--finish {item_id} is not active")
            appended.append(append_event(ledger, event="finish", runtime_ref=args.runtime_ref, timestamp_value=timestamp_value, id=item_id, status=args.status))
    elif args.status:
        raise SystemExit("--status is allowed only with --finish")

    for item_id in validate_ids(args.close, known, "--close"):
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        if item_id not in state["active"]:
            raise SystemExit(f"--close {item_id} is not active")
        if item_id not in state["finished"]:
            raise SystemExit(f"--close {item_id} has not finished")
        appended.append(append_event(ledger, event="close", runtime_ref=args.runtime_ref, timestamp_value=timestamp_value, id=item_id))
        after_close = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        if after_close["remaining_capacity"] > 0 and after_close["unexcused"]:
            appended.append(
                append_event(
                    ledger,
                    event="refill",
                    runtime_ref=args.runtime_ref,
                    timestamp_value=timestamp_value,
                    eligible_ids=after_close["unexcused"],
                )
            )

    for item_id in validate_ids(args.defer, known, "--defer"):
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        if item_id not in state["eligible"]:
            raise SystemExit(f"--defer {item_id} is not currently eligible")
        reason_code, reason = require_reason(args, "defer")
        appended.append(
            append_event(
                ledger,
                event="defer",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                id=item_id,
                reason_code=reason_code,
                reason=reason,
            )
        )

    for item_id in validate_ids(args.blocked, known, "--blocked"):
        reason_code, reason = require_reason(args, "blocked")
        appended.append(
            append_event(
                ledger,
                event="blocked",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                id=item_id,
                reason_code=reason_code,
                reason=reason,
            )
        )

    if args.under_capacity:
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        if not state["unexcused"]:
            raise SystemExit("--under-capacity requires at least one unexcused eligible id")
        reason_code, reason = require_reason(args, "under_capacity")
        appended.append(
            append_event(
                ledger,
                event="under_capacity",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                eligible_ids=state["unexcused"],
                reason_code=reason_code,
                reason=reason,
            )
        )

    return appended, replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)


def validate_final(ledger_path: Path, ledger: dict, spec: dict, manifest_path: Path) -> list[str]:
    defects: list[str] = []
    STATUS_VALIDATION.validate_scheduler_ledger(
        defects,
        ledger,
        "$.scheduler",
        scheduler_kind=spec["kind"],
        expected_path=spec["path"],
        expected_ids=spec["item_ids"],
        dependencies=spec["dependencies"],
        capacity=spec["capacity"],
        manifest_path=manifest_path,
        require_all_launched=False,
    )
    if defects:
        return [f"{ledger_path}: {defect}" for defect in defects]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--scope", choices=["main", "worker"], required=True)
    parser.add_argument("--branch-id")
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument("--timestamp", help="Defaults to deterministic synthetic ISO timestamps derived from event sequence numbers.")
    parser.add_argument("--init", action="store_true", help="Create the manifest-derived scheduler ledger when missing.")
    parser.add_argument("--record-ready", action="store_true", help="Append ready events for newly eligible scheduler ids.")
    parser.add_argument("--launch", action="append", default=[], help="Append a launch event for an eligible id.")
    parser.add_argument("--finish", action="append", default=[], help="Append a finish event for an active id.")
    parser.add_argument("--status", choices=sorted(TERMINAL_STATUSES))
    parser.add_argument("--close", action="append", default=[], help="Append close event(s); emits refill when capacity frees with eligible work.")
    parser.add_argument("--defer", action="append", default=[], help="Append a structured defer event for an eligible id.")
    parser.add_argument("--blocked", action="append", default=[], help="Append a structured blocked event for an id.")
    parser.add_argument("--under-capacity", action="store_true", help="Record under-capacity evidence for current unexcused eligible ids.")
    parser.add_argument("--reason-code", choices=sorted(REASON_CODES))
    parser.add_argument("--reason")
    parser.add_argument("--list-ready", action="store_true", help="Print launchable ids after applying requested events.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--validate-final", action="store_true", help="Run strict closed-ledger validation after writing.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    manifest = read_json(manifest_path)
    spec = scheduler_spec(manifest_path, manifest, args.scope, args.branch_id)
    ledger_path = (manifest_path.parent / spec["path"]).resolve()
    ledger = load_or_create_ledger(ledger_path, spec, manifest_path, create=args.init)
    appended, state = apply_actions(args, ledger, spec, args.timestamp)

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer")
    ready = state["launchable"][: args.limit] if args.limit is not None else state["launchable"]

    if appended or args.init:
        write_json_atomic(ledger_path, ledger)

    defects = validate_final(ledger_path, ledger, spec, manifest_path) if args.validate_final else []
    result = {
        "status": "pass" if not defects else "failed",
        "ledger_path": ledger_path.as_posix(),
        "appended_events": appended,
        "ready": ready,
        "state": state,
        "defects": defects,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.list_ready:
        for item_id in ready:
            print(item_id)
    else:
        print(ledger_path)
        for event in appended:
            print(f"{event['seq']}: {event['event']}")
        for defect in defects:
            print(defect)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
