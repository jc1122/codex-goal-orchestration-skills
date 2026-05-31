#!/usr/bin/env python3
"""Run compact runtime packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import glob
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


TIMEOUT_NOT_FOUND = "timeout command not found; refusing unbounded {role} attempt.\n"
CONFIG_NAME = "launch-config.json"
WORKER_STATUS_BEGIN = "BEGIN_WORKER_STATUS_JSON"
WORKER_STATUS_END = "END_WORKER_STATUS_JSON"


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


def clean_outputs(packet_dir: Path, output_name: str, attempts: list[dict[str, Any]]) -> None:
    remove_if_exists(packet_dir / output_name)
    remove_if_exists(packet_dir / "telemetry.json")
    remove_if_exists(packet_dir / "fallback.blocked.txt")
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


def is_worktree_dirty(worktree: str) -> bool:
    try:
        status = subprocess.run(
            ["git", "-C", worktree, "status", "--porcelain"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(status.stdout.strip())


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
        path = line[3:] if len(line) > 3 and line[2] == " " else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed_files.append(path)
    return changed_files


def write_terminal_worker(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    output_path = packet_dir / string_value(config, "output_name")
    data = {
        "packet_id": string_value(config, "packet_id"),
        "role": "worker",
        "status": "blocked",
        "branch": string_value(config, "branch"),
        "worktree": string_value(config, "worktree"),
        "selected_ladder": config.get("selected_ladder", []),
        "selection_reason": config.get("selection_reason", ""),
        "changed_files": extract_changed_files(string_value(config, "worktree")),
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


def write_terminal(packet_dir: Path, config: dict[str, Any], message: str) -> None:
    role = string_value(config, "role")
    if role == "research-worker":
        write_terminal_research(packet_dir, config, message)
    elif role == "reviewer":
        write_terminal_review(packet_dir, config, message)
    elif role == "worker":
        write_terminal_worker(packet_dir, config, message)
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
    subprocess.run(command, check=False)


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
    command = [
        string_value(config, "gemini_command"),
        "--model",
        model,
        "--approval-mode",
        approval,
        "--skip-trust",
        "-p",
        str(config.get("worker_prompt", "Follow the complete worker packet instructions provided on stdin.")),
    ]
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
    return 0 if extract_status_json(packet_dir, schema_path, output_path, config) else 1


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
    return 0 if extract_status_json(packet_dir, schema_path, output_path, config) else 1


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
    return run_with_timeout(
        command=command,
        timeout_seconds=timeout_seconds,
        kill_after_seconds=kill_after_seconds,
        role=string_value(config, "role"),
        cwd=str(packet_dir),
        stdin_data=prompt_path.read_bytes(),
        stdout_path=event_path,
    )


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


def extract_status_json(
    packet_dir: Path,
    schema_path: Path,
    raw_path: Path,
    config: dict[str, Any],
) -> bool:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    marker_block = string_value(config.get("status_markers", {}), "begin") if isinstance(config.get("status_markers"), dict) else WORKER_STATUS_BEGIN
    marker_end = string_value(config.get("status_markers", {}), "end") if isinstance(config.get("status_markers"), dict) else WORKER_STATUS_END
    output_path = packet_dir / string_value(config, "output_name")
    sources: list[tuple[str, str]] = [("raw output", raw_path.read_text(encoding="utf-8", errors="replace"))]
    jsonl_parts: list[str] = []
    for line in sources[0][1].splitlines():
        try:
            data = json.loads(line)
        except Exception:
            continue
        jsonl_parts.extend(collect_strings(data))
    if jsonl_parts:
        sources.append(("decoded JSONL strings", "\n".join(jsonl_parts)))

    source_errors: list[str] = []
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
            if data.get("status") == "success":
                data["status"] = "pass"
            validate_instance(data, schema)
        except Exception as exc:
            source_errors.append(f"{source_name}: invalid marked worker status JSON: {exc}")
            continue
        output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return True

    for message in source_errors:
        print(message, file=sys.stderr)
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
    provider = attempt.get("provider")
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
    clean_outputs(packet_dir, output_name, attempts)

    if role == "worker":
        for index, attempt in enumerate(attempts):
            rc, _ = run_worker_attempt(
                packet_dir=packet_dir,
                config=config,
                attempt=attempt,
                attempt_index=index,
                schema_name=schema_name,
                output_name=output_name,
                worktree=worktree,
            )
            if rc == 0:
                write_telemetry(packet_dir, config)
                return 0
            if output_path.exists() and output_path.stat().st_size > 0:
                write_telemetry(packet_dir, config)
                return 1
            if is_worktree_dirty(worktree):
                label = event_label(attempt, f"attempt-{index + 1}")
                suffix = "refusing fallback in same worktree." if index < len(attempts) - 1 else "no fallback remains."
                message = f"{label} failed after leaving dirty worktree; {suffix}"
                (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
                write_terminal(packet_dir, config, message)
                write_telemetry(packet_dir, config)
                return 2
        if is_worktree_dirty(worktree):
            message = "worker failed after leaving dirty worktree; no fallback remains."
            (packet_dir / "fallback.blocked.txt").write_text(message + "\n", encoding="utf-8")
            write_terminal(packet_dir, config, message)
            write_telemetry(packet_dir, config)
            return 2
        message = string_value(config, "terminal_message")
        write_terminal(packet_dir, config, message)
        write_telemetry(packet_dir, config)
        return 1

    for index, attempt in enumerate(attempts):
        _label = event_label(attempt, f"attempt-{index + 1}")
        provider = attempt.get("provider")
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
        else:
            raise SystemExit(f"{CONFIG_NAME} unsupported {role} provider: {provider}")
        if rc == 0:
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
