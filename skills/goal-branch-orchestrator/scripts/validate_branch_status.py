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
ROUTE_POLICY_VERSION = "goal-route-policy-v2"
RESEARCH_ALIASES = CONTRACT.RESEARCH_ALIASES
# Reviewer routes are bridge-led with native gpt fallback (REVIEW_MODEL_ROUTES);
# include every alias that can legitimately appear in a reviewer telemetry
# artifact (bridge deepseek + native gpt, plus legacy gpt-5.4-mini).
REVIEWER_ALLOWED_ALIASES = tuple(
    dict.fromkeys(
        [alias for route in CONTRACT.REVIEW_MODEL_ROUTES.values() for alias in route]
        + ["gpt-5.4-mini", "gpt-5.4", "gpt-5.5"]
    )
)
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RESEARCH_FORBIDDEN_COMMAND_PATTERNS = [
    (r"\bgit\s+(push|commit|reset|checkout|clean|merge|rebase)\b", "git state mutation"),
    (r"\b(curl|http|https)\b.*\s-x\s*(post|put|patch|delete)\b", "state-changing HTTP method"),
    (
        r"\bcurl\b.*(--request\s+(post|put|patch|delete)|--data\b|--data-raw\b|--form\b|\s-d\s)",
        "state-changing curl request",
    ),
    (r"\bwget\b.*--post", "state-changing wget request"),
    (r"\bgh\s+(pr|issue)\s+(create|edit|comment|close|reopen|merge)\b", "state-changing GitHub command"),
    (r"\bgh\s+repo\s+(delete|archive|edit|rename|transfer)\b", "state-changing GitHub repo command"),
    (r"\bgh\s+release\s+(create|upload|delete|edit)\b", "state-changing GitHub release command"),
    (r"\bgh\s+api\b.*(--method|-x)\s*(post|put|patch|delete)\b", "state-changing GitHub API method"),
    (
        r"\b(pip|pip3|npm|pnpm|yarn|apt|apt-get|brew|cargo|go)\s+(install|add|update|upgrade|remove|uninstall|publish)\b",
        "package or system mutation",
    ),
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
WORKER_FORBIDDEN_COMMAND_PATTERNS = [
    (r"\bgit\s+(add|commit|push|reset|checkout|clean|merge|rebase|stash)\b", "git state mutation"),
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


def goal_config_from_manifest(manifest: object, manifest_path: Path | None) -> dict | None:
    if isinstance(manifest, dict) and isinstance(manifest.get("goal_config"), dict):
        return manifest["goal_config"]
    if isinstance(manifest, dict) and manifest_path is not None:
        config_path = manifest.get("goal_config_path")
        if isinstance(config_path, str) and config_path.strip() and is_repo_relative_path(config_path):
            candidate = (manifest_path.parent / config_path).resolve()
            try:
                candidate.relative_to(manifest_path.parent.resolve())
            except ValueError:
                return None
            if candidate.is_file():
                data = load_json(candidate)
                if isinstance(data, dict):
                    return data
    return None


def expected_review_model_policy(defects: list[str], manifest: object, manifest_path: Path | None) -> dict:
    manifest_root = require_object(defects, manifest, "manifest")
    policy = manifest_root.get("review_model_policy")
    goal_config = goal_config_from_manifest(manifest_root, manifest_path)
    if goal_config is not None:
        model_policies = (
            goal_config.get("model_policies") if isinstance(goal_config.get("model_policies"), dict) else {}
        )
        expected = model_policies.get("review_model_policy")
        if isinstance(expected, dict):
            if policy != expected:
                defect(
                    defects, "manifest.review_model_policy", "must match goal_config.model_policies.review_model_policy"
                )
            return expected
    if policy != CONTRACT.REVIEW_MODEL_POLICY:
        defect(defects, "manifest.review_model_policy", "must match shared deterministic review router policy")
    return CONTRACT.REVIEW_MODEL_POLICY


def validate_reviewer_policy_tiers(defects: list[str], policy: dict, path: str, manifest: object) -> None:
    routes = policy.get("routes")
    if not isinstance(routes, dict):
        defect(defects, f"{path}.routes", "must be an object")
        return
    normalized: dict[str, tuple[str, ...]] = {}
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        value = routes.get(tier)
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            defect(defects, f"{path}.routes.{tier}", "must be a non-empty string array")
            continue
        normalized[tier] = tuple(value)
    if set(normalized) != set(CONTRACT.REVIEW_ROUTE_TIERS):
        return
    model_roles: list[str] = []
    manifest_root = manifest if isinstance(manifest, dict) else {}
    models = manifest_root.get("models")
    if isinstance(models, dict):
        model_roles = [key for key in models if isinstance(key, str)]
    for policy_key, route_key in (
        ("worker_model_policy", "allowed_routes"),
        ("lite_model_policy", "default_ladder"),
        ("amender_model_policy", "allowed_routes"),
    ):
        policy_value = manifest_root.get(policy_key)
        if not isinstance(policy_value, dict):
            continue
        values = policy_value.get(route_key)
        if isinstance(values, list):
            model_roles.extend(item for item in values if isinstance(item, str) and item.strip())
    for route in normalized.values():
        model_roles.extend(route)
    distinct_roles = set(model_roles)
    if len(distinct_roles) > 1 and normalized["light"] == normalized["standard"] == normalized["heavy"]:
        defect(
            defects,
            f"{path}.routes",
            "light, standard, and heavy reviewer routes must not all be identical when multiple model roles are configured",
        )
    heavy_markers = ("demanding", "heavy", "premium", "pro", "gpt-5.5")
    cheap_available = any(not any(marker in alias.lower() for marker in heavy_markers) for alias in distinct_roles)
    if len(distinct_roles) > 1 and cheap_available and normalized["light"]:
        light = normalized["light"]
        if all(any(marker in alias.lower() for marker in heavy_markers) for alias in light):
            defect(
                defects,
                f"{path}.routes.light",
                "must use a cheaper reviewer route when a non-heavy configured model role exists",
            )


def path_is_owned(path: str, owned_paths: list[str]) -> bool:
    return any(path == owned or path.startswith(f"{owned.rstrip('/')}/") for owned in owned_paths)


def is_runtime_cache_path(path: str) -> bool:
    parts = [part for part in Path(path).parts if part]
    if any(part in {"__pycache__", ".pytest_cache", ".ruff_cache"} for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if path.endswith((".pyc", ".pyo", ".egg-info")):
        return True
    return path == ".runtime-cache" or path.startswith(".runtime-cache/")


def work_items_by_packet(branch: object) -> dict[str, dict]:
    if not isinstance(branch, dict):
        return {}
    result: dict[str, dict] = {}
    for item in branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("packet_id"), str):
            result[item["packet_id"]] = item
    return result


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


def validate_worker_command_evidence(defects: list[str], commands: object, path: str) -> None:
    values = require_string_list(defects, commands, path, min_items=1)
    for index, command in enumerate(values):
        normalized = " ".join(command.lower().split())
        for pattern, reason in WORKER_FORBIDDEN_COMMAND_PATTERNS:
            if re.search(pattern, normalized):
                defect(defects, f"{path}[{index}]", f"must not list mutating command evidence: {reason}")


def validate_research_security(defects: list[str], commands: list[str], local_files: list[str], path: str) -> None:
    for index, command in enumerate(commands):
        normalized = " ".join(command.lower().split())
        for pattern, reason in RESEARCH_FORBIDDEN_COMMAND_PATTERNS:
            if re.search(pattern, normalized):
                defect(
                    defects,
                    f"{path}.commands_run[{index}]",
                    f"research-worker command violates read-only security policy: {reason}",
                )
                break
        for marker in RESEARCH_SECRET_MARKERS:
            if marker in normalized:
                defect(
                    defects,
                    f"{path}.commands_run[{index}]",
                    f"research-worker command appears to inspect secret or credential material: {marker}",
                )
                break
    for index, file_path in enumerate(local_files):
        normalized = file_path.lower()
        for marker in RESEARCH_SECRET_MARKERS:
            if marker in normalized:
                defect(
                    defects,
                    f"{path}.local_files_read[{index}]",
                    f"research-worker local file appears to be secret or credential material: {marker}",
                )
                break


def validate_worker_ladder(
    defects: list[str],
    value: object,
    path: str,
    *,
    allowed_routes: list[str] | tuple[str, ...] | set[str] | frozenset[str] | None = None,
    default_ladder: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    if not isinstance(value, list) or not value:
        defect(defects, path, "must be a non-empty array")
        return []
    allowed = list(allowed_routes) if allowed_routes is not None else []
    order = list(default_ladder) if default_ladder is not None else []
    aliases = []
    positions = []
    seen = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            defect(defects, item_path, "must be a non-empty string")
            continue
        if allowed and item not in allowed:
            defect(defects, item_path, f"must be one of {sorted(allowed)}")
            continue
        if item in seen:
            defect(defects, item_path, "must not repeat a route alias")
            continue
        seen.add(item)
        aliases.append(item)
        if order:
            fallback = len(order) + (allowed.index(item) if item in allowed else len(allowed))
            positions.append(order.index(item) if item in order else fallback)
    if positions != sorted(positions):
        defect(defects, path, "must preserve standard ladder order")
    return aliases


def validate_worker_route_class(defects: list[str], value: object, path: str) -> str:
    route_class = require_string(defects, value, path)
    if route_class and route_class not in WORKER_ROUTE_CLASSES:
        defect(defects, path, f"must be one of {list(WORKER_ROUTE_CLASSES)}")
    return route_class


def validate_route_class_cost(
    defects: list[str], route_class: str, selected_ladder: list[str], path: str, reason: object
) -> None:
    if route_class not in WORKER_ROUTE_CLASS_LADDERS or not selected_ladder:
        return
    if any(alias not in ALLOWED_WORKER_ROUTES for alias in selected_ladder):
        return
    allowed = list(WORKER_ROUTE_CLASS_LADDERS[route_class])
    if route_class in {"mechanical", "docs", "small-edit", "normal-code"}:
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            defect(
                defects,
                path,
                f"route_class {route_class!r} must not use premium/full route aliases: {', '.join(disallowed)}",
            )
    if route_class == "complex-code":
        reason_text = reason if isinstance(reason, str) else ""
        markers = ("complex", "risk", "cross-module", "premium", "architecture", "validator", "scheduler")
        if not any(marker in reason_text.lower() for marker in markers):
            defect(
                defects,
                path,
                "complex-code route_class must include a concrete cost/risk justification in selection_reason",
            )


def is_route_health_skipped_attempt(attempt: object) -> bool:
    if not isinstance(attempt, dict) or attempt.get("called") is not False:
        return False
    route_health = attempt.get("route_health") if isinstance(attempt.get("route_health"), dict) else {}
    status_parse = attempt.get("status_parse") if isinstance(attempt.get("status_parse"), dict) else {}
    if route_health.get("degraded") is True:
        return True
    provider_error = status_parse.get("provider_error_code") or attempt.get("provider_error_code")
    if isinstance(provider_error, str) and provider_error.strip().upper() == "ROUTE_HEALTH_DEGRADED":
        return True
    markers = [
        attempt.get("failure_class"),
        attempt.get("failure_subclass"),
        status_parse.get("failure_class"),
        status_parse.get("failure_subclass"),
    ]
    return any(isinstance(marker, str) and marker.strip().lower() == "route_degraded" for marker in markers)


def effective_ladder_for_called_attempts(selected_ladder: list[str], telemetry_attempts: list[object]) -> list[str]:
    skipped_aliases = {
        attempt.get("alias")
        for attempt in telemetry_attempts
        if is_route_health_skipped_attempt(attempt) and isinstance(attempt.get("alias"), str)
    }
    if not skipped_aliases:
        return selected_ladder
    return [alias for alias in selected_ladder if alias not in skipped_aliases]


def validate_worker_route_artifact(
    defects: list[str],
    route_value: object,
    path: str,
    *,
    worker: dict,
) -> None:
    route = require_object(defects, route_value, path)
    for key in [
        "schema_version",
        "packet_id",
        "role",
        "route_class",
        "selected_ladder",
        "selection_reason",
        "policy_router",
        "policy_version",
        "route_policy_version",
    ]:
        if key not in route:
            defect(defects, path, f"missing key: {key}")
    if route.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if route.get("packet_id") != worker.get("packet_id"):
        defect(defects, f"{path}.packet_id", "must match worker packet_id")
    if route.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    require_string(defects, route.get("policy_router"), f"{path}.policy_router")
    require_string(defects, route.get("policy_version"), f"{path}.policy_version")
    if route.get("route_policy_version") != ROUTE_POLICY_VERSION:
        defect(defects, f"{path}.route_policy_version", f"must be {ROUTE_POLICY_VERSION!r}")
    route_class = validate_worker_route_class(defects, route.get("route_class"), f"{path}.route_class")
    route_allowed = route.get("allowed_aliases")
    route_default = route.get("default_ladder")
    selected_ladder = validate_worker_ladder(
        defects,
        route.get("selected_ladder"),
        f"{path}.selected_ladder",
        allowed_routes=route_allowed
        if isinstance(route_allowed, list) and route_allowed
        else list(ALLOWED_WORKER_ROUTES),
        default_ladder=route_default
        if isinstance(route_default, list) and route_default
        else list(DEFAULT_WORKER_LADDER),
    )
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
    manifest_path: Path | None,
) -> list[str]:
    route = require_object(defects, route_value, path)
    for key in [
        "schema_version",
        "packet_id",
        "role",
        "tier",
        "selected_ladder",
        "selection_reason",
        "policy_router",
        "policy_version",
        "route_policy_version",
        "policy_routes",
    ]:
        if key not in route:
            defect(defects, path, f"missing key: {key}")
    if route.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if route.get("packet_id") != packet_id:
        defect(defects, f"{path}.packet_id", "must match review packet_id")
    if route.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    require_string(defects, route.get("policy_router"), f"{path}.policy_router")
    require_string(defects, route.get("policy_version"), f"{path}.policy_version")
    if route.get("route_policy_version") != ROUTE_POLICY_VERSION:
        defect(defects, f"{path}.route_policy_version", f"must be {ROUTE_POLICY_VERSION!r}")
    tier = route.get("tier")
    if tier not in CONTRACT.REVIEW_ROUTE_TIERS:
        defect(defects, f"{path}.tier", f"must be one of {list(CONTRACT.REVIEW_ROUTE_TIERS)}")
    selected = require_string_list(defects, route.get("selected_ladder"), f"{path}.selected_ladder", min_items=1)
    policy = expected_review_model_policy(defects, manifest, manifest_path)
    validate_reviewer_policy_tiers(defects, policy, "manifest.review_model_policy", manifest)
    routes = policy.get("routes") if isinstance(policy.get("routes"), dict) else {}
    expected = (
        routes.get(str(tier)) if tier in CONTRACT.REVIEW_ROUTE_TIERS and isinstance(routes.get(str(tier)), list) else []
    )
    if route.get("policy_routes") != routes:
        defect(defects, f"{path}.policy_routes", "must match manifest review_model_policy.routes")
    if selected and expected and selected != expected:
        defect(defects, f"{path}.selected_ladder", f"must match review_model_policy route for tier {tier!r}")
    selection_reason = require_string(defects, route.get("selection_reason"), f"{path}.selection_reason")
    if tier == "heavy":
        heavy_triggers = route.get("heavy_triggers")
        if not isinstance(heavy_triggers, list) or not any(
            isinstance(item, str) and item.strip() for item in heavy_triggers
        ):
            defect(defects, f"{path}.heavy_triggers", "must explain heavy reviewer routing with at least one trigger")
        if selection_reason and "default deterministic review tier" in selection_reason.lower():
            defect(
                defects,
                f"{path}.selection_reason",
                "must not be the default reason when heavy reviewer routing is selected",
            )
    expected_router = policy.get("router", CONTRACT.REVIEW_MODEL_POLICY["router"])
    if route.get("policy_router") != expected_router:
        defect(defects, f"{path}.policy_router", f"must be {expected_router!r}")
    return selected


def validate_launch_config_identity(
    defects: list[str], config: dict, packet_dir: Path, path: str, *, packet_id: str | None, role: str, output_name: str
) -> None:
    if config.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if packet_id and config.get("packet_id") != packet_id:
        defect(defects, f"{path}.packet_id", "must match packet artifact id")
    if config.get("role") != role:
        defect(defects, f"{path}.role", f"must be {role!r}")
    worktree = require_string(defects, config.get("worktree"), f"{path}.worktree")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    schema_name = require_string(defects, config.get("schema_name"), f"{path}.schema_name")
    if schema_name and schema_name != "research.schema.json" and not schema_name.endswith(".schema.json"):
        defect(defects, f"{path}.schema_name", "must name a packet-local schema JSON file")
    if schema_name and not (packet_dir / schema_name).exists():
        defect(defects, f"{path}.schema_name", f"schema file does not exist: {packet_dir / schema_name}")
    configured_output = require_string(defects, config.get("output_name"), f"{path}.output_name")
    if configured_output != output_name:
        defect(defects, f"{path}.output_name", f"must be {output_name!r}")
    if configured_output and not (packet_dir / configured_output).exists():
        defect(defects, f"{path}.output_name", f"output artifact does not exist: {packet_dir / configured_output}")
    require_string(defects, config.get("telemetry_script"), f"{path}.telemetry_script")


def validate_launch_config_debug_events(
    defects: list[str], config: dict, packet_dir: Path, path: str, *, packet_id: str | None
) -> None:
    debug_events_name = config.get("debug_events_name")
    if not (isinstance(debug_events_name, str) and debug_events_name.strip()):
        return
    debug_path = packet_dir / debug_events_name
    if not debug_path.exists():
        defect(defects, f"{path}.debug_events_name", f"debug events artifact does not exist: {debug_path}")
        return
    debug_events: list[dict] = []
    for line_index, line in enumerate(debug_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            defect(defects, f"{path}.debug_events_name[{line_index}]", f"must be JSONL object: {exc}")
            continue
        if isinstance(item, dict):
            debug_events.append(item)
    has_start = any(
        item.get("packet_id") == packet_id and item.get("phase") == "packet" and item.get("event") == "start"
        for item in debug_events
    )
    has_end = any(
        item.get("packet_id") == packet_id and item.get("phase") == "packet" and item.get("event") == "end"
        for item in debug_events
    )
    if not has_start:
        defect(defects, f"{path}.debug_events_name", "must include packet start event")
    if not has_end:
        defect(defects, f"{path}.debug_events_name", "must include packet end event")


def validate_launch_attempt_provider(defects: list[str], attempt: dict, attempt_path: str, *, alias: str) -> None:
    provider = require_string(defects, attempt.get("provider"), f"{attempt_path}.provider")
    model = require_string(defects, attempt.get("model"), f"{attempt_path}.model")
    if provider and provider not in {"codex", CONTRACT.BRIDGE_HARNESS_KIND}:
        defect(
            defects,
            f"{attempt_path}.provider",
            f"must be a supported route adapter (codex or {CONTRACT.BRIDGE_HARNESS_KIND}), got {provider!r}",
        )
    if provider == "codex" and alias in CONTRACT.CODEX_ROUTE_MODELS:
        expected_model = CONTRACT.codex_model(alias)
        if model != expected_model:
            defect(defects, f"{attempt_path}.model", f"must be {expected_model!r} for alias {alias!r}")
    if provider == CONTRACT.BRIDGE_HARNESS_KIND and CONTRACT.is_bridge_alias(alias):
        expected_model = CONTRACT.bridge_model(alias)
        if model != expected_model:
            defect(defects, f"{attempt_path}.model", f"must be {expected_model!r} for bridge alias {alias!r}")
        bridge = attempt.get("bridge")
        if not isinstance(bridge, dict):
            defect(defects, f"{attempt_path}.bridge", "must be an object for opencode-bridge attempts")
        else:
            if bridge.get("provider") != CONTRACT.BRIDGE_PROVIDER_ID:
                defect(defects, f"{attempt_path}.bridge.provider", f"must be {CONTRACT.BRIDGE_PROVIDER_ID!r}")
            for key in ("model", "variant", "permission_profile", "run_dir"):
                require_string(defects, bridge.get(key), f"{attempt_path}.bridge.{key}")


def validate_launch_attempt(defects: list[str], attempt: dict, attempt_path: str, *, role: str) -> None:
    require_string(defects, attempt.get("command"), f"{attempt_path}.command")
    rendered_command = require_string(defects, attempt.get("rendered_command"), f"{attempt_path}.rendered_command")
    if rendered_command and isinstance(attempt.get("command"), str) and rendered_command != attempt.get("command"):
        defect(defects, f"{attempt_path}.rendered_command", "must match command until runtime records executed_command")
    if attempt.get("route_policy_version") != ROUTE_POLICY_VERSION:
        defect(defects, f"{attempt_path}.route_policy_version", f"must be {ROUTE_POLICY_VERSION!r}")
    telemetry_capability = require_object(
        defects, attempt.get("telemetry_capability"), f"{attempt_path}.telemetry_capability"
    )
    if telemetry_capability:
        require_string(
            defects, telemetry_capability.get("token_usage"), f"{attempt_path}.telemetry_capability.token_usage"
        )
        require_string(defects, telemetry_capability.get("source"), f"{attempt_path}.telemetry_capability.source")
    timeout = attempt.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        defect(defects, f"{attempt_path}.timeout_seconds", "must be a positive integer")
    sandbox = attempt.get("sandbox")
    if role in {"worker", "reviewer"} and not isinstance(sandbox, str):
        defect(defects, f"{attempt_path}.sandbox", "must record the attempt sandbox")
    if role in {"worker", "reviewer"} and attempt.get("provider") == "codex":
        if attempt.get("ignore_user_config") is not True:
            defect(defects, f"{attempt_path}.ignore_user_config", "must be true for Codex packet isolation")
        if attempt.get("ignore_rules") is not True:
            defect(defects, f"{attempt_path}.ignore_rules", "must be true for Codex packet isolation")
    event_logs = attempt.get("event_logs")
    if not isinstance(event_logs, list) or not all(isinstance(item, str) and item.strip() for item in event_logs):
        defect(defects, f"{attempt_path}.event_logs", "must list packet-local event log paths")


def validate_launch_config_attempts(defects: list[str], config: dict, path: str, *, role: str) -> list[str]:
    attempts = config.get("attempts")
    aliases: list[str] = []
    if not isinstance(attempts, list) or not attempts:
        defect(defects, f"{path}.attempts", "must be a non-empty array")
        return aliases
    for index, raw_attempt in enumerate(attempts):
        attempt_path = f"{path}.attempts[{index}]"
        attempt = require_object(defects, raw_attempt, attempt_path)
        alias = require_string(defects, attempt.get("alias"), f"{attempt_path}.alias")
        if alias:
            aliases.append(alias)
        validate_launch_attempt_provider(defects, attempt, attempt_path, alias=alias)
        validate_launch_attempt(defects, attempt, attempt_path, role=role)
    selected_ladder = config.get("selected_ladder")
    if (
        isinstance(selected_ladder, list)
        and aliases
        and [item for item in selected_ladder if isinstance(item, str)] != aliases
    ):
        defect(defects, f"{path}.selected_ladder", "must match launch attempt aliases exactly")
    return aliases


def validate_launch_config_artifact(
    defects: list[str],
    packet_dir: Path,
    path: str,
    *,
    packet_id: str | None,
    role: str,
    output_name: str,
) -> list[str]:
    config_path = packet_dir / "launch-config.json"
    if not config_path.exists():
        defect(defects, path, f"launch config does not exist: {config_path}")
        return []
    config = require_object(defects, load_json_artifact(defects, config_path, path), path)
    validate_launch_config_identity(
        defects, config, packet_dir, path, packet_id=packet_id, role=role, output_name=output_name
    )
    validate_launch_config_debug_events(defects, config, packet_dir, path, packet_id=packet_id)
    return validate_launch_config_attempts(defects, config, path, role=role)


def validate_worker_payload(
    defects: list[str],
    value: object,
    path: str,
    *,
    required: tuple[str, ...],
    require_role: bool = False,
    require_branch: bool = False,
    require_status_path: bool = False,
) -> dict:
    data = require_object(defects, value, path)
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if require_role and data.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    if require_branch:
        require_string(defects, data.get("branch"), f"{path}.branch")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path") if require_status_path else ""
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    for key in [
        "branch_id",
        "work_item_id",
        "manifest_hash",
        "manifest_epoch",
        "worktree_path",
        "route_id",
        "evidence_summary",
    ]:
        require_string(defects, data.get(key), f"{path}.{key}")
    if status_path and not is_absolute_path(status_path):
        defect(defects, f"{path}.status_path", "must be an absolute path without traversal")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    route_class = validate_worker_route_class(defects, data.get("route_class"), f"{path}.route_class")
    selected_ladder = validate_worker_ladder(defects, data.get("selected_ladder"), f"{path}.selected_ladder")
    selection_reason = require_string(defects, data.get("selection_reason"), f"{path}.selection_reason")
    validate_route_class_cost(defects, route_class, selected_ladder, f"{path}.selected_ladder", selection_reason)
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_worker_command_evidence(defects, data.get("commands_run"), f"{path}.commands_run")
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")
    return data


def validate_worker_status(defects: list[str], value: object, path: str) -> None:
    validate_worker_payload(
        defects,
        value,
        path,
        required=CONTRACT.WORKER_ROLLUP_REQUIRED,
        require_status_path=True,
    )


def validate_research_payload(
    defects: list[str],
    value: object,
    path: str,
    *,
    required: tuple[str, ...],
    require_branch: bool = False,
    require_status_path: bool = False,
) -> dict:
    data = require_object(defects, value, path)
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("role") != "research-worker":
        defect(defects, f"{path}.role", "must be 'research-worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    if require_branch:
        require_string(defects, data.get("branch"), f"{path}.branch")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path") if require_status_path else ""
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
            defect(
                defects,
                f"{path}.local_files_read[{index}]",
                "must be a repo-relative path without git porcelain status",
            )
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


def validate_research_status(defects: list[str], value: object, path: str) -> None:
    validate_research_payload(
        defects,
        value,
        path,
        required=CONTRACT.RESEARCH_ROLLUP_REQUIRED,
        require_status_path=True,
    )


def validate_packet_status(defects: list[str], value: object, path: str) -> None:
    if isinstance(value, dict) and value.get("role") == "research-worker":
        validate_research_status(defects, value, path)
    else:
        validate_worker_status(defects, value, path)


def validate_worker_artifact(defects: list[str], value: object, path: str) -> dict:
    return validate_worker_payload(
        defects,
        value,
        path,
        required=CONTRACT.WORKER_STATUS_REQUIRED,
        require_role=True,
        require_branch=True,
    )


def resolve_bundle_artifact_path(defects: list[str], value: object, path: str, *, manifest_path: Path) -> Path | None:
    artifact = require_string(defects, value, path)
    if not artifact:
        return None
    candidate = Path(artifact)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif is_repo_relative_path(artifact):
        resolved = (manifest_path.parent / artifact).resolve()
    else:
        defect(defects, path, "must be an absolute path or safe bundle-relative path")
        return None
    try:
        resolved.relative_to(manifest_path.parent.resolve())
    except ValueError:
        defect(defects, path, "must stay inside the bundle root")
        return None
    return resolved


def validate_repair_evidence_identity(
    defects: list[str], evidence_obj: dict, path: str, *, branch_id: str, packet_id: str
) -> str:
    if evidence_obj.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    if evidence_obj.get("kind") != "worker-repair-promotion":
        defect(defects, f"{path}.kind", "must be worker-repair-promotion")
    if evidence_obj.get("branch_id") != branch_id:
        defect(defects, f"{path}.branch_id", f"must be {branch_id!r}")
    if evidence_obj.get("packet_id") != packet_id:
        defect(defects, f"{path}.packet_id", f"must be {packet_id!r}")
    if evidence_obj.get("code_integrated") is not True:
        defect(defects, f"{path}.code_integrated", "must be true")
    integrated_commit = require_string(defects, evidence_obj.get("integrated_commit"), f"{path}.integrated_commit")
    if integrated_commit and not COMMIT_SHA_RE.fullmatch(integrated_commit):
        defect(defects, f"{path}.integrated_commit", "must be a 40-character lowercase git commit")
    return integrated_commit


def validate_repair_promotion_source_status(
    defects: list[str], promotion: dict, path: str, *, manifest_path: Path, status_artifact: Path
) -> None:
    source_status_path = resolve_bundle_artifact_path(
        defects,
        promotion.get("source_status_path"),
        f"{path}.repair_promotion.source_status_path",
        manifest_path=manifest_path,
    )
    if source_status_path is None:
        return
    if not source_status_path.exists():
        defect(defects, f"{path}.repair_promotion.source_status_path", f"artifact does not exist: {source_status_path}")
    elif source_status_path == status_artifact:
        defect(
            defects,
            f"{path}.repair_promotion.source_status_path",
            "must not point at the promoted canonical status",
        )
    else:
        source_status = load_json_artifact(defects, source_status_path, f"{path}.repair_promotion.source_status_path")
        if isinstance(source_status, dict) and source_status.get("status") == "pass":
            defect(
                defects,
                f"{path}.repair_promotion.source_status_path.status",
                "must preserve a non-pass source status",
            )


def validate_repair_evidence_command_copy(defects: list[str], evidence_obj: dict, worker: dict, path: str) -> None:
    evidence_commands = require_string_list(
        defects, evidence_obj.get("commands_run"), f"{path}.commands_run", min_items=1
    )
    evidence_tests = require_string_list(defects, evidence_obj.get("tests"), f"{path}.tests", min_items=1)
    worker_commands = worker.get("commands_run") if isinstance(worker.get("commands_run"), list) else []
    worker_tests = worker.get("tests") if isinstance(worker.get("tests"), list) else []
    for command in evidence_commands:
        if command not in worker_commands:
            defect(defects, f"{path}.commands_run", "must be copied into promoted worker commands_run")
    for test_command in evidence_tests:
        if test_command not in worker_tests:
            defect(defects, f"{path}.tests", "must be copied into promoted worker tests")
    if not any("git diff --check" in command for command in evidence_commands):
        defect(defects, f"{path}.commands_run", "must include git diff --check evidence")


def validate_worker_repair_promotion(
    defects: list[str],
    worker: dict,
    path: str,
    *,
    manifest_path: Path,
    branch_id: str,
    packet_id: str,
    work_item_id: str,
    status_artifact: Path,
    worktree: str,
) -> bool:
    repair_path_value = worker.get("repair_evidence_path")
    promotion = worker.get("repair_promotion")
    if repair_path_value is None and promotion is None:
        return False
    if worker.get("status") != "pass":
        defect(defects, path, "may be present only on pass worker status")
        return False
    if not isinstance(promotion, dict):
        defect(defects, f"{path}.repair_promotion", "must be an object when repair_evidence_path is present")
        return False
    evidence_path = resolve_bundle_artifact_path(defects, repair_path_value, path, manifest_path=manifest_path)
    if evidence_path is None:
        return False
    if not evidence_path.exists():
        defect(defects, path, f"repair evidence artifact does not exist: {evidence_path}")
        return False
    try:
        evidence = load_json_artifact(defects, evidence_path, path)
    except Exception:  # noqa: BLE001
        return False
    evidence_obj = require_object(defects, evidence, path)
    integrated_commit = validate_repair_evidence_identity(
        defects, evidence_obj, path, branch_id=branch_id, packet_id=packet_id
    )
    if promotion.get("kind") != "worker-repair-promotion":
        defect(defects, f"{path}.repair_promotion.kind", "must be worker-repair-promotion")
    if promotion.get("integrated_commit") != integrated_commit:
        defect(defects, f"{path}.repair_promotion.integrated_commit", "must match repair evidence integrated_commit")
    if promotion.get("validated_by") != "promote_worker_repair_evidence.py":
        defect(defects, f"{path}.repair_promotion.validated_by", "must be promote_worker_repair_evidence.py")
    validate_repair_promotion_source_status(
        defects, promotion, path, manifest_path=manifest_path, status_artifact=status_artifact
    )
    source_telemetry = promotion.get("source_telemetry_path")
    if source_telemetry is not None:
        source_telemetry_path = resolve_bundle_artifact_path(
            defects,
            source_telemetry,
            f"{path}.repair_promotion.source_telemetry_path",
            manifest_path=manifest_path,
        )
        if source_telemetry_path is not None and not source_telemetry_path.exists():
            defect(
                defects,
                f"{path}.repair_promotion.source_telemetry_path",
                f"artifact does not exist: {source_telemetry_path}",
            )
    validate_repair_evidence_command_copy(defects, evidence_obj, worker, path)
    if evidence_obj.get("work_item_id") not in {None, work_item_id}:
        defect(defects, f"{path}.work_item_id", f"must be {work_item_id!r} when present")
    if evidence_obj.get("worktree") not in {None, worktree}:
        defect(defects, f"{path}.worktree", "must match promoted worker worktree when present")
    return True


def validate_research_artifact(defects: list[str], value: object, path: str) -> dict:
    return validate_research_payload(
        defects,
        value,
        path,
        required=CONTRACT.RESEARCH_STATUS_REQUIRED,
        require_branch=True,
    )


def resolve_worker_status_artifact(
    defects: list[str], item: dict, item_path: str, *, item_role: str, manifest_path: Path, require_existing: bool
) -> Path | None:
    status_path_value = item.get("status_path")
    if (
        not isinstance(status_path_value, str)
        or not status_path_value.strip()
        or not is_absolute_path(status_path_value)
    ):
        return None
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
        return None
    return status_artifact


def validate_worker_changed_files(
    defects: list[str], artifact: dict, item_path: str, *, owned_paths: list[str]
) -> None:
    changed_files = artifact.get("changed_files") if isinstance(artifact.get("changed_files"), list) else []
    for changed_index, changed in enumerate(changed_files):
        if not isinstance(changed, str):
            continue
        if is_runtime_cache_path(changed):
            defect(
                defects,
                f"{item_path}.changed_files[{changed_index}]",
                "must not include runtime cache or bytecode paths",
            )
        elif artifact.get("status") == "pass" and not path_is_owned(changed, owned_paths):
            # Fail closed when owned_paths is empty: a passing worker that declares no owned
            # paths must not be able to land arbitrary changes. Dropping the `owned_paths and`
            # short-circuit makes the validator consistent with runtime_packet_runner's
            # worker_ownership_violations (which already treats empty owned as "owns nothing").
            defect(
                defects,
                f"{item_path}.changed_files[{changed_index}]",
                "must be inside the manifest work item owned_paths",
            )


def validate_worker_manifest_identity(
    defects: list[str],
    artifact: dict,
    item_path: str,
    *,
    packet_id: object,
    branch_id: object,
    status_artifact: Path,
    manifest_path: Path,
    expected_route_classes: dict[str, str],
    manifest_work_items: dict[str, dict],
    allow_archived_manifest_hashes: bool,
) -> bool:
    expected_route_class = expected_route_classes.get(str(packet_id))
    if expected_route_class and artifact.get("route_class") != expected_route_class:
        defect(defects, f"{item_path}.route_class", "must match manifest work item route_class")
    manifest_item = manifest_work_items.get(str(packet_id))
    expected_work_item_id = manifest_item.get("id") if isinstance(manifest_item, dict) else None
    expected_route_id = f"{packet_id}:{artifact.get('route_class')}:{','.join(item for item in artifact.get('selected_ladder', []) if isinstance(item, str))}"
    if artifact.get("branch_id") != branch_id:
        defect(defects, f"{item_path}.branch_id", "must match manifest branch id")
    if isinstance(expected_work_item_id, str) and artifact.get("work_item_id") != expected_work_item_id:
        defect(defects, f"{item_path}.work_item_id", "must match manifest work item id")
    allowed_manifest_hashes = {sha256_file(manifest_path)}
    if allow_archived_manifest_hashes:
        allowed_manifest_hashes.update(archived_manifest_sha256s(manifest_path))
    if artifact.get("manifest_hash") not in allowed_manifest_hashes:
        defect(defects, f"{item_path}.manifest_hash", "must match current or archived manifest sha256")
    if artifact.get("worktree_path") != artifact.get("worktree"):
        defect(defects, f"{item_path}.worktree_path", "must match worker worktree")
    if artifact.get("route_id") != expected_route_id:
        defect(defects, f"{item_path}.route_id", "must match packet_id:route_class:selected_ladder")
    owned_paths = (
        [value for value in manifest_item.get("owned_paths", []) if isinstance(value, str) and value.strip()]
        if isinstance(manifest_item, dict)
        else []
    )
    validate_worker_changed_files(defects, artifact, item_path, owned_paths=owned_paths)
    return validate_worker_repair_promotion(
        defects,
        artifact,
        f"{item_path}.repair_evidence_path",
        manifest_path=manifest_path,
        branch_id=branch_id if isinstance(branch_id, str) else "",
        packet_id=str(packet_id) if isinstance(packet_id, str) else "",
        work_item_id=expected_work_item_id if isinstance(expected_work_item_id, str) else "",
        status_artifact=status_artifact,
        worktree=artifact.get("worktree") if isinstance(artifact.get("worktree"), str) else "",
    )


def validate_research_worker_telemetry(
    defects: list[str], artifact: dict, status_artifact: Path, item_path: str, *, packet_id: object
) -> None:
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
        defect(
            defects,
            f"{item_path}.telemetry_path.accepted_alias",
            "must identify the accepted research route when research status is pass",
        )


def validate_worker_route_and_telemetry(
    defects: list[str],
    artifact: dict,
    status_artifact: Path,
    item_path: str,
    *,
    packet_id: object,
    repair_promoted: bool,
) -> None:
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
    selected_ladder = artifact.get("selected_ladder") if isinstance(artifact.get("selected_ladder"), list) else []
    telemetry = validate_telemetry_artifact(
        defects,
        status_artifact.parent / "telemetry.json",
        f"{item_path}.telemetry_path",
        packet_id=str(packet_id) if isinstance(packet_id, str) else None,
        role="worker",
        allowed_aliases=selected_ladder or DEFAULT_WORKER_LADDER,
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
    if telemetry_aliases and selected_ladder and telemetry_aliases != selected_ladder:
        defect(
            defects,
            f"{item_path}.telemetry_path.attempts",
            "declared telemetry attempts must match selected_ladder exactly",
        )
    called_aliases = [
        attempt.get("alias")
        for attempt in telemetry_attempts
        if isinstance(attempt, dict) and attempt.get("called") is True and isinstance(attempt.get("alias"), str)
    ]
    effective_ladder = effective_ladder_for_called_attempts(selected_ladder, telemetry_attempts)
    if called_aliases and selected_ladder and called_aliases != effective_ladder[: len(called_aliases)]:
        defect(
            defects,
            f"{item_path}.telemetry_path.attempts",
            "called worker attempts must be a prefix of selected_ladder",
        )
    if (
        artifact.get("status") == "pass"
        and not repair_promoted
        and telemetry.get("accepted_alias") not in selected_ladder
    ):
        defect(
            defects,
            f"{item_path}.telemetry_path.accepted_alias",
            "must identify the accepted worker route when worker status is pass",
        )


def validate_worker_artifact_entry(
    defects: list[str],
    item: dict,
    index: int,
    *,
    branch_name: object,
    branch_id: object,
    manifest_path: Path,
    require_existing: bool,
    allow_archived_manifest_hashes: bool,
    worker_compared_keys: list[str],
    research_compared_keys: list[str],
    expected_route_classes: dict[str, str],
    manifest_work_items: dict[str, dict],
) -> None:
    item_path = f"$.worker_statuses[{index}]"
    item_role = item.get("role") if isinstance(item.get("role"), str) else "worker"
    status_artifact = resolve_worker_status_artifact(
        defects,
        item,
        item_path,
        item_role=item_role,
        manifest_path=manifest_path,
        require_existing=require_existing,
    )
    if status_artifact is None:
        return
    packet_id = item.get("packet_id")
    validate_launch_config_artifact(
        defects,
        status_artifact.parent,
        f"{item_path}.launch_config_path",
        packet_id=str(packet_id) if isinstance(packet_id, str) else None,
        role=item_role,
        output_name="research.json" if item_role == "research-worker" else "status.json",
    )
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
    if isinstance(branch_name, str) and branch_name.strip() and artifact.get("branch") != branch_name:
        defect(defects, f"{item_path}.status_path.branch", "must match branch status branch")
    if item_role == "worker":
        repair_promoted = validate_worker_manifest_identity(
            defects,
            artifact,
            item_path,
            packet_id=packet_id,
            branch_id=branch_id,
            status_artifact=status_artifact,
            manifest_path=manifest_path,
            expected_route_classes=expected_route_classes,
            manifest_work_items=manifest_work_items,
            allow_archived_manifest_hashes=allow_archived_manifest_hashes,
        )
    else:
        repair_promoted = False
    for key in compared_keys:
        if artifact.get(key) != item.get(key):
            defect(defects, f"{item_path}.{key}", "must match packet status artifact")
    if item_role == "research-worker":
        validate_research_worker_telemetry(defects, artifact, status_artifact, item_path, packet_id=packet_id)
        return
    validate_worker_route_and_telemetry(
        defects, artifact, status_artifact, item_path, packet_id=packet_id, repair_promoted=repair_promoted
    )


def validate_worker_artifacts(
    defects: list[str],
    worker_statuses: object,
    branch_status: object,
    *,
    branch_entry: object,
    branch_name: object,
    manifest_path: Path,
    allow_archived_manifest_hashes: bool = False,
) -> None:
    if not isinstance(worker_statuses, list):
        return
    require_existing = branch_status in {"pass", "partial"}
    worker_compared_keys = [key for key in CONTRACT.WORKER_STATUS_REQUIRED if key not in {"role", "branch"}]
    research_compared_keys = [key for key in CONTRACT.RESEARCH_STATUS_REQUIRED if key != "branch"]
    branch_id = branch_entry.get("id") if isinstance(branch_entry, dict) else ""
    expected_route_classes = (
        expected_worker_route_classes(defects, branch_entry, branch_id)
        if isinstance(branch_id, str) and branch_id
        else {}
    )
    manifest_work_items = work_items_by_packet(branch_entry)
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        validate_worker_artifact_entry(
            defects,
            item,
            index,
            branch_name=branch_name,
            branch_id=branch_id,
            manifest_path=manifest_path,
            require_existing=require_existing,
            allow_archived_manifest_hashes=allow_archived_manifest_hashes,
            worker_compared_keys=worker_compared_keys,
            research_compared_keys=research_compared_keys,
            expected_route_classes=expected_route_classes,
            manifest_work_items=manifest_work_items,
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
        deps = require_string_list(
            defects, item.get("depends_on", []), f"manifest.branch.work_items[{index}].depends_on"
        )
        for dep in deps:
            if dep not in work_item_order or work_item_order[dep] >= index:
                defect(
                    defects,
                    f"manifest.branch.work_items[{index}].depends_on",
                    f"must reference only prior work item ids; invalid dependency: {dep}",
                )
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
            defect(
                defects, f"manifest.branch.work_items[{index}].worker_type", f"must be one of {sorted(WORK_ITEM_ROLES)}"
            )
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
                defect(
                    defects,
                    f"manifest.branch.work_items[{index}].route_class",
                    "must be omitted for research-worker items",
                )
            continue
        if not isinstance(route_class, str) or route_class not in MANIFEST_WORKER_ROUTE_CLASSES:
            defect(
                defects,
                f"manifest.branch.work_items[{index}].route_class",
                f"must be one of {list(MANIFEST_WORKER_ROUTE_CLASSES)}",
            )
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
        dependencies[packet_id] = (
            [f"{branch_id}-{dep}" for dep in deps if isinstance(dep, str) and dep.strip()]
            if isinstance(deps, list)
            else []
        )
    return dependencies


def worktree_freshness_path(branch_id: str) -> str:
    return f"branches/{branch_id}.worktree_freshness.json"


def review_route_policy_path(branch_id: str) -> str:
    return f"branches/{branch_id}.review_route_policy.json"


def review_evidence_path(branch_id: str) -> str:
    return f"branches/{branch_id}.pre_review_evidence.json"


PACKET_TERMINAL_INPUT_NAMES = [
    "launcher-state.json",
    "packet.summary.json",
]

ATTEMPT_TERMINAL_INPUT_NAMES = [
    "launcher-state.json",
    "packet.summary.json",
    "status.json",
    "research.json",
    "review.json",
    "route.json",
    "telemetry.json",
    "telemetry.debug.json",
    "ownership.blocked.txt",
]


def packet_artifact_root(worker_type: object, packet_id: str) -> str:
    role = worker_type if isinstance(worker_type, str) else "worker"
    if role == "research":
        role = "research-worker"
    if role == "research-worker":
        return f"research/{packet_id}"
    return f"workers/{packet_id}"


def discovered_attempt_terminal_paths(bundle_dir: Path | None, packet_root: str) -> list[str]:
    if bundle_dir is None:
        return []
    attempts_dir = bundle_dir / packet_root / "attempts"
    if not attempts_dir.is_dir():
        return []
    paths: list[str] = []
    for attempt_dir in sorted(attempts_dir.iterdir(), key=lambda item: item.name):
        if not attempt_dir.is_dir() or not re.fullmatch(r"attempt-\d{3,}", attempt_dir.name):
            continue
        for name in ATTEMPT_TERMINAL_INPUT_NAMES:
            rel_path = f"{packet_root}/attempts/{attempt_dir.name}/{name}"
            if (bundle_dir / rel_path).exists():
                paths.append(rel_path)
    return paths


def diagnostic_pre_review_input_paths(
    branch_entry: dict, branch_id: str, *, bundle_dir: Path | None = None
) -> list[str]:
    paths: list[str] = []
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list):
        return paths
    for item in work_items:
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            item_id = item.get("id")
            packet_id = f"{branch_id}-{item_id}" if isinstance(item_id, str) and item_id.strip() else ""
        if not packet_id:
            continue
        worker_type = item.get("worker_type", "worker")
        role = worker_type if isinstance(worker_type, str) else "worker"
        packet_root = packet_artifact_root(role, packet_id)
        paths.extend(discovered_attempt_terminal_paths(bundle_dir, packet_root))
    return paths


def required_pre_review_input_paths(branch_entry: dict, branch_id: str, *, bundle_dir: Path | None = None) -> list[str]:
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
                item_id = item.get("id")
                packet_id = f"{branch_id}-{item_id}" if isinstance(item_id, str) and item_id.strip() else ""
            if not packet_id:
                continue
            worker_type = item.get("worker_type", "worker")
            role = worker_type if isinstance(worker_type, str) else "worker"
            if role == "research":
                role = "research-worker"
            packet_root = packet_artifact_root(role, packet_id)
            if role == "research-worker":
                paths.append(f"research/{packet_id}/research.json")
                paths.append(f"research/{packet_id}/telemetry.json")
            else:
                paths.append(f"workers/{packet_id}/status.json")
                paths.append(f"workers/{packet_id}/route.json")
                paths.append(f"workers/{packet_id}/telemetry.json")
            paths.extend(f"{packet_root}/{name}" for name in PACKET_TERMINAL_INPUT_NAMES)
    return [path for path in paths if isinstance(path, str) and path.strip()]


_GIT_STDOUT_CACHE: dict[tuple[str, tuple[str, ...]], subprocess.CompletedProcess[str]] = {}


def cacheable_git_stdout(command: list[str]) -> bool:
    if len(command) < 2 or command[0] != "git":
        return False
    if command[1] in {"rev-parse", "merge-base"}:
        return True
    return command[1:3] == ["diff", "--name-status"] or (
        command[1:3] == ["diff", "--name-only"] and any("...HEAD" in part for part in command[3:])
    )


def run_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    key = (cwd.resolve().as_posix(), tuple(command))
    if cacheable_git_stdout(command):
        cached = _GIT_STDOUT_CACHE.get(key)
        if cached is not None:
            return cached
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode == 0 and cacheable_git_stdout(command):
        _GIT_STDOUT_CACHE[key] = result
    return result


def clear_git_stdout_cache() -> None:
    _GIT_STDOUT_CACHE.clear()


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


def freshness_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    for rel_path in paths:
        if not is_repo_relative_path(rel_path) or is_runtime_cache_path(rel_path):
            continue
        if rel_path not in result:
            result.append(rel_path)
    return result


def expected_worktree_freshness(
    defects: list[str],
    *,
    worktree: Path,
    base_ref: str,
    branch_id: str,
    branch_status: dict,
    path: str,
) -> dict:
    head = git_stdout(
        defects, ["git", "rev-parse", "HEAD"], cwd=worktree, path=path, label="git rev-parse HEAD"
    ).strip()
    merge_base = git_stdout(
        defects,
        ["git", "merge-base", base_ref, "HEAD"],
        cwd=worktree,
        path=path,
        label=f"git merge-base {base_ref} HEAD",
    ).strip()
    name_status = git_stdout(
        defects,
        ["git", "diff", "--name-status", "--find-renames", f"{base_ref}...HEAD"],
        cwd=worktree,
        path=path,
        label=f"git diff --name-status --find-renames {base_ref}...HEAD",
    )
    base_paths = git_paths(
        defects,
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=worktree,
        path=path,
        label=f"git diff --name-only {base_ref}...HEAD",
    )
    unstaged_paths = git_paths(
        defects, ["git", "diff", "--name-only", "HEAD"], cwd=worktree, path=path, label="git diff --name-only HEAD"
    )
    staged_paths = git_paths(
        defects,
        ["git", "diff", "--cached", "--name-only", "HEAD"],
        cwd=worktree,
        path=path,
        label="git diff --cached --name-only HEAD",
    )
    untracked_paths = git_paths(
        defects,
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        path=path,
        label="git ls-files --others --exclude-standard",
    )
    status_paths = (
        [item for item in branch_status.get("changed_files", []) if isinstance(item, str) and item.strip()]
        if isinstance(branch_status.get("changed_files"), list)
        else []
    )
    base_paths = freshness_paths(base_paths)
    current_paths = freshness_paths([*status_paths, *base_paths, *unstaged_paths, *staged_paths, *untracked_paths])
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
    require_current_snapshot: bool,
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
    expected_static = {
        "schema_version": 1,
        "branch_id": branch_id,
        "worktree": worktree_value,
        "base_ref": base_ref,
    }
    for key, expected_value in expected_static.items():
        if artifact.get(key) != expected_value:
            defect(defects, f"{path}.{key}", "must match the archived branch worktree freshness snapshot identity")
    require_string(defects, artifact.get("worktree_head"), f"{path}.worktree_head")
    require_string(defects, artifact.get("merge_base"), f"{path}.merge_base")
    digest = artifact.get("diff_name_status_sha256")
    if not isinstance(digest, str) or not STATUS_VALIDATION.SHA256_RE.fullmatch(digest):
        defect(defects, f"{path}.diff_name_status_sha256", "must be sha256:<64 lowercase hex chars>")
    for key in ["base_range_changed_files", "current_changed_files"]:
        validate_path_list(defects, artifact.get(key), f"{path}.{key}")
    current_hashes = require_object(defects, artifact.get("current_file_hashes"), f"{path}.current_file_hashes")
    for rel_path, digest_value in current_hashes.items():
        if not isinstance(rel_path, str) or not is_repo_relative_path(rel_path):
            defect(defects, f"{path}.current_file_hashes", "keys must be repo-relative paths without traversal")
        if not isinstance(digest_value, str) or (
            digest_value not in {"missing", "non-file", "outside-worktree"}
            and not digest_value.startswith("symlink:")
            and not STATUS_VALIDATION.SHA256_RE.fullmatch(digest_value)
        ):
            defect(
                defects,
                f"{path}.current_file_hashes.{rel_path}",
                "must be a sha256 digest or a supported file-state marker",
            )
    if not require_current_snapshot:
        return
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
            defect(
                defects,
                "$.worker_parallelism.max_observed_active_worker_packets",
                "must match scheduler ledger reconstruction exactly",
            )
        try:
            expected_refill_events = scheduler_refill_events(manifest_path.parent / expected_path)
        except Exception as exc:  # noqa: BLE001
            defect(
                defects, "$.worker_parallelism.refill_events", f"could not reconstruct scheduler refill events: {exc}"
            )
            expected_refill_events = []
        actual_refill_events = runtime_parallelism.get("refill_events")
        if (
            isinstance(actual_refill_events, list)
            and all(isinstance(item, str) for item in actual_refill_events)
            and actual_refill_events != expected_refill_events
        ):
            defect(defects, "$.worker_parallelism.refill_events", "must match scheduler ledger refill events exactly")
    finished_status = summary.get("finished_status") if isinstance(summary.get("finished_status"), dict) else {}
    worker_statuses = root.get("worker_statuses")
    if isinstance(worker_statuses, list):
        for index, item in enumerate(worker_statuses):
            if not isinstance(item, dict):
                continue
            packet_id = item.get("packet_id")
            if (
                isinstance(packet_id, str)
                and packet_id in finished_status
                and item.get("status") != finished_status[packet_id]
            ):
                defect(
                    defects, f"$.worker_statuses[{index}].status", "must match scheduler finish status for the packet"
                )


def validate_manifest_branch_identity(
    defects: list[str], root: dict, branch_entry: dict, *, branch_id: str, manifest_path: Path, status_path: Path
) -> None:
    manifest_branch_name = branch_entry.get("branch_name")
    if (
        isinstance(manifest_branch_name, str)
        and manifest_branch_name.strip()
        and root.get("branch") != manifest_branch_name
    ):
        defect(defects, "$.branch", f"must match manifest branch_name {manifest_branch_name!r}")

    manifest_status_path = branch_entry.get("status_path")
    if isinstance(manifest_status_path, str) and manifest_status_path.strip():
        expected_status_path = (manifest_path.parent / manifest_status_path).resolve().as_posix()
        if status_path.as_posix() != expected_status_path:
            defect(defects, "--status", "must match manifest branch status_path")

    worker_parallelism = root.get("worker_parallelism")
    manifest_max_workers = branch_entry.get("max_active_worker_packets")
    if (
        not is_strict_int(manifest_max_workers)
        or manifest_max_workers < 1
        or manifest_max_workers > MAX_WORKER_PACKETS_PER_BRANCH
    ):
        defect(defects, "manifest.branch.max_active_worker_packets", "must be an integer from 1 to 4")
    if (
        isinstance(worker_parallelism, dict)
        and is_strict_int(manifest_max_workers)
        and worker_parallelism.get("max_active_worker_packets") != manifest_max_workers
    ):
        defect(
            defects, "$.worker_parallelism.max_active_worker_packets", "must match manifest max_active_worker_packets"
        )
    expected_scheduler_path = CONTRACT.worker_scheduler_path(branch_id)
    branch_worker_parallelism = branch_entry.get("worker_parallelism")
    if (
        isinstance(branch_worker_parallelism, dict)
        and branch_worker_parallelism.get("scheduler_path") != expected_scheduler_path
    ):
        defect(defects, "manifest.branch.worker_parallelism.scheduler_path", f"must be {expected_scheduler_path!r}")
    pre_review_gate_path = branch_entry.get("pre_review_gate_path")
    expected_gate_path = CONTRACT.pre_review_gate_path(branch_id)
    if pre_review_gate_path != expected_gate_path:
        defect(defects, "manifest.branch.pre_review_gate_path", f"must be {expected_gate_path!r}")


def validate_worker_status_packet_set(
    defects: list[str],
    root: dict,
    branch_entry: dict,
    *,
    branch_id: str,
    require_all_workers: bool,
) -> None:
    expected_ids = set(expected_worker_packet_ids(defects, branch_entry, branch_id))
    expected_roles = expected_worker_packet_roles(defects, branch_entry, branch_id)
    worker_statuses = root.get("worker_statuses")
    if not isinstance(worker_statuses, list) or not expected_ids:
        return
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
            defect(
                defects,
                "$.worker_statuses",
                f"contains worker packet statuses not declared in manifest: {', '.join(extra)}",
            )


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
    validate_manifest_branch_identity(
        defects, root, branch_entry, branch_id=branch_id, manifest_path=manifest_path, status_path=status_path
    )
    validate_worker_status_packet_set(
        defects, root, branch_entry, branch_id=branch_id, require_all_workers=require_all_workers
    )
    return branch_entry


def validate_parallelism_counts(defects: list[str], data: dict, path: str) -> None:
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
    if (
        not is_strict_int(max_observed_active)
        or max_observed_active < 0
        or max_observed_active > MAX_WORKER_PACKETS_PER_BRANCH
    ):
        defect(defects, f"{path}.max_observed_active", "must be an integer from 0 to 4")
    if data.get("concurrent_launch_default") is not True:
        defect(defects, f"{path}.concurrent_launch_default", "must be true")
    if data.get("rolling_refill_default") is not True:
        defect(defects, f"{path}.rolling_refill_default", "must be true")
    if data.get("scheduling_mode") != "rolling":
        defect(defects, f"{path}.scheduling_mode", "must be rolling")


def validate_parallelism_serialization(defects: list[str], data: dict, path: str, *, worker_count: int) -> None:
    max_active = data.get("max_active_worker_packets")
    observed = data.get("max_observed_active_worker_packets")
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
        defect(
            defects,
            f"{path}.refill_events",
            "must record worker slot refill events when worker count exceeds max_active_worker_packets",
        )
    if (
        worker_count > 1
        and is_strict_int(max_active)
        and is_strict_int(observed)
        and observed < min(max_active, worker_count)
        and not serial_reasons
    ):
        defect(
            defects,
            f"{path}.serial_reasons",
            "must justify observed worker parallelism below available worker concurrency",
        )


def validate_worker_parallelism(defects: list[str], value: object, path: str, *, worker_count: int) -> None:
    data = require_object(defects, value, path)
    validate_parallelism_counts(defects, data, path)
    validate_parallelism_serialization(defects, data, path, worker_count=worker_count)


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
    if "finding_classes" in data:
        finding_classes = require_string_list(defects, data.get("finding_classes"), f"{path}.finding_classes")
        allowed_finding_classes = {"project_bug", "orchestration_bug", "verification_gap", "no_issue"}
        for index, item in enumerate(finding_classes):
            if item not in allowed_finding_classes:
                defect(defects, f"{path}.finding_classes[{index}]", f"must be one of {sorted(allowed_finding_classes)}")
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
            defect(
                defects,
                f"{path}.semantic_input_hashes",
                "must match pre_review_gate.json semantic_input_hashes exactly",
            )
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
        defect(
            defects, f"{path}.reuse_policy.source_review_path", "must be an absolute path or safe bundle-relative path"
        )
        return True
    if source_telemetry is None:
        defect(
            defects,
            f"{path}.reuse_policy.source_telemetry_path",
            "must be an absolute path or safe bundle-relative path",
        )
        return True
    if not source_review.exists():
        defect(defects, f"{path}.reuse_policy.source_review_path", f"source review does not exist: {source_review}")
        return True
    if not source_telemetry.exists():
        defect(
            defects,
            f"{path}.reuse_policy.source_telemetry_path",
            f"source telemetry does not exist: {source_telemetry}",
        )
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
            defect(
                defects,
                f"{path}.reuse_policy.source_review_path.semantic_input_hashes",
                "must match current semantic input hashes for accepted reuse",
            )
        source_verdict = source_data.get("verdict")
        if source_verdict not in REVIEW_STATUSES - {"missing"}:
            defect(defects, f"{path}.reuse_policy.source_review_path.verdict", "must be a valid reviewer verdict")
    validate_telemetry_artifact(
        defects,
        source_telemetry,
        f"{path}.reuse_policy.source_telemetry_path",
        role="reviewer",
        allowed_aliases=REVIEWER_ALLOWED_ALIASES,
        require_called=True,
    )
    return True


def validate_review_pre_review_gate(
    defects: list[str],
    branch_entry: dict,
    *,
    branch_id: str,
    packet_id: object,
    manifest: object,
    manifest_path: Path,
    branch_status_root: dict,
    require_current_worktree_freshness: bool,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None,
) -> dict[str, str] | None:
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
        required_input_paths=required_pre_review_input_paths(
            branch_entry,
            branch_id,
            bundle_dir=manifest_path.parent,
        ),
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
        require_current_snapshot=require_current_worktree_freshness,
    )
    if isinstance(gate.get("semantic_input_hashes"), dict):
        return {
            key: value
            for key, value in gate["semantic_input_hashes"].items()
            if isinstance(key, str) and isinstance(value, str)
        }
    return None


def validate_reviewer_route_for_branch(
    defects: list[str],
    review_packet_dir: Path,
    *,
    packet_id: object,
    manifest: object,
    manifest_path: Path,
) -> list[str]:
    route_path = review_packet_dir / "route.json"
    route_aliases: list[str] = []
    validate_launch_config_artifact(
        defects,
        review_packet_dir,
        "$.review_status.launch_config_path",
        packet_id=packet_id if isinstance(packet_id, str) else None,
        role="reviewer",
        output_name="review.json",
    )
    if not route_path.exists():
        defect(defects, "$.review_status.route_path", f"route artifact does not exist: {route_path}")
    else:
        route_aliases = validate_reviewer_route_artifact(
            defects,
            load_json_artifact(defects, route_path, "$.review_status.route_path"),
            "$.review_status.route_path",
            packet_id=packet_id if isinstance(packet_id, str) else "",
            manifest=manifest,
            manifest_path=manifest_path,
        )
    return route_aliases


def validate_reviewer_reuse_telemetry(
    defects: list[str], telemetry_path: Path, *, packet_id: object, route_aliases: list[str]
) -> None:
    if not telemetry_path.exists():
        return
    telemetry = validate_telemetry_artifact(
        defects,
        telemetry_path,
        "$.review_status.telemetry_path",
        packet_id=packet_id if isinstance(packet_id, str) else None,
        role="reviewer",
        allowed_aliases=route_aliases or REVIEWER_ALLOWED_ALIASES,
        require_called=False,
    )
    called = (
        [
            attempt
            for attempt in telemetry.get("attempts", [])
            if isinstance(attempt, dict) and attempt.get("called") is True
        ]
        if isinstance(telemetry.get("attempts"), list)
        else []
    )
    if called:
        defect(
            defects,
            "$.review_status.telemetry_path.attempts",
            "accepted reviewer reuse must not record a fresh called model attempt",
        )


def validate_reviewer_fresh_telemetry(
    defects: list[str], telemetry_path: Path, *, packet_id: object, route_aliases: list[str]
) -> None:
    telemetry = validate_telemetry_artifact(
        defects,
        telemetry_path,
        "$.review_status.telemetry_path",
        packet_id=packet_id if isinstance(packet_id, str) else None,
        role="reviewer",
        allowed_aliases=route_aliases or REVIEWER_ALLOWED_ALIASES,
        require_called=True,
    )
    telemetry_attempts = telemetry.get("attempts") if isinstance(telemetry.get("attempts"), list) else []
    telemetry_aliases = [
        attempt.get("alias")
        for attempt in telemetry_attempts
        if isinstance(attempt, dict) and isinstance(attempt.get("alias"), str)
    ]
    if route_aliases and telemetry_aliases and telemetry_aliases != route_aliases:
        defect(
            defects,
            "$.review_status.telemetry_path.attempts",
            "declared reviewer telemetry attempts must match route.json selected_ladder exactly",
        )
    called_aliases = [
        attempt.get("alias")
        for attempt in telemetry_attempts
        if isinstance(attempt, dict) and attempt.get("called") is True and isinstance(attempt.get("alias"), str)
    ]
    effective_ladder = effective_ladder_for_called_attempts(route_aliases, telemetry_attempts)
    if route_aliases and called_aliases and called_aliases != effective_ladder[: len(called_aliases)]:
        defect(
            defects,
            "$.review_status.telemetry_path.attempts",
            "called reviewer attempts must be a prefix of route.json selected_ladder",
        )


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
    require_current_worktree_freshness: bool = True,
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
    if not require_existing:
        return
    review_data = load_json_artifact(defects, review_artifact, "$.review_status")
    packet_id = review_data.get("packet_id") if isinstance(review_data, dict) else None
    branch_id = branch_entry.get("id") if isinstance(branch_entry.get("id"), str) else None
    expected_semantic_hashes = None
    allowed_hashes_by_rel_path = (
        archived_manifest_hashes_by_rel_path(manifest_path) if allow_archived_manifest_hashes else None
    )
    if branch_id:
        expected_semantic_hashes = validate_review_pre_review_gate(
            defects,
            branch_entry,
            branch_id=branch_id,
            packet_id=packet_id,
            manifest=manifest,
            manifest_path=manifest_path,
            branch_status_root=branch_status_root,
            require_current_worktree_freshness=require_current_worktree_freshness,
            allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
        )
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
    route_aliases = validate_reviewer_route_for_branch(
        defects, review_packet_dir, packet_id=packet_id, manifest=manifest, manifest_path=manifest_path
    )
    telemetry_path = review_packet_dir / "telemetry.json"
    if reuse_accepted:
        validate_reviewer_reuse_telemetry(defects, telemetry_path, packet_id=packet_id, route_aliases=route_aliases)
        return
    validate_reviewer_fresh_telemetry(defects, telemetry_path, packet_id=packet_id, route_aliases=route_aliases)


def validate_review_waiver_artifact(
    defects: list[str],
    root: dict,
    branch_entry: dict,
    *,
    manifest_path: Path,
    branch_id: str,
    status: object,
    review_status: object,
) -> None:
    if status == "pass" or review_status != "missing":
        return
    waiver_rel = require_string(defects, root.get("review_waiver_path"), "$.review_waiver_path")
    if not waiver_rel:
        return
    if not is_repo_relative_path(waiver_rel):
        defect(defects, "$.review_waiver_path", "must be a repo-relative path without traversal")
        return
    waiver_path = (manifest_path.parent / waiver_rel).resolve()
    if not waiver_path.exists():
        defect(defects, "$.review_waiver_path", f"artifact does not exist: {waiver_path}")
        return
    waiver = require_object(
        defects, load_json_artifact(defects, waiver_path, "$.review_waiver_path"), "$.review_waiver_path"
    )
    if waiver.get("schema_version") != 1:
        defect(defects, "$.review_waiver_path.schema_version", "must be 1")
    if waiver.get("kind") not in {"review-waiver", "terminal-blocker-review"}:
        defect(defects, "$.review_waiver_path.kind", "must be review-waiver or terminal-blocker-review")
    if waiver.get("branch_id") != branch_id:
        defect(defects, "$.review_waiver_path.branch_id", f"must be {branch_id!r}")
    if waiver.get("branch_status") != status:
        defect(defects, "$.review_waiver_path.branch_status", "must match branch status")
    if waiver.get("review_status") != review_status:
        defect(defects, "$.review_waiver_path.review_status", "must match branch review_status")
    reviewer_launch_skipped = waiver.get("reviewer_launch_skipped")
    if not isinstance(reviewer_launch_skipped, bool):
        defect(defects, "$.review_waiver_path.reviewer_launch_skipped", "must be a boolean")
    review_artifact_rejected = waiver.get("review_artifact_rejected")
    if review_artifact_rejected is not None and not isinstance(review_artifact_rejected, bool):
        defect(defects, "$.review_waiver_path.review_artifact_rejected", "must be a boolean when present")
    if reviewer_launch_skipped is not True and review_artifact_rejected is not True:
        defect(
            defects,
            "$.review_waiver_path",
            "must record either reviewer_launch_skipped=true or review_artifact_rejected=true",
        )
    require_string(defects, waiver.get("reason_code"), "$.review_waiver_path.reason_code")
    require_string(defects, waiver.get("reason"), "$.review_waiver_path.reason")
    require_string(defects, waiver.get("validated_by"), "$.review_waiver_path.validated_by")
    require_string_list(defects, waiver.get("blockers"), "$.review_waiver_path.blockers", min_items=1)
    review_path = branch_entry.get("review_path")
    if isinstance(review_path, str) and waiver.get("review_path") != review_path:
        defect(defects, "$.review_waiver_path.review_path", "must match manifest branch review_path")


def validate_branch_status_header(
    defects: list[str],
    root: dict,
    *,
    branch_id: str | None,
    branch: str | None,
    worktree: str | None,
) -> object:
    required = [
        "branch_id",
        "status",
        "schema_status",
        "runtime_status",
        "dod_status",
        "resume_action",
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
    schema_status = root.get("schema_status")
    if schema_status not in {"pass", "failed"}:
        defect(defects, "$.schema_status", "must be 'pass' or 'failed'")
    runtime_status = root.get("runtime_status")
    if runtime_status not in STATUSES:
        defect(defects, "$.runtime_status", f"must be one of {sorted(STATUSES)}")
    elif runtime_status != status:
        defect(defects, "$.runtime_status", "must match status")
    dod_status = root.get("dod_status")
    if dod_status not in {"pass", "incomplete"}:
        defect(defects, "$.dod_status", "must be 'pass' or 'incomplete'")
    elif status == "pass" and dod_status != "pass":
        defect(defects, "$.dod_status", "must be pass when branch status is pass")
    elif status in {"partial", "blocked", "failed"} and dod_status == "pass":
        defect(defects, "$.dod_status", "must not be pass when branch status is non-pass")
    resume_action = root.get("resume_action")
    if resume_action not in {"reuse_terminal_status", "repair_or_reassemble"}:
        defect(defects, "$.resume_action", "must be reuse_terminal_status or repair_or_reassemble")
    require_string(defects, root.get("branch"), "$.branch")
    root_worktree = require_string(defects, root.get("worktree"), "$.worktree")
    if root_worktree and not is_absolute_path(root_worktree):
        defect(defects, "$.worktree", "must be an absolute path without traversal")
    return status


def validate_branch_worker_statuses_shape(defects: list[str], worker_statuses: object, status: object) -> None:
    min_workers = 1 if status in {"pass", "partial"} else 0
    if (
        not isinstance(worker_statuses, list)
        or len(worker_statuses) < min_workers
        or len(worker_statuses) > MAX_WORKER_PACKETS_PER_BRANCH
    ):
        defect(defects, "$.worker_statuses", f"must contain {min_workers} to 4 worker status objects")
    else:
        for index, item in enumerate(worker_statuses):
            validate_packet_status(defects, item, f"$.worker_statuses[{index}]")


def validate_branch_review_phase(
    defects: list[str],
    root: dict,
    branch_entry: dict,
    *,
    status: object,
    root_branch_id: str | None,
    manifest: object,
    manifest_path: Path,
    worktree: str | None,
    allow_archived_manifest_hashes: bool,
    require_current_worktree_freshness: bool | None,
) -> None:
    review_status = root.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, "$.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if status == "pass" and review_status != "mergeable":
        defect(defects, "$.review_status", "must be mergeable when branch status is pass")
    if not branch_entry:
        return
    if require_current_worktree_freshness is None:
        require_current_worktree_freshness = worktree is not None and not allow_archived_manifest_hashes
    validate_review_artifact_for_branch(
        defects,
        branch_entry,
        review_status,
        status,
        branch_status_root=root,
        manifest=manifest,
        manifest_path=manifest_path,
        allow_archived_manifest_hashes=allow_archived_manifest_hashes,
        require_current_worktree_freshness=require_current_worktree_freshness,
    )
    if root_branch_id:
        validate_review_waiver_artifact(
            defects,
            root,
            branch_entry,
            manifest_path=manifest_path,
            branch_id=root_branch_id,
            status=status,
            review_status=review_status,
        )


def validate_branch_status_trailer(defects: list[str], root: dict, *, status: object, manifest: object) -> None:
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
    require_current_worktree_freshness: bool | None = None,
) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    status = validate_branch_status_header(defects, root, branch_id=branch_id, branch=branch, worktree=worktree)
    worker_statuses = root.get("worker_statuses")
    validate_branch_worker_statuses_shape(defects, worker_statuses, status)
    validate_worker_rollup(defects, worker_statuses, status)
    branch_entry = validate_manifest_branch_contract(
        defects,
        root,
        manifest,
        manifest_path=manifest_path,
        status_path=status_path,
        require_all_workers=status == "pass",
    )
    validate_worker_artifacts(
        defects,
        worker_statuses,
        status,
        branch_entry=branch_entry,
        branch_name=root.get("branch"),
        manifest_path=manifest_path,
        allow_archived_manifest_hashes=allow_archived_manifest_hashes,
    )
    worker_count = len(worker_statuses) if isinstance(worker_statuses, list) else 0
    validate_worker_parallelism(
        defects, root.get("worker_parallelism"), "$.worker_parallelism", worker_count=worker_count
    )
    root_branch_id = root.get("branch_id") if isinstance(root.get("branch_id"), str) else None
    validate_lite_advice_entries(
        defects,
        root.get("lite_advice"),
        "$.lite_advice",
        manifest_path=manifest_path,
        branch_id=root_branch_id,
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
    validate_branch_review_phase(
        defects,
        root,
        branch_entry,
        status=status,
        root_branch_id=root_branch_id,
        manifest=manifest,
        manifest_path=manifest_path,
        worktree=worktree,
        allow_archived_manifest_hashes=allow_archived_manifest_hashes,
        require_current_worktree_freshness=require_current_worktree_freshness,
    )
    validate_branch_status_trailer(defects, root, status=status, manifest=manifest)
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
    parser.add_argument("--branch-id")
    parser.add_argument("--branch")
    parser.add_argument("--worktree")
    parser.add_argument("--allow-archived-manifest-hashes", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True).as_posix() if args.worktree else None
    status_data = load_json(status_path)
    defects = validate_branch_status(
        status_data,
        branch_id=args.branch_id,
        branch=args.branch,
        worktree=worktree,
        manifest=load_json(manifest_path),
        manifest_path=manifest_path,
        status_path=status_path,
        allow_archived_manifest_hashes=args.allow_archived_manifest_hashes,
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
