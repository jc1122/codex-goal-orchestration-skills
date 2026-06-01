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
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


MODEL_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+)(?![A-Za-z0-9_.-])")
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
    for value in selected:
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
    else:
        version = command_result([resolved_binary, "--version"], timeout_seconds=20)
        models = command_result([resolved_binary, *model_list_args], timeout_seconds=60) if provider else {
            "returncode": 1,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": 0,
            "timed_out": False,
        }

    if models_fixture is not None:
        available = models_fixture
    else:
        available = parse_models(models["stdout"] + "\n" + models["stderr"])

    result.update(
        {
            "version": version["stdout"].strip() or version["stderr"].strip(),
            "version_returncode": version["returncode"],
            "models_returncode": models["returncode"],
            "models_output_chars": len(models["stdout"]) + len(models["stderr"]),
            "models_elapsed_ms": models["elapsed_ms"],
            "available_model_count": len(available),
            "model_available": provider_model in available,
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
        failures.append(f"model not listed by opencode: {provider_model}")
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
        command = [binary, *smoke_args]
        if timeout_seconds <= 0:
            timeout_seconds = 600

        with tempfile.TemporaryDirectory(prefix=f"goal-config-{role}-") as tmp:
            result = command_result(
                [resolve_binary(binary) or binary, *smoke_args],
                timeout_seconds=timeout_seconds,
            )

        session_id = parse_session_id(result["stdout"])
        readback = read_opencode_session(session_id, opencode_db) if session_id else {"status": "missing_session_id"}
        assistant_response = readback.get("assistant_response") if isinstance(readback.get("assistant_response"), str) else ""
        contains_expected = expected in assistant_response
        if result["timed_out"]:
            failures.append(f"{role} opencode smoke timed out")
        if result["returncode"] != 0:
            failures.append(f"{role} opencode smoke returncode={result['returncode']}")
        if not session_id:
            failures.append(f"{role} opencode smoke did not emit a session id")
        if readback.get("status") != "pass":
            failures.append(f"{role} opencode session readback failed: {readback.get('status')}")
        if not contains_expected:
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
        help="Role to check, such as lite, demanding, lite_agent, or demanding_agent. Defaults to all models.",
    )
    parser.add_argument("--models-output", type=Path, help="Use this opencode models output fixture instead of live CLI.")
    parser.add_argument("--opencode-db", type=Path, default=opencode_db_path(), help="opencode session database path.")
    args = parser.parse_args()

    config = load_json(args.config)
    failures = validate_config_shape(config)
    roles = model_roles(config, args.harness) if not failures else []
    models = config.get("models", {})
    harnesses = config.get("harnesses", {})
    smokes = config.get("harness_smokes") if isinstance(config.get("harness_smokes"), dict) else {}
    models_fixture = fixture_models(args.models_output)
    harness_reports: list[dict[str, Any]] = []

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
            )
        elif kind in {"codex", "gemini", "generic-cli"}:
            model_check, model_failures = check_non_opencode_model(model, harness=harness)
        else:
            model_check = {"status": "failed", "reason": f"unsupported harness kind: {kind}"}
            model_failures = [f"unsupported harness kind: {kind}"]

        report["model_check"] = model_check
        if args.require_models:
            failures.extend(f"{role}: {failure}" for failure in model_failures)

        if args.smoke:
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

    result = {
        "schema_version": 1,
        "status": "failed" if failures else "pass",
        "config_path": args.config.resolve().as_posix(),
        "profile": config.get("profile"),
        "checked_roles": roles,
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
