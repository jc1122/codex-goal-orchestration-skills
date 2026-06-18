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
from typing import NamedTuple


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
SCHEDULER_EVENT_SCHEMA_VERSION = 2


def scheduler_max_ready_width(item_ids: list[str], dependencies: dict[str, list[str]]) -> int:
    remaining: dict[str, list[str]] = {item_id: list(dependencies.get(item_id, [])) for item_id in item_ids}
    levels: dict[str, int] = {}
    while remaining:
        progressed = False
        for item_id, deps in list(remaining.items()):
            if any(dep in remaining for dep in deps):
                continue
            dep_levels = [levels.get(dep, 0) for dep in deps if dep in levels]
            levels[item_id] = 1 + (max(dep_levels) if dep_levels else 0)
            remaining.pop(item_id)
            progressed = True
        if not progressed:
            for item_id in list(remaining):
                levels[item_id] = 1
                remaining.pop(item_id)
    widths: dict[int, int] = {}
    for level in levels.values():
        widths[level] = widths.get(level, 0) + 1
    return max(widths.values(), default=0)


def scheduler_ready_width_reason(
    item_count: int,
    ready_width: int,
    capacity: int,
    *,
    scope: str,
    identifier: str | None = None,
) -> str | None:
    usable_capacity = min(max(capacity, 1), item_count) if item_count else 0
    if usable_capacity <= 1 or ready_width >= usable_capacity:
        return None
    scope_name = f"{scope} {identifier}" if identifier else scope
    return (
        f"{scope_name} exposes {ready_width} ready item(s) against usable capacity {usable_capacity};"
        " this limits parallel fill under current dependencies"
    )


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
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
    return (
        (datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=seq)).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def manifest_epoch(manifest: dict) -> str:
    value = manifest.get("manifest_epoch")
    if value is None:
        value = manifest.get("epoch", "current")
    if not isinstance(value, str) or not value.strip():
        return "current"
    return value


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
    known = [
        str(branch.get("id")) for branch in branches if isinstance(branch, dict) and isinstance(branch.get("id"), str)
    ]
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
        raise SystemExit(
            f"branch {branch_id} must not declare more than {CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH} work items"
        )
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
        if (
            not isinstance(max_active, int)
            or isinstance(max_active, bool)
            or max_active < 1
            or max_active > CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
        ):
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
    if (
        not isinstance(max_active, int)
        or isinstance(max_active, bool)
        or max_active < 1
        or max_active > CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
    ):
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


class _ReplayLedgerState(NamedTuple):
    """Collections produced by reducing a scheduler ledger's event stream.

    Mirrors the exact local variables the inlined replay loop maintained; the
    orchestrator consumes them in the identical downstream order.
    """

    active: set[str]
    ready: set[str]
    launched: set[str]
    finished: set[str]
    closed: set[str]
    blocked: set[str]
    deferred: set[str]
    under_capacity: set[str]
    blocked_excuses: set[str]
    deferred_excuses: set[str]
    under_capacity_excuses: set[str]
    finished_status: dict[str, str]
    blocked_reason_codes: dict[str, str]
    max_observed_active: int


def _reduce_ledger_events(ledger: dict, capacity: int, known: set[str]) -> _ReplayLedgerState:
    """Reduce the ledger event stream into scheduler state sets.

    Identical to the inlined replay loop: same validation order, same raises,
    same set/dict mutations event-by-event.
    """
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
    blocked_reason_codes: dict[str, str] = {}
    max_observed_active = 0
    for index, event in enumerate(ledger.get("events", [])):
        if not isinstance(event, dict):
            raise SystemExit(f"scheduler events[{index}] must be an object")
        name = event.get("event")
        event_id = event.get("id")
        if name in {"ready", "launch", "finish", "close", "defer", "blocked"} and (
            not isinstance(event_id, str) or event_id not in known
        ):
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
            max_observed_active = max(max_observed_active, len(active))
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
            reason_code = event.get("reason_code")
            if isinstance(reason_code, str):
                blocked_reason_codes[str(event_id)] = reason_code
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
    return _ReplayLedgerState(
        active=active,
        ready=ready,
        launched=launched,
        finished=finished,
        closed=closed,
        blocked=blocked,
        deferred=deferred,
        under_capacity=under_capacity,
        blocked_excuses=blocked_excuses,
        deferred_excuses=deferred_excuses,
        under_capacity_excuses=under_capacity_excuses,
        finished_status=finished_status,
        blocked_reason_codes=blocked_reason_codes,
        max_observed_active=max_observed_active,
    )


def _is_repair_relaunch(
    item_id: str,
    state: _ReplayLedgerState,
    *,
    allow_relaunch: bool,
    relaunch_reason_codes: set[str] | None,
) -> bool:
    """Repair-relaunch predicate, identical to the inlined ``is_repair_relaunch`` closure."""
    if not (
        allow_relaunch
        and item_id in state.launched
        and item_id in state.closed
        and state.finished_status.get(item_id) != "pass"
    ):
        return False
    if relaunch_reason_codes is None:
        return True
    return state.blocked_reason_codes.get(item_id) in relaunch_reason_codes


def _replay_eligible_ids(
    item_ids: list[str],
    dependencies: dict[str, list[str]],
    state: _ReplayLedgerState,
    *,
    allow_relaunch: bool,
    relaunch_reason_codes: set[str] | None,
) -> list[str]:
    """Eligible-id computation, identical to the inlined ``eligible_ids`` closure."""
    eligible = []
    for item_id in item_ids:
        if item_id in state.active:
            continue
        if item_id in state.launched and (
            item_id not in state.closed
            or state.finished_status.get(item_id) == "pass"
            or not _is_repair_relaunch(
                item_id,
                state,
                allow_relaunch=allow_relaunch,
                relaunch_reason_codes=relaunch_reason_codes,
            )
        ):
            continue
        deps = dependencies.get(item_id, [])
        if all(dep in state.closed and state.finished_status.get(dep) == "pass" for dep in deps):
            eligible.append(item_id)
    return eligible


def _assemble_replay_state(
    item_ids: list[str],
    capacity: int,
    state: _ReplayLedgerState,
    eligible: list[str],
    unexcused: list[str],
    launchable: list[str],
) -> dict:
    """Build the public replay result dict, identical to the inlined return."""
    return {
        "active": [item_id for item_id in item_ids if item_id in state.active],
        "ready": [item_id for item_id in item_ids if item_id in state.ready],
        "launched": [item_id for item_id in item_ids if item_id in state.launched],
        "finished": [item_id for item_id in item_ids if item_id in state.finished],
        "closed": [item_id for item_id in item_ids if item_id in state.closed],
        "blocked": [item_id for item_id in item_ids if item_id in state.blocked],
        "deferred": [item_id for item_id in item_ids if item_id in state.deferred or item_id in state.under_capacity],
        "finished_status": state.finished_status,
        "eligible": eligible,
        "unexcused": unexcused,
        "launchable": launchable,
        "remaining_capacity": max(0, capacity - len(state.active)),
        "max_observed_active": state.max_observed_active,
    }


def replay(
    ledger: dict,
    item_ids: list[str],
    dependencies: dict[str, list[str]],
    capacity: int,
    *,
    allow_relaunch: bool = False,
    relaunch_reason_codes: set[str] | None = None,
) -> dict:
    known = set(item_ids)
    state = _reduce_ledger_events(ledger, capacity, known)
    eligible = _replay_eligible_ids(
        item_ids,
        dependencies,
        state,
        allow_relaunch=allow_relaunch,
        relaunch_reason_codes=relaunch_reason_codes,
    )
    excused = state.blocked_excuses | state.deferred_excuses | state.under_capacity_excuses
    unexcused = [
        item_id
        for item_id in eligible
        if item_id not in excused
        or _is_repair_relaunch(
            item_id, state, allow_relaunch=allow_relaunch, relaunch_reason_codes=relaunch_reason_codes
        )
    ]
    launchable = [
        item_id
        for item_id in unexcused
        if item_id not in state.launched
        or _is_repair_relaunch(
            item_id, state, allow_relaunch=allow_relaunch, relaunch_reason_codes=relaunch_reason_codes
        )
    ]
    return _assemble_replay_state(item_ids, capacity, state, eligible, unexcused, launchable)


def append_event(
    ledger: dict,
    *,
    event: str,
    runtime_ref: str,
    timestamp_value: str | None,
    manifest_sha256: str,
    manifest_epoch_value: str,
    **fields: object,
) -> dict:
    seq = len(ledger["events"]) + 1
    data = {
        "seq": seq,
        "timestamp": timestamp_value or deterministic_timestamp(seq),
        "wall_clock_timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runtime_ref": runtime_ref,
        "event": event,
        "schema_version": SCHEDULER_EVENT_SCHEMA_VERSION,
        "manifest_sha256": manifest_sha256,
        "manifest_epoch": manifest_epoch_value,
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


def apply_actions(
    args: argparse.Namespace,
    ledger: dict,
    spec: dict,
    timestamp_value: str,
    manifest_sha: str,
    manifest_epoch_value: str,
) -> tuple[list[dict], dict]:
    item_ids = list(spec["item_ids"])
    dependencies = dict(spec["dependencies"])
    capacity = int(spec["capacity"])
    # Allow explicit relaunches in normal operation; artifact closeout handles this separately.
    if spec.get("kind") == "branch-worker-pool":
        allow_relaunch = True
        relaunch_reason_codes = None
    elif spec.get("kind") == "main-branch-pool":
        allow_relaunch = True
        relaunch_reason_codes = {"stale_active", "native_agent_unreachable", "timeout", "launcher_failed"}
    else:
        allow_relaunch = False
        relaunch_reason_codes = None
    known = set(item_ids)
    appended: list[dict] = []

    if args.record_ready:
        state = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        already_ready = set(state["ready"])
        for item_id in state["eligible"]:
            if item_id not in already_ready:
                appended.append(
                    append_event(
                        ledger,
                        event="ready",
                        runtime_ref=args.runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                    )
                )

    for item_id in validate_ids(args.launch, known, "--launch"):
        state = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        if item_id not in state["launchable"]:
            raise SystemExit(f"--launch {item_id} is not currently eligible")
        if state["remaining_capacity"] <= 0:
            raise SystemExit(f"--launch {item_id} would exceed scheduler capacity")
        appended.append(
            append_event(
                ledger,
                event="launch",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                manifest_sha256=manifest_sha,
                manifest_epoch_value=manifest_epoch_value,
                id=item_id,
            )
        )

    if args.finish:
        if not args.status:
            raise SystemExit("--finish requires --status")
        for item_id in validate_ids(args.finish, known, "--finish"):
            state = replay(
                ledger,
                item_ids,
                dependencies,
                capacity,
                allow_relaunch=allow_relaunch,
                relaunch_reason_codes=relaunch_reason_codes,
            )
            if item_id not in state["active"]:
                raise SystemExit(f"--finish {item_id} is not active")
            appended.append(
                append_event(
                    ledger,
                    event="finish",
                    runtime_ref=args.runtime_ref,
                    timestamp_value=timestamp_value,
                    manifest_sha256=manifest_sha,
                    manifest_epoch_value=manifest_epoch_value,
                    id=item_id,
                    status=args.status,
                )
            )
    elif args.status:
        raise SystemExit("--status is allowed only with --finish")

    for item_id in validate_ids(args.close, known, "--close"):
        state = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        if item_id not in state["active"]:
            raise SystemExit(f"--close {item_id} is not active")
        if item_id not in state["finished"]:
            raise SystemExit(f"--close {item_id} has not finished")
        appended.append(
            append_event(
                ledger,
                event="close",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                manifest_sha256=manifest_sha,
                manifest_epoch_value=manifest_epoch_value,
                id=item_id,
            )
        )
        after_close = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        if after_close["remaining_capacity"] > 0 and after_close["unexcused"]:
            appended.append(
                append_event(
                    ledger,
                    event="refill",
                    runtime_ref=args.runtime_ref,
                    timestamp_value=timestamp_value,
                    manifest_sha256=manifest_sha,
                    manifest_epoch_value=manifest_epoch_value,
                    eligible_ids=after_close["unexcused"],
                )
            )

    for item_id in validate_ids(args.defer, known, "--defer"):
        state = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        if item_id not in state["eligible"]:
            raise SystemExit(f"--defer {item_id} is not currently eligible")
        reason_code, reason = require_reason(args, "defer")
        appended.append(
            append_event(
                ledger,
                event="defer",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                manifest_sha256=manifest_sha,
                manifest_epoch_value=manifest_epoch_value,
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
                manifest_sha256=manifest_sha,
                manifest_epoch_value=manifest_epoch_value,
                id=item_id,
                reason_code=reason_code,
                reason=reason,
            )
        )

    if args.under_capacity:
        state = replay(
            ledger,
            item_ids,
            dependencies,
            capacity,
            allow_relaunch=allow_relaunch,
            relaunch_reason_codes=relaunch_reason_codes,
        )
        if not state["unexcused"]:
            raise SystemExit("--under-capacity requires at least one unexcused eligible id")
        reason_code, reason = require_reason(args, "under_capacity")
        appended.append(
            append_event(
                ledger,
                event="under_capacity",
                runtime_ref=args.runtime_ref,
                timestamp_value=timestamp_value,
                manifest_sha256=manifest_sha,
                manifest_epoch_value=manifest_epoch_value,
                eligible_ids=state["unexcused"],
                reason_code=reason_code,
                reason=reason,
            )
        )

    return appended, replay(
        ledger,
        item_ids,
        dependencies,
        capacity,
        allow_relaunch=allow_relaunch,
        relaunch_reason_codes=relaunch_reason_codes,
    )


def artifact_statuses(manifest_path: Path, manifest: dict, scope: str, branch_id: str | None) -> dict[str, str]:
    bundle_dir = manifest_path.parent
    statuses: dict[str, str] = {}
    if scope == "main":
        branches = manifest.get("branches")
        if not isinstance(branches, list):
            raise SystemExit("manifest branches must be an array")
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            item_id = branch.get("id")
            rel_path = branch.get("status_path")
            if not isinstance(item_id, str) or not isinstance(rel_path, str):
                continue
            status_path = bundle_dir / rel_path
            if not status_path.exists():
                continue
            status = read_json(status_path).get("status")
            if status not in TERMINAL_STATUSES:
                raise SystemExit(f"status artifact {status_path} must contain a terminal status")
            statuses[item_id] = str(status)
        return statuses

    if branch_id is None:
        raise SystemExit("--branch-id is required for worker artifact closeout")
    branch = manifest_branch(manifest, branch_id)
    work_items = branch.get("work_items")
    if not isinstance(work_items, list):
        raise SystemExit(f"branch {branch_id} work_items must be an array")
    for item in work_items:
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            continue
        worker_type = item.get("worker_type", "worker")
        if worker_type == CONTRACT.RESEARCH_WORKER_TYPE:
            status_path = bundle_dir / "research" / packet_id / "research.json"
        else:
            status_path = bundle_dir / "workers" / packet_id / "status.json"
        if not status_path.exists():
            continue
        status = read_json(status_path).get("status")
        if status not in TERMINAL_STATUSES:
            raise SystemExit(f"status artifact {status_path} must contain a terminal status")
        if worker_type != CONTRACT.RESEARCH_WORKER_TYPE and status == "pass":
            summary_path = bundle_dir / "workers" / packet_id / "packet.summary.json"
            if summary_path.exists():
                summary = read_json(summary_path)
                summary_status = summary.get("output_status")
                if summary_status in TERMINAL_STATUSES and summary_status != "pass":
                    status = "blocked"
        statuses[packet_id] = str(status)
    return statuses


def close_from_artifacts(
    ledger: dict,
    spec: dict,
    *,
    runtime_ref: str,
    timestamp_value: str | None,
    manifest_sha: str,
    manifest_epoch_value: str,
    terminal_statuses: dict[str, str],
) -> tuple[list[dict], dict]:
    item_ids = list(spec["item_ids"])
    dependencies = dict(spec["dependencies"])
    capacity = int(spec["capacity"])
    # Artifact closeout should only replay terminal evidence; avoid automatic relaunch.
    allow_relaunch = False
    worker_scope = spec.get("kind") == "branch-worker-pool"
    appended: list[dict] = []
    max_steps = max(10, len(item_ids) * 8)

    for _ in range(max_steps):
        progressed = False
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        already_ready = set(state["ready"])
        for item_id in state["eligible"]:
            if item_id not in already_ready:
                appended.append(
                    append_event(
                        ledger,
                        event="ready",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                    )
                )
                progressed = True
        if progressed:
            continue

        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        blocked_candidates = [
            item_id
            for item_id in state["closed"]
            if worker_scope
            and terminal_statuses.get(item_id) in TERMINAL_STATUSES
            and terminal_statuses.get(item_id) != "pass"
            and item_id not in state["blocked"]
        ]
        if blocked_candidates:
            for item_id in blocked_candidates:
                status = terminal_statuses[item_id]
                reason_code = "process_exited_blocked" if status == "blocked" else "artifact_invalid"
                appended.append(
                    append_event(
                        ledger,
                        event="blocked",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                        reason_code=reason_code,
                        reason=f"status artifact reported {status}",
                    )
                )
            state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
            if state["remaining_capacity"] > 0 and state["unexcused"]:
                appended.append(
                    append_event(
                        ledger,
                        event="refill",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        eligible_ids=state["unexcused"],
                    )
                )
            continue

        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        finished = set(state["finished"])
        for item_id in state["active"]:
            if item_id not in finished:
                continue
            terminal_status = terminal_statuses.get(item_id, "pass")
            appended.append(
                append_event(
                    ledger,
                    event="close",
                    runtime_ref=runtime_ref,
                    timestamp_value=timestamp_value,
                    manifest_sha256=manifest_sha,
                    manifest_epoch_value=manifest_epoch_value,
                    id=item_id,
                )
            )
            progressed = True
            after_close = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
            if (
                worker_scope
                and terminal_status in TERMINAL_STATUSES
                and terminal_status != "pass"
                and item_id not in after_close["blocked"]
            ):
                appended.append(
                    append_event(
                        ledger,
                        event="blocked",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                        reason_code="process_exited_blocked" if terminal_status == "blocked" else "artifact_invalid",
                        reason=f"status artifact reported {terminal_status}",
                    )
                )
                after_close = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
            if after_close["remaining_capacity"] > 0 and after_close["unexcused"]:
                appended.append(
                    append_event(
                        ledger,
                        event="refill",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        eligible_ids=after_close["unexcused"],
                    )
                )
            break
        if progressed:
            continue

        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        finished = set(state["finished"])
        for item_id in state["active"]:
            status = terminal_statuses.get(item_id)
            if status and item_id not in finished:
                appended.append(
                    append_event(
                        ledger,
                        event="finish",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                        status=status,
                    )
                )
                progressed = True
                break
        if progressed:
            continue

        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        closed = set(state["closed"])
        launchable = [
            item_id for item_id in state["launchable"] if item_id in terminal_statuses and item_id not in closed
        ]
        while launchable and state["remaining_capacity"] > 0:
            item_id = launchable.pop(0)
            appended.append(
                append_event(
                    ledger,
                    event="launch",
                    runtime_ref=runtime_ref,
                    timestamp_value=timestamp_value,
                    manifest_sha256=manifest_sha,
                    manifest_epoch_value=manifest_epoch_value,
                    id=item_id,
                )
            )
            progressed = True
            state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
            closed = set(state["closed"])
            launchable = [
                candidate
                for candidate in state["launchable"]
                if candidate in terminal_statuses and candidate not in closed
            ]
        if progressed:
            continue

        # Dependency-stuck closeout: an item that can never launch because an upstream dependency
        # closed non-pass (or is itself blocked) must carry an explicit dependency_failed blocked
        # event. status_validation._validate_ledger_final_state requires exactly this for every
        # unlaunched item with a failed dependency; without it, `--close-from-artifacts
        # --validate-final` reports a normal "upstream failed -> downstream blocked" run as failed
        # with un-self-repairable defects (and reconcile's dependency-failed recovery path stays
        # dead). Each item is blocked at most once, so the loop still converges.
        state = replay(ledger, item_ids, dependencies, capacity, allow_relaunch=allow_relaunch)
        closed = set(state["closed"])
        blocked = set(state["blocked"])
        active = set(state["active"])
        for item_id in item_ids:
            if item_id in closed or item_id in blocked or item_id in active:
                continue
            stuck_deps = [
                dep
                for dep in dependencies.get(item_id, [])
                if (dep in closed and terminal_statuses.get(dep) in {"partial", "blocked", "failed"}) or dep in blocked
            ]
            if stuck_deps:
                appended.append(
                    append_event(
                        ledger,
                        event="blocked",
                        runtime_ref=runtime_ref,
                        timestamp_value=timestamp_value,
                        manifest_sha256=manifest_sha,
                        manifest_epoch_value=manifest_epoch_value,
                        id=item_id,
                        reason_code="dependency_failed",
                        reason=f"dependency did not pass: {', '.join(stuck_deps)}",
                    )
                )
                progressed = True
        if progressed:
            continue

        return appended, state

    raise SystemExit("--close-from-artifacts did not converge; inspect scheduler ledger")


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
    parser.add_argument(
        "--timestamp", help="Defaults to deterministic synthetic ISO timestamps derived from event sequence numbers."
    )
    parser.add_argument(
        "--init", action="store_true", help="Create the manifest-derived scheduler ledger when missing."
    )
    parser.add_argument(
        "--record-ready", action="store_true", help="Append ready events for newly eligible scheduler ids."
    )
    parser.add_argument("--launch", action="append", default=[], help="Append a launch event for an eligible id.")
    parser.add_argument("--finish", action="append", default=[], help="Append a finish event for an active id.")
    parser.add_argument("--status", choices=sorted(TERMINAL_STATUSES))
    parser.add_argument(
        "--close",
        action="append",
        default=[],
        help="Append close event(s); emits refill when capacity frees with eligible work.",
    )
    parser.add_argument(
        "--defer", action="append", default=[], help="Append a structured defer event for an eligible id."
    )
    parser.add_argument("--blocked", action="append", default=[], help="Append a structured blocked event for an id.")
    parser.add_argument(
        "--under-capacity",
        action="store_true",
        help="Record under-capacity evidence for current unexcused eligible ids.",
    )
    parser.add_argument(
        "--close-from-artifacts",
        action="store_true",
        help="Append ready/launch/finish/close events for terminal manifest-owned status artifacts.",
    )
    parser.add_argument("--reason-code", choices=sorted(REASON_CODES))
    parser.add_argument("--reason")
    parser.add_argument(
        "--list-ready", action="store_true", help="Print launchable ids after applying requested events."
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--validate-final", action="store_true", help="Run strict closed-ledger validation after writing."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    manifest = read_json(manifest_path)
    manifest_sha = sha256_file(manifest_path)
    manifest_epoch_value = manifest_epoch(manifest)
    spec = scheduler_spec(manifest_path, manifest, args.scope, args.branch_id)
    ledger_path = (manifest_path.parent / spec["path"]).resolve()
    ledger = load_or_create_ledger(ledger_path, spec, manifest_path, create=args.init)
    appended, state = apply_actions(
        args,
        ledger,
        spec,
        args.timestamp,
        manifest_sha=manifest_sha,
        manifest_epoch_value=manifest_epoch_value,
    )
    if args.close_from_artifacts:
        artifact_appended, state = close_from_artifacts(
            ledger,
            spec,
            runtime_ref=args.runtime_ref,
            timestamp_value=args.timestamp,
            manifest_sha=manifest_sha,
            manifest_epoch_value=manifest_epoch_value,
            terminal_statuses=artifact_statuses(manifest_path, manifest, args.scope, args.branch_id),
        )
        appended.extend(artifact_appended)

    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer")
    ready = state["launchable"][: args.limit] if args.limit is not None else state["launchable"]

    ready_width = scheduler_max_ready_width(spec["item_ids"], spec["dependencies"])
    scope_label = "branch" if args.scope == "main" else f"worker {args.branch_id}" if args.branch_id else "worker"
    underutilization_reason = scheduler_ready_width_reason(
        len(spec["item_ids"]),
        ready_width,
        spec["capacity"],
        scope=scope_label,
    )
    validation_defects = validate_final(ledger_path, ledger, spec, manifest_path)
    validation_status = "pass" if not validation_defects else "failed"
    ledger["status"] = validation_status
    ledger["validation_status"] = validation_status
    ledger["max_observed_active"] = state["max_observed_active"]
    ledger["ready_width"] = ready_width
    ledger["underutilization_reason"] = underutilization_reason

    if args.init or appended or args.validate_final:
        write_json_atomic(ledger_path, ledger)

    defects = validation_defects if args.validate_final else []
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
