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
import sqlite3
import subprocess
import shlex
import time
import sys
from pathlib import Path
from typing import Any


MODEL_RE = re.compile(r"(?<![A-Za-z0-9_.~/+-])([~A-Za-z0-9_.+-]+(?:/[~A-Za-z0-9_.:+-]+)+)(?![A-Za-z0-9_.~/+-])")
ROLE_ALIASES = {
    "lite": "lite_agent",
    "flash": "lite_agent",
    "lite_agent": "lite_agent",
    "demanding": "demanding_agent",
    "pro": "demanding_agent",
    "heavy": "demanding_agent",
    "demanding_agent": "demanding_agent",
}
HARNESS_KIND_VALUES = {"opencode-bridge", "codex", "generic-cli"}
MAX_ERROR_MESSAGE_CHARS = 220
VALIDATION_MODES = {"model-check", "smoke", "debug"}
DISCOVERY_PROFILES: dict[str, dict[str, Any]] = {
    "mixed-fast": {
        "early_accept_count": 4,
        "stop_provider_on_auth_error": True,
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


def command_result(command: list[str], *, timeout_seconds: int | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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


def opencode_db_path() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def parse_models(output: str) -> set[str]:
    return set(MODEL_RE.findall(output))


def route_id(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return sanitized or "route"


def model_candidates(provider: str, model: str) -> set[str]:
    candidates = {model}
    prefix = f"{provider}/"
    if model.startswith(prefix):
        candidates.add(model[len(prefix) :])
    else:
        candidates.add(f"{provider}/{model}")
    return {candidate for candidate in candidates if candidate}


def discovery_candidate_model(provider: str, listed_model: str) -> str:
    if listed_model.startswith(f"{provider}/") or listed_model.startswith(f"~{provider}/"):
        return listed_model
    return f"{provider}/{listed_model}"


def discovery_smoke(role: str, timeout_seconds: int) -> dict[str, Any]:
    token = f"GOAL_CONFIG_DISCOVER_{route_id(role).upper()}_SMOKE_OK"
    return {
        "prompt": f"Reply with {token} and nothing else.",
        "expect": token,
        "timeout_seconds": timeout_seconds,
        "readback": "opencode_session_db",
    }


def parse_session_id(stdout: str) -> str | None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            session_id = event.get("sessionID")
            if isinstance(session_id, str) and session_id:
                return session_id
            part = event.get("part")
            if isinstance(part, dict):
                session_id = part.get("sessionID")
                if isinstance(session_id, str) and session_id:
                    return session_id
    return None


def safe_json(data: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def compact_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return None


def short_message(message: str) -> str:
    compact = re.sub(r"\s+", " ", message).strip()
    auth_match = re.search(
        r"(?i)(AuthenticateToken authentication failed|401[^.;\n]*|403[^.;\n]*|unauthorized[^.;\n]*)", compact
    )
    if auth_match:
        compact = auth_match.group(0).strip()
    if len(compact) > MAX_ERROR_MESSAGE_CHARS:
        return compact[: MAX_ERROR_MESSAGE_CHARS - 3].rstrip() + "..."
    return compact


def first_compact_value(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = compact_text(data.get(key))
        if value:
            return value
    return None


def collect_json_errors(value: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if isinstance(value, dict):
        error_payload = value.get("error")
        message = first_compact_value(value, ("message", "msg", "detail", "description"))
        status = first_compact_value(value, ("status", "statusCode", "status_code", "code"))
        error_text = compact_text(error_payload) if not isinstance(error_payload, (dict, list)) else None
        if value.get("type") == "error" or error_payload is not None:
            entry: dict[str, str] = {}
            if status:
                entry["status"] = status
            if message:
                entry["message"] = message
            elif error_text:
                entry["message"] = error_text
            if entry:
                errors.append(entry)
        for child in value.values():
            errors.extend(collect_json_errors(child))
    elif isinstance(value, list):
        for child in value:
            errors.extend(collect_json_errors(child))
    return errors


def normalize_errors(errors: list[dict[str, str]], *, provider: str, include_raw_errors: bool) -> list[dict[str, Any]]:
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}
    for error in errors:
        raw_message = error.get("message", "")
        message = short_message(raw_message)
        status = error.get("status", "")
        key = (status, message)
        item = aggregate.setdefault(
            key,
            {
                "provider": provider,
                "status": status,
                "message": message,
                "count": 0,
            },
        )
        item["count"] += 1
        if include_raw_errors:
            item.setdefault("raw_messages", [])
            if raw_message and raw_message not in item["raw_messages"]:
                item["raw_messages"].append(raw_message)
    return list(aggregate.values())


def extract_opencode_errors(
    stdout: str,
    stderr: str,
    *,
    provider: str,
    include_raw_errors: bool = False,
) -> list[dict[str, Any]]:
    errors: list[dict[str, str]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for error in collect_json_errors(event):
            key = (error.get("status"), error.get("message"))
            if key not in seen:
                errors.append(error)
                seen.add(key)

    combined = f"{stdout}\n{stderr}"
    auth_match = re.search(
        r"(?i)(401|403)?[^.\n]*(AuthenticateToken|authentication failed|unauthorized)[^.\n]*", combined
    )
    if auth_match:
        message = auth_match.group(0).strip()
        status = "401" if "401" in message else ("403" if "403" in message else "")
        key = (status or None, message)
        if key not in seen:
            entry = {"message": message}
            if status:
                entry["status"] = status
            errors.append(entry)
    return normalize_errors(errors, provider=provider, include_raw_errors=include_raw_errors)


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


def read_opencode_session(session_id: str, db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"status": "missing_db", "db_path": db_path.as_posix()}

    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            """
            select id, model, tokens_input, tokens_output, tokens_reasoning,
                   tokens_cache_read, tokens_cache_write, time_created, time_updated
            from session where id=?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return {"status": "missing_session", "db_path": db_path.as_posix(), "session_id": session_id}

        messages: dict[str, str] = {}
        for message_id, message_data in con.execute(
            "select id, data from message where session_id=? order by time_created",
            (session_id,),
        ):
            parsed = safe_json(message_data)
            role = parsed.get("role")
            if isinstance(role, str):
                messages[message_id] = role

        assistant_texts: list[str] = []
        assistant_reasoning_chars = 0
        for message_id, part_data in con.execute(
            "select message_id, data from part where session_id=? order by time_created",
            (session_id,),
        ):
            if messages.get(message_id) != "assistant":
                continue
            part = safe_json(part_data)
            part_type = part.get("type")
            text = part.get("text")
            if part_type == "text" and isinstance(text, str):
                assistant_texts.append(text)
            elif part_type == "reasoning" and isinstance(text, str):
                assistant_reasoning_chars += len(text)

        response_text = "".join(assistant_texts)
        model_data = safe_json(row[1])
        return {
            "status": "pass",
            "session_id": row[0],
            "provider": model_data.get("providerID"),
            "model": model_data.get("id") or model_data.get("modelID"),
            "tokens": {
                "input": row[2],
                "output": row[3],
                "reasoning": row[4],
                "cache_read": row[5],
                "cache_write": row[6],
            },
            "time": {
                "created": row[7],
                "updated": row[8],
            },
            "assistant_response": response_text,
            "assistant_response_chars": len(response_text),
            "assistant_reasoning_chars": assistant_reasoning_chars,
        }
    finally:
        con.close()


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


def fixture_models(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    return parse_models(path.read_text(encoding="utf-8"))


def resolve_binary(command: str) -> str | None:
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


def render_model_list_args(harness: dict[str, Any], provider: str) -> list[str]:
    args = harness.get("model_list_args")
    return render_tokens(args, context={"provider": provider, "model": "", "prompt": "", "role": "", "alias": ""})


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
            if kind == "opencode":
                failures.append(f"harness {name} missing opencode smoke_args")
            else:
                failures.append(f"harness {name} missing smoke_args")
        if kind == "opencode":
            model_list_args = harness.get("model_list_args")
            if not isinstance(model_list_args, list) or not model_list_args:
                failures.append(f"harness {name} missing model_list_args")
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

    if validation_mode == "debug" and (config.get("telemetry", {}).get("mode") or "standard") != "debug":
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


def check_opencode_model(
    model: dict[str, Any],
    *,
    harness: dict[str, Any],
    models_fixture: set[str] | None,
    model_list_cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    provider = model.get("provider")
    provider_model = model.get("model")
    failures: list[str] = []
    binary = harness.get("command")
    if not isinstance(provider, str) or not provider:
        failures.append("missing provider")
    if not isinstance(provider_model, str) or not provider_model:
        failures.append("missing model")
    if not isinstance(binary, str) or not binary:
        failures.append("opencode harness missing command")

    result: dict[str, Any] = {
        "source": "fixture" if models_fixture is not None else "live",
        "harness": harness.get("kind"),
        "provider": provider,
        "model": provider_model,
        "binary": None,
    }
    if failures:
        result["status"] = "failed"
        return result, failures

    resolved_binary = resolve_binary(binary)
    result["binary"] = resolved_binary
    if not resolved_binary and models_fixture is None:
        failures.append("opencode binary not found")
        result["status"] = "failed"
        return result, failures

    model_list_args = render_model_list_args(harness, provider)
    cache_key = json.dumps(
        {
            "binary": resolved_binary,
            "provider": provider,
            "model_list_args": model_list_args,
        },
        sort_keys=True,
    )
    if models_fixture is not None:
        version = {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": 0,
            "timed_out": False,
        }
        models = {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": 0,
            "timed_out": False,
        }
        cache_status = "fixture"
    elif cache_key in model_list_cache:
        cached = model_list_cache[cache_key]
        version = command_result([resolved_binary, "--version"], timeout_seconds=20)
        models = {
            "returncode": cached["returncode"],
            "stdout": cached["stdout"],
            "stderr": cached["stderr"],
            "elapsed_ms": cached["elapsed_ms"],
            "timed_out": cached["timed_out"],
        }
        cache_status = "hit"
    else:
        version = command_result([resolved_binary, "--version"], timeout_seconds=20)
        models = (
            command_result([resolved_binary, *model_list_args], timeout_seconds=60)
            if provider
            else {
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "elapsed_ms": 0,
                "timed_out": False,
            }
        )
        model_list_cache[cache_key] = dict(models)
        cache_status = "miss"

    if models_fixture is not None:
        available = models_fixture
    else:
        available = parse_models(models["stdout"] + "\n" + models["stderr"])
    candidates = model_candidates(provider, provider_model)
    available_candidate = sorted(candidates & available)

    result.update(
        {
            "version": version["stdout"].strip() or version["stderr"].strip(),
            "version_returncode": version["returncode"],
            "models_returncode": models["returncode"],
            "models_cache": cache_status,
            "models_output_chars": len(models["stdout"]) + len(models["stderr"]),
            "models_elapsed_ms": models["elapsed_ms"],
            "available_model_count": len(available),
            "model_candidates": sorted(candidates),
            "model_available": bool(available_candidate),
            "matched_model": available_candidate[0] if available_candidate else None,
        }
    )
    result["status"] = (
        "pass"
        if result["model_available"]
        and version["returncode"] == 0
        and (models["returncode"] == 0 or models_fixture is not None)
        else "failed"
    )
    if version["returncode"] != 0:
        failures.append("opencode --version failed")
    if models["returncode"] != 0 and models_fixture is None:
        failures.append(f"opencode models {provider} failed")
    if not provider_model:
        failures.append("missing model")
    elif not result["model_available"]:
        failures.append(f"model not listed by opencode: {provider_model}; tried {', '.join(sorted(candidates))}")
    return result, failures


def check_non_opencode_model(model: dict[str, Any], *, harness: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    provider = model.get("provider")
    provider_model = model.get("model")
    kind = harness.get("kind")
    binary = harness.get("command")
    resolved_binary = resolve_binary(binary)
    failures: list[str] = []
    result: dict[str, Any] = {
        "source": "fixture" if harness is None else "live",
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
    opencode_db: Path,
    include_raw_errors: bool = False,
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

    if kind == "opencode":
        if timeout_seconds <= 0:
            timeout_seconds = 600

        resolved_opencode = resolve_binary(binary)
        if resolved_opencode is None:
            # Fail closed: a missing harness binary must reject the route, matching
            # the generic/codex branch below and the documented fail-closed contract.
            return {
                "status": "failed",
                "reason": f"{kind} binary not found",
            }, [f"{role} {kind} binary not found"]

        result = command_result(
            [resolved_opencode, *smoke_args],
            timeout_seconds=timeout_seconds,
        )

        session_id = parse_session_id(result["stdout"])
        opencode_errors = extract_opencode_errors(
            result["stdout"],
            result["stderr"],
            provider=str(model.get("provider") or ""),
            include_raw_errors=include_raw_errors,
        )
        readback = read_opencode_session(session_id, opencode_db) if session_id else {"status": "missing_session_id"}
        assistant_response = (
            readback.get("assistant_response") if isinstance(readback.get("assistant_response"), str) else ""
        )
        response_excerpt, response_excerpt_source = focused_response_excerpt(assistant_response, expected)
        tokens = readback.get("tokens", empty_tokens())
        contains_expected = expected in assistant_response
        if result["timed_out"]:
            failures.append(f"{role} opencode smoke timed out")
        if result["returncode"] != 0:
            failures.append(f"{role} opencode smoke returncode={result['returncode']}")
        for error in opencode_errors:
            provider = f" provider={error['provider']}" if error.get("provider") else ""
            status = f" status={error['status']}" if error.get("status") else ""
            message = f" message={error['message']}" if error.get("message") else ""
            count = f" count={error['count']}" if error.get("count") else ""
            failures.append(f"{role} opencode error{provider}{status}{message}{count}")
        if not session_id:
            failures.append(f"{role} opencode smoke did not emit a session id")
        if readback.get("status") != "pass":
            failures.append(f"{role} opencode session readback failed: {readback.get('status')}")
        if not contains_expected and not opencode_errors:
            failures.append(f"{role} assistant response did not contain expected smoke text")

        return {
            "status": "pass" if not failures else "failed",
            "returncode": result["returncode"],
            "timed_out": result["timed_out"],
            "elapsed_ms": result["elapsed_ms"],
            "stdout_chars": len(result["stdout"]),
            "stderr_chars": len(result["stderr"]),
            "session_id": session_id,
            "contains_expected": contains_expected,
            "opencode_errors": opencode_errors,
            "assistant_response_chars": readback.get("assistant_response_chars", 0),
            "assistant_reasoning_chars": readback.get("assistant_reasoning_chars", 0),
            "tokens": tokens,
            "token_telemetry": token_telemetry(tokens, source="opencode_session_db"),
            "response_excerpt": response_excerpt,
            "response_excerpt_source": response_excerpt_source,
        }, failures

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
    opencode_db: Path,
    include_raw_errors: bool,
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
        opencode_db=opencode_db,
        include_raw_errors=include_raw_errors,
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
                for error in smoke.get("opencode_errors") or []:
                    if isinstance(error, dict):
                        provider = f" provider={error['provider']}" if error.get("provider") else ""
                        status = f" status={error['status']}" if error.get("status") else ""
                        message = f" message={error['message']}" if error.get("message") else ""
                        count = f" count={error['count']}" if error.get("count") else ""
                        reasons.append(f"opencode error{provider}{status}{message}{count}")
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


def has_auth_error(report: dict[str, Any]) -> bool:
    smoke = report.get("smoke") if isinstance(report.get("smoke"), dict) else {}
    for error in smoke.get("opencode_errors") or []:
        if not isinstance(error, dict):
            continue
        status = str(error.get("status") or "")
        message = str(error.get("message") or "").lower()
        if status in {"401", "403"} or "unauthorized" in message or "authentication failed" in message:
            return True
    return False


def profile_discovery_candidates(
    profile_name: str,
    *,
    providers: list[str],
    model_filter: str | None,
    max_candidates: int | None,
) -> list[dict[str, Any]]:
    profile = DISCOVERY_PROFILES.get(profile_name)
    if profile is None:
        raise SystemExit(f"unknown discovery profile: {profile_name}")
    matcher = re.compile(model_filter) if model_filter else None
    allowed_providers = set(providers)
    candidates: list[dict[str, Any]] = []
    for item in profile["candidates"]:
        candidate = dict(item)
        if allowed_providers and candidate.get("provider") not in allowed_providers:
            continue
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
    models_fixture: set[str] | None,
    model_list_cache: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    kind = harness.get("kind")
    if kind == "opencode":
        return check_opencode_model(
            model,
            harness=harness,
            models_fixture=models_fixture,
            model_list_cache=model_list_cache,
        )
    if kind in {"opencode-bridge", "codex", "generic-cli"}:
        return check_non_opencode_model(model, harness=harness)
    return {"status": "failed", "reason": f"unsupported harness kind: {kind}"}, [f"unsupported harness kind: {kind}"]


def discover_profile_routes(
    config: dict[str, Any],
    *,
    profile_name: str,
    providers: list[str],
    model_filter: str | None,
    max_candidates: int | None,
    smoke: bool,
    models_fixture: set[str] | None,
    opencode_db: Path,
    include_raw_errors: bool,
    smoke_cache: dict[tuple[str, str, str], dict[str, Any]],
    discover_all_candidates: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    profile = DISCOVERY_PROFILES[profile_name]
    candidates = profile_discovery_candidates(
        profile_name,
        providers=providers,
        model_filter=model_filter,
        max_candidates=max_candidates,
    )
    harnesses = config.get("harnesses") if isinstance(config.get("harnesses"), dict) else {}
    timeout_seconds = int(config.get("effort", {}).get("lite_timeout_seconds") or 600)
    model_list_cache: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    reports: list[dict[str, Any]] = []
    unvisited_routes: list[dict[str, Any]] = []
    stopped_providers: set[tuple[str, str]] = set()
    accepted_count = 0
    early_accept_count = int(profile.get("early_accept_count") or 0)
    stop_provider_on_auth_error = bool(profile.get("stop_provider_on_auth_error"))

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

        stop_key = (str(candidate["harness"]), str(candidate["provider"]))
        if stop_key in stopped_providers:
            report["model_check"] = {"status": "skipped", "reason": "provider stopped after auth error"}
            report["smoke"] = {"status": "skipped", "reason": "provider stopped after auth error"} if smoke else None
            reports.append(report)
            continue

        harness = harnesses.get(candidate["harness"])
        if not isinstance(harness, dict):
            report["model_check"] = {"status": "failed", "reason": "harness not configured"}
            report["model_failures"] = [f"harness {candidate['harness']!r} is not configured"]
            reports.append(report)
            continue

        model_check, model_failures = check_model_for_harness(
            model,
            harness=harness,
            models_fixture=models_fixture,
            model_list_cache=model_list_cache,
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
                opencode_db=opencode_db,
                include_raw_errors=include_raw_errors,
                smoke_cache=smoke_cache,
            )
            report["smoke"] = smoke_report
            if stop_provider_on_auth_error and has_auth_error(report):
                stopped_providers.add(stop_key)
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


def discover_available_routes(
    config: dict[str, Any],
    *,
    harness_name: str,
    providers: list[str],
    model_filter: str | None,
    max_candidates: int | None,
    smoke: bool,
    models_fixture: set[str] | None,
    opencode_db: Path,
    include_raw_errors: bool,
    smoke_cache: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    failures: list[str] = []
    harnesses = config.get("harnesses")
    harness = harnesses.get(harness_name) if isinstance(harnesses, dict) else None
    if not isinstance(harness, dict):
        return [], [], [f"discover harness {harness_name!r} is not configured"]
    if harness.get("kind") != "opencode":
        return [], [], [f"discover harness {harness_name!r} must be opencode"]

    matcher = re.compile(model_filter) if model_filter else None
    timeout_seconds = int(config.get("effort", {}).get("lite_timeout_seconds") or 600)
    candidates: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    model_list_cache: dict[str, dict[str, Any]] = {}

    for provider in providers:
        provider_seed = {
            "role": f"discover_{route_id(provider)}_seed",
            "alias": f"discover-{route_id(provider)}-seed",
            "harness": harness_name,
            "provider": provider,
            "model": f"{provider}/__goal_config_discovery_seed__",
        }
        model_check, model_failures = check_opencode_model(
            provider_seed,
            harness=harness,
            models_fixture=models_fixture,
            model_list_cache=model_list_cache,
        )
        available = set(models_fixture or [])
        if models_fixture is None:
            cache_key = json.dumps(
                {
                    "binary": model_check.get("binary"),
                    "provider": provider,
                    "model_list_args": render_model_list_args(harness, provider),
                },
                sort_keys=True,
            )
            cached = model_list_cache.get(cache_key)
            if cached:
                available = parse_models(cached.get("stdout", "") + "\n" + cached.get("stderr", ""))
        if not available:
            failures.append(f"discover {provider}: no models listed")
            if model_failures:
                failures.extend(f"discover {provider}: {failure}" for failure in model_failures)
            continue

        provider_models = [
            model
            for model in sorted(available)
            if model.startswith(f"{provider}/")
            or model.startswith(f"~{provider}/")
            or ("/" in model and not model.startswith("~") and not re.match(r"^[A-Za-z0-9_.+-]+/.+/.+", model))
        ]
        if not provider_models:
            provider_models = sorted(available)
        for listed_model in provider_models:
            candidate_model = discovery_candidate_model(provider, listed_model)
            if matcher and not matcher.search(candidate_model):
                continue
            candidates.append(
                {
                    "role": f"discover_{route_id(candidate_model)}",
                    "alias": f"discover-{route_id(candidate_model)}",
                    "harness": harness_name,
                    "provider": provider,
                    "model": candidate_model,
                    "listed_model": listed_model,
                }
            )

    if max_candidates is not None:
        candidates = candidates[:max_candidates]

    for candidate in candidates:
        model = {
            "role": candidate["role"],
            "alias": candidate["alias"],
            "harness": candidate["harness"],
            "provider": candidate["provider"],
            "model": candidate["model"],
        }
        report: dict[str, Any] = dict(model)
        report["listed_model"] = candidate["listed_model"]
        model_check, model_failures = check_opencode_model(
            model,
            harness=harness,
            models_fixture=models_fixture,
            model_list_cache=model_list_cache,
        )
        report["model_check"] = model_check
        if smoke and model_check.get("status") == "pass":
            smoke_report, _smoke_failures = run_or_reuse_smoke(
                candidate["role"],
                model,
                discovery_smoke(candidate["role"], timeout_seconds),
                harness=harness,
                opencode_db=opencode_db,
                include_raw_errors=include_raw_errors,
                smoke_cache=smoke_cache,
            )
            report["smoke"] = smoke_report
        elif smoke:
            report["smoke"] = {"status": "skipped", "reason": "model check failed"}
        if model_failures:
            report["model_failures"] = model_failures
        reports.append(report)

    if not candidates:
        failures.append("discover produced no candidate routes")
    return candidates, reports, failures


def rejection_counts(rejected_routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for route in rejected_routes:
        if not isinstance(route, dict):
            continue
        reasons = route.get("reasons") if isinstance(route.get("reasons"), list) else []
        if not reasons:
            reasons = ["rejected"]
        for reason_value in reasons:
            reason = str(reason_value)
            status_match = re.search(r"\bstatus=([^ ]+)", reason)
            message_match = re.search(r"\bmessage=(.*?)(?:\s+count=\d+|$)", reason)
            status = status_match.group(1) if status_match else ""
            message = short_message(message_match.group(1) if message_match else reason)
            key = (
                str(route.get("harness") or ""),
                str(route.get("provider") or ""),
                status,
                message,
            )
            item = counts.setdefault(
                key,
                {
                    "harness": key[0],
                    "provider": key[1],
                    "status": status,
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
        status = f" status={item['status']}" if item.get("status") else ""
        print(
            f"- {item.get('harness') or '-'} {item.get('provider') or '-'}{status} "
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


def main() -> int:
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
    parser.add_argument(
        "--discover-provider",
        action="append",
        default=[],
        help="Discover opencode candidate routes for this provider. Repeat for multiple providers.",
    )
    parser.add_argument(
        "--discover-harness",
        default="opencode",
        help="Configured opencode harness name to use for discovery.",
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
    parser.add_argument(
        "--include-raw-errors",
        action="store_true",
        help="Include full raw provider error payloads in opencode error records.",
    )
    parser.add_argument("--state-output", type=Path, help="Write goal-config-state.json for UX/state handoff.")
    parser.add_argument(
        "--models-output", type=Path, help="Use this opencode models output fixture instead of live CLI."
    )
    parser.add_argument("--opencode-db", type=Path, default=opencode_db_path(), help="opencode session database path.")
    args = parser.parse_args()
    if args.json:
        if args.stdout_mode not in (None, "full"):
            parser.error("--json cannot be combined with --stdout summary or --stdout none")
        args.stdout_mode = "full"

    config = load_json(args.config)
    contract = load_contract()
    failures = validate_config_shape(config)
    models = config.get("models", {})
    harnesses = config.get("harnesses", {})
    models_fixture = fixture_models(args.models_output)
    smoke_cache = load_smoke_cache(args.reuse_smoke_report)
    if args.discover_max is not None and args.discover_max <= 0:
        failures.append("--discover-max must be a positive integer")
    mode = report_mode(
        smoke_requested=args.smoke,
        discover_requested=bool(args.discover_profile or args.discover_provider),
    )
    config_validation = get_config_validation_mode(config)
    command = report_command()

    preflight_remediation: dict[str, Any] | None = None
    if args.for_preflight:
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
            result = {
                "schema_version": 1,
                "status": "failed" if failures else "pass",
                "mode": mode,
                "check_mode": mode,
                "config_validation_mode": config_validation,
                "command": command,
                "config_path": args.config.resolve().as_posix(),
                "profile": config.get("profile"),
                "checked_roles": reused["checked_roles"],
                "accepted_routes": reused["accepted_routes"],
                "rejected_routes": reused["rejected_routes"],
                "skipped_routes": reused["skipped_routes"],
                "unvisited_routes": reused["unvisited_routes"],
                "opencode_binary": resolve_binary("opencode") if isinstance(harnesses, dict) else None,
                "opencode_db": args.opencode_db.as_posix(),
                "harnesses": reused["harnesses"],
                "failures": failures,
                "remediation": preflight_remediation,
                "reused_smoke_reports": reused["used_reports"],
                "preflight_route_evidence_mode": "reused_smoke_report",
            }
            write_state(result, output=args.output, state_output=args.state_output, for_preflight=True)
            write_report(result, output=args.output, stdout_mode=args.stdout_mode)
            return 1 if failures else 0
        if failures or not args.smoke or not args.reuse_smoke_report:
            result = {
                "schema_version": 1,
                "status": "failed" if failures else "pass",
                "mode": mode,
                "check_mode": mode,
                "config_validation_mode": config_validation,
                "command": command,
                "config_path": args.config.resolve().as_posix(),
                "profile": config.get("profile"),
                "checked_roles": [],
                "accepted_routes": [],
                "rejected_routes": [],
                "skipped_routes": [],
                "unvisited_routes": [],
                "opencode_binary": resolve_binary("opencode") if isinstance(harnesses, dict) else None,
                "opencode_db": args.opencode_db.as_posix(),
                "harnesses": [],
                "failures": failures,
                "remediation": preflight_remediation,
            }
            write_state(result, output=args.output, state_output=args.state_output, for_preflight=True)
            write_report(result, output=args.output, stdout_mode=args.stdout_mode)
            return 1 if failures else 0

    if (args.discover_provider or args.discover_profile) and not failures:
        candidates: list[dict[str, Any]] = []
        harness_reports: list[dict[str, Any]] = []
        unvisited_routes: list[dict[str, Any]] = []
        discovery_failures: list[str] = []
        if args.discover_profile:
            profile_candidates_, profile_reports, profile_failures, profile_unvisited = discover_profile_routes(
                config,
                profile_name=args.discover_profile,
                providers=[],
                model_filter=args.discover_model_filter,
                max_candidates=args.discover_max,
                smoke=args.smoke,
                models_fixture=models_fixture,
                opencode_db=args.opencode_db,
                include_raw_errors=args.include_raw_errors,
                smoke_cache=smoke_cache,
                discover_all_candidates=args.discover_all_candidates,
            )
            candidates.extend(profile_candidates_)
            harness_reports.extend(profile_reports)
            discovery_failures.extend(profile_failures)
            unvisited_routes.extend(profile_unvisited)
        if args.discover_provider:
            dynamic_candidates, dynamic_reports, dynamic_failures = discover_available_routes(
                config,
                harness_name=args.discover_harness,
                providers=args.discover_provider,
                model_filter=args.discover_model_filter,
                max_candidates=args.discover_max,
                smoke=args.smoke,
                models_fixture=models_fixture,
                opencode_db=args.opencode_db,
                include_raw_errors=args.include_raw_errors,
                smoke_cache=smoke_cache,
            )
            candidates.extend(dynamic_candidates)
            harness_reports.extend(dynamic_reports)
            discovery_failures.extend(dynamic_failures)
        accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
        failures.extend(discovery_failures)
        if not accepted_routes:
            failures.append("discover accepted no routes")
        result = {
            "schema_version": 1,
            "status": "failed" if failures else "pass",
            "mode": mode,
            "check_mode": mode,
            "config_validation_mode": config_validation,
            "command": command,
            "config_path": args.config.resolve().as_posix(),
            "profile": config.get("profile"),
            "discover_profile": args.discover_profile,
            "discover_harness": args.discover_harness,
            "discover_providers": args.discover_provider,
            "discover_model_filter": args.discover_model_filter,
            "candidate_routes": candidates,
            "checked_roles": [str(report["role"]) for report in harness_reports if isinstance(report.get("role"), str)],
            "accepted_routes": accepted_routes,
            "rejected_routes": rejected_routes,
            "skipped_routes": classify_skipped_routes(harness_reports),
            "unvisited_routes": unvisited_routes,
            "opencode_binary": resolve_binary(str(harnesses.get(args.discover_harness, {}).get("command", "opencode")))
            if isinstance(harnesses, dict)
            else None,
            "opencode_db": args.opencode_db.as_posix(),
            "harnesses": harness_reports,
            "failures": failures,
        }
        if preflight_remediation is not None:
            result["remediation"] = preflight_remediation
        write_state(result, output=args.output, state_output=args.state_output, for_preflight=args.for_preflight)
        write_report(result, output=args.output, stdout_mode=args.stdout_mode)
        return 1 if failures else 0

    roles = model_roles(config, args.harness) if not failures else []
    smokes = config.get("harness_smokes") if isinstance(config.get("harness_smokes"), dict) else {}
    harness_reports: list[dict[str, Any]] = []
    model_list_cache: dict[str, dict[str, Any]] = {}
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

        kind = harness.get("kind")
        report["harness_kind"] = kind
        if kind == "opencode":
            model_check, model_failures = check_opencode_model(
                model,
                harness=harness,
                models_fixture=models_fixture,
                model_list_cache=model_list_cache,
            )
        elif kind in {"opencode-bridge", "codex", "generic-cli"}:
            model_check, model_failures = check_non_opencode_model(model, harness=harness)
        else:
            model_check = {"status": "failed", "reason": f"unsupported harness kind: {kind}"}
            model_failures = [f"unsupported harness kind: {kind}"]

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
                    opencode_db=args.opencode_db,
                    include_raw_errors=args.include_raw_errors,
                    smoke_cache=smoke_cache,
                )
                report["smoke"] = smoke_report
                failures.extend(f"{role}: {failure}" for failure in smoke_failures)

        harness_reports.append(report)

    accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
    result = {
        "schema_version": 1,
        "status": "failed" if failures else "pass",
        "mode": mode,
        "check_mode": mode,
        "config_validation_mode": config_validation,
        "command": command,
        "config_path": args.config.resolve().as_posix(),
        "profile": config.get("profile"),
        "checked_roles": roles,
        "accepted_routes": accepted_routes,
        "rejected_routes": rejected_routes,
        "opencode_binary": resolve_binary("opencode") if isinstance(harnesses, dict) else None,
        "opencode_db": args.opencode_db.as_posix(),
        "harnesses": harness_reports,
        "failures": failures,
    }
    if preflight_remediation is not None:
        result["remediation"] = preflight_remediation
    write_state(result, output=args.output, state_output=args.state_output, for_preflight=args.for_preflight)
    write_report(result, output=args.output, stdout_mode=args.stdout_mode)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
