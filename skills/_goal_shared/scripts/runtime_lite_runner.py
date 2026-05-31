#!/usr/bin/env python3
"""Run compact Lite advisory packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


CONFIG_NAME = "launch-config.json"
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
TIMEOUT_NOT_FOUND = "timeout command not found; refusing unbounded Lite advisor attempt.\n"


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def path_for(packet_dir: Path, config: dict[str, Any], key: str) -> Path:
    return packet_dir / string_value(config, key)


def first_attempt(config: dict[str, Any]) -> dict[str, Any]:
    attempts = list_value(config, "attempts")
    if len(attempts) != 1:
        raise SystemExit(f"{CONFIG_NAME} Lite runner expects exactly one attempt")
    return attempts[0]


def command_string(config: dict[str, Any], inputs: dict[str, Any] | None = None) -> str:
    attempt = first_attempt(config)
    value = attempt.get("command")
    if isinstance(value, str) and value:
        return value
    gemini_path = ""
    if inputs is not None and isinstance(inputs.get("gemini_path"), str):
        gemini_path = str(inputs.get("gemini_path"))
    command = gemini_path if gemini_path else "gemini"
    return (
        f"{command} --model {string_value(config, 'model')} "
        f"--approval-mode {string_value(config, 'approval_mode')} --skip-trust --output-format text"
    )


def terminal_message(config: dict[str, Any], key: str) -> str:
    messages = config.get("terminal_messages")
    if isinstance(messages, dict):
        value = messages.get(key)
        if isinstance(value, str) and value:
            return value
    defaults = {
        "gemini_unavailable": "Gemini CLI command unavailable at packet creation path: ",
        "inputs_stale": "Lite advisor input files changed or became unavailable after packet creation.",
        "prompt_stale": "Lite advisor prompt.md changed or became unavailable after packet creation.",
        "task_stale": "Lite advisor task.md changed or became unavailable after packet creation.",
        "gemini_stale": "Gemini CLI binary or version changed or could not be verified after packet creation.",
        "command_failed": "Lite advisor command failed. Inspect advice.raw.txt for CLI, quota, auth, or model errors.",
        "invalid_output": "Lite advisor did not produce valid advice JSON.",
    }
    return defaults[key]


def write_terminal_advice(packet_dir: Path, config: dict[str, Any], status: str, message: str) -> None:
    output_path = path_for(packet_dir, config, "output_name")
    inputs_path = path_for(packet_dir, config, "inputs_name")
    inputs = read_json(inputs_path)
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "lite_advisor",
        "purpose": string_value(config, "purpose"),
        "status": status,
        "source_files": inputs.get("source_files", []),
        "recommended_reads": [],
        "risk_flags": [],
        "advice": {},
        "summary": message,
        "blockers": [message],
        "commands_run": [command_string(config, inputs)],
    }
    write_json(output_path, data)


def write_telemetry(packet_dir: Path, config: dict[str, Any]) -> None:
    command = [
        "python3",
        string_value(config, "telemetry_script"),
        "--packet-dir",
        packet_dir.as_posix(),
        "--packet-id",
        string_value(config, "packet_id"),
        "--role",
        "lite_advisor",
        "--output-name",
        string_value(config, "output_name"),
        "--prompt-name",
        string_value(config, "prompt_name"),
        "--output",
        string_value(config, "telemetry_name"),
    ]
    for attempt in list_value(config, "attempts"):
        command.extend(["--attempt-json", json.dumps(attempt, sort_keys=True)])
    subprocess.run(command, check=False)


def verify_inputs_current(config: dict[str, Any], inputs: dict[str, Any]) -> tuple[bool, str]:
    base_dir = Path(string_value(config, "base_dir"))
    if not base_dir.is_absolute() or not base_dir.exists():
        return False, f"invalid or missing Lite base_dir: {base_dir}"
    for item in inputs.get("source_files", []):
        if not isinstance(item, dict):
            return False, "Lite source_files entries must be objects"
        rel = item.get("path", "")
        if not isinstance(rel, str) or not rel:
            return False, "Lite input has invalid relative path"
        path = (base_dir / rel).resolve()
        try:
            path.relative_to(base_dir.resolve())
        except ValueError:
            return False, f"Lite input escaped base_dir: {rel}"
        if not path.exists():
            return False, f"Lite input missing: {rel}"
        actual_hash = sha256_file(path)
        actual_size = path.stat().st_size
        if actual_hash != item.get("sha256") or actual_size != item.get("size_bytes"):
            return (
                False,
                f"Lite input stale: {rel} expected {item.get('sha256')}/{item.get('size_bytes')} "
                f"got {actual_hash}/{actual_size}",
            )
    return True, ""


def verify_file_hash(path: Path, expected: object, label: str) -> tuple[bool, str]:
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        return False, f"missing {label}_sha256 in input-files.json"
    if not path.exists():
        return False, f"Lite {label} missing: {path}"
    actual = sha256_file(path)
    if actual != expected:
        return False, f"Lite {label} stale: expected {expected} got {actual}"
    return True, ""


def verify_gemini_binary(inputs: dict[str, Any]) -> tuple[bool, str]:
    gemini_path = inputs.get("gemini_path")
    if not isinstance(gemini_path, str) or not gemini_path.strip():
        return False, f"Gemini CLI command unavailable at packet creation path: {gemini_path or ''}"
    path = Path(gemini_path)
    if not path.is_absolute() or not path.exists() or not os.access(path, os.X_OK):
        return False, f"Gemini CLI command unavailable at packet creation path: {gemini_path}"
    expected_sha = inputs.get("gemini_sha256")
    if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
        return False, "missing captured Gemini sha256 in input-files.json"
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha:
        return False, f"Gemini CLI binary changed: expected {expected_sha} got {actual_sha}"
    expected_version = inputs.get("gemini_version")
    if not isinstance(expected_version, str) or not expected_version.strip() or expected_version == "unavailable":
        return False, "missing captured Gemini version in input-files.json"
    try:
        completed = subprocess.run(
            [path.as_posix(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"could not recheck Gemini version: {exc}"
    version_lines = (completed.stdout or completed.stderr).strip().splitlines()
    actual_version = version_lines[0] if version_lines else "version-unavailable"
    if actual_version != expected_version:
        return False, f"Gemini CLI version changed: expected {expected_version!r} got {actual_version!r}"
    return True, ""


def run_with_timeout(config: dict[str, Any], command: list[str], *, cwd: Path, stdin_path: Path, stdout_path: Path) -> int:
    if shutil.which("timeout") is None:
        stdout_path.write_text(TIMEOUT_NOT_FOUND, encoding="utf-8")
        return 127
    full_command = [
        "timeout",
        "--foreground",
        f"--kill-after={int_value(config, 'timeout_kill_after_seconds')}s",
        f"{int_value(config, 'attempt_timeout_seconds')}s",
    ] + command
    with stdin_path.open("rb") as stdin, stdout_path.open("wb") as stdout:
        result = subprocess.run(
            full_command,
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return result.returncode


def extract_advice_json(raw_path: Path, output_path: Path, config: dict[str, Any]) -> bool:
    begin = string_value(config, "status_begin")
    end = string_value(config, "status_end")
    text = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.exists() else ""
    begin_count = text.count(begin)
    end_count = text.count(end)
    if begin_count != 1 or end_count != 1:
        with raw_path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\nexpected exactly one {begin} and one {end} marker; "
                f"found {begin_count} begin marker(s) and {end_count} end marker(s).\n"
            )
        return False
    start = text.index(begin) + len(begin)
    finish = text.index(end)
    if finish <= start:
        with raw_path.open("a", encoding="utf-8") as handle:
            handle.write("\nLite advice end marker appears before begin marker.\n")
        return False
    try:
        data = json.loads(text[start:finish].strip())
    except Exception as exc:  # noqa: BLE001
        with raw_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\nLite advice JSON is invalid: {exc}\n")
        return False
    write_json(output_path, data)
    return True


def validate_advice(packet_dir: Path, config: dict[str, Any]) -> bool:
    result = subprocess.run(
        [
            "python3",
            string_value(config, "validation_script"),
            "--advice",
            path_for(packet_dir, config, "output_name").as_posix(),
            "--inputs",
            path_for(packet_dir, config, "inputs_name").as_posix(),
            "--packet-id",
            string_value(config, "packet_id"),
            "--purpose",
            string_value(config, "purpose"),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
    )
    return result.returncode == 0


def run_packet(packet_dir: Path) -> int:
    config = read_json(packet_dir / CONFIG_NAME)
    if config.get("schema_version") != 1:
        raise SystemExit(f"{CONFIG_NAME} schema_version must be 1")
    if config.get("role") != "lite_advisor":
        raise SystemExit(f"{CONFIG_NAME} role must be 'lite_advisor'")

    inputs_path = path_for(packet_dir, config, "inputs_name")
    prompt_path = path_for(packet_dir, config, "prompt_name")
    task_path = path_for(packet_dir, config, "task_name")
    output_path = path_for(packet_dir, config, "output_name")
    raw_path = path_for(packet_dir, config, "raw_name")
    telemetry_path = path_for(packet_dir, config, "telemetry_name")
    inputs = read_json(inputs_path)

    for path in [output_path, raw_path, telemetry_path]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    gemini_path_value = inputs.get("gemini_path")
    gemini_message_key = "gemini_stale"
    if not isinstance(gemini_path_value, str) or not gemini_path_value.strip():
        gemini_message_key = "gemini_unavailable"
    checks = [
        (verify_inputs_current(config, inputs), terminal_message(config, "inputs_stale")),
        (verify_file_hash(prompt_path, inputs.get("prompt_sha256"), "prompt"), terminal_message(config, "prompt_stale")),
        (verify_file_hash(task_path, inputs.get("task_sha256"), "task"), terminal_message(config, "task_stale")),
        (verify_gemini_binary(inputs), terminal_message(config, gemini_message_key)),
    ]
    for (ok, detail), message in checks:
        if not ok:
            blocker = detail if detail.startswith(message) else f"{message} {detail}".strip()
            write_terminal_advice(packet_dir, config, "blocked", blocker)
            write_telemetry(packet_dir, config)
            return 0

    gemini_path = string_value(inputs, "gemini_path")
    command = [
        gemini_path,
        "--model",
        string_value(config, "model"),
        "--approval-mode",
        string_value(config, "approval_mode"),
        "--skip-trust",
        "--output-format",
        "text",
        "-p",
        string_value(config, "runner_prompt"),
    ]
    rc = run_with_timeout(config, command, cwd=Path(string_value(config, "base_dir")), stdin_path=prompt_path, stdout_path=raw_path)
    if rc != 0:
        write_terminal_advice(
            packet_dir,
            config,
            "blocked",
            terminal_message(config, "command_failed"),
        )
        write_telemetry(packet_dir, config)
        return 0

    if extract_advice_json(raw_path, output_path, config):
        write_telemetry(packet_dir, config)
        if validate_advice(packet_dir, config):
            return 0

    write_terminal_advice(packet_dir, config, "blocked", terminal_message(config, "invalid_output"))
    write_telemetry(packet_dir, config)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", required=True)
    args = parser.parse_args()
    packet_dir = Path(args.packet_dir).resolve()
    if not packet_dir.is_dir():
        raise SystemExit(f"--packet-dir must be an existing directory: {packet_dir}")
    return run_packet(packet_dir)


if __name__ == "__main__":
    raise SystemExit(main())
