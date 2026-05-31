#!/usr/bin/env python3
"""Validate a goal branch-orchestrator status artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
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
REVIEW_STATUSES = set(CONTRACT.REVIEW_STATUSES)
BRANCH_LITE_PURPOSES = {"branch-packet-planning", "context-pack", "worker-summary", "blocked-triage"}
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
DEFAULT_WORKER_LADDER = CONTRACT.DEFAULT_WORKER_LADDER
ALLOWED_WORKER_ROUTES = CONTRACT.ALLOWED_WORKER_ROUTES
WORKER_ROUTE_CLASSES = CONTRACT.WORKER_ROUTE_CLASSES
MANIFEST_WORKER_ROUTE_CLASSES = tuple(route_class for route_class in WORKER_ROUTE_CLASSES if route_class != "custom")
WORKER_ROUTE_CLASS_LADDERS = CONTRACT.WORKER_ROUTE_CLASS_LADDERS
WORK_ITEM_ROLES = set(CONTRACT.WORK_ITEM_ROLES)
RESEARCH_ALIASES = CONTRACT.RESEARCH_ALIASES
RESEARCH_FORBIDDEN_COMMAND_PATTERNS = [
    (r"\bgit\s+(push|commit|reset|checkout|clean|merge|rebase)\b", "git state mutation"),
    (r"\b(curl|http|https)\b.*\s-x\s*(post|put|patch|delete)\b", "state-changing HTTP method"),
    (r"\bcurl\b.*(--request\s+(post|put|patch|delete)|--data\b|--data-raw\b|--form\b|\s-d\s)", "state-changing curl request"),
    (r"\bwget\b.*--post", "state-changing wget request"),
    (r"\bgh\s+(pr|issue)\s+(create|edit|comment|close|reopen|merge)\b", "state-changing GitHub command"),
    (r"\bgh\s+repo\s+(delete|archive|edit|rename|transfer)\b", "state-changing GitHub repo command"),
    (r"\bgh\s+release\s+(create|upload|delete|edit)\b", "state-changing GitHub release command"),
    (r"\bgh\s+api\b.*(--method|-x)\s*(post|put|patch|delete)\b", "state-changing GitHub API method"),
    (r"\b(pip|pip3|npm|pnpm|yarn|apt|apt-get|brew|cargo|go)\s+(install|add|update|upgrade|remove|uninstall|publish)\b", "package or system mutation"),
    (r"\bpython3?\s+-m\s+pip\s+(install|uninstall|download)\b", "Python package mutation or fetch"),
    (r"\b(rm|mv|cp|tee|touch|mkdir|rmdir|chmod|chown|truncate|dd)\b", "local filesystem mutation"),
    (r"\bsed\s+-i\b|\bperl\s+-p?i\b", "in-place file edit"),
    (r"(^|[^&])>\s*[^&\s]", "shell output redirection to file"),
    (r"(^|\s)(env|printenv|set)(\s|$)", "environment or secret inspection"),
]
RESEARCH_SECRET_MARKERS = [
    ".env",
    ".ssh/",
    "/.ssh",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".gnupg/",
    "/.gnupg",
    ".netrc",
    "/etc/shadow",
    "aws/credentials",
    "application_default_credentials",
]
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
validate_telemetry_artifact = STATUS_VALIDATION.validate_telemetry_artifact
validate_scheduler_artifact = STATUS_VALIDATION.validate_scheduler_artifact
validate_scheduler_rollup = STATUS_VALIDATION.validate_scheduler_rollup
validate_pre_review_gate_artifact = STATUS_VALIDATION.validate_pre_review_gate_artifact
relative_hashes = STATUS_VALIDATION.relative_hashes
validate_reuse_policy = STATUS_VALIDATION.validate_reuse_policy
archived_manifest_hashes_by_rel_path = STATUS_VALIDATION.archived_manifest_hashes_by_rel_path
archived_manifest_sha256s = STATUS_VALIDATION.archived_manifest_sha256s


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def validate_url_list(defects: list[str], value: object, path: str) -> None:
    for index, item in enumerate(require_string_list(defects, value, path)):
        if not (item.startswith("https://") or item.startswith("http://")):
            defect(defects, f"{path}[{index}]", "must be an http(s) source URL")


def validate_research_security(defects: list[str], commands: list[str], local_files: list[str], path: str) -> None:
    for index, command in enumerate(commands):
        normalized = " ".join(command.lower().split())
        for pattern, reason in RESEARCH_FORBIDDEN_COMMAND_PATTERNS:
            if re.search(pattern, normalized):
                defect(defects, f"{path}.commands_run[{index}]", f"research-worker command violates read-only security policy: {reason}")
                break
        for marker in RESEARCH_SECRET_MARKERS:
            if marker in normalized:
                defect(defects, f"{path}.commands_run[{index}]", f"research-worker command appears to inspect secret or credential material: {marker}")
                break
    for index, file_path in enumerate(local_files):
        normalized = file_path.lower()
        for marker in RESEARCH_SECRET_MARKERS:
            if marker in normalized:
                defect(defects, f"{path}.local_files_read[{index}]", f"research-worker local file appears to be secret or credential material: {marker}")
                break


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


def validate_worker_route_class(defects: list[str], value: object, path: str) -> str:
    route_class = require_string(defects, value, path)
    if route_class and route_class not in WORKER_ROUTE_CLASSES:
        defect(defects, path, f"must be one of {list(WORKER_ROUTE_CLASSES)}")
    return route_class


def validate_route_class_cost(defects: list[str], route_class: str, selected_ladder: list[str], path: str, reason: object) -> None:
    if route_class not in WORKER_ROUTE_CLASS_LADDERS or not selected_ladder:
        return
    allowed = list(WORKER_ROUTE_CLASS_LADDERS[route_class])
    if route_class in {"mechanical", "docs", "small-edit", "normal-code"}:
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            defect(defects, path, f"route_class {route_class!r} must not use premium/full route aliases: {', '.join(disallowed)}")
    if route_class == "complex-code":
        reason_text = reason if isinstance(reason, str) else ""
        markers = ("complex", "risk", "cross-module", "premium", "architecture", "validator", "scheduler")
        if not any(marker in reason_text.lower() for marker in markers):
            defect(defects, path, "complex-code route_class must include a concrete cost/risk justification in selection_reason")


def validate_worker_route_artifact(
    defects: list[str],
    route_value: object,
    path: str,
    *,
    worker: dict,
) -> None:
    route = require_object(defects, route_value, path)
    for key in ["packet_id", "role", "route_class", "selected_ladder", "selection_reason"]:
        if key not in route:
            defect(defects, path, f"missing key: {key}")
    if route.get("packet_id") != worker.get("packet_id"):
        defect(defects, f"{path}.packet_id", "must match worker packet_id")
    if route.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    route_class = validate_worker_route_class(defects, route.get("route_class"), f"{path}.route_class")
    selected_ladder = validate_worker_ladder(defects, route.get("selected_ladder"), f"{path}.selected_ladder")
    selection_reason = require_string(defects, route.get("selection_reason"), f"{path}.selection_reason")
    validate_route_class_cost(defects, route_class, selected_ladder, f"{path}.selected_ladder", selection_reason)
    if route.get("route_class") != worker.get("route_class"):
        defect(defects, f"{path}.route_class", "must match worker route_class")
    if route.get("selected_ladder") != worker.get("selected_ladder"):
        defect(defects, f"{path}.selected_ladder", "must match worker selected_ladder")
    if route.get("selection_reason") != worker.get("selection_reason"):
        defect(defects, f"{path}.selection_reason", "must match worker selection_reason")


def validate_reviewer_route_artifact(
    defects: list[str],
    route_value: object,
    path: str,
    *,
    packet_id: str,
    manifest: object,
) -> list[str]:
    route = require_object(defects, route_value, path)
    for key in ["schema_version", "packet_id", "role", "tier", "selected_ladder", "selection_reason", "policy_router"]:
        if key not in route:
            defect(defects, path, f"missing key: {key}")
    if route.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if route.get("packet_id") != packet_id:
        defect(defects, f"{path}.packet_id", "must match review packet_id")
    if route.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    tier = route.get("tier")
    if tier not in CONTRACT.REVIEW_ROUTE_TIERS:
        defect(defects, f"{path}.tier", f"must be one of {list(CONTRACT.REVIEW_ROUTE_TIERS)}")
    selected = require_string_list(defects, route.get("selected_ladder"), f"{path}.selected_ladder", min_items=1)
    manifest_root = require_object(defects, manifest, "manifest")
    policy = manifest_root.get("review_model_policy")
    if policy != CONTRACT.REVIEW_MODEL_POLICY:
        defect(defects, "manifest.review_model_policy", "must match shared deterministic review router policy")
    expected = CONTRACT.review_route_for_tier(str(tier)) if tier in CONTRACT.REVIEW_ROUTE_TIERS else []
    if selected and expected and selected != expected:
        defect(defects, f"{path}.selected_ladder", f"must match review_model_policy route for tier {tier!r}")
    selection_reason = require_string(defects, route.get("selection_reason"), f"{path}.selection_reason")
    if tier == "heavy":
        heavy_triggers = route.get("heavy_triggers")
        if not isinstance(heavy_triggers, list) or not any(isinstance(item, str) and item.strip() for item in heavy_triggers):
            defect(defects, f"{path}.heavy_triggers", "must explain heavy reviewer routing with at least one trigger")
        if selection_reason and "default deterministic review tier" in selection_reason.lower():
            defect(defects, f"{path}.selection_reason", "must not be the default reason when heavy reviewer routing is selected")
    if route.get("policy_router") != CONTRACT.REVIEW_MODEL_POLICY["router"]:
        defect(defects, f"{path}.policy_router", f"must be {CONTRACT.REVIEW_MODEL_POLICY['router']!r}")
    return selected


def validate_worker_status(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = CONTRACT.WORKER_ROLLUP_REQUIRED
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
    route_class = validate_worker_route_class(defects, data.get("route_class"), f"{path}.route_class")
    selected_ladder = validate_worker_ladder(defects, data.get("selected_ladder"), f"{path}.selected_ladder")
    selection_reason = require_string(defects, data.get("selection_reason"), f"{path}.selection_reason")
    validate_route_class_cost(defects, route_class, selected_ladder, f"{path}.selected_ladder", selection_reason)
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")


def validate_research_status(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = CONTRACT.RESEARCH_ROLLUP_REQUIRED
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("role") != "research-worker":
        defect(defects, f"{path}.role", "must be 'research-worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if status_path and not is_absolute_path(status_path):
        defect(defects, f"{path}.status_path", "must be an absolute path without traversal")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    require_string_list(defects, data.get("search_queries"), f"{path}.search_queries")
    validate_url_list(defects, data.get("source_urls"), f"{path}.source_urls")
    require_string_list(defects, data.get("tools_used"), f"{path}.tools_used")
    local_files = require_string_list(defects, data.get("local_files_read"), f"{path}.local_files_read")
    for index, item in enumerate(local_files):
        if not is_repo_relative_path(item):
            defect(defects, f"{path}.local_files_read[{index}]", "must be a repo-relative path without git porcelain status")
    commands = require_string_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_research_security(defects, commands, local_files, path)
    require_string_list(defects, data.get("findings"), f"{path}.findings", min_items=1)
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass":
        if blockers:
            defect(defects, f"{path}.blockers", "must be empty when status is pass")
        if not data.get("source_urls"):
            defect(defects, f"{path}.source_urls", "must record at least one source URL when status is pass")
        if not data.get("tools_used"):
            defect(defects, f"{path}.tools_used", "must record at least one tool family when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")


def validate_packet_status(defects: list[str], value: object, path: str) -> None:
    if isinstance(value, dict) and value.get("role") == "research-worker":
        validate_research_status(defects, value, path)
    else:
        validate_worker_status(defects, value, path)


def validate_worker_artifact(defects: list[str], value: object, path: str) -> dict:
    data = require_object(defects, value, path)
    required = CONTRACT.WORKER_STATUS_REQUIRED
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
    route_class = validate_worker_route_class(defects, data.get("route_class"), f"{path}.route_class")
    selected_ladder = validate_worker_ladder(defects, data.get("selected_ladder"), f"{path}.selected_ladder")
    selection_reason = require_string(defects, data.get("selection_reason"), f"{path}.selection_reason")
    validate_route_class_cost(defects, route_class, selected_ladder, f"{path}.selected_ladder", selection_reason)
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


def validate_research_artifact(defects: list[str], value: object, path: str) -> dict:
    data = require_object(defects, value, path)
    required = CONTRACT.RESEARCH_STATUS_REQUIRED
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("role") != "research-worker":
        defect(defects, f"{path}.role", "must be 'research-worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    require_string(defects, data.get("branch"), f"{path}.branch")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    require_string_list(defects, data.get("search_queries"), f"{path}.search_queries")
    validate_url_list(defects, data.get("source_urls"), f"{path}.source_urls")
    require_string_list(defects, data.get("tools_used"), f"{path}.tools_used")
    local_files = require_string_list(defects, data.get("local_files_read"), f"{path}.local_files_read")
    for index, item in enumerate(local_files):
        if not is_repo_relative_path(item):
            defect(defects, f"{path}.local_files_read[{index}]", "must be a repo-relative path without git porcelain status")
    commands = require_string_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_research_security(defects, commands, local_files, path)
    require_string_list(defects, data.get("findings"), f"{path}.findings", min_items=1)
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass":
        if blockers:
            defect(defects, f"{path}.blockers", "must be empty when status is pass")
        if not data.get("source_urls"):
            defect(defects, f"{path}.source_urls", "must record at least one source URL when status is pass")
        if not data.get("tools_used"):
            defect(defects, f"{path}.tools_used", "must record at least one tool family when status is pass")
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
    worker_compared_keys = [key for key in CONTRACT.WORKER_STATUS_REQUIRED if key not in {"role", "branch"}]
    research_compared_keys = [key for key in CONTRACT.RESEARCH_STATUS_REQUIRED if key != "branch"]
    branch_id = branch.get("id") if isinstance(branch, dict) else ""
    expected_route_classes = expected_worker_route_classes(defects, branch, branch_id) if isinstance(branch_id, str) and branch_id else {}
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        item_path = f"$.worker_statuses[{index}]"
        item_role = item.get("role") if isinstance(item.get("role"), str) else "worker"
        status_path_value = item.get("status_path")
        if not isinstance(status_path_value, str) or not status_path_value.strip() or not is_absolute_path(status_path_value):
            continue
        status_artifact = Path(status_path_value).resolve()
        packet_id = item.get("packet_id")
        if isinstance(packet_id, str) and packet_id.strip():
            expected_status_artifact = (
                (manifest_path.parent / "research" / packet_id / "research.json").resolve()
                if item_role == "research-worker"
                else (manifest_path.parent / "workers" / packet_id / "status.json").resolve()
            )
            if status_artifact != expected_status_artifact:
                defect(
                    defects,
                    f"{item_path}.status_path",
                    f"must be manifest-owned {item_role} status path: {expected_status_artifact}",
                )
        if not status_artifact.exists():
            if require_existing:
                defect(defects, f"{item_path}.status_path", f"artifact does not exist: {status_artifact}")
            continue
        if item_role == "research-worker":
            artifact = validate_research_artifact(
                defects,
                load_json_artifact(defects, status_artifact, f"{item_path}.status_path"),
                f"{item_path}.status_path",
            )
            compared_keys = research_compared_keys
        else:
            artifact = validate_worker_artifact(
                defects,
                load_json_artifact(defects, status_artifact, f"{item_path}.status_path"),
                f"{item_path}.status_path",
            )
            compared_keys = worker_compared_keys
        if isinstance(branch, str) and branch.strip() and artifact.get("branch") != branch:
            defect(defects, f"{item_path}.status_path.branch", "must match branch status branch")
        if item_role == "worker":
            expected_route_class = expected_route_classes.get(str(packet_id))
            if expected_route_class and artifact.get("route_class") != expected_route_class:
                defect(defects, f"{item_path}.route_class", "must match manifest work item route_class")
        for key in compared_keys:
            if artifact.get(key) != item.get(key):
                defect(defects, f"{item_path}.{key}", "must match packet status artifact")
        if item_role == "research-worker":
            telemetry = validate_telemetry_artifact(
                defects,
                status_artifact.parent / "telemetry.json",
                f"{item_path}.telemetry_path",
                packet_id=str(packet_id) if isinstance(packet_id, str) else None,
                role="research-worker",
                allowed_aliases=RESEARCH_ALIASES,
                require_called=True,
            )
            if artifact.get("status") == "pass" and telemetry.get("accepted_alias") not in RESEARCH_ALIASES:
                defect(defects, f"{item_path}.telemetry_path.accepted_alias", "must identify the accepted research route when research status is pass")
            continue
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
        telemetry = validate_telemetry_artifact(
            defects,
            status_artifact.parent / "telemetry.json",
            f"{item_path}.telemetry_path",
            packet_id=str(packet_id) if isinstance(packet_id, str) else None,
            role="worker",
            allowed_aliases=DEFAULT_WORKER_LADDER,
            require_called=True,
        )
        if telemetry.get("route_class") and telemetry.get("route_class") != artifact.get("route_class"):
            defect(defects, f"{item_path}.telemetry_path.route_class", "must match worker status route_class")
        telemetry_attempts = telemetry.get("attempts") if isinstance(telemetry.get("attempts"), list) else []
        telemetry_aliases = [
            attempt.get("alias")
            for attempt in telemetry_attempts
            if isinstance(attempt, dict) and isinstance(attempt.get("alias"), str)
        ]
        selected_ladder = artifact.get("selected_ladder") if isinstance(artifact.get("selected_ladder"), list) else []
        if telemetry_aliases and selected_ladder and telemetry_aliases != selected_ladder:
            defect(defects, f"{item_path}.telemetry_path.attempts", "declared telemetry attempts must match selected_ladder exactly")
        called_aliases = [
            attempt.get("alias")
            for attempt in telemetry_attempts
            if isinstance(attempt, dict) and attempt.get("called") is True and isinstance(attempt.get("alias"), str)
        ]
        if called_aliases and selected_ladder and called_aliases != selected_ladder[: len(called_aliases)]:
            defect(defects, f"{item_path}.telemetry_path.attempts", "called worker attempts must be a prefix of selected_ladder")
        if artifact.get("status") == "pass" and telemetry.get("accepted_alias") not in selected_ladder:
            defect(defects, f"{item_path}.telemetry_path.accepted_alias", "must identify the accepted worker route when worker status is pass")


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


def expected_worker_packet_roles(defects: list[str], branch_entry: dict, branch_id: str) -> dict[str, str]:
    roles = {}
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list):
        return roles
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        packet_id = item.get("packet_id")
        if not isinstance(item_id, str) or not isinstance(packet_id, str):
            continue
        expected_packet_id = f"{branch_id}-{item_id}"
        if packet_id != expected_packet_id:
            continue
        role = item.get("worker_type", "worker")
        if role == "research":
            role = "research-worker"
        if role not in WORK_ITEM_ROLES:
            defect(defects, f"manifest.branch.work_items[{index}].worker_type", f"must be one of {sorted(WORK_ITEM_ROLES)}")
            role = "worker"
        roles[packet_id] = role
    return roles


def expected_worker_route_classes(defects: list[str], branch_entry: dict, branch_id: str) -> dict[str, str]:
    route_classes = {}
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list):
        return route_classes
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        packet_id = item.get("packet_id")
        if not isinstance(item_id, str) or not isinstance(packet_id, str):
            continue
        expected_packet_id = f"{branch_id}-{item_id}"
        if packet_id != expected_packet_id:
            continue
        role = item.get("worker_type", "worker")
        if role == "research":
            role = "research-worker"
        route_class = item.get("route_class")
        if role == "research-worker":
            if route_class is not None:
                defect(defects, f"manifest.branch.work_items[{index}].route_class", "must be omitted for research-worker items")
            continue
        if not isinstance(route_class, str) or route_class not in MANIFEST_WORKER_ROUTE_CLASSES:
            defect(defects, f"manifest.branch.work_items[{index}].route_class", f"must be one of {list(MANIFEST_WORKER_ROUTE_CLASSES)}")
            route_class = CONTRACT.DEFAULT_WORKER_ROUTE_CLASS
        route_classes[packet_id] = route_class
    return route_classes


def expected_worker_dependencies(branch_entry: dict, branch_id: str) -> dict[str, list[str]]:
    dependencies: dict[str, list[str]] = {}
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list):
        return dependencies
    for item in work_items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        packet_id = item.get("packet_id")
        if not isinstance(item_id, str) or not isinstance(packet_id, str):
            continue
        if packet_id != f"{branch_id}-{item_id}":
            continue
        deps = item.get("depends_on", [])
        dependencies[packet_id] = [
            f"{branch_id}-{dep}"
            for dep in deps
            if isinstance(dep, str) and dep.strip()
        ] if isinstance(deps, list) else []
    return dependencies


def worktree_freshness_path(branch_id: str) -> str:
    return f"branches/{branch_id}.worktree_freshness.json"


def review_route_policy_path(branch_id: str) -> str:
    return f"branches/{branch_id}.review_route_policy.json"


def review_evidence_path(branch_id: str) -> str:
    return f"branches/{branch_id}.pre_review_evidence.json"


def required_pre_review_input_paths(branch_entry: dict, branch_id: str) -> list[str]:
    paths = [
        "job.manifest.json",
        str(branch_entry.get("prompt", "")),
        CONTRACT.worker_scheduler_path(branch_id),
        worktree_freshness_path(branch_id),
        review_route_policy_path(branch_id),
        review_evidence_path(branch_id),
    ]
    work_items = branch_entry.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            if not isinstance(item, dict):
                continue
            packet_id = item.get("packet_id")
            if not isinstance(packet_id, str) or not packet_id.strip():
                continue
            worker_type = item.get("worker_type", "worker")
            if worker_type == "research":
                worker_type = "research-worker"
            if worker_type == "research-worker":
                paths.append(f"research/{packet_id}/research.json")
                paths.append(f"research/{packet_id}/telemetry.json")
            else:
                paths.append(f"workers/{packet_id}/status.json")
                paths.append(f"workers/{packet_id}/route.json")
                paths.append(f"workers/{packet_id}/telemetry.json")
    return [path for path in paths if isinstance(path, str) and path.strip()]


def run_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def git_stdout(defects: list[str], command: list[str], *, cwd: Path, path: str, label: str) -> str:
    result = run_git(command, cwd=cwd)
    if result.returncode != 0:
        defect(defects, path, f"{label} failed ({result.returncode}): {result.stdout.strip()}")
        return ""
    return result.stdout


def git_paths(defects: list[str], command: list[str], *, cwd: Path, path: str, label: str) -> list[str]:
    stdout = git_stdout(defects, command, cwd=cwd, path=path, label=label)
    paths: list[str] = []
    for line in stdout.splitlines():
        rel_path = line.strip()
        if not rel_path:
            continue
        if not is_repo_relative_path(rel_path):
            defect(defects, path, f"{label} returned unsafe path: {rel_path!r}")
            continue
        if rel_path not in paths:
            paths.append(rel_path)
    return paths


def current_file_state(worktree: Path, rel_path: str) -> str:
    target = (worktree / rel_path).resolve()
    try:
        target.relative_to(worktree.resolve())
    except ValueError:
        return "outside-worktree"
    if target.is_symlink():
        return "symlink:" + sha256_text(os.readlink(target)).removeprefix("sha256:")
    if not target.exists():
        return "missing"
    if not target.is_file():
        return "non-file"
    return sha256_file(target)


def expected_worktree_freshness(
    defects: list[str],
    *,
    worktree: Path,
    base_ref: str,
    branch_id: str,
    branch_status: dict,
    path: str,
) -> dict:
    head = git_stdout(defects, ["git", "rev-parse", "HEAD"], cwd=worktree, path=path, label="git rev-parse HEAD").strip()
    merge_base = git_stdout(defects, ["git", "merge-base", base_ref, "HEAD"], cwd=worktree, path=path, label=f"git merge-base {base_ref} HEAD").strip()
    name_status = git_stdout(
        defects,
        ["git", "diff", "--name-status", "--find-renames", f"{base_ref}...HEAD"],
        cwd=worktree,
        path=path,
        label=f"git diff --name-status --find-renames {base_ref}...HEAD",
    )
    base_paths = git_paths(defects, ["git", "diff", "--name-only", f"{base_ref}...HEAD"], cwd=worktree, path=path, label=f"git diff --name-only {base_ref}...HEAD")
    unstaged_paths = git_paths(defects, ["git", "diff", "--name-only", "HEAD"], cwd=worktree, path=path, label="git diff --name-only HEAD")
    staged_paths = git_paths(defects, ["git", "diff", "--cached", "--name-only", "HEAD"], cwd=worktree, path=path, label="git diff --cached --name-only HEAD")
    untracked_paths = git_paths(defects, ["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree, path=path, label="git ls-files --others --exclude-standard")
    status_paths = [
        item
        for item in branch_status.get("changed_files", [])
        if isinstance(item, str) and item.strip() and is_repo_relative_path(item)
    ] if isinstance(branch_status.get("changed_files"), list) else []
    current_paths: list[str] = []
    for rel_path in [*status_paths, *base_paths, *unstaged_paths, *staged_paths, *untracked_paths]:
        if rel_path not in current_paths:
            current_paths.append(rel_path)
    return {
        "schema_version": 1,
        "branch_id": branch_id,
        "worktree": worktree.as_posix(),
        "base_ref": base_ref,
        "worktree_head": head.splitlines()[0] if head else "",
        "merge_base": merge_base.splitlines()[0] if merge_base else "",
        "diff_name_status_sha256": sha256_text(name_status),
        "base_range_changed_files": base_paths,
        "current_changed_files": current_paths,
        "current_file_hashes": {rel_path: current_file_state(worktree, rel_path) for rel_path in current_paths},
        "commands_run": [
            "git rev-parse HEAD",
            f"git merge-base {base_ref} HEAD",
            f"git diff --name-status --find-renames {base_ref}...HEAD",
            f"git diff --name-only {base_ref}...HEAD",
            "git diff --name-only HEAD",
            "git diff --cached --name-only HEAD",
            "git ls-files --others --exclude-standard",
        ],
    }


def validate_worktree_freshness_artifact(
    defects: list[str],
    gate: dict,
    path: str,
    *,
    manifest: object,
    manifest_path: Path,
    branch_id: str,
    branch_status: dict,
) -> None:
    rel_path = worktree_freshness_path(branch_id)
    semantic_hashes = gate.get("semantic_input_hashes") if isinstance(gate.get("semantic_input_hashes"), dict) else {}
    if rel_path not in semantic_hashes:
        defect(defects, f"{path}.semantic_input_hashes", f"must include worktree freshness artifact {rel_path}")
    artifact_path = manifest_path.parent / rel_path
    if not artifact_path.exists():
        defect(defects, path, f"worktree freshness artifact does not exist: {artifact_path}")
        return
    artifact = require_object(defects, load_json_artifact(defects, artifact_path, path), path)
    manifest_root = require_object(defects, manifest, "manifest")
    base_ref = require_string(defects, manifest_root.get("base_ref"), "manifest.base_ref")
    worktree_value = require_string(defects, branch_status.get("worktree"), "$.worktree")
    if not base_ref or not worktree_value:
        return
    expected = expected_worktree_freshness(
        defects,
        worktree=Path(worktree_value).resolve(),
        base_ref=base_ref,
        branch_id=branch_id,
        branch_status=branch_status,
        path=path,
    )
    for key in [
        "schema_version",
        "branch_id",
        "worktree",
        "base_ref",
        "worktree_head",
        "merge_base",
        "diff_name_status_sha256",
        "base_range_changed_files",
        "current_changed_files",
        "current_file_hashes",
    ]:
        if artifact.get(key) != expected.get(key):
            defect(defects, f"{path}.{key}", "must match the current branch worktree freshness snapshot")


def scheduler_refill_events(scheduler_path: Path) -> list[str]:
    if not scheduler_path.exists():
        return []
    data = load_json(scheduler_path)
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return []
    refill_events: list[str] = []
    for event in data["events"]:
        if not isinstance(event, dict) or event.get("event") != "refill":
            continue
        seq = event.get("seq")
        eligible = event.get("eligible_ids", [])
        suffix = ",".join(item for item in eligible if isinstance(item, str)) if isinstance(eligible, list) else ""
        refill_events.append(f"seq:{seq}:{suffix}" if isinstance(seq, int) else suffix)
    return refill_events


def validate_worker_scheduler(
    defects: list[str],
    root: dict,
    branch_entry: dict,
    *,
    manifest_path: Path,
    branch_id: str,
    status: object,
    allow_archived_manifest_hashes: bool = False,
) -> None:
    manifest_max_workers = branch_entry.get("max_active_worker_packets")
    max_workers = manifest_max_workers if is_strict_int(manifest_max_workers) else MAX_WORKER_PACKETS_PER_BRANCH
    expected_path = CONTRACT.worker_scheduler_path(branch_id)
    worker_parallelism = branch_entry.get("worker_parallelism")
    if isinstance(worker_parallelism, dict) and worker_parallelism.get("scheduler_path") != expected_path:
        defect(defects, "manifest.branch.worker_parallelism.scheduler_path", f"must be {expected_path!r}")
    expected_ids = expected_worker_packet_ids(defects, branch_entry, branch_id)
    dependencies = expected_worker_dependencies(branch_entry, branch_id)
    summary = validate_scheduler_artifact(
        defects,
        manifest_path.parent / expected_path,
        "$.worker_parallelism.scheduler_path",
        scheduler_kind="branch-worker-pool",
        expected_path=expected_path,
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=max_workers,
        manifest_path=manifest_path,
        allowed_manifest_sha256s=archived_manifest_sha256s(manifest_path) if allow_archived_manifest_hashes else None,
        require_all_launched=status == "pass",
    )
    validate_scheduler_rollup(
        defects,
        root.get("worker_parallelism"),
        "$.worker_parallelism",
        expected_path=expected_path,
        summary=summary,
        max_capacity=MAX_WORKER_PACKETS_PER_BRANCH,
    )
    runtime_parallelism = root.get("worker_parallelism")
    if isinstance(runtime_parallelism, dict):
        observed = runtime_parallelism.get("max_observed_active_worker_packets")
        if is_strict_int(observed) and observed != summary.get("max_observed_active"):
            defect(defects, "$.worker_parallelism.max_observed_active_worker_packets", "must match scheduler ledger reconstruction exactly")
        try:
            expected_refill_events = scheduler_refill_events(manifest_path.parent / expected_path)
        except Exception as exc:  # noqa: BLE001
            defect(defects, "$.worker_parallelism.refill_events", f"could not reconstruct scheduler refill events: {exc}")
            expected_refill_events = []
        actual_refill_events = runtime_parallelism.get("refill_events")
        if isinstance(actual_refill_events, list) and all(isinstance(item, str) for item in actual_refill_events):
            if actual_refill_events != expected_refill_events:
                defect(defects, "$.worker_parallelism.refill_events", "must match scheduler ledger refill events exactly")
    finished_status = summary.get("finished_status") if isinstance(summary.get("finished_status"), dict) else {}
    worker_statuses = root.get("worker_statuses")
    if isinstance(worker_statuses, list):
        for index, item in enumerate(worker_statuses):
            if not isinstance(item, dict):
                continue
            packet_id = item.get("packet_id")
            if isinstance(packet_id, str) and packet_id in finished_status and item.get("status") != finished_status[packet_id]:
                defect(defects, f"$.worker_statuses[{index}].status", "must match scheduler finish status for the packet")


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
    expected_scheduler_path = CONTRACT.worker_scheduler_path(branch_id)
    branch_worker_parallelism = branch_entry.get("worker_parallelism")
    if isinstance(branch_worker_parallelism, dict) and branch_worker_parallelism.get("scheduler_path") != expected_scheduler_path:
        defect(defects, "manifest.branch.worker_parallelism.scheduler_path", f"must be {expected_scheduler_path!r}")
    pre_review_gate_path = branch_entry.get("pre_review_gate_path")
    expected_gate_path = CONTRACT.pre_review_gate_path(branch_id)
    if pre_review_gate_path != expected_gate_path:
        defect(defects, "manifest.branch.pre_review_gate_path", f"must be {expected_gate_path!r}")

    expected_ids = set(expected_worker_packet_ids(defects, branch_entry, branch_id))
    expected_roles = expected_worker_packet_roles(defects, branch_entry, branch_id)
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
        expected_role = expected_roles.get(packet_id, "worker")
        actual_role = item.get("role") if isinstance(item.get("role"), str) else "worker"
        if actual_role != expected_role:
            defect(defects, f"$.worker_statuses[{index}].role", f"must be {expected_role!r} for manifest work item")
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
        "scheduler_path",
        "max_worker_packets_per_branch",
        "max_active_worker_packets",
        "max_observed_active_worker_packets",
        "max_observed_active",
        "concurrent_launch_default",
        "rolling_refill_default",
        "scheduling_mode",
        "launched_ids",
        "finished_ids",
        "active_ids",
        "blocked_ids",
        "deferred_ids",
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
    require_string(defects, data.get("scheduler_path"), f"{path}.scheduler_path")
    for key in ["launched_ids", "finished_ids", "active_ids", "blocked_ids", "deferred_ids"]:
        require_string_list(defects, data.get(key), f"{path}.{key}")
    max_observed_active = data.get("max_observed_active")
    if not is_strict_int(max_observed_active) or max_observed_active < 0 or max_observed_active > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_observed_active", "must be an integer from 0 to 4")
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
    manifest_path: Path | None = None,
    expected_semantic_hashes: dict[str, str] | None = None,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None = None,
) -> None:
    data = require_object(defects, value, path)
    required = CONTRACT.REVIEW_REQUIRED
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
    if manifest_path is not None:
        semantic_hashes = relative_hashes(
            defects,
            data.get("semantic_input_hashes"),
            f"{path}.semantic_input_hashes",
            root_dir=manifest_path.parent,
            allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
        )
        if expected_semantic_hashes is not None and semantic_hashes != expected_semantic_hashes:
            defect(defects, f"{path}.semantic_input_hashes", "must match pre_review_gate.json semantic_input_hashes exactly")
    elif "semantic_input_hashes" in data and not isinstance(data.get("semantic_input_hashes"), dict):
        defect(defects, f"{path}.semantic_input_hashes", "must be an object")
    validate_reuse_policy(defects, data.get("reuse_policy"), f"{path}.reuse_policy")
    require_string(defects, data.get("summary"), f"{path}.summary")


def resolve_reuse_source_path(bundle_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    if not STATUS_VALIDATION.is_repo_relative_path(value):
        return None
    return (bundle_root / value).resolve()


def validate_review_reuse_sources(
    defects: list[str],
    review_data: object,
    path: str,
    *,
    manifest: object,
    manifest_path: Path,
    expected_semantic_hashes: dict[str, str] | None,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None = None,
) -> bool:
    if not isinstance(review_data, dict):
        return False
    reuse_policy = review_data.get("reuse_policy")
    if not isinstance(reuse_policy, dict) or reuse_policy.get("accepted") is not True:
        return False
    source_review = resolve_reuse_source_path(manifest_path.parent, reuse_policy.get("source_review_path"))
    source_telemetry = resolve_reuse_source_path(manifest_path.parent, reuse_policy.get("source_telemetry_path"))
    if source_review is None:
        defect(defects, f"{path}.reuse_policy.source_review_path", "must be an absolute path or safe bundle-relative path")
        return True
    if source_telemetry is None:
        defect(defects, f"{path}.reuse_policy.source_telemetry_path", "must be an absolute path or safe bundle-relative path")
        return True
    if not source_review.exists():
        defect(defects, f"{path}.reuse_policy.source_review_path", f"source review does not exist: {source_review}")
        return True
    if not source_telemetry.exists():
        defect(defects, f"{path}.reuse_policy.source_telemetry_path", f"source telemetry does not exist: {source_telemetry}")
        return True
    source_data = load_json_artifact(defects, source_review, f"{path}.reuse_policy.source_review_path")
    if isinstance(source_data, dict):
        source_hashes = relative_hashes(
            defects,
            source_data.get("semantic_input_hashes"),
            f"{path}.reuse_policy.source_review_path.semantic_input_hashes",
            root_dir=manifest_path.parent,
            allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
        )
        if expected_semantic_hashes is not None and source_hashes != expected_semantic_hashes:
            defect(defects, f"{path}.reuse_policy.source_review_path.semantic_input_hashes", "must match current semantic input hashes for accepted reuse")
        source_verdict = source_data.get("verdict")
        if source_verdict not in REVIEW_STATUSES - {"missing"}:
            defect(defects, f"{path}.reuse_policy.source_review_path.verdict", "must be a valid reviewer verdict")
    validate_telemetry_artifact(
        defects,
        source_telemetry,
        f"{path}.reuse_policy.source_telemetry_path",
        role="reviewer",
        allowed_aliases=("gpt-5.4-mini", "gpt-5.4", "gpt-5.5"),
        require_called=True,
    )
    return True


def validate_review_artifact_for_branch(
    defects: list[str],
    branch_entry: dict,
    review_status: object,
    branch_status: object,
    *,
    branch_status_root: dict,
    manifest: object,
    manifest_path: Path,
    allow_archived_manifest_hashes: bool = False,
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
    review_data = load_json_artifact(defects, review_artifact, "$.review_status")
    packet_id = review_data.get("packet_id") if isinstance(review_data, dict) else None
    branch_id = branch_entry.get("id") if isinstance(branch_entry.get("id"), str) else None
    expected_semantic_hashes = None
    allowed_hashes_by_rel_path = archived_manifest_hashes_by_rel_path(manifest_path) if allow_archived_manifest_hashes else None
    if branch_id:
        gate_path_value = branch_entry.get("pre_review_gate_path")
        expected_gate_path = CONTRACT.pre_review_gate_path(branch_id)
        if gate_path_value != expected_gate_path:
            defect(defects, "$.review_status.pre_review_gate_path", f"must be {expected_gate_path!r}")
        gate = validate_pre_review_gate_artifact(
            defects,
            manifest_path.parent / expected_gate_path,
            "$.review_status.pre_review_gate",
            manifest_path=manifest_path,
            branch_id=branch_id,
            review_packet_id=packet_id if isinstance(packet_id, str) else None,
            required_input_paths=required_pre_review_input_paths(branch_entry, branch_id),
            allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
        )
        validate_worktree_freshness_artifact(
            defects,
            gate,
            "$.review_status.pre_review_gate.worktree_freshness",
            manifest=manifest,
            manifest_path=manifest_path,
            branch_id=branch_id,
            branch_status=branch_status_root,
        )
        if isinstance(gate.get("semantic_input_hashes"), dict):
            expected_semantic_hashes = {
                key: value
                for key, value in gate["semantic_input_hashes"].items()
                if isinstance(key, str) and isinstance(value, str)
            }
    validate_review_artifact(
        defects,
        review_data,
        str(review_status),
        "$.review_status",
        manifest=manifest,
        branch_id=branch_id,
        manifest_path=manifest_path,
        expected_semantic_hashes=expected_semantic_hashes,
        allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
    )
    review_packet_dir = (
        manifest_path.parent / "reviewers" / packet_id
        if isinstance(packet_id, str) and packet_id.strip()
        else review_artifact.parent
    )
    reuse_accepted = validate_review_reuse_sources(
        defects,
        review_data,
        "$.review_status",
        manifest=manifest,
        manifest_path=manifest_path,
        expected_semantic_hashes=expected_semantic_hashes,
        allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
    )
    route_path = review_packet_dir / "route.json"
    route_aliases: list[str] = []
    if not route_path.exists():
        defect(defects, "$.review_status.route_path", f"route artifact does not exist: {route_path}")
    else:
        route_aliases = validate_reviewer_route_artifact(
            defects,
            load_json_artifact(defects, route_path, "$.review_status.route_path"),
            "$.review_status.route_path",
            packet_id=packet_id if isinstance(packet_id, str) else "",
            manifest=manifest,
        )
    telemetry_path = review_packet_dir / "telemetry.json"
    if reuse_accepted:
        if telemetry_path.exists():
            telemetry = validate_telemetry_artifact(
                defects,
                telemetry_path,
                "$.review_status.telemetry_path",
                packet_id=packet_id if isinstance(packet_id, str) else None,
                role="reviewer",
                allowed_aliases=route_aliases or ("gpt-5.4-mini", "gpt-5.4", "gpt-5.5"),
                require_called=False,
            )
            called = [
                attempt for attempt in telemetry.get("attempts", [])
                if isinstance(attempt, dict) and attempt.get("called") is True
            ] if isinstance(telemetry.get("attempts"), list) else []
            if called:
                defect(defects, "$.review_status.telemetry_path.attempts", "accepted reviewer reuse must not record a fresh called model attempt")
        return
    telemetry = validate_telemetry_artifact(
        defects,
        telemetry_path,
        "$.review_status.telemetry_path",
        packet_id=packet_id if isinstance(packet_id, str) else None,
        role="reviewer",
        allowed_aliases=route_aliases or ("gpt-5.4-mini", "gpt-5.4", "gpt-5.5"),
        require_called=True,
    )
    telemetry_attempts = telemetry.get("attempts") if isinstance(telemetry.get("attempts"), list) else []
    telemetry_aliases = [
        attempt.get("alias")
        for attempt in telemetry_attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("alias"), str)
    ]
    if route_aliases and telemetry_aliases and telemetry_aliases != route_aliases:
        defect(defects, "$.review_status.telemetry_path.attempts", "declared reviewer telemetry attempts must match route.json selected_ladder exactly")
    called_aliases = [
        attempt.get("alias")
        for attempt in telemetry_attempts
        if isinstance(attempt, dict) and attempt.get("called") is True and isinstance(attempt.get("alias"), str)
    ]
    if route_aliases and called_aliases and called_aliases != route_aliases[: len(called_aliases)]:
        defect(defects, "$.review_status.telemetry_path.attempts", "called reviewer attempts must be a prefix of route.json selected_ladder")


def validate_branch_status(
    data: object,
    *,
    branch_id: str | None,
    branch: str | None,
    worktree: str | None,
    manifest: object,
    manifest_path: Path,
    status_path: Path,
    allow_archived_manifest_hashes: bool = False,
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
            validate_packet_status(defects, item, f"$.worker_statuses[{index}]")
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
        require_all_workers=status == "pass",
    )
    if branch_entry and root_branch_id:
        validate_worker_scheduler(
            defects,
            root,
            branch_entry,
            manifest_path=manifest_path,
            branch_id=root_branch_id,
            status=status,
            allow_archived_manifest_hashes=allow_archived_manifest_hashes,
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
            branch_status_root=root,
            manifest=manifest,
            manifest_path=manifest_path,
            allow_archived_manifest_hashes=allow_archived_manifest_hashes,
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
    parser.add_argument("--allow-archived-manifest-hashes", action="store_true")
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
        allow_archived_manifest_hashes=args.allow_archived_manifest_hashes,
    )
    result = {"status": "pass" if not defects else "failed", "status_path": status_path.as_posix(), "defects": defects}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']}")
        for item in defects:
            print(item)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
