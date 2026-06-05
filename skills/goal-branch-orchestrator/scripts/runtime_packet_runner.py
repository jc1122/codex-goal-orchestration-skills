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
ROUTE_HEALTH_NAME = "route-health.json"
ROUTE_DEGRADE_EMPTY_OUTPUT_THRESHOLD = 2
OPENCODE_WAL_FAILURE_SIGNATURE = "PRAGMA journal_mode = WAL"
OPENCODE_SQLITE_WAL_SUBCLASS = "opencode_sqlite_wal_failure"
OPENCODE_EMPTY_OUTPUT_SUBCLASS = "opencode_empty_assistant_output"
OPENCODE_PROVIDER_API_ERROR_SUBCLASS = "opencode_provider_api_error"
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
PACKET_CACHE_PATH_PARTS = (
    ".runtime-cache",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "xdg-cache",
)
KNOWN_PACKET_CACHE_FILES = (
    "unleash-repo-schema-v1-codeium-language-server.json",
)
KNOWN_PACKET_CACHE_ROOT_NAMES = (
    ".runtime-cache",
    ".cache",
    "xdg-cache",
    ".pytest_cache",
    ".ruff_cache",
)
MAX_UNTRACKED_SALVAGE_FILE_BYTES = 1024 * 1024
MAX_UNTRACKED_SALVAGE_TOTAL_BYTES = 5 * 1024 * 1024
KNOWN_PACKET_CACHE_RELATIVE = (
    "tmp",
    "lock",
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


def scheduler_closed_pass_for_packet(scheduler_path: Path, packet_id: str) -> bool:
    if not scheduler_path.exists():
        return False
    data = read_json(scheduler_path)
    events = data.get("events")
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


def guard_scheduler_closed_pass(packet_dir: Path, config: dict[str, Any]) -> None:
    guard = config.get("scheduler_guard")
    if not isinstance(guard, dict):
        return
    scheduler_path_value = guard.get("scheduler_path")
    packet_id = guard.get("packet_id") or config.get("packet_id")
    if not isinstance(scheduler_path_value, str) or not scheduler_path_value.strip():
        return
    if not isinstance(packet_id, str) or not packet_id.strip():
        return
    scheduler_path = Path(scheduler_path_value)
    if scheduler_closed_pass_for_packet(scheduler_path, packet_id):
        raise SystemExit(
            f"refusing to run {packet_dir}: scheduler already closed {packet_id} as pass; create a new packet id for retries"
        )


def bundle_root_for_packet_dir(packet_dir: Path) -> Path | None:
    if packet_dir.parent.name in {"workers", "reviewers", "research"}:
        return packet_dir.parent.parent
    return None


def route_health_path(packet_dir: Path) -> Path | None:
    bundle_root = bundle_root_for_packet_dir(packet_dir)
    if bundle_root is None:
        return None
    return bundle_root / ROUTE_HEALTH_NAME


def read_route_health(packet_dir: Path) -> dict[str, Any]:
    path = route_health_path(packet_dir)
    if path is None or not path.exists():
        return {"schema_version": 1, "routes": {}}
    data = read_json(path)
    routes = data.get("routes")
    if not isinstance(routes, dict):
        data["routes"] = {}
    return data


def write_route_health(packet_dir: Path, data: dict[str, Any]) -> None:
    path = route_health_path(packet_dir)
    if path is None:
        return
    write_json(path, data)


def route_health_key(attempt: dict[str, Any]) -> str:
    alias = attempt.get("alias")
    if isinstance(alias, str) and alias.strip():
        return alias
    provider = attempt.get("provider") or attempt.get("harness_kind")
    model = attempt.get("model")
    return f"{provider or 'unknown'}:{model or 'unknown'}"


def degraded_route_health(packet_dir: Path, attempt: dict[str, Any]) -> dict[str, Any] | None:
    data = read_route_health(packet_dir)
    route = data.get("routes", {}).get(route_health_key(attempt))
    if isinstance(route, dict) and route.get("degraded") is True:
        return route
    return None


def record_bundle_route_failure(packet_dir: Path, attempt: dict[str, Any]) -> None:
    if attempt.get("failure_subclass") != OPENCODE_EMPTY_OUTPUT_SUBCLASS:
        return
    key = route_health_key(attempt)
    data = read_route_health(packet_dir)
    routes = data.setdefault("routes", {})
    if not isinstance(routes, dict):
        routes = {}
        data["routes"] = routes
    route = routes.setdefault(key, {})
    if not isinstance(route, dict):
        route = {}
        routes[key] = route
    route["alias"] = attempt.get("alias")
    route["provider"] = attempt.get("provider") or attempt.get("harness_kind")
    route["model"] = attempt.get("model")
    failures = route.setdefault("failures", {})
    if not isinstance(failures, dict):
        failures = {}
        route["failures"] = failures
    count = int(failures.get(OPENCODE_EMPTY_OUTPUT_SUBCLASS, 0) or 0) + 1
    failures[OPENCODE_EMPTY_OUTPUT_SUBCLASS] = count
    if count >= ROUTE_DEGRADE_EMPTY_OUTPUT_THRESHOLD:
        route["degraded"] = True
        route["degraded_reason"] = OPENCODE_EMPTY_OUTPUT_SUBCLASS
        route["degraded_after_count"] = count
    write_route_health(packet_dir, data)


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


def packet_cache_retention_requested(config: dict[str, Any]) -> bool:
    return (
        isinstance(config.get("debug_events_name"), str)
        and bool(config.get("debug_events_name").strip())
        and isinstance(config.get("telemetry_debug_name"), str)
        and bool(config.get("telemetry_debug_name").strip())
    )


def _is_packet_cache_candidate(path: Path) -> bool:
    if not path.exists():
        return False
    parts = set(path.parts)
    if parts.intersection(PACKET_CACHE_PATH_PARTS):
        return True
    if path.name in KNOWN_PACKET_CACHE_FILES:
        return True
    parent = path.parent.name if len(path.parts) > 1 else ""
    return parent in {"tmp", "lock"} and path.suffix == ".json" and path.name in KNOWN_PACKET_CACHE_FILES


def packet_runtime_cache_candidates(packet_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in sorted(packet_dir.rglob("*")):
        if candidate == packet_dir:
            continue
        if not candidate.is_file() and not candidate.is_dir():
            continue
        if _is_packet_cache_candidate(candidate):
            candidates.append(candidate)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        root = candidate
        rel_candidate = candidate.relative_to(packet_dir)
        for index, part in enumerate(rel_candidate.parts):
            if part in KNOWN_PACKET_CACHE_ROOT_NAMES:
                root = packet_dir / Path(*rel_candidate.parts[: index + 1])
                break
        if root == candidate and rel_candidate.parts:
            for index in range(len(rel_candidate.parts) - 1, 0, -1):
                part = rel_candidate.parts[index]
                parent = rel_candidate.parts[index - 1]
                if part in KNOWN_PACKET_CACHE_RELATIVE and parent in KNOWN_PACKET_CACHE_ROOT_NAMES:
                    root = packet_dir / Path(*rel_candidate.parts[:index + 1])
                    break
        if root.is_relative_to(packet_dir):
            if root in seen:
                continue
            seen.add(root)
            deduped.append(root)
    return sorted(deduped)


def cleanup_packet_runtime_cache(packet_dir: Path, *, keep: bool = False) -> dict[str, Any]:
    candidates = [path.relative_to(packet_dir).as_posix() for path in packet_runtime_cache_candidates(packet_dir)]
    if not candidates:
        return {
            "status": "skipped",
            "retention_requested": keep,
            "candidates": [],
            "removed": [],
            "failed": [],
            "candidates_count": 0,
            "removed_count": 0,
            "failed_count": 0,
        }
    if keep:
        return {
            "status": "kept_for_debug",
            "retention_requested": True,
            "candidates": candidates,
            "removed": [],
            "failed": [],
            "candidates_count": len(candidates),
            "removed_count": 0,
            "failed_count": 0,
        }
    removed: list[str] = []
    failed: list[str] = []
    for value in candidates:
        path = packet_dir / value
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{value}: {exc}")
        else:
            removed.append(value)
    return {
        "status": _cleanup_status(len(candidates), len(failed), len(removed)),
        "retention_requested": False,
        "candidates": candidates,
        "removed": removed,
        "failed": failed,
        "candidates_count": len(candidates),
        "removed_count": len(removed),
        "failed_count": len(failed),
    }


def clean_outputs(packet_dir: Path, output_name: str, attempts: list[dict[str, Any]], config: dict[str, Any] | None = None) -> None:
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
    if config is not None:
        cache_summary = cleanup_packet_runtime_cache(
            packet_dir,
            keep=packet_cache_retention_requested(config),
        )
        if cache_summary["status"] != "skipped" or (packet_dir / GENERATED_CLEANUP_NAME).exists():
            path = packet_dir / GENERATED_CLEANUP_NAME
            data = read_optional_json(path)
            data["packet_runtime_cache"] = cache_summary
            write_json(path, data)


def cleanup_runtime_cache_evidence(packet_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    summary = cleanup_packet_runtime_cache(
        packet_dir,
        keep=packet_cache_retention_requested(config),
    )
    if summary["status"] != "skipped":
        path = packet_dir / GENERATED_CLEANUP_NAME
        data = read_optional_json(path)
        data["packet_runtime_cache"] = summary
        write_json(path, data)
    return summary


def attempt_elapsed_ms(attempt: dict[str, Any]) -> int | None:
    execution = attempt.get("execution")
    if isinstance(execution, dict):
        value = execution.get("elapsed_ms")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def opencode_db_path(xdg_data_home: Path | None = None) -> Path:
    if xdg_data_home is not None:
        return xdg_data_home / "opencode" / "opencode.db"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def opencode_packet_env(packet_dir: Path) -> tuple[dict[str, str], Path]:
    root = packet_dir / ".runtime-cache" / "opencode"
    xdg_data = root / "xdg-data"
    xdg_state = root / "xdg-state"
    xdg_cache = root / "xdg-cache"
    for path in [xdg_data, xdg_state, xdg_cache]:
        path.mkdir(parents=True, exist_ok=True)
    return {
        "XDG_DATA_HOME": xdg_data.as_posix(),
        "XDG_STATE_HOME": xdg_state.as_posix(),
        "XDG_CACHE_HOME": xdg_cache.as_posix(),
    }, opencode_db_path(xdg_data)


def opencode_wal_failure_report(execution: dict[str, Any], packet_dir: Path) -> dict[str, Any] | None:
    stderr_name = execution.get("stderr_path")
    if not isinstance(stderr_name, str) or not stderr_name:
        return None
    stderr_path = packet_dir / stderr_name
    if not stderr_path.exists():
        return None
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    if OPENCODE_WAL_FAILURE_SIGNATURE not in stderr_text:
        return None
    return {
        "status": "harness_failure",
        "failure_subclass": OPENCODE_SQLITE_WAL_SUBCLASS,
        "provider_error_code": "OPENCODE_SQLITE_WAL",
        "messages": [
            "Opencode failed before model execution while initializing its sqlite session database: "
            + OPENCODE_WAL_FAILURE_SIGNATURE,
            f"stderr: {stderr_name}",
        ],
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


def opencode_error_event_report(event_path: Path) -> dict[str, Any] | None:
    if not event_path.exists():
        return None
    for line in event_path.read_text(encoding="utf-8", errors="replace").splitlines():
        event = safe_parse_json_dict(line)
        if not event or event.get("type") != "error":
            continue
        error = event.get("error")
        if not isinstance(error, dict):
            continue
        data = error.get("data")
        data = data if isinstance(data, dict) else {}
        metadata = data.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        message = data.get("message") or error.get("message")
        message = message if isinstance(message, str) and message.strip() else "opencode provider API error"
        status_code = data.get("statusCode") or data.get("status_code") or data.get("status")
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            status_code = None
        provider_error_code = data.get("code") or error.get("code")
        if not isinstance(provider_error_code, str) or not provider_error_code.strip():
            provider_error_code = f"OPENCODE_PROVIDER_HTTP_{status_code}" if status_code is not None else "OPENCODE_PROVIDER_API_ERROR"
        report: dict[str, Any] = {
            "status": "failed",
            "failure_class": "provider_api_error",
            "failure_subclass": OPENCODE_PROVIDER_API_ERROR_SUBCLASS,
            "provider_error_code": provider_error_code,
            "provider_error_name": error.get("name") if isinstance(error.get("name"), str) else None,
            "provider_error_message": message,
            "provider_http_status": status_code,
            "provider_error_url": metadata.get("url") if isinstance(metadata.get("url"), str) else None,
            "provider_response_body": data.get("responseBody") if isinstance(data.get("responseBody"), str) else None,
            "messages": [message],
        }
        return {key: value for key, value in report.items() if value is not None}
    return None


def _command_from_parts(parts: list[str] | tuple[str, ...]) -> str:
    return shlex.join(str(item) for item in parts)


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


def opencode_empty_output_report(assistant_text: str, readback: dict[str, Any]) -> dict[str, Any] | None:
    if assistant_text.strip() or readback.get("status") != "pass":
        return None
    tokens = readback.get("tokens")
    if not isinstance(tokens, dict):
        return None
    token_values: list[int] = []
    for key in ("input", "output", "reasoning", "cache_read", "cache_write"):
        value = tokens.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            token_values.append(value)
        elif value is not None:
            return None
    if not token_values or any(value != 0 for value in token_values):
        return None
    message = "opencode readback returned empty assistant output with zero token usage"
    return {
        "status": "failed",
        "failure_class": "schema_or_output_readback",
        "failure_subclass": OPENCODE_EMPTY_OUTPUT_SUBCLASS,
        "provider_error_code": "OPENCODE_NO_ASSISTANT_OUTPUT",
        "messages": [message],
    }


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
    if attempt_state == "pass":
        return None
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
        if OPENCODE_WAL_FAILURE_SIGNATURE.lower() in lowered:
            return OPENCODE_SQLITE_WAL_SUBCLASS
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
    attempt["route_health"] = route_health
    if attempt_state != "pass":
        provider_error_code = parse_report.get("provider_error_code")
        if not provider_error_code:
            provider_error_code = detect_provider_error_code(lines)
        provider_error_code = str(provider_error_code) if isinstance(provider_error_code, str) and provider_error_code else None
        parse_report["provider_error_code"] = provider_error_code
        if provider_error_code:
            attempt["provider_error_code"] = provider_error_code
    else:
        parse_report["provider_error_code"] = None
        attempt.pop("provider_error_code", None)
    failure_subclass = attempt_failure_subclass(parse_report, route_health, attempt_state, message=message or None)
    if attempt_state != "pass" and failure_subclass is not None:
        parse_report["failure_subclass"] = parse_report.get("failure_subclass") or failure_subclass
        attempt["failure_subclass"] = parse_report["failure_subclass"]
    else:
        attempt.pop("failure_subclass", None)
    for key in (
        "provider_error_name",
        "provider_error_message",
        "provider_http_status",
        "provider_error_url",
        "provider_response_body",
    ):
        if key in parse_report:
            attempt[key] = parse_report[key]
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
    status_parse_messages = parse_report.get("messages")
    status_parse = {
        "status": parse_report.get("status"),
        "failure_subclass": parse_report.get("failure_subclass"),
        "provider_error_code": parse_report.get("provider_error_code"),
        "messages": status_parse_messages[:3] if isinstance(status_parse_messages, list) else [],
        "message_count": len(status_parse_messages) if isinstance(status_parse_messages, list) else 0,
    }
    for key in (
        "failure_class",
        "provider_error_name",
        "provider_error_message",
        "provider_http_status",
        "provider_error_url",
        "provider_response_body",
    ):
        if key in parse_report:
            status_parse[key] = parse_report[key]
    if message:
        status_parse["final_message"] = message
    attempt["status_parse"] = status_parse


def _attempt_stop_reason(attempt: dict[str, Any], attempt_state: str) -> str | None:
    if attempt_state == "pass":
        return None
    execution = attempt.get("execution", {})
    if isinstance(execution, dict):
        timed_out = execution.get("timed_out")
        if timed_out is True:
            return "timeout"
    route_health = attempt.get("route_health")
    if isinstance(route_health, dict) and int(route_health.get("transport_disconnect_count", 0) or 0) > 0:
        return "transport_disconnect"
    if attempt.get("failure_subclass") == "transport_disconnect":
        return "transport_disconnect"
    if attempt.get("failure_subclass") == OPENCODE_SQLITE_WAL_SUBCLASS:
        return "harness_unavailable"
    if attempt.get("failure_class") == "schema_or_output_readback":
        return "schema_readback_failure"
    if attempt.get("failure_subclass") in {"marker_protocol", "schema_validation_failure", "parser_failure"}:
        return "schema_readback_failure"
    if attempt_state == "timeout":
        return "timeout"
    if attempt.get("failure_subclass") == "owned_path_violation":
        return "owned_path_violation"
    return None


def _parse_failure_detected(parse_report: dict[str, Any]) -> bool:
    failure_subclass = parse_report.get("failure_subclass")
    return failure_subclass in {
        "marker_protocol",
        "schema_validation_failure",
        "parser_failure",
        OPENCODE_SQLITE_WAL_SUBCLASS,
        OPENCODE_EMPTY_OUTPUT_SUBCLASS,
        OPENCODE_PROVIDER_API_ERROR_SUBCLASS,
    }


def record_executed_command(attempt: dict[str, Any], command: list[str]) -> None:
    command_text = _command_from_parts(command)
    attempt["executed_command"] = command_text
    executed_commands = attempt.get("executed_commands")
    if not isinstance(executed_commands, list):
        executed_commands = []
        attempt["executed_commands"] = executed_commands
    if command_text and command_text not in executed_commands:
        executed_commands.append(command_text)


def append_attempt_execution(attempt: dict[str, Any], execution: dict[str, Any], *, phase: str = "runtime") -> None:
    attempt["called"] = True
    attempt["execution"] = {
        "phase": phase,
        **{
            key: execution.get(key)
            for key in (
                "returncode",
                "elapsed_ms",
                "timed_out",
                "stdout_bytes",
                "stderr_bytes",
                "command",
                "command_parts",
                "stderr_path",
                "started_at",
                "completed_at",
            )
        },
    }
    execution_history = attempt.get("execution_history")
    if not isinstance(execution_history, list):
        execution_history = []
        attempt["execution_history"] = execution_history
    execution_history.append(attempt["execution"].copy())


def command_lines_from_attempts(attempts: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for attempt in attempts:
        if attempt.get("called") is not True:
            continue
        value = attempt.get("executed_commands")
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item and item not in seen:
                    seen.add(item)
                    commands.append(item)
        elif isinstance(attempt.get("executed_command"), str) and attempt.get("executed_command").strip():
            item = attempt.get("executed_command").strip()
            if item not in seen:
                seen.add(item)
                commands.append(item)
    return commands


def terminal_command_lines(config: dict[str, Any], commands_run: list[str] | None = None) -> list[str]:
    if commands_run:
        return [item for item in commands_run if isinstance(item, str) and item.strip()]
    derived = command_lines_from_attempts(list_value(config, "attempts"))
    if derived:
        return derived
    return []


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
    elapsed_ms: int | None = None,
    stop_reason: str | None = None,
    salvage_context: dict[str, Any] | None = None,
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
            "execution",
            "execution_history",
            "provenance_level",
            "status_parse",
            "stop_reason",
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
    if elapsed_ms is not None:
        event["elapsed_ms"] = elapsed_ms
    if stop_reason:
        event["stop_reason"] = stop_reason
    if salvage_context:
        event["dirty_salvage_context"] = salvage_context
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
    failure_class = parse_report.get("failure_class")
    if isinstance(failure_class, str) and failure_class.strip():
        return failure_class.strip()
    failure_subclass = parse_report.get("failure_subclass")
    if failure_subclass:
        if failure_subclass == "transport_disconnect":
            return "transport_disconnect"
        if failure_subclass == OPENCODE_SQLITE_WAL_SUBCLASS:
            return "harness_unavailable"
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


def read_only_attempt(command: list[str], role: str) -> bool:
    if role in {"reviewer", "research-worker"}:
        return True
    for index, item in enumerate(command):
        if item == "-s" and index + 1 < len(command) and command[index + 1] == "read-only":
            return True
        if item.startswith("--sandbox=") and item.split("=", 1)[1] == "read-only":
            return True
    return False


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".codex-goal-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink()
    except Exception:
        return False
    return True


def runtime_env_roots(packet_dir: Path, *, command: list[str], role: str) -> tuple[Path, Path, Path | None]:
    packet_cache = packet_dir / ".runtime-cache"
    packet_tmp = packet_cache / "tmp"
    packet_xdg_cache = packet_cache / "xdg-cache"
    if read_only_attempt(command, role):
        shm = Path("/dev/shm")
        if shm.is_dir() and os.access(shm, os.W_OK | os.X_OK):
            digest = hashlib.sha256(packet_dir.as_posix().encode("utf-8")).hexdigest()[:16]
            shm_root = shm / f"codex-goal-{digest}"
            shm_tmp = shm_root / "tmp"
            shm_xdg_cache = shm_root / "xdg-cache"
            if _writable_dir(shm_tmp) and _writable_dir(shm_xdg_cache):
                return shm_tmp, shm_xdg_cache, shm_root
    packet_tmp.mkdir(parents=True, exist_ok=True)
    packet_xdg_cache.mkdir(parents=True, exist_ok=True)
    return packet_tmp, packet_xdg_cache, None


def run_with_timeout(
    *,
    command: list[str],
    timeout_seconds: int,
    kill_after_seconds: int,
    role: str,
    cwd: str,
    stdin_data: bytes | None,
    stdout_path: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    if shutil.which("timeout") is None:
        stdout_path.write_text(TIMEOUT_NOT_FOUND.format(role=role), encoding="utf-8")
        return {
            "returncode": 127,
            "elapsed_ms": 0,
            "timed_out": False,
            "stdout_bytes": len(TIMEOUT_NOT_FOUND.format(role=role).encode("utf-8")),
            "stderr_bytes": 0,
            "command": _command_from_parts(command),
            "command_parts": command,
            "started_at": started_at,
            "completed_at": started_at,
        }
    tmp_root, xdg_cache, external_cache_root = runtime_env_roots(stdout_path.parent, command=command, role=role)
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TEMP"] = tmp_root.as_posix()
    env["TMP"] = tmp_root.as_posix()
    env["TMPDIR"] = tmp_root.as_posix()
    env["XDG_CACHE_HOME"] = xdg_cache.as_posix()
    if extra_env:
        env.update(extra_env)
    pytest_addopts = env.get("PYTEST_ADDOPTS", "").strip()
    cache_opt = "-p no:cacheprovider"
    env["PYTEST_ADDOPTS"] = (pytest_addopts + " " + cache_opt).strip() if cache_opt not in pytest_addopts else pytest_addopts
    full_command = [
        "timeout",
        "--foreground",
        f"--kill-after={kill_after_seconds}s",
        f"{timeout_seconds}s",
    ] + command
    start = time.perf_counter()
    proc: subprocess.Popen[bytes] | None = None
    stdout_data = b""
    stderr_data = b""
    try:
        proc = subprocess.Popen(
            full_command,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout_data, stderr_data = proc.communicate(input=stdin_data)
    finally:
        elapsed_ms = int(round((time.perf_counter() - start) * 1000))
        completed_at = datetime.now(timezone.utc).isoformat()
        if external_cache_root is not None:
            shutil.rmtree(external_cache_root, ignore_errors=True)
    stdout_data = stdout_data or b""
    stderr_data = stderr_data or b""
    stdout_path.write_bytes(stdout_data)
    stderr_path = stdout_path.with_suffix(stdout_path.suffix + ".stderr")
    stderr_path.write_bytes(stderr_data)
    if proc is None:
        raise SystemExit("subprocess launch failed; no process object created")
    returncode = proc.returncode if proc.returncode is not None else -1
    timed_out = returncode in TIMEOUT_RETURN_CODES
    return {
        "returncode": returncode,
        "elapsed_ms": elapsed_ms,
        "timed_out": timed_out,
        "stdout_bytes": len(stdout_data),
        "stderr_bytes": len(stderr_data),
        "command": _command_from_parts(full_command),
        "command_parts": full_command,
        "started_at": started_at,
        "completed_at": completed_at,
        "stderr_path": stderr_path.name,
    }


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
        output = "\n".join(worktree_status_lines(worktree, untracked_files_all=True))
    except Exception:  # noqa: BLE001
        return []
    changed_files = []
    for line in output.splitlines():
        path = porcelain_status_path(line)
        if path and not is_runtime_cache_path(path):
            changed_files.append(path)
    return changed_files


def worktree_status_map(worktree: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in worktree_status_lines(worktree, untracked_files_all=True):
        if not line.strip():
            continue
        status = line[:2]
        path = porcelain_status_path(line)
        if path:
            result[path] = status.strip()
    return result


def summarize_dirty_stop_salvage(
    packet_dir: Path,
    config: dict[str, Any],
    worktree: str,
    packet_changed_files: list[str],
    *,
    attempt: dict[str, Any],
    attempt_index: int | None = None,
    message: str,
) -> dict[str, Any]:
    if not packet_changed_files:
        return {}
    status_map = worktree_status_map(worktree)
    owned_files = [
        item
        for item in config.get("owned_files", [])
        if isinstance(item, str) and item.strip()
    ] if isinstance(config.get("owned_files"), list) else []
    owned_changes = [item for item in packet_changed_files if path_is_owned(item, owned_files)] if owned_files else []
    external_changes = [item for item in packet_changed_files if item not in owned_changes]
    tracked_changes: list[str] = []
    untracked_changes: list[str] = []
    for item in packet_changed_files:
        code = status_map.get(item, "")
        if code.startswith("?"):
            untracked_changes.append(item)
        elif code:
            tracked_changes.append(item)
    patch_path = packet_dir / "dirty-stop.patch"
    diff_paths = sorted(set(tracked_changes))
    diff_text = ""
    if diff_paths:
        completed = subprocess.run(
            ["git", "-C", worktree, "diff", "--no-color", "--", *diff_paths],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        diff_text = completed.stdout
    patch_path.write_text(diff_text, encoding="utf-8")
    untracked_salvage_dir = packet_dir / "dirty-stop-untracked-files"
    if untracked_salvage_dir.exists():
        shutil.rmtree(untracked_salvage_dir)
    copied_untracked: list[dict[str, Any]] = []
    skipped_untracked: list[dict[str, Any]] = []
    total_salvaged_bytes = 0
    for relative_path in sorted(set(untracked_changes)):
        target = safe_worktree_target(worktree, relative_path)
        if target is None:
            skipped_untracked.append({"path": relative_path, "reason": "path escaped worktree"})
            continue
        if not target.is_file():
            skipped_untracked.append({"path": relative_path, "reason": "not a regular file"})
            continue
        try:
            size = target.stat().st_size
        except OSError as exc:
            skipped_untracked.append({"path": relative_path, "reason": f"stat failed: {exc.__class__.__name__}"})
            continue
        if size > MAX_UNTRACKED_SALVAGE_FILE_BYTES:
            skipped_untracked.append({"path": relative_path, "reason": "file exceeds per-file salvage limit", "bytes": size})
            continue
        if total_salvaged_bytes + size > MAX_UNTRACKED_SALVAGE_TOTAL_BYTES:
            skipped_untracked.append({"path": relative_path, "reason": "total salvage byte limit exceeded", "bytes": size})
            continue
        destination = untracked_salvage_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, destination)
        total_salvaged_bytes += size
        copied_untracked.append(
            {
                "path": relative_path,
                "salvaged_path": f"{untracked_salvage_dir.name}/{relative_path}",
                "bytes": size,
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        )
    if not copied_untracked and untracked_salvage_dir.exists():
        shutil.rmtree(untracked_salvage_dir)
    if external_changes and owned_files:
        recommendation = "repair manually; dirty changes include files outside owned paths"
    elif diff_text and copied_untracked:
        recommendation = "apply dirty-stop.patch, copy files from dirty-stop-untracked-files into a fresh worktree, and retry the worker packet"
    elif diff_text:
        recommendation = "apply dirty-stop.patch in a fresh worktree and retry the worker packet"
    elif copied_untracked:
        recommendation = "copy files from dirty-stop-untracked-files into a fresh worktree and retry the worker packet"
    else:
        recommendation = "inspect dirty-stop-context.json; no recoverable patch or untracked file copy was produced"
    salvage_context: dict[str, Any] = {
        "schema_version": 1,
        "kind": "dirty_stop_salvage",
        "packet_id": string_value(config, "packet_id"),
        "attempt_index": attempt_index,
        "attempt_alias": attempt.get("alias"),
        "attempt_provider": attempt.get("provider"),
        "sandbox": string_value(config, "sandbox"),
        "status_message": message,
        "changed_files": packet_changed_files,
        "tracked_changes": tracked_changes,
        "untracked_changes": untracked_changes,
        "ownership": {
            "owned_changes": owned_changes,
            "external_changes": external_changes,
            "has_owned_policy": bool(owned_files),
            "all_owned": bool(owned_files) and not external_changes,
            "external_count": len(external_changes),
            "owned_count": len(owned_changes),
        },
        "patch": {
            "path": patch_path.name,
            "has_content": bool(diff_text),
            "patched_files": sorted(set(diff_paths)),
        },
        "untracked_file_salvage": {
            "directory": untracked_salvage_dir.name if copied_untracked else None,
            "copied_files": copied_untracked,
            "skipped_files": skipped_untracked,
            "total_bytes": total_salvaged_bytes,
            "per_file_limit_bytes": MAX_UNTRACKED_SALVAGE_FILE_BYTES,
            "total_limit_bytes": MAX_UNTRACKED_SALVAGE_TOTAL_BYTES,
        },
        "relaunch_recommendation": recommendation,
    }
    context_path = packet_dir / "dirty-stop-context.json"
    context_path.write_text(json.dumps(salvage_context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return salvage_context


def write_terminal_worker(
    packet_dir: Path,
    config: dict[str, Any],
    message: str,
    *,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    salvage_context: dict[str, Any] | None = None,
) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    effective_commands = commands_run
    if effective_commands is None:
        effective_commands = terminal_command_lines(config)
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
        "commands_run": effective_commands,
        "tests": [],
        "blockers": [
            message,
            "Inspect worker event logs in this packet directory for the underlying CLI, schema, quota, auth, or model error.",
        ],
        "handoff": message
        + " Inspect worker event logs in this packet directory for the underlying CLI, schema, quota, auth, or model error.",
    }
    if salvage_context:
        data["dirty_salvage_context"] = salvage_context
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_terminal_research(
    packet_dir: Path,
    config: dict[str, Any],
    message: str,
    *,
    commands_run: list[str] | None = None,
) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    commands = terminal_command_lines(config, commands_run)
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


def write_terminal_review(
    packet_dir: Path,
    config: dict[str, Any],
    message: str,
    *,
    commands_run: list[str] | None = None,
) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "reviewer",
        "verdict": "blocked",
        "findings": [message],
        "finding_classes": ["orchestration_bug"],
        "commands_run": terminal_command_lines(config, commands_run),
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


def write_terminal(
    packet_dir: Path,
    config: dict[str, Any],
    message: str,
    *,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    salvage_context: dict[str, Any] | None = None,
) -> None:
    role = string_value(config, "role")
    if role == "research-worker":
        write_terminal_research(packet_dir, config, message, commands_run=commands_run)
    elif role == "reviewer":
        write_terminal_review(packet_dir, config, message, commands_run=commands_run)
    elif role == "worker":
        write_terminal_worker(
            packet_dir,
            config,
            message,
            changed_files=changed_files,
            commands_run=commands_run,
            salvage_context=salvage_context,
        )
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


def run_gemini_probe_command(
    attempt: dict[str, Any],
    *,
    label: str,
    packet_dir: Path,
    config: dict[str, Any],
    worktree: str,
) -> tuple[int, dict[str, Any], Path]:
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
    append_attempt_execution(attempt, rc if isinstance(rc, dict) else {"returncode": rc}, phase=f"probe-{label}")
    execution = rc if isinstance(rc, dict) else {"returncode": rc}
    if execution.get("returncode") != 0:
        return int(execution.get("returncode") or 1), execution, log_path
    returncode = validate_probe_output(log_path, prompt, "Gemini")
    execution["validation_returncode"] = returncode
    return returncode, execution, log_path


def run_gemini_attempt(
    attempt: dict[str, Any],
    *,
    label: str,
    packet_dir: Path,
    config: dict[str, Any],
    schema_path: Path,
    worktree: str,
) -> tuple[int, dict[str, Any], Path]:
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
    if not isinstance(rc, dict):
        rc = {"returncode": rc}
    append_attempt_execution(attempt, rc, phase=f"attempt-{label}")
    if rc.get("returncode") != 0:
        return int(rc.get("returncode") or 1), rc, output_path
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    result = 0 if extract_status_json(packet_dir, schema_path, output_path, config, parse_report=parse_report) else 1
    return result, rc, output_path


def run_copilot_probe_command(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], worktree: str) -> tuple[int, dict[str, Any], Path]:
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
    version_execution = run_with_timeout(
        command=version_command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=version_probe,
    )
    append_attempt_execution(attempt, version_execution, phase=f"probe-{label}-version")
    if version_execution.get("returncode", 0) != 0:
        return version_execution.get("returncode", 1), version_execution, version_probe
    record_executed_command(attempt, command_probe)
    execution = run_with_timeout(
        command=command_probe,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=probe_path,
    )
    append_attempt_execution(attempt, execution, phase=f"probe-{label}")
    validation_returncode = 1
    if execution.get("returncode", 1) == 0:
        validation_returncode = validate_probe_output(probe_path, prompt, "Copilot")
    execution["validation_returncode"] = validation_returncode
    return validation_returncode, execution, probe_path


def run_copilot_attempt(attempt: dict[str, Any], *, label: str, packet_dir: Path, config: dict[str, Any], schema_path: Path, worktree: str) -> tuple[int, dict[str, Any], Path]:
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
    execution = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=None,
        stdout_path=output_path,
    )
    append_attempt_execution(attempt, execution, phase=f"attempt-{label}")
    if execution.get("returncode", 1) != 0:
        return int(execution.get("returncode", 1)), execution, output_path
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    return 0 if extract_status_json(packet_dir, schema_path, output_path, config, parse_report=parse_report) else 1, execution, output_path


def run_codex_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> tuple[int, dict[str, Any], Path]:
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
    execution = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=str(packet_dir),
        stdin_data=prompt_path.read_bytes(),
        stdout_path=event_path,
    )
    append_attempt_execution(attempt, execution, phase=f"attempt-{label}")
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    if execution.get("returncode", 1) != 0:
        output_nonempty = output_path.exists() and output_path.stat().st_size > 0
        if role == "worker" and output_nonempty and ensure_status_json(packet_dir, schema_path, output_path, event_path, config, parse_report=parse_report):
            parse_report["status"] = "schema_success_nonzero_exit"
            parse_report["nonzero_returncode"] = int(execution.get("returncode", 1))
            return 0, execution, event_path
        return int(execution.get("returncode", 1)), execution, event_path
    if role == "worker" and not ensure_status_json(packet_dir, schema_path, output_path, event_path, config, parse_report=parse_report):
        return 1, execution, event_path
    return 0, execution, event_path


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


def run_opencode_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> tuple[int, dict[str, Any], Path]:
    schema_path = packet_dir / schema_name
    output_path = packet_dir / output_name
    prompt_path = packet_dir / "prompt.md"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    event_path = packet_dir / f"events-{label}-readback-error.log"
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    binary = attempt.get("command_binary") if isinstance(attempt.get("command_binary"), str) else "opencode"
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved:
        print(f"opencode binary not found: {binary}", file=sys.stderr)
        event_path.write_text(f"opencode binary not found: {binary}\n", encoding="utf-8")
        return 127, {
            "returncode": 127,
            "elapsed_ms": 0,
            "timed_out": False,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "command": str(binary),
            "command_parts": [binary],
        }, event_path
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
    extra_env, db_path = opencode_packet_env(packet_dir)
    attempt["opencode_state"] = {
        "xdg_data_home": extra_env["XDG_DATA_HOME"],
        "xdg_state_home": extra_env["XDG_STATE_HOME"],
        "xdg_cache_home": extra_env["XDG_CACHE_HOME"],
        "db_path": db_path.as_posix(),
    }
    execution = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=str(packet_dir),
        stdin_data=None,
        stdout_path=event_path,
        extra_env=extra_env,
    )
    append_attempt_execution(attempt, execution, phase=f"attempt-{label}")
    if execution.get("returncode", 1) != 0:
        parse_report = opencode_wal_failure_report(execution, packet_dir) or opencode_error_event_report(event_path)
        if parse_report:
            attempt["_parse_report"] = parse_report
        return int(execution.get("returncode", 1)), execution, event_path
    session_id = parse_session_id(event_path.read_text(encoding="utf-8", errors="replace") if event_path.exists() else "")
    if not session_id:
        print("opencode attempt did not emit a session id", file=sys.stderr)
        return 1, execution, event_path
    assistant_text, readback = read_opencode_assistant_text(session_id, db_path)
    assistant_log_path = packet_dir / f"events-{label}-assistant.log"
    assistant_log_path.write_text(assistant_text, encoding="utf-8")
    parse_report: dict[str, Any] = opencode_error_event_report(event_path) or opencode_empty_output_report(assistant_text, readback) or {}
    if parse_report:
        readback = {**readback, **parse_report, "status": "failed"}
    (packet_dir / f"events-{label}-opencode-readback.json").write_text(json.dumps(readback, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    attempt["_parse_report"] = parse_report
    if parse_report:
        return 1, execution, event_path
    if not ensure_status_json(packet_dir, schema_path, output_path, assistant_log_path, config, parse_report=parse_report):
        return 1, execution, event_path
    return 0, execution, event_path


def run_generic_cli_model(attempt: dict[str, Any], *, packet_dir: Path, config: dict[str, Any], schema_name: str, output_name: str, worktree: str, label: str) -> tuple[int, dict[str, Any], Path]:
    schema_path = packet_dir / schema_name
    output_path = packet_dir / output_name
    prompt_path = packet_dir / "prompt.md"
    prompt_text = prompt_path.read_text(encoding="utf-8")
    timeout_seconds = int_value(attempt, "timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    event_path = packet_dir / f"events-{label}.log"
    binary = attempt.get("command_binary") if isinstance(attempt.get("command_binary"), str) else ""
    if not binary:
        print("generic CLI attempt missing command_binary", file=sys.stderr)
        event_path.write_text("generic CLI attempt missing command_binary\n", encoding="utf-8")
        return (
            127,
            {"returncode": 127, "elapsed_ms": 0, "timed_out": False, "stdout_bytes": 0, "stderr_bytes": 0, "command": "", "command_parts": []},
            event_path,
        )
    resolved = shutil.which(binary) if not os.path.isabs(binary) else binary
    if not resolved:
        print(f"generic CLI binary not found: {binary}", file=sys.stderr)
        event_path.write_text(f"generic CLI binary not found: {binary}\n", encoding="utf-8")
        return (
            127,
            {"returncode": 127, "elapsed_ms": 0, "timed_out": False, "stdout_bytes": 0, "stderr_bytes": 0, "command": binary, "command_parts": [binary]},
            event_path,
        )
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
    execution = run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=worktree,
        stdin_data=prompt_path.read_bytes(),
        stdout_path=event_path,
    )
    append_attempt_execution(attempt, execution, phase=f"attempt-{label}")
    if execution.get("returncode", 1) != 0:
        attempt["provenance_level"] = "low"
        return int(execution.get("returncode", 1)), execution, event_path
    parse_report: dict[str, Any] = {}
    attempt["_parse_report"] = parse_report
    if not ensure_status_json(packet_dir, schema_path, output_path, event_path, config, parse_report=parse_report):
        attempt["provenance_level"] = "low"
        return 1, execution, event_path
    attempt["provenance_level"] = "low"
    return 0, execution, event_path


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


def commands_include_diff_check_head(commands_run: object) -> bool:
    if not isinstance(commands_run, list):
        return False
    for command in commands_run:
        if not isinstance(command, str) or not command.strip():
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            if token != "git":
                continue
            args = tokens[index + 1 :]
            if args and args[0] == "diff" and "--check" in args and "HEAD" in args:
                return True
    return False


def validate_packet_post_constraints(data: dict[str, Any], config: dict[str, Any]) -> None:
    if config.get("role") != "worker":
        if config.get("role") == "reviewer":
            verification_gaps = data.get("verification_gaps")
            if data.get("verdict") == "mergeable" and verification_gaps:
                raise ValueError("verification_gaps must be empty when verdict is mergeable")
        return
    expected_ladder = config.get("selected_ladder")
    if isinstance(expected_ladder, list) and data.get("selected_ladder") != expected_ladder:
        raise ValueError("selected_ladder must match launch-config selected_ladder exactly")
    changed_files = data.get("changed_files")
    has_changed_files = isinstance(changed_files, list) and any(isinstance(item, str) and item.strip() for item in changed_files)
    if data.get("status") == "pass" and has_changed_files and not commands_include_diff_check_head(data.get("commands_run")):
        raise ValueError("passing worker status commands_run must include git diff --check HEAD")


def normalize_status_before_validation(data: Any, config: dict[str, Any]) -> list[str]:
    if not isinstance(data, dict):
        return []
    if config.get("role") != "worker":
        return []
    messages: list[str] = []
    worktree = data.get("worktree")
    worktree_path = data.get("worktree_path")
    if (not isinstance(worktree, str) or not worktree.strip()) and isinstance(worktree_path, str) and worktree_path.strip():
        data["worktree"] = worktree_path.strip()
        messages.append("normalized missing worktree from worktree_path")
    elif (not isinstance(worktree_path, str) or not worktree_path.strip()) and isinstance(worktree, str) and worktree.strip():
        data["worktree_path"] = worktree.strip()
        messages.append("normalized missing worktree_path from worktree")
    evidence = data.get("evidence_summary")
    handoff = data.get("handoff")
    if (not isinstance(evidence, str) or not evidence.strip()) and isinstance(handoff, str) and handoff.strip():
        data["evidence_summary"] = handoff.strip()
        messages.append("normalized missing evidence_summary from handoff")
    return messages


def output_matches_schema(schema_path: Path, output_path: Path, config: dict[str, Any]) -> bool:
    if not output_path.exists():
        return False
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        data = json.loads(output_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "success":
            data["status"] = "pass"
            output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        normalization_messages = normalize_status_before_validation(data, config)
        validate_instance(data, schema)
        validate_packet_post_constraints(data, config)
        if normalization_messages:
            output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
            normalization_messages = normalize_status_before_validation(data, config)
            validate_instance(data, schema)
            validate_packet_post_constraints(data, config)
        except Exception as exc:
            source_validation_errors.append(f"{source_name}: invalid status object: {exc}")
            continue
        output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        parse_report["failure_subclass"] = None
        parse_report["status"] = "recovered"
        normalization_notes = [f"{source_name}: {message}" for message in normalization_messages]
        parse_report["messages"] = source_errors + source_validation_errors + normalization_notes
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
        rc, _, probe_event_path = run_gemini_probe_command(attempt, label=label, packet_dir=packet_dir, config=config, worktree=worktree)
        if rc != 0:
            return rc, probe_event_path
        attempt_rc, _, attempt_event_path = run_gemini_attempt(
            attempt,
            label=label,
            packet_dir=packet_dir,
            config=config,
            schema_path=packet_dir / schema_name,
            worktree=worktree,
        )
        return attempt_rc, attempt_event_path
    if provider == "copilot":
        rc, _, probe_event_path = run_copilot_probe_command(attempt, label=label, packet_dir=packet_dir, config=config, worktree=worktree)
        if rc != 0:
            return rc, probe_event_path
        attempt_rc, _, attempt_event_path = run_copilot_attempt(
            attempt,
            label=label,
            packet_dir=packet_dir,
            config=config,
            schema_path=packet_dir / schema_name,
            worktree=worktree,
        )
        return attempt_rc, attempt_event_path
    if provider == "codex":
        attempt_rc, _, attempt_event_path = run_codex_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        )
        return attempt_rc, attempt_event_path
    if provider == "opencode":
        attempt_rc, _, attempt_event_path = run_opencode_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        )
        return attempt_rc, attempt_event_path
    if provider == "generic-cli":
        attempt_rc, _, attempt_event_path = run_generic_cli_model(
            attempt,
            packet_dir=packet_dir,
            config=config,
            schema_name=schema_name,
            output_name=output_name,
            worktree=worktree,
            label=label,
        )
        return attempt_rc, attempt_event_path
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
    guard_scheduler_closed_pass(packet_dir, config)
    clean_outputs(packet_dir, output_name, attempts, config)

    if role == "worker":
        baseline_changed_files = changed_file_fingerprints(worktree)
        for index, attempt in enumerate(attempts):
            command_lines = command_lines_from_attempts(attempts)
            degraded = degraded_route_health(packet_dir, attempt)
            if degraded is not None:
                message = (
                    f"{event_label(attempt, f'attempt-{index + 1}')} skipped because bundle route health "
                    f"degraded after {degraded.get('degraded_reason')}"
                )
                attempt["called"] = False
                attempt["accepted"] = False
                attempt["failure_class"] = "route_degraded"
                attempt["failure_subclass"] = "route_degraded"
                attempt["provider_error_code"] = "ROUTE_HEALTH_DEGRADED"
                attempt["route_health"] = {
                    "transport_disconnect_count": 0,
                    "capacity_exhausted": False,
                    "degraded": True,
                    "degraded_reason": degraded.get("degraded_reason"),
                    "degraded_after_count": degraded.get("degraded_after_count"),
                }
                attempt["status_parse"] = {
                    "status": "failed",
                    "failure_subclass": "route_degraded",
                    "provider_error_code": "ROUTE_HEALTH_DEGRADED",
                    "messages": [message],
                    "message_count": 1,
                    "final_message": message,
                }
                write_launcher_state(
                    packet_dir,
                    config,
                    state="fail-clean",
                    attempt=attempt,
                    attempt_index=index,
                    returncode=1,
                    dirty=False,
                    output_nonempty=False,
                    message=message,
                    stop_reason="route_degraded",
                )
                continue
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
            stop_reason = _attempt_stop_reason(attempt, state)
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
            record_bundle_route_failure(packet_dir, attempt)
            command_lines = command_lines_from_attempts(attempts)
            write_launcher_state(
                packet_dir,
                config,
                state=state,
                attempt=attempt,
                attempt_index=index,
                returncode=rc,
                dirty=dirty,
                output_nonempty=output_nonempty,
                elapsed_ms=attempt_elapsed_ms(attempt),
                stop_reason=stop_reason,
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
                    write_terminal(packet_dir, config, message, changed_files=packet_changed_files, commands_run=command_lines)
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
                        elapsed_ms=attempt_elapsed_ms(attempt),
                        stop_reason=stop_reason,
                    )
                    cleanup_runtime_cache_evidence(packet_dir, config)
                    write_telemetry(packet_dir, config)
                    return 2
                cleanup_runtime_cache_evidence(packet_dir, config)
                write_telemetry(packet_dir, config)
                return 0
            if dirty:
                label = event_label(attempt, f"attempt-{index + 1}")
                suffix = "refusing fallback in same worktree." if index < len(attempts) - 1 else "no fallback remains."
                message = f"{label} failed after leaving dirty worktree; {suffix}"
                if failure_message:
                    message = f"{message} details: {failure_message}"
                (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
                salvage_context = summarize_dirty_stop_salvage(
                    packet_dir=packet_dir,
                    config=config,
                    worktree=worktree,
                    packet_changed_files=packet_changed_files,
                    attempt=attempt,
                    attempt_index=index,
                    message=message,
                )
                write_terminal(
                    packet_dir,
                    config,
                    message,
                    changed_files=packet_changed_files,
                    commands_run=command_lines,
                    salvage_context=salvage_context,
                )
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
                    elapsed_ms=attempt_elapsed_ms(attempt),
                    stop_reason=stop_reason,
                    salvage_context=salvage_context,
                )
                cleanup_runtime_cache_evidence(packet_dir, config)
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
                command_lines = command_lines_from_attempts(attempts)
                write_terminal(packet_dir, config, message, commands_run=command_lines)
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
                    elapsed_ms=attempt_elapsed_ms(attempt),
                    stop_reason=stop_reason,
                )
                cleanup_runtime_cache_evidence(packet_dir, config)
                write_telemetry(packet_dir, config)
                return 1
        packet_changed_files = packet_delta_changed_files(worktree, baseline_changed_files)
        if packet_changed_files:
            message = "worker failed after leaving dirty worktree; no fallback remains."
            (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
            command_lines = command_lines_from_attempts(attempts)
            salvage_context = summarize_dirty_stop_salvage(
                packet_dir=packet_dir,
                config=config,
                worktree=worktree,
                packet_changed_files=packet_changed_files,
                attempt=attempts[-1] if attempts else {},
                attempt_index=len(attempts) - 1 if attempts else None,
                message=message,
            )
            write_terminal(
                packet_dir,
                config,
                message,
                changed_files=packet_changed_files,
                commands_run=command_lines,
                salvage_context=salvage_context,
            )
            write_launcher_state(
                packet_dir,
                config,
                state="blocked",
                attempt=attempts[-1] if attempts else None,
                attempt_index=len(attempts) - 1 if attempts else None,
                returncode=rc,
                dirty=True,
                message=message,
                stop_reason="dirty_stop",
                salvage_context=salvage_context,
            )
            cleanup_runtime_cache_evidence(packet_dir, config)
            write_telemetry(packet_dir, config)
            return 2
        message = string_value(config, "terminal_message")
        parse_report = attempts[-1].get("_parse_report") if attempts else {}
        if isinstance(parse_report, dict):
            parse_messages = _event_parse_messages(parse_report)
            if parse_messages:
                message = f"{message}: {'; '.join(parse_messages[:2])}"
        final_attempt = attempts[-1] if attempts else {}
        command_lines = command_lines_from_attempts(attempts)
        final_state = "fail-clean" if attempts else None
        final_stop_reason = _attempt_stop_reason(final_attempt, final_state) if final_state else None
        write_terminal(packet_dir, config, message, commands_run=command_lines)
        write_launcher_state(
            packet_dir,
            config,
            state="blocked",
            attempt=attempts[-1] if attempts else None,
            attempt_index=len(attempts) - 1 if attempts else None,
            dirty=False,
            message=message,
            elapsed_ms=attempt_elapsed_ms(final_attempt) if attempts else None,
            stop_reason=final_stop_reason,
        )
        cleanup_runtime_cache_evidence(packet_dir, config)
        write_telemetry(packet_dir, config)
        return 1

    baseline_changed_files = changed_file_fingerprints(worktree)
    for index, attempt in enumerate(attempts):
        _label = event_label(attempt, f"attempt-{index + 1}")
        provider = attempt.get("harness_kind") or attempt.get("provider")
        write_launcher_state(packet_dir, config, state="active", attempt=attempt, attempt_index=index)
        event_path: Path | None = None
        if provider == "codex":
            rc, _, event_path = run_codex_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
        elif provider == "opencode":
            rc, _, event_path = run_opencode_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
        elif provider == "generic-cli":
            rc, _, event_path = run_generic_cli_model(
                attempt,
                packet_dir=packet_dir,
                config=config,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
                label=_label,
            )
        elif provider == "gemini":
            rc, _, event_path = run_gemini_attempt(
                attempt,
                label=_label,
                packet_dir=packet_dir,
                config=config,
                schema_path=packet_dir / schema_name,
                worktree=worktree,
            )
        else:
            raise SystemExit(f"{CONFIG_NAME} unsupported {role} provider: {provider}")
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
        stop_reason = _attempt_stop_reason(attempt, state)
        write_launcher_state(
            packet_dir,
            config,
            state=state,
            attempt=attempt,
            attempt_index=index,
            returncode=rc,
            dirty=dirty,
            output_nonempty=output_nonempty,
            elapsed_ms=attempt_elapsed_ms(attempt),
            stop_reason=stop_reason,
        )
        if rc == 0 and dirty:
            message = f"{role} changed worktree files despite read-only/review semantics: " + ", ".join(packet_changed_files)
            attempt["failure_class"] = "dirty_worktree"
            attempt["failure_subclass"] = "read_only_attempt_left_dirty_worktree"
            (packet_dir / "dirty-worktree.blocked.txt").write_text(message + "\n", encoding="utf-8")
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
                elapsed_ms=attempt_elapsed_ms(attempt),
                stop_reason="dirty_stop",
            )
            cleanup_runtime_cache_evidence(packet_dir, config)
            write_telemetry(packet_dir, config)
            write_packet_summary(packet_dir, config)
            return 2
        if rc == 0:
            cleanup_runtime_cache_evidence(packet_dir, config)
            write_telemetry(packet_dir, config)
            return 0
        if output_nonempty:
            if _parse_failure_detected(parse_report) and index < len(attempts) - 1:
                clear_invalid_output_for_fallback(output_path)
                continue
            message = string_value(config, "terminal_message")
            if failure_message:
                message = f"{message}: {failure_message}"
            command_lines = command_lines_from_attempts(attempts)
            write_terminal(packet_dir, config, message, commands_run=command_lines)
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
                elapsed_ms=attempt_elapsed_ms(attempt),
                stop_reason=stop_reason,
            )
            cleanup_runtime_cache_evidence(packet_dir, config)
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
    final_attempt = attempts[-1] if attempts else {}
    final_state = "fail-clean" if attempts else None
    final_stop_reason = _attempt_stop_reason(final_attempt, final_state) if final_state else None
    command_lines = command_lines_from_attempts(attempts)
    write_terminal(packet_dir, config, message, commands_run=command_lines)
    write_launcher_state(
        packet_dir,
        config,
        state="blocked",
        attempt=attempts[-1] if attempts else None,
        attempt_index=len(attempts) - 1 if attempts else None,
        dirty=False,
        message=message,
        elapsed_ms=attempt_elapsed_ms(final_attempt) if attempts else None,
        stop_reason=final_stop_reason,
    )
    cleanup_runtime_cache_evidence(packet_dir, config)
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
