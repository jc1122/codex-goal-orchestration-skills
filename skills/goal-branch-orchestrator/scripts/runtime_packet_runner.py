#!/usr/bin/env python3
"""Run compact runtime packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TIMEOUT_NOT_FOUND = "timeout command not found; refusing unbounded {role} attempt.\n"
CONFIG_NAME = "launch-config.json"
WORKER_STATUS_BEGIN = "BEGIN_WORKER_STATUS_JSON"
WORKER_STATUS_END = "END_WORKER_STATUS_JSON"
TIMEOUT_RETURN_CODES = {124, 137}
STREAM_DISCONNECT_PATTERN = re.compile(r"stream disconnected", re.IGNORECASE)
CAPACITY_ERROR_CODES = {"MODEL_CAPACITY_EXHAUSTED", "RESOURCE_EXHAUSTED"}
LAUNCHER_STATES = ("active", "timeout", "fail-clean", "fail-dirty", "pass", "blocked")
GENERATED_CLEANUP_NAME = "generated-artifact-cleanup.json"
CACHE_PATH_PARTS = (
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
)
CACHE_PATH_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".egg-info",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON must be an object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_debug_event(packet_dir: Path, config: dict[str, Any], event: dict[str, Any]) -> None:
    name = config.get("debug_events_name")
    if not isinstance(name, str) or not name.strip():
        return
    payload = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "packet_id": config.get("packet_id"),
        "role": config.get("role"),
        **event,
    }
    with (packet_dir / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def string_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{CONFIG_NAME} missing non-empty string: {key}")
    return value


def optional_string_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise SystemExit(f"{CONFIG_NAME} {key} must be a string when present")
    return value


def int_value(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SystemExit(f"{CONFIG_NAME} missing positive integer: {key}")
    return value


def bool_value(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise SystemExit(f"{CONFIG_NAME} {key} must be a boolean when present")
    return value


def list_value(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list):
        raise SystemExit(f"{CONFIG_NAME} missing list: {key}")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise SystemExit(f"{CONFIG_NAME} {key}[{index}] must be an object")
        result.append(item)
    return result


def check_worktree(worktree: str) -> None:
    result = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def clear_invalid_output_for_fallback(output_path: Path) -> None:
    remove_if_exists(output_path)


def clean_outputs(packet_dir: Path, output_name: str, attempts: list[dict[str, Any]]) -> None:
    remove_if_exists(packet_dir / output_name)
    remove_if_exists(packet_dir / "telemetry.json")
    remove_if_exists(packet_dir / "packet.summary.json")
    remove_if_exists(packet_dir / "fallback.blocked.txt")
    remove_if_exists(packet_dir / "launcher-state.json")
    remove_if_exists(packet_dir / GENERATED_CLEANUP_NAME)
    seen: set[str] = set()
    for attempt in attempts:
        for key in ("event_logs", "probe_logs"):
            logs = attempt.get(key, [])
            if not isinstance(logs, list):
                continue
            for value in logs:
                if isinstance(value, str) and value not in seen:
                    seen.add(value)
                    remove_if_exists(packet_dir / value)
    for path in glob.glob((packet_dir / "events-*.jsonl").as_posix()):
        remove_if_exists(Path(path))
    for path in glob.glob((packet_dir / "events-*.log").as_posix()):
        remove_if_exists(Path(path))
    for path in glob.glob((packet_dir / "events-*-opencode-readback.json").as_posix()):
        remove_if_exists(Path(path))


def opencode_db_path() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


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


def safe_parse_json_dict(data: str) -> dict[str, Any] | None:
    value = safe_json(data)
    return value if value else None


def collect_lines(*paths: Path) -> list[str]:
    lines: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def detect_provider_error_code(lines: list[str]) -> str | None:
    for line in lines:
        parsed = safe_parse_json_dict(line)
        for key in ("error_code", "code", "status"):
            value = parsed.get(key) if parsed is not None and isinstance(parsed, dict) else None
            if isinstance(value, str):
                normalized = value.strip().upper()
                if normalized in CAPACITY_ERROR_CODES:
                    return normalized
        upper = line.upper()
        for code in CAPACITY_ERROR_CODES:
            if code in upper:
                return code
    return None


def summarize_route_health(lines: list[str]) -> dict[str, Any]:
    transport_disconnect_count = 0
    for line in lines:
        if STREAM_DISCONNECT_PATTERN.search(line):
            transport_disconnect_count += 1
    provider_error_code = detect_provider_error_code(lines)
    return {
        "transport_disconnect_count": transport_disconnect_count,
        "capacity_exhausted": provider_error_code in CAPACITY_ERROR_CODES,
    }


def status_objects_from_text(raw_text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        parsed = safe_parse_json_dict(line)
        if parsed is not None:
            objects.append(parsed)
    parsed = safe_parse_json_dict(raw_text)
    if parsed is not None and parsed not in objects:
        objects.append(parsed)
    return objects


def read_opencode_assistant_text(session_id: str, db_path: Path) -> tuple[str, dict[str, Any]]:
    if not db_path.exists():
        return "", {"status": "missing_db", "db_path": db_path.as_posix()}
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            """
            select id, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write
            from session where id=?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return "", {"status": "missing_session", "session_id": session_id}
        roles: dict[str, str] = {}
        for message_id, message_data in con.execute(
            "select id, data from message where session_id=? order by time_created",
            (session_id,),
        ):
            parsed = safe_json(message_data)
            role = parsed.get("role")
            if isinstance(role, str):
                roles[message_id] = role
        texts: list[str] = []
        for message_id, part_data in con.execute(
            "select message_id, data from part where session_id=? order by time_created",
            (session_id,),
        ):
            if roles.get(message_id) != "assistant":
                continue
            part = safe_json(part_data)
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "".join(texts), {
            "status": "pass",
            "session_id": row[0],
            "tokens": {
                "input": row[1],
                "output": row[2],
                "reasoning": row[3],
                "cache_read": row[4],
                "cache_write": row[5],
            },
        }
    finally:
        con.close()


def state_artifact_name(config: dict[str, Any]) -> str:
    value = config.get("state_artifact")
    return value if isinstance(value, str) and value.strip() else "launcher-state.json"


def classify_attempt_state(returncode: int, *, output_nonempty: bool, dirty: bool) -> str:
    if returncode == 0:
        return "pass"
    if dirty:
        return "fail-dirty"
    if returncode in TIMEOUT_RETURN_CODES:
        return "timeout"
    return "fail-clean"


def _event_parse_messages(parse_report: dict[str, Any]) -> list[str]:
    messages = parse_report.get("messages")
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, str) and item.strip()]


def _normalize_route_health(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "transport_disconnect_count": 0,
            "capacity_exhausted": False,
        }
    return {
        "transport_disconnect_count": int(value.get("transport_disconnect_count", 0) or 0),
        "capacity_exhausted": bool(value.get("capacity_exhausted", False)),
    }


def attempt_failure_subclass(
    parse_report: dict[str, Any],
    route_health: dict[str, Any],
    attempt_state: str,
    message: str | None = None,
) -> str | None:
    if parse_report.get("failure_subclass"):
        value = str(parse_report["failure_subclass"])
        return value
    if parse_report.get("provider_error_code"):
        provider_error_code = str(parse_report["provider_error_code"])
        if provider_error_code in CAPACITY_ERROR_CODES and attempt_state in {"fail-clean", "fail-dirty", "timeout"}:
            return "provider_capacity_exhausted"
    if attempt_state in {"fail-clean", "fail-dirty", "timeout"}:
        if _normalize_route_health(route_health).get("capacity_exhausted") and parse_report.get("provider_error_code"):
            return "provider_capacity_exhausted"
    if _normalize_route_health(route_health).get("transport_disconnect_count", 0):
        return "transport_disconnect"
    if message:
        lowered = message.lower()
        if "outside owned paths" in lowered:
            return "owned_path_violation"
    return None


def _extract_attempt_evidence_lines(output_path: Path, event_path: Path | None) -> list[str]:
    if event_path is None:
        return collect_lines(output_path)
    return collect_lines(output_path, event_path)


def _finalize_attempt_observation(
    attempt: dict[str, Any],
    *,
    parse_report: dict[str, Any],
    output_path: Path,
    event_path: Path | None,
    attempt_state: str,
    returncode: int,
    dirty: bool,
    output_nonempty: bool,
    message: str = "",
) -> None:
    lines = _extract_attempt_evidence_lines(output_path, event_path)
    route_health = summarize_route_health(lines)
    provider_error_code = parse_report.get("provider_error_code")
    if not provider_error_code:
        provider_error_code = detect_provider_error_code(lines)
    provider_error_code = str(provider_error_code) if isinstance(provider_error_code, str) and provider_error_code else None
    parse_report["provider_error_code"] = provider_error_code
    if provider_error_code:
        attempt["provider_error_code"] = provider_error_code
    attempt["route_health"] = route_health
    failure_subclass = attempt_failure_subclass(parse_report, route_health, attempt_state, message=message or None)
    if failure_subclass is not None:
        parse_report["failure_subclass"] = parse_report.get("failure_subclass") or failure_subclass
        attempt["failure_subclass"] = parse_report["failure_subclass"]
    else:
        attempt.pop("failure_subclass", None)
    attempt["failure_class"] = attempt_failure_class(
        {
            "state": attempt_state,
            "returncode": returncode,
            "dirty": dirty,
            "output_nonempty": output_nonempty,
            "message": message,
        },
        attempt,
        parse_report=parse_report,
    )


def _parse_failure_detected(parse_report: dict[str, Any]) -> bool:
    failure_subclass = parse_report.get("failure_subclass")
    return failure_subclass in {"marker_protocol", "schema_validation_failure", "parser_failure"}


def record_executed_command(attempt: dict[str, Any], command: list[str]) -> None:
    attempt["executed_command"] = shlex.join(str(item) for item in command)


def write_launcher_state(
    packet_dir: Path,
    config: dict[str, Any],
    *,
    state: str,
    attempt: dict[str, Any] | None = None,
    attempt_index: int | None = None,
    returncode: int | None = None,
    dirty: bool | None = None,
    output_nonempty: bool | None = None,
    message: str = "",
) -> None:
    if state not in LAUNCHER_STATES:
        raise SystemExit(f"unsupported launcher state: {state}")
    path = packet_dir / state_artifact_name(config)
    if path.exists():
        data = read_json(path)
    else:
        data = {
            "schema_version": 1,
            "packet_id": string_value(config, "packet_id"),
            "role": string_value(config, "role"),
            "state_machine": "active -> timeout|fail-clean|fail-dirty|pass|blocked",
            "terminal_state": None,
            "events": [],
        }
    events = data.get("events")
    if not isinstance(events, list):
        events = []
        data["events"] = events
    event = {
        "seq": len(events) + 1,
        "state": state,
    }
    if attempt_index is not None:
        event["attempt_index"] = attempt_index
    if attempt is not None:
        event["alias"] = attempt.get("alias")
        event["provider"] = attempt.get("provider")
        event["model"] = attempt.get("model")
        for key in ["command", "rendered_command", "executed_command"]:
            value = attempt.get(key)
            if isinstance(value, str) and value.strip():
                event[key] = value
        for key in (
            "failure_class",
            "failure_subclass",
            "provider_error_code",
            "owned_path_violation",
            "generated_artifact_cleanup",
            "generated_artifact_cleanup_path",
        ):
            if key in attempt:
                event[key] = attempt.get(key)
        route_health = attempt.get("route_health")
        if isinstance(route_health, dict):
            event["route_health"] = _normalize_route_health(route_health)
    if returncode is not None:
        event["returncode"] = returncode
    if dirty is not None:
        event["dirty"] = dirty
    if output_nonempty is not None:
        event["output_nonempty"] = output_nonempty
    if message:
        event["message"] = message
    events.append(event)
    if state == "active":
        data["terminal_state"] = None
    elif state in {"timeout", "fail-clean", "fail-dirty", "pass", "blocked"}:
        data["terminal_state"] = state
    write_json(path, data)


def read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except SystemExit:
        return {}


def packet_next_action(output_status: object, terminal_state: object) -> str:
    if output_status in {"pass", "mergeable"} and terminal_state == "pass":
        return "validate_and_collect"
    if output_status in {"blocked", "failed"} or terminal_state == "blocked":
        return "close_blocked_or_create_repair"
    if terminal_state in {"timeout", "fail-clean", "fail-dirty"}:
        return "inspect_packet_failure"
    return "inspect_packet_artifacts"


def attempt_failure_class(
    event: dict[str, Any],
    attempt: dict[str, Any],
    parse_report: dict[str, Any] | None = None,
) -> str:
    parse_report = parse_report or {}
    state = event.get("state")
    if state in {None, "active"}:
        return "unknown"
    if state == "pass":
        return "none"
    if state == "timeout":
        return "timeout"
    failure_subclass = parse_report.get("failure_subclass")
    if failure_subclass:
        return "schema_or_output_readback"
    message = str(event.get("message", "")).lower()
    if "outside owned paths" in message:
        return "ownership"
    if event.get("dirty") is True:
        return "dirty_worktree"
    returncode = event.get("returncode")
    if returncode in {126, 127}:
        return "harness_unavailable"
    if event.get("output_nonempty") is True:
        return "schema_or_output_readback"
    provider = attempt.get("harness_kind") or attempt.get("provider")
    if provider:
        return f"{provider}_failure"
    return "harness_failure"


def write_packet_summary(packet_dir: Path, config: dict[str, Any]) -> None:
    output_name = string_value(config, "output_name")
    output_path = packet_dir / output_name
    telemetry_path = packet_dir / "telemetry.json"
    launcher_path = packet_dir / state_artifact_name(config)
    cleanup_path = packet_dir / GENERATED_CLEANUP_NAME
    output = read_optional_json(output_path)
    telemetry = read_optional_json(telemetry_path)
    launcher = read_optional_json(launcher_path)
    cleanup = read_optional_json(cleanup_path)
    launcher_events = launcher.get("events") if isinstance(launcher.get("events"), list) else []
    telemetry_attempts = telemetry.get("attempts") if isinstance(telemetry.get("attempts"), list) else []
    attempts: list[dict[str, Any]] = []
    for index, attempt in enumerate(list_value(config, "attempts")):
        attempt_events = [
            event for event in launcher_events
            if isinstance(event, dict) and event.get("attempt_index") == index
        ]
        last_event = attempt_events[-1] if attempt_events else {}
        telemetry_attempt = telemetry_attempts[index] if index < len(telemetry_attempts) and isinstance(telemetry_attempts[index], dict) else {}
        attempts.append(
            {
                "attempt_index": index,
                "alias": attempt.get("alias"),
                "provider": attempt.get("provider"),
                "harness": attempt.get("harness"),
                "harness_kind": attempt.get("harness_kind"),
                "model": attempt.get("model"),
                "timeout_seconds": attempt.get("timeout_seconds"),
                "rendered_command": attempt.get("rendered_command"),
                "state": last_event.get("state"),
                "returncode": last_event.get("returncode"),
                "dirty": last_event.get("dirty"),
                "output_nonempty": last_event.get("output_nonempty"),
                "executed_command": last_event.get("executed_command"),
                "failure_class": attempt.get("failure_class")
                or attempt_failure_class(last_event, attempt, parse_report=attempt.get("_parse_report", {})),
                "failure_subclass": attempt.get("failure_subclass") or last_event.get("failure_subclass"),
                "provider_error_code": last_event.get("provider_error_code") or attempt.get("provider_error_code"),
                "route_health": _normalize_route_health(last_event.get("route_health")),
                "owned_path_violation": attempt.get("owned_path_violation"),
                "generated_artifact_cleanup": attempt.get("generated_artifact_cleanup") or last_event.get("generated_artifact_cleanup"),
                "generated_artifact_cleanup_path": attempt.get("generated_artifact_cleanup_path")
                or last_event.get("generated_artifact_cleanup_path"),
                "called": telemetry_attempt.get("called"),
                "accepted": telemetry_attempt.get("accepted"),
                "usage": telemetry_attempt.get("usage"),
            }
        )
    output_status = output.get("status") or output.get("verdict")
    summary = {
        "schema_version": 1,
        "packet_id": string_value(config, "packet_id"),
        "role": string_value(config, "role"),
        "route_class": config.get("route_class"),
        "selected_ladder": config.get("selected_ladder", []),
        "selection_reason": config.get("selection_reason", ""),
        "worktree": string_value(config, "worktree"),
        "output_path": output_name,
        "output_exists": output_path.exists(),
        "output_status": output_status,
        "changed_files": output.get("changed_files") if isinstance(output.get("changed_files"), list) else [],
        "blockers": output.get("blockers") if isinstance(output.get("blockers"), list) else [],
        "telemetry_path": "telemetry.json",
        "telemetry_exists": telemetry_path.exists(),
        "launcher_state_path": state_artifact_name(config),
        "launcher_state_exists": launcher_path.exists(),
        "terminal_state": launcher.get("terminal_state"),
        "generated_artifact_cleanup_path": GENERATED_CLEANUP_NAME,
        "generated_artifact_cleanup_exists": cleanup_path.exists(),
        "generated_artifact_cleanup": summarize_generated_artifact_cleanup(cleanup),
        "attempts": attempts,
        "next_action": packet_next_action(output_status, launcher.get("terminal_state")),
    }
    write_json(packet_dir / "packet.summary.json", summary)
    append_debug_event(packet_dir, config, {"phase": "packet_summary", "event": "written"})


def event_label(attempt: dict[str, Any], fallback: str) -> str:
    logs = attempt.get("event_logs")
    if isinstance(logs, list) and logs:
        first = logs[0]
        if isinstance(first, str) and first.startswith("events-"):
            return first.removeprefix("events-").split(".")[0]
    alias = attempt.get("alias")
    if isinstance(alias, str) and alias:
        return alias.replace(".", "-")
    return fallback


def run_with_timeout(
    *,
    command: list[str],
    timeout_seconds: int,
    kill_after_seconds: int,
    role: str,
    cwd: str,
    stdin_data: bytes | None,
    stdout_path: Path,
) -> int:
    if shutil.which("timeout") is None:
        stdout_path.write_text(TIMEOUT_NOT_FOUND.format(role=role), encoding="utf-8")
        return 127
    cache_root = stdout_path.parent / ".runtime-cache"
    tmp_root = cache_root / "tmp"
    xdg_cache = cache_root / "xdg-cache"
    tmp_root.mkdir(parents=True, exist_ok=True)
    xdg_cache.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TMPDIR"] = tmp_root.as_posix()
    env["XDG_CACHE_HOME"] = xdg_cache.as_posix()
    pytest_addopts = env.get("PYTEST_ADDOPTS", "").strip()
    cache_opt = "-p no:cacheprovider"
    env["PYTEST_ADDOPTS"] = (pytest_addopts + " " + cache_opt).strip() if cache_opt not in pytest_addopts else pytest_addopts
    full_command = [
        "timeout",
        "--foreground",
        f"--kill-after={kill_after_seconds}s",
        f"{timeout_seconds}s",
    ] + command
    with stdout_path.open("wb") as stdout:
        result = subprocess.run(
            full_command,
            cwd=cwd,
            input=stdin_data,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
        )
    return result.returncode


def first_log_path(packet_dir: Path, attempt: dict[str, Any], key: str, fallback: str) -> Path:
    logs = attempt.get(key, [])
    if isinstance(logs, list):
        for value in logs:
            if isinstance(value, str) and value:
                return packet_dir / value
    return packet_dir / fallback


def validate_probe_output(path: Path, prompt: str, label: str) -> int:
    expected = prompt.rsplit(":", 1)[-1].strip()
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    if expected in text:
        return 0
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n{label} model probe did not return expected token: {expected}\n")
    return 1


def worktree_status_lines(worktree: str, *, untracked_files_all: bool = False) -> list[str]:
    command = ["git", "-C", worktree, "status", "--porcelain"]
    if untracked_files_all:
        command.append("--untracked-files=all")
    try:
        status = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return []
    return [line for line in status.stdout.splitlines() if line.strip()]


def porcelain_status_path(line: str) -> str:
    path = line[3:] if len(line) > 3 and line[2] == " " else line.strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip()


def is_runtime_cache_path(path: str) -> bool:
    parts = [part for part in Path(path).parts if part]
    if any(part in CACHE_PATH_PARTS for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if path.endswith(CACHE_PATH_SUFFIXES):
        return True
    return path.startswith(".runtime-cache/") or path == ".runtime-cache"


def generated_artifact_cleanup_root(path: str) -> str:
    parts = [part for part in Path(path).parts if part]
    for index, part in enumerate(parts):
        if part in CACHE_PATH_PARTS or part.endswith(".egg-info") or part == ".runtime-cache":
            return Path(*parts[: index + 1]).as_posix()
    return path


def generated_artifact_paths(worktree: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for line in worktree_status_lines(worktree, untracked_files_all=True):
        if not line.startswith("?? "):
            continue
        path = porcelain_status_path(line)
        if path and path not in seen and is_runtime_cache_path(path):
            cleanup_root = generated_artifact_cleanup_root(path)
            if cleanup_root not in seen:
                seen.add(cleanup_root)
                paths.append(cleanup_root)
    return sorted(paths)


def safe_worktree_target(worktree: str, relative_path: str) -> Path | None:
    path = Path(relative_path)
    if path.is_absolute():
        return None
    root = Path(worktree).resolve()
    target = root / relative_path
    try:
        target.parent.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return target


def _cleanup_status(candidates_count: int, failed_count: int, removed_count: int) -> str:
    if failed_count:
        return "partial"
    if candidates_count or removed_count:
        return "pass"
    return "skipped"


def summarize_generated_artifact_cleanup(data: dict[str, Any]) -> dict[str, Any]:
    records = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    return {
        "status": data.get("status", "skipped"),
        "generated_artifacts_only": data.get("generated_artifacts_only", True),
        "attempts": len(records),
        "candidates_count": data.get("candidates_count", 0),
        "removed_count": data.get("removed_count", 0),
        "failed_count": data.get("failed_count", 0),
    }


def cleanup_attempt_summary(cleanup: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": cleanup.get("status", "skipped"),
        "generated_artifacts_only": cleanup.get("generated_artifacts_only", True),
        "candidates_count": cleanup.get("candidates_count", 0),
        "removed_count": cleanup.get("removed_count", 0),
        "failed_count": cleanup.get("failed_count", 0),
    }


def cleanup_generated_artifacts(worktree: str, *, attempt_index: int, attempt: dict[str, Any]) -> dict[str, Any]:
    candidates = generated_artifact_paths(worktree)
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    for relative_path in candidates:
        target = safe_worktree_target(worktree, relative_path.rstrip("/"))
        if target is None:
            failed.append({"path": relative_path, "error": "path escaped worktree"})
            continue
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(relative_path)
        except FileNotFoundError:
            removed.append(relative_path)
        except OSError as exc:
            failed.append({"path": relative_path, "error": f"{exc.__class__.__name__}: {exc}"})
    cleanup = {
        "schema_version": 1,
        "attempt_index": attempt_index,
        "alias": attempt.get("alias"),
        "provider": attempt.get("provider"),
        "model": attempt.get("model"),
        "generated_artifacts_only": True,
        "candidates": candidates,
        "removed": removed,
        "failed": failed,
        "candidates_count": len(candidates),
        "removed_count": len(removed),
        "failed_count": len(failed),
    }
    cleanup["status"] = _cleanup_status(len(candidates), len(failed), len(removed))
    return cleanup


def record_generated_artifact_cleanup(packet_dir: Path, attempt: dict[str, Any], cleanup: dict[str, Any]) -> None:
    if cleanup.get("status") == "skipped" and not (packet_dir / GENERATED_CLEANUP_NAME).exists():
        return
    path = packet_dir / GENERATED_CLEANUP_NAME
    data = read_optional_json(path)
    records = data.get("attempts") if isinstance(data.get("attempts"), list) else []
    records.append(cleanup)
    candidates_count = sum(
        int(record.get("candidates_count", 0) or 0)
        for record in records
        if isinstance(record, dict)
    )
    removed_count = sum(
        int(record.get("removed_count", 0) or 0)
        for record in records
        if isinstance(record, dict)
    )
    failed_count = sum(
        int(record.get("failed_count", 0) or 0)
        for record in records
        if isinstance(record, dict)
    )
    aggregate = {
        "schema_version": 1,
        "status": _cleanup_status(candidates_count, failed_count, removed_count),
        "generated_artifacts_only": True,
        "candidates_count": candidates_count,
        "removed_count": removed_count,
        "failed_count": failed_count,
        "attempts": records,
    }
    write_json(path, aggregate)
    attempt["generated_artifact_cleanup_path"] = GENERATED_CLEANUP_NAME
    attempt["generated_artifact_cleanup"] = cleanup_attempt_summary(cleanup)


def actionable_worktree_status_lines(worktree: str) -> list[str]:
    return [
        line
        for line in worktree_status_lines(worktree)
        if not is_runtime_cache_path(porcelain_status_path(line))
    ]


def is_worktree_dirty(worktree: str, *, ignore_runtime_cache: bool = False) -> bool:
    status_lines = actionable_worktree_status_lines(worktree) if ignore_runtime_cache else worktree_status_lines(worktree)
    return bool(status_lines)


def file_fingerprint(worktree: str, path: str) -> str:
    target = Path(worktree) / path
    try:
        stat_result = target.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"error:{exc.__class__.__name__}"
    if target.is_symlink():
        try:
            return f"symlink:{os.readlink(target)}"
        except OSError as exc:
            return f"symlink-error:{exc.__class__.__name__}"
    if target.is_file():
        digest = hashlib.sha256()
        try:
            with target.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            return f"file:{stat_result.st_size}:{digest.hexdigest()}"
        except OSError as exc:
            return f"file-error:{exc.__class__.__name__}"
    if target.is_dir():
        return "dir"
    return f"other:{stat_result.st_mode}:{stat_result.st_size}"


def changed_file_fingerprints(worktree: str) -> dict[str, str]:
    return {
        path: file_fingerprint(worktree, path)
        for path in extract_changed_files(worktree)
    }


def packet_delta_changed_files(worktree: str, baseline: dict[str, str]) -> list[str]:
    current = changed_file_fingerprints(worktree)
    changed: list[str] = []
    for path in sorted(set(current) | set(baseline)):
        current_fingerprint = current[path] if path in current else file_fingerprint(worktree, path)
        if current_fingerprint != baseline.get(path):
            changed.append(path)
    return changed


def path_is_owned(path: str, owned_paths: list[str]) -> bool:
    for owned in owned_paths:
        if path == owned or path.startswith(f"{owned.rstrip('/')}/"):
            return True
    return False


def worker_ownership_violations(config: dict[str, Any], changed_files: list[str]) -> list[str]:
    owned_files = [
        item
        for item in config.get("owned_files", [])
        if isinstance(item, str) and item.strip()
    ] if isinstance(config.get("owned_files"), list) else []
    if not owned_files:
        return []
    violations: list[str] = []
    for changed in changed_files:
        if not path_is_owned(changed, owned_files):
            violations.append(changed)
    return violations


def extract_changed_files(worktree: str) -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "-C", worktree, "status", "--short"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001
        return []
    changed_files = []
    for line in output.splitlines():
        path = porcelain_status_path(line)
        if path and not is_runtime_cache_path(path):
            changed_files.append(path)
    return changed_files


def write_terminal_worker(packet_dir: Path, config: dict[str, Any], message: str, *, changed_files: list[str] | None = None) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "worker",
        "status": "blocked",
        "branch_id": string_value(config, "branch_id"),
        "work_item_id": optional_string_value(config, "work_item_id"),
        "manifest_hash": optional_string_value(config, "manifest_hash"),
        "manifest_epoch": string_value(config, "manifest_epoch"),
        "worktree_path": string_value(config, "worktree_path") or string_value(config, "worktree"),
        "route_id": string_value(config, "route_id"),
        "evidence_summary": string_value(config, "evidence_summary") or message,
        "branch": string_value(config, "branch"),
        "worktree": string_value(config, "worktree"),
        "route_class": string_value(config, "route_class"),
        "selected_ladder": config.get("selected_ladder", []),
        "selection_reason": config.get("selection_reason", ""),
        "changed_files": changed_files if changed_files is not None else extract_changed_files(string_value(config, "worktree")),
        "commands_run": config.get("selected_commands", [item.get("command", "") for item in list_value(config, "attempts")]),
        "tests": [],
        "blockers": [
            message,
            "Inspect worker event logs in this packet directory for the underlying CLI, schema, quota, auth, or model error.",
        ],
        "handoff": message
        + " Inspect worker event logs in this packet directory for the underlying CLI, schema, quota, auth, or model error.",
    }
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_terminal_research(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    output_name = string_value(config, "output_name")
    commands = [
        (
            f"codex --search exec --ephemeral -m {attempt.get('model', '')} "
            f"-s read-only --json --output-schema {string_value(config, 'schema_name')} -o {output_name}"
        )
        for attempt in list_value(config, "attempts")
    ]
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "research-worker",
        "status": "blocked",
        "branch": string_value(config, "branch"),
        "worktree": string_value(config, "worktree"),
        "search_queries": [],
        "source_urls": [],
        "tools_used": [],
        "local_files_read": [],
        "commands_run": commands,
        "findings": [message],
        "blockers": [
            message,
            "Inspect research-worker event logs in this packet directory for the underlying CLI or schema error.",
        ],
        "handoff": message + " Inspect research-worker event logs in this packet directory for the underlying CLI or schema error.",
    }
    write_json(output_path, data)


def write_terminal_review(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "reviewer",
        "verdict": "blocked",
        "findings": [message],
        "commands_run": config.get("terminal_commands", []),
        "verification_gaps": [
            message,
            "Inspect reviewer event logs in this packet directory for the underlying CLI or schema error.",
        ],
        "residual_risks": [],
        "semantic_input_hashes": config.get("semantic_input_hashes", {}),
        "reuse_policy": config.get("reuse_policy", {}),
        "summary": message,
    }
    write_json(output_path, data)


def write_terminal(packet_dir: Path, config: dict[str, Any], message: str, *, changed_files: list[str] | None = None) -> None:
    role = string_value(config, "role")
    if role == "research-worker":
        write_terminal_research(packet_dir, config, message)
    elif role == "reviewer":
        write_terminal_review(packet_dir, config, message)
    elif role == "worker":
        write_terminal_worker(packet_dir, config, message, changed_files=changed_files)
    else:
        raise SystemExit(f"unsupported compact runner role: {role}")


def write_telemetry(packet_dir: Path, config: dict[str, Any]) -> None:
    script = string_value(config, "telemetry_script")
    command = [
        "python3",
        script,
        "--packet-dir",
        packet_dir.as_posix(),
        "--packet-id",
        string_value(config, "packet_id"),
        "--role",
        string_value(config, "role"),
        "--output-name",
        string_value(config, "output_name"),
        "--prompt-name",
        "prompt.md",
    ]
    for attempt in list_value(config, "attempts"):
        command.extend(["--attempt-json", json.dumps(attempt, sort_keys=True)])
    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        command.extend(["--debug", "--debug-output", debug_name])
    subprocess.run(command, check=False)
    append_debug_event(packet_dir, config, {"phase": "telemetry", "event": "written"})
    write_packet_summary(packet_dir, config)


def run_gemini_probe_command(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], worktree: str) -> int:
    model = attempt.get("probe_model")
    if not isinstance(model, str) or not model:
        raise SystemExit(f"{CONFIG_NAME} missing probe model for {label}")
    approval = string_value(config, "gemini_approval_mode") if isinstance(config.get("gemini_approval_mode"), str) else "yolo"
    prompt = str(attempt.get("probe_prompt", string_value(config, "gemini_probe_prompt")))
    command = [
        string_value(config, "gemini_command"),
        "--model",
        model,
        "--approval-mode",
        approval,
        "--skip-trust",
        "-p",
        prompt,
    ]
    record_executed_command(attempt, command)
    timeout_seconds = int_value(attempt, "probe_timeout_seconds") if isinstance(attempt.get("probe_timeout_seconds"), int) else int_value(config, "gemini_probe_timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    log_path = first_log_path(packet_dir, attempt, "probe_logs", f"events-{label}-probe.log")
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=log_path,
    )
    if rc != 0:
        return rc
    return validate_probe_output(log_path, prompt, "Gemini")


def run_gemini_attempt(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], schema_path: Path, worktree: str) -> int:
    model = attempt.get("model")
    if not isinstance(model, str) or not model:
        raise SystemExit(f"{CONFIG_NAME} missing model for {label}")
    approval = string_value(config, "gemini_approval_mode") if isinstance(config.get("gemini_approval_mode"), str) else "yolo"
    command_binary = attempt.get("command_binary") if isinstance(attempt.get("command_binary"), str) else string_value(config, "gemini_command")
    command = [
        command_binary,
        "--model",
        model,
        "--approval-mode",
        approval,
        "--skip-trust",
        "-p",
        str(config.get("worker_prompt", "Follow the complete worker packet instructions provided on stdin.")),
    ]
    record_executed_command(attempt, command)
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    output_path = first_log_path(packet_dir, attempt, "event_logs", f"events-{label}.log")
    prompt_path = packet_dir / "prompt.md"
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=prompt_path.read_bytes(),
        stdout_path=output_path,
    )
    if rc != 0:
        return rc
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    return 0 if extract_status_json(packet_dir, schema_path, output_path, config, parse_report=parse_report) else 1


def run_copilot_probe_command(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], worktree: str) -> int:
    model = attempt.get("probe_model")
    if not isinstance(model, str) or not model:
        model = string_value(attempt, "model")
    effort = attempt.get("probe_reasoning_effort") or attempt.get("effort")
    if not isinstance(effort, str) or not effort:
        effort = string_value(config, "copilot_probe_reasoning_effort")
    timeout_seconds = int_value(attempt, "probe_timeout_seconds") if isinstance(attempt.get("probe_timeout_seconds"), int) else int_value(config, "copilot_probe_timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    prompt = str(attempt.get("probe_prompt", string_value(config, "copilot_probe_prompt")))
    probe_share = packet_dir / f"session-{label}-probe.md"
    logs = attempt.get("probe_logs", [])
    probe_path = packet_dir / str(logs[0]) if logs else packet_dir / f"events-{label}-probe.jsonl"
    version_probe = None
    for item in logs:
        if isinstance(item, str) and item.endswith("-version.log"):
            version_probe = packet_dir / item
            break
    if version_probe is None:
        version_probe = packet_dir / f"events-{label}-version.log"

    command_probe = [
        string_value(config, "copilot_command"),
        "copilot",
        "--",
        "-C",
        worktree,
        "--model",
        model,
        "--effort",
        effort,
        "--no-ask-user",
        "--no-custom-instructions",
        "--no-remote",
        "--disable-builtin-mcps",
        "--log-level",
        "error",
        "--output-format",
        "json",
        "--stream",
        "off",
        "--deny-tool",
        "shell,write,url,memory",
        "--share",
        str(probe_share),
        "-p",
        prompt,
    ]

    version_command = [string_value(config, "copilot_command"), "copilot", "--", "--version"]
    record_executed_command(attempt, version_command)
    version_rc = run_with_timeout(
        command=version_command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=version_probe,
    )
    if version_rc != 0:
        return version_rc
    record_executed_command(attempt, command_probe)
    rc = run_with_timeout(
        command=command_probe,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=probe_path,
    )
    if rc != 0:
        return rc
    return validate_probe_output(probe_path, prompt, "Copilot")


def run_copilot_attempt(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], schema_path: Path, worktree: str) -> int:
    model = attempt.get("model")
    if not isinstance(model, str) or not model:
        raise SystemExit(f"{CONFIG_NAME} missing model for {label}")
    effort = attempt.get("effort")
    if not isinstance(effort, str) or not effort:
        effort = string_value(config, "copilot_reasoning_effort")
    output_path = packet_dir / f"events-{label}.jsonl"
    session_path = packet_dir / f"session-{label}.md"
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    prompt_path = packet_dir / "prompt.md"
    command = [
        string_value(config, "copilot_command"),
        "copilot",
        "--",
        "-C",
        worktree,
        "--model",
        model,
        "--effort",
        effort,
        "--no-ask-user",
        "--no-custom-instructions",
        "--no-remote",
        "--disable-builtin-mcps",
        "--log-level",
        "error",
        "--output-format",
        "json",
        "--stream",
        "off",
        "--allow-tool=read,write,shell(pwd),shell(git:*),shell(python3:*),shell(pytest:*),shell(uv:*),shell(rg:*),shell(sed:*),shell(cat:*),shell(ls:*)",
        "--deny-tool=shell(git push),shell(git reset),shell(rm),memory,url",
        f"--share={session_path}",
        "-p",
        prompt_path.read_text(encoding="utf-8"),
    ]
    record_executed_command(attempt, command)
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=output_path,
    )
    if rc != 0:
        return rc
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    return 0 if extract_status_json(packet_dir, schema_path, output_path, config, parse_report=parse_report) else 1


def run_codex_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> int:
    role = string_value(config, "role")
    schema_path = packet_dir / schema_name
    output_path = packet_dir / output_name
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    model = attempt.get("model")
    if not isinstance(model, str) or not model:
        raise SystemExit(f"{CONFIG_NAME} attempt missing model")
    event_path = packet_dir / f"events-{label}.jsonl"
    prompt_path = packet_dir / "prompt.md"
    lean_flags: list[str] = []
    if role != "research-worker":
        if bool_value(attempt, "ignore_user_config"):
            lean_flags.append("--ignore-user-config")
        if bool_value(attempt, "ignore_rules"):
            lean_flags.append("--ignore-rules")
    command = [
        "codex",
        "exec",
        "--ephemeral",
        *lean_flags,
        "-m",
        model,
        "-C",
        worktree,
        "-s",
        string_value(config, "sandbox"),
        "--json",
        "--output-schema",
        schema_path.as_posix(),
        "-o",
        output_path.as_posix(),
        "-",
    ]
    if role == "research-worker":
        command = ["codex", "--search", "exec", "--ephemeral", "-m", model, "-C", worktree, "-s", string_value(config, "sandbox"), "--json", "--output-schema", schema_path.as_posix(), "-o", output_path.as_posix(), "-"]
    record_executed_command(attempt, command)
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=str(packet_dir),
        stdin_data=prompt_path.read_bytes(),
        stdout_path=event_path,
    )
    if rc != 0:
        return rc
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    if role == "worker" and not ensure_status_json(packet_dir, schema_path, output_path, event_path, config, parse_report=parse_report):
        return 1
    return 0


def render_runtime_args(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], prompt_text: str, worktree: str, schema_path: Path, output_path: Path) -> list[str]:
    args = attempt.get("run_args")
    if not isinstance(args, list):
        return []
    context = {
        "alias": str(attempt.get("alias", "")),
        "model": str(attempt.get("model", "")),
        "provider": str(attempt.get("provider_id", attempt.get("provider", ""))),
        "role": str(config.get("role", "")),
        "packet_id": str(config.get("packet_id", "")),
        "worktree": worktree,
        "packet_dir": packet_dir.as_posix(),
        "prompt": prompt_text,
        "prompt_file": (packet_dir / "prompt.md").as_posix(),
        "schema_file": schema_path.as_posix(),
        "output_file": output_path.as_posix(),
    }
    rendered: list[str] = []
    for item in args:
        if isinstance(item, str):
            rendered.append(item.format(**context))
    return rendered


def run_opencode_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> int:
    schema_path = packet_dir / schema_name
    output_path = packet_dir / output_name
    prompt_path = packet_dir / "prompt.md"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    binary = attempt.get("command_binary") if isinstance(attempt.get("command_binary"), str) else "opencode"
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved:
        print(f"opencode binary not found: {binary}", file=sys.stderr)
        return 127
    event_path = packet_dir / f"events-{label}.jsonl"
    args = render_runtime_args(
        attempt,
        packet_dir=packet_dir,
        config=config,
        prompt_text=prompt_text,
        worktree=worktree,
        schema_path=schema_path,
        output_path=output_path,
    )
    if not args:
        args = ["run", "--pure", "--format", "json", "--model", string_value(attempt, "model"), "--dir", worktree, prompt_text]
    command = [resolved, *args]
    record_executed_command(attempt, command)
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=str(packet_dir),
        stdin_data=None,
        stdout_path=event_path,
    )
    if rc != 0:
        return rc
    session_id = parse_session_id(event_path.read_text(encoding="utf-8", errors="replace") if event_path.exists() else "")
    if not session_id:
        print("opencode attempt did not emit a session id", file=sys.stderr)
        return 1
    assistant_text, readback = read_opencode_assistant_text(session_id, opencode_db_path())
    (packet_dir / f"events-{label}-assistant.log").write_text(assistant_text, encoding="utf-8")
    (packet_dir / f"events-{label}-opencode-readback.json").write_text(json.dumps(readback, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    if not ensure_status_json(packet_dir, schema_path, output_path, packet_dir / f"events-{label}-assistant.log", config, parse_report=parse_report):
        return 1
    return 0


def run_generic_cli_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> int:
    schema_path = packet_dir / schema_name
    output_path = packet_dir / output_name
    prompt_path = packet_dir / "prompt.md"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    binary = attempt.get("command_binary") if isinstance(attempt.get("command_binary"), str) else ""
    if not binary:
        print("generic CLI attempt missing command_binary", file=sys.stderr)
        return 127
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved:
        print(f"generic CLI binary not found: {binary}", file=sys.stderr)
        return 127
    event_path = packet_dir / f"events-{label}.log"
    args = render_runtime_args(
        attempt,
        packet_dir=packet_dir,
        config=config,
        prompt_text=prompt_text,
        worktree=worktree,
        schema_path=schema_path,
        output_path=output_path,
    )
    command = [resolved, *args]
    record_executed_command(attempt, command)
    rc = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=prompt_path.read_bytes(),
        stdout_path=event_path,
    )
    if rc != 0:
        return rc
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    if not ensure_status_json(packet_dir, schema_path, output_path, event_path, config, parse_report=parse_report):
        return 1
    return 0


def collect_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from collect_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from collect_strings(item)


def validate_type(value, expected_type: str):
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return True


def validate_instance(instance: Any, schema: dict[str, Any]) -> None:
    if schema.get("type") == "object" and not isinstance(instance, dict):
        raise ValueError("status is not a JSON object")
    required = schema.get("required", [])
    missing = [field for field in required if field not in instance]
    if missing:
        raise ValueError(f"status missing required fields: {', '.join(missing)}")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        extra = sorted(set(instance) - set(properties))
        if extra:
            raise ValueError(f"status has unsupported fields: {', '.join(extra)}")
    for field, field_schema in properties.items():
        if field not in instance:
            continue
        value = instance[field]
        if "const" in field_schema and value != field_schema["const"]:
            raise ValueError(f"{field} must be {field_schema['const']!r}")
        if "enum" in field_schema and value not in field_schema["enum"]:
            raise ValueError(f"{field} must be one of {field_schema['enum']!r}")
        if "type" in field_schema and not validate_type(value, field_schema["type"]):
            raise ValueError(f"{field} has wrong type")
        if isinstance(value, str):
            if "minLength" in field_schema and len(value) < field_schema["minLength"]:
                raise ValueError(f"{field} is too short")
            if "pattern" in field_schema and re.fullmatch(field_schema["pattern"], value) is None:
                raise ValueError(f"{field} does not match required pattern")
        if field_schema.get("type") == "array":
            if "minItems" in field_schema and len(value) < field_schema["minItems"]:
                raise ValueError(f"{field} contains too few items")
            item_schema = field_schema.get("items", {})
            item_type = item_schema.get("type")
            for item in value:
                if item_type and not validate_type(item, item_type):
                    raise ValueError(f"{field} contains item with wrong type")
                if "enum" in item_schema and item not in item_schema["enum"]:
                    raise ValueError(f"{field} contains item outside allowed enum")
                if isinstance(item, str):
                    if "minLength" in item_schema and len(item) < item_schema["minLength"]:
                        raise ValueError(f"{field} contains item that is too short")
                    if "pattern" in item_schema and re.fullmatch(item_schema["pattern"], item) is None:
                        raise ValueError(f"{field} contains item that does not match required pattern")


def validate_packet_post_constraints(data: dict[str, Any], config: dict[str, Any]) -> None:
    if config.get("role") != "worker":
        return
    expected_ladder = config.get("selected_ladder")
    if isinstance(expected_ladder, list) and data.get("selected_ladder") != expected_ladder:
        raise ValueError("selected_ladder must match launch-config selected_ladder exactly")


def output_matches_schema(schema_path: Path, output_path: Path, config: dict[str, Any]) -> bool:
    if not output_path.exists():
        return False
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        data = json.loads(output_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "success":
            data["status"] = "pass"
            output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        validate_instance(data, schema)
        validate_packet_post_constraints(data, config)
    except Exception:
        return False
    return True


def ensure_status_json(
    packet_dir: Path,
    schema_path: Path,
    output_path: Path,
    event_path: Path,
    config: dict[str, Any],
    parse_report: dict[str, Any] | None = None,
) -> bool:
    parse_report = parse_report if parse_report is not None else {}
    parse_report.clear()
    parse_report["failure_subclass"] = None
    parse_report["provider_error_code"] = None
    parse_report["messages"] = []
    if output_matches_schema(schema_path, output_path, config):
        parse_report["status"] = "schema_success"
        return True
    if output_path.exists() and output_path.stat().st_size > 0:
        raw_copy = packet_dir / f"{output_path.name}.raw"
        if not raw_copy.exists():
            shutil.copyfile(output_path, raw_copy)
    parse_report["status"] = "schema_failure"
    return extract_status_json(packet_dir, schema_path, [output_path, event_path], config, parse_report=parse_report)


def extract_status_json(
    packet_dir: Path,
    schema_path: Path,
    raw_path: Path | list[Path],
    config: dict[str, Any],
    parse_report: dict[str, Any] | None = None,
) -> bool:
    parse_report = parse_report if parse_report is not None else {}
    parse_report["failure_subclass"] = parse_report.get("failure_subclass")
    parse_report.setdefault("provider_error_code", None)
    parse_report["messages"] = parse_report.get("messages", [])
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    marker_block = string_value(config.get("status_markers", {}), "begin") if isinstance(config.get("status_markers"), dict) else WORKER_STATUS_BEGIN
    marker_end = string_value(config.get("status_markers", {}), "end") if isinstance(config.get("status_markers"), dict) else WORKER_STATUS_END
    output_path = packet_dir / string_value(config, "output_name")
    raw_paths = raw_path if isinstance(raw_path, list) else [raw_path]
    sources: list[tuple[str, str]] = []
    evidence_lines: list[str] = []
    for path in raw_paths:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            sources.append((f"raw output {path.name}", text))
            evidence_lines.extend(text.splitlines())
    jsonl_parts: list[str] = []
    for _source_name, source_text in sources:
        for line in source_text.splitlines():
            try:
                data = json.loads(line)
            except Exception:
                continue
            jsonl_parts.extend(collect_strings(data))
    for line in output_path.read_text(encoding="utf-8", errors="replace").splitlines() if output_path.exists() else []:
        try:
            data = json.loads(line)
        except Exception:
            continue
        jsonl_parts.extend(collect_strings(data))
    if jsonl_parts:
        sources.append(("decoded JSONL strings", "\n".join(jsonl_parts)))
    for source_name, source_text in sources:
        evidence_lines.extend(source_text.splitlines())
    parse_report["provider_error_code"] = detect_provider_error_code(evidence_lines)

    source_errors: list[str] = []
    status_candidates: list[tuple[dict[str, Any], str]] = []
    for source_name, source_text in sources:
        begin_count = source_text.count(marker_block)
        end_count = source_text.count(marker_end)
        if begin_count != 1 or end_count != 1:
            source_errors.append(
                f"{source_name}: expected exactly one {marker_block} and one {marker_end} marker; "
                f"found {begin_count} begin marker(s) and {end_count} end marker(s)."
            )
            continue
        start = source_text.index(marker_block) + len(marker_block)
        finish = source_text.index(marker_end)
        if finish <= start:
            source_errors.append(f"{source_name}: worker status end marker appears before begin marker.")
            continue
        candidate = source_text[start:finish].strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and data.get("status") == "success":
                data["status"] = "pass"
            status_candidates.append((data, f"{source_name}: marker"))
            continue
        except Exception as exc:
            source_errors.append(f"{source_name}: invalid marked worker status JSON: {exc}")
            continue

    for source_name, source_text in sources:
        for data in status_objects_from_text(source_text):
            status_candidates.append((data, f"{source_name}: json"))

    source_validation_errors: list[str] = []
    for data, source_name in status_candidates:
        try:
            if isinstance(data, dict) and data.get("status") == "success":
                data["status"] = "pass"
            validate_instance(data, schema)
            validate_packet_post_constraints(data, config)
        except Exception as exc:
            source_validation_errors.append(f"{source_name}: invalid status object: {exc}")
            continue
        output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        parse_report["failure_subclass"] = None
        parse_report["status"] = "recovered"
        parse_report["messages"] = source_errors + source_validation_errors
        return True

    for message in source_errors:
        print(message, file=sys.stderr)
    parse_report["messages"] = source_errors + source_validation_errors
    if any("marker" in str(item) for item in parse_report["messages"]):
        parse_report["failure_subclass"] = "marker_protocol"
    elif source_validation_errors:
        parse_report["failure_subclass"] = "schema_validation_failure"
    else:
        parse_report["failure_subclass"] = "parser_failure"
    return False


def run_worker_attempt(
    *,
    packet_dir: Path,
    config: dict[str, Any],
    attempt: dict[str, Any],
    attempt_index: int,
    schema_name: str,
    output_name: str,
    worktree: str,
) -> tuple[int, Path | None]:
    label = event_label(attempt, f"attempt-{attempt_index + 1}")
    provider = attempt.get("harness_kind") or attempt.get("provider")
    if provider == "gemini":
        if run_gemini_probe_command(attempt, label=label, packet_dir=packet_dir, config=config, worktree=worktree) != 0:
            return 1, packet_dir / f"events-{label}.jsonl"
        return run_gemini_attempt(
            attempt,
            label=label,
            packet_dir=packet_dir,
            config=config,
            schema_path=packet_dir / schema_name,
            worktree=worktree,
        ), packet_dir / f"events-{label}.jsonl"
    if provider == "copilot":
        if run_copilot_probe_command(attempt, label=label, packet_dir=packet_dir, config=config, worktree=worktree) != 0:
            return 1, packet_dir / f"events-{label}.jsonl"
        return run_copilot_attempt(
            attempt,
            label=label,
            packet_dir=packet_dir,
            config=config,
            schema_path=packet_dir / schema_name,
            worktree=worktree,
        ), packet_dir / f"events-{label}.jsonl"
    if provider == "codex":
        return run_codex_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        ), packet_dir / f"events-{label}.jsonl"
    if provider == "opencode":
        return run_opencode_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        ), packet_dir / f"events-{label}.jsonl"
    if provider == "generic-cli":
        return run_generic_cli_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        ), packet_dir / f"events-{label}.log"
    raise SystemExit(f"{CONFIG_NAME} unsupported worker provider: {provider}")


def run_packet(packet_dir: Path) -> int:
    config = read_json(packet_dir / CONFIG_NAME)
    if config.get("schema_version") != 1:
        raise SystemExit(f"{CONFIG_NAME} schema_version must be 1")
    role = string_value(config, "role")
    if role not in {"research-worker", "reviewer", "worker"}:
        raise SystemExit(f"unsupported compact runner role: {role}")
    output_name = string_value(config, "output_name")
    output_path = packet_dir / output_name
    worktree = string_value(config, "worktree")
    attempts = list_value(config, "attempts")
    schema_name = string_value(config, "schema_name")
    check_worktree(worktree)
    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        remove_if_exists(packet_dir / debug_name)
    clean_outputs(packet_dir, output_name, attempts)

    if role == "worker":
        baseline_changed_files = changed_file_fingerprints(worktree)
        for index, attempt in enumerate(attempts):
            write_launcher_state(packet_dir, config, state="active", attempt=attempt, attempt_index=index)
            rc, event_path = run_worker_attempt(
                packet_dir=packet_dir,
                config=config,
                attempt=attempt,
                attempt_index=index,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
            )
            output_nonempty = output_path.exists() and output_path.stat().st_size > 0
            cleanup = cleanup_generated_artifacts(worktree, attempt_index=index, attempt=attempt)
            record_generated_artifact_cleanup(packet_dir, attempt, cleanup)
            packet_changed_files = packet_delta_changed_files(worktree, baseline_changed_files)
            dirty = bool(packet_changed_files)
            state = classify_attempt_state(rc, output_nonempty=output_nonempty, dirty=dirty)
            parse_report = attempt.get("_parse_report")
            if not isinstance(parse_report, dict):
                parse_report = {}
                attempt["_parse_report"] = parse_report
            parse_messages = _event_parse_messages(parse_report)
            failure_message = "; ".join(parse_messages[:2]) if parse_messages else ""
            _finalize_attempt_observation(
                attempt,
                parse_report=parse_report,
                output_path=output_path,
                event_path=event_path,
                attempt_state=state,
                returncode=rc,
                dirty=dirty,
                output_nonempty=output_nonempty,
                message=failure_message,
            )
            write_launcher_state(
                packet_dir,
                config,
                state=state,
                attempt=attempt,
                attempt_index=index,
                returncode=rc,
                dirty=dirty,
                output_nonempty=output_nonempty,
            )
            if rc == 0:
                ownership_violations = worker_ownership_violations(config, packet_changed_files)
                if ownership_violations:
                    message = "worker changed files outside owned paths: " + ", ".join(ownership_violations)
                    attempt["failure_class"] = "ownership"
                    attempt["failure_subclass"] = "owned_path_violation"
                    attempt["owned_path_violation"] = ownership_violations
                    parse_report["failure_subclass"] = "owned_path_violation"
                    (packet_dir / "ownership.blocked.txt").write_text(message + "\n", encoding="utf-8")
                    write_terminal(packet_dir, config, message, changed_files=packet_changed_files)
                    write_launcher_state(
                        packet_dir,
                        config,
                        state="blocked",
                        attempt=attempt,
                        attempt_index=index,
                        returncode=rc,
                        dirty=True,
                        output_nonempty=output_nonempty,
                        message=message,
                    )
                    write_telemetry(packet_dir, config)
                    return 2
                write_telemetry(packet_dir, config)
                return 0
            if dirty:
                label = event_label(attempt, f"attempt-{index + 1}")
                suffix = "refusing fallback in same worktree." if index < len(attempts) - 1 else "no fallback remains."
                message = f"{label} failed after leaving dirty worktree; {suffix}"
                if failure_message:
                    message = f"{message} details: {failure_message}"
                (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
                write_terminal(packet_dir, config, message, changed_files=packet_changed_files)
                write_launcher_state(
                    packet_dir,
                    config,
                    state="blocked",
                    attempt=attempt,
                    attempt_index=index,
                    returncode=rc,
                    dirty=True,
                    output_nonempty=output_nonempty,
                    message=message,
                )
                write_telemetry(packet_dir, config)
                return 2
            if _parse_failure_detected(parse_report):
                if index < len(attempts) - 1:
                    clear_invalid_output_for_fallback(output_path)
                    continue
            if output_nonempty:
                message = string_value(config, "terminal_message")
                if failure_message:
                    message = f"{message}: {failure_message}"
                write_terminal(packet_dir, config, message)
                write_launcher_state(
                    packet_dir,
                    config,
                    state="blocked",
                    attempt=attempt,
                    attempt_index=index,
                    returncode=rc,
                    dirty=False,
                    output_nonempty=output_nonempty,
                    message=message,
                )
                write_telemetry(packet_dir, config)
                return 1
        packet_changed_files = packet_delta_changed_files(worktree, baseline_changed_files)
        if packet_changed_files:
            message = "worker failed after leaving dirty worktree; no fallback remains."
            (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
            write_terminal(packet_dir, config, message, changed_files=packet_changed_files)
            write_launcher_state(packet_dir, config, state="blocked", dirty=True, message=message)
            write_telemetry(packet_dir, config)
            return 2
        message = string_value(config, "terminal_message")
        parse_report = attempts[-1].get("_parse_report") if attempts else {}
        if isinstance(parse_report, dict):
            parse_messages = _event_parse_messages(parse_report)
            if parse_messages:
                message = f"{message}: {'; '.join(parse_messages[:2])}"
        write_terminal(packet_dir, config, message)
        write_launcher_state(packet_dir, config, state="blocked", dirty=False, message=message)
        write_telemetry(packet_dir, config)
        return 1

    for index, attempt in enumerate(attempts):
        _label = event_label(attempt, f"attempt-{index + 1}")
        provider = attempt.get("harness_kind") or attempt.get("provider")
        write_launcher_state(packet_dir, config, state="active", attempt=attempt, attempt_index=index)
        event_path: Path | None = None
        if provider == "codex":
            rc = run_codex_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
            event_path = packet_dir / f"events-{_label}.jsonl"
        elif provider == "opencode":
            rc = run_opencode_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
            event_path = packet_dir / f"events-{_label}.jsonl"
        elif provider == "generic-cli":
            rc = run_generic_cli_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
            event_path = packet_dir / f"events-{_label}.log"
        elif provider == "gemini":
            rc = run_gemini_attempt(
                attempt,
                label=_label,
                packet_dir=packet_dir,
                config=config,
                schema_path=packet_dir / schema_name,
                worktree=worktree,
            )
            event_path = packet_dir / f"events-{_label}.jsonl"
        else:
            raise SystemExit(f"{CONFIG_NAME} unsupported {role} provider: {provider}")
        output_nonempty = output_path.exists() and output_path.stat().st_size > 0
        state = classify_attempt_state(rc, output_nonempty=output_nonempty, dirty=False)
        parse_report = attempt.get("_parse_report")
        if not isinstance(parse_report, dict):
            parse_report = {}
            attempt["_parse_report"] = parse_report
        parse_messages = _event_parse_messages(parse_report)
        failure_message = "; ".join(parse_messages[:2]) if parse_messages else ""
        _finalize_attempt_observation(
            attempt,
            parse_report=parse_report,
            output_path=output_path,
            event_path=event_path,
            attempt_state=state,
            returncode=rc,
            dirty=False,
            output_nonempty=output_nonempty,
            message=failure_message,
        )
        write_launcher_state(
            packet_dir,
            config,
            state=state,
            attempt=attempt,
            attempt_index=index,
            returncode=rc,
            dirty=False,
            output_nonempty=output_nonempty,
        )
        if rc == 0:
            write_telemetry(packet_dir, config)
            return 0
        if output_nonempty:
            if _parse_failure_detected(parse_report) and index < len(attempts) - 1:
                clear_invalid_output_for_fallback(output_path)
                continue
            message = string_value(config, "terminal_message")
            if failure_message:
                message = f"{message}: {failure_message}"
            write_terminal(packet_dir, config, message)
            write_launcher_state(
                packet_dir,
                config,
                state="blocked",
                attempt=attempt,
                attempt_index=index,
                returncode=rc,
                dirty=False,
                output_nonempty=output_nonempty,
                message=message,
            )
            write_telemetry(packet_dir, config)
            return 1
        if _parse_failure_detected(parse_report) and index < len(attempts) - 1:
            clear_invalid_output_for_fallback(output_path)
            continue

    message = string_value(config, "terminal_message")
    parse_report = attempts[-1].get("_parse_report") if attempts else {}
    if isinstance(parse_report, dict):
        parse_messages = _event_parse_messages(parse_report)
        if parse_messages:
            message = f"{message}: {parse_messages[-1]}"
    write_terminal(packet_dir, config, message)
    write_launcher_state(packet_dir, config, state="blocked", dirty=False, message=message)
    write_telemetry(packet_dir, config)
    return 1

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", required=True)
    args = parser.parse_args()
    packet_dir = Path(args.packet_dir).resolve()
    if not packet_dir.is_dir():
        raise SystemExit(f"--packet-dir must be an existing directory: {packet_dir}")
    config = read_json(packet_dir / CONFIG_NAME)
    started = time.monotonic()
    append_debug_event(packet_dir, config, {"phase": "packet", "event": "start"})
    try:
        rc = run_packet(packet_dir)
    except BaseException:
        append_debug_event(
            packet_dir,
            config,
            {
                "phase": "packet",
                "event": "end",
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "status": "error",
            },
        )
        raise
    append_debug_event(
        packet_dir,
        config,
        {
            "phase": "packet",
            "event": "end",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": "ok" if rc == 0 else "nonzero",
            "exit_status": rc,
        },
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
