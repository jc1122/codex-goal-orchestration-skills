#!/usr/bin/env python3
"""Create model-aware worker, research-worker, or reviewer packets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import NamedTuple


def _load_shared_script(module_name: str, script_name: str, label: str):
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / script_name
    if not path.exists():
        raise SystemExit(f"missing {label}: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {label}: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_shared_script(
    "goal_shared_orchestration_contract", "orchestration_contract.py", "shared orchestration contract"
)
STATUS_VALIDATION = _load_shared_script(
    "goal_shared_status_validation", "status_validation.py", "shared status validation helpers"
)
CONTEXT_PACK = _load_shared_script("goal_shared_context_pack", "context_pack.py", "shared context pack helper")
BRANCH_VALIDATOR = None
BRIDGE_HARNESS_KIND = CONTRACT.BRIDGE_HARNESS_KIND
BRIDGE_PROVIDER_ID = CONTRACT.BRIDGE_PROVIDER_ID
# Bridge launches delegate deepseek work through the opencode-worker-bridge
# control script `opencode_worker.py`. Permission profiles are role-scoped:
# workers get workspace-write, read-only roles (reviewer/research/audit/etc.)
# get read-only.
BRIDGE_WORKER_PERMISSION_PROFILE = "workspace-write"
BRIDGE_READONLY_PERMISSION_PROFILE = "read-only"
BRIDGE_POOL_MAX_WORKERS = 4
BRIDGE_RUN_DIR_PARENT = "bridge"
BRIDGE_POOL_DIR = "bridge/pool"
RESEARCH_ALIAS = CONTRACT.RESEARCH_ALIASES[0]
RESEARCH_FALLBACK_ALIAS = CONTRACT.RESEARCH_ALIASES[1]
WORKER_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.WORKER_ATTEMPT_TIMEOUT_SECONDS
RESEARCH_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.RESEARCH_ATTEMPT_TIMEOUT_SECONDS
REVIEWER_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.REVIEWER_ATTEMPT_TIMEOUT_SECONDS
TIMEOUT_KILL_AFTER_SECONDS = CONTRACT.TIMEOUT_KILL_AFTER_SECONDS
WORKER_STATUS_BEGIN = "BEGIN_WORKER_STATUS_JSON"
WORKER_STATUS_END = "END_WORKER_STATUS_JSON"
REVIEW_STATUS_BEGIN = "BEGIN_REVIEW_JSON"
REVIEW_STATUS_END = "END_REVIEW_JSON"
MAX_CONTEXT_PACK_CHARS = CONTEXT_PACK.DEFAULT_TOTAL_CHARS
MAX_CONTEXT_FILE_CHARS = CONTEXT_PACK.DEFAULT_PER_FILE_CHARS
MAX_PACKET_PROMPT_CHARS = 40000
APPROX_CHARS_PER_TOKEN = 4
DEFAULT_WORKER_LADDER = CONTRACT.DEFAULT_WORKER_LADDER
DEFAULT_WORKER_ROUTE_CLASS = CONTRACT.DEFAULT_WORKER_ROUTE_CLASS
ROUTE_POLICY_VERSION = "goal-route-policy-v2"
WORKER_ROUTE_CLASSES = CONTRACT.WORKER_ROUTE_CLASSES
WORKER_ROUTE_CLASS_LADDERS = CONTRACT.WORKER_ROUTE_CLASS_LADDERS
CODEX_LEAN_EXEC_FLAGS_TEXT = " ".join(CONTRACT.CODEX_LEAN_EXEC_FLAGS)
WORKER_ROUTE_EVENT_LABELS = {
    "ds-pro-max": "ds-pro-max",
    "ds-flash-max": "ds-flash-max",
    "codex-spark": "spark",
    "codex-mini": "mini",
}
CODEX_WORKER_ROUTES = frozenset({"codex-spark", "codex-mini"})
WORKER_PACKET_PROMPT = "Follow the complete worker packet instructions provided on stdin."


PATH_RULES = _load_shared_script("goal_shared_path_rules", "path_rules.py", "shared path rules")
require_safe_label = PATH_RULES.require_safe_packet_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
safe_branch_name = PATH_RULES.safe_branch_name
shell_quote = CONTRACT.shell_quote


def branch_validator():
    global BRANCH_VALIDATOR
    if BRANCH_VALIDATOR is None:
        path = Path(__file__).resolve().parent / "validate_branch_status.py"
        spec = importlib.util.spec_from_file_location("goal_branch_validate_branch_status", path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"could not load branch status validator: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        BRANCH_VALIDATOR = module
    return BRANCH_VALIDATOR


def nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_owned_paths(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        normalized.append(PATH_RULES.require_relative_path(value, "owned paths"))
    return normalized


def normalize_context_files(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        path = resolve_absolute_path(value, "--context-file", must_exist=True)
        normalized.append(path.as_posix())
    return normalized


def _validate_file_in_base(raw: object, base: Path, *, label: str, allow_escape: bool = False) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path_text = raw.strip()
    if "\\" in path_text:
        return f"{label} must use POSIX path separators: {path_text!r}"
    path = Path(path_text)
    resolved = path if path.is_absolute() else (base / path).resolve()
    if not allow_escape and not path.is_absolute():
        try:
            resolved.relative_to(base.resolve())
        except ValueError:
            return f"{label} must stay within base directory {base.as_posix()}; got {path_text!r}"
    if not resolved.is_file():
        return f"{label} does not exist as a file: {path_text!r} (resolved: {resolved.as_posix()})"
    return None


def validate_runtime_context_inputs(
    *,
    packet_id: str,
    worktree: Path,
    context_files: list[str],
    manifest: dict | None,
    manifest_branch_id: str,
    manifest_path: Path | None,
    manifest_work_item: dict | None,
) -> None:
    defects: list[str] = []
    for context_file in context_files:
        defect = _validate_file_in_base(context_file, worktree, label=f"{packet_id} context file {context_file}")
        if defect is not None:
            defects.append(defect)

    if manifest_work_item is not None:
        manifest_context_files = compact_list(manifest_work_item.get("context_files"))
        for context_file in manifest_context_files:
            defect = _validate_file_in_base(
                context_file,
                worktree,
                label=f"{packet_id} manifest context file {context_file}",
                allow_escape=True,
            )
            if defect is not None:
                defects.append(defect)

    if manifest is not None and manifest_path is not None:
        branch = branch_entry(manifest, manifest_branch_id)
        if isinstance(branch, dict):
            dependency_base = manifest_path.parent
            for dep_id in compact_list(branch.get("depends_on")):
                dependency = branch_entry(manifest, dep_id)
                if not isinstance(dependency, dict):
                    continue
                for field in ("status_path", "review_path", "pre_review_gate_path"):
                    dependency_path = dependency.get(field)
                    if isinstance(dependency_path, str):
                        defect = _validate_file_in_base(
                            dependency_path,
                            dependency_base,
                            label=f"{packet_id} dependency {dep_id} {field}",
                            allow_escape=False,
                        )
                        if defect is not None:
                            defects.append(defect)

    if defects:
        raise SystemExit("runtime packet pre-launch validation failed:\n" + "\n".join(f"- {item}" for item in defects))


def normalize_worker_ladder(
    values: list[str],
    *,
    default_ladder: list[str] | None = None,
    allowed_routes: list[str] | None = None,
) -> list[str]:
    default_ladder = default_ladder or list(DEFAULT_WORKER_LADDER)
    allowed_routes = allowed_routes or list(default_ladder)
    if not values:
        return list(default_ladder)
    flattened = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                flattened.append(item)
    if not flattened:
        raise SystemExit("worker route must contain at least one route alias")
    seen = set()
    positions = []
    for alias in flattened:
        if alias not in allowed_routes:
            raise SystemExit(f"unsupported worker route alias: {alias!r}")
        if alias in seen:
            raise SystemExit(f"worker route alias repeated: {alias!r}")
        seen.add(alias)
        positions.append(
            default_ladder.index(alias)
            if alias in default_ladder
            else len(default_ladder) + allowed_routes.index(alias)
        )
    if positions != sorted(positions):
        raise SystemExit("worker route aliases must preserve standard ladder order: " + ", ".join(default_ladder))
    return flattened


def normalize_route_class(value: object, *, allow_custom: bool = True) -> str:
    if value is None:
        return DEFAULT_WORKER_ROUTE_CLASS
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"route class must be one of {', '.join(WORKER_ROUTE_CLASSES)}")
    normalized = value.strip()
    allowed = WORKER_ROUTE_CLASSES if allow_custom else tuple(item for item in WORKER_ROUTE_CLASSES if item != "custom")
    if normalized not in allowed:
        raise SystemExit(f"route class must be one of {', '.join(allowed)}")
    return normalized


def worker_policy_from_manifest(manifest: dict | None) -> dict:
    if isinstance(manifest, dict) and isinstance(manifest.get("worker_model_policy"), dict):
        return manifest["worker_model_policy"]
    return CONTRACT.WORKER_MODEL_POLICY


def worker_policy_is_manifest_configured(manifest: dict | None, policy: dict) -> bool:
    if not isinstance(manifest, dict):
        return False
    if isinstance(manifest.get("worker_model_policy"), dict):
        return True
    return policy.get("source") == "goal_config"


def goal_config_from_manifest(manifest: dict | None, manifest_path: Path | None = None) -> dict | None:
    if isinstance(manifest, dict) and isinstance(manifest.get("goal_config"), dict):
        return manifest["goal_config"]
    if isinstance(manifest, dict) and manifest_path is not None:
        config_path = manifest.get("goal_config_path")
        if isinstance(config_path, str) and config_path.strip():
            candidate = (manifest_path.parent / config_path).resolve()
            if candidate.is_file():
                return load_json(candidate)
    return None


def policy_default_ladder(policy: dict) -> list[str]:
    ladder = policy.get("default_ladder")
    return list(ladder) if isinstance(ladder, list) and ladder else list(DEFAULT_WORKER_LADDER)


def policy_allowed_routes(policy: dict) -> list[str]:
    routes = policy.get("allowed_routes")
    return list(routes) if isinstance(routes, list) and routes else policy_default_ladder(policy)


def ladder_for_route_class(route_class: str, policy: dict | None = None) -> list[str]:
    if policy is None:
        return list(WORKER_ROUTE_CLASS_LADDERS.get(route_class, WORKER_ROUTE_CLASS_LADDERS[DEFAULT_WORKER_ROUTE_CLASS]))
    route_classes = policy.get("route_classes") if isinstance(policy.get("route_classes"), dict) else {}
    ladder = route_classes.get(route_class)
    if isinstance(ladder, list) and ladder:
        return list(ladder)
    return policy_default_ladder(policy)


def default_selection_reason(route_class: str) -> str:
    return CONTRACT.worker_route_class_reason(route_class)


def validate_route_class_selection(
    route_class: str, selected_ladder: list[str], selection_reason: str, policy: dict | None = None
) -> None:
    if policy is not None:
        allowed = set(ladder_for_route_class(route_class, policy))
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            raise SystemExit(
                f"route_class {route_class!r} cannot use configured route aliases: " + ", ".join(disallowed)
            )
        return
    if route_class in {"mechanical", "docs", "small-edit", "normal-code"}:
        allowed = set(ladder_for_route_class(route_class))
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            raise SystemExit(
                f"route_class {route_class!r} cannot use premium/full worker route aliases: " + ", ".join(disallowed)
            )
    if route_class == "complex-code":
        reason = selection_reason.lower()
        markers = ("complex", "risk", "cross-module", "premium", "architecture", "validator", "scheduler")
        if not any(marker in reason for marker in markers):
            raise SystemExit(
                "--selection-reason for route_class 'complex-code' must include a concrete cost/risk justification"
            )


def is_ordered_subsequence(values: list[str], ladder: list[str]) -> bool:
    if not values:
        return False
    position = 0
    for alias in ladder:
        if position < len(values) and values[position] == alias:
            position += 1
    return position == len(values)


def restore_configured_ladder_when_unpruned(
    selected_ladder: list[str],
    *,
    route_class: str,
    worker_policy: dict,
    explicit_routes: bool,
    allow_route_pruning: bool,
) -> tuple[list[str], bool]:
    if not explicit_routes or allow_route_pruning:
        return selected_ladder, False
    configured = ladder_for_route_class(route_class, worker_policy)
    if len(selected_ladder) >= len(configured):
        return selected_ladder, False
    if not is_ordered_subsequence(selected_ladder, configured):
        return selected_ladder, False
    return configured, True


def explicit_route_would_prune_configured_ladder(
    selected_ladder: list[str],
    *,
    route_class: str,
    worker_policy: dict,
    explicit_routes: bool,
) -> bool:
    if not explicit_routes:
        return False
    configured = ladder_for_route_class(route_class, worker_policy)
    return len(selected_ladder) < len(configured) and is_ordered_subsequence(selected_ladder, configured)


def validate_route_pruning_reason(selection_reason: str) -> None:
    reason = selection_reason.lower()
    markers = (
        "operator",
        "user",
        "explicit request",
        "model catalog",
        "unavailable",
        "unsupported",
        "route health",
        "route-health",
        "provider",
        "harness",
        "auth",
        "401",
        "403",
        "transport",
        "disconnect",
        "empty output",
        "capacity",
        "quota",
        "runtime cap",
        "budget",
        "timeout",
        "degraded",
        "smoke failure",
        "previous failure",
        "prior failure",
        "retry after",
    )
    if not any(marker in reason for marker in markers):
        raise SystemExit(
            "--allow-route-pruning requires --selection-reason to state a concrete external reason "
            "such as operator choice, route health, model catalog unavailability, provider failure, "
            "timeout, or budget cap"
        )


def route_policy_metadata(
    *,
    route_class: str,
    worker_policy: dict,
    explicit_routes: bool,
    allow_route_pruning: bool,
    pruned_configured_ladder: bool,
    restored_configured_ladder: bool,
) -> dict:
    return {
        "source": worker_policy.get("source", "default"),
        "policy_version": worker_policy.get("version", ROUTE_POLICY_VERSION),
        "route_class": route_class,
        "route_class_ladder": ladder_for_route_class(route_class, worker_policy),
        "default_ladder": policy_default_ladder(worker_policy),
        "allowed_routes": policy_allowed_routes(worker_policy),
        "explicit_worker_routes": explicit_routes,
        "allow_route_pruning": allow_route_pruning,
        "pruned_configured_ladder": pruned_configured_ladder,
        "restored_configured_ladder": restored_configured_ladder,
    }


def model_catalog_rows(path: Path) -> tuple[dict, dict[str, dict]]:
    data = load_json(path)
    if data.get("schema_version") != 1:
        raise SystemExit(f"model catalog schema_version must be 1: {path}")
    if data.get("status") != "pass":
        raise SystemExit(f"model catalog status must be pass before worker packet generation: {path}")
    rows = data.get("route_models")
    if not isinstance(rows, list):
        raise SystemExit(f"model catalog route_models must be a list: {path}")
    by_alias: dict[str, dict] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"model catalog route_models[{index}] must be an object: {path}")
        alias = row.get("alias")
        if isinstance(alias, str) and alias:
            by_alias[alias] = row
    return data, by_alias


def apply_model_catalog_to_worker_ladder(
    selected_ladder: list[str],
    *,
    catalog_path: Path | None,
    explicit_routes: bool,
) -> tuple[list[str], dict | None]:
    if catalog_path is None:
        return selected_ladder, None

    data, rows = model_catalog_rows(catalog_path)
    retained: list[str] = []
    filtered: list[dict] = []
    defects: list[str] = []
    checked_aliases = [alias for alias in selected_ladder if alias in CODEX_WORKER_ROUTES]
    for alias in selected_ladder:
        if alias not in CODEX_WORKER_ROUTES:
            retained.append(alias)
            continue
        row = rows.get(alias)
        if row is None:
            defects.append(f"{alias}: missing from model catalog route_models")
            continue
        present = row.get("present")
        supported = row.get("supported_in_api")
        if present is True and supported is True:
            retained.append(alias)
            continue
        detail = {
            "alias": alias,
            "model": row.get("model"),
            "present": present,
            "supported_in_api": supported,
            "reason": "not present" if present is not True else "not supported_in_api",
        }
        if explicit_routes:
            defects.append(f"{alias}: model={detail['model']} present={present} supported_in_api={supported}")
        else:
            filtered.append(detail)

    if defects:
        raise SystemExit(
            "model catalog rejects selected worker route(s); choose a supported route or omit explicit routes:\n"
            + "\n".join(f"- {item}" for item in defects)
        )
    if not retained:
        raise SystemExit(
            "model catalog removed every selected worker route; choose a supported worker route explicitly"
        )
    metadata = {
        "path": catalog_path.as_posix(),
        "sha256": CONTEXT_PACK.sha256_file(catalog_path),
        "source": data.get("source"),
        "status": data.get("status"),
        "checked_aliases": checked_aliases,
        "filtered_aliases": filtered,
    }
    return retained, metadata


def event_label_for_alias(alias: str) -> str:
    if alias in WORKER_ROUTE_EVENT_LABELS:
        return WORKER_ROUTE_EVENT_LABELS[alias]
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", alias).strip("-").lower()
    return label or "configured"


def render_attempt_args(args: object, *, context: dict[str, str]) -> list[str]:
    if not isinstance(args, list):
        return []
    rendered: list[str] = []
    for item in args:
        if isinstance(item, str):
            rendered.append(item.format(**context))
    return rendered


def configured_route_commands(selected_ladder: list[str], goal_config: dict) -> list[str]:
    models = goal_config.get("models") if isinstance(goal_config.get("models"), dict) else {}
    harnesses = goal_config.get("harnesses") if isinstance(goal_config.get("harnesses"), dict) else {}
    commands: list[str] = []
    for alias in selected_ladder:
        model = models.get(alias)
        model = model if isinstance(model, dict) else {}
        harness_name = model.get("harness")
        harness_name = harness_name if isinstance(harness_name, str) else ""
        harness = harnesses.get(harness_name, {})
        command = harness.get("command", harness_name)
        args = harness.get("run_args") or harness.get("smoke_args") or []
        rendered = render_attempt_args(
            args,
            context={
                "alias": alias,
                "model": str(model.get("model", "")),
                "provider": str(model.get("provider", "")),
                "role": alias,
                "packet_id": "<packet_id>",
                "worktree": "<worktree>",
                "prompt": "<prompt>",
                "prompt_file": "<prompt.md>",
                "schema_file": "<schema.json>",
                "output_file": "<output.json>",
                "packet_dir": "<packet_dir>",
            },
        )
        commands.append(" ".join([str(command), *rendered]).strip())
    return commands


def bridge_permission_profile(role: str) -> str:
    """Role -> bridge permission profile.

    Workers edit their worktree (workspace-write); every read-only role
    (reviewer, research, audit, plan-amender, lite-advisor, ...) is read-only.
    """
    return BRIDGE_WORKER_PERMISSION_PROFILE if role == "worker" else BRIDGE_READONLY_PERMISSION_PROFILE


def bridge_run_dir_rel(alias: str) -> str:
    return f"{BRIDGE_RUN_DIR_PARENT}/{alias}"


def bridge_run_args(alias: str, *, run_dir_rel: str, retry: bool) -> list[str]:
    """Templated human-renderable command string args for the bridge launch.

    The runner drives the bridge command sequence directly (pool-acquire ->
    start -> delegate/supervisor -> stop -> pool-release); these args exist so
    configured_route_commands / golden can render a stable human command.
    """
    sub = "supervisor" if retry else "delegate"
    args = [
        "{bridge_root}/scripts/opencode_worker.py",
        sub,
        "--provider",
        BRIDGE_PROVIDER_ID,
        "--model",
        CONTRACT.bridge_model(alias),
        "--variant",
        CONTRACT.bridge_variant(alias),
        "--run-dir",
        "{packet_dir}/" + run_dir_rel,
    ]
    return args


def bridge_telemetry_attempt(
    alias: str,
    *,
    role: str,
    timeout_seconds: int,
    sandbox: str,
    retry: bool = False,
) -> dict:
    """Build a single BRIDGE attempt dict (sibling of the native builder).

    A bridge attempt delegates a deepseek launch through opencode_worker.py.
    The launch plan drives pool-acquire -> start -> delegate (or supervisor for
    the retry loop) -> stop -> pool-release with a per-packet run_dir/pool_dir.
    """
    label = CONTRACT.bridge_event_label(alias)
    run_dir_rel = bridge_run_dir_rel(alias)
    profile = bridge_permission_profile(role)
    model = CONTRACT.bridge_model(alias)
    variant = CONTRACT.bridge_variant(alias)
    run_args = bridge_run_args(alias, run_dir_rel=run_dir_rel, retry=retry)
    sub = "supervisor" if retry else "delegate"
    command = (
        f"python3 {{bridge_root}}/scripts/opencode_worker.py {sub} "
        f"--provider {BRIDGE_PROVIDER_ID} --model {model} --variant {variant} "
        f"--permission-profile {profile} --run-dir {{packet_dir}}/{run_dir_rel}"
    )
    return {
        "alias": alias,
        "provider": BRIDGE_HARNESS_KIND,
        "provider_id": BRIDGE_PROVIDER_ID,
        "model": model,
        "variant": variant,
        "harness": BRIDGE_HARNESS_KIND,
        "harness_kind": BRIDGE_HARNESS_KIND,
        "command_binary": "python3",
        "command": command,
        "run_args": run_args,
        "run_readback": "bridge_run_dir",
        "effort": "configured",
        "sandbox": sandbox,
        "timeout_seconds": timeout_seconds,
        "event_logs": [f"events-{label}.jsonl"],
        "probe_logs": [],
        "status_markers": {
            "begin": WORKER_STATUS_BEGIN,
            "end": WORKER_STATUS_END,
        },
        "bridge": {
            "provider": BRIDGE_PROVIDER_ID,
            "model": model,
            "variant": variant,
            "permission_profile": profile,
            "run_dir": run_dir_rel,
            "pool_dir": BRIDGE_POOL_DIR,
            "pool_max_workers": BRIDGE_POOL_MAX_WORKERS,
            "prompt_file": "prompt.md",
            "supervisor": bool(retry),
        },
    }


def _harness_variant(harness: dict, default: str = "max") -> str:
    """Extract the ``--variant`` value from a harness ``run_args`` template.

    The goal-config opencode-bridge harness encodes the launch variant inline in
    ``run_args`` (``--variant max``). When absent we fall back to ``max`` -- the
    bridge launch contract used by the contract-alias builder.
    """
    run_args = harness.get("run_args")
    if isinstance(run_args, list):
        for index, item in enumerate(run_args):
            if item == "--variant" and index + 1 < len(run_args):
                candidate = run_args[index + 1]
                if isinstance(candidate, str) and candidate.strip() and "{" not in candidate:
                    return candidate.strip()
    return default


def configured_bridge_block(
    *,
    model: dict,
    harness: dict,
    role: str,
    alias: str,
) -> dict:
    """Build a ``bridge`` block for a configured opencode-bridge role.

    Mirrors the contract-alias bridge block (``bridge_telemetry_attempt``) but
    sources model/variant/provider from the goal-config model+harness rather than
    the contract route tables, so non-contract aliases (e.g. ``lite_agent``) carry
    the bridge block the launch-config adapter requires.
    """
    return {
        "provider": BRIDGE_PROVIDER_ID,
        "model": model.get("model"),
        "variant": _harness_variant(harness),
        "permission_profile": bridge_permission_profile(role),
        "run_dir": bridge_run_dir_rel(alias),
        "pool_dir": BRIDGE_POOL_DIR,
        "pool_max_workers": BRIDGE_POOL_MAX_WORKERS,
        "prompt_file": "prompt.md",
        "supervisor": False,
    }


def configured_telemetry_attempts(
    selected_ladder: list[str],
    goal_config: dict,
    *,
    timeout_seconds: int,
    sandbox: str,
    role: str = "worker",
) -> list[dict]:
    models = goal_config.get("models") if isinstance(goal_config.get("models"), dict) else {}
    harnesses = goal_config.get("harnesses") if isinstance(goal_config.get("harnesses"), dict) else {}
    attempts: list[dict] = []
    for alias in selected_ladder:
        if CONTRACT.is_bridge_alias(alias):
            attempts.append(
                bridge_telemetry_attempt(
                    alias,
                    role=role,
                    timeout_seconds=timeout_seconds,
                    sandbox=sandbox,
                )
            )
            continue
        model = models.get(alias)
        if not isinstance(model, dict):
            raise SystemExit(f"goal_config missing model role used by route ladder: {alias}")
        harness_name = model.get("harness")
        harness = harnesses.get(harness_name) if isinstance(harness_name, str) else None
        if not isinstance(harness, dict):
            raise SystemExit(f"goal_config model {alias} references unknown harness: {harness_name}")
        kind = harness.get("kind")
        label = event_label_for_alias(alias)
        event_suffix = "jsonl" if kind in {"codex", BRIDGE_HARNESS_KIND} else "log"
        attempt = {
            "alias": alias,
            "provider": kind,
            "provider_id": model.get("provider"),
            "model": model.get("model"),
            "harness": harness_name,
            "harness_kind": kind,
            "command_binary": harness.get("command"),
            "command": configured_route_commands([alias], goal_config)[0],
            "run_args": harness.get("run_args") or harness.get("smoke_args") or [],
            "run_readback": harness.get("run_readback", "stdout"),
            "effort": "configured",
            "sandbox": sandbox,
            "timeout_seconds": timeout_seconds,
            "event_logs": [f"events-{label}.{event_suffix}"],
            "probe_logs": [],
            "status_markers": {
                "begin": WORKER_STATUS_BEGIN,
                "end": WORKER_STATUS_END,
            },
        }
        if kind == "codex":
            attempt["ignore_user_config"] = True
            attempt["ignore_rules"] = True
            attempt["run_args"] = [
                "exec",
                "--ephemeral",
                *CONTRACT.CODEX_LEAN_EXEC_FLAGS,
                "-m",
                "{model}",
                "-s",
                sandbox,
                "{prompt}",
            ]
            attempt["command"] = (
                "codex exec --ephemeral " + CODEX_LEAN_EXEC_FLAGS_TEXT + f" -m {model.get('model')} -s {sandbox}"
            )
        elif kind == BRIDGE_HARNESS_KIND:
            # A configured opencode-bridge role (e.g. lite_agent / demanding_agent)
            # is launched through the same bridge runtime seam as the contract
            # bridge aliases. Attach the bridge block the launch-config adapter
            # requires, sourcing model/variant/provider from the goal-config role
            # + harness so non-contract aliases are accepted just like contract
            # aliases. Without this the adapter rejects "attempts[*].bridge must be
            # an object".
            attempt["variant"] = _harness_variant(harness)
            attempt["run_readback"] = harness.get("run_readback", "bridge_run_dir")
            attempt["bridge"] = configured_bridge_block(
                model=model,
                harness=harness,
                role=role,
                alias=alias,
            )
        attempts.append(attempt)
    return attempts


def fallback_telemetry_attempts(
    selected_ladder: list[str],
    *,
    role: str,
    timeout_seconds: int,
    sandbox: str,
) -> list[dict]:
    """Build attempts without a goal_config, dispatching per-rung.

    Bridge aliases -> bridge attempt; native codex aliases -> lean codex exec.
    A mixed ladder (e.g. ds-pro-max -> codex-spark) emits one attempt per rung.
    """
    attempts: list[dict] = []
    codex_aliases: list[str] = []
    for alias in selected_ladder:
        if CONTRACT.is_bridge_alias(alias):
            attempts.append(
                bridge_telemetry_attempt(
                    alias,
                    role=role,
                    timeout_seconds=timeout_seconds,
                    sandbox=sandbox,
                )
            )
        elif alias in CONTRACT.CODEX_ROUTE_MODELS:
            codex_attempt = CONTRACT.codex_telemetry_attempts(
                [alias],
                timeout_seconds=timeout_seconds,
                sandbox=sandbox,
                lean=True,
            )[0]
            codex_aliases.append(alias)
            attempts.append(codex_attempt)
        else:
            raise SystemExit(f"unsupported route alias for fallback attempts: {alias!r}")
    for attempt in attempts:
        attempt.setdefault("sandbox", sandbox)
    return attempts


def worker_telemetry_attempts(selected_ladder: list[str], goal_config: dict | None = None) -> list[dict]:
    if goal_config is not None:
        return configured_telemetry_attempts(
            selected_ladder,
            goal_config,
            timeout_seconds=WORKER_ATTEMPT_TIMEOUT_SECONDS,
            sandbox="workspace-write",
            role="worker",
        )
    return fallback_telemetry_attempts(
        selected_ladder,
        role="worker",
        timeout_seconds=WORKER_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="workspace-write",
    )


def reviewer_telemetry_attempts(selected_ladder: list[str], goal_config: dict | None = None) -> list[dict]:
    if goal_config is not None:
        return configured_telemetry_attempts(
            selected_ladder,
            goal_config,
            timeout_seconds=REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
            sandbox="read-only",
            role="reviewer",
        )
    return fallback_telemetry_attempts(
        selected_ladder,
        role="reviewer",
        timeout_seconds=REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
    )


def research_telemetry_attempts() -> list[dict]:
    return CONTRACT.codex_telemetry_attempts(
        [RESEARCH_ALIAS, RESEARCH_FALLBACK_ALIAS],
        timeout_seconds=RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
        event_labels=["primary", "fallback"],
        search=True,
    )


def runtime_runner_path() -> Path:
    return Path(__file__).resolve().parent / "runtime_packet_runner.py"


def compact_launch_script() -> str:
    runner = runtime_runner_path()
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
runner={shell_quote(runner.as_posix())}
if [[ ! -f "$runner" ]]; then
  echo "runtime packet runner missing: $runner" >&2
  exit 127
fi
exec python3 "$runner" --packet-dir "$(pwd)"
"""


def exact_string_schema(value: str) -> dict:
    return {"type": "string", "const": value}


def nullable_string_schema() -> dict:
    return {"type": ["string", "null"]}


def strict_schema_defects(schema: object, path: str = "$") -> list[str]:
    defects: list[str] = []
    if not isinstance(schema, dict):
        return defects
    if "const" in schema and isinstance(schema.get("const"), (list, dict)):
        defects.append(f"{path}: OpenAI structured output schemas do not support non-scalar const values")
    schema_type = schema.get("type")
    is_object = schema_type == "object" or "properties" in schema
    if is_object:
        if schema.get("additionalProperties") is not False:
            defects.append(f"{path}: object schemas must set additionalProperties=false")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            defects.append(f"{path}: object schemas must define properties")
            properties = {}
        required = schema.get("required")
        if not isinstance(required, list):
            defects.append(f"{path}: object schemas must define required")
            required = []
        missing_required = sorted(set(properties) - {item for item in required if isinstance(item, str)})
        if missing_required:
            defects.append(f"{path}: strict schemas must require every property: {', '.join(missing_required)}")
        for name, subschema in properties.items():
            defects.extend(strict_schema_defects(subschema, f"{path}.properties.{name}"))
    if schema_type == "array":
        if "items" not in schema:
            defects.append(f"{path}: array schemas must define items")
        else:
            defects.extend(strict_schema_defects(schema.get("items"), f"{path}.items"))
    return defects


def validate_openai_strict_schema(schema: dict, schema_name: str) -> None:
    defects = strict_schema_defects(schema)
    if defects:
        raise SystemExit(f"{schema_name} is not OpenAI strict-schema compatible:\n" + "\n".join(defects))


def status_schema(
    packet_id: str,
    branch: str,
    worktree: str,
    selected_ladder: list[str] | None = None,
    *,
    branch_id: str = "",
    work_item_id: str = "",
    manifest_hash: str = "",
    manifest_epoch: str = "current",
    route_id: str = "",
) -> dict:
    repo_relative_path = r"^(?!/)(?!.*//)(?!.*\\)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$))(?![ MADRCU?!]{1,2} ).+"
    nonempty_string = {"type": "string", "minLength": 1}
    selected_ladder_schema = (
        {
            "type": "array",
            "minItems": len(selected_ladder),
            "maxItems": len(selected_ladder),
            "items": {"type": "string", "enum": selected_ladder},
        }
        if selected_ladder
        else {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": list(DEFAULT_WORKER_LADDER)},
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.WORKER_STATUS_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("worker"),
            "status": {"type": "string", "enum": list(CONTRACT.STATUSES)},
            "branch_id": exact_string_schema(branch_id),
            "work_item_id": exact_string_schema(work_item_id),
            "manifest_hash": exact_string_schema(manifest_hash),
            "manifest_epoch": exact_string_schema(manifest_epoch),
            "worktree_path": exact_string_schema(worktree),
            "route_id": exact_string_schema(route_id),
            "evidence_summary": nonempty_string,
            "branch": exact_string_schema(branch),
            "worktree": exact_string_schema(worktree),
            "route_class": {"type": "string", "enum": list(WORKER_ROUTE_CLASSES)},
            "selected_ladder": selected_ladder_schema,
            "selection_reason": nonempty_string,
            "changed_files": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "pattern": repo_relative_path},
            },
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "tests": {"type": "array", "items": nonempty_string},
            "blockers": {"type": "array", "items": nonempty_string},
            "handoff": nonempty_string,
        },
    }


def review_schema(
    packet_id: str, semantic_hashes: dict[str, str] | None = None, reuse_policy: dict | None = None
) -> dict:
    nonempty_string = {"type": "string", "minLength": 1}
    semantic_hashes = semantic_hashes or {}
    semantic_properties = {key: {"type": "string", "const": value} for key, value in sorted(semantic_hashes.items())}
    _reuse_policy = reuse_policy or {
        "mode": "new",
        "accepted": False,
        "semantic_hashes_match": False,
        "source_review_path": None,
        "source_telemetry_path": None,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.REVIEW_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("reviewer"),
            "verdict": {"type": "string", "enum": [item for item in CONTRACT.REVIEW_STATUSES if item != "missing"]},
            "findings": {"type": "array", "items": nonempty_string},
            "finding_classes": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["project_bug", "orchestration_bug", "verification_gap", "no_issue"],
                },
            },
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "verification_gaps": {"type": "array", "items": nonempty_string},
            "residual_risks": {"type": "array", "items": nonempty_string},
            "semantic_input_hashes": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(semantic_properties),
                "properties": semantic_properties,
            },
            "reuse_policy": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "mode",
                    "accepted",
                    "semantic_hashes_match",
                    "source_review_path",
                    "source_telemetry_path",
                ],
                "properties": {
                    "mode": {"type": "string", "enum": ["new", "reuse"], "const": _reuse_policy.get("mode", "new")},
                    "accepted": {"type": "boolean", "const": bool(_reuse_policy.get("accepted", False))},
                    "semantic_hashes_match": {
                        "type": "boolean",
                        "const": bool(_reuse_policy.get("semantic_hashes_match", False)),
                    },
                    "source_review_path": nullable_string_schema(),
                    "source_telemetry_path": nullable_string_schema(),
                },
            },
            "summary": nonempty_string,
        },
    }


def research_schema(packet_id: str, branch: str, worktree: str) -> dict:
    repo_relative_path = r"^(?!/)(?!.*//)(?!.*\\)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$))(?![ MADRCU?!]{1,2} ).+"
    url = r"^https?://[^ \t\r\n]+$"
    nonempty_string = {"type": "string", "minLength": 1}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.RESEARCH_STATUS_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("research-worker"),
            "status": {"type": "string", "enum": list(CONTRACT.STATUSES)},
            "branch": exact_string_schema(branch),
            "worktree": exact_string_schema(worktree),
            "search_queries": {"type": "array", "items": nonempty_string},
            "source_urls": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": url}},
            "tools_used": {"type": "array", "items": nonempty_string},
            "local_files_read": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "pattern": repo_relative_path},
            },
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "findings": {"type": "array", "minItems": 1, "items": nonempty_string},
            "blockers": {"type": "array", "items": nonempty_string},
            "handoff": nonempty_string,
        },
    }


def optional_list(title: str, values: list[str]) -> str:
    if not values:
        return f"{title}: none"
    return title + ":\n" + "\n".join(f"- {value}" for value in values)


def context_section(worktree: str, context_files: list[str], *, include_worktree_excerpts: bool) -> str:
    pack = CONTEXT_PACK.pack_context(
        worktree=Path(worktree).resolve(),
        context_files=[Path(value).resolve() for value in context_files],
        total_chars=MAX_CONTEXT_PACK_CHARS,
        per_file_chars=MAX_CONTEXT_FILE_CHARS,
        include_worktree_excerpts=include_worktree_excerpts,
    )
    return CONTEXT_PACK.markdown_from_pack(pack)


def context_budget_report(
    *,
    prompt_text: str,
    task_text: str,
    context_files: list[str],
    include_worktree_context_excerpts: bool,
) -> dict:
    prompt_chars = len(prompt_text)
    task_chars = len(task_text)
    estimate = max(1, round(prompt_chars / APPROX_CHARS_PER_TOKEN))
    status = "pass" if prompt_chars <= MAX_PACKET_PROMPT_CHARS else "blocked"
    return {
        "schema_version": 1,
        "status": status,
        "prompt_chars": prompt_chars,
        "prompt_tokens_estimate": estimate,
        "prompt_char_limit": MAX_PACKET_PROMPT_CHARS,
        "task_chars": task_chars,
        "context_files_count": len(context_files),
        "context_pack_total_char_limit": MAX_CONTEXT_PACK_CHARS,
        "context_pack_per_file_char_limit": MAX_CONTEXT_FILE_CHARS,
        "worktree_context_mode": "embedded_excerpt" if include_worktree_context_excerpts else "path_reference",
        "load_policy": "bounded_context_pack_and_path_manifest",
    }


def enforce_context_budget(packet_id: str, report: dict) -> None:
    if report.get("status") == "pass":
        return
    raise SystemExit(
        "runtime packet context budget exceeded before launch: "
        f"{packet_id} prompt_chars={report.get('prompt_chars')} "
        f"limit={report.get('prompt_char_limit')}"
    )


def load_task(path: Path | None) -> str:
    if not path:
        return "- Replace this section with the bounded task objective before launch."
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def read_json_or_none(path: Path) -> tuple[dict | None, str | None]:
    try:
        return load_json(path), None
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- load_json fails closed via SystemExit
        return None, str(exc)


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return ""


def markdown_section(text: str, heading: str, *, max_chars: int = 800) -> str:
    marker = f"## {heading}"
    lines = text.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == marker:
            collecting = True
            continue
        if collecting and stripped.startswith("## "):
            break
        if collecting:
            collected.append(line)
    value = "\n".join(collected).strip()
    if len(value) > max_chars:
        return value[:max_chars].rstrip() + "\n[truncated]"
    return value


def find_manifest_context(
    context_files: list[str], branch_id: str, packet_id: str
) -> tuple[Path, dict, dict, dict] | None:
    for value in context_files:
        path = Path(value)
        if path.name != "job.manifest.json":
            continue
        try:
            manifest = load_json(path)
        except (Exception, SystemExit):  # noqa: BLE001 -- load_json fails closed via SystemExit
            continue
        branch_data = branch_entry(manifest, branch_id)
        if not branch_data:
            continue
        work_items = branch_data.get("work_items") if isinstance(branch_data.get("work_items"), list) else []
        matches = [item for item in work_items if isinstance(item, dict) and item.get("packet_id") == packet_id]
        if len(matches) != 1:
            continue
        return path, manifest, branch_data, matches[0]
    return None


def compact_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str) and item.strip()]


def bullet_list(values: list[str]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values)


def compact_worker_context(
    *,
    branch_id: str,
    packet_id: str,
    task_file: Path | None,
    task_text: str,
    owned_files: list[str],
    context_files: list[str],
) -> tuple[str, list[str], dict] | None:
    found = find_manifest_context(context_files, branch_id, packet_id)
    if found is None:
        return None
    manifest_path, manifest, branch_data, work_item = found
    task_sha = CONTEXT_PACK.sha256_file(task_file) if task_file else None
    manifest_sha = CONTEXT_PACK.sha256_file(manifest_path)
    branch_objective = markdown_section(task_text, "Objective", max_chars=500)
    branch_scope = markdown_section(task_text, "Scope", max_chars=500)
    stop_conditions = markdown_section(task_text, "Stop Conditions", max_chars=500)
    work_owned_paths = compact_list(work_item.get("owned_paths")) or owned_files
    if not work_owned_paths:
        raise SystemExit(
            f"worker packet {packet_id} (branch {branch_id}) declares no owned_paths; "
            "a worker must own at least one path"
        )
    work_context_files = compact_list(work_item.get("context_files"))
    verification = compact_list(work_item.get("verification"))
    dod = compact_list(work_item.get("dod"))
    depends_on = compact_list(work_item.get("depends_on"))
    worker_parallelism = (
        branch_data.get("worker_parallelism") if isinstance(branch_data.get("worker_parallelism"), dict) else {}
    )
    artifact = {
        "schema_version": 1,
        "kind": "compact_worker_context",
        "source": "job.manifest.json branch/work-item slice",
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": manifest_sha,
        "task_file": task_file.as_posix() if task_file else None,
        "task_file_sha256": task_sha,
        "job_id": manifest.get("job_id"),
        "base_ref": manifest.get("base_ref"),
        "branch": {
            "id": branch_data.get("id"),
            "branch_name": branch_data.get("branch_name"),
            "worktree_path": branch_data.get("worktree_path"),
            "prompt": branch_data.get("prompt"),
            "status_path": branch_data.get("status_path"),
            "review_path": branch_data.get("review_path"),
            "pre_review_gate_path": branch_data.get("pre_review_gate_path"),
            "owned_paths": compact_list(branch_data.get("owned_paths")),
            "max_active_worker_packets": branch_data.get("max_active_worker_packets"),
            "worker_scheduler_path": worker_parallelism.get("scheduler_path"),
        },
        "work_item": {
            "id": work_item.get("id"),
            "packet_id": work_item.get("packet_id"),
            "worker_type": work_item.get("worker_type", "worker"),
            "route_class": work_item.get("route_class", DEFAULT_WORKER_ROUTE_CLASS),
            "objective": work_item.get("objective"),
            "owned_paths": work_owned_paths,
            "context_files": work_context_files,
            "depends_on": depends_on,
            "verification": verification,
            "dod": dod,
        },
    }
    task_lines = [
        "# Compact Worker Task",
        "",
        "This task was generated deterministically from `packet-context.json`; use the full branch prompt or manifest only if this compact task is insufficient or a validator/launcher fails.",
        "",
        f"Job: {manifest.get('job_id', '')}",
        f"Base ref: {manifest.get('base_ref', '')}",
        f"Branch prompt: {branch_data.get('prompt', '')}",
        f"Manifest: {manifest_path.as_posix()} ({manifest_sha})",
    ]
    heading = first_markdown_heading(task_text)
    if heading:
        task_lines.append(f"Branch heading: {heading}")
    if branch_objective:
        task_lines.extend(["", "Branch objective:", branch_objective])
    if branch_scope:
        task_lines.extend(["", "Branch scope:", branch_scope])
    task_lines.extend(
        [
            "",
            f"Work item: {work_item.get('id', '')} / {packet_id}",
            f"Worker type: {work_item.get('worker_type', 'worker')}",
            f"Route class: {work_item.get('route_class', DEFAULT_WORKER_ROUTE_CLASS)}",
            f"Objective: {work_item.get('objective', '')}",
            "",
            "Owned paths:",
            bullet_list(work_owned_paths),
            "",
            "Context files:",
            bullet_list(work_context_files),
            "",
            "Depends on:",
            bullet_list(depends_on),
            "",
            "Verification commands:",
            bullet_list(verification),
            "",
            "Definition of Done:",
            bullet_list(dod),
        ]
    )
    if stop_conditions:
        task_lines.extend(["", "Stop conditions:", stop_conditions])
    task_lines.extend(
        [
            "",
            "Worker rules:",
            "- Edit only owned paths unless returning `blocked` explains why broader ownership is required.",
            "- Run the listed verification commands or record the concrete blocker.",
            "- Use `git diff --check HEAD` and `git diff --check <base-ref>...HEAD` before claiming readiness when the base ref is available.",
            "- Do not read skill Python source unless a script or validator fails and source-level debugging is required.",
        ]
    )
    filtered_context_files = [value for value in context_files if Path(value).resolve() != manifest_path.resolve()]
    return "\n".join(task_lines).rstrip() + "\n", filtered_context_files, artifact


def archive_existing_packet_dir(packet_dir: Path, *, replace: bool) -> str | None:
    if not packet_dir.exists():
        return None
    if packet_dir.is_dir() and not any(packet_dir.iterdir()):
        return None
    if not replace:
        raise SystemExit(f"runtime packet already exists; pass --replace to archive and recreate: {packet_dir}")
    attempts_dir = packet_dir / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    next_index = 1
    for child in sorted(attempts_dir.iterdir()):
        if child.is_dir() and child.name.startswith("attempt-"):
            suffix = child.name.removeprefix("attempt-")
            if suffix.isdigit():
                next_index = max(next_index, int(suffix) + 1)
    archive_dir = attempts_dir / f"attempt-{next_index:03d}"
    archive_dir.mkdir()
    index_entries = []
    for child in sorted(packet_dir.iterdir()):
        if child.name == "attempts":
            continue
        archived_path = archive_dir / child.name
        index_entries.append(
            {
                "artifact_path": child.relative_to(packet_dir).as_posix(),
                "archived_path": archived_path.relative_to(packet_dir).as_posix(),
                "original_sha256": CONTEXT_PACK.sha256_file(child) if child.is_file() else None,
                "stale_reason": "packet_replaced",
                "superseding_artifact": child.relative_to(packet_dir).as_posix(),
                "superseding_packet": packet_dir.name,
                "terminal_reason": None,
                "excluded_from_current_evidence": True,
                "retention_epoch": archive_dir.name,
            }
        )
        child.rename(archived_path)
    write_json(
        packet_dir / "stale-artifacts.index.json",
        {
            "schema_version": 1,
            "packet_id": packet_dir.name,
            "retention_epoch": archive_dir.name,
            "entries": index_entries,
        },
    )
    return archive_dir.name


def scheduler_closed_pass_for_packet(scheduler_path: Path, packet_id: str) -> bool:
    if not scheduler_path.exists():
        return False
    try:
        ledger = load_json(scheduler_path)
    except (Exception, SystemExit):  # noqa: BLE001 -- load_json fails closed via SystemExit
        return False
    events = ledger.get("events")
    if not isinstance(events, list):
        return False
    active = False
    finished_status: str | None = None
    closed_pass = False
    for event in events:
        if not isinstance(event, dict) or event.get("id") != packet_id:
            continue
        name = event.get("event")
        if name == "launch":
            active = True
            finished_status = None
            closed_pass = False
        elif name == "finish" and active:
            status = event.get("status")
            finished_status = status if isinstance(status, str) else None
        elif name == "close" and active:
            closed_pass = finished_status == "pass"
            active = False
    return closed_pass


def worker_scheduler_path(manifest_path: Path | None, branch_id: str) -> Path | None:
    if manifest_path is None or not branch_id:
        return None
    return manifest_path.parent / "schedulers" / f"{branch_id}.worker.scheduler.json"


def refuse_closed_pass_replacement(
    packet_dir: Path, *, replace: bool, scheduler_path: Path | None, packet_id: str
) -> None:
    if not replace or scheduler_path is None or not packet_dir.exists():
        return
    if scheduler_closed_pass_for_packet(scheduler_path, packet_id):
        raise SystemExit(
            f"refusing to replace scheduler-closed pass packet {packet_id}; create a new packet id or update scheduler evidence first"
        )


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        return {}
    matches = [item for item in branches if isinstance(item, dict) and item.get("id") == branch_id]
    return matches[0] if len(matches) == 1 else {}


def bundle_path(bundle_dir: Path, value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    relative = PATH_RULES.require_relative_path(value, field)
    return (bundle_dir / relative).resolve().as_posix()


def reviewer_branch_status_context(bundle_dir: Path, branch_data: dict, gate: dict) -> dict:
    status_path_value = bundle_path(bundle_dir, branch_data.get("status_path"), "branch status_path")
    context: dict[str, object] = {
        "path": status_path_value,
        "currentness": "unknown",
        "pre_review_gate_is_authoritative": gate.get("status") == "pass",
        "stale_missing_gate_blocker_ignored": False,
    }
    if not status_path_value:
        return context
    status_path = Path(status_path_value)
    data, _error = read_json_or_none(status_path)
    if not isinstance(data, dict):
        return context
    raw_blockers = data.get("blockers")
    blockers = [item for item in raw_blockers if isinstance(item, str)] if isinstance(raw_blockers, list) else []
    missing_gate_blocker = any("pre-review gate" in item and "missing" in item for item in blockers)
    if gate.get("status") == "pass" and missing_gate_blocker:
        context.update(
            {
                "currentness": "pre_gate_status_stale_after_passing_pre_review_gate",
                "stale_missing_gate_blocker_ignored": True,
                "instruction": (
                    "The pre_review_gate artifact is newer/current for reviewer readiness; ignore branch_status "
                    "blockers that only claim the pre-review gate is missing."
                ),
            }
        )
    else:
        context["currentness"] = "current_or_not_known_stale"
    return context


def reviewer_packet_context(
    *,
    packet_id: str,
    branch_id: str,
    worktree: Path,
    manifest_path: Path,
    manifest: dict,
    gate_path: Path,
    gate: dict,
    review_route: dict,
    review_schema_path: Path,
    review_output_path: Path,
) -> dict:
    bundle_dir = manifest_path.parent
    branch_data = branch_entry(manifest, branch_id)
    worker_artifacts: list[dict] = []
    work_items = branch_data.get("work_items") if isinstance(branch_data.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        worker_packet_id = item.get("packet_id")
        if not isinstance(worker_packet_id, str) or not worker_packet_id.strip():
            item_id = item.get("id")
            worker_packet_id = f"{branch_id}-{item_id}" if isinstance(item_id, str) else ""
        if not worker_packet_id:
            continue
        role = item.get("worker_type", "worker")
        if role == "research":
            role = "research-worker"
        if role == "research-worker":
            packet_dir = bundle_dir / "research" / worker_packet_id
            status_path = packet_dir / "research.json"
        else:
            packet_dir = bundle_dir / "workers" / worker_packet_id
            status_path = packet_dir / "status.json"
        worker_artifacts.append(
            {
                "packet_id": worker_packet_id,
                "role": role if role == "research-worker" else "worker",
                "status_path": status_path.resolve().as_posix(),
                "telemetry_path": (packet_dir / "telemetry.json").resolve().as_posix(),
                "route_path": (packet_dir / "route.json").resolve().as_posix(),
                "status_summary": _summarize_reviewer_artifact(status_path),
            }
        )
    base_ref = str(manifest.get("base_ref", "main"))
    changed_paths = _path_classified_changed_paths(branch_data, gate)
    branch_status_context = reviewer_branch_status_context(bundle_dir, branch_data, gate)
    return {
        "schema_version": 1,
        "kind": "compact_reviewer_context",
        "packet_id": packet_id,
        "role": "reviewer",
        "branch_id": branch_id,
        "branch_name": branch_data.get("branch_name"),
        "worktree": worktree.as_posix(),
        "base_ref": base_ref,
        "read_limits": {
            "max_prompt_chars": MAX_PACKET_PROMPT_CHARS,
            "max_context_pack_chars": MAX_CONTEXT_PACK_CHARS,
            "max_context_file_chars": MAX_CONTEXT_FILE_CHARS,
            "max_path_scoped_reads": 200,
        },
        "path_scoped_changed_paths": _path_scoped_changed_paths(changed_paths, source="review_gate"),
        "changed_path_risk_counts": _path_risk_counts(changed_paths),
        "read_first": {
            "pre_review_gate": gate_path.as_posix(),
            "branch_status": branch_status_context.get("path"),
            "branch_status_context": branch_status_context,
            "branch_prompt": bundle_path(bundle_dir, branch_data.get("prompt"), "branch prompt"),
            "manifest": manifest_path.as_posix(),
            "worker_artifacts": worker_artifacts,
            "review_schema": review_schema_path.as_posix(),
            "review_output": review_output_path.as_posix(),
            "commands_to_run": [
                "git status --short --branch",
                f"git diff --check {base_ref}...HEAD",
                f"git diff --stat {base_ref}...HEAD",
                f"git diff --name-only {base_ref}...HEAD",
            ],
            "bounded_context_guidance": {
                "path_scoped_changed_paths": "Use this as the primary evidence source; prefer file hunks over broad logs.",
                "worker_artifact_summaries": [
                    {
                        "packet_id": item["packet_id"],
                        "role": item.get("role"),
                        "selected_state": item.get("status_summary"),
                    }
                    for item in worker_artifacts
                ],
            },
        },
        "write_only": {
            "review_output": review_output_path.as_posix(),
        },
        "route": review_route,
        "semantic_input_hashes": gate.get("semantic_input_hashes", {}),
        "reuse_policy": gate.get("reuse_policy", {}),
    }


def branch_entry_for_packet(manifest: dict, branch_value: str, packet_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        return {}
    for key in ("id", "branch_name"):
        matches = [item for item in branches if isinstance(item, dict) and item.get(key) == branch_value]
        if len(matches) == 1:
            return matches[0]
    packet_prefix = packet_id.split("-", 1)[0] if "-" in packet_id else ""
    if packet_prefix:
        return branch_entry(manifest, packet_prefix)
    return {}


def review_changed_paths(gate: dict, branch: dict) -> list[str]:
    paths: list[str] = []
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    ownership = checks.get("ownership") if isinstance(checks.get("ownership"), dict) else {}
    for source in [
        gate.get("changed_paths"),
        gate.get("changed_files"),
        ownership.get("changed_files"),
        branch.get("owned_paths"),
    ]:
        if not isinstance(source, list):
            continue
        for value in source:
            if isinstance(value, str) and value.strip() and value not in paths:
                paths.append(value)
    return paths


def branch_route_classes(branch: dict) -> list[str]:
    classes: list[str] = []
    work_items = branch.get("work_items") if isinstance(branch.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        worker_type = item.get("worker_type", "worker")
        if worker_type in {"research-worker", "research"}:
            route_class = "research-worker"
        else:
            route_class = item.get("route_class", DEFAULT_WORKER_ROUTE_CLASS)
        if isinstance(route_class, str) and route_class.strip() and route_class not in classes:
            classes.append(route_class)
    return classes


def docs_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".markdown", ".txt", ".rst", ".adoc")) or lowered.startswith(
        ("docs/", "doc/", "readme", "changelog", "license", "notice")
    )


def test_path(path: str) -> bool:
    lowered = path.lower()
    return (
        lowered.startswith(("test/", "tests/", "spec/", "specs/"))
        or "/tests/" in lowered
        or lowered.startswith("test_")
        or "_test." in lowered
        or ".spec." in lowered
    )


def production_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not docs_path(path) and not test_path(path)]


def _review_route_markers_for_role(role: str) -> dict[str, str]:
    if role == "reviewer":
        return {"begin": REVIEW_STATUS_BEGIN, "end": REVIEW_STATUS_END}
    return {"begin": WORKER_STATUS_BEGIN, "end": WORKER_STATUS_END}


def _normalize_review_routes(routes: object) -> dict[str, list[str]]:
    if not isinstance(routes, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        value = routes.get(tier)
        if not isinstance(value, list):
            continue
        candidate = [item for item in value if isinstance(item, str) and item.strip()]
        if candidate:
            normalized[tier] = candidate
    return normalized


def _manifest_reviewer_route_aliases(
    manifest: dict | None,
    manifest_path: Path | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    contract_aliases: list[str] = []
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        for alias in CONTRACT.review_route_for_tier(tier):
            if alias not in contract_aliases:
                contract_aliases.append(alias)
    alias_map: dict[str, list[str]] = {alias: [] for alias in contract_aliases}
    role_alias_map: dict[str, list[str]] = {"lite_agent": [], "demanding_agent": []}
    if not isinstance(manifest, dict):
        return alias_map, role_alias_map

    raw_models = manifest.get("models")
    items = []
    if isinstance(raw_models, dict):
        items = raw_models.items()
    elif isinstance(raw_models, list):
        for item in raw_models:
            if isinstance(item, dict) and isinstance(item.get("alias"), str):
                items.append((item.get("alias"), item))
    goal_config = goal_config_from_manifest(manifest, manifest_path)
    if isinstance(goal_config, dict):
        raw_goal_models = goal_config.get("models")
        if isinstance(raw_goal_models, dict):
            for alias, spec in raw_goal_models.items():
                if isinstance(alias, str) and isinstance(spec, dict):
                    items.append((alias, spec))
    if not items:
        return alias_map, role_alias_map

    seen: dict[str, set[str]] = {alias: set() for alias in contract_aliases}
    role_seen: dict[str, set[str]] = {alias: set() for alias in role_alias_map}
    for alias, spec in items:
        if not isinstance(alias, str):
            continue
        if not isinstance(spec, dict):
            continue
        model_value = spec.get("model")
        if not isinstance(model_value, str):
            continue
        model = model_value.strip().lower()
        role = spec.get("role")
        if isinstance(role, str):
            role_key = role.strip().lower()
            if role_key in role_alias_map and alias not in role_seen[role_key]:
                role_seen[role_key].add(alias)
                role_alias_map[role_key].append(alias)
        for route_alias, route_model in CONTRACT.CODEX_ROUTE_MODELS.items():
            if route_alias not in alias_map:
                continue
            if route_model == model:
                if alias not in seen[route_alias]:
                    seen[route_alias].add(alias)
                    alias_map[route_alias].append(alias)
                break
    return alias_map, role_alias_map


def _resolve_goal_config_review_routes(
    manifest: dict | None, manifest_path: Path | None = None
) -> dict[str, list[str]]:
    if not isinstance(manifest, dict):
        return {}
    goal_config = goal_config_from_manifest(manifest, manifest_path)
    if not isinstance(goal_config, dict):
        return {}
    model_policies = goal_config.get("model_policies")
    if not isinstance(model_policies, dict):
        return {}
    review_policy = model_policies.get("review_model_policy")
    if not isinstance(review_policy, dict):
        return {}
    return _normalize_review_routes(review_policy.get("routes"))


def _resolve_review_alias(alias: str, manifest_aliases: dict[str, list[str]]) -> list[str]:
    if not alias:
        return []
    alias = alias.strip()
    if not alias:
        return []
    if alias in manifest_aliases:
        return list(manifest_aliases[alias])
    for aliases in manifest_aliases.values():
        if alias in aliases:
            return list(aliases)
    normalized = alias.lower().replace("_", "-")
    for canonical_alias, aliases in manifest_aliases.items():
        if canonical_alias.replace("-", "") in normalized.replace("-", "") and aliases:
            return list(aliases)
    return [alias]


def _resolve_review_route_list(raw_routes: list[str], manifest_aliases: dict[str, list[str]]) -> list[str]:
    if not raw_routes:
        return []
    resolved: list[str] = []
    seen: set[str] = set()
    for alias in raw_routes:
        for item in _resolve_review_alias(alias, manifest_aliases):
            if item and item not in seen:
                seen.add(item)
                resolved.append(item)
    return resolved


def _default_route_variants(
    manifest_aliases: dict[str, list[str]], role_aliases: dict[str, list[str]]
) -> dict[str, list[str]]:
    variants: dict[str, list[str]] = {}
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        variants[tier] = []
        for canonical_alias in CONTRACT.review_route_for_tier(tier):
            if canonical_alias in manifest_aliases and manifest_aliases[canonical_alias]:
                for value in manifest_aliases[canonical_alias]:
                    if value not in variants[tier]:
                        variants[tier].append(value)
        if not variants[tier]:
            if tier == "light" and role_aliases.get("lite_agent"):
                for value in role_aliases["lite_agent"]:
                    if value not in variants[tier]:
                        variants[tier].append(value)
            elif tier in {"standard", "heavy"} and role_aliases.get("demanding_agent"):
                for value in role_aliases["demanding_agent"]:
                    if value not in variants[tier]:
                        variants[tier].append(value)
            else:
                for canonical_alias in CONTRACT.review_route_for_tier(tier):
                    if canonical_alias not in variants[tier]:
                        variants[tier].append(canonical_alias)
    return variants


def _route_variants_from_manifest(
    policy: dict, manifest: dict | None, manifest_path: Path | None = None
) -> dict[str, list[str]]:
    policy_routes = _normalize_review_routes(policy.get("routes") if isinstance(policy, dict) else None)
    goal_config_routes = _resolve_goal_config_review_routes(manifest, manifest_path=manifest_path)
    if not policy_routes and goal_config_routes:
        policy_routes = goal_config_routes
    manifest_aliases, role_aliases = _manifest_reviewer_route_aliases(manifest, manifest_path=manifest_path)
    defaults = _default_route_variants(manifest_aliases, role_aliases)
    has_manifest_mapping = any(manifest_aliases.values()) or any(role_aliases.values())
    resolved: dict[str, list[str]] = {}
    configured = False
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        selected = policy_routes.get(tier, [])
        if selected:
            configured = True
            resolved[tier] = _resolve_review_route_list(selected, manifest_aliases)
        else:
            resolved[tier] = []
    # Fill missing tiers using local defaults.
    for tier in CONTRACT.REVIEW_ROUTE_TIERS:
        if not resolved[tier]:
            resolved[tier] = list(defaults[tier])
    # If routes are explicitly collapsed across tiers and manifest has at least two
    # distinct review variants, expand to manifest-aware defaults to preserve
    # the intended cheap-vs-heavy ladder shape.
    has_variants = len({tuple(value) for value in resolved.values()}) > 1
    default_variants_distinct = len({tuple(value) for value in defaults.values()}) > 1
    if configured and not has_variants and default_variants_distinct and has_manifest_mapping:
        return defaults
    return resolved


def _classify_path_risk(path: str) -> str:
    if docs_path(path):
        return "docs"
    if test_path(path):
        return "test"
    return "production"


def _path_risk_counts(paths: list[str]) -> dict[str, int]:
    counts = {"production": 0, "docs": 0, "test": 0}
    for path in paths:
        category = _classify_path_risk(path)
        counts[category] = counts.get(category, 0) + 1
    return counts


def _path_scoped_changed_paths(paths: list[str], *, source: str = "review_gate") -> list[dict[str, str]]:
    return [
        {
            "path": path,
            "risk": _classify_path_risk(path),
            "source": source,
        }
        for path in paths
    ]


def _summarize_reviewer_artifact(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    if not path.is_file():
        return {"exists": False, "reason": "not a file"}
    try:
        data = load_json(path)
    except (Exception, SystemExit):  # noqa: BLE001 -- load_json fails closed via SystemExit
        return {"exists": False, "reason": "invalid json"}
    return {
        "exists": True,
        "status": data.get("status") if isinstance(data, dict) else None,
        "verdict": data.get("verdict") if isinstance(data, dict) else None,
        "route_class": data.get("route_class") if isinstance(data, dict) else None,
        "selected_ladder": data.get("selected_ladder") if isinstance(data, dict) else None,
    }


def _work_item_owned_paths(branch: dict) -> list[str]:
    if not isinstance(branch, dict):
        return []
    collected: list[str] = []
    for item in branch.get("work_items", []):
        if isinstance(item, dict):
            collected.extend([value for value in compact_list(item.get("owned_paths")) if value])
    return collected


def _path_classified_changed_paths(branch: dict, gate: dict) -> list[str]:
    paths = review_changed_paths(gate, branch)
    if paths:
        return paths
    collected: list[str] = []
    for value in _work_item_owned_paths(branch):
        if value and value not in collected:
            collected.append(value)
    for value in branch.get("owned_paths", []):
        if isinstance(value, str) and value.strip() and value not in collected:
            collected.append(value)
    return collected


def explicit_review_tier(value: object) -> str:
    if isinstance(value, str) and value in CONTRACT.REVIEW_ROUTE_TIERS:
        return value
    return ""


def infer_review_tier(manifest: dict, gate: dict, branch: dict) -> tuple[str, list[str]]:
    explicit = explicit_review_tier(gate.get("review_tier")) or explicit_review_tier(branch.get("review_tier"))
    if explicit:
        explicit_reason = nonempty_text(gate.get("review_tier_reason")) or nonempty_text(
            branch.get("review_tier_reason")
        )
        return explicit, [explicit_reason or f"explicit {explicit} review tier"]
    changed_paths = review_changed_paths(gate, branch)
    trigger_hits: list[str] = []
    lower_paths = " ".join(changed_paths).lower()
    for pattern in CONTRACT.REVIEW_HEAVY_TRIGGER_PATTERNS:
        if pattern in lower_paths.replace("-", "_") or pattern in lower_paths:
            trigger_hits.append(pattern)
    diff_stats = gate.get("diff_stats") if isinstance(gate.get("diff_stats"), dict) else {}
    files_changed = diff_stats.get("files_changed")
    lines_changed = diff_stats.get("lines_changed")
    if isinstance(files_changed, int) and not isinstance(files_changed, bool) and files_changed >= 20:
        trigger_hits.append("large-diff")
    if isinstance(lines_changed, int) and not isinstance(lines_changed, bool) and lines_changed >= 800:
        trigger_hits.append("large-diff")
    if gate.get("prior_reviewer_blockers"):
        trigger_hits.append("reviewer-blocker")
    route_classes = branch_route_classes(branch)
    if "complex-code" in route_classes:
        trigger_hits.append("complex-code route class")
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    tests = checks.get("tests") if isinstance(checks.get("tests"), dict) else {}
    if tests.get("status") not in {None, "pass"}:
        trigger_hits.append("incomplete verification")
    if gate.get("status") not in {None, "pass"}:
        trigger_hits.append("incomplete pre-review gate")
    if trigger_hits:
        return "heavy", sorted(set(trigger_hits))
    if route_classes and set(route_classes) <= {"docs", "mechanical"}:
        return "light", ["docs/mechanical route classes with no production behavior signal"]
    if changed_paths and not production_paths(changed_paths) and len(changed_paths) <= 6:
        return "light", ["docs or test-only review surface with no production path changes"]
    if route_classes and set(route_classes) <= {"small-edit", "normal-code"}:
        return "standard", ["normal or small-edit implementation route classes require standard reviewer routing"]
    policy = manifest.get("review_model_policy") if isinstance(manifest.get("review_model_policy"), dict) else {}
    default_tier = (
        policy.get("default_tier")
        if policy.get("default_tier") in CONTRACT.REVIEW_ROUTE_TIERS
        else CONTRACT.REVIEW_MODEL_POLICY["default_tier"]
    )
    return str(default_tier), ["default deterministic review tier"]


def select_review_route(
    manifest: dict, gate: dict, *, branch_id: str, packet_id: str, manifest_path: Path | None = None
) -> dict:
    branch = branch_entry(manifest, branch_id)
    tier, reasons = infer_review_tier(manifest, gate, branch)
    policy = manifest.get("review_model_policy") if isinstance(manifest.get("review_model_policy"), dict) else {}
    policy_routes = policy.get("routes") if isinstance(policy.get("routes"), dict) else {}
    route_variants = _route_variants_from_manifest(policy, manifest, manifest_path=manifest_path)
    route = route_variants.get(tier, CONTRACT.review_route_for_tier(tier))
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": "reviewer",
        "tier": tier,
        "selected_ladder": route,
        "selection_reason": "; ".join(reasons),
        "policy_router": policy.get("router", CONTRACT.REVIEW_MODEL_POLICY["router"]),
        "policy_version": policy.get("version", ROUTE_POLICY_VERSION),
        "route_policy_version": ROUTE_POLICY_VERSION,
        "policy_routes": policy_routes or CONTRACT.REVIEW_MODEL_POLICY["routes"],
        "route_variants": route_variants,
        "heavy_triggers": reasons if tier == "heavy" else [],
        "route_classes": branch_route_classes(branch),
        "changed_paths": review_changed_paths(gate, branch),
        "selection_context": {
            "tier": tier,
            "reasons": reasons,
        },
    }


def reviewer_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    context_files: list[str],
    packet_context_path: str,
    packet_context_inline: str,
    include_worktree_context_excerpts: bool,
) -> str:
    context_pointer = (
        f"Packet context to read first:\n- {packet_context_path}"
        if packet_context_path
        else context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)
    )
    inline_context = (
        "\nThe same packet-local compact_reviewer_context is embedded below so restricted CLI harnesses can review without reading outside the worktree:\n\n"
        "```json\n"
        f"{packet_context_inline}\n"
        "```\n"
        if packet_context_inline
        else ""
    )
    return f"""# Branch Reviewer Packet {packet_id}

You are Reviewer {packet_id}. Do not edit files.

Worktree: {worktree}
Branch: {branch}

{context_pointer}
{inline_context}

Before reviewing, run:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Review the branch against its prompt, worker status files, bounded diffs, test evidence, and claim-boundary rules. Lead with findings ordered by severity. Ground findings in file/line references or command evidence where possible. When a finding is a concrete application/project bug, classify it separately from orchestration/tooling defects by adding `finding_classes` entries such as `project_bug` or `orchestration_bug`; reuse unchanged project-bug findings until touched files or test evidence change.

The branch orchestrator must have supplied a passing schema v2 `pre_review_gate.json` before this packet was generated. Read it from the provided context, copy its `semantic_input_hashes` exactly into the final review JSON as `semantic_input_hashes`, and record a `reuse_policy` object. Set reviewer reuse to accepted only when every semantic input hash matches exactly and both the source review and source telemetry are present; otherwise produce a fresh review.

Read the packet-local `compact_reviewer_context` first. It lists the exact branch prompt, branch status, pre-review gate, worker status, worker telemetry, schema, and output paths. If `read_first.branch_status_context.stale_missing_gate_blocker_ignored` is true, treat the passing `pre_review_gate` as the current reviewer-readiness artifact and do not report the stale branch-status missing-gate blocker as a current finding. Use named paths before searching any bundle directory. Do not read memory, broad bundle directories, full event logs, or unrelated repo files unless a named packet artifact is missing, contradictory, or insufficient to substantiate a concrete finding. Prefer `git diff --stat`, `git diff --name-only`, and targeted file hunks for changed paths over full diffs. Keep review reads path-scoped using `path_scoped_changed_paths`, and obey `read_limits` before opening artifacts.

Determine the branch base ref from `compact_reviewer_context`. Before reporting merge readiness, run `git diff --check <base-ref>...HEAD` and record the command result. If the base ref is unavailable, report a verification gap instead of assuming merge readiness.

Do not emit placeholder, draft, or example final-shaped JSON before inspection is complete. Return exactly one final JSON object matching `{schema_name}` only after command inspection and evidence review are finished. `commands_run` must contain exact command strings that were actually run.

If your CLI harness does not write `{schema_name}` directly, print the final review object between these exact marker lines and do not print any other JSON object between them:

{REVIEW_STATUS_BEGIN}
{{"packet_id":"{packet_id}","role":"reviewer","verdict":"blocked","findings":["replace with concrete finding"],"finding_classes":["project_bug"],"commands_run":["pwd","git status --short --branch"],"verification_gaps":["replace with concrete gap"],"residual_risks":[],"semantic_input_hashes":{{}},"reuse_policy":{{}},"summary":"replace with concise summary"}}
{REVIEW_STATUS_END}
"""


def research_worker_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    include_worktree_context_excerpts: bool,
) -> str:
    example_research = json.dumps(
        {
            "packet_id": packet_id,
            "role": "research-worker",
            "status": "blocked",
            "branch": branch,
            "worktree": worktree,
            "search_queries": [],
            "source_urls": [],
            "tools_used": [],
            "local_files_read": [],
            "commands_run": ["pwd", "git status --short --branch"],
            "findings": ["replace with concrete finding or blocker"],
            "blockers": ["replace with concrete blocker"],
            "handoff": "replace with concise research handoff",
        },
        separators=(",", ":"),
    )
    return f"""# Research Worker Packet {packet_id}

You are Research Worker {packet_id}. Do not edit files.

Worktree: {worktree}
Branch: {branch}

Allowed information sources:

- Native Codex live web search enabled by the launcher.
- Configured read-only CLI tools, MCP servers, connector tools, browser/search tools, package metadata lookups, remote APIs, and shell/network inspection commands when they are relevant to the task.
- Local read-only file and command inspection for the assigned worktree, explicit context files, and configured tool or skill documentation when task-relevant.

Safety boundaries:

- Do not write or modify local files.
- Do not mutate remote services or repositories.
- Do not inspect secrets or unrelated private files.
- Do not post messages, send email, create tickets, buy anything, change calendars/docs/issues, authenticate new accounts, alter credentials, or exfiltrate secrets.
- Use broad tools only for read-only information retrieval and record what you used.

Local read scope:

{optional_list("Relevant local files/modules", owned_files)}

{context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)}

Before researching, run:

```bash
pwd
git status --short --branch
```

Task:

{task_text}

Use the appropriate broad read-only tools for current outside information. Record every search query you rely on in `search_queries`; leave it empty only when you used direct URLs, local files, connectors, or other non-search tools instead. Record every source URL that supports a finding in `source_urls`. Use direct source URLs, not just search-result pages. Record every local file you read in `local_files_read` using repo-relative paths only.
Record every distinct external or local tool family you used in `tools_used`, for example `codex-native-search`, `web-open`, `shell-curl`, `local-rg`, `local-sed`, `mcp-docs`, or `connector-drive`.

Return a research status object matching `{schema_name}`. Allowed `status` values are exactly `pass`, `partial`, `blocked`, or `failed`. Use `pass` only when the research task is complete, source URLs are captured for all online claims, local files read are recorded, and `tools_used` identifies the tool families used. `commands_run` must contain exact local or shell commands that were actually run.

Do not emit placeholder, draft, or example final-shaped JSON before research is complete. Return exactly one final JSON object matching `{schema_name}`.

Example shape only:

```json
{example_research}
```
"""


def worker_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    selected_ladder: list[str],
    route_class: str,
    selection_reason: str,
    include_worktree_context_excerpts: bool,
    *,
    branch_id: str = "",
    work_item_id: str = "",
    manifest_hash: str = "",
    manifest_epoch: str = "current",
    route_id: str = "",
) -> str:
    example_status = json.dumps(
        {
            "packet_id": packet_id,
            "role": "worker",
            "status": "blocked",
            "branch_id": branch_id,
            "work_item_id": work_item_id,
            "manifest_hash": manifest_hash,
            "manifest_epoch": manifest_epoch,
            "worktree_path": worktree,
            "route_id": route_id,
            "evidence_summary": "replace with concise evidence summary",
            "branch": branch,
            "worktree": worktree,
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "changed_files": [],
            "commands_run": ["pwd", "git status --short --branch"],
            "tests": [],
            "blockers": ["replace with concrete blocker"],
            "handoff": "replace with concise handoff",
        },
        separators=(",", ":"),
    )
    return f"""# Worker Packet {packet_id}

You are Worker {packet_id}.

Worktree: {worktree}
Branch: {branch}

You are not alone in the codebase. Do not revert edits made by others. Own only the files/modules assigned here. If the task needs more than roughly 40k tokens of context, stop and return `blocked` instead of broadening scope.

Selected worker ladder: {", ".join(selected_ladder)}
Route class: {route_class}
Route selection reason: {selection_reason}

Copy `branch_id`, `work_item_id`, `manifest_hash`, `manifest_epoch`, `worktree_path`, `route_id`, `route_class`, `selected_ladder`, and `selection_reason` exactly into the final worker status. Do not change model aliases, model ids, effort levels, or provider order.

{optional_list("Owned files/modules", owned_files)}

{context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)}

Before editing, run:

```bash
pwd
git status --short --branch
```

Task:

{task_text}

Return a worker status object matching `{schema_name}`. Allowed `status` values are exactly `pass`, `partial`, `blocked`, or `failed`. Use `pass` for successful completion; never use `success`. Include every field shown in the example status, especially non-empty `evidence_summary` and `handoff`. `changed_files` must contain repo-relative file paths only, without git porcelain prefixes such as `M ` or `?? `. `commands_run` and `tests` must contain exact command strings that were actually run.

If your CLI harness does not write `{schema_name}` directly, print the final status object between these exact marker lines and do not print any other JSON object between them:

{WORKER_STATUS_BEGIN}
{example_status}
{WORKER_STATUS_END}
"""


def prompt_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    selected_ladder: list[str] | None,
    route_class: str,
    selection_reason: str,
    packet_context_path: str = "",
    packet_context_inline: str = "",
    include_worktree_context_excerpts: bool = False,
    worker_attribution: dict[str, str] | None = None,
) -> str:
    if role == "reviewer":
        return reviewer_prompt(
            packet_id,
            branch,
            worktree,
            schema_name,
            context_files,
            packet_context_path,
            packet_context_inline,
            include_worktree_context_excerpts,
        )
    if role == "research-worker":
        return research_worker_prompt(
            packet_id,
            branch,
            worktree,
            schema_name,
            owned_files,
            context_files,
            task_text,
            include_worktree_context_excerpts,
        )
    if role == "worker":
        attribution = worker_attribution or {}
        return worker_prompt(
            packet_id,
            branch,
            worktree,
            schema_name,
            owned_files,
            context_files,
            task_text,
            selected_ladder or list(DEFAULT_WORKER_LADDER),
            route_class,
            selection_reason,
            include_worktree_context_excerpts,
            branch_id=attribution.get("branch_id", ""),
            work_item_id=attribution.get("work_item_id", ""),
            manifest_hash=attribution.get("manifest_hash", ""),
            manifest_epoch=attribution.get("manifest_epoch", "current"),
            route_id=attribution.get("route_id", ""),
        )
    raise SystemExit(f"unsupported role for prompt generation: {role}")


def launch_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    selected_ladder: list[str] | None,
    selection_reason: str,
    review_route: dict | None = None,
    review_semantic_hashes: dict[str, str] | None = None,
    review_reuse_policy: dict | None = None,
) -> str:
    if role in {"research-worker", "reviewer", "worker"}:
        return compact_launch_script()

    raise SystemExit(f"unsupported role for launch script generation: {role}")


def reviewer_ladder_from_route(review_route: dict | None) -> list[str]:
    route = review_route or {
        "selected_ladder": CONTRACT.review_route_for_tier(CONTRACT.REVIEW_MODEL_POLICY["default_tier"]),
        "selection_reason": "Default light reviewer route.",
    }
    selected = [item for item in route.get("selected_ladder", []) if isinstance(item, str) and item]
    return selected or CONTRACT.review_route_for_tier(CONTRACT.REVIEW_MODEL_POLICY["default_tier"])


def launch_config_base(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    sandbox: str,
    attempt_timeout_seconds: int,
) -> dict:
    return {
        "schema_version": 1,
        "role": role,
        "packet_id": packet_id,
        "branch": branch,
        "worktree": worktree,
        "schema_name": schema_name,
        "output_name": output_name,
        "state_artifact": "launcher-state.json",
        "sandbox": sandbox,
        "attempt_timeout_seconds": attempt_timeout_seconds,
        "timeout_kill_after_seconds": TIMEOUT_KILL_AFTER_SECONDS,
    }


def selected_commands_from_attempts(attempts: list[dict]) -> list[str]:
    commands: list[str] = []
    for attempt in attempts:
        command = attempt.get("rendered_command") or attempt.get("command")
        if isinstance(command, str) and command.strip():
            commands.append(command)
    return commands


def annotate_attempt_metadata(config: dict, retry_ordinal: str | None = None) -> dict:
    attempts = config.get("attempts")
    if not isinstance(attempts, list):
        return config
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        command = attempt.get("command")
        if isinstance(command, str) and command.strip():
            attempt.setdefault("rendered_command", command)
        attempt.setdefault("route_policy_version", ROUTE_POLICY_VERSION)
        if retry_ordinal:
            attempt.setdefault("retry_ordinal", retry_ordinal)
        attempt.setdefault(
            "telemetry_capability",
            {
                "token_usage": "best_effort",
                "source": "provider_or_harness_output",
            },
        )
    return config


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_launch_config_adapter(config: dict) -> None:
    attempts = config.get("attempts")
    defects: list[str] = []
    if not isinstance(attempts, list) or not attempts:
        defects.append("launch-config attempts must be a non-empty array")
    else:
        for index, attempt in enumerate(attempts):
            path = f"attempts[{index}]"
            if not isinstance(attempt, dict):
                defects.append(f"{path} must be an object")
                continue
            provider = attempt.get("harness_kind") or attempt.get("provider")
            if provider not in {"codex", BRIDGE_HARNESS_KIND}:
                defects.append(f"{path}.provider must be a supported route adapter, got {provider!r}")
                continue
            for key in ["alias", "model", "command", "rendered_command", "route_policy_version"]:
                if not _nonempty_string(attempt.get(key)):
                    defects.append(f"{path}.{key} must be a non-empty string")
            timeout = attempt.get("timeout_seconds")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                defects.append(f"{path}.timeout_seconds must be a positive integer")
            telemetry_capability = attempt.get("telemetry_capability")
            if not isinstance(telemetry_capability, dict) or not _nonempty_string(
                telemetry_capability.get("token_usage")
            ):
                defects.append(f"{path}.telemetry_capability.token_usage must describe token telemetry support")
            if provider == BRIDGE_HARNESS_KIND:
                if not _nonempty_string(attempt.get("command_binary")):
                    defects.append(f"{path}.command_binary is required for {provider} attempts")
                run_args = attempt.get("run_args")
                if not isinstance(run_args, list) or not run_args:
                    defects.append(f"{path}.run_args must be a non-empty array for {provider} attempts")
                if not _nonempty_string(attempt.get("run_readback")):
                    defects.append(f"{path}.run_readback is required for {provider} attempts")
                bridge = attempt.get("bridge")
                if not isinstance(bridge, dict):
                    defects.append(f"{path}.bridge must be an object for {provider} attempts")
                else:
                    for key in ["provider", "model", "variant", "permission_profile", "run_dir"]:
                        if not _nonempty_string(bridge.get(key)):
                            defects.append(f"{path}.bridge.{key} is required for {provider} attempts")
                    if _nonempty_string(bridge.get("provider")) and bridge.get("provider") != BRIDGE_PROVIDER_ID:
                        defects.append(f"{path}.bridge.provider must be {BRIDGE_PROVIDER_ID!r} for {provider} attempts")
                    if not _nonempty_string(bridge.get("pool_dir")):
                        defects.append(f"{path}.bridge.pool_dir is required for {provider} attempts")
    selected_ladder = config.get("selected_ladder")
    if selected_ladder is not None:
        aliases = (
            [attempt.get("alias") for attempt in attempts if isinstance(attempt, dict)]
            if isinstance(attempts, list)
            else []
        )
        if selected_ladder != aliases:
            defects.append("launch-config selected_ladder must exactly match attempt aliases")
    if defects:
        raise SystemExit("launch-config adapter validation failed before launch:\n" + "\n".join(defects))


def compact_launch_config(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    owned_files: list[str] | None = None,
    selected_ladder: list[str] | None = None,
    route_class: str = DEFAULT_WORKER_ROUTE_CLASS,
    selection_reason: str = "",
    model_catalog: dict | None = None,
    review_route: dict | None = None,
    review_semantic_hashes: dict[str, str] | None = None,
    review_reuse_policy: dict | None = None,
    telemetry_debug: bool = False,
    goal_config: dict | None = None,
    route_policy: dict | None = None,
    retry_ordinal: str | None = None,
) -> dict | None:
    telemetry_script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    selected_ladder = selected_ladder or list(DEFAULT_WORKER_LADDER)
    debug_config = (
        {
            "telemetry_debug_name": CONTRACT.TELEMETRY_DEBUG_NAME,
            "debug_events_name": CONTRACT.TELEMETRY_DEBUG_EVENTS_NAME,
        }
        if telemetry_debug
        else {}
    )
    if role == "worker":
        worker_attempts = worker_telemetry_attempts(selected_ladder, goal_config)
        return annotate_attempt_metadata(
            {
                **launch_config_base(
                    "worker",
                    packet_id,
                    branch,
                    worktree,
                    schema_name,
                    output_name,
                    "workspace-write",
                    WORKER_ATTEMPT_TIMEOUT_SECONDS,
                ),
                **debug_config,
                "route_class": route_class,
                "selected_ladder": selected_ladder,
                "selection_reason": selection_reason,
                "owned_files": owned_files or [],
                "worker_prompt": WORKER_PACKET_PROMPT,
                "status_markers": {
                    "begin": WORKER_STATUS_BEGIN,
                    "end": WORKER_STATUS_END,
                },
                "attempts": worker_attempts,
                "selected_commands": selected_commands_from_attempts(worker_attempts),
                "model_catalog": model_catalog or {},
                "route_policy": route_policy or {},
                "telemetry_script": telemetry_script,
                "terminal_message": f"All selected worker route attempts failed cleanly without producing {output_name}.",
                "retry_ordinal": retry_ordinal,
            },
            retry_ordinal=retry_ordinal,
        )
    if role == "research-worker":
        return annotate_attempt_metadata(
            {
                **launch_config_base(
                    "research-worker",
                    packet_id,
                    branch,
                    worktree,
                    schema_name,
                    output_name,
                    "read-only",
                    RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
                ),
                **debug_config,
                "retry_ordinal": retry_ordinal,
                "attempts": research_telemetry_attempts(),
                "telemetry_script": telemetry_script,
                "terminal_message": f"Research worker primary and fallback failed without producing {output_name}.",
            },
            retry_ordinal=retry_ordinal,
        )
    if role == "reviewer":
        reviewer_ladder = reviewer_ladder_from_route(review_route)
        selected_route = review_route or {}
        selection_tier = selected_route.get("tier")
        selection_context = selected_route.get("selection_context")
        terminal_commands = [
            (
                configured_route_commands([alias], goal_config)[0]
                if goal_config
                else (
                    bridge_telemetry_attempt(
                        alias, role="reviewer", timeout_seconds=REVIEWER_ATTEMPT_TIMEOUT_SECONDS, sandbox="read-only"
                    )["command"]
                    if CONTRACT.is_bridge_alias(alias)
                    else CONTRACT.codex_command(alias, sandbox="read-only", lean=True)
                )
            )
            for alias in reviewer_ladder
        ]
        return annotate_attempt_metadata(
            {
                **launch_config_base(
                    "reviewer",
                    packet_id,
                    branch,
                    worktree,
                    schema_name,
                    output_name,
                    "read-only",
                    REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
                ),
                **debug_config,
                "attempts": reviewer_telemetry_attempts(reviewer_ladder, goal_config),
                "telemetry_script": telemetry_script,
                "status_markers": _review_route_markers_for_role("reviewer"),
                "review_route": selected_route,
                "selection_context": selection_context
                or {"tier": selection_tier, "reasons": selected_route.get("selection_reason")},
                "semantic_input_hashes": review_semantic_hashes or {},
                "reuse_policy": review_reuse_policy
                or {
                    "mode": "new",
                    "accepted": False,
                    "semantic_hashes_match": False,
                    "source_review_path": None,
                    "source_telemetry_path": None,
                },
                "terminal_commands": terminal_commands,
                "terminal_message": f"Reviewer primary and fallback failed without producing {output_name}.",
                "retry_ordinal": retry_ordinal,
            },
            retry_ordinal=retry_ordinal,
        )
    return None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["worker", "research-worker", "reviewer"], required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument(
        "--out-dir",
        required=True,
        metavar="PARENT_DIR",
        help="Parent directory for packet directories, such as bundle/workers or bundle/reviewers; the packet id is appended automatically.",
    )
    parser.add_argument(
        "--manifest",
        help="Absolute path to job.manifest.json. Required for reviewer packets; optional for compact worker packets.",
    )
    parser.add_argument(
        "--pre-review-gate", help="Required for reviewer packets; absolute path to pre_review_gate.json."
    )
    parser.add_argument("--task-file")
    parser.add_argument("--owned-file", action="append", default=[])
    parser.add_argument("--context-file", action="append", default=[])
    parser.add_argument(
        "--include-worktree-context-excerpts",
        action="store_true",
        help="Embed bounded excerpts for worktree-local --context-file inputs in worker/research prompts; default is path-only.",
    )
    parser.add_argument(
        "--worker-route",
        action="append",
        nargs="+",
        default=[],
        help="Allowed worker route alias. Repeat to choose a non-empty ordered subsequence of the standard ladder.",
    )
    parser.add_argument(
        "--route-class",
        help=(
            "Worker route class. Defaults from the manifest work item when available; otherwise "
            f"{DEFAULT_WORKER_ROUTE_CLASS}. Known classes: {', '.join(WORKER_ROUTE_CLASSES)}."
        ),
    )
    parser.add_argument(
        "--model-catalog",
        help=(
            "Optional fresh check_model_catalog.py --json output. For worker packets, unsupported Codex "
            "route aliases are pruned from the default ladder and rejected when explicitly selected."
        ),
    )
    parser.add_argument(
        "--allow-route-pruning",
        action="store_true",
        help="Honor an explicit --worker-route subsequence that prunes a manifest-configured route-class ladder.",
    )
    parser.add_argument(
        "--selection-reason", help="Required when --worker-route is supplied; recorded in route.json and worker status."
    )
    parser.add_argument(
        "--replace", action="store_true", help="Archive an existing packet directory under attempts/ and recreate it."
    )
    return parser


class CommonInputs(NamedTuple):
    packet_id: str
    branch: str
    manifest_branch_id: str
    manifest: dict | None
    manifest_path: Path | None
    telemetry_debug: bool
    worktree: Path
    owned_files: list[str]
    context_files: list[str]
    task_file: Path | None


def resolve_common_inputs(args: argparse.Namespace) -> CommonInputs:
    packet_id = require_safe_label(args.packet_id, "packet-id")
    branch = args.branch
    if not safe_branch_name(branch):
        raise SystemExit(f"branch is not a safe git branch name: {branch!r}")
    manifest_branch_id = branch
    manifest: dict | None = None
    manifest_path: Path | None = None
    telemetry_debug = False
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    owned_files = normalize_owned_paths(args.owned_file)
    context_files = normalize_context_files(args.context_file)
    if args.manifest and args.role == "worker":
        manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
        manifest = load_json(manifest_path)
        telemetry_debug = CONTRACT.telemetry_debug_enabled(manifest)
        branch_data = branch_entry_for_packet(manifest, branch, packet_id)
        if branch_data:
            branch_id_value = branch_data.get("id")
            branch_name_value = branch_data.get("branch_name")
            if isinstance(branch_id_value, str) and branch_id_value.strip():
                manifest_branch_id = branch_id_value
            if isinstance(branch_name_value, str) and branch_name_value.strip():
                if not safe_branch_name(branch_name_value):
                    raise SystemExit(f"manifest branch_name is not a safe git branch name: {branch_name_value!r}")
                branch = branch_name_value
        manifest_value = manifest_path.as_posix()
        if manifest_value not in context_files:
            context_files.append(manifest_value)
    elif args.manifest and args.role == "research-worker":
        raise SystemExit("--manifest is only valid for worker compact context or reviewer packet generation")
    task_file = resolve_absolute_path(args.task_file, "--task-file", must_exist=True) if args.task_file else None
    if args.role in {"research-worker", "reviewer"} and (args.worker_route or args.selection_reason):
        raise SystemExit("research-worker and reviewer packets must not set worker route options")
    if args.model_catalog and args.role != "worker":
        raise SystemExit("--model-catalog is only valid for worker packets")
    return CommonInputs(
        packet_id=packet_id,
        branch=branch,
        manifest_branch_id=manifest_branch_id,
        manifest=manifest,
        manifest_path=manifest_path,
        telemetry_debug=telemetry_debug,
        worktree=worktree,
        owned_files=owned_files,
        context_files=context_files,
        task_file=task_file,
    )


class ReviewerResolution(NamedTuple):
    early_return: bool
    manifest: dict | None
    manifest_path: Path | None
    manifest_branch_id: str
    telemetry_debug: bool
    gate_path: Path | None
    gate: dict | None
    review_route: dict | None
    review_semantic_hashes: dict[str, str] | None
    review_reuse_policy: dict | None


def resolve_reviewer_route(args: argparse.Namespace, *, packet_id: str, manifest_branch_id: str) -> ReviewerResolution:
    if not args.manifest:
        raise SystemExit("reviewer packets require --manifest")
    if not args.pre_review_gate:
        raise SystemExit("reviewer packets require --pre-review-gate")
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    gate_path = resolve_absolute_path(args.pre_review_gate, "--pre-review-gate", must_exist=True)
    manifest = load_json(manifest_path)
    telemetry_debug = CONTRACT.telemetry_debug_enabled(manifest)
    gate = load_json(gate_path)
    branch_id = packet_id.split("-R", 1)[0] if "-R" in packet_id else ""
    manifest_branch_id = branch_id or manifest_branch_id
    defects: list[str] = []
    STATUS_VALIDATION.validate_pre_review_gate_artifact(
        defects,
        gate_path,
        "pre_review_gate",
        manifest_path=manifest_path,
        branch_id=branch_id,
        review_packet_id=packet_id,
        required_input_paths=branch_validator().required_pre_review_input_paths(
            branch_entry(manifest, branch_id),
            branch_id,
            bundle_dir=manifest_path.parent,
        ),
    )
    if defects:
        raise SystemExit("pre-review gate failed; refusing reviewer packet generation:\n" + "\n".join(defects))
    gate_reuse_policy = gate.get("reuse_policy") if isinstance(gate.get("reuse_policy"), dict) else {}
    if gate_reuse_policy.get("accepted") is True and gate_reuse_policy.get("source_telemetry_path"):
        print("pre-review gate accepted reviewer reuse with telemetry; no reviewer model packet generated")
        return ReviewerResolution(
            early_return=True,
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_branch_id=manifest_branch_id,
            telemetry_debug=telemetry_debug,
            gate_path=gate_path,
            gate=gate,
            review_route=None,
            review_semantic_hashes=None,
            review_reuse_policy=None,
        )
    review_route = select_review_route(
        manifest,
        gate,
        branch_id=branch_id,
        packet_id=packet_id,
        manifest_path=manifest_path,
    )
    review_semantic_hashes = (
        {
            key: value
            for key, value in gate.get("semantic_input_hashes", {}).items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(gate.get("semantic_input_hashes"), dict)
        else {}
    )
    review_reuse_policy = {
        "mode": "new",
        "accepted": False,
        "semantic_hashes_match": False,
        "source_review_path": None,
        "source_telemetry_path": None,
    }
    if gate_reuse_policy.get("accepted") is True:
        review_reuse_policy = dict(gate_reuse_policy)
    return ReviewerResolution(
        early_return=False,
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_branch_id=manifest_branch_id,
        telemetry_debug=telemetry_debug,
        gate_path=gate_path,
        gate=gate,
        review_route=review_route,
        review_semantic_hashes=review_semantic_hashes,
        review_reuse_policy=review_reuse_policy,
    )


class WorkerRouting(NamedTuple):
    selected_ladder: list[str]
    route_class: str
    selection_reason: str
    model_catalog: dict | None
    manifest: dict | None
    manifest_path: Path | None
    telemetry_debug: bool
    manifest_work_item: dict | None
    worker_route_policy: dict | None
    goal_config: dict | None


def resolve_worker_routing(
    args: argparse.Namespace,
    *,
    packet_id: str,
    manifest_branch_id: str,
    manifest: dict | None,
    manifest_path: Path | None,
    telemetry_debug: bool,
    context_files: list[str],
) -> WorkerRouting:
    normalized_worker_routes: list[str] = []
    for item in args.worker_route:
        if isinstance(item, str):
            normalized_worker_routes.append(item)
        else:
            normalized_worker_routes.extend(item)
    manifest_work_item: dict | None = None
    manifest_context = find_manifest_context(context_files, manifest_branch_id, packet_id)
    if manifest_context is not None:
        _manifest_path, _manifest, _branch_data, manifest_work_item = manifest_context
        manifest = _manifest
        manifest_path = _manifest_path
        telemetry_debug = telemetry_debug or CONTRACT.telemetry_debug_enabled(_manifest)
    worker_policy = worker_policy_from_manifest(manifest)
    goal_config = goal_config_from_manifest(manifest, manifest_path)
    manifest_configured_worker_policy = worker_policy_is_manifest_configured(manifest, worker_policy)
    worker_default_ladder = policy_default_ladder(worker_policy)
    worker_allowed_routes = policy_allowed_routes(worker_policy)
    explicit_worker_routes = bool(normalized_worker_routes)
    manifest_route_class = manifest_work_item.get("route_class") if isinstance(manifest_work_item, dict) else None
    route_class = normalize_route_class(
        args.route_class
        or manifest_route_class
        or ("custom" if normalized_worker_routes else DEFAULT_WORKER_ROUTE_CLASS)
    )
    selected_ladder = (
        normalize_worker_ladder(
            normalized_worker_routes,
            default_ladder=worker_default_ladder,
            allowed_routes=worker_allowed_routes,
        )
        if normalized_worker_routes
        else ladder_for_route_class(route_class, worker_policy)
    )
    explicit_route_prunes_configured_ladder = explicit_route_would_prune_configured_ladder(
        selected_ladder,
        route_class=route_class,
        worker_policy=worker_policy,
        explicit_routes=explicit_worker_routes and manifest_configured_worker_policy,
    )
    selected_ladder, restored_configured_ladder = restore_configured_ladder_when_unpruned(
        selected_ladder,
        route_class=route_class,
        worker_policy=worker_policy,
        explicit_routes=explicit_worker_routes and manifest_configured_worker_policy,
        allow_route_pruning=args.allow_route_pruning,
    )
    catalog_path = (
        resolve_absolute_path(args.model_catalog, "--model-catalog", must_exist=True) if args.model_catalog else None
    )
    selected_ladder, model_catalog = apply_model_catalog_to_worker_ladder(
        selected_ladder,
        catalog_path=catalog_path,
        explicit_routes=bool(normalized_worker_routes),
    )
    selection_reason = nonempty_text(args.selection_reason)
    if args.worker_route and not selection_reason:
        raise SystemExit("--selection-reason is required when --worker-route is supplied")
    if not selection_reason:
        if manifest_configured_worker_policy:
            selection_reason = f"{route_class} route class selected from manifest worker_model_policy: " + ", ".join(
                selected_ladder
            )
        else:
            selection_reason = default_selection_reason(route_class)
    if restored_configured_ladder:
        selection_reason += (
            " Explicit worker route was expanded to the full configured route-class ladder; "
            "use --allow-route-pruning with a pruning reason to keep a shorter ladder."
        )
    if args.allow_route_pruning and explicit_route_prunes_configured_ladder:
        validate_route_pruning_reason(selection_reason)
    if model_catalog and model_catalog.get("filtered_aliases"):
        aliases = ", ".join(str(item.get("alias")) for item in model_catalog["filtered_aliases"])
        selection_reason += f" Model catalog pruned unavailable Codex route(s): {aliases}."
    validate_route_class_selection(
        route_class,
        selected_ladder,
        selection_reason,
        worker_policy if manifest_configured_worker_policy else None,
    )
    worker_route_policy = route_policy_metadata(
        route_class=route_class,
        worker_policy=worker_policy,
        explicit_routes=explicit_worker_routes,
        allow_route_pruning=args.allow_route_pruning,
        pruned_configured_ladder=explicit_route_prunes_configured_ladder and not restored_configured_ladder,
        restored_configured_ladder=restored_configured_ladder,
    )
    return WorkerRouting(
        selected_ladder=selected_ladder,
        route_class=route_class,
        selection_reason=selection_reason,
        model_catalog=model_catalog,
        manifest=manifest,
        manifest_path=manifest_path,
        telemetry_debug=telemetry_debug,
        manifest_work_item=manifest_work_item,
        worker_route_policy=worker_route_policy,
        goal_config=goal_config,
    )


class PacketSchema(NamedTuple):
    schema_name: str
    output_name: str
    schema: dict
    worker_attribution: dict[str, str]


def build_packet_schema(
    args: argparse.Namespace,
    *,
    packet_id: str,
    branch: str,
    worktree: Path,
    manifest: dict | None,
    manifest_path: Path | None,
    manifest_branch_id: str,
    manifest_work_item: dict | None,
    selected_ladder: list[str] | None,
    route_class: str,
    review_semantic_hashes: dict[str, str] | None,
    review_reuse_policy: dict | None,
) -> PacketSchema:
    worker_attribution: dict[str, str] = {}
    if args.role == "reviewer":
        schema_name = "review.schema.json"
        output_name = "review.json"
        schema = review_schema(packet_id, review_semantic_hashes, review_reuse_policy)
    elif args.role == "research-worker":
        schema_name = "research.schema.json"
        output_name = "research.json"
        schema = research_schema(packet_id, branch, str(worktree))
    else:
        schema_name = "status.schema.json"
        output_name = "status.json"
        manifest_hash = CONTEXT_PACK.sha256_file(manifest_path) if manifest_path else ""
        work_item_id = manifest_work_item.get("id") if isinstance(manifest_work_item, dict) else ""
        manifest_epoch = (
            str(manifest.get("manifest_epoch") or manifest.get("epoch") or "current")
            if isinstance(manifest, dict)
            else "current"
        )
        route_id = f"{packet_id}:{route_class}:{','.join(selected_ladder or [])}"
        worker_attribution = {
            "branch_id": manifest_branch_id,
            "work_item_id": str(work_item_id),
            "manifest_hash": manifest_hash,
            "manifest_epoch": manifest_epoch,
            "worktree_path": str(worktree),
            "route_id": route_id,
            "evidence_summary": "worker output is attributed to manifest, work item, route, and worktree",
        }
        schema = status_schema(
            packet_id,
            branch,
            str(worktree),
            selected_ladder,
            branch_id=manifest_branch_id,
            work_item_id=str(work_item_id),
            manifest_hash=manifest_hash,
            manifest_epoch=manifest_epoch,
            route_id=route_id,
        )
    return PacketSchema(
        schema_name=schema_name,
        output_name=output_name,
        schema=schema,
        worker_attribution=worker_attribution,
    )


class PacketContext(NamedTuple):
    task_text: str
    context_files: list[str]
    owned_files: list[str]
    packet_context: dict | None
    packet_context_path: str
    packet_context_inline: str


def build_packet_context(
    args: argparse.Namespace,
    *,
    packet_id: str,
    manifest_branch_id: str,
    worktree: Path,
    manifest: dict | None,
    manifest_path: Path | None,
    manifest_work_item: dict | None,
    gate_path: Path | None,
    gate: dict | None,
    review_route: dict | None,
    schema_name: str,
    output_name: str,
    packet_dir: Path,
    task_text: str,
    owned_files: list[str],
    context_files: list[str],
    task_file: Path | None,
) -> PacketContext:
    packet_context: dict | None = None
    packet_context_path = ""
    if args.role == "reviewer":
        packet_context_path = (packet_dir / "packet-context.json").resolve().as_posix()
        packet_context = reviewer_packet_context(
            packet_id=packet_id,
            branch_id=manifest_branch_id,
            worktree=worktree,
            manifest_path=manifest_path,
            manifest=manifest,
            gate_path=gate_path,
            gate=gate,
            review_route=review_route or {},
            review_schema_path=packet_dir / schema_name,
            review_output_path=packet_dir / output_name,
        )
    packet_context_inline = json.dumps(packet_context, indent=2, sort_keys=True) if packet_context is not None else ""
    if args.role == "worker":
        compact_context = compact_worker_context(
            branch_id=manifest_branch_id,
            packet_id=packet_id,
            task_file=task_file,
            task_text=task_text,
            owned_files=owned_files,
            context_files=context_files,
        )
        if compact_context is not None:
            task_text, context_files, packet_context = compact_context
            work_item = packet_context.get("work_item") if isinstance(packet_context, dict) else None
            manifest_owned_files = compact_list(work_item.get("owned_paths")) if isinstance(work_item, dict) else []
            if manifest_owned_files:
                owned_files = manifest_owned_files

    validate_runtime_context_inputs(
        packet_id=packet_id,
        worktree=worktree,
        context_files=context_files,
        manifest=manifest,
        manifest_branch_id=manifest_branch_id,
        manifest_path=manifest_path,
        manifest_work_item=manifest_work_item,
    )
    return PacketContext(
        task_text=task_text,
        context_files=context_files,
        owned_files=owned_files,
        packet_context=packet_context,
        packet_context_path=packet_context_path,
        packet_context_inline=packet_context_inline,
    )


def build_worker_route_artifact(
    *,
    packet_id: str,
    branch: str,
    manifest_branch_id: str,
    route_class: str,
    selected_ladder: list[str] | None,
    selection_reason: str,
    manifest: dict | None,
    worker_route_policy: dict | None,
    model_catalog: dict | None,
    context_budget: dict,
) -> dict:
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": "worker",
        "branch_id": manifest_branch_id,
        "branch": branch,
        "route_class": route_class,
        "selected_ladder": selected_ladder,
        "selection_reason": selection_reason,
        "policy_router": worker_policy_from_manifest(manifest).get("router", "goal-config-v1"),
        "policy_version": worker_policy_from_manifest(manifest).get("version", ROUTE_POLICY_VERSION),
        "route_policy_version": ROUTE_POLICY_VERSION,
        "default_ladder": policy_default_ladder(worker_policy_from_manifest(manifest)),
        "allowed_aliases": policy_allowed_routes(worker_policy_from_manifest(manifest)),
        "route_catalog_sha256": model_catalog.get("sha256") if isinstance(model_catalog, dict) else None,
        "route_catalog_source": model_catalog.get("source") if isinstance(model_catalog, dict) else None,
        "model_catalog": model_catalog or {},
        "route_policy": worker_route_policy or {},
        "context_budget": context_budget,
    }


def emit_packet_files(
    packet_dir: Path,
    *,
    schema_name: str,
    schema: dict,
    packet_context: dict | None,
    prompt_text: str,
    route: dict | None,
    launch_config: dict | None,
    launch_script: str,
) -> None:
    packet_dir.mkdir(parents=True, exist_ok=True)

    write_json(packet_dir / schema_name, schema)
    if packet_context is not None:
        write_json(packet_dir / "packet-context.json", packet_context)
    (packet_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")
    if route is not None:
        write_json(packet_dir / "route.json", route)
    if launch_config is not None:
        write_json(packet_dir / "launch-config.json", launch_config)
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch_script, encoding="utf-8")
    os.chmod(launch_path, 0o755)


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    common = resolve_common_inputs(args)
    packet_id = common.packet_id
    branch = common.branch
    manifest_branch_id = common.manifest_branch_id
    manifest = common.manifest
    manifest_path = common.manifest_path
    telemetry_debug = common.telemetry_debug
    worktree = common.worktree
    owned_files = common.owned_files
    context_files = common.context_files
    task_file = common.task_file

    review_route: dict | None = None
    review_semantic_hashes: dict[str, str] | None = None
    review_reuse_policy: dict | None = None
    gate_path: Path | None = None
    gate: dict | None = None
    if args.role == "reviewer":
        reviewer = resolve_reviewer_route(
            args,
            packet_id=packet_id,
            manifest_branch_id=manifest_branch_id,
        )
        manifest = reviewer.manifest
        manifest_path = reviewer.manifest_path
        manifest_branch_id = reviewer.manifest_branch_id
        telemetry_debug = reviewer.telemetry_debug
        gate_path = reviewer.gate_path
        gate = reviewer.gate
        if reviewer.early_return:
            return 0
        review_route = reviewer.review_route
        review_semantic_hashes = reviewer.review_semantic_hashes
        review_reuse_policy = reviewer.review_reuse_policy
    selected_ladder: list[str] | None = None
    route_class = DEFAULT_WORKER_ROUTE_CLASS
    selection_reason = ""
    model_catalog: dict | None = None
    manifest_work_item: dict | None = None
    worker_route_policy: dict | None = None
    goal_config: dict | None = None
    if args.role == "worker":
        routing = resolve_worker_routing(
            args,
            packet_id=packet_id,
            manifest_branch_id=manifest_branch_id,
            manifest=manifest,
            manifest_path=manifest_path,
            telemetry_debug=telemetry_debug,
            context_files=context_files,
        )
        selected_ladder = routing.selected_ladder
        route_class = routing.route_class
        selection_reason = routing.selection_reason
        model_catalog = routing.model_catalog
        manifest = routing.manifest
        manifest_path = routing.manifest_path
        telemetry_debug = routing.telemetry_debug
        manifest_work_item = routing.manifest_work_item
        worker_route_policy = routing.worker_route_policy
        goal_config = routing.goal_config

    packet_schema = build_packet_schema(
        args,
        packet_id=packet_id,
        branch=branch,
        worktree=worktree,
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_branch_id=manifest_branch_id,
        manifest_work_item=manifest_work_item,
        selected_ladder=selected_ladder,
        route_class=route_class,
        review_semantic_hashes=review_semantic_hashes,
        review_reuse_policy=review_reuse_policy,
    )
    schema_name = packet_schema.schema_name
    output_name = packet_schema.output_name
    schema = packet_schema.schema
    worker_attribution = packet_schema.worker_attribution

    validate_openai_strict_schema(schema, schema_name)
    task_text = load_task(task_file)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    if out_dir.name == packet_id:
        raise SystemExit("--out-dir must be the parent packet directory; this script appends --packet-id automatically")
    packet_dir = out_dir / packet_id

    context = build_packet_context(
        args,
        packet_id=packet_id,
        manifest_branch_id=manifest_branch_id,
        worktree=worktree,
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_work_item=manifest_work_item,
        gate_path=gate_path,
        gate=gate,
        review_route=review_route,
        schema_name=schema_name,
        output_name=output_name,
        packet_dir=packet_dir,
        task_text=task_text,
        owned_files=owned_files,
        context_files=context_files,
        task_file=task_file,
    )
    task_text = context.task_text
    context_files = context.context_files
    owned_files = context.owned_files
    packet_context = context.packet_context
    packet_context_path = context.packet_context_path
    packet_context_inline = context.packet_context_inline

    prompt_text = prompt_for(
        args.role,
        packet_id,
        branch,
        str(worktree),
        schema_name,
        owned_files,
        context_files,
        task_text,
        selected_ladder,
        route_class,
        selection_reason,
        packet_context_path,
        packet_context_inline,
        args.include_worktree_context_excerpts,
        worker_attribution=worker_attribution,
    )
    context_budget = context_budget_report(
        prompt_text=prompt_text,
        task_text=task_text,
        context_files=context_files,
        include_worktree_context_excerpts=args.include_worktree_context_excerpts,
    )
    enforce_context_budget(packet_id, context_budget)
    if packet_context is not None:
        packet_context["context_budget"] = context_budget

    route: dict | None = None
    if args.role == "worker":
        route = build_worker_route_artifact(
            packet_id=packet_id,
            branch=branch,
            manifest_branch_id=manifest_branch_id,
            route_class=route_class,
            selected_ladder=selected_ladder,
            selection_reason=selection_reason,
            manifest=manifest,
            worker_route_policy=worker_route_policy,
            model_catalog=model_catalog,
            context_budget=context_budget,
        )
    elif args.role == "reviewer" and review_route is not None:
        route = review_route
    scheduler_path = worker_scheduler_path(manifest_path, manifest_branch_id) if args.role == "worker" else None
    refuse_closed_pass_replacement(
        packet_dir,
        replace=args.replace,
        scheduler_path=scheduler_path,
        packet_id=packet_id,
    )
    retry_ordinal = archive_existing_packet_dir(packet_dir, replace=args.replace)
    launch_config = compact_launch_config(
        args.role,
        packet_id,
        branch,
        str(worktree),
        schema_name,
        output_name,
        owned_files=owned_files,
        selected_ladder=selected_ladder,
        route_class=route_class,
        selection_reason=selection_reason,
        model_catalog=model_catalog,
        review_route=review_route,
        review_semantic_hashes=review_semantic_hashes,
        review_reuse_policy=review_reuse_policy,
        telemetry_debug=telemetry_debug,
        goal_config=goal_config if args.role == "worker" else goal_config_from_manifest(manifest, manifest_path),
        route_policy=worker_route_policy,
        retry_ordinal=retry_ordinal,
    )
    if launch_config is not None:
        launch_config["context_budget"] = context_budget
        if args.role == "worker":
            launch_config.update(worker_attribution)
            if scheduler_path is not None:
                launch_config["scheduler_guard"] = {
                    "scheduler_path": scheduler_path.resolve().as_posix(),
                    "packet_id": packet_id,
                    "closed_pass_action": "refuse_clean_outputs",
                }
        validate_launch_config_adapter(launch_config)
        launch_config["adapter_validation"] = {
            "status": "pass",
            "checked_attempts": [
                attempt.get("alias")
                for attempt in launch_config.get("attempts", [])
                if isinstance(attempt, dict) and isinstance(attempt.get("alias"), str)
            ],
        }

    launch_script = launch_for(
        args.role,
        packet_id,
        branch,
        str(worktree),
        schema_name,
        output_name,
        selected_ladder,
        selection_reason,
        review_route=review_route,
        review_semantic_hashes=review_semantic_hashes,
        review_reuse_policy=review_reuse_policy,
    )
    emit_packet_files(
        packet_dir,
        schema_name=schema_name,
        schema=schema,
        packet_context=packet_context,
        prompt_text=prompt_text,
        route=route,
        launch_config=launch_config,
        launch_script=launch_script,
    )
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
