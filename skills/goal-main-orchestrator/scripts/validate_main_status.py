#!/usr/bin/env python3
"""Validate a goal main-orchestrator status artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, NamedTuple


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
TELEMETRY_ROOTS = ("audit", "workers", "research", "reviewers", "lite", "amendments")
AUDIT_TELEMETRY_ALIASES = ("gpt-5.5", "gpt-5.4", "deterministic-prompt-audit")
BRANCH_STATUS_VALIDATOR = None

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
archived_manifest_sha256s = STATUS_VALIDATION.archived_manifest_sha256s
archived_manifest_hashes_by_rel_path = STATUS_VALIDATION.archived_manifest_hashes_by_rel_path
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
    if not isinstance(data.get("status"), str) or data.get("status") not in STATUSES:
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
    recovered_by = data.get("recovered_by")
    if recovered_by is not None:
        require_string_list(defects, recovered_by, f"{path}.recovered_by", min_items=1)
    recovery_status = data.get("recovery_status")
    if recovery_status is not None and (
        not isinstance(recovery_status, str) or recovery_status not in {"pending", "recovered"}
    ):
        defect(defects, f"{path}.recovery_status", "must be pending or recovered")
    if recovery_status == "recovered" and not isinstance(recovered_by, list):
        defect(defects, f"{path}.recovered_by", "is required when recovery_status is recovered")
    review_waiver_path = data.get("review_waiver_path")
    if review_waiver_path is not None and (
        not isinstance(review_waiver_path, str)
        or not review_waiver_path.strip()
        or not is_repo_relative_path(review_waiver_path)
    ):
        defect(defects, f"{path}.review_waiver_path", "must be a repo-relative path without traversal")


def branch_summary_success(item: dict | None) -> bool:
    return bool(item and item.get("status") == "pass" and item.get("review_status") == "mergeable")


def manifest_branch_declares_recovery(manifest_branch: dict[str, Any] | None, target_branch_id: object) -> bool:
    if not isinstance(target_branch_id, str) or not target_branch_id.strip():
        return False
    if not isinstance(manifest_branch, dict):
        return False
    for field in ["recovers_from", "supersedes"]:
        values = manifest_branch.get(field)
        if isinstance(values, list) and target_branch_id in [item for item in values if isinstance(item, str)]:
            return True
    return False


def branch_summary_recovered(item: dict, by_id: dict[str, dict], manifest_by_id: dict[str, dict[str, Any]]) -> bool:
    if branch_summary_success(item):
        return True
    if item.get("recovery_status") != "recovered":
        return False
    target_branch_id = item.get("branch_id")
    recovered_by = item.get("recovered_by")
    if not isinstance(recovered_by, list):
        return False
    return any(
        isinstance(branch_id, str)
        and branch_summary_success(by_id.get(branch_id))
        and manifest_branch_declares_recovery(manifest_by_id.get(branch_id), target_branch_id)
        for branch_id in recovered_by
    )


def validate_audit_artifacts(defects: list[str], root: dict, *, manifest_path: Path, require_artifacts: bool) -> None:
    audit_status = root.get("audit_status")
    audit_path = manifest_path.parent / "audit" / "prompt-audit.json"
    if not audit_path.exists():
        if require_artifacts or audit_status != "missing":
            defect(defects, "$.audit_status", f"prompt audit artifact does not exist: {audit_path}")
        return
    audit = require_object(
        defects, load_json_artifact(defects, audit_path, "$.audit_status.artifact"), "$.audit_status.artifact"
    )
    if audit.get("manifest") != manifest_path.as_posix():
        defect(defects, "$.audit_status.artifact.manifest", "must match manifest path")
    if not isinstance(audit.get("status"), str) or audit.get("status") not in AUDIT_STATUSES - {"missing"}:
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
        allowed_aliases=AUDIT_TELEMETRY_ALIASES,
        require_called=True,
    )


def validate_telemetry_summary_files(
    defects: list[str], summary: dict, summary_path: Path, *, manifest_path: Path, require_artifacts: bool
) -> object:
    telemetry_files = summary.get("telemetry_files")
    discovered = sorted(
        path.relative_to(manifest_path.parent).as_posix()
        for root_name in TELEMETRY_ROOTS
        for path in (manifest_path.parent / root_name).glob("**/telemetry.json")
        if (manifest_path.parent / root_name).is_dir()
    )
    if not isinstance(telemetry_files, list):
        defect(defects, "$.telemetry_summary.telemetry_files", "must be an array")
    elif require_artifacts and not telemetry_files:
        defect(defects, "$.telemetry_summary.telemetry_files", "must contain packet telemetry files")
    elif isinstance(telemetry_files, list):
        listed = {item for item in telemetry_files if isinstance(item, str)}
        for rel_path in discovered:
            if rel_path not in listed:
                defect(
                    defects, "$.telemetry_summary.telemetry_files", f"omits discovered telemetry artifact: {rel_path}"
                )
        summary_mtime = summary_path.stat().st_mtime_ns
        for index, rel_path in enumerate(telemetry_files):
            if not isinstance(rel_path, str) or not rel_path.strip() or not is_repo_relative_path(rel_path):
                defect(
                    defects,
                    f"$.telemetry_summary.telemetry_files[{index}]",
                    "must be a bundle-relative path without traversal",
                )
                continue
            telemetry_file = manifest_path.parent / rel_path
            if not telemetry_file.exists():
                defect(
                    defects,
                    f"$.telemetry_summary.telemetry_files[{index}]",
                    f"telemetry file does not exist: {telemetry_file}",
                )
            elif telemetry_file.stat().st_mtime_ns > summary_mtime:
                defect(
                    defects,
                    f"$.telemetry_summary.telemetry_files[{index}]",
                    "telemetry.summary.json is stale relative to this telemetry artifact",
                )
    return telemetry_files


def validate_telemetry_summary_totals(
    defects: list[str], summary: dict, telemetry_files: object, *, require_artifacts: bool
) -> dict:
    totals = require_object(defects, summary.get("totals"), "$.telemetry_summary.totals")
    for key in [
        "packet_count",
        "attempts_declared",
        "attempts_called",
        "prompt_chars",
        "output_chars",
        "event_log_chars",
    ]:
        value = totals.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            defect(defects, f"$.telemetry_summary.totals.{key}", "must be a non-negative integer")
    if require_artifacts and isinstance(telemetry_files, list) and totals.get("packet_count") != len(telemetry_files):
        defect(defects, "$.telemetry_summary.totals.packet_count", "must match telemetry_files length")
    return totals


def validate_telemetry_summary_premium(defects: list[str], summary: dict) -> None:
    premium_usage = require_object(defects, summary.get("premium_usage"), "$.telemetry_summary.premium_usage")
    for key in ["audit_gpt_5_5", "amender_gpt_5_5", "reviewer_gpt_5_5"]:
        bucket = require_object(defects, premium_usage.get(key), f"$.telemetry_summary.premium_usage.{key}")
        for metric in ["attempts_declared", "attempts_called", "accepted_attempts"]:
            value = bucket.get(metric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                defect(defects, f"$.telemetry_summary.premium_usage.{key}.{metric}", "must be a non-negative integer")
        STATUS_VALIDATION.validate_usage(
            defects, bucket.get("known_usage"), f"$.telemetry_summary.premium_usage.{key}.known_usage"
        )


def validate_telemetry_summary_cost(defects: list[str], summary: dict, totals: dict) -> dict:
    cost = require_object(defects, summary.get("cost_summary"), "$.telemetry_summary.cost_summary")
    for key in [
        "declared_attempts",
        "called_attempts",
        "prompt_bytes",
        "output_bytes",
        "fallback_count",
        "failed_same_class_attempts",
    ]:
        value = cost.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            defect(defects, f"$.telemetry_summary.cost_summary.{key}", "must be a non-negative integer")
    if cost.get("declared_attempts") != totals.get("attempts_declared"):
        defect(defects, "$.telemetry_summary.cost_summary.declared_attempts", "must match totals.attempts_declared")
    if cost.get("called_attempts") != totals.get("attempts_called"):
        defect(defects, "$.telemetry_summary.cost_summary.called_attempts", "must match totals.attempts_called")
    if cost.get("prompt_bytes") != totals.get("prompt_bytes"):
        defect(defects, "$.telemetry_summary.cost_summary.prompt_bytes", "must match totals.prompt_bytes")
    if cost.get("output_bytes") != totals.get("output_bytes"):
        defect(defects, "$.telemetry_summary.cost_summary.output_bytes", "must match totals.output_bytes")
    for key in [
        "accepted_aliases",
        "declared_aliases",
        "called_aliases",
        "premium_aliases_declared",
        "premium_aliases_called",
        "premium_aliases_accepted",
        "premium_aliases_avoided",
    ]:
        bucket = require_object(defects, cost.get(key), f"$.telemetry_summary.cost_summary.{key}")
        for alias, value in bucket.items():
            if not isinstance(alias, str) or not alias.strip():
                defect(defects, f"$.telemetry_summary.cost_summary.{key}", "alias keys must be non-empty strings")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                defect(defects, f"$.telemetry_summary.cost_summary.{key}.{alias}", "must be a non-negative integer")
    return cost


def validate_telemetry_summary_mini_spark(defects: list[str], cost: dict) -> None:
    mini_spark = require_object(
        defects, cost.get("mini_spark_usage"), "$.telemetry_summary.cost_summary.mini_spark_usage"
    )
    for alias in ["codex-mini", "codex-spark"]:
        bucket = require_object(
            defects, mini_spark.get(alias), f"$.telemetry_summary.cost_summary.mini_spark_usage.{alias}"
        )
        for metric in ["attempts_declared", "attempts_called", "accepted_attempts"]:
            value = bucket.get(metric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                defect(
                    defects,
                    f"$.telemetry_summary.cost_summary.mini_spark_usage.{alias}.{metric}",
                    "must be a non-negative integer",
                )
        STATUS_VALIDATION.validate_usage(
            defects, bucket.get("known_usage"), f"$.telemetry_summary.cost_summary.mini_spark_usage.{alias}.known_usage"
        )


def validate_telemetry_summary(defects: list[str], *, manifest_path: Path, require_artifacts: bool) -> None:
    summary_path = manifest_path.parent / "telemetry.summary.json"
    if not summary_path.exists():
        if require_artifacts:
            defect(defects, "$.telemetry_summary", f"telemetry summary does not exist: {summary_path}")
        return
    summary = require_object(
        defects, load_json_artifact(defects, summary_path, "$.telemetry_summary"), "$.telemetry_summary"
    )
    if summary.get("schema_version") != 1:
        defect(defects, "$.telemetry_summary.schema_version", "must be 1")
    if summary.get("bundle_dir") != manifest_path.parent.as_posix():
        defect(defects, "$.telemetry_summary.bundle_dir", "must match manifest bundle directory")
    telemetry_files = validate_telemetry_summary_files(
        defects, summary, summary_path, manifest_path=manifest_path, require_artifacts=require_artifacts
    )
    totals = validate_telemetry_summary_totals(defects, summary, telemetry_files, require_artifacts=require_artifacts)
    validate_telemetry_summary_premium(defects, summary)
    cost = validate_telemetry_summary_cost(defects, summary, totals)
    validate_telemetry_summary_mini_spark(defects, cost)


def validate_decision_artifact(
    defects: list[str], data: object, path: str, *, amendment_id: str, manifest_path: Path
) -> dict:
    decision = require_object(defects, data, path)
    if decision.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if decision.get("amendment_id") != amendment_id:
        defect(defects, f"{path}.amendment_id", f"must be {amendment_id!r}")
    decision_value = decision.get("decision")
    if decision_value not in CONTRACT.AMENDMENT_DECISIONS:
        defect(defects, f"{path}.decision", f"must be one of {list(CONTRACT.AMENDMENT_DECISIONS)}")
    reason_code = decision.get("reason_code")
    if reason_code not in CONTRACT.AMENDMENT_DECISION_REASON_CODES:
        defect(defects, f"{path}.reason_code", f"must be one of {list(CONTRACT.AMENDMENT_DECISION_REASON_CODES)}")
    if decision_value == "launch" and reason_code not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        defect(defects, f"{path}.reason_code", "is not valid for a launch decision")
    if decision_value == "skip" and reason_code in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        defect(
            defects, f"{path}.reason_code", "describes a launch-required condition and is not valid for a skip decision"
        )
    require_string(defects, decision.get("reason"), f"{path}.reason")
    if decision.get("manifest") != manifest_path.as_posix():
        defect(defects, f"{path}.manifest", "must match current manifest path")
    manifest_sha = decision.get("manifest_sha256")
    if not isinstance(manifest_sha, str) or not STATUS_VALIDATION.SHA256_RE.fullmatch(manifest_sha):
        defect(defects, f"{path}.manifest_sha256", "must be sha256:<64 lowercase hex chars>")
    require_string_list(defects, decision.get("terminal_branch_ids"), f"{path}.terminal_branch_ids", min_items=1)
    active_ids = decision.get("active_branch_ids")
    if not isinstance(active_ids, list) or any(not isinstance(item, str) or not item.strip() for item in active_ids):
        defect(defects, f"{path}.active_branch_ids", "must be an array of non-empty strings")
        active_id_set: set[str] = set()
    else:
        active_id_set = set(active_ids)
    terminal_statuses = require_object(
        defects, decision.get("terminal_branch_statuses"), f"{path}.terminal_branch_statuses"
    )
    for branch_id in (
        decision.get("terminal_branch_ids", []) if isinstance(decision.get("terminal_branch_ids"), list) else []
    ):
        if isinstance(branch_id, str) and branch_id not in terminal_statuses:
            defect(defects, f"{path}.terminal_branch_statuses", f"missing terminal status for {branch_id}")
    raw_terminal_ids = decision.get("terminal_branch_ids")
    terminal_ids = (
        {item for item in raw_terminal_ids if isinstance(item, str)} if isinstance(raw_terminal_ids, list) else set()
    )
    overlap = sorted(active_id_set & terminal_ids)
    if overlap:
        defect(defects, f"{path}.active_branch_ids", "must not overlap terminal_branch_ids: " + ", ".join(overlap))
    return decision


def validate_amendment_decision_blockers(defects: list[str], root: dict, *, status: object) -> set[str]:
    records = root.get("amendment_decision_blockers", [])
    if records is None:
        return set()
    if not isinstance(records, list):
        defect(defects, "$.amendment_decision_blockers", "must be an array when present")
        return set()
    if status == "pass" and records:
        defect(defects, "$.amendment_decision_blockers", "must be empty or omitted when status is pass")
    branch_statuses = root.get("branch_statuses")
    terminal_branch_statuses: dict[str, str] = {}
    if isinstance(branch_statuses, list):
        for item in branch_statuses:
            if isinstance(item, dict) and isinstance(item.get("branch_id"), str) and item.get("status") in STATUSES:
                terminal_branch_statuses[str(item.get("branch_id"))] = str(item.get("status"))
    covered: set[str] = set()
    for index, item in enumerate(records):
        item_path = f"$.amendment_decision_blockers[{index}]"
        record = require_object(defects, item, item_path)
        if record.get("schema_version") != 1:
            defect(defects, f"{item_path}.schema_version", "must be 1")
        reason_code = require_string(defects, record.get("reason_code"), f"{item_path}.reason_code")
        if reason_code not in {"amendment_creation_blocked", "amender_packet_blocked", "amender_route_blocked"}:
            defect(defects, f"{item_path}.reason_code", "must be an accepted amendment blocker reason")
        require_string(defects, record.get("reason"), f"{item_path}.reason")
        terminal_ids = require_string_list(
            defects, record.get("terminal_branch_ids"), f"{item_path}.terminal_branch_ids", min_items=1
        )
        active_ids = record.get("active_branch_ids", [])
        if not isinstance(active_ids, list) or any(
            not isinstance(value, str) or not value.strip() for value in active_ids
        ):
            defect(defects, f"{item_path}.active_branch_ids", "must be an array of non-empty strings")
            active_set: set[str] = set()
        else:
            active_set = set(active_ids)
        overlap = sorted(active_set & set(terminal_ids))
        if overlap:
            defect(
                defects, f"{item_path}.active_branch_ids", "must not overlap terminal_branch_ids: " + ", ".join(overlap)
            )
        statuses = require_object(
            defects, record.get("terminal_branch_statuses"), f"{item_path}.terminal_branch_statuses"
        )
        for branch_id in terminal_ids:
            if branch_id not in terminal_branch_statuses:
                defect(
                    defects,
                    f"{item_path}.terminal_branch_ids",
                    f"branch is not present in branch_statuses: {branch_id}",
                )
                continue
            if statuses.get(branch_id) != terminal_branch_statuses[branch_id]:
                defect(defects, f"{item_path}.terminal_branch_statuses", f"must match branch_statuses for {branch_id}")
            covered.add(branch_id)
        require_string_list(defects, record.get("evidence_paths"), f"{item_path}.evidence_paths", min_items=1)
        ignored = record.get("ignored_decision_artifacts", [])
        if ignored is not None:
            require_string_list(defects, ignored, f"{item_path}.ignored_decision_artifacts")
    return covered


def validate_packet_validation_artifact(
    defects: list[str], data: object, path: str, *, amendment_id: str, manifest_path: Path
) -> dict:
    validation = require_object(defects, data, path)
    if validation.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if validation.get("amendment_id") != amendment_id:
        defect(defects, f"{path}.amendment_id", f"must be {amendment_id!r}")
    if validation.get("status") != "pass":
        defect(defects, f"{path}.status", "must be pass")
    if validation.get("manifest") != manifest_path.as_posix():
        defect(defects, f"{path}.manifest", "must match current manifest path")
    for key in ["packet_dir", "decision", "route", "telemetry", "proposal"]:
        require_string(defects, validation.get(key), f"{path}.{key}")
    if validation.get("proposal_sha256") is not None:
        proposal_value = validation.get("proposal")
        if isinstance(proposal_value, str) and proposal_value.strip():
            proposal_path = Path(proposal_value)
            if not proposal_path.is_file():
                defect(
                    defects, f"{path}.proposal", f"artifact does not exist or is not a regular file: {proposal_path}"
                )
            elif validation.get("proposal_sha256") != STATUS_VALIDATION.sha256_file(proposal_path):
                defect(defects, f"{path}.proposal_sha256", "must match current proposal sha256")
    telemetry_value = validation.get("telemetry")
    if isinstance(telemetry_value, str) and telemetry_value.strip():
        telemetry_path = Path(telemetry_value)
        validate_telemetry_artifact(
            defects,
            telemetry_path,
            f"{path}.telemetry",
            packet_id=amendment_id,
            role=CONTRACT.AMENDER_ROLE,
            allowed_aliases=getattr(CONTRACT, "ALLOWED_AMENDER_TELEMETRY_ALIASES", CONTRACT.ALLOWED_AMENDER_ROUTES),
            require_called=True,
        )
    validation_defects = validation.get("defects")
    if not isinstance(validation_defects, list):
        defect(defects, f"{path}.defects", "must be an array")
    elif validation_defects:
        defect(defects, f"{path}.defects", "must be empty when status is pass")
    return validation


class AmendmentDecisionAccumulators(NamedTuple):
    """Mutable sets threaded through the per-record amendment decision loop."""

    recorded_decisions: set[str]
    recorded_launch_ids: set[str]
    decision_branch_ids: set[str]
    seen_ids: set[str]


def validate_amendment_decision_record(
    defects: list[str],
    item: object,
    item_path: str,
    *,
    bundle_dir: Path,
    manifest_path: Path,
    acc: AmendmentDecisionAccumulators,
) -> None:
    record = require_object(defects, item, item_path)
    amendment_id = require_string(defects, record.get("amendment_id"), f"{item_path}.amendment_id")
    if amendment_id in acc.seen_ids:
        defect(defects, f"{item_path}.amendment_id", f"duplicates amendment decision {amendment_id!r}")
    if amendment_id:
        acc.seen_ids.add(amendment_id)
    decision_value = record.get("decision")
    if decision_value not in CONTRACT.AMENDMENT_DECISIONS:
        defect(defects, f"{item_path}.decision", f"must be one of {list(CONTRACT.AMENDMENT_DECISIONS)}")
    decision_path_value = require_string(defects, record.get("decision_path"), f"{item_path}.decision_path")
    if not decision_path_value or not is_repo_relative_path(decision_path_value):
        defect(defects, f"{item_path}.decision_path", "must be a bundle-relative path without traversal")
        return
    if amendment_id and decision_path_value != f"amendments/{amendment_id}.decision.json":
        defect(defects, f"{item_path}.decision_path", "must use the deterministic amendment decision path")
    acc.recorded_decisions.add(decision_path_value)
    decision_path = bundle_dir / decision_path_value
    decision_data = validate_decision_artifact(
        defects,
        load_json_artifact(defects, decision_path, f"{item_path}.decision_path"),
        f"{item_path}.decision_path",
        amendment_id=amendment_id,
        manifest_path=manifest_path,
    )
    if decision_data.get("decision") != decision_value:
        defect(defects, f"{item_path}.decision", "must match decision artifact")
    for branch_id in (
        decision_data.get("terminal_branch_ids", [])
        if isinstance(decision_data.get("terminal_branch_ids"), list)
        else []
    ):
        if isinstance(branch_id, str):
            acc.decision_branch_ids.add(branch_id)

    packet_validation_value = record.get("packet_validation_path")
    if decision_value == "launch":
        if amendment_id:
            acc.recorded_launch_ids.add(amendment_id)
        packet_validation_path_text = require_string(
            defects, packet_validation_value, f"{item_path}.packet_validation_path"
        )
        if not packet_validation_path_text or not is_repo_relative_path(packet_validation_path_text):
            defect(defects, f"{item_path}.packet_validation_path", "must be a bundle-relative path without traversal")
            return
        if amendment_id and packet_validation_path_text != f"amendments/{amendment_id}.packet/packet.validation.json":
            defect(
                defects,
                f"{item_path}.packet_validation_path",
                "must use the deterministic amender packet validation path",
            )
        packet_validation_path = bundle_dir / packet_validation_path_text
        validate_packet_validation_artifact(
            defects,
            load_json_artifact(defects, packet_validation_path, f"{item_path}.packet_validation_path"),
            f"{item_path}.packet_validation_path",
            amendment_id=amendment_id,
            manifest_path=manifest_path,
        )
    elif packet_validation_value is not None:
        defect(defects, f"{item_path}.packet_validation_path", "must be null or omitted for skip decisions")


def validate_amendment_omitted_decisions(
    defects: list[str],
    discovered_decisions: set[str],
    recorded_decisions: set[str],
    *,
    bundle_dir: Path,
    manifest_path: Path,
    blocker_branch_ids: set[str],
    status: object,
) -> None:
    omitted_decisions = sorted(discovered_decisions - recorded_decisions)
    current_omitted: list[str] = []
    for decision_rel in omitted_decisions:
        amendment_id = Path(decision_rel).name.removesuffix(".decision.json")
        local_defects: list[str] = []
        decision_data = validate_decision_artifact(
            local_defects,
            load_json_artifact(local_defects, bundle_dir / decision_rel, "$.amendment_decisions.omitted"),
            "$.amendment_decisions.omitted",
            amendment_id=amendment_id,
            manifest_path=manifest_path,
        )
        decision_terminal_ids = (
            {branch_id for branch_id in decision_data.get("terminal_branch_ids", []) if isinstance(branch_id, str)}
            if isinstance(decision_data, dict) and isinstance(decision_data.get("terminal_branch_ids"), list)
            else set()
        )
        if (
            (not local_defects and not decision_terminal_ids <= blocker_branch_ids)
            or status == "pass"
            or not blocker_branch_ids
        ):
            current_omitted.append(decision_rel)
    if current_omitted:
        defect(
            defects,
            "$.amendment_decisions",
            "omits discovered amendment decision artifacts: " + ", ".join(current_omitted),
        )


def validate_amendment_packet_coverage(
    defects: list[str],
    root: dict,
    *,
    amendments_dir: Path,
    discovered_packets: set[str],
    discovered_decisions: set[str],
    recorded_launch_ids: set[str],
    decision_branch_ids: set[str],
    blocker_branch_ids: set[str],
) -> None:
    for amendment_id in sorted(discovered_packets):
        if f"amendments/{amendment_id}.decision.json" not in discovered_decisions:
            defect(defects, "$.amendment_decisions", f"missing amender decision artifact for packet: {amendment_id}")
        if amendment_id not in recorded_launch_ids:
            defect(
                defects,
                "$.amendment_decisions",
                f"amender packet is not covered by a launch decision record: {amendment_id}",
            )
        validation_path = amendments_dir / f"{amendment_id}.packet" / "packet.validation.json"
        if not validation_path.exists():
            defect(defects, "$.amendment_decisions", f"missing amender packet validation artifact: {validation_path}")
    branch_statuses = root.get("branch_statuses")
    if isinstance(branch_statuses, list):
        terminal_branch_ids = {
            str(item.get("branch_id"))
            for item in branch_statuses
            if isinstance(item, dict) and item.get("status") in STATUSES and isinstance(item.get("branch_id"), str)
        }
        missing = sorted(terminal_branch_ids - decision_branch_ids - blocker_branch_ids)
        if missing:
            defect(
                defects,
                "$.amendment_decisions",
                "missing amendment launch/skip decisions for terminal branches: " + ", ".join(missing),
            )


def validate_amendment_decisions(defects: list[str], root: dict, *, manifest_path: Path, status: object) -> None:
    records = root.get("amendment_decisions")
    if not isinstance(records, list):
        defect(defects, "$.amendment_decisions", "must be an array")
        return
    blocker_branch_ids = validate_amendment_decision_blockers(defects, root, status=status)
    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_files = sorted(amendments_dir.glob("*.decision.json")) if amendments_dir.is_dir() else []
    packet_dirs = (
        sorted(path for path in amendments_dir.glob("*.packet") if path.is_dir()) if amendments_dir.is_dir() else []
    )
    discovered_decisions = {path.relative_to(bundle_dir).as_posix() for path in decision_files}
    discovered_packets = {path.name.removesuffix(".packet") for path in packet_dirs}
    acc = AmendmentDecisionAccumulators(
        recorded_decisions=set(),
        recorded_launch_ids=set(),
        decision_branch_ids=set(),
        seen_ids=set(),
    )

    if root.get("branch_statuses") and not records and not blocker_branch_ids:
        defect(defects, "$.amendment_decisions", "must record launch or skip decisions for terminal branch checkpoints")

    for index, item in enumerate(records):
        validate_amendment_decision_record(
            defects,
            item,
            f"$.amendment_decisions[{index}]",
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            acc=acc,
        )

    validate_amendment_omitted_decisions(
        defects,
        discovered_decisions,
        acc.recorded_decisions,
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        blocker_branch_ids=blocker_branch_ids,
        status=status,
    )
    validate_amendment_packet_coverage(
        defects,
        root,
        amendments_dir=amendments_dir,
        discovered_packets=discovered_packets,
        discovered_decisions=discovered_decisions,
        recorded_launch_ids=acc.recorded_launch_ids,
        decision_branch_ids=acc.decision_branch_ids,
        blocker_branch_ids=blocker_branch_ids,
    )


def load_branch_status_validator(defects: list[str]):
    global BRANCH_STATUS_VALIDATOR
    if BRANCH_STATUS_VALIDATOR is not None:
        return BRANCH_STATUS_VALIDATOR
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
    BRANCH_STATUS_VALIDATOR = module
    return module


def expected_branches_from_manifest(defects: list[str], manifest: object) -> dict[str, dict[str, Any]]:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    expected: dict[str, dict[str, Any]] = {}
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
        worktree_path = require_string(
            defects, branch_data.get("worktree_path"), f"manifest.branches[{index}].worktree_path"
        )
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
            defect(
                defects, f"manifest.branches[{index}].worktree_path", "must be a repo-relative path without traversal"
            )
        if branch_id and status_path and review_path:
            expected[branch_id] = {
                "status_path": status_path,
                "review_path": review_path,
                "branch_name": branch_name,
                "depends_on": branch_data.get("depends_on", []),
                "recovers_from": branch_data.get("recovers_from", []),
                "supersedes": branch_data.get("supersedes", []),
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
        allowed_manifest_sha256s=archived_manifest_sha256s(manifest_path),
        require_all_launched=status == "pass",
    )
    validate_scheduler_rollup(
        defects,
        root.get("branch_parallelism"),
        "$.branch_parallelism",
        expected_path=CONTRACT.MAIN_SCHEDULER_PATH,
        summary=summary,
        max_capacity=CONTRACT.MAX_ACTIVE_BRANCH_AGENTS,
    )
    finished_status = summary.get("finished_status") if isinstance(summary.get("finished_status"), dict) else {}
    branch_statuses = root.get("branch_statuses")
    if isinstance(branch_statuses, list):
        for index, item in enumerate(branch_statuses):
            if not isinstance(item, dict):
                continue
            branch_id = item.get("branch_id")
            if (
                isinstance(branch_id, str)
                and branch_id in finished_status
                and item.get("status") != finished_status[branch_id]
            ):
                defect(
                    defects, f"$.branch_statuses[{index}].status", "must match scheduler finish status for the branch"
                )
    serial_reasons = parallelization.get("serial_reasons")
    has_serial_reasons = isinstance(serial_reasons, list) and any(
        isinstance(item, str) and item.strip() for item in serial_reasons
    )
    ready_width = len([branch_id for branch_id, deps in dependencies.items() if not deps])
    observed = summary.get("max_observed_active")
    if (
        len(expected_ids) > 1
        and is_strict_int(observed)
        and observed < min(max_active, ready_width)
        and not has_serial_reasons
    ):
        defect(
            defects,
            "$.branch_parallelism.max_observed_active",
            "must justify observed branch parallelism below available ready width with manifest.parallelization.serial_reasons",
        )


def validate_manifest_branch_coverage(defects: list[str], root: dict, expected: dict[str, dict[str, Any]]) -> None:
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
    if root.get("status") == "pass":
        missing = sorted(set(expected) - set(seen))
        extra = sorted(set(seen) - set(expected))
        if missing:
            defect(defects, "$.branch_statuses", f"missing manifest branch summaries: {', '.join(missing)}")
        if extra:
            defect(
                defects, "$.branch_statuses", f"contains branch summaries not declared in manifest: {', '.join(extra)}"
            )


def validate_review_artifact(
    defects: list[str],
    data: object,
    expected_verdict: str,
    path: str,
    *,
    manifest: object,
    branch_id: str | None,
    manifest_path: Path | None = None,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None = None,
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
    if "finding_classes" in review:
        finding_classes = require_string_list(defects, review.get("finding_classes"), f"{path}.finding_classes")
        allowed_finding_classes = {"project_bug", "orchestration_bug", "verification_gap", "no_issue"}
        for index, item in enumerate(finding_classes):
            if item not in allowed_finding_classes:
                defect(defects, f"{path}.finding_classes[{index}]", f"must be one of {sorted(allowed_finding_classes)}")
    validate_base_range_diff_check(defects, review.get("commands_run"), f"{path}.commands_run", manifest)
    verification_gaps = require_string_list(defects, review.get("verification_gaps"), f"{path}.verification_gaps")
    if verdict == "mergeable" and verification_gaps:
        defect(defects, f"{path}.verification_gaps", "must be empty when verdict is mergeable")
    require_string_list(defects, review.get("residual_risks"), f"{path}.residual_risks")
    if manifest_path is not None:
        relative_hashes(
            defects,
            review.get("semantic_input_hashes"),
            f"{path}.semantic_input_hashes",
            root_dir=manifest_path.parent,
            allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
        )
    elif "semantic_input_hashes" in review and not isinstance(review.get("semantic_input_hashes"), dict):
        defect(defects, f"{path}.semantic_input_hashes", "must be an object")
    validate_reuse_policy(defects, review.get("reuse_policy"), f"{path}.reuse_policy")
    require_string(defects, review.get("summary"), f"{path}.summary")


def validate_branch_artifacts(
    defects: list[str],
    root: dict,
    expected: dict[str, dict[str, Any]],
    *,
    manifest: object,
    manifest_path: Path,
    require_artifacts: bool,
) -> None:
    branch_statuses = root.get("branch_statuses")
    if not isinstance(branch_statuses, list):
        return
    branch_validator = None
    allowed_hashes_by_rel_path = archived_manifest_hashes_by_rel_path(manifest_path)
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
                    allow_archived_manifest_hashes=True,
                )
                for branch_defect in branch_defects:
                    defect(defects, f"{item_path}.status_path", f"invalid branch status artifact: {branch_defect}")
            if isinstance(branch_status, dict):
                if branch_status.get("status") != item.get("status"):
                    defect(defects, f"{item_path}.status", "must match branch status artifact status")
                if branch_status.get("review_status") != item.get("review_status"):
                    defect(defects, f"{item_path}.review_status", "must match branch status artifact review_status")
                if item.get("review_waiver_path") is not None and branch_status.get("review_waiver_path") != item.get(
                    "review_waiver_path"
                ):
                    defect(
                        defects,
                        f"{item_path}.review_waiver_path",
                        "must match branch status artifact review_waiver_path",
                    )

        recovered = item.get("recovery_status") == "recovered"
        require_review_artifact = (require_artifacts and not recovered) or item.get("review_status") != "missing"
        if not review_artifact.exists():
            if require_review_artifact:
                defect(defects, f"{item_path}.review_path", f"artifact does not exist: {review_artifact}")
        elif item.get("review_status") == "missing" and item.get("status") != "pass":
            continue
        else:
            validate_review_artifact(
                defects,
                load_json_artifact(defects, review_artifact, f"{item_path}.review_path"),
                str(item.get("review_status", "")),
                f"{item_path}.review_path",
                manifest=manifest,
                branch_id=branch_id,
                manifest_path=manifest_path,
                allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
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
    if not isinstance(status, str) or status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    schema_status = root.get("schema_status")
    if not isinstance(schema_status, str) or schema_status not in {"assembled", "failed"}:
        defect(defects, "$.schema_status", "must be 'assembled' or 'failed'")
    runtime_status = root.get("runtime_status")
    if not isinstance(runtime_status, str) or runtime_status not in STATUSES:
        defect(defects, "$.runtime_status", f"must be one of {sorted(STATUSES)}")
    elif runtime_status != status:
        defect(defects, "$.runtime_status", "must match status")
    dod_status = root.get("dod_status")
    if not isinstance(dod_status, str) or dod_status not in {"pass", "incomplete"}:
        defect(defects, "$.dod_status", "must be 'pass' or 'incomplete'")
    elif status == "pass" and dod_status != "pass":
        defect(defects, "$.dod_status", "must be pass when main status is pass")
    elif isinstance(status, str) and status in {"partial", "blocked", "failed"} and dod_status == "pass":
        defect(defects, "$.dod_status", "must not be pass when main status is non-pass")
    review_status = root.get("review_status")
    if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
        defect(defects, "$.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    resume_action = root.get("resume_action")
    if not isinstance(resume_action, str) or resume_action not in {"reuse_terminal_status", "resume_or_repair"}:
        defect(defects, "$.resume_action", "must be reuse_terminal_status or resume_or_repair")
    audit_status = root.get("audit_status")
    if not isinstance(audit_status, str) or audit_status not in AUDIT_STATUSES:
        defect(defects, "$.audit_status", f"must be one of {sorted(AUDIT_STATUSES)}")
    if status == "pass" and audit_status != "pass":
        defect(defects, "$.audit_status", "must be pass when main status is pass")
    validate_audit_artifacts(defects, root, manifest_path=manifest_path, require_artifacts=status == "pass")
    branch_statuses = root.get("branch_statuses")
    min_branches = 1 if isinstance(status, str) and status in {"pass", "partial"} else 0
    if (
        not isinstance(branch_statuses, list)
        or len(branch_statuses) < min_branches
        or len(branch_statuses) > MAX_TOTAL_BRANCHES
    ):
        defect(defects, "$.branch_statuses", f"must contain {min_branches} to {MAX_TOTAL_BRANCHES} item(s)")
    else:
        for index, item in enumerate(branch_statuses):
            validate_branch_summary(defects, item, f"$.branch_statuses[{index}]")
        if status == "pass":
            by_id = {
                str(item["branch_id"]): item
                for item in branch_statuses
                if isinstance(item, dict) and isinstance(item.get("branch_id"), str)
            }
            for index, item in enumerate(branch_statuses):
                if isinstance(item, dict) and not branch_summary_recovered(item, by_id, expected_branches):
                    defect(
                        defects,
                        f"$.branch_statuses[{index}].status",
                        "must be pass or recovered by a manifest-declared passing mergeable branch when main status is pass",
                    )
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
    validate_amendment_decisions(defects, root, manifest_path=manifest_path, status=status)
    validate_lite_advice_entries(defects, root.get("lite_advice"), "$.lite_advice", manifest_path=manifest_path)
    cost_summary_path = require_string(defects, root.get("cost_summary_path"), "$.cost_summary_path")
    if cost_summary_path and cost_summary_path != "telemetry.summary.json#cost_summary":
        defect(defects, "$.cost_summary_path", "must be 'telemetry.summary.json#cost_summary'")
    validate_telemetry_summary(defects, manifest_path=manifest_path, require_artifacts=status == "pass")
    require_string_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    require_string_list(defects, root.get("dod_checklist"), "$.dod_checklist", min_items=1)
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    if status == "pass" and blockers:
        defect(defects, "$.blockers", "must be empty when status is pass")
    if isinstance(status, str) and status in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, "$.blockers", "must explain non-pass status")
    require_string(defects, root.get("summary"), "$.summary")
    return defects


def outcome_lanes(data: object, defects: list[str]) -> dict[str, bool]:
    root = data if isinstance(data, dict) else {}
    status = root.get("status")
    dod_status = root.get("dod_status")
    review_status = root.get("review_status")
    return {
        "artifact_valid": not defects,
        "runtime_success": status == "pass",
        "dod_complete": dod_status == "pass",
        "review_complete": review_status == "mergeable",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    # The validator is the fail-closed gate: a malformed status or manifest must produce a
    # structured `failed` result with defects, not an unhandled JSONDecodeError traceback.
    boot_defects: list[str] = []
    status_data = load_json_artifact(boot_defects, status_path, "$")
    manifest_data = load_json_artifact(boot_defects, manifest_path, "$.manifest")
    defects = boot_defects + validate_main_status(
        status_data,
        job_id=args.job_id,
        manifest=manifest_data,
        manifest_path=manifest_path,
    )
    result = {
        "status": "pass" if not defects else "failed",
        "status_path": status_path.as_posix(),
        "defects": defects,
        **outcome_lanes(status_data, defects),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']}")
        for item in defects:
            print(item)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
