#!/usr/bin/env python3
"""Validate goal orchestration config model availability and harness smokes."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import shlex
import time
import sys
from pathlib import Path
from typing import Any, NamedTuple


ROLE_ALIASES = {
    "lite": "lite_agent",
    "flash": "lite_agent",
    "lite_agent": "lite_agent",
    "demanding": "demanding_agent",
    "pro": "demanding_agent",
    "heavy": "demanding_agent",
    "demanding_agent": "demanding_agent",
}
MAX_ERROR_MESSAGE_CHARS = 220
VALIDATION_MODES = {"model-check", "smoke", "debug"}
DISCOVERY_PROFILES: dict[str, dict[str, Any]] = {
    "mixed-fast": {
        "early_accept_count": 4,
        "candidates": [
            {
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "alias": "ds-flash-max",
            },
            {
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "alias": "ds-pro-max",
            },
            {"harness": "codex", "provider": "openai", "model": "gpt-5.4-mini", "alias": "codex-mini"},
            {"harness": "codex", "provider": "openai", "model": "gpt-5.4", "alias": "codex-heavy"},
            {"harness": "codex", "provider": "openai", "model": "gpt-5.3-codex-spark", "alias": "codex-spark"},
            {"harness": "codex", "provider": "openai", "model": "gpt-5.5", "alias": "gpt-5-5"},
        ],
    },
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a JSON object: {path}")
    return data


def load_contract() -> Any:
    shared_path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    spec = importlib.util.spec_from_file_location("_goal_shared_orchestration_contract", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared contract: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Single source of truth for harness kinds (orchestration_contract); kept in lockstep
# with the runtime launcher's dispatchable kinds by scripts/check_harness_contract.py.
HARNESS_KIND_VALUES = frozenset(load_contract().SUPPORTED_HARNESS_KINDS)


def command_result(command: list[str], *, timeout_seconds: int | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed_ms": elapsed_ms,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed_ms,
            "timed_out": True,
        }


def route_id(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return sanitized or "route"


def discovery_smoke(role: str, timeout_seconds: int) -> dict[str, Any]:
    token = f"GOAL_CONFIG_DISCOVER_{route_id(role).upper()}_SMOKE_OK"
    return {
        "prompt": f"Reply with {token} and nothing else.",
        "expect": token,
        "timeout_seconds": timeout_seconds,
    }


def short_message(message: str) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    if len(compact) > MAX_ERROR_MESSAGE_CHARS:
        return compact[: MAX_ERROR_MESSAGE_CHARS - 3].rstrip() + "..."
    return compact


def route_key(model: dict[str, Any]) -> tuple[str, str, str] | None:
    harness = model.get("harness")
    provider = model.get("provider")
    model_id = model.get("model")
    if not all(isinstance(value, str) and value for value in (harness, provider, model_id)):
        return None
    return str(harness), str(provider), str(model_id)


def json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def load_smoke_cache(paths: list[Path]) -> dict[tuple[str, str, str], dict[str, Any]]:
    cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in paths:
        report = load_json(path)
        for item in report.get("harnesses") or []:
            if not isinstance(item, dict):
                continue
            key = route_key(item)
            smoke = item.get("smoke")
            if key is None or not isinstance(smoke, dict) or smoke.get("status") != "pass":
                continue
            cache[key] = {
                "smoke": json_clone(smoke),
                "source_role": item.get("role"),
                "source_report": path.as_posix(),
            }
    return cache


def reusable_smoke_route_report(paths: list[Path]) -> dict[str, Any]:
    checked_roles: list[str] = []
    accepted_routes: list[dict[str, Any]] = []
    rejected_routes: list[dict[str, Any]] = []
    skipped_routes: list[dict[str, Any]] = []
    unvisited_routes: list[dict[str, Any]] = []
    harness_reports: list[dict[str, Any]] = []
    failures: list[str] = []
    used_reports: list[dict[str, Any]] = []

    for path in paths:
        report = load_json(path)
        if report.get("status") != "pass":
            failures.append(f"reusable smoke report status must be pass: {path}")
            continue
        used_reports.append(
            {
                "path": path.resolve().as_posix(),
                "mode": report.get("mode"),
                "check_mode": report.get("check_mode"),
                "config_path": report.get("config_path"),
            }
        )
        for role in report.get("checked_roles") or []:
            if isinstance(role, str) and role not in checked_roles:
                checked_roles.append(role)
        for key, target in [
            ("accepted_routes", accepted_routes),
            ("rejected_routes", rejected_routes),
            ("skipped_routes", skipped_routes),
            ("unvisited_routes", unvisited_routes),
            ("harnesses", harness_reports),
        ]:
            values = report.get(key)
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, dict):
                        target.append(json_clone(item))

    return {
        "checked_roles": checked_roles,
        "accepted_routes": accepted_routes,
        "rejected_routes": rejected_routes,
        "skipped_routes": skipped_routes,
        "unvisited_routes": unvisited_routes,
        "harnesses": harness_reports,
        "failures": failures,
        "used_reports": used_reports,
    }


def cached_smoke_report(
    cache: dict[tuple[str, str, str], dict[str, Any]], model: dict[str, Any]
) -> dict[str, Any] | None:
    key = route_key(model)
    if key is None or key not in cache:
        return None
    entry = cache[key]
    smoke = json_clone(entry["smoke"])
    smoke["reused"] = True
    smoke["reused_from_role"] = entry.get("source_role")
    if entry.get("source_report"):
        smoke["reused_from_report"] = entry["source_report"]
    return smoke


def remember_smoke(
    cache: dict[tuple[str, str, str], dict[str, Any]],
    model: dict[str, Any],
    role: str,
    smoke: dict[str, Any],
) -> None:
    key = route_key(model)
    if key is None or smoke.get("status") != "pass":
        return
    cache[key] = {
        "smoke": json_clone(smoke),
        "source_role": role,
        "source_report": "current",
    }


def empty_tokens() -> dict[str, int | None]:
    return {"input": None, "output": None, "reasoning": None, "cache_read": None, "cache_write": None}


def token_telemetry(
    tokens: dict[str, Any] | None, *, source: str, unavailable_reason: str | None = None
) -> dict[str, Any]:
    token_data = tokens if isinstance(tokens, dict) else {}
    present = {
        key: isinstance(token_data.get(key), int)
        for key in ("input", "output", "reasoning", "cache_read", "cache_write")
    }
    available = any(present.values())
    result: dict[str, Any] = {
        "available": available,
        "source": source,
        "fields_present": present,
    }
    if unavailable_reason and not available:
        result["reason"] = unavailable_reason
    return result


def focused_response_excerpt(output: str, expected: str, *, limit: int = 240) -> tuple[str, str]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for line in lines:
        if line == expected:
            return line[:limit], "expected_line"
    for line in lines:
        if expected in line:
            return line[:limit], "line_containing_expected"
    index = output.find(expected)
    if index >= 0:
        start = max(0, index - 80)
        end = min(len(output), index + len(expected) + 80)
        return output[start:end].strip()[:limit], "window_containing_expected"
    return output[:limit], "raw_prefix"


def model_roles(config: dict[str, Any], selected: list[str]) -> list[str]:
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        raise SystemExit("config.models must be a non-empty object")
    if not selected:
        return sorted(models)
    roles: list[str] = []
    seen: set[str] = set()
    expanded = [item.strip() for value in selected for item in value.split(",") if item.strip()]
    for value in expanded:
        role = ROLE_ALIASES.get(value, value)
        if role not in models:
            raise SystemExit(f"selected role {value!r} is not in config.models")
        if role not in seen:
            roles.append(role)
            seen.add(role)
    return roles


def resolve_binary(command: str | None) -> str | None:
    if not command:
        return None
    if os.path.isabs(command) or command.startswith("."):
        return command if Path(command).exists() else None
    return shutil.which(command)


def render_tokens(command: Any, *, context: dict[str, str]) -> list[str]:
    if not isinstance(command, list):
        return []
    rendered: list[str] = []
    for item in command:
        if not isinstance(item, str):
            continue
        rendered.append(item.format(**context))
    return rendered


def render_harness_args(harness: dict[str, Any], *, context: dict[str, str]) -> list[str]:
    args = harness.get("smoke_args")
    if args is None:
        return []
    rendered = render_tokens(args, context=context)
    return [token for token in rendered if token != ""]


def validate_harness_shape(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    harnesses = config.get("harnesses")
    if not isinstance(harnesses, dict) or not harnesses:
        failures.append("harnesses must be a non-empty object")
        return failures

    for name, harness in harnesses.items():
        if not isinstance(harness, dict):
            failures.append(f"harness {name} must be an object")
            continue
        kind = harness.get("kind")
        if kind not in HARNESS_KIND_VALUES:
            failures.append(f"harness {name} has unsupported kind: {kind}")
        command = harness.get("command")
        if not isinstance(command, str) or not command:
            failures.append(f"harness {name} missing command")
        smoke_args = harness.get("smoke_args")
        if not isinstance(smoke_args, list) or not smoke_args:
            failures.append(f"harness {name} missing smoke_args")
    return failures


def get_config_validation_mode(config: dict[str, Any]) -> str | None:
    validation = config.get("validation")
    if not isinstance(validation, dict):
        return None
    value = validation.get("mode")
    return value if isinstance(value, str) else None


def normalize_telemetry_collect(config: dict[str, Any], contract: Any) -> list[str]:
    telemetry = config.get("telemetry")
    if not isinstance(telemetry, dict):
        return ["telemetry must be an object"]

    failures: list[str] = []
    telemetry_mode = telemetry.get("mode", "standard")
    if telemetry_mode not in contract.TELEMETRY_POLICY_MODES:
        failures.append(
            f"telemetry.mode must be one of {tuple(contract.TELEMETRY_POLICY_MODES)}; got {telemetry_mode!r}"
        )

    if "schema_version" in telemetry and telemetry.get("schema_version") != contract.TELEMETRY_POLICY_SCHEMA_VERSION:
        failures.append(
            "telemetry.schema_version must match contract TELEMETRY_POLICY_SCHEMA_VERSION; "
            f"got {telemetry.get('schema_version')!r}"
        )

    collect = telemetry.get("collect")
    if collect is not None and not isinstance(collect, list):
        failures.append("telemetry.collect must be a list when provided")
    elif isinstance(collect, list):
        unsupported: list[str] = []
        for item in collect:
            if not isinstance(item, str):
                failures.append(f"telemetry.collect item must be a string: {item!r}")
                continue
            if item not in contract.TELEMETRY_COLLECT_ITEMS:
                unsupported.append(item)
        if unsupported:
            failures.append("telemetry.collect contains unsupported items: " + ", ".join(sorted(unsupported)))

    raw_text = telemetry.get("raw_text")
    if raw_text is not None and not isinstance(raw_text, bool):
        failures.append(f"telemetry.raw_text must be boolean; got {raw_text!r}")

    return failures


def validate_for_preflight(config: dict[str, Any], mode: str, contract: Any) -> list[str]:
    failures: list[str] = []
    aggressiveness = config.get("aggressiveness")
    if not isinstance(aggressiveness, dict):
        failures.append("aggressiveness must be an object")
        return failures

    branch_cap = aggressiveness.get("max_active_branch_agents")
    worker_cap = aggressiveness.get("max_active_worker_packets")
    max_waves = aggressiveness.get("max_waves")

    cap_checks = (
        ("max_active_branch_agents", branch_cap, int(contract.MAX_ACTIVE_BRANCH_AGENTS)),
        ("max_active_worker_packets", worker_cap, int(contract.MAX_WORKER_PACKETS_PER_BRANCH)),
        ("max_waves", max_waves, int(contract.MAX_WAVES)),
    )
    for name, value, cap in cap_checks:
        if not isinstance(value, int) or isinstance(value, bool):
            failures.append(f"aggressiveness.{name} must be an integer")
            continue
        if value < 1 or value > cap:
            failures.append(f"aggressiveness.{name} must be an integer from 1 to {cap}; got {value!r}")

    validation_mode = get_config_validation_mode(config)
    if validation_mode is None:
        failures.append("validation.mode must be an object field: model-check, smoke, or debug")
    elif validation_mode not in VALIDATION_MODES:
        failures.append(f"validation.mode must be one of {tuple(VALIDATION_MODES)}; got {validation_mode!r}")

    if validation_mode in {"smoke", "debug"} and mode not in {"smoke", "discover"}:
        failures.append(f"validation mode {validation_mode!r} requires smoke/discover check mode; got mode {mode!r}")

    telemetry_cfg = config.get("telemetry")
    telemetry_mode = telemetry_cfg.get("mode") if isinstance(telemetry_cfg, dict) else None
    if validation_mode == "debug" and (telemetry_mode or "standard") != "debug":
        failures.append("validation mode debug requires telemetry.mode=debug")

    failures.extend(normalize_telemetry_collect(config, contract))

    preflight_schema = [
        "token_counts",
        "text_counts",
        "time_counts",
    ]
    units = config.get("usage_units")
    if not isinstance(units, dict):
        failures.append("usage_units must be an object")
    else:
        for key in preflight_schema:
            if key not in units or not isinstance(units[key], list):
                failures.append(f"usage_units must include array for {key}")

    model_policies = config.get("model_policies")
    required_policies = {
        "worker_model_policy",
        "review_model_policy",
        "amender_model_policy",
        "lite_model_policy",
    }
    if not isinstance(model_policies, dict):
        failures.append("model_policies must be an object")
    elif not required_policies.issubset(model_policies):
        missing = ", ".join(sorted(required_policies - set(model_policies)))
        failures.append(f"model_policies missing required keys: {missing}")

    return failures


def remediate_for_preflight(
    config: dict[str, Any], *, mode: str, contract: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    remediated = copy.deepcopy(config)
    actions: list[dict[str, Any]] = []

    aggressiveness = remediated.setdefault("aggressiveness", {})
    if isinstance(aggressiveness, dict):
        cap_fields = (
            ("max_active_branch_agents", int(contract.MAX_ACTIVE_BRANCH_AGENTS)),
            ("max_active_worker_packets", int(contract.MAX_WORKER_PACKETS_PER_BRANCH)),
            ("max_waves", int(contract.MAX_WAVES)),
        )
        for field, cap in cap_fields:
            value = aggressiveness.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value > cap:
                aggressiveness[field] = cap
                actions.append(
                    {
                        "field": f"aggressiveness.{field}",
                        "action": "clamp",
                        "from": value,
                        "to": cap,
                        "reason": "preflight schema cap",
                    }
                )
        branch_cap = aggressiveness.get("max_active_branch_agents")
        max_waves = aggressiveness.get("max_waves")
        if isinstance(branch_cap, int) and isinstance(max_waves, int):
            total = branch_cap * max_waves
            if aggressiveness.get("total_branch_cap") != total:
                actions.append(
                    {
                        "field": "aggressiveness.total_branch_cap",
                        "action": "recompute",
                        "from": aggressiveness.get("total_branch_cap"),
                        "to": total,
                        "reason": "normalized branch cap multiplied by normalized max_waves",
                    }
                )
                aggressiveness["total_branch_cap"] = total

    telemetry = remediated.setdefault("telemetry", {})
    if isinstance(telemetry, dict):
        collect = telemetry.get("collect")
        unsupported = []
        if isinstance(collect, list):
            unsupported = [
                item for item in collect if isinstance(item, str) and item not in contract.TELEMETRY_COLLECT_ITEMS
            ]
        if unsupported or not isinstance(collect, list):
            telemetry["collect"] = list(contract.TELEMETRY_COLLECT_ITEMS)
            actions.append(
                {
                    "field": "telemetry.collect",
                    "action": "replace",
                    "from": collect,
                    "to": list(contract.TELEMETRY_COLLECT_ITEMS),
                    "reason": "preflight expects semantic telemetry groups; detailed counters belong in usage_units",
                }
            )
        if telemetry.get("schema_version") not in (None, contract.TELEMETRY_POLICY_SCHEMA_VERSION):
            actions.append(
                {
                    "field": "telemetry.schema_version",
                    "action": "set",
                    "from": telemetry.get("schema_version"),
                    "to": contract.TELEMETRY_POLICY_SCHEMA_VERSION,
                    "reason": "preflight telemetry policy schema version",
                }
            )
            telemetry["schema_version"] = contract.TELEMETRY_POLICY_SCHEMA_VERSION
        if telemetry.get("mode") == "debug":
            preflight_intent = remediated.setdefault("preflight_intent", {})
            if isinstance(preflight_intent, dict) and preflight_intent.get("telemetry_mode") != "debug":
                actions.append(
                    {
                        "field": "preflight_intent.telemetry_mode",
                        "action": "set",
                        "from": preflight_intent.get("telemetry_mode"),
                        "to": "debug",
                        "reason": "preserve debug telemetry intent for goal-preflight",
                    }
                )
                preflight_intent["telemetry_mode"] = "debug"

    validation_mode = get_config_validation_mode(remediated)
    if validation_mode in {"smoke", "debug"} and mode not in {"smoke", "discover"}:
        actions.append(
            {
                "field": "validation.mode",
                "action": "rerun_check",
                "from": validation_mode,
                "to": validation_mode,
                "reason": "rerun compatibility with --smoke or discovery mode; remediation intentionally preserves validation.mode",
            }
        )

    if actions:
        compatibility = remediated.setdefault("compatibility", {})
        history = compatibility.setdefault("preflight_remediation", [])
        if isinstance(history, list):
            history.append({"source": "check_goal_config.py --for-preflight", "actions": actions})
    return remediated, actions


def check_non_opencode_model(model: dict[str, Any], *, harness: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    provider = model.get("provider")
    provider_model = model.get("model")
    kind = harness.get("kind")
    binary = harness.get("command")
    resolved_binary = resolve_binary(binary)
    failures: list[str] = []
    result: dict[str, Any] = {
        "source": "live",
        "harness": kind,
        "provider": provider,
        "model": provider_model,
        "binary": resolved_binary,
        "command": binary,
    }
    if not isinstance(provider, str) or not provider:
        failures.append("missing provider")
    if not isinstance(provider_model, str) or not provider_model:
        failures.append("missing model")
    if failures:
        result["status"] = "failed"
        return result, failures
    result["status"] = "pass" if resolved_binary else "failed"
    if not resolved_binary:
        failures.append(f"{kind} binary not found")
    return result, failures


def run_harness_smoke(
    role: str,
    model: dict[str, Any],
    smoke: dict[str, Any],
    *,
    harness: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    if not isinstance(smoke, dict):
        return {"status": "failed", "reason": "missing smoke config"}, [f"{role}: missing smoke config"]

    prompt = smoke.get("prompt")
    expected = smoke.get("expect")
    if not isinstance(prompt, str) or not prompt:
        failures.append(f"{role} smoke prompt is missing")
    if not isinstance(expected, str) or not expected:
        failures.append(f"{role} smoke expected text is missing")
    timeout_seconds = int(smoke.get("timeout_seconds") or 600)
    if failures:
        return {"status": "failed"}, failures

    kind = harness.get("kind")
    binary = harness.get("command")
    if not isinstance(binary, str) or not binary:
        return {"status": "failed", "reason": "missing harness command"}, [f"{role} harness command missing"]

    context = {
        "role": role,
        "provider": model.get("provider", ""),
        "model": model.get("model", ""),
        "alias": model.get("alias", ""),
        "prompt": prompt,
    }
    smoke_args = render_harness_args(harness, context=context)
    if not smoke_args:
        return {"status": "failed", "reason": "missing smoke_args"}, [f"{role} harness smoke command is missing"]

    resolved = resolve_binary(binary)
    if resolved is None:
        return {
            "status": "failed",
            "reason": f"{kind} binary not found",
        }, [f"{role} {kind} binary not found"]

    result = command_result([resolved, *smoke_args], timeout_seconds=timeout_seconds)
    output = result["stdout"] + result["stderr"]
    response_excerpt, response_excerpt_source = focused_response_excerpt(output, expected)
    contains_expected = expected in output
    if result["timed_out"]:
        failures.append(f"{role} {kind} smoke timed out")
    if result["returncode"] != 0:
        failures.append(f"{role} {kind} smoke returncode={result['returncode']}")
    if not contains_expected:
        failures.append(f"{role} {kind} smoke output did not contain expected text")

    return {
        "status": "pass" if not failures else "failed",
        "returncode": result["returncode"],
        "timed_out": result["timed_out"],
        "elapsed_ms": result["elapsed_ms"],
        "stdout_chars": len(result["stdout"]),
        "stderr_chars": len(result["stderr"]),
        "response_chars": len(output),
        "contains_expected": contains_expected,
        "tokens": empty_tokens(),
        "token_telemetry": token_telemetry(
            empty_tokens(),
            source=str(kind or "generic-cli"),
            unavailable_reason="harness output did not expose token counters; compare character counts and elapsed_ms",
        ),
        "response_excerpt": response_excerpt,
        "response_excerpt_source": response_excerpt_source,
    }, failures


def run_or_reuse_smoke(
    role: str,
    model: dict[str, Any],
    smoke: dict[str, Any],
    *,
    harness: dict[str, Any],
    smoke_cache: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    cached = cached_smoke_report(smoke_cache, model)
    if cached is not None:
        return cached, []
    smoke_report, smoke_failures = run_harness_smoke(
        role,
        model,
        smoke,
        harness=harness,
    )
    remember_smoke(smoke_cache, model, role, smoke_report)
    return smoke_report, smoke_failures


def validate_config_shape(config: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if config.get("schema_version") != 1:
        failures.append("schema_version must be 1")
    units = config.get("usage_units")
    if not isinstance(units, dict):
        failures.append("usage_units must be an object")
    serialized = json.dumps(config, sort_keys=True).lower()
    for forbidden in ("usd", "dollar", "pricing", "price"):
        if forbidden in serialized:
            failures.append(f"config must not contain billing field or unit: {forbidden}")
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        failures.append("models must be a non-empty object")
    failures.extend(validate_harness_shape(config))
    return failures


def classify_routes(
    harness_reports: list[dict[str, Any]], *, smoke_requested: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for report in harness_reports:
        route = {
            "role": report.get("role"),
            "alias": report.get("alias"),
            "harness": report.get("harness"),
            "provider": report.get("provider"),
            "model": report.get("model"),
        }
        reasons: list[str] = []
        model_check = report.get("model_check") if isinstance(report.get("model_check"), dict) else {}
        if model_check.get("status") != "pass":
            reasons.append(str(model_check.get("reason") or f"model_check={model_check.get('status')}"))
        smoke = report.get("smoke") if isinstance(report.get("smoke"), dict) else None
        if smoke_requested:
            if smoke is None:
                reasons.append("smoke=missing")
            elif smoke.get("status") != "pass":
                reason = smoke.get("reason") or f"smoke={smoke.get('status')}"
                reasons.append(str(reason))
        if reasons:
            rejected.append({**route, "reasons": reasons})
        else:
            accepted.append(route)
    return accepted, rejected


def classify_skipped_routes(harness_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skipped: list[dict[str, Any]] = []
    for report in harness_reports:
        reasons: list[str] = []
        model_check = report.get("model_check") if isinstance(report.get("model_check"), dict) else {}
        smoke = report.get("smoke") if isinstance(report.get("smoke"), dict) else {}
        if model_check.get("status") == "skipped":
            reasons.append(str(model_check.get("reason") or "model_check=skipped"))
        if smoke.get("status") == "skipped":
            reasons.append(str(smoke.get("reason") or "smoke=skipped"))
        if not reasons:
            continue
        skipped.append(
            {
                "role": report.get("role"),
                "alias": report.get("alias"),
                "harness": report.get("harness"),
                "provider": report.get("provider"),
                "model": report.get("model"),
                "reasons": reasons,
            }
        )
    return skipped


def report_is_accepted(report: dict[str, Any], *, smoke_requested: bool) -> bool:
    accepted, _rejected = classify_routes([report], smoke_requested=smoke_requested)
    return bool(accepted)


def profile_discovery_candidates(
    profile_name: str,
    *,
    model_filter: str | None,
    max_candidates: int | None,
) -> list[dict[str, Any]]:
    profile = DISCOVERY_PROFILES.get(profile_name)
    if profile is None:
        raise SystemExit(f"unknown discovery profile: {profile_name}")
    matcher = re.compile(model_filter) if model_filter else None
    candidates: list[dict[str, Any]] = []
    for item in profile["candidates"]:
        candidate = dict(item)
        text = " ".join(str(candidate.get(key, "")) for key in ("harness", "provider", "model", "alias"))
        if matcher and not matcher.search(text):
            continue
        model_id = str(candidate.get("model", "route"))
        candidate["role"] = f"discover_{route_id(str(candidate.get('alias') or model_id))}"
        candidate.setdefault("alias", f"discover-{route_id(model_id)}")
        candidate["profile"] = profile_name
        candidates.append(candidate)
        if max_candidates is not None and len(candidates) >= max_candidates:
            break
    return candidates


def check_model_for_harness(
    model: dict[str, Any],
    *,
    harness: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    kind = harness.get("kind")
    if kind in HARNESS_KIND_VALUES:
        return check_non_opencode_model(model, harness=harness)
    return {"status": "failed", "reason": f"unsupported harness kind: {kind}"}, [f"unsupported harness kind: {kind}"]


def discover_profile_routes(
    config: dict[str, Any],
    *,
    profile_name: str,
    model_filter: str | None,
    max_candidates: int | None,
    smoke: bool,
    smoke_cache: dict[tuple[str, str, str], dict[str, Any]],
    discover_all_candidates: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    profile = DISCOVERY_PROFILES[profile_name]
    candidates = profile_discovery_candidates(
        profile_name,
        model_filter=model_filter,
        max_candidates=max_candidates,
    )
    harnesses = config.get("harnesses") if isinstance(config.get("harnesses"), dict) else {}
    effort_cfg = config.get("effort") if isinstance(config.get("effort"), dict) else {}
    timeout_seconds = int(effort_cfg.get("lite_timeout_seconds") or 600)
    failures: list[str] = []
    reports: list[dict[str, Any]] = []
    unvisited_routes: list[dict[str, Any]] = []
    accepted_count = 0
    early_accept_count = int(profile.get("early_accept_count") or 0)

    for index, candidate in enumerate(candidates):
        model = {
            "role": candidate["role"],
            "alias": candidate["alias"],
            "harness": candidate["harness"],
            "provider": candidate["provider"],
            "model": candidate["model"],
        }
        report: dict[str, Any] = dict(model)
        report["profile"] = profile_name

        harness = harnesses.get(candidate["harness"])
        if not isinstance(harness, dict):
            report["model_check"] = {"status": "failed", "reason": "harness not configured"}
            report["model_failures"] = [f"harness {candidate['harness']!r} is not configured"]
            reports.append(report)
            continue

        model_check, model_failures = check_model_for_harness(
            model,
            harness=harness,
        )
        report["model_check"] = model_check
        if model_failures:
            report["model_failures"] = model_failures
        if smoke and model_check.get("status") == "pass":
            smoke_report, _smoke_failures = run_or_reuse_smoke(
                candidate["role"],
                model,
                discovery_smoke(candidate["role"], timeout_seconds),
                harness=harness,
                smoke_cache=smoke_cache,
            )
            report["smoke"] = smoke_report
        elif smoke:
            report["smoke"] = {"status": "skipped", "reason": "model check failed"}
        reports.append(report)

        if report_is_accepted(report, smoke_requested=smoke):
            accepted_count += 1
            if early_accept_count and accepted_count >= early_accept_count and not discover_all_candidates:
                unvisited_routes = [
                    {
                        **route,
                        "reason": f"early_accept_count reached ({early_accept_count})",
                    }
                    for route in candidates[index + 1 :]
                ]
                break

    if not candidates:
        failures.append(f"discover profile {profile_name} produced no candidate routes")
    return candidates, reports, failures, unvisited_routes


def rejection_counts(rejected_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], dict[str, Any]] = {}
    for route in rejected_routes:
        if not isinstance(route, dict):
            continue
        reasons = route.get("reasons") if isinstance(route.get("reasons"), list) else []
        if not reasons:
            reasons = ["rejected"]
        for reason_value in reasons:
            message = short_message(str(reason_value))
            key = (
                str(route.get("harness") or ""),
                str(route.get("provider") or ""),
                message,
            )
            item = counts.setdefault(
                key,
                {
                    "harness": key[0],
                    "provider": key[1],
                    "status": "",
                    "message": message,
                    "count": 0,
                },
            )
            item["count"] += 1
    return list(counts.values())


def token_telemetry_summary(harness_reports: list[dict[str, Any]]) -> dict[str, Any]:
    available = 0
    unavailable = 0
    by_harness: dict[str, dict[str, int]] = {}
    for report in harness_reports:
        smoke = report.get("smoke") if isinstance(report.get("smoke"), dict) else {}
        telemetry = smoke.get("token_telemetry") if isinstance(smoke.get("token_telemetry"), dict) else None
        if telemetry is None:
            continue
        harness = str(report.get("harness") or report.get("harness_kind") or "unknown")
        item = by_harness.setdefault(harness, {"available": 0, "unavailable": 0})
        if telemetry.get("available") is True:
            available += 1
            item["available"] += 1
        else:
            unavailable += 1
            item["unavailable"] += 1
    return {
        "available_routes": available,
        "unavailable_routes": unavailable,
        "by_harness": by_harness,
    }


def attach_summary(result: dict[str, Any]) -> None:
    accepted_route_count = len(result.get("accepted_routes") or [])
    checked_role_count = len(result.get("checked_roles") or [])
    harness_count = len(result.get("harnesses") or [])
    route_model_availability_verified = accepted_route_count > 0
    route_verification_status = (
        "routes_verified"
        if route_model_availability_verified
        else "schema_pass_routes_not_checked"
        if result.get("status") == "pass"
        else "failed"
    )
    result["route_model_availability_verified"] = route_model_availability_verified
    result["route_verification_status"] = route_verification_status
    result["summary"] = {
        "accepted_route_count": accepted_route_count,
        "rejected_route_count": len(result.get("rejected_routes") or []),
        "skipped_route_count": len(result.get("skipped_routes") or []),
        "unvisited_route_count": len(result.get("unvisited_routes") or []),
        "checked_role_count": checked_role_count,
        "harness_count": harness_count,
        "route_model_availability_verified": route_model_availability_verified,
        "route_verification_status": route_verification_status,
        "failure_count": len(result.get("failures") or []),
        "rejection_counts": rejection_counts(result.get("rejected_routes") or []),
        "token_telemetry": token_telemetry_summary(result.get("harnesses") or []),
    }


def report_command() -> str:
    return " ".join(shlex.quote(part) for part in sys.argv)


def report_mode(*, smoke_requested: bool, discover_requested: bool) -> str:
    if discover_requested:
        return "discover"
    if smoke_requested:
        return "smoke"
    return "check"


def report_path_for_state(mode: str) -> str:
    return "/abs/goal-config-smoke.json" if mode in {"smoke", "debug"} else "/abs/goal-config-check.json"


def route_summary(route: dict[str, Any]) -> str:
    return " ".join(str(route.get(key, "-")) for key in ("role", "harness", "provider", "model"))


def print_report_summary(result: dict[str, Any], *, output: Path | None) -> None:
    attach_summary(result)
    summary = result["summary"]
    output_path = output.resolve().as_posix() if output is not None else "-"
    print(
        " ".join(
            [
                f"status={result.get('status')}",
                f"mode={result.get('mode', 'check')}",
                f"accepted={summary['accepted_route_count']}",
                f"rejected={summary['rejected_route_count']}",
                f"skipped={summary['skipped_route_count']}",
                f"unvisited={summary['unvisited_route_count']}",
                f"failures={summary['failure_count']}",
                f"route_verification={summary['route_verification_status']}",
                f"output={output_path}",
            ]
        )
    )
    print("accepted_routes:")
    for route in result.get("accepted_routes") or []:
        if isinstance(route, dict):
            print(f"- {route_summary(route)}")
    print("rejection_counts:")
    for item in summary["rejection_counts"]:
        print(
            f"- {item.get('harness') or '-'} {item.get('provider') or '-'} "
            f"count={item.get('count')} message={item.get('message')}"
        )


def write_report(result: dict[str, Any], *, output: Path | None, stdout_mode: str | None) -> None:
    attach_summary(result)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")

    effective_stdout = stdout_mode or ("summary" if output else "full")
    if effective_stdout == "none":
        return
    if effective_stdout == "summary":
        print_report_summary(result, output=output)
        return
    print(text, end="")


def write_state(
    result: dict[str, Any],
    *,
    output: Path | None,
    state_output: Path | None,
    for_preflight: bool = False,
) -> None:
    if state_output is None:
        return
    mode = result.get("mode", "check")
    status = result.get("status")
    complete = (mode != "discover" and status == "pass") and not for_preflight
    output_path = output.resolve().as_posix() if output is not None else None
    if mode == "discover":
        phase = "discovery"
        missing_preferences = ["final role mapping from accepted_routes"]
        next_command = None
        if output_path:
            next_command = (
                "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py "
                f"--from-discovery {output_path} --mapping auto --output /abs/goal.config.json "
                "--state-output /abs/goal-config-state.json"
            )
    elif complete:
        phase = "validated"
        missing_preferences = []
        report_path_for_bundle = report_path_for_state(mode=mode)
        next_command = (
            "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py "
            "--brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle "
            f"--goal-config /abs/goal.config.json --goal-config-check {report_path_for_bundle}"
        )
    elif for_preflight:
        phase = "preflight_compatible" if status == "pass" else "preflight_incompatible"
        next_command = None
        check_mode = result.get("check_mode")
        config_validation_mode = result.get("config_validation_mode")
        required_mode = str(check_mode or mode or "check")
        if required_mode == "debug":
            required_mode = "smoke"
        if config_validation_mode in {"smoke", "debug"}:
            required_mode = "smoke"
        next_mode_flag = " --smoke" if required_mode in {"smoke", "discover"} else ""
        if output_path:
            next_command = (
                "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py "
                f"--config /abs/goal.config.json --require-models{next_mode_flag}"
            )
            next_report = report_path_for_state(mode=str(required_mode or "check"))
            next_command += f" --output {next_report}"
        missing_preferences = [f"run full check_goal_config report for requested {required_mode} compatibility check"]
    else:
        phase = "blocked"
        missing_preferences = ["repair failing routes or credentials, then rerun validation"]
        next_command = None
        if output_path:
            next_command = (
                f"inspect {output_path} failures, repair the config or provider auth, then rerun check_goal_config.py"
            )
    state = {
        "schema_version": 1,
        "phase": phase,
        "complete": complete,
        "missing_preferences": missing_preferences,
        "next_command": next_command,
        "mode": mode,
        "report_path": output_path,
        "status": status,
        "config_validation_mode": result.get("config_validation_mode"),
        "check_mode": result.get("check_mode"),
    }
    state_output.parent.mkdir(parents=True, exist_ok=True)
    state_output.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="Path to goal.config.json.")
    parser.add_argument("--output", type=Path, help="Write validation report JSON.")
    parser.add_argument("--require-models", action="store_true", help="Fail if configured models are unavailable.")
    parser.add_argument("--smoke", action="store_true", help="Run configured harness smoke tests.")
    parser.add_argument(
        "--harness",
        action="append",
        default=[],
        help="Role to check, such as lite, demanding, lite_agent, or demanding_agent. Repeat or pass comma-separated roles. Defaults to all models.",
    )
    parser.add_argument(
        "--for-preflight",
        action="store_true",
        help="Run preflight compatibility checks and return before model availability or smoke validation.",
    )
    parser.add_argument(
        "--remediated-output",
        type=Path,
        help="With --for-preflight, write a mechanically preflight-remediated config JSON.",
    )
    parser.add_argument("--discover-model-filter", help="Regex filter applied to discovered provider/model ids.")
    parser.add_argument("--discover-max", type=int, help="Maximum discovered candidates to validate.")
    parser.add_argument(
        "--discover-profile",
        choices=tuple(DISCOVERY_PROFILES),
        help="Use a deterministic mixed discovery profile across configured harnesses.",
    )
    parser.add_argument(
        "--discover-all-candidates",
        action="store_true",
        help="Disable discovery-profile early accept stop so every configured profile candidate is checked or explicitly skipped.",
    )
    parser.add_argument(
        "--reuse-smoke-report",
        action="append",
        type=Path,
        default=[],
        help="Reuse passing smoke evidence from a prior goal-config check or discovery report.",
    )
    parser.add_argument(
        "--stdout",
        dest="stdout_mode",
        choices=("summary", "full", "none"),
        help="Control stdout. Defaults to summary when --output is present, otherwise full JSON.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Alias for --stdout full; print the report JSON to stdout.",
    )
    parser.add_argument("--state-output", type=Path, help="Write goal-config-state.json for UX/state handoff.")
    return parser


def parse_cli_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    args = parser.parse_args()
    if args.json:
        if args.stdout_mode not in (None, "full"):
            parser.error("--json cannot be combined with --stdout summary or --stdout none")
        args.stdout_mode = "full"
    return args


class CheckContext(NamedTuple):
    config: dict[str, Any]
    contract: Any
    failures: list[str]
    models: dict[str, Any]
    harnesses: Any
    smoke_cache: dict[tuple[str, str, str], dict[str, Any]]
    mode: str
    config_validation: str | None
    command: str


def build_check_context(args: argparse.Namespace) -> CheckContext:
    config = load_json(args.config)
    contract = load_contract()
    failures = validate_config_shape(config)
    models = config.get("models", {})
    harnesses = config.get("harnesses", {})
    smoke_cache = load_smoke_cache(args.reuse_smoke_report)
    if args.discover_max is not None and args.discover_max <= 0:
        failures.append("--discover-max must be a positive integer")
    mode = report_mode(
        smoke_requested=args.smoke,
        discover_requested=bool(args.discover_profile),
    )
    config_validation = get_config_validation_mode(config)
    command = report_command()
    return CheckContext(
        config=config,
        contract=contract,
        failures=failures,
        models=models,
        harnesses=harnesses,
        smoke_cache=smoke_cache,
        mode=mode,
        config_validation=config_validation,
        command=command,
    )


def base_check_result(
    args: argparse.Namespace,
    ctx: CheckContext,
    *,
    failures: list[str],
    checked_roles: list[str],
    accepted_routes: list[dict[str, Any]],
    rejected_routes: list[dict[str, Any]],
    harnesses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Common check-report envelope shared by the preflight/discover/standard modes."""
    return {
        "schema_version": 1,
        "status": "failed" if failures else "pass",
        "mode": ctx.mode,
        "check_mode": ctx.mode,
        "config_validation_mode": ctx.config_validation,
        "command": ctx.command,
        "config_path": args.config.resolve().as_posix(),
        "profile": ctx.config.get("profile"),
        "checked_roles": checked_roles,
        "accepted_routes": accepted_routes,
        "rejected_routes": rejected_routes,
        "harnesses": harnesses,
        "failures": failures,
    }


def run_for_preflight_mode(args: argparse.Namespace, ctx: CheckContext) -> tuple[int | None, dict[str, Any] | None]:
    config = ctx.config
    contract = ctx.contract
    failures = ctx.failures
    mode = ctx.mode

    failures.extend(validate_for_preflight(config, mode, contract))
    remediated_config, remediation_actions = remediate_for_preflight(config, mode=mode, contract=contract)
    if args.remediated_output:
        args.remediated_output.parent.mkdir(parents=True, exist_ok=True)
        args.remediated_output.write_text(
            json.dumps(remediated_config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    preflight_remediation = {
        "available": bool(remediation_actions),
        "actions": remediation_actions,
        "remediated_config_path": args.remediated_output.resolve().as_posix() if args.remediated_output else None,
        "follow_up": "rerun --for-preflight with --smoke for debug/smoke validation modes"
        if any(action.get("action") == "rerun_check" for action in remediation_actions)
        else None,
    }
    if args.smoke and args.reuse_smoke_report:
        reused = reusable_smoke_route_report(args.reuse_smoke_report)
        failures.extend(reused["failures"])
        result = base_check_result(
            args,
            ctx,
            failures=failures,
            checked_roles=reused["checked_roles"],
            accepted_routes=reused["accepted_routes"],
            rejected_routes=reused["rejected_routes"],
            harnesses=reused["harnesses"],
        )
        result.update(
            {
                "skipped_routes": reused["skipped_routes"],
                "unvisited_routes": reused["unvisited_routes"],
                "remediation": preflight_remediation,
                "reused_smoke_reports": reused["used_reports"],
                "preflight_route_evidence_mode": "reused_smoke_report",
            }
        )
        write_state(result, output=args.output, state_output=args.state_output, for_preflight=True)
        write_report(result, output=args.output, stdout_mode=args.stdout_mode)
        return 1 if failures else 0, preflight_remediation

    result = base_check_result(
        args,
        ctx,
        failures=failures,
        checked_roles=[],
        accepted_routes=[],
        rejected_routes=[],
        harnesses=[],
    )
    result.update(
        {
            "skipped_routes": [],
            "unvisited_routes": [],
            "remediation": preflight_remediation,
        }
    )
    write_state(result, output=args.output, state_output=args.state_output, for_preflight=True)
    write_report(result, output=args.output, stdout_mode=args.stdout_mode)
    return 1 if failures else 0, preflight_remediation


def run_discover_mode(
    args: argparse.Namespace, ctx: CheckContext, *, preflight_remediation: dict[str, Any] | None
) -> int:
    config = ctx.config
    failures = ctx.failures
    smoke_cache = ctx.smoke_cache

    candidates: list[dict[str, Any]] = []
    harness_reports: list[dict[str, Any]] = []
    unvisited_routes: list[dict[str, Any]] = []
    discovery_failures: list[str] = []
    if args.discover_profile:
        profile_candidates_, profile_reports, profile_failures, profile_unvisited = discover_profile_routes(
            config,
            profile_name=args.discover_profile,
            model_filter=args.discover_model_filter,
            max_candidates=args.discover_max,
            smoke=args.smoke,
            smoke_cache=smoke_cache,
            discover_all_candidates=args.discover_all_candidates,
        )
        candidates.extend(profile_candidates_)
        harness_reports.extend(profile_reports)
        discovery_failures.extend(profile_failures)
        unvisited_routes.extend(profile_unvisited)
    accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
    failures.extend(discovery_failures)
    if not accepted_routes:
        failures.append("discover accepted no routes")
    result = base_check_result(
        args,
        ctx,
        failures=failures,
        checked_roles=[str(report["role"]) for report in harness_reports if isinstance(report.get("role"), str)],
        accepted_routes=accepted_routes,
        rejected_routes=rejected_routes,
        harnesses=harness_reports,
    )
    result.update(
        {
            "discover_profile": args.discover_profile,
            "discover_model_filter": args.discover_model_filter,
            "candidate_routes": candidates,
            "skipped_routes": classify_skipped_routes(harness_reports),
            "unvisited_routes": unvisited_routes,
        }
    )
    if preflight_remediation is not None:
        result["remediation"] = preflight_remediation
    write_state(result, output=args.output, state_output=args.state_output, for_preflight=args.for_preflight)
    write_report(result, output=args.output, stdout_mode=args.stdout_mode)
    return 1 if failures else 0


def collect_role_reports(args: argparse.Namespace, ctx: CheckContext) -> tuple[list[str], list[dict[str, Any]]]:
    config = ctx.config
    failures = ctx.failures
    models = ctx.models
    harnesses = ctx.harnesses
    smoke_cache = ctx.smoke_cache

    roles = model_roles(config, args.harness) if not failures else []
    smokes = config.get("harness_smokes") if isinstance(config.get("harness_smokes"), dict) else {}
    harness_reports: list[dict[str, Any]] = []
    missing_smoke_roles: set[str] = set()
    if args.smoke:
        missing_smoke_roles = {role for role in roles if not isinstance(smokes.get(role), dict)}
        if missing_smoke_roles:
            failures.append(f"missing smoke config for roles: {', '.join(sorted(missing_smoke_roles))}")

    for role in roles:
        model = models[role]
        if not isinstance(model, dict):
            failures.append(f"{role} model entry must be an object")
            continue

        harness_name = model.get("harness")
        harness = harnesses.get(harness_name) if isinstance(harnesses, dict) else None

        report: dict[str, Any] = {
            "role": role,
            "harness": harness_name,
            "provider": model.get("provider"),
            "model": model.get("model"),
            "alias": model.get("alias"),
        }

        if not isinstance(harness_name, str) or not harness_name:
            report["model_check"] = {"status": "failed", "reason": "missing harness"}
            failures.append(f"{role}: missing harness")
            harness_reports.append(report)
            continue
        if harness is None or not isinstance(harness, dict):
            report["model_check"] = {"status": "failed", "reason": "harness not configured"}
            if args.require_models or args.smoke:
                failures.append(f"{role}: harness {harness_name!r} is not configured")
            harness_reports.append(report)
            continue

        report["harness_kind"] = harness.get("kind")
        model_check, model_failures = check_model_for_harness(model, harness=harness)

        report["model_check"] = model_check
        if args.require_models:
            failures.extend(f"{role}: {failure}" for failure in model_failures)

        if args.smoke and missing_smoke_roles:
            if role in missing_smoke_roles:
                report["smoke"] = {"status": "failed", "reason": "missing smoke config"}
            else:
                report["smoke"] = {"status": "skipped", "reason": "smoke preflight failed"}
        elif args.smoke:
            smoke = smokes.get(role)
            if not isinstance(smoke, dict):
                report["smoke"] = {"status": "failed", "reason": "missing smoke config"}
                failures.append(f"{role}: missing smoke config")
            else:
                smoke_report, smoke_failures = run_or_reuse_smoke(
                    role,
                    model,
                    smoke,
                    harness=harness,
                    smoke_cache=smoke_cache,
                )
                report["smoke"] = smoke_report
                failures.extend(f"{role}: {failure}" for failure in smoke_failures)

        harness_reports.append(report)
    return roles, harness_reports


def run_standard_mode(
    args: argparse.Namespace, ctx: CheckContext, *, preflight_remediation: dict[str, Any] | None
) -> int:
    failures = ctx.failures

    roles, harness_reports = collect_role_reports(args, ctx)
    accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
    result = base_check_result(
        args,
        ctx,
        failures=failures,
        checked_roles=roles,
        accepted_routes=accepted_routes,
        rejected_routes=rejected_routes,
        harnesses=harness_reports,
    )
    if preflight_remediation is not None:
        result["remediation"] = preflight_remediation
    write_state(result, output=args.output, state_output=args.state_output, for_preflight=args.for_preflight)
    write_report(result, output=args.output, stdout_mode=args.stdout_mode)
    return 1 if failures else 0


def main() -> int:
    parser = build_parser()
    args = parse_cli_args(parser)

    ctx = build_check_context(args)
    failures = ctx.failures

    preflight_remediation: dict[str, Any] | None = None
    if args.for_preflight:
        exit_code, preflight_remediation = run_for_preflight_mode(args, ctx)
        if exit_code is not None:
            return exit_code

    if args.discover_profile and not failures:
        return run_discover_mode(args, ctx, preflight_remediation=preflight_remediation)

    return run_standard_mode(args, ctx, preflight_remediation=preflight_remediation)


if __name__ == "__main__":
    raise SystemExit(main())
