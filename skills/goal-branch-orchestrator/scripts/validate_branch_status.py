#!/usr/bin/env python3
"""Validate a goal branch-orchestrator status artifact."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


STATUSES = {"pass", "partial", "blocked", "failed"}
REVIEW_STATUSES = {"mergeable", "mergeable_after_fixes", "blocked", "reject", "missing"}
MAX_WORKER_PACKETS_PER_BRANCH = 4
PORCELAIN_PREFIX_RE = re.compile(r"^[ MADRCU?!]{2} ")


def resolve_absolute_path(value: str, field: str, *, must_exist: bool) -> Path:
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators: {value!r}")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise SystemExit(f"{field} must be an absolute path: {value!r}")
    if ".." in expanded.parts:
        raise SystemExit(f"{field} must not contain '..' traversal: {value!r}")
    if must_exist and not expanded.exists():
        raise SystemExit(f"{field} does not exist: {expanded}")
    return expanded.resolve(strict=must_exist)


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def defect(defects: list[str], path: str, message: str) -> None:
    defects.append(f"{path}: {message}")


def require_object(defects: list[str], value: object, path: str) -> dict:
    if not isinstance(value, dict):
        defect(defects, path, "must be an object")
        return {}
    return value


def require_string(defects: list[str], value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        defect(defects, path, "must be a non-empty string")
        return ""
    return value


def require_string_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            defect(defects, f"{path}[{index}]", "must be a non-empty string")
        else:
            result.append(item)
    if len(result) < min_items:
        defect(defects, path, f"must contain at least {min_items} item(s)")
    return result


def is_repo_relative_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or value.startswith("/")
        or value.startswith("./")
        or value == "."
        or "/./" in value
        or value.endswith("/.")
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or PORCELAIN_PREFIX_RE.match(value) is not None
    )


def is_absolute_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or not path.is_absolute()
        or ".." in path.parts
    )


def validate_path_list(defects: list[str], value: object, path: str) -> None:
    for index, item in enumerate(require_string_list(defects, value, path)):
        if not is_repo_relative_path(item):
            defect(defects, f"{path}[{index}]", "must be a repo-relative path without git porcelain status")


def validate_command_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> None:
    require_string_list(defects, value, path, min_items=min_items)


def validate_worker_status(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "status",
        "status_path",
        "worktree",
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
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")


def validate_worker_parallelism(defects: list[str], value: object, path: str, *, worker_count: int) -> None:
    data = require_object(defects, value, path)
    required = [
        "max_worker_packets_per_branch",
        "max_active_worker_packets",
        "max_observed_active_worker_packets",
        "concurrent_launch_default",
        "serialized_workers",
        "serial_reasons",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    if data.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_worker_packets_per_branch", "must be 4")
    max_active = data.get("max_active_worker_packets")
    if not isinstance(max_active, int) or max_active < 1 or max_active > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_active_worker_packets", "must be an integer from 1 to 4")
    observed = data.get("max_observed_active_worker_packets")
    if not isinstance(observed, int) or observed < 0 or observed > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must be an integer from 0 to 4")
    if isinstance(max_active, int) and isinstance(observed, int) and observed > max_active:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must not exceed max_active_worker_packets")
    if data.get("concurrent_launch_default") is not True:
        defect(defects, f"{path}.concurrent_launch_default", "must be true")
    serialized_workers = require_string_list(defects, data.get("serialized_workers"), f"{path}.serialized_workers")
    serial_reasons = require_string_list(defects, data.get("serial_reasons"), f"{path}.serial_reasons")
    if serialized_workers and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify serialized workers")
    if isinstance(max_active, int) and max_active < MAX_WORKER_PACKETS_PER_BRANCH and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify max_active_worker_packets below 4")
    if (
        worker_count > 1
        and isinstance(max_active, int)
        and isinstance(observed, int)
        and observed < min(max_active, worker_count)
        and not serial_reasons
    ):
        defect(defects, f"{path}.serial_reasons", "must justify observed worker parallelism below available worker concurrency")


def validate_branch_status(data: object, *, branch_id: str | None, branch: str | None, worktree: str | None) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "branch_id",
        "status",
        "branch",
        "worktree",
        "worker_statuses",
        "worker_parallelism",
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
    worker_count = len(worker_statuses) if isinstance(worker_statuses, list) else 0
    validate_worker_parallelism(defects, root.get("worker_parallelism"), "$.worker_parallelism", worker_count=worker_count)
    review_status = root.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, "$.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if status == "pass" and review_status != "mergeable":
        defect(defects, "$.review_status", "must be mergeable when branch status is pass")
    validate_path_list(defects, root.get("changed_files"), "$.changed_files")
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
    parser.add_argument("--branch-id")
    parser.add_argument("--branch")
    parser.add_argument("--worktree")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True).as_posix() if args.worktree else None
    defects = validate_branch_status(load_json(status_path), branch_id=args.branch_id, branch=args.branch, worktree=worktree)
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
