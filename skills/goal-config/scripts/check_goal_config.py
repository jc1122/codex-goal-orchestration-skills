#!/usr/bin/env python3
"""Validate goal orchestration config model availability and harness smokes."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any


MODEL_RE = re.compile(
    r"(?<![A-Za-z0-9_.~/+-])([~A-Za-z0-9_.+-]+(?:/[~A-Za-z0-9_.:+-]+)+)(?![A-Za-z0-9_.~/+-])"
)
ROLE_ALIASES = {
    "lite": "lite_agent",
    "flash": "lite_agent",
    "lite_agent": "lite_agent",
    "demanding": "demanding_agent",
    "pro": "demanding_agent",
    "heavy": "demanding_agent",
    "demanding_agent": "demanding_agent",
}
HARNESS_KIND_VALUES = {"opencode", "codex", "gemini", "generic-cli"}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"config must be a JSON object: {path}")
    return data


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
        candidates.add(model[len(prefix):])
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


def extract_opencode_errors(stdout: str, stderr: str) -> list[dict[str, str]]:
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
    auth_match = re.search(r"(?i)(401|403)?[^.\n]*(AuthenticateToken|authentication failed|unauthorized)[^.\n]*", combined)
    if auth_match:
        message = auth_match.group(0).strip()
        status = "401" if "401" in message else ("403" if "403" in message else "")
        key = (status or None, message)
        if key not in seen:
            entry = {"message": message}
            if status:
                entry["status"] = status
            errors.append(entry)
    return errors


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
        models = command_result([resolved_binary, *model_list_args], timeout_seconds=60) if provider else {
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": 0,
            "timed_out": False,
        }
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

        result = command_result(
            [resolve_binary(binary) or binary, *smoke_args],
            timeout_seconds=timeout_seconds,
        )

        session_id = parse_session_id(result["stdout"])
        opencode_errors = extract_opencode_errors(result["stdout"], result["stderr"])
        readback = read_opencode_session(session_id, opencode_db) if session_id else {"status": "missing_session_id"}
        assistant_response = readback.get("assistant_response") if isinstance(readback.get("assistant_response"), str) else ""
        contains_expected = expected in assistant_response
        if result["timed_out"]:
            failures.append(f"{role} opencode smoke timed out")
        if result["returncode"] != 0:
            failures.append(f"{role} opencode smoke returncode={result['returncode']}")
        for error in opencode_errors:
            status = f" status={error['status']}" if error.get("status") else ""
            message = f" message={error['message']}" if error.get("message") else ""
            failures.append(f"{role} opencode error{status}{message}")
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
            "tokens": readback.get(
                "tokens",
                {"input": None, "output": None, "reasoning": None, "cache_read": None, "cache_write": None},
            ),
            "response_excerpt": assistant_response[:240],
        }, failures

    resolved = resolve_binary(binary)
    if resolved is None:
        return {
            "status": "failed",
            "reason": f"{kind} binary not found",
        }, [f"{role} {kind} binary not found"]

    result = command_result([resolved, *smoke_args], timeout_seconds=timeout_seconds)
    output = result["stdout"] + result["stderr"]
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
        "response_excerpt": output[:240],
    }, failures


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


def classify_routes(harness_reports: list[dict[str, Any]], *, smoke_requested: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
                        status = f" status={error['status']}" if error.get("status") else ""
                        message = f" message={error['message']}" if error.get("message") else ""
                        reasons.append(f"opencode error{status}{message}")
        if reasons:
            rejected.append({**route, "reasons": reasons})
        else:
            accepted.append(route)
    return accepted, rejected


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
            smoke_report, _smoke_failures = run_harness_smoke(
                candidate["role"],
                model,
                discovery_smoke(candidate["role"], timeout_seconds),
                harness=harness,
                opencode_db=opencode_db,
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
    parser.add_argument("--models-output", type=Path, help="Use this opencode models output fixture instead of live CLI.")
    parser.add_argument("--opencode-db", type=Path, default=opencode_db_path(), help="opencode session database path.")
    args = parser.parse_args()

    config = load_json(args.config)
    failures = validate_config_shape(config)
    models = config.get("models", {})
    harnesses = config.get("harnesses", {})
    models_fixture = fixture_models(args.models_output)
    if args.discover_max is not None and args.discover_max <= 0:
        failures.append("--discover-max must be a positive integer")

    if args.discover_provider and not failures:
        candidates, harness_reports, discovery_failures = discover_available_routes(
            config,
            harness_name=args.discover_harness,
            providers=args.discover_provider,
            model_filter=args.discover_model_filter,
            max_candidates=args.discover_max,
            smoke=args.smoke,
            models_fixture=models_fixture,
            opencode_db=args.opencode_db,
        )
        accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
        failures.extend(discovery_failures)
        if not accepted_routes:
            failures.append("discover accepted no routes")
        result = {
            "schema_version": 1,
            "status": "failed" if failures else "pass",
            "mode": "discover",
            "config_path": args.config.resolve().as_posix(),
            "profile": config.get("profile"),
            "discover_harness": args.discover_harness,
            "discover_providers": args.discover_provider,
            "discover_model_filter": args.discover_model_filter,
            "candidate_routes": candidates,
            "checked_roles": [candidate["role"] for candidate in candidates],
            "accepted_routes": accepted_routes,
            "rejected_routes": rejected_routes,
            "opencode_binary": resolve_binary(str(harnesses.get(args.discover_harness, {}).get("command", "opencode"))) if isinstance(harnesses, dict) else None,
            "opencode_db": args.opencode_db.as_posix(),
            "harnesses": harness_reports,
            "failures": failures,
        }
        text = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        print(text, end="")
        return 1 if failures else 0

    roles = model_roles(config, args.harness) if not failures else []
    smokes = config.get("harness_smokes") if isinstance(config.get("harness_smokes"), dict) else {}
    harness_reports: list[dict[str, Any]] = []
    model_list_cache: dict[str, dict[str, Any]] = {}
    missing_smoke_roles: set[str] = set()
    if args.smoke:
        missing_smoke_roles = {
            role
            for role in roles
            if not isinstance(smokes.get(role), dict)
        }
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
        elif kind in {"codex", "gemini", "generic-cli"}:
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
                smoke_report, smoke_failures = run_harness_smoke(
                    role,
                    model,
                    smoke,
                    harness=harness,
                    opencode_db=args.opencode_db,
                )
                report["smoke"] = smoke_report
                failures.extend(f"{role}: {failure}" for failure in smoke_failures)

        harness_reports.append(report)

    accepted_routes, rejected_routes = classify_routes(harness_reports, smoke_requested=args.smoke)
    result = {
        "schema_version": 1,
        "status": "failed" if failures else "pass",
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
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
