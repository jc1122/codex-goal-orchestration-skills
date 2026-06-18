#!/usr/bin/env python3
"""Shared helpers for goal-plan-amender scripts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple
import contextlib


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, path.parent.as_posix())
    try:
        spec.loader.exec_module(module)
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(path.parent.as_posix())
    return module


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]
SHARED_SCRIPTS = SKILLS_ROOT / "_goal_shared" / "scripts"
PREFLIGHT_SCRIPTS = SKILLS_ROOT / "goal-preflight" / "scripts"

CONTRACT = _load_module("goal_shared_orchestration_contract", SHARED_SCRIPTS / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")
PREFLIGHT = _load_module("goal_preflight_create_goal_bundle", PREFLIGHT_SCRIPTS / "create_goal_bundle.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
require_safe_id = PATH_RULES.require_safe_id
require_safe_label = PATH_RULES.require_safe_label
require_branch_name = PATH_RULES.require_branch_name
relative_path_defect = PATH_RULES.relative_path_defect
safe_branch_name = PATH_RULES.safe_branch_name

PROTECTED_BRANCH_KEYS = (
    "id",
    "wave",
    "prompt",
    "branch_name",
    "worktree_path",
    "status_path",
    "review_path",
    "pre_review_gate_path",
    "depends_on",
    "owned_paths",
    "work_items",
    "max_active_worker_packets",
    "worker_parallelism",
    "recovers_from",
    "supersedes",
    "recovery_mode",
    "contention_reason",
    "worker_contention_reason",
)
NONPASS_TERMINAL_STATUSES = {"partial", "blocked", "failed"}
RUNTIME_BRIEF_PRESERVED_KEYS = (
    "repo_status",
    "preflight_compatibility",
    "preflight_input_precedence",
    "preflight_warnings",
    "execution_strategy",
)
RUNTIME_MANIFEST_PROVENANCE_KEYS = (
    "goal_config_path",
    "goal_config_sha256",
    "goal_config_summary",
    "goal_config_check_path",
    "goal_config_check_sha256",
    "goal_config_check_summary",
    "goal_config_provenance",
    "route_policy_degraded_telemetry_waiver",
)


def json_text(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def load_json_object(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def goal_config_from_manifest(manifest: dict | None, manifest_path: Path | None = None) -> dict | None:
    if isinstance(manifest, dict) and isinstance(manifest.get("goal_config"), dict):
        return manifest["goal_config"]
    if not isinstance(manifest, dict) or manifest_path is None:
        return None
    config_path = manifest.get("goal_config_path")
    if not isinstance(config_path, str) or not config_path.strip():
        return None
    candidate = (manifest_path.parent / config_path).resolve()
    if not candidate.is_file():
        return None
    return load_json_object(candidate)


def _model_items(value: object) -> list[tuple[str, dict]]:
    if isinstance(value, dict):
        return [(alias, spec) for alias, spec in value.items() if isinstance(alias, str) and isinstance(spec, dict)]
    if isinstance(value, list):
        result: list[tuple[str, dict]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            alias = item.get("role") or item.get("alias")
            if isinstance(alias, str) and alias.strip():
                result.append((alias, item))
        return result
    return []


def amender_model_specs(manifest: dict | None, manifest_path: Path | None = None) -> dict[str, dict]:
    specs: dict[str, dict] = {}
    if isinstance(manifest, dict):
        for alias, spec in _model_items(manifest.get("models")):
            specs[alias] = dict(spec)
        summary = manifest.get("goal_config_summary")
        if isinstance(summary, dict):
            for alias, spec in _model_items(summary.get("models")):
                specs[alias] = dict(spec)
    goal_config = goal_config_from_manifest(manifest, manifest_path)
    if isinstance(goal_config, dict):
        for alias, spec in _model_items(goal_config.get("models")):
            specs[alias] = dict(spec)
    return specs


def amender_harnesses(manifest: dict | None, manifest_path: Path | None = None) -> dict[str, dict]:
    goal_config = goal_config_from_manifest(manifest, manifest_path)
    harnesses = goal_config.get("harnesses") if isinstance(goal_config, dict) else None
    return (
        {key: value for key, value in harnesses.items() if isinstance(key, str) and isinstance(value, dict)}
        if isinstance(harnesses, dict)
        else {}
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def amender_model_policy(manifest: dict | None, manifest_path: Path | None = None) -> dict:
    policy = manifest.get("amender_model_policy") if isinstance(manifest, dict) else None
    if not isinstance(policy, dict):
        # deepcopy the shared module-level default so a caller mutating the result cannot corrupt
        # CONTRACT.AMENDER_MODEL_POLICY process-wide (matches the deepcopy convention used elsewhere).
        return copy.deepcopy(CONTRACT.AMENDER_MODEL_POLICY)
    validate_amender_model_policy(manifest, manifest_path)
    return policy


def validate_amender_model_policy(manifest: dict | None, manifest_path: Path | None = None) -> dict:
    policy = manifest.get("amender_model_policy") if isinstance(manifest, dict) else None
    if policy == CONTRACT.AMENDER_MODEL_POLICY:
        return copy.deepcopy(CONTRACT.AMENDER_MODEL_POLICY)
    defects: list[str] = []
    if not isinstance(policy, dict):
        raise ValueError("manifest amender_model_policy must be an object")
    default_ladder = _string_list(policy.get("default_ladder"))
    allowed_routes = _string_list(policy.get("allowed_routes"))
    if not default_ladder:
        defects.append("default_ladder must be a non-empty string array")
    if not allowed_routes:
        defects.append("allowed_routes must be a non-empty string array")
    if len(set(allowed_routes)) != len(allowed_routes):
        defects.append("allowed_routes must not contain duplicates")
    if len(set(default_ladder)) != len(default_ladder):
        defects.append("default_ladder must not contain duplicates")
    missing = [alias for alias in default_ladder if alias not in allowed_routes]
    if missing:
        defects.append("default_ladder aliases must be present in allowed_routes: " + ", ".join(missing))
    positions = [allowed_routes.index(alias) for alias in default_ladder if alias in allowed_routes]
    if positions != sorted(positions):
        defects.append("default_ladder must preserve allowed_routes order")
    if policy.get("launcher") != "goal-main-orchestrator":
        defects.append("launcher must be goal-main-orchestrator")
    if policy.get("selection_reason_required") is not True:
        defects.append("selection_reason_required must be true")
    if policy.get("sandbox") != "read-only":
        defects.append("sandbox must be read-only")
    timeout = policy.get("timeout_seconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        defects.append("timeout_seconds must be a positive integer")
    if not isinstance(policy.get("ordering_rule"), str) or not policy.get("ordering_rule", "").strip():
        defects.append("ordering_rule must be a non-empty string")
    specs = amender_model_specs(manifest, manifest_path)
    missing_specs = [
        alias for alias in allowed_routes if alias not in specs and alias not in CONTRACT.ALLOWED_AMENDER_ROUTES
    ]
    if missing_specs:
        defects.append(
            "allowed_routes aliases need goal-config model metadata or built-in Codex aliases: "
            + ", ".join(missing_specs)
        )
    if defects:
        raise ValueError(
            "manifest amender_model_policy is not compatible with plan-amender routing: " + "; ".join(defects)
        )
    return policy


def normalize_amender_ladder(manifest: dict | None, manifest_path: Path | None, values: list[str]) -> list[str]:
    policy = amender_model_policy(manifest, manifest_path)
    default_ladder = _string_list(policy.get("default_ladder")) or list(CONTRACT.DEFAULT_AMENDER_LADDER)
    allowed_routes = _string_list(policy.get("allowed_routes")) or list(CONTRACT.ALLOWED_AMENDER_ROUTES)
    if not values:
        return default_ladder
    specs = amender_model_specs(manifest, manifest_path)
    alias_to_route: dict[str, str] = {alias: alias for alias in allowed_routes}
    for route_alias in allowed_routes:
        spec = specs.get(route_alias)
        if not isinstance(spec, dict):
            continue
        for key in ("alias", "model"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                alias_to_route.setdefault(value.strip(), route_alias)
    flattened: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                flattened.append(item)
    if not flattened:
        raise ValueError("amender route must contain at least one route alias")
    normalized: list[str] = []
    seen: set[str] = set()
    positions: list[int] = []
    for item in flattened:
        route_alias = alias_to_route.get(item)
        if route_alias is None:
            raise ValueError(f"unsupported amender route alias: {item!r}")
        if route_alias in seen:
            raise ValueError(f"amender route alias repeated: {item!r}")
        seen.add(route_alias)
        normalized.append(route_alias)
        positions.append(allowed_routes.index(route_alias))
    if positions != sorted(positions):
        raise ValueError("amender route aliases must preserve allowed route order: " + ", ".join(allowed_routes))
    return normalized


def amender_event_label(alias: str) -> str:
    label = "".join(char if char.isalnum() or char in "._-" else "-" for char in alias).strip("-").lower()
    return label or "configured"


def bridge_amender_attempt(alias: str, *, timeout_seconds: int) -> dict:
    """Bridge (opencode-worker-bridge) plan-amender attempt for a deepseek route.

    The amender proposal is read-only (proposal-only): the bridge delegates a
    deepseek launch through scripts/opencode_worker.py under permission-profile
    read-only and we map the file-backed goal-delegator-* artifacts onto the
    telemetry schema. Token usage flows through events-<label>.jsonl; no USD.
    """
    model = CONTRACT.bridge_model(alias)
    variant = CONTRACT.bridge_variant(alias)
    label = CONTRACT.bridge_event_label(alias)
    permission_profile = "read-only"
    command = (
        "opencode_worker.py delegate "
        f"--provider {CONTRACT.BRIDGE_PROVIDER_ID} "
        f"--model {model} --variant {variant} "
        f"--permission-profile {permission_profile}"
    )
    return {
        "alias": alias,
        "provider": CONTRACT.BRIDGE_HARNESS_KIND,
        "provider_id": CONTRACT.BRIDGE_PROVIDER_ID,
        "model": model,
        "variant": variant,
        "harness": CONTRACT.BRIDGE_HARNESS_KIND,
        "harness_kind": CONTRACT.BRIDGE_HARNESS_KIND,
        "permission_profile": permission_profile,
        "command": command,
        "effort": variant,
        "sandbox": "read-only",
        "timeout_seconds": timeout_seconds,
        "event_logs": [f"events-{label}.jsonl"],
        "probe_logs": [],
        "bridge": {
            "provider": CONTRACT.BRIDGE_PROVIDER_ID,
            "model": model,
            "variant": variant,
            "permission_profile": permission_profile,
        },
    }


def amender_telemetry_attempts(
    manifest: dict | None, manifest_path: Path | None, selected_ladder: list[str]
) -> list[dict]:
    policy = amender_model_policy(manifest, manifest_path)
    timeout = policy.get("timeout_seconds")
    timeout_seconds = (
        timeout
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout > 0
        else CONTRACT.AMENDER_ATTEMPT_TIMEOUT_SECONDS
    )
    specs = amender_model_specs(manifest, manifest_path)
    harnesses = amender_harnesses(manifest, manifest_path)
    if not specs:
        attempts: list[dict] = []
        for alias in selected_ladder:
            if CONTRACT.is_bridge_alias(alias):
                attempts.append(bridge_amender_attempt(alias, timeout_seconds=timeout_seconds))
                continue
            attempts.extend(
                CONTRACT.codex_telemetry_attempts([alias], timeout_seconds=timeout_seconds, sandbox="read-only")
            )
        return attempts
    attempts = []
    for alias in selected_ladder:
        if CONTRACT.is_bridge_alias(alias) and not isinstance(specs.get(alias), dict):
            attempts.append(bridge_amender_attempt(alias, timeout_seconds=timeout_seconds))
            continue
        spec = specs.get(alias)
        if not isinstance(spec, dict):
            if alias in CONTRACT.ALLOWED_AMENDER_ROUTES:
                attempts.extend(
                    CONTRACT.codex_telemetry_attempts([alias], timeout_seconds=timeout_seconds, sandbox="read-only")
                )
                continue
            raise SystemExit(f"goal_config missing model role used by amender route ladder: {alias}")
        harness_name = spec.get("harness")
        harness = harnesses.get(harness_name) if isinstance(harness_name, str) else None
        kind = harness.get("kind") if isinstance(harness, dict) else harness_name
        label = amender_event_label(alias)
        event_suffix = "jsonl" if kind == "codex" else "log"
        command_binary = harness.get("command") if isinstance(harness, dict) else harness_name
        model = spec.get("model") or spec.get("alias") or alias
        command = f"{command_binary} {model}".strip()
        if kind == "codex":
            command = f"codex exec --ephemeral -m {model} -s read-only"
        attempts.append(
            {
                "alias": alias,
                "provider": kind,
                "provider_id": spec.get("provider"),
                "model": model,
                "harness": harness_name,
                "harness_kind": kind,
                "command_binary": command_binary,
                "command": command,
                "run_args": harness.get("run_args") or harness.get("smoke_args") or []
                if isinstance(harness, dict)
                else [],
                "run_readback": harness.get("run_readback", "stdout") if isinstance(harness, dict) else "stdout",
                "effort": "configured",
                "sandbox": "read-only",
                "timeout_seconds": timeout_seconds,
                "event_logs": [f"events-{label}.{event_suffix}"],
                "probe_logs": [],
            }
        )
    return attempts


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(data), encoding="utf-8")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def source_record(path: Path, label: str) -> dict:
    return {
        "label": label,
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def add_if_exists(records: list[dict], path: Path, label: str) -> None:
    if path.exists() and path.is_file():
        records.append(source_record(path, label))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def raw_sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(data: object) -> str:
    return sha256_text(json_text(data))


def amendment_lineage_path(bundle_dir: Path, amendment_id: str) -> Path:
    return bundle_dir / "amendments" / f"{amendment_id}.lineage.json"


def make_lineage_stage(
    stage: str,
    path: str,
    sha256: str,
    *,
    parent_sha256: str | None = None,
) -> dict[str, str]:
    record: dict[str, str] = {
        "stage": stage,
        "path": path,
        "sha256": sha256,
    }
    if parent_sha256 is not None:
        record["parent_sha256"] = parent_sha256
    return record


def init_lineage(amendment_id: str, *, schema_version: int = 1) -> dict:
    return {
        "schema_version": schema_version,
        "amendment_id": amendment_id,
        "stages": [],
    }


def _validate_lineage_stages(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    stages: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            stage = item.get("stage")
            path = item.get("path")
            sha = item.get("sha256")
            if isinstance(stage, str) and isinstance(path, str) and isinstance(sha, str):
                stages.append(item)
    return stages


def load_lineage(path: Path, amendment_id: str | None = None) -> dict:
    if not path.exists():
        return init_lineage(amendment_id or "", schema_version=1)
    data = load_json_object(path)
    if not isinstance(data, dict):
        return init_lineage(amendment_id or "", schema_version=1)
    stored_schema_version = data.get("schema_version", 1)
    if not isinstance(stored_schema_version, int):
        stored_schema_version = 1
    result = init_lineage(amendment_id or str(data.get("amendment_id", "")), schema_version=stored_schema_version)
    result["stages"] = _validate_lineage_stages(data.get("stages"))
    if isinstance(data.get("meta"), dict):
        result["meta"] = copy.deepcopy(data["meta"])
    return result


def add_lineage_stage(
    lineage: dict,
    *,
    stage: str,
    path: str,
    sha256: str,
    parent_sha256: str | None = None,
) -> dict:
    stages = lineage.setdefault("stages", [])
    if not isinstance(stages, list):
        stages = []
        lineage["stages"] = stages
    next_stage = make_lineage_stage(stage, path, sha256, parent_sha256=parent_sha256)
    for existing in stages:
        if (
            isinstance(existing, dict)
            and existing.get("stage") == stage
            and existing.get("path") == path
            and existing.get("sha256") == sha256
        ):
            return lineage
    stages.append(next_stage)
    return lineage


def latest_lineage_sha(lineage: dict) -> str | None:
    stages = _validate_lineage_stages(lineage.get("stages"))
    if not stages:
        return None
    last = stages[-1]
    value = last.get("sha256")
    return value if isinstance(value, str) else None


def lineage_path_rel(bundle_dir: Path, artifact_path: Path) -> str:
    try:
        return artifact_path.resolve().relative_to(bundle_dir.resolve()).as_posix()
    except Exception:
        return artifact_path.as_posix()


def branch_map(manifest: dict) -> dict[str, dict]:
    branches = manifest.get("branches", [])
    if not isinstance(branches, list):
        return {}
    result: dict[str, dict] = {}
    for branch in branches:
        if isinstance(branch, dict) and isinstance(branch.get("id"), str):
            result[branch["id"]] = branch
    return result


def branch_index(branches: list[dict], branch_id: str) -> int | None:
    for index, branch in enumerate(branches):
        if isinstance(branch, dict) and branch.get("id") == branch_id:
            return index
    return None


def branch_brief_from_manifest(branch: dict) -> dict:
    result = copy.deepcopy(branch)
    worker_parallelism = result.get("worker_parallelism")
    if isinstance(worker_parallelism, dict):
        result.setdefault("worker_serial_reasons", worker_parallelism.get("serial_reasons", []))
        result.setdefault("worker_parallelization_rationale", worker_parallelism.get("parallelization_rationale", ""))
    return result


def manifest_to_brief(manifest: dict) -> dict:
    parallelization = manifest.get("parallelization", {})
    if not isinstance(parallelization, dict):
        parallelization = {}
    brief = {
        "job_id": manifest.get("job_id"),
        "title": manifest.get("title") or manifest.get("job_id"),
        "base_ref": manifest.get("base_ref", "main"),
        "goal": manifest.get("goal", ""),
        "source_summary": manifest.get("source_summary", ""),
        "required_evidence": copy.deepcopy(manifest.get("required_evidence", [])),
        "final_dod": copy.deepcopy(manifest.get("final_dod", [])),
        "artifact_policy": manifest.get("artifact_policy", ""),
        "cleanup_policy": manifest.get("cleanup_policy", ""),
        "telemetry_policy": copy.deepcopy(manifest.get("telemetry_policy", CONTRACT.TELEMETRY_POLICY_DEFAULT)),
        "max_active_branch_agents": manifest.get("max_active_branch_agents", CONTRACT.MAX_ACTIVE_BRANCH_AGENTS),
        "serial_reasons": parallelization.get("serial_reasons", []),
        "parallelization_rationale": parallelization.get("parallelization_rationale", ""),
        "preflight_lite_advice": manifest.get("preflight_lite_advice", []),
        "source_attachments": copy.deepcopy(manifest.get("source_attachments", [])),
        "source_attachment_promotions": copy.deepcopy(manifest.get("source_attachment_promotions", [])),
        "runtime_cap": copy.deepcopy(manifest.get("runtime_cap")),
        "runtime_rules_path": manifest.get("runtime_rules_path"),
        "runtime_rules_sha256": manifest.get("runtime_rules_sha256"),
        "branches": [
            branch_brief_from_manifest(branch) for branch in manifest.get("branches", []) if isinstance(branch, dict)
        ],
    }
    for key in RUNTIME_BRIEF_PRESERVED_KEYS:
        if key in manifest:
            brief[key] = copy.deepcopy(manifest[key])
    return brief


def finalize_candidate_runtime_metadata(candidate: dict) -> dict:
    candidate["runtime_index_path"] = "runtime.index.json"
    runtime_index = PREFLIGHT.build_runtime_index(candidate)
    candidate["runtime_index_sha256"] = raw_sha256_text(PREFLIGHT.canonical_json_text(runtime_index))
    return runtime_index


def enrich_brief_runtime_metadata(brief: dict, candidate: dict, *, bundle_dir: Path | None = None) -> dict:
    enriched = copy.deepcopy(brief)
    if bundle_dir is not None:
        enriched["bundle_root"] = bundle_dir.resolve().as_posix()
    repo_status = candidate.get("repo_status") if isinstance(candidate.get("repo_status"), dict) else {}
    repo_root = repo_status.get("repo_root")
    if isinstance(repo_root, str) and repo_root:
        enriched["repo_root"] = repo_root
    for key in [
        "runtime_index_path",
        "runtime_index_sha256",
        "route_contract",
        "route_contract_sha256",
        "execution_strategy",
        "ownership_feasibility",
    ]:
        if key in candidate:
            enriched[key] = copy.deepcopy(candidate[key])
    return enriched


def write_runtime_index(bundle_dir: Path, candidate: dict) -> None:
    runtime_index = PREFLIGHT.build_runtime_index(candidate)
    expected_hash = candidate.get("runtime_index_sha256")
    actual_hash = raw_sha256_text(PREFLIGHT.canonical_json_text(runtime_index))
    if expected_hash != actual_hash:
        raise SystemExit("candidate runtime_index_sha256 does not match regenerated runtime.index.json")
    write_json(bundle_dir / "runtime.index.json", runtime_index)


def load_runtime_sidecar(bundle_dir: Path, relative_path: str) -> dict | None:
    if relative_path_defect(relative_path, relative_path):
        return None
    path = bundle_dir / relative_path
    if not path.is_file():
        return None
    return load_json_object(path)


def hydrate_brief_runtime_sidecars(brief: dict, manifest: dict, bundle_dir: Path | None) -> None:
    if bundle_dir is None:
        return
    if manifest.get("goal_config_path") == "goal.config.json":
        goal_config = load_runtime_sidecar(bundle_dir, "goal.config.json")
        if goal_config is not None:
            brief["goal_config"] = goal_config
    if manifest.get("goal_config_check_path") == "goal-config.check.json":
        goal_config_check = load_runtime_sidecar(bundle_dir, "goal-config.check.json")
        if goal_config_check is not None:
            brief["goal_config_check"] = goal_config_check


def preserve_runtime_manifest_provenance(source: dict, normalized: dict) -> None:
    for key in RUNTIME_MANIFEST_PROVENANCE_KEYS:
        if key in source and key not in normalized:
            normalized[key] = copy.deepcopy(source[key])


def prompt_regeneration_branch_ids(candidate: dict, protected_branch_ids: set[str]) -> list[str]:
    ids: list[str] = []
    protected = set(protected_branch_ids)
    for branch in candidate.get("branches", []):
        if not isinstance(branch, dict):
            continue
        branch_id = branch.get("id")
        if isinstance(branch_id, str) and branch_id not in protected:
            ids.append(branch_id)
    return ids


def normalize_candidate_manifest(manifest: dict, bundle_dir: Path | None = None) -> tuple[dict, dict]:
    brief = PREFLIGHT.normalize_brief(manifest_to_brief(manifest))
    hydrate_brief_runtime_sidecars(brief, manifest, bundle_dir)
    for key in [
        "source_attachments",
        "source_attachment_promotions",
        "runtime_cap",
        "runtime_rules_path",
        "runtime_rules_sha256",
    ]:
        if key in manifest:
            brief[key] = copy.deepcopy(manifest[key])
    normalized = PREFLIGHT.manifest_from_normalized_brief(brief, bundle_dir)
    preserve_runtime_manifest_provenance(manifest, normalized)
    for branch in normalized.get("branches", []) if isinstance(normalized.get("branches"), list) else []:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        source = branch_map(manifest).get(branch["id"])
        if not isinstance(source, dict):
            continue
        for key in ["recovers_from", "supersedes", "recovery_mode"]:
            if key in source:
                branch[key] = copy.deepcopy(source[key])
    for key in ["amendment_history", "obsolete_branches"]:
        if key in manifest:
            normalized[key] = copy.deepcopy(manifest[key])
    finalize_candidate_runtime_metadata(normalized)
    brief = enrich_brief_runtime_metadata(brief, normalized)
    return normalized, brief


def scheduler_state(manifest_path: Path, manifest: dict) -> tuple[set[str], dict[str, str]]:
    parallelization = manifest.get("parallelization", {})
    scheduler_path = (
        parallelization.get("scheduler_path") if isinstance(parallelization, dict) else CONTRACT.MAIN_SCHEDULER_PATH
    )
    if not isinstance(scheduler_path, str) or relative_path_defect(scheduler_path, "scheduler_path"):
        raise ValueError("manifest scheduler_path is unsafe; refusing to infer protected branch state")
    ledger_path = manifest_path.parent / scheduler_path
    if not ledger_path.exists():
        return set(), {}
    try:
        ledger = load_json_object(ledger_path)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- load_json_object fails closed via SystemExit
        raise ValueError(
            f"could not read scheduler ledger for protected branch inference: {ledger_path}: {exc}"
        ) from exc
    active: set[str] = set()
    finished_status: dict[str, str] = {}
    terminal: dict[str, str] = {}
    events = ledger.get("events", [])
    if not isinstance(events, list):
        raise ValueError(f"scheduler ledger events must be an array for protected branch inference: {ledger_path}")
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str):
            continue
        item_id = event["id"]
        name = event.get("event")
        if name == "launch":
            active.add(item_id)
            terminal.pop(item_id, None)
        elif (
            name == "finish"
            and isinstance(event.get("status"), str)
            and event.get("status") in CONTRACT.SCHEDULER_TERMINAL_STATUSES
        ):
            finished_status[item_id] = str(event["status"])
        elif name == "close":
            active.discard(item_id)
            if item_id in finished_status:
                terminal[item_id] = finished_status[item_id]
    return active, terminal


def status_file_terminal_state(manifest_path: Path, manifest: dict) -> dict[str, str]:
    statuses: dict[str, str] = {}
    bundle_dir = manifest_path.parent
    for branch_id, branch in branch_map(manifest).items():
        status_path = branch.get("status_path")
        if not isinstance(status_path, str) or relative_path_defect(status_path, f"branch {branch_id}.status_path"):
            continue
        path = bundle_dir / status_path
        if not path.exists():
            continue
        try:
            data = load_json_object(path)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 -- load_json_object fails closed via SystemExit
            raise ValueError(
                f"could not read terminal branch status artifact for protected branch inference: {path}: {exc}"
            ) from exc
        status = data.get("status")
        if status in CONTRACT.STATUSES:
            statuses[branch_id] = str(status)
    return statuses


def protected_ids(
    manifest_path: Path,
    manifest: dict,
    *,
    active_ids: list[str] | None = None,
    terminal_ids: list[str] | None = None,
    infer_scheduler: bool = True,
) -> tuple[set[str], set[str], dict[str, str]]:
    if not infer_scheduler:
        raise ValueError("scheduler/status inference is mandatory for amendment protected branch ids")
    active = set(active_ids or [])
    terminal_status: dict[str, str] = {branch_id: "terminal" for branch_id in (terminal_ids or [])}
    inferred_active, inferred_terminal = scheduler_state(manifest_path, manifest)
    active |= inferred_active
    terminal_status.update(inferred_terminal)
    status_terminal = status_file_terminal_state(manifest_path, manifest)
    stale_active = sorted(active & set(status_terminal))
    if stale_active:
        raise ValueError(
            "branch status artifacts mark scheduler-active branch ids as terminal; "
            f"refusing stale status overlap: {', '.join(stale_active)}"
        )
    terminal_status.update(status_terminal)
    overlap = sorted(active & set(terminal_status))
    if overlap:
        raise ValueError(
            f"branch ids cannot be both active and terminal for amendment protection: {', '.join(overlap)}"
        )
    return active, set(terminal_status), terminal_status


def ensure_amendment_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("amendment_id must be a non-empty string")
    return require_safe_id(value.strip().upper(), "amendment_id")


def require_list(value: object, field: str, defects: list[str], *, min_items: int = 0) -> list:
    if not isinstance(value, list):
        defects.append(f"{field} must be an array")
        return []
    if len(value) < min_items:
        defects.append(f"{field} must contain at least {min_items} item(s)")
    return value


def operation_branch_ids(operation: dict) -> set[str]:
    ids: set[str] = set()
    branch_id = operation.get("branch_id")
    if isinstance(branch_id, str):
        ids.add(branch_id)
    branch = operation.get("branch")
    if isinstance(branch, dict) and isinstance(branch.get("id"), str):
        ids.add(branch["id"])
    branches = operation.get("branches")
    if isinstance(branches, list):
        for item in branches:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.add(item["id"])
    return ids


def replace_dependency(branches: list[dict], old_id: str, replacement_ids: list[str]) -> None:
    for branch in branches:
        deps = branch.get("depends_on")
        if not isinstance(deps, list) or old_id not in deps:
            continue
        new_deps = []
        for dep in deps:
            if dep == old_id:
                for replacement in replacement_ids:
                    if replacement not in new_deps:
                        new_deps.append(replacement)
            elif dep not in new_deps:
                new_deps.append(dep)
        branch["depends_on"] = new_deps


def append_dependency(branch: dict, dependency: str) -> None:
    deps = branch.get("depends_on")
    if not isinstance(deps, list):
        deps = []
    if dependency not in deps:
        deps.append(dependency)
    branch["depends_on"] = deps


class OperationContext(NamedTuple):
    """Mutable accumulators shared by per-operation handlers.

    ``branches``, ``obsolete_entries`` and ``changed_branch_ids`` are mutated in
    place exactly as the original inlined loop body did; ``defects`` collects the
    same defect strings in the same append order.
    """

    branches: list
    obsolete_entries: list
    changed_branch_ids: set[str]
    defects: list[str]


def _apply_add_branch(ctx: OperationContext, operation: dict, path: str) -> None:
    branch = operation.get("branch")
    if not isinstance(branch, dict):
        ctx.defects.append(f"{path}.branch must be an object")
        return
    branch_id = branch.get("id")
    if not isinstance(branch_id, str):
        ctx.defects.append(f"{path}.branch.id must be a string")
        return
    if branch_index(ctx.branches, branch_id) is not None:
        ctx.defects.append(f"{path}.branch.id duplicates existing branch {branch_id}")
        return
    ctx.branches.append(branch)
    ctx.changed_branch_ids.add(branch_id)


def _apply_replace_unstarted_branch(
    ctx: OperationContext, operation: dict, path: str, *, branch_id: str, target_index: int
) -> None:
    branch = operation.get("branch")
    if not isinstance(branch, dict):
        ctx.defects.append(f"{path}.branch must be an object")
        return
    replacement_id = branch.get("id")
    if not isinstance(replacement_id, str):
        ctx.defects.append(f"{path}.branch.id must be a string")
        return
    existing_index = branch_index(ctx.branches, replacement_id)
    if existing_index is not None and existing_index != target_index:
        ctx.defects.append(f"{path}.branch.id duplicates existing branch {replacement_id}")
        return
    ctx.branches[target_index] = branch
    replace_dependency(ctx.branches[target_index + 1 :], branch_id, [replacement_id])
    ctx.changed_branch_ids.add(replacement_id)


def _validate_split_branches(ctx: OperationContext, operation: dict, path: str) -> list | None:
    """Validate the split ``branches`` array, appending defects in original order.

    Returns the new-branch list when structurally valid, otherwise ``None`` after
    appending exactly the same defect the inlined logic produced.
    """
    new_branches = require_list(operation.get("branches"), f"{path}.branches", ctx.defects, min_items=2)
    if len(new_branches) > CONTRACT.MAX_ACTIVE_BRANCH_AGENTS:
        ctx.defects.append(f"{path}.branches must contain at most {CONTRACT.MAX_ACTIVE_BRANCH_AGENTS} branches")
        return None
    if any(not isinstance(branch, dict) for branch in new_branches):
        ctx.defects.append(f"{path}.branches entries must be objects")
        return None
    replacement_ids = [branch.get("id") for branch in new_branches]
    if any(not isinstance(value, str) for value in replacement_ids):
        ctx.defects.append(f"{path}.branches[].id values must be strings")
        return None
    duplicate_ids = {value for value in replacement_ids if replacement_ids.count(value) > 1}
    if duplicate_ids:
        ctx.defects.append(f"{path}.branches contain duplicate ids: {', '.join(sorted(duplicate_ids))}")
        return None
    return new_branches


def _apply_split_unstarted_branch(
    ctx: OperationContext, operation: dict, path: str, *, branch_id: str, target_index: int
) -> None:
    new_branches = _validate_split_branches(ctx, operation, path)
    if new_branches is None:
        return
    replacement_ids = [branch.get("id") for branch in new_branches]
    defects_before = len(ctx.defects)
    for replacement_id in replacement_ids:
        existing_index = branch_index(ctx.branches, str(replacement_id))
        if existing_index is not None and existing_index != target_index:
            ctx.defects.append(f"{path}.branches id duplicates existing branch {replacement_id}")
    # Abort if THIS duplicate-id loop appended any defect. Tracking the count is robust; the prior
    # `ctx.defects[-1].startswith(path)` check could drop a valid split if a later edit appended an
    # unrelated defect, or pass it through on a pre-existing defect that happened to share the prefix.
    if len(ctx.defects) != defects_before:
        return
    ctx.branches[target_index : target_index + 1] = new_branches
    replace_dependency(
        ctx.branches[target_index + len(new_branches) :], branch_id, [str(value) for value in replacement_ids]
    )
    ctx.changed_branch_ids.update(str(value) for value in replacement_ids)


def _apply_add_dependency(ctx: OperationContext, operation: dict, path: str, *, branch_id: str, target: dict) -> None:
    dependencies = require_list(operation.get("depends_on"), f"{path}.depends_on", ctx.defects, min_items=1)
    for dependency in dependencies:
        if not isinstance(dependency, str):
            ctx.defects.append(f"{path}.depends_on entries must be strings")
            continue
        append_dependency(target, dependency)
    ctx.changed_branch_ids.add(branch_id)


def _apply_add_work_item(ctx: OperationContext, operation: dict, path: str, *, branch_id: str, target: dict) -> None:
    work_item = operation.get("work_item")
    if not isinstance(work_item, dict):
        ctx.defects.append(f"{path}.work_item must be an object")
        return
    work_items = target.get("work_items")
    if not isinstance(work_items, list):
        ctx.defects.append(f"{path}.branch work_items must be an array")
        return
    work_items.append(work_item)
    target["work_items"] = work_items
    ctx.changed_branch_ids.add(branch_id)


def _apply_mark_obsolete(
    ctx: OperationContext, operation: dict, path: str, *, branch_id: str, target_index: int
) -> None:
    referencing: list[str] = []
    for branch in ctx.branches:
        if not isinstance(branch, dict):
            continue
        if branch.get("id") == branch_id:
            continue
        for field in ("depends_on", "recovers_from", "supersedes"):
            refs = branch.get(field)
            if isinstance(refs, list) and branch_id in refs:
                referencing.append(f"{branch.get('id')} ({field})")
                break
    if referencing:
        ctx.defects.append(
            f"{path} cannot mark {branch_id} obsolete while other branches reference it via "
            f"depends_on/recovers_from/supersedes: {', '.join(str(item) for item in referencing)}"
        )
        return
    removed = ctx.branches.pop(target_index)
    ctx.obsolete_entries.append(
        {
            "branch_id": branch_id,
            "reason": operation.get("reason", "Marked obsolete by accepted amendment."),
            "archived_branch": removed,
        }
    )


def _resolve_target_branch_id(ctx: OperationContext, operation: dict, path: str) -> str | None:
    branch_id = operation.get("branch_id")
    if not isinstance(branch_id, str):
        ctx.defects.append(f"{path}.branch_id must be a string")
        return None
    if branch_index(ctx.branches, branch_id) is None:
        ctx.defects.append(f"{path}.branch_id does not exist in the manifest: {branch_id}")
        return None
    return branch_id


def _apply_one_operation(ctx: OperationContext, operation: dict, path: str, *, protected_branch_ids: set[str]) -> None:
    op = operation.get("op")
    if op not in CONTRACT.ADAPTATION_ALLOWED_OPERATIONS:
        ctx.defects.append(f"{path}.op must be one of {list(CONTRACT.ADAPTATION_ALLOWED_OPERATIONS)}")
        return
    touched_protected = sorted(operation_branch_ids(operation) & protected_branch_ids)
    if touched_protected:
        ctx.defects.append(f"{path} attempts to modify protected branch ids: {', '.join(touched_protected)}")
        return

    if op == "add_branch":
        _apply_add_branch(ctx, operation, path)
        return

    branch_id = _resolve_target_branch_id(ctx, operation, path)
    if branch_id is None:
        return
    target_index = branch_index(ctx.branches, branch_id)

    if op == "replace_unstarted_branch":
        _apply_replace_unstarted_branch(ctx, operation, path, branch_id=branch_id, target_index=target_index)
        return

    if op == "split_unstarted_branch":
        _apply_split_unstarted_branch(ctx, operation, path, branch_id=branch_id, target_index=target_index)
        return

    target = ctx.branches[target_index]
    if op == "add_dependency_to_unstarted_branch":
        _apply_add_dependency(ctx, operation, path, branch_id=branch_id, target=target)
        return

    if op == "add_work_item_to_unstarted_branch":
        _apply_add_work_item(ctx, operation, path, branch_id=branch_id, target=target)
        return

    if op == "mark_unstarted_branch_obsolete":
        _apply_mark_obsolete(ctx, operation, path, branch_id=branch_id, target_index=target_index)
        return


def apply_operations_to_manifest(
    manifest: dict,
    proposal: dict,
    *,
    protected_branch_ids: set[str],
    terminal_branch_statuses: dict[str, str] | None = None,
) -> tuple[dict | None, list[str], list[str]]:
    defects: list[str] = []
    candidate = copy.deepcopy(manifest)
    branches = candidate.get("branches")
    if not isinstance(branches, list):
        return None, [], ["manifest.branches must be an array"]
    changed_branch_ids: set[str] = set()
    obsolete_entries = candidate.setdefault("obsolete_branches", [])
    if not isinstance(obsolete_entries, list):
        defects.append("manifest.obsolete_branches must be an array when present")
        obsolete_entries = []
        candidate["obsolete_branches"] = obsolete_entries

    ctx = OperationContext(
        branches=branches,
        obsolete_entries=obsolete_entries,
        changed_branch_ids=changed_branch_ids,
        defects=defects,
    )
    operations = require_list(proposal.get("operations"), "operations", defects, min_items=1)
    for index, raw_operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(raw_operation, dict):
            defects.append(f"{path} must be an object")
            continue
        operation = copy.deepcopy(raw_operation)
        _apply_one_operation(ctx, operation, path, protected_branch_ids=protected_branch_ids)

    defects.extend(
        changed_branch_nonpass_dependency_defects(
            candidate,
            changed_branch_ids,
            terminal_branch_statuses or {},
        )
    )
    return (candidate if not defects else None), sorted(changed_branch_ids), defects


def changed_branch_nonpass_dependency_defects(
    candidate: dict,
    changed_branch_ids: set[str],
    terminal_branch_statuses: dict[str, str],
) -> list[str]:
    nonpass = {
        branch_id: status
        for branch_id, status in terminal_branch_statuses.items()
        if status in NONPASS_TERMINAL_STATUSES
    }
    if not nonpass:
        return []
    defects: list[str] = []
    branches = branch_map(candidate)
    for branch_id in sorted(changed_branch_ids):
        branch = branches.get(branch_id)
        if not isinstance(branch, dict):
            continue
        depends_on = branch.get("depends_on", [])
        if not isinstance(depends_on, list):
            continue
        blocked = [dep for dep in depends_on if isinstance(dep, str) and dep in nonpass]
        if blocked:
            details = ", ".join(f"{dep} ({nonpass[dep]})" for dep in blocked)
            defects.append(
                f"changed branch {branch_id} depends_on non-pass terminal branch ids: {details}; "
                "use recovers_from for recovery evidence instead of depends_on"
            )
    return defects


def protected_entries_unchanged(before: dict, after: dict, protected_branch_ids: set[str]) -> list[str]:
    defects: list[str] = []
    before_map = branch_map(before)
    after_map = branch_map(after)
    for branch_id in sorted(protected_branch_ids):
        before_branch = before_map.get(branch_id)
        after_branch = after_map.get(branch_id)
        if before_branch is None:
            continue
        if after_branch is None:
            defects.append(f"protected branch {branch_id} is missing from candidate manifest")
            continue
        for key in PROTECTED_BRANCH_KEYS:
            if before_branch.get(key) != after_branch.get(key):
                defects.append(f"protected branch {branch_id} changed immutable field {key}")
    return defects


def validate_candidate_with_lint(
    manifest_path: Path,
    candidate: dict,
    normalized_brief: dict,
    *,
    prompt_branch_ids: list[str],
) -> list[str]:
    bundle_dir = manifest_path.parent
    with tempfile.TemporaryDirectory(prefix="goal-amender-lint-") as tmp:
        tmp_bundle = Path(tmp) / "bundle"
        shutil.copytree(bundle_dir, tmp_bundle)
        write_json(tmp_bundle / "job.manifest.json", candidate)
        write_runtime_index(tmp_bundle, candidate)
        PREFLIGHT.write_bundle_prompts(
            enrich_brief_runtime_metadata(normalized_brief, candidate, bundle_dir=tmp_bundle),
            tmp_bundle,
            branch_ids=set(prompt_branch_ids),
            write_main=False,
        )
        lint = PREFLIGHT.lint_bundle(tmp_bundle, write_output=False)
    defects = []
    for item in lint.get("defects", []) if isinstance(lint, dict) else []:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        if isinstance(severity, str) and severity in {"critical", "major"}:
            defects.append(f"candidate lint {severity}: {item.get('file')}: {item.get('message')}")
    return defects


def validate_proposal(
    *,
    manifest_path: Path,
    proposal_path: Path,
    active_branch_ids: list[str] | None = None,
    terminal_branch_ids: list[str] | None = None,
    infer_scheduler: bool = True,
    run_lint: bool = True,
) -> tuple[dict, dict | None, dict | None]:
    manifest = load_json_object(manifest_path)
    proposal = load_json_object(proposal_path)
    defects: list[str] = []
    try:
        active, terminal, terminal_status = protected_ids(
            manifest_path,
            manifest,
            active_ids=active_branch_ids,
            terminal_ids=terminal_branch_ids,
            infer_scheduler=infer_scheduler,
        )
    except ValueError as exc:
        active = set(active_branch_ids or [])
        terminal_status = {branch_id: "terminal" for branch_id in (terminal_branch_ids or [])}
        terminal = set(terminal_status)
        defects.append(str(exc))
    protected = active | terminal
    if active & terminal:
        defects.append(
            "active_branch_ids and terminal_branch_ids must not overlap: " + ", ".join(sorted(active & terminal))
        )
    if not terminal:
        defects.append("at least one terminal branch id is required to validate a manifest amendment")

    try:
        amendment_id = ensure_amendment_id(proposal.get("amendment_id"))
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- ensure_amendment_id->require_safe_id raises SystemExit
        amendment_id = ""
        defects.append(str(exc))
    if proposal.get("schema_version") != 1:
        defects.append("schema_version must be 1")
    if proposal.get("job_id") != manifest.get("job_id"):
        defects.append("job_id must match manifest job_id")
    if not isinstance(proposal.get("rationale"), str) or not proposal.get("rationale", "").strip():
        defects.append("rationale must be a non-empty string")
    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        defects.append("manifest adaptation_policy does not match the shared amendment proposal policy")
    try:
        validate_amender_model_policy(manifest, manifest_path)
    except (ValueError, SystemExit) as exc:  # validate_amender_model_policy -> load_json_object raises SystemExit
        defects.append(str(exc))

    candidate = None
    normalized_brief = None
    changed_branch_ids: list[str] = []
    if not defects:
        candidate, changed_branch_ids, op_defects = apply_operations_to_manifest(
            manifest,
            proposal,
            protected_branch_ids=protected,
            terminal_branch_statuses=terminal_status,
        )
        defects.extend(op_defects)

    if candidate is not None and not defects:
        try:
            candidate, normalized_brief = normalize_candidate_manifest(candidate, manifest_path.parent)
        except SystemExit as exc:
            defects.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            defects.append(f"candidate normalization failed: {exc}")

    if candidate is not None and not defects:
        defects.extend(protected_entries_unchanged(manifest, candidate, protected))

    if candidate is not None and normalized_brief is not None and not defects and run_lint:
        prompt_branch_ids = prompt_regeneration_branch_ids(candidate, protected)
        defects.extend(
            validate_candidate_with_lint(
                manifest_path,
                candidate,
                normalized_brief,
                prompt_branch_ids=prompt_branch_ids,
            )
        )

    status = "pass" if not defects else "failed"
    result = {
        "schema_version": 1,
        "amendment_id": amendment_id or proposal.get("amendment_id"),
        "status": status,
        "manifest": manifest_path.as_posix(),
        "proposal": proposal_path.as_posix(),
        "manifest_sha256_before": sha256_file(manifest_path),
        "proposal_sha256": sha256_file(proposal_path),
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "protected_branch_ids": sorted(protected),
        "changed_branch_ids": changed_branch_ids,
        "candidate_branch_ids": [
            branch["id"]
            for branch in candidate.get("branches", [])
            if isinstance(branch, dict) and isinstance(branch.get("id"), str)
        ]
        if isinstance(candidate, dict) and isinstance(candidate.get("branches"), list)
        else [],
        "candidate_manifest_sha256": canonical_sha256(candidate) if candidate is not None and not defects else None,
        "defects": defects,
    }
    return result, candidate if status == "pass" else None, normalized_brief if status == "pass" else None
