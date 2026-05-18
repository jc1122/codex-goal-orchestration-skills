#!/usr/bin/env python3
"""Validate a goal branch-orchestrator status artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def _load_status_validation():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "status_validation.py"
    if not path.exists():
        raise SystemExit(f"missing shared status validation helpers: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_status_validation", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared status validation helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STATUS_VALIDATION = _load_status_validation()
STATUSES = {"pass", "partial", "blocked", "failed"}
REVIEW_STATUSES = {"mergeable", "mergeable_after_fixes", "blocked", "reject", "missing"}
BRANCH_LITE_PURPOSES = {"branch-packet-planning", "context-pack", "worker-summary", "blocked-triage"}
MAX_WORKER_PACKETS_PER_BRANCH = 4
DEFAULT_WORKER_LADDER = (
    "gemini-pro",
    "gemini-flash",
    "codex-spark",
    "copilot-gpt-5.4",
    "codex-mini",
)
ALLOWED_WORKER_ROUTES = set(DEFAULT_WORKER_LADDER)
SAFE_REVIEW_PACKET_RE = STATUS_VALIDATION.SAFE_PACKET_RE
is_strict_int = STATUS_VALIDATION.is_strict_int
resolve_absolute_path = STATUS_VALIDATION.resolve_absolute_path
load_json = STATUS_VALIDATION.load_json
load_json_artifact = STATUS_VALIDATION.load_json_artifact
defect = STATUS_VALIDATION.defect
require_object = STATUS_VALIDATION.require_object
require_string = STATUS_VALIDATION.require_string
require_string_list = STATUS_VALIDATION.require_string_list
is_absolute_path = STATUS_VALIDATION.is_absolute_path
validate_base_range_diff_check = STATUS_VALIDATION.validate_base_range_diff_check


def is_repo_relative_path(value: str) -> bool:
    return STATUS_VALIDATION.is_repo_relative_path(value, reject_porcelain=True)


def validate_path_list(defects: list[str], value: object, path: str) -> None:
    for index, item in enumerate(require_string_list(defects, value, path)):
        if not is_repo_relative_path(item):
            defect(defects, f"{path}[{index}]", "must be a repo-relative path without git porcelain status")


def validate_lite_advice_entries(
    defects: list[str],
    value: object,
    path: str,
    *,
    manifest_path: Path,
    branch_id: str | None,
) -> None:
    branch_prefix = f"{branch_id}-L" if isinstance(branch_id, str) and branch_id.strip() else ""
    STATUS_VALIDATION.validate_runtime_lite_advice_entries(
        defects,
        value,
        path,
        manifest_path=manifest_path,
        script_dir=Path(__file__).resolve().parent,
        validator_module_name="goal_branch_validate_lite_advice",
        allowed_purposes=BRANCH_LITE_PURPOSES,
        skill_name="goal-branch-orchestrator",
        scope_label="branch",
        malformed_packet_prefix=branch_prefix,
        required_packet_prefix=branch_prefix,
        reject_source_porcelain=True,
    )


def validate_command_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> None:
    require_string_list(defects, value, path, min_items=min_items)


def validate_worker_ladder(defects: list[str], value: object, path: str) -> list[str]:
    if not isinstance(value, list) or not value:
        defect(defects, path, "must be a non-empty array")
        return []
    aliases = []
    positions = []
    seen = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            defect(defects, item_path, "must be a non-empty string")
            continue
        if item not in ALLOWED_WORKER_ROUTES:
            defect(defects, item_path, f"must be one of {sorted(ALLOWED_WORKER_ROUTES)}")
            continue
        if item in seen:
            defect(defects, item_path, "must not repeat a route alias")
            continue
        seen.add(item)
        aliases.append(item)
        positions.append(DEFAULT_WORKER_LADDER.index(item))
    if positions != sorted(positions):
        defect(defects, path, "must preserve standard ladder order")
    return aliases


def validate_worker_route_artifact(
    defects: list[str],
    route_value: object,
    path: str,
    *,
    worker: dict,
) -> None:
    route = require_object(defects, route_value, path)
    for key in ["packet_id", "role", "selected_ladder", "selection_reason"]:
        if key not in route:
            defect(defects, path, f"missing key: {key}")
    if route.get("packet_id") != worker.get("packet_id"):
        defect(defects, f"{path}.packet_id", "must match worker packet_id")
    if route.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    validate_worker_ladder(defects, route.get("selected_ladder"), f"{path}.selected_ladder")
    require_string(defects, route.get("selection_reason"), f"{path}.selection_reason")
    if route.get("selected_ladder") != worker.get("selected_ladder"):
        defect(defects, f"{path}.selected_ladder", "must match worker selected_ladder")
    if route.get("selection_reason") != worker.get("selection_reason"):
        defect(defects, f"{path}.selection_reason", "must match worker selection_reason")


def validate_worker_status(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "status",
        "status_path",
        "worktree",
        "selected_ladder",
        "selection_reason",
        "changed_files",
        "commands_run",
        "tests",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if status_path and not is_absolute_path(status_path):
        defect(defects, f"{path}.status_path", "must be an absolute path without traversal")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    validate_worker_ladder(defects, data.get("selected_ladder"), f"{path}.selected_ladder")
    require_string(defects, data.get("selection_reason"), f"{path}.selection_reason")
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")


def validate_worker_artifact(defects: list[str], value: object, path: str) -> dict:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "role",
        "status",
        "branch",
        "worktree",
        "selected_ladder",
        "selection_reason",
        "changed_files",
        "commands_run",
        "tests",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    require_string(defects, data.get("branch"), f"{path}.branch")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    validate_worker_ladder(defects, data.get("selected_ladder"), f"{path}.selected_ladder")
    require_string(defects, data.get("selection_reason"), f"{path}.selection_reason")
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")
    return data


def validate_worker_artifacts(
    defects: list[str],
    worker_statuses: object,
    branch_status: object,
    *,
    branch: object,
    manifest_path: Path,
) -> None:
    if not isinstance(worker_statuses, list):
        return
    require_existing = branch_status in {"pass", "partial"}
    compared_keys = [
        "packet_id",
        "status",
        "worktree",
        "selected_ladder",
        "selection_reason",
        "changed_files",
        "commands_run",
        "tests",
        "blockers",
        "handoff",
    ]
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        item_path = f"$.worker_statuses[{index}]"
        status_path_value = item.get("status_path")
        if not isinstance(status_path_value, str) or not status_path_value.strip() or not is_absolute_path(status_path_value):
            continue
        status_artifact = Path(status_path_value).resolve()
        packet_id = item.get("packet_id")
        if isinstance(packet_id, str) and packet_id.strip():
            expected_status_artifact = (manifest_path.parent / "workers" / packet_id / "status.json").resolve()
            if status_artifact != expected_status_artifact:
                defect(
                    defects,
                    f"{item_path}.status_path",
                    f"must be manifest-owned worker status path: {expected_status_artifact}",
                )
        if not status_artifact.exists():
            if require_existing:
                defect(defects, f"{item_path}.status_path", f"artifact does not exist: {status_artifact}")
            continue
        artifact = validate_worker_artifact(
            defects,
            load_json_artifact(defects, status_artifact, f"{item_path}.status_path"),
            f"{item_path}.status_path",
        )
        if isinstance(branch, str) and branch.strip() and artifact.get("branch") != branch:
            defect(defects, f"{item_path}.status_path.branch", "must match branch status branch")
        for key in compared_keys:
            if artifact.get(key) != item.get(key):
                defect(defects, f"{item_path}.{key}", "must match worker status artifact")
        route_path = status_artifact.parent / "route.json"
        if not route_path.exists():
            defect(defects, f"{item_path}.status_path", f"route artifact does not exist: {route_path}")
        else:
            validate_worker_route_artifact(
                defects,
                load_json_artifact(defects, route_path, f"{item_path}.route_path"),
                f"{item_path}.route_path",
                worker=artifact,
            )


def manifest_branch_entry(defects: list[str], manifest: object, branch_id: str) -> dict:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    if not isinstance(branches, list) or not branches:
        defect(defects, "manifest.branches", "must be a non-empty array")
        return {}
    matches = []
    for index, item in enumerate(branches):
        if not isinstance(item, dict):
            defect(defects, f"manifest.branches[{index}]", "must be an object")
            continue
        item_id = item.get("id")
        if item_id == branch_id:
            matches.append((index, item))
    if not matches:
        defect(defects, "$.branch_id", f"must be declared in manifest: {branch_id!r}")
        return {}
    if len(matches) > 1:
        defect(defects, "manifest.branches", f"must not duplicate branch id {branch_id!r}")
    return matches[0][1]


def expected_worker_packet_ids(defects: list[str], branch_entry: dict, branch_id: str) -> list[str]:
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "manifest.branch.work_items", "must contain 1 to 4 work item objects")
        return []
    packet_ids = []
    work_item_order = {}
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            defect(defects, f"manifest.branch.work_items[{index}]", "must be an object")
            continue
        item_id = require_string(defects, item.get("id"), f"manifest.branch.work_items[{index}].id")
        packet_id = require_string(defects, item.get("packet_id"), f"manifest.branch.work_items[{index}].packet_id")
        if item_id and packet_id:
            expected_packet_id = f"{branch_id}-{item_id}"
            if packet_id != expected_packet_id:
                defect(defects, f"manifest.branch.work_items[{index}].packet_id", f"must be {expected_packet_id!r}")
            packet_ids.append(packet_id)
            work_item_order[item_id] = index
        deps = require_string_list(defects, item.get("depends_on", []), f"manifest.branch.work_items[{index}].depends_on")
        for dep in deps:
            if dep not in work_item_order or work_item_order[dep] >= index:
                defect(defects, f"manifest.branch.work_items[{index}].depends_on", f"must reference only prior work item ids; invalid dependency: {dep}")
    if len(packet_ids) != len(set(packet_ids)):
        defect(defects, "manifest.branch.work_items", "packet_id values must be unique")
    return packet_ids


def validate_manifest_branch_contract(
    defects: list[str],
    root: dict,
    manifest: object,
    *,
    manifest_path: Path,
    status_path: Path,
    require_all_workers: bool,
) -> dict:
    branch_id = root.get("branch_id")
    if not isinstance(branch_id, str) or not branch_id.strip():
        return {}
    branch_entry = manifest_branch_entry(defects, manifest, branch_id)
    if not branch_entry:
        return {}

    manifest_branch_name = branch_entry.get("branch_name")
    if isinstance(manifest_branch_name, str) and manifest_branch_name.strip() and root.get("branch") != manifest_branch_name:
        defect(defects, "$.branch", f"must match manifest branch_name {manifest_branch_name!r}")

    manifest_status_path = branch_entry.get("status_path")
    if isinstance(manifest_status_path, str) and manifest_status_path.strip():
        expected_status_path = (manifest_path.parent / manifest_status_path).resolve().as_posix()
        if status_path.as_posix() != expected_status_path:
            defect(defects, "--status", "must match manifest branch status_path")

    worker_parallelism = root.get("worker_parallelism")
    manifest_max_workers = branch_entry.get("max_active_worker_packets")
    if not is_strict_int(manifest_max_workers) or manifest_max_workers < 1 or manifest_max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "manifest.branch.max_active_worker_packets", "must be an integer from 1 to 4")
    if (
        isinstance(worker_parallelism, dict)
        and is_strict_int(manifest_max_workers)
        and worker_parallelism.get("max_active_worker_packets") != manifest_max_workers
    ):
        defect(defects, "$.worker_parallelism.max_active_worker_packets", "must match manifest max_active_worker_packets")

    expected_ids = set(expected_worker_packet_ids(defects, branch_entry, branch_id))
    worker_statuses = root.get("worker_statuses")
    if not isinstance(worker_statuses, list) or not expected_ids:
        return branch_entry
    seen_ids = set()
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            continue
        if packet_id in seen_ids:
            defect(defects, f"$.worker_statuses[{index}].packet_id", f"duplicates worker packet {packet_id!r}")
            continue
        seen_ids.add(packet_id)
        if packet_id not in expected_ids:
            defect(defects, f"$.worker_statuses[{index}].packet_id", "is not declared by manifest work_items")
    if require_all_workers:
        missing = sorted(expected_ids - seen_ids)
        extra = sorted(seen_ids - expected_ids)
        if missing:
            defect(defects, "$.worker_statuses", f"missing manifest worker packet statuses: {', '.join(missing)}")
        if extra:
            defect(defects, "$.worker_statuses", f"contains worker packet statuses not declared in manifest: {', '.join(extra)}")
    return branch_entry


def validate_worker_parallelism(defects: list[str], value: object, path: str, *, worker_count: int) -> None:
    data = require_object(defects, value, path)
    required = [
        "max_worker_packets_per_branch",
        "max_active_worker_packets",
        "max_observed_active_worker_packets",
        "concurrent_launch_default",
        "rolling_refill_default",
        "scheduling_mode",
        "serialized_workers",
        "deferred_workers",
        "serial_reasons",
        "refill_events",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    if data.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_worker_packets_per_branch", "must be 4")
    max_active = data.get("max_active_worker_packets")
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_active_worker_packets", "must be an integer from 1 to 4")
    observed = data.get("max_observed_active_worker_packets")
    if not is_strict_int(observed) or observed < 0 or observed > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must be an integer from 0 to 4")
    if is_strict_int(max_active) and is_strict_int(observed) and observed > max_active:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must not exceed max_active_worker_packets")
    if data.get("concurrent_launch_default") is not True:
        defect(defects, f"{path}.concurrent_launch_default", "must be true")
    if data.get("rolling_refill_default") is not True:
        defect(defects, f"{path}.rolling_refill_default", "must be true")
    if data.get("scheduling_mode") != "rolling":
        defect(defects, f"{path}.scheduling_mode", "must be rolling")
    serialized_workers = require_string_list(defects, data.get("serialized_workers"), f"{path}.serialized_workers")
    deferred_workers = require_string_list(defects, data.get("deferred_workers"), f"{path}.deferred_workers")
    serial_reasons = require_string_list(defects, data.get("serial_reasons"), f"{path}.serial_reasons")
    refill_events = require_string_list(defects, data.get("refill_events"), f"{path}.refill_events")
    if serialized_workers and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify serialized workers")
    if deferred_workers and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify deferred workers")
    if is_strict_int(max_active) and max_active < MAX_WORKER_PACKETS_PER_BRANCH and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify max_active_worker_packets below 4")
    if is_strict_int(max_active) and worker_count > max_active and not refill_events:
        defect(defects, f"{path}.refill_events", "must record worker slot refill events when worker count exceeds max_active_worker_packets")
    if (
        worker_count > 1
        and is_strict_int(max_active)
        and is_strict_int(observed)
        and observed < min(max_active, worker_count)
        and not serial_reasons
    ):
        defect(defects, f"{path}.serial_reasons", "must justify observed worker parallelism below available worker concurrency")


def validate_worker_rollup(defects: list[str], worker_statuses: object, branch_status: object) -> None:
    if not isinstance(worker_statuses, list):
        return
    if branch_status != "pass":
        return
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        worker_status = item.get("status")
        if worker_status != "pass":
            defect(defects, f"$.worker_statuses[{index}].status", "must be pass when branch status is pass")


def validate_review_artifact(
    defects: list[str],
    value: object,
    expected_verdict: str,
    path: str,
    *,
    manifest: object,
    branch_id: str | None,
) -> None:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "role",
        "verdict",
        "findings",
        "commands_run",
        "verification_gaps",
        "residual_risks",
        "summary",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    packet_id = require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if packet_id and not SAFE_REVIEW_PACKET_RE.fullmatch(packet_id):
        defect(defects, f"{path}.packet_id", "must be a safe packet id")
    if branch_id and packet_id and not packet_id.startswith(f"{branch_id}-R"):
        defect(defects, f"{path}.packet_id", f"must start with {branch_id}-R")
    if data.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    verdict = data.get("verdict")
    if verdict not in REVIEW_STATUSES - {"missing"}:
        defect(defects, f"{path}.verdict", f"must be one of {sorted(REVIEW_STATUSES - {'missing'})}")
    if expected_verdict != "missing" and verdict != expected_verdict:
        defect(defects, f"{path}.verdict", "must match branch review_status")
    require_string_list(defects, data.get("findings"), f"{path}.findings")
    validate_base_range_diff_check(defects, data.get("commands_run"), f"{path}.commands_run", manifest)
    verification_gaps = require_string_list(defects, data.get("verification_gaps"), f"{path}.verification_gaps")
    if verdict == "mergeable" and verification_gaps:
        defect(defects, f"{path}.verification_gaps", "must be empty when verdict is mergeable")
    require_string_list(defects, data.get("residual_risks"), f"{path}.residual_risks")
    require_string(defects, data.get("summary"), f"{path}.summary")


def validate_review_artifact_for_branch(
    defects: list[str],
    branch_entry: dict,
    review_status: object,
    branch_status: object,
    *,
    manifest: object,
    manifest_path: Path,
) -> None:
    review_path = branch_entry.get("review_path")
    if not isinstance(review_path, str) or not review_path.strip():
        return
    review_artifact = (manifest_path.parent / review_path).resolve()
    require_existing = branch_status == "pass" or review_status != "missing"
    if not review_artifact.exists():
        if require_existing:
            defect(defects, "$.review_status", f"review artifact does not exist: {review_artifact}")
        return
    validate_review_artifact(
        defects,
        load_json_artifact(defects, review_artifact, "$.review_status"),
        str(review_status),
        "$.review_status",
        manifest=manifest,
        branch_id=str(branch_entry.get("id", "")) or None,
    )


def validate_branch_status(
    data: object,
    *,
    branch_id: str | None,
    branch: str | None,
    worktree: str | None,
    manifest: object,
    manifest_path: Path,
    status_path: Path,
) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "branch_id",
        "status",
        "branch",
        "worktree",
        "worker_statuses",
        "worker_parallelism",
        "lite_advice",
        "review_status",
        "changed_files",
        "commands_run",
        "tests",
        "dod_checklist",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    if branch_id and root.get("branch_id") != branch_id:
        defect(defects, "$.branch_id", f"must be {branch_id!r}")
    if branch and root.get("branch") != branch:
        defect(defects, "$.branch", f"must be {branch!r}")
    if worktree and root.get("worktree") != worktree:
        defect(defects, "$.worktree", f"must be {worktree!r}")
    require_string(defects, root.get("branch_id"), "$.branch_id")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    require_string(defects, root.get("branch"), "$.branch")
    root_worktree = require_string(defects, root.get("worktree"), "$.worktree")
    if root_worktree and not is_absolute_path(root_worktree):
        defect(defects, "$.worktree", "must be an absolute path without traversal")
    worker_statuses = root.get("worker_statuses")
    min_workers = 1 if status in {"pass", "partial"} else 0
    if not isinstance(worker_statuses, list) or len(worker_statuses) < min_workers or len(worker_statuses) > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "$.worker_statuses", f"must contain {min_workers} to 4 worker status objects")
    else:
        for index, item in enumerate(worker_statuses):
            validate_worker_status(defects, item, f"$.worker_statuses[{index}]")
    validate_worker_rollup(defects, worker_statuses, status)
    validate_worker_artifacts(
        defects,
        worker_statuses,
        status,
        branch=root.get("branch"),
        manifest_path=manifest_path,
    )
    worker_count = len(worker_statuses) if isinstance(worker_statuses, list) else 0
    validate_worker_parallelism(defects, root.get("worker_parallelism"), "$.worker_parallelism", worker_count=worker_count)
    root_branch_id = root.get("branch_id") if isinstance(root.get("branch_id"), str) else None
    validate_lite_advice_entries(
        defects,
        root.get("lite_advice"),
        "$.lite_advice",
        manifest_path=manifest_path,
        branch_id=root_branch_id,
    )
    branch_entry = validate_manifest_branch_contract(
        defects,
        root,
        manifest,
        manifest_path=manifest_path,
        status_path=status_path,
        require_all_workers=status in {"pass", "partial"},
    )
    review_status = root.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, "$.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if status == "pass" and review_status != "mergeable":
        defect(defects, "$.review_status", "must be mergeable when branch status is pass")
    if branch_entry:
        validate_review_artifact_for_branch(
            defects,
            branch_entry,
            review_status,
            status,
            manifest=manifest,
            manifest_path=manifest_path,
        )
    validate_path_list(defects, root.get("changed_files"), "$.changed_files")
    if status == "pass":
        validate_base_range_diff_check(defects, root.get("commands_run"), "$.commands_run", manifest)
    else:
        validate_command_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    validate_command_list(defects, root.get("tests"), "$.tests")
    require_string_list(defects, root.get("dod_checklist"), "$.dod_checklist", min_items=1)
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    if status == "pass" and blockers:
        defect(defects, "$.blockers", "must be empty when status is pass")
    if status in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, "$.blockers", "must explain non-pass status")
    require_string(defects, root.get("handoff"), "$.handoff")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id")
    parser.add_argument("--branch")
    parser.add_argument("--worktree")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True).as_posix() if args.worktree else None
    defects = validate_branch_status(
        load_json(status_path),
        branch_id=args.branch_id,
        branch=args.branch,
        worktree=worktree,
        manifest=load_json(manifest_path),
        manifest_path=manifest_path,
        status_path=status_path,
    )
    result = {"status": "pass" if not defects else "failed", "status_path": status_path.as_posix(), "defects": defects}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status={result['status']}")
        for item in defects:
            print(item)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
