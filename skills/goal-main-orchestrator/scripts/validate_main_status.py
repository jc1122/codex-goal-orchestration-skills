#!/usr/bin/env python3
"""Validate a goal main-orchestrator status artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


STATUSES = {"pass", "partial", "blocked", "failed"}
AUDIT_STATUSES = {"pass", "failed", "blocked", "missing"}
REVIEW_STATUSES = {"mergeable", "mergeable_after_fixes", "blocked", "reject", "missing"}
MAX_TOTAL_BRANCHES = 20


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
    )


def validate_branch_summary(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = ["branch_id", "status", "status_path", "review_path", "review_status"]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("branch_id"), f"{path}.branch_id")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path")
    review_path = require_string(defects, data.get("review_path"), f"{path}.review_path")
    if status_path and not is_repo_relative_path(status_path):
        defect(defects, f"{path}.status_path", "must be a repo-relative path without traversal")
    if review_path and not is_repo_relative_path(review_path):
        defect(defects, f"{path}.review_path", "must be a repo-relative path without traversal")
    review_status = data.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, f"{path}.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if data.get("status") == "pass" and review_status != "mergeable":
        defect(defects, f"{path}.review_status", "must be mergeable when branch status is pass")


def expected_branches_from_manifest(defects: list[str], manifest: object) -> dict[str, dict[str, str]]:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    expected: dict[str, dict[str, str]] = {}
    if not isinstance(branches, list) or not branches:
        defect(defects, "manifest.branches", "must be a non-empty array")
        return expected
    for index, branch in enumerate(branches):
        branch_data = require_object(defects, branch, f"manifest.branches[{index}]")
        branch_id = require_string(defects, branch_data.get("id"), f"manifest.branches[{index}].id")
        status_path = require_string(defects, branch_data.get("status_path"), f"manifest.branches[{index}].status_path")
        review_path = require_string(defects, branch_data.get("review_path"), f"manifest.branches[{index}].review_path")
        if branch_id in expected:
            defect(defects, f"manifest.branches[{index}].id", f"duplicates branch id {branch_id!r}")
        if status_path and not is_repo_relative_path(status_path):
            defect(defects, f"manifest.branches[{index}].status_path", "must be a repo-relative path without traversal")
        if review_path and not is_repo_relative_path(review_path):
            defect(defects, f"manifest.branches[{index}].review_path", "must be a repo-relative path without traversal")
        if branch_id and status_path and review_path:
            expected[branch_id] = {"status_path": status_path, "review_path": review_path}
    return expected


def validate_manifest_branch_coverage(defects: list[str], root: dict, manifest: object) -> None:
    expected = expected_branches_from_manifest(defects, manifest)
    branch_statuses = root.get("branch_statuses")
    if not isinstance(branch_statuses, list):
        return
    seen: dict[str, int] = {}
    for index, item in enumerate(branch_statuses):
        if not isinstance(item, dict):
            continue
        branch_id = item.get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            continue
        if branch_id in seen:
            defect(defects, f"$.branch_statuses[{index}].branch_id", f"duplicates branch summary for {branch_id!r}")
            continue
        seen[branch_id] = index
        expected_entry = expected.get(branch_id)
        if expected_entry is None:
            defect(defects, f"$.branch_statuses[{index}].branch_id", "is not declared in manifest")
            continue
        if item.get("status_path") != expected_entry["status_path"]:
            defect(defects, f"$.branch_statuses[{index}].status_path", "must match manifest branch status_path")
        if item.get("review_path") != expected_entry["review_path"]:
            defect(defects, f"$.branch_statuses[{index}].review_path", "must match manifest branch review_path")
    if root.get("status") in {"pass", "partial"}:
        missing = sorted(set(expected) - set(seen))
        extra = sorted(set(seen) - set(expected))
        if missing:
            defect(defects, "$.branch_statuses", f"missing manifest branch summaries: {', '.join(missing)}")
        if extra:
            defect(defects, "$.branch_statuses", f"contains branch summaries not declared in manifest: {', '.join(extra)}")


def validate_main_status(data: object, *, job_id: str | None, manifest: object) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "job_id",
        "status",
        "audit_status",
        "branch_statuses",
        "commands_run",
        "dod_checklist",
        "blockers",
        "summary",
    ]
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    if job_id and root.get("job_id") != job_id:
        defect(defects, "$.job_id", f"must be {job_id!r}")
    manifest_root = require_object(defects, manifest, "manifest")
    manifest_job_id = manifest_root.get("job_id")
    if isinstance(manifest_job_id, str) and manifest_job_id.strip() and root.get("job_id") != manifest_job_id:
        defect(defects, "$.job_id", f"must match manifest job_id {manifest_job_id!r}")
    require_string(defects, root.get("job_id"), "$.job_id")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    audit_status = root.get("audit_status")
    if audit_status not in AUDIT_STATUSES:
        defect(defects, "$.audit_status", f"must be one of {sorted(AUDIT_STATUSES)}")
    if status == "pass" and audit_status != "pass":
        defect(defects, "$.audit_status", "must be pass when main status is pass")
    branch_statuses = root.get("branch_statuses")
    min_branches = 1 if status in {"pass", "partial"} else 0
    if not isinstance(branch_statuses, list) or len(branch_statuses) < min_branches or len(branch_statuses) > MAX_TOTAL_BRANCHES:
        defect(defects, "$.branch_statuses", f"must contain {min_branches} to {MAX_TOTAL_BRANCHES} item(s)")
    else:
        for index, item in enumerate(branch_statuses):
            validate_branch_summary(defects, item, f"$.branch_statuses[{index}]")
        if status == "pass":
            for index, item in enumerate(branch_statuses):
                if isinstance(item, dict) and item.get("status") != "pass":
                    defect(defects, f"$.branch_statuses[{index}].status", "must be pass when main status is pass")
    validate_manifest_branch_coverage(defects, root, manifest)
    require_string_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    require_string_list(defects, root.get("dod_checklist"), "$.dod_checklist", min_items=1)
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    if status == "pass" and blockers:
        defect(defects, "$.blockers", "must be empty when status is pass")
    if status in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, "$.blockers", "must explain non-pass status")
    require_string(defects, root.get("summary"), "$.summary")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    defects = validate_main_status(load_json(status_path), job_id=args.job_id, manifest=load_json(manifest_path))
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
