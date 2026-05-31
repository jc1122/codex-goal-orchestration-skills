#!/usr/bin/env python3
"""Run compact runtime packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


TIMEOUT_NOT_FOUND = "timeout command not found; refusing unbounded {role} attempt.\n"
CONFIG_NAME = "launch-config.json"


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


def clean_outputs(packet_dir: Path, output_name: str, attempts: list[dict[str, Any]]) -> None:
    remove_if_exists(packet_dir / output_name)
    remove_if_exists(packet_dir / "telemetry.json")
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


def event_label(attempt: dict[str, Any], fallback: str) -> str:
    logs = attempt.get("event_logs")
    if isinstance(logs, list) and logs:
        first = logs[0]
        if isinstance(first, str) and first.startswith("events-") and first.endswith(".jsonl"):
            return first.removeprefix("events-").removesuffix(".jsonl")
    alias = attempt.get("alias")
    if isinstance(alias, str) and alias:
        return alias.replace(".", "-")
    return fallback


def run_model(
    *,
    packet_dir: Path,
    config: dict[str, Any],
    attempt: dict[str, Any],
    label: str,
) -> int:
    role = string_value(config, "role")
    worktree = string_value(config, "worktree")
    schema_name = string_value(config, "schema_name")
    output_name = string_value(config, "output_name")
    timeout_seconds = int_value(config, "attempt_timeout_seconds")
    kill_after_seconds = int_value(config, "timeout_kill_after_seconds")
    model = attempt.get("model")
    if not isinstance(model, str) or not model:
        raise SystemExit(f"{CONFIG_NAME} attempt missing model")
    event_path = packet_dir / f"events-{label}.jsonl"
    prompt_path = packet_dir / "prompt.md"
    if shutil.which("timeout") is None:
        event_path.write_text(TIMEOUT_NOT_FOUND.format(role=role), encoding="utf-8")
        return 127
    command = [
        "timeout",
        "--foreground",
        f"--kill-after={kill_after_seconds}s",
        f"{timeout_seconds}s",
    ]
    if role == "research-worker":
        command.extend(["codex", "--search", "exec", "--ephemeral"])
    else:
        command.extend(["codex", "exec", "--ephemeral"])
    command.extend(
        [
            "-m",
            model,
            "-C",
            worktree,
            "-s",
            string_value(config, "sandbox"),
            "--json",
            "--output-schema",
            (packet_dir / schema_name).as_posix(),
            "-o",
            (packet_dir / output_name).as_posix(),
            "-",
        ]
    )
    with prompt_path.open("rb") as stdin, event_path.open("wb") as stdout:
        result = subprocess.run(
            command,
            cwd=packet_dir,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return result.returncode


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
    subprocess.run(command, check=False)


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


def write_terminal(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    role = string_value(config, "role")
    if role == "research-worker":
        write_terminal_research(packet_dir, config, message)
    elif role == "reviewer":
        write_terminal_review(packet_dir, config, message)
    else:
        raise SystemExit(f"unsupported compact runner role: {role}")


def run_packet(packet_dir: Path) -> int:
    config = read_json(packet_dir / CONFIG_NAME)
    if config.get("schema_version") != 1:
        raise SystemExit(f"{CONFIG_NAME} schema_version must be 1")
    role = string_value(config, "role")
    if role not in {"research-worker", "reviewer"}:
        raise SystemExit(f"unsupported compact runner role: {role}")
    output_name = string_value(config, "output_name")
    output_path = packet_dir / output_name
    attempts = list_value(config, "attempts")
    check_worktree(string_value(config, "worktree"))
    clean_outputs(packet_dir, output_name, attempts)
    for index, attempt in enumerate(attempts):
        label = event_label(attempt, f"attempt-{index + 1}")
        if run_model(packet_dir=packet_dir, config=config, attempt=attempt, label=label) == 0:
            write_telemetry(packet_dir, config)
            return 0
        if output_path.exists() and output_path.stat().st_size > 0:
            write_telemetry(packet_dir, config)
            return 1
    message = string_value(config, "terminal_message")
    write_terminal(packet_dir, config, message)
    write_telemetry(packet_dir, config)
    return 1


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
