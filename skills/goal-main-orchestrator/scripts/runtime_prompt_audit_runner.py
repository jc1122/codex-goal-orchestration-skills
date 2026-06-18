#!/usr/bin/env python3
"""Run compact prompt-audit packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


CONFIG_NAME = "launch-config.json"
ACTIVE_PROCESS: subprocess.Popen[bytes] | None = None
ACTIVE_PACKET_DIR: Path | None = None
ACTIVE_CONFIG: dict[str, Any] | None = None
REQUIRED_AUDIT_KEYS = {
    "manifest",
    "repo_root",
    "status",
    "can_start",
    "checked_files",
    "defects",
    "missing_dod_items",
    "actionability_verdict",
    "commands_run",
    "summary",
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON must be an object: {path}")
    return data


def append_debug_event(packet_dir: Path, config: dict[str, Any], event: dict[str, Any]) -> None:
    name = config.get("debug_events_name")
    if not isinstance(name, str) or not name.strip():
        return
    payload = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "packet_id": config.get("packet_id"),
        "role": config.get("role"),
        **event,
    }
    with (packet_dir / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def string_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{CONFIG_NAME} missing non-empty string: {key}")
    return value


def int_value(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SystemExit(f"{CONFIG_NAME} missing positive integer: {key}")
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


def packet_path(packet_dir: Path, config: dict[str, Any], key: str) -> Path:
    return packet_dir / string_value(config, key)


def remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def first_log_path(packet_dir: Path, attempt: dict[str, Any], fallback: str) -> Path:
    logs = attempt.get("event_logs")
    if isinstance(logs, list):
        for value in logs:
            if isinstance(value, str) and value:
                return packet_dir / value
    return packet_dir / fallback


def clean_outputs(packet_dir: Path, config: dict[str, Any]) -> None:
    remove_if_exists(packet_path(packet_dir, config, "output_name"))
    remove_if_exists(packet_path(packet_dir, config, "telemetry_name"))
    seen: set[str] = set()
    for attempt in list_value(config, "attempts"):
        logs = attempt.get("event_logs")
        if not isinstance(logs, list):
            continue
        for value in logs:
            if isinstance(value, str) and value not in seen:
                seen.add(value)
                remove_if_exists(packet_dir / value)


def valid_string_list(value: object, *, min_items: int = 0) -> bool:
    return (
        isinstance(value, list)
        and len(value) >= min_items
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def valid_defects(value: object) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("file"), str) or not item["file"].strip():
            return False
        if item.get("severity") not in {"critical", "major", "minor"}:
            return False
        if not isinstance(item.get("message"), str) or not item["message"].strip():
            return False
    return True


def valid_audit_data(data: object, *, manifest: str, repo_root: str) -> bool:
    if not isinstance(data, dict) or not set(data) >= REQUIRED_AUDIT_KEYS:
        return False
    if data.get("manifest") != manifest or data.get("repo_root") != repo_root:
        return False
    if data.get("status") not in {"pass", "failed", "blocked"}:
        return False
    if not isinstance(data.get("can_start"), bool):
        return False
    if not valid_string_list(data.get("checked_files")):
        return False
    if not valid_string_list(data.get("missing_dod_items")):
        return False
    if not valid_string_list(data.get("commands_run"), min_items=1):
        return False
    if not valid_defects(data.get("defects")):
        return False
    for key in ["actionability_verdict", "summary"]:
        if not isinstance(data.get(key), str) or not data[key].strip():
            return False
    if data["status"] == "pass":
        if data["can_start"] is not True or data["missing_dod_items"] or not data["checked_files"]:
            return False
        for item in data["defects"]:
            if isinstance(item, dict) and item.get("severity") in {"critical", "major"}:
                return False
    return True


def valid_audit_file(path: Path, config: dict[str, Any]) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return valid_audit_data(
        data,
        manifest=string_value(config, "manifest_path"),
        repo_root=string_value(config, "repo_root"),
    )


def audit_status(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "blocked"
    status = data.get("status")
    return status if isinstance(status, str) else "blocked"


def write_telemetry(packet_dir: Path, config: dict[str, Any]) -> None:
    command = [
        "python3",
        string_value(config, "telemetry_script"),
        "--packet-dir",
        packet_dir.as_posix(),
        "--packet-id",
        string_value(config, "packet_id"),
        "--role",
        "prompt-auditor",
        "--output-name",
        string_value(config, "output_name"),
        "--prompt-name",
        string_value(config, "prompt_name"),
        "--output",
        string_value(config, "telemetry_name"),
    ]
    for attempt in list_value(config, "attempts"):
        command.extend(["--attempt-json", json.dumps(attempt, sort_keys=True)])
    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        command.extend(["--debug", "--debug-output", debug_name])
    subprocess.run(command, check=False)
    append_debug_event(packet_dir, config, {"phase": "telemetry", "event": "written"})


def command_for_attempt(packet_dir: Path, config: dict[str, Any], attempt: dict[str, Any]) -> list[str]:
    return [
        "codex",
        "exec",
        "--ephemeral",
        "-m",
        string_value(attempt, "model"),
        "-C",
        string_value(config, "repo_root"),
        "-s",
        "read-only",
        "--json",
        "--output-schema",
        packet_path(packet_dir, config, "schema_name").as_posix(),
        "-o",
        packet_path(packet_dir, config, "output_name").as_posix(),
        "-",
    ]


def run_with_timeout(
    *,
    command: list[str],
    timeout_seconds: int,
    kill_after_seconds: int,
    cwd: Path,
    prompt_path: Path,
    log_path: Path,
) -> int:
    global ACTIVE_PROCESS
    if shutil.which("timeout") is None:
        log_path.write_text("timeout command not found; refusing unbounded prompt-audit attempt.\n", encoding="utf-8")
        return 127
    full_command = [
        "timeout",
        "--foreground",
        f"--kill-after={kill_after_seconds}s",
        f"{timeout_seconds}s",
        *command,
    ]
    try:
        with prompt_path.open("rb") as stdin, log_path.open("wb") as stdout:
            process = subprocess.Popen(
                full_command,
                cwd=cwd,
                stdin=stdin,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            ACTIVE_PROCESS = process
            return process.wait()
    except FileNotFoundError as exc:
        log_path.write_text(f"prompt-audit command unavailable: {exc}\n", encoding="utf-8")
        return 127
    except Exception as exc:  # noqa: BLE001
        log_path.write_text(f"prompt-audit command failed before launch: {exc}\n", encoding="utf-8")
        return 1
    finally:
        ACTIVE_PROCESS = None


def candidate_event_texts(event: object) -> list[str]:
    texts: list[str] = []
    if isinstance(event, dict):
        item = event.get("item")
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        for key in ["text", "message", "content"]:
            text = event.get(key)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    return texts


def recover_audit_from_events(log_path: Path, output_path: Path, config: dict[str, Any]) -> bool:
    if not log_path.exists():
        return False
    candidates: list[str] = []
    for raw in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        candidates.append(stripped)
        try:
            event = json.loads(stripped)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        candidates.extend(candidate_event_texts(event))
    for text in reversed(candidates):
        try:
            data = json.loads(text)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if valid_audit_data(
            data,
            manifest=string_value(config, "manifest_path"),
            repo_root=string_value(config, "repo_root"),
        ):
            write_json(output_path, data)
            return True
    return False


def terminal_message(config: dict[str, Any], key: str) -> str:
    messages = config.get("terminal_messages")
    if isinstance(messages, dict):
        value = messages.get(key)
        if isinstance(value, str) and value:
            return value
    defaults = {
        "git_invalid": "Repository root is not a valid git worktree; prompt audit cannot run.",
        "missing_runtime_file": "Prompt audit runtime input file is missing.",
        "command_failed": "Prompt audit command failed before producing a valid prompt-audit.json.",
        "invalid_output": "Prompt audit did not produce a valid prompt-audit.json artifact.",
        "interrupted": "Prompt audit runner was interrupted before producing a valid prompt-audit.json.",
    }
    return defaults[key]


def failure_summary(packet_dir: Path, config: dict[str, Any]) -> str:
    parts = []
    for attempt in list_value(config, "attempts"):
        # Match the run loop's event-log naming (dots replaced) so the fallback path agrees.
        label = str(attempt.get("alias", "attempt")).replace(".", "-")
        log_path = first_log_path(packet_dir, attempt, f"events-{label}.jsonl")
        if not log_path.exists():
            parts.append(f"{log_path.name}: missing")
            continue
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        if "timeout" in lowered or "timed out" in lowered:
            parts.append(f"{log_path.name}: timeout")
        elif "unsupported" in lowered and "model" in lowered:
            parts.append(f"{log_path.name}: model-unsupported")
        elif "schema" in lowered:
            parts.append(f"{log_path.name}: schema-or-output-invalid")
        elif "command unavailable" in lowered or "not found" in lowered:
            parts.append(f"{log_path.name}: command-unavailable")
        elif text.strip():
            tail = " | ".join(line.strip() for line in text.splitlines()[-5:] if line.strip())
            parts.append(f"{log_path.name}: {tail[:500]}")
        else:
            parts.append(f"{log_path.name}: empty")
    return (
        "Prompt audit attempts failed without producing a valid prompt-audit.json. failure_fingerprints="
        + "; ".join(parts)
    )


def write_terminal_audit(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    commands = config.get("commands_run")
    if not isinstance(commands, list) or not all(isinstance(item, str) and item for item in commands):
        commands = [str(attempt.get("command", "")) for attempt in list_value(config, "attempts")]
    data = {
        "manifest": string_value(config, "manifest_path"),
        "repo_root": string_value(config, "repo_root"),
        "status": "blocked",
        "can_start": False,
        "checked_files": [],
        "defects": [
            {
                "file": "prompt-audit",
                "severity": "critical",
                "message": message,
            }
        ],
        "missing_dod_items": [
            "prompt audit did not produce a valid audit artifact",
            "Inspect audit event logs in this packet directory for the underlying CLI or schema error.",
        ],
        "actionability_verdict": "blocked",
        "commands_run": commands,
        "summary": message
        + " Inspect audit event logs in this packet directory for the underlying CLI or schema error.",
    }
    write_json(packet_path(packet_dir, config, "output_name"), data)


def repo_is_valid(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", path.as_posix(), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def terminate_active_process() -> None:
    process = ACTIVE_PROCESS
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            process.terminate()
        except Exception:  # noqa: BLE001
            return


def handle_interrupt(signum: int, _frame: object) -> None:
    terminate_active_process()
    if ACTIVE_PACKET_DIR is not None and ACTIVE_CONFIG is not None:
        write_terminal_audit(
            ACTIVE_PACKET_DIR,
            ACTIVE_CONFIG,
            f"{terminal_message(ACTIVE_CONFIG, 'interrupted')} signal={signum}",
        )
        write_telemetry(ACTIVE_PACKET_DIR, ACTIVE_CONFIG)
    raise SystemExit(128 + signum)


def run_packet(packet_dir: Path) -> int:
    global ACTIVE_CONFIG, ACTIVE_PACKET_DIR
    config = read_json(packet_dir / CONFIG_NAME)
    if config.get("schema_version") != 1:
        raise SystemExit(f"{CONFIG_NAME} schema_version must be 1")
    if config.get("role") != "prompt-auditor":
        raise SystemExit(f"{CONFIG_NAME} role must be 'prompt-auditor'")
    ACTIVE_PACKET_DIR = packet_dir
    ACTIVE_CONFIG = config
    signal.signal(signal.SIGTERM, handle_interrupt)
    signal.signal(signal.SIGINT, handle_interrupt)

    clean_outputs(packet_dir, config)
    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        remove_if_exists(packet_dir / debug_name)
    output_path = packet_path(packet_dir, config, "output_name")
    prompt_path = packet_path(packet_dir, config, "prompt_name")
    schema_path = packet_path(packet_dir, config, "schema_name")
    repo_root = Path(string_value(config, "repo_root"))

    if not repo_is_valid(repo_root):
        write_terminal_audit(packet_dir, config, terminal_message(config, "git_invalid"))
        write_telemetry(packet_dir, config)
        return 1
    for path in [prompt_path, schema_path]:
        if not path.exists():
            write_terminal_audit(
                packet_dir, config, f"{terminal_message(config, 'missing_runtime_file')} Missing: {path.name}"
            )
            write_telemetry(packet_dir, config)
            return 1

    for index, attempt in enumerate(list_value(config, "attempts")):
        label = str(attempt.get("alias", f"attempt-{index + 1}")).replace(".", "-")
        log_path = first_log_path(packet_dir, attempt, f"events-{label}.jsonl")
        remove_if_exists(output_path)
        run_with_timeout(
            command=command_for_attempt(packet_dir, config, attempt),
            timeout_seconds=int_value(config, "attempt_timeout_seconds"),
            kill_after_seconds=int_value(config, "timeout_kill_after_seconds"),
            cwd=repo_root,
            prompt_path=prompt_path,
            log_path=log_path,
        )
        if output_path.exists() and valid_audit_file(output_path, config):
            write_telemetry(packet_dir, config)
            return 0 if audit_status(output_path) == "pass" else 1
        if recover_audit_from_events(log_path, output_path, config):
            write_telemetry(packet_dir, config)
            return 0 if audit_status(output_path) == "pass" else 1

    write_terminal_audit(
        packet_dir, config, f"{terminal_message(config, 'invalid_output')} {failure_summary(packet_dir, config)}"
    )
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
    append_debug_event(packet_dir, config, {"phase": "prompt_audit", "event": "start"})
    try:
        rc = run_packet(packet_dir)
    except BaseException:
        append_debug_event(
            packet_dir,
            config,
            {
                "phase": "prompt_audit",
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
            "phase": "prompt_audit",
            "event": "end",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": "ok" if rc == 0 else "nonzero",
            "exit_status": rc,
        },
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
