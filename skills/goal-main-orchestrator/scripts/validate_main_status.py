#!/usr/bin/env python3
"""Validate a goal main-orchestrator status artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
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


CONTRACT = _load_contract()
STATUS_VALIDATION = _load_status_validation()
STATUSES = set(CONTRACT.STATUSES)
AUDIT_STATUSES = {"pass", "failed", "blocked", "missing"}
REVIEW_STATUSES = set(CONTRACT.REVIEW_STATUSES)
MAIN_LITE_PURPOSES = {"audit-defect-summary", "main-summary"}
MAX_TOTAL_BRANCHES = CONTRACT.DEFAULT_TOTAL_BRANCH_CAP
SAFE_REVIEW_PACKET_RE = STATUS_VALIDATION.SAFE_PACKET_RE

resolve_absolute_path = STATUS_VALIDATION.resolve_absolute_path
load_json = STATUS_VALIDATION.load_json
load_json_artifact = STATUS_VALIDATION.load_json_artifact
defect = STATUS_VALIDATION.defect
require_object = STATUS_VALIDATION.require_object
require_string = STATUS_VALIDATION.require_string
require_string_list = STATUS_VALIDATION.require_string_list
validate_base_range_diff_check = STATUS_VALIDATION.validate_base_range_diff_check
validate_telemetry_artifact = STATUS_VALIDATION.validate_telemetry_artifact
validate_scheduler_artifact = STATUS_VALIDATION.validate_scheduler_artifact
validate_scheduler_rollup = STATUS_VALIDATION.validate_scheduler_rollup
relative_hashes = STATUS_VALIDATION.relative_hashes
validate_reuse_policy = STATUS_VALIDATION.validate_reuse_policy
is_repo_relative_path = STATUS_VALIDATION.is_repo_relative_path
is_absolute_path = STATUS_VALIDATION.is_absolute_path
is_strict_int = STATUS_VALIDATION.is_strict_int


def validate_lite_advice_entries(defects: list[str], value: object, path: str, *, manifest_path: Path) -> None:
    STATUS_VALIDATION.validate_runtime_lite_advice_entries(
        defects,
        value,
        path,
        manifest_path=manifest_path,
        script_dir=Path(__file__).resolve().parent,
        validator_module_name="goal_main_validate_lite_advice",
        allowed_purposes=MAIN_LITE_PURPOSES,
        skill_name="goal-main-orchestrator",
        scope_label="main",
        malformed_packet_prefix="M",
    )


def validate_branch_summary(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = CONTRACT.BRANCH_SUMMARY_REQUIRED
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


def validate_audit_artifacts(defects: list[str], root: dict, *, manifest_path: Path, require_artifacts: bool) -> None:
    audit_status = root.get("audit_status")
    audit_path = manifest_path.parent / "audit" / "prompt-audit.json"
    if not audit_path.exists():
        if require_artifacts or audit_status != "missing":
            defect(defects, "$.audit_status", f"prompt audit artifact does not exist: {audit_path}")
        return
    audit = require_object(defects, load_json_artifact(defects, audit_path, "$.audit_status.artifact"), "$.audit_status.artifact")
    if audit.get("manifest") != manifest_path.as_posix():
        defect(defects, "$.audit_status.artifact.manifest", "must match manifest path")
    if audit.get("status") not in AUDIT_STATUSES - {"missing"}:
        defect(defects, "$.audit_status.artifact.status", f"must be one of {sorted(AUDIT_STATUSES - {'missing'})}")
    if audit_status != "missing" and audit.get("status") != audit_status:
        defect(defects, "$.audit_status", "must match prompt audit artifact status")
    if not isinstance(audit.get("can_start"), bool):
        defect(defects, "$.audit_status.artifact.can_start", "must be a boolean")
    require_string_list(defects, audit.get("checked_files"), "$.audit_status.artifact.checked_files")
    require_string_list(defects, audit.get("commands_run"), "$.audit_status.artifact.commands_run", min_items=1)
    if audit.get("status") == "pass":
        if audit.get("can_start") is not True:
            defect(defects, "$.audit_status.artifact.can_start", "must be true when audit status is pass")
        if audit.get("missing_dod_items"):
            defect(defects, "$.audit_status.artifact.missing_dod_items", "must be empty when audit status is pass")
    validate_telemetry_artifact(
        defects,
        audit_path.parent / "telemetry.json",
        "$.audit_status.telemetry_path",
        packet_id="prompt-audit",
        role="prompt-auditor",
        allowed_aliases=("gpt-5.5", "gpt-5.4"),
        require_called=True,
    )


def validate_telemetry_summary(defects: list[str], *, manifest_path: Path, require_artifacts: bool) -> None:
    summary_path = manifest_path.parent / "telemetry.summary.json"
    if not summary_path.exists():
        if require_artifacts:
            defect(defects, "$.telemetry_summary", f"telemetry summary does not exist: {summary_path}")
        return
    summary = require_object(defects, load_json_artifact(defects, summary_path, "$.telemetry_summary"), "$.telemetry_summary")
    if summary.get("schema_version") != 1:
        defect(defects, "$.telemetry_summary.schema_version", "must be 1")
    if summary.get("bundle_dir") != manifest_path.parent.as_posix():
        defect(defects, "$.telemetry_summary.bundle_dir", "must match manifest bundle directory")
    telemetry_files = summary.get("telemetry_files")
    if not isinstance(telemetry_files, list):
        defect(defects, "$.telemetry_summary.telemetry_files", "must be an array")
    elif require_artifacts and not telemetry_files:
        defect(defects, "$.telemetry_summary.telemetry_files", "must contain packet telemetry files")
    elif isinstance(telemetry_files, list):
        summary_mtime = summary_path.stat().st_mtime_ns
        for index, rel_path in enumerate(telemetry_files):
            if not isinstance(rel_path, str) or not rel_path.strip() or not is_repo_relative_path(rel_path):
                defect(defects, f"$.telemetry_summary.telemetry_files[{index}]", "must be a bundle-relative path without traversal")
                continue
            telemetry_file = manifest_path.parent / rel_path
            if not telemetry_file.exists():
                defect(defects, f"$.telemetry_summary.telemetry_files[{index}]", f"telemetry file does not exist: {telemetry_file}")
            elif telemetry_file.stat().st_mtime_ns > summary_mtime:
                defect(defects, f"$.telemetry_summary.telemetry_files[{index}]", "telemetry.summary.json is stale relative to this telemetry artifact")
    totals = require_object(defects, summary.get("totals"), "$.telemetry_summary.totals")
    for key in ["packet_count", "attempts_declared", "attempts_called", "prompt_chars", "output_chars", "event_log_chars"]:
        value = totals.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            defect(defects, f"$.telemetry_summary.totals.{key}", "must be a non-negative integer")
    if require_artifacts and isinstance(telemetry_files, list) and totals.get("packet_count") != len(telemetry_files):
        defect(defects, "$.telemetry_summary.totals.packet_count", "must match telemetry_files length")
    premium_usage = require_object(defects, summary.get("premium_usage"), "$.telemetry_summary.premium_usage")
    for key in ["audit_gpt_5_5", "reviewer_gpt_5_5"]:
        bucket = require_object(defects, premium_usage.get(key), f"$.telemetry_summary.premium_usage.{key}")
        for metric in ["attempts_declared", "attempts_called", "accepted_attempts"]:
            value = bucket.get(metric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                defect(defects, f"$.telemetry_summary.premium_usage.{key}.{metric}", "must be a non-negative integer")
        STATUS_VALIDATION.validate_usage(defects, bucket.get("known_usage"), f"$.telemetry_summary.premium_usage.{key}.known_usage")


def load_branch_status_validator(defects: list[str]):
    path = Path(__file__).resolve().parents[2] / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py"
    if not path.exists():
        defect(defects, "$", f"missing branch status validator: {path}")
        return None
    spec = importlib.util.spec_from_file_location("goal_branch_validate_branch_status", path)
    if spec is None or spec.loader is None:
        defect(defects, "$", f"could not load branch status validator: {path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        defect(defects, "$", f"could not import branch status validator {path}: {exc}")
        return None
    return module


def expected_branches_from_manifest(defects: list[str], manifest: object) -> dict[str, dict[str, str]]:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    expected: dict[str, dict[str, str]] = {}
    seen_values: dict[str, dict[str, str]] = {
        "id": {},
        "branch_name": {},
        "status_path": {},
        "review_path": {},
        "worktree_path": {},
    }
    if not isinstance(branches, list) or not branches:
        defect(defects, "manifest.branches", "must be a non-empty array")
        return expected
    for index, branch in enumerate(branches):
        branch_data = require_object(defects, branch, f"manifest.branches[{index}]")
        branch_id = require_string(defects, branch_data.get("id"), f"manifest.branches[{index}].id")
        branch_name = require_string(defects, branch_data.get("branch_name"), f"manifest.branches[{index}].branch_name")
        status_path = require_string(defects, branch_data.get("status_path"), f"manifest.branches[{index}].status_path")
        review_path = require_string(defects, branch_data.get("review_path"), f"manifest.branches[{index}].review_path")
        worktree_path = require_string(defects, branch_data.get("worktree_path"), f"manifest.branches[{index}].worktree_path")
        for field, value in [
            ("id", branch_id),
            ("branch_name", branch_name),
            ("status_path", status_path),
            ("review_path", review_path),
            ("worktree_path", worktree_path),
        ]:
            if not value:
                continue
            owner = seen_values[field].get(value)
            if owner is not None:
                defect(defects, f"manifest.branches[{index}].{field}", f"duplicates branch {owner}: {value!r}")
            else:
                seen_values[field][value] = branch_id
        if status_path and not is_repo_relative_path(status_path):
            defect(defects, f"manifest.branches[{index}].status_path", "must be a repo-relative path without traversal")
        if review_path and not is_repo_relative_path(review_path):
            defect(defects, f"manifest.branches[{index}].review_path", "must be a repo-relative path without traversal")
        if worktree_path and not is_repo_relative_path(worktree_path):
            defect(defects, f"manifest.branches[{index}].worktree_path", "must be a repo-relative path without traversal")
        if branch_id and status_path and review_path:
            expected[branch_id] = {
                "status_path": status_path,
                "review_path": review_path,
                "branch_name": branch_name,
                "depends_on": branch_data.get("depends_on", []),
            }
    return expected


def validate_main_scheduler(
    defects: list[str],
    root: dict,
    expected: dict[str, dict[str, object]],
    *,
    manifest: object,
    manifest_path: Path,
    status: object,
) -> None:
    manifest_root = require_object(defects, manifest, "manifest")
    max_active = manifest_root.get("max_active_branch_agents")
    if not is_strict_int(max_active) or max_active < 1 or max_active > CONTRACT.MAX_ACTIVE_BRANCH_AGENTS:
        defect(defects, "manifest.max_active_branch_agents", "must be an integer from 1 to 4")
        max_active = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
    parallelization = require_object(defects, manifest_root.get("parallelization"), "manifest.parallelization")
    scheduler_path_value = parallelization.get("scheduler_path")
    if scheduler_path_value != CONTRACT.MAIN_SCHEDULER_PATH:
        defect(defects, "manifest.parallelization.scheduler_path", f"must be {CONTRACT.MAIN_SCHEDULER_PATH!r}")
    expected_ids = list(expected.keys())
    dependencies = {}
    for branch_id, entry in expected.items():
        deps = entry.get("depends_on")
        dependencies[branch_id] = [item for item in deps if isinstance(item, str)] if isinstance(deps, list) else []
    summary = validate_scheduler_artifact(
        defects,
        manifest_path.parent / CONTRACT.MAIN_SCHEDULER_PATH,
        "$.branch_parallelism.scheduler_path",
        scheduler_kind="main-branch-pool",
        expected_path=CONTRACT.MAIN_SCHEDULER_PATH,
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=max_active,
        manifest_path=manifest_path,
        require_all_launched=status in {"pass", "partial"},
    )
    validate_scheduler_rollup(
        defects,
        root.get("branch_parallelism"),
        "$.branch_parallelism",
        expected_path=CONTRACT.MAIN_SCHEDULER_PATH,
        summary=summary,
        max_capacity=CONTRACT.MAX_ACTIVE_BRANCH_AGENTS,
    )


def validate_manifest_branch_coverage(defects: list[str], root: dict, expected: dict[str, dict[str, str]]) -> None:
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


def validate_review_artifact(
    defects: list[str],
    data: object,
    expected_verdict: str,
    path: str,
    *,
    manifest: object,
    branch_id: str | None,
    manifest_path: Path | None = None,
) -> None:
    review = require_object(defects, data, path)
    required = CONTRACT.REVIEW_REQUIRED
    for key in required:
        if key not in review:
            defect(defects, path, f"missing key: {key}")
    packet_id = require_string(defects, review.get("packet_id"), f"{path}.packet_id")
    if packet_id and not SAFE_REVIEW_PACKET_RE.fullmatch(packet_id):
        defect(defects, f"{path}.packet_id", "must be a safe packet id")
    if branch_id and packet_id and not packet_id.startswith(f"{branch_id}-R"):
        defect(defects, f"{path}.packet_id", f"must start with {branch_id}-R")
    if review.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    verdict = review.get("verdict")
    if verdict not in REVIEW_STATUSES - {"missing"}:
        defect(defects, f"{path}.verdict", f"must be one of {sorted(REVIEW_STATUSES - {'missing'})}")
    if expected_verdict != "missing" and verdict != expected_verdict:
        defect(defects, f"{path}.verdict", "must match branch summary review_status")
    require_string_list(defects, review.get("findings"), f"{path}.findings")
    validate_base_range_diff_check(defects, review.get("commands_run"), f"{path}.commands_run", manifest)
    verification_gaps = require_string_list(defects, review.get("verification_gaps"), f"{path}.verification_gaps")
    if verdict == "mergeable" and verification_gaps:
        defect(defects, f"{path}.verification_gaps", "must be empty when verdict is mergeable")
    require_string_list(defects, review.get("residual_risks"), f"{path}.residual_risks")
    if manifest_path is not None:
        relative_hashes(defects, review.get("input_hashes"), f"{path}.input_hashes", root_dir=manifest_path.parent)
    elif "input_hashes" in review and not isinstance(review.get("input_hashes"), dict):
        defect(defects, f"{path}.input_hashes", "must be an object")
    validate_reuse_policy(defects, review.get("reuse_policy"), f"{path}.reuse_policy")
    require_string(defects, review.get("summary"), f"{path}.summary")


def validate_branch_artifacts(
    defects: list[str],
    root: dict,
    expected: dict[str, dict[str, str]],
    *,
    manifest: object,
    manifest_path: Path,
    require_artifacts: bool,
) -> None:
    branch_statuses = root.get("branch_statuses")
    if not isinstance(branch_statuses, list):
        return
    branch_validator = None
    for index, item in enumerate(branch_statuses):
        if not isinstance(item, dict):
            continue
        item_path = f"$.branch_statuses[{index}]"
        branch_id = item.get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            continue
        expected_entry = expected.get(branch_id)
        if expected_entry is None:
            continue
        status_artifact = (manifest_path.parent / expected_entry["status_path"]).resolve()
        review_artifact = (manifest_path.parent / expected_entry["review_path"]).resolve()

        if not status_artifact.exists():
            defect(defects, f"{item_path}.status_path", f"artifact does not exist: {status_artifact}")
        else:
            branch_status = load_json_artifact(defects, status_artifact, f"{item_path}.status_path")
            if branch_validator is None:
                branch_validator = load_branch_status_validator(defects)
            if branch_validator is not None:
                branch_defects = branch_validator.validate_branch_status(
                    branch_status,
                    branch_id=branch_id,
                    branch=expected_entry.get("branch_name") or None,
                    worktree=None,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    status_path=status_artifact,
                )
                for branch_defect in branch_defects:
                    defect(defects, f"{item_path}.status_path", f"invalid branch status artifact: {branch_defect}")
            if isinstance(branch_status, dict):
                if branch_status.get("status") != item.get("status"):
                    defect(defects, f"{item_path}.status", "must match branch status artifact status")
                if branch_status.get("review_status") != item.get("review_status"):
                    defect(defects, f"{item_path}.review_status", "must match branch status artifact review_status")

        require_review_artifact = require_artifacts or item.get("review_status") != "missing"
        if not review_artifact.exists():
            if require_review_artifact:
                defect(defects, f"{item_path}.review_path", f"artifact does not exist: {review_artifact}")
        else:
            validate_review_artifact(
                defects,
                load_json_artifact(defects, review_artifact, f"{item_path}.review_path"),
                str(item.get("review_status", "")),
                f"{item_path}.review_path",
                manifest=manifest,
                branch_id=branch_id,
                manifest_path=manifest_path,
            )


def validate_main_status(data: object, *, job_id: str | None, manifest: object, manifest_path: Path) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = CONTRACT.MAIN_STATUS_REQUIRED
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    if job_id and root.get("job_id") != job_id:
        defect(defects, "$.job_id", f"must be {job_id!r}")
    manifest_root = require_object(defects, manifest, "manifest")
    manifest_job_id = manifest_root.get("job_id")
    if isinstance(manifest_job_id, str) and manifest_job_id.strip() and root.get("job_id") != manifest_job_id:
        defect(defects, "$.job_id", f"must match manifest job_id {manifest_job_id!r}")
    expected_branches = expected_branches_from_manifest(defects, manifest)
    require_string(defects, root.get("job_id"), "$.job_id")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    audit_status = root.get("audit_status")
    if audit_status not in AUDIT_STATUSES:
        defect(defects, "$.audit_status", f"must be one of {sorted(AUDIT_STATUSES)}")
    if status == "pass" and audit_status != "pass":
        defect(defects, "$.audit_status", "must be pass when main status is pass")
    validate_audit_artifacts(defects, root, manifest_path=manifest_path, require_artifacts=status == "pass")
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
    validate_manifest_branch_coverage(defects, root, expected_branches)
    validate_main_scheduler(
        defects,
        root,
        expected_branches,
        manifest=manifest,
        manifest_path=manifest_path,
        status=status,
    )
    validate_branch_artifacts(
        defects,
        root,
        expected_branches,
        manifest=manifest,
        manifest_path=manifest_path,
        require_artifacts=status == "pass",
    )
    validate_lite_advice_entries(defects, root.get("lite_advice"), "$.lite_advice", manifest_path=manifest_path)
    validate_telemetry_summary(defects, manifest_path=manifest_path, require_artifacts=status == "pass")
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
    defects = validate_main_status(
        load_json(status_path),
        job_id=args.job_id,
        manifest=load_json(manifest_path),
        manifest_path=manifest_path,
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
