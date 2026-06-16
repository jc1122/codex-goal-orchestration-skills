#!/usr/bin/env python3
"""Run compact Lite advisory packet launchers from packet-local config."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
import contextlib


CONFIG_NAME = "launch-config.json"
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
TIMEOUT_NOT_FOUND = "timeout command not found; refusing unbounded Lite advisor attempt.\n"
# Bridge adapter (opencode-worker-bridge): the Lite route delegates a deepseek
# launch through scripts/opencode_worker.py under permission-profile read-only
# and maps the file-backed goal-delegator-* artifacts onto telemetry. No USD.
BRIDGE_JOB_ENVELOPE_NAME = "job_envelope.json"
BRIDGE_WORKER_STATUS_NAME = "worker.status.json"
BRIDGE_SUPERVISOR_VERDICT_NAME = "supervisor_verdict.json"
BRIDGE_WORKER_STATE_NAME = "opencode-worker-state.json"
BRIDGE_PASS_STATUSES = frozenset({"passed", "completed", "done", "success"})
BRIDGE_DEFAULT_POOL_MAX_WORKERS = 4


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
        "timestamp": datetime.now(UTC).isoformat(),
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
    control_script = ""
    if inputs is not None and isinstance(inputs.get("bridge_control_script"), str):
        control_script = str(inputs.get("bridge_control_script"))
    if not control_script:
        control_script = optional_string_value(config, "bridge_control_script")
    control = control_script if control_script else "opencode_worker.py"
    return (
        f"python3 {control} delegate "
        f"--provider {string_value(attempt, 'provider_id') if attempt.get('provider_id') else 'deepseek'} "
        f"--model {string_value(config, 'model')} "
        f"--variant {string_value(config, 'variant')} "
        f"--permission-profile {string_value(config, 'permission_profile')}"
    )


def optional_string_value(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    return value if isinstance(value, str) else ""


def terminal_message(config: dict[str, Any], key: str) -> str:
    messages = config.get("terminal_messages")
    if isinstance(messages, dict):
        value = messages.get(key)
        if isinstance(value, str) and value:
            return value
    defaults = {
        "bridge_unavailable": "opencode-worker-bridge control script unavailable at packet creation path: ",
        "inputs_stale": "Lite advisor input files changed or became unavailable after packet creation.",
        "prompt_stale": "Lite advisor prompt.md changed or became unavailable after packet creation.",
        "task_stale": "Lite advisor task.md changed or became unavailable after packet creation.",
        "bridge_stale": "opencode-worker-bridge control script changed or could not be verified after packet creation.",
        "command_failed": "Lite advisor bridge delegate failed. Inspect the bridge run-dir artifacts for transport, model, permission, or validation errors.",
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
    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        command.extend(["--debug", "--debug-output", debug_name])
    subprocess.run(command, check=False)
    append_debug_event(packet_dir, config, {"phase": "telemetry", "event": "written"})


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


def verify_bridge_control(config: dict[str, Any], inputs: dict[str, Any]) -> tuple[bool, str]:
    control_script = inputs.get("bridge_control_script")
    if not isinstance(control_script, str) or not control_script.strip():
        return (
            False,
            f"opencode-worker-bridge control script unavailable at packet creation path: {control_script or ''}",
        )
    path = Path(control_script)
    if not path.is_absolute() or not path.exists() or path.name != "opencode_worker.py":
        return False, f"opencode-worker-bridge control script unavailable at packet creation path: {control_script}"
    expected_version = inputs.get("bridge_control_version")
    if not isinstance(expected_version, str) or not expected_version.strip() or expected_version == "unavailable":
        return False, "missing captured bridge control-script version in input-files.json"
    return True, ""


def resolve_bridge_control_path(config: dict[str, Any], inputs: dict[str, Any]) -> Path:
    control_script = inputs.get("bridge_control_script") or config.get("bridge_control_script")
    return Path(str(control_script))


def run_bridge_subcommand(
    config: dict[str, Any],
    control_path: Path,
    subcommand: str,
    extra_args: list[str],
    *,
    cwd: Path,
    stdout_path: Path,
) -> int:
    if shutil.which("timeout") is None:
        stdout_path.write_text(TIMEOUT_NOT_FOUND, encoding="utf-8")
        return 127
    command = ["python3", control_path.as_posix(), subcommand, *extra_args]
    full_command = [
        "timeout",
        "--foreground",
        f"--kill-after={int_value(config, 'timeout_kill_after_seconds')}s",
        f"{int_value(config, 'attempt_timeout_seconds')}s",
    ] + command
    with stdout_path.open("wb") as stdout:
        result = subprocess.run(
            full_command,
            cwd=cwd,
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


def read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _elapsed_ms_from_timestamps(timestamps: Any) -> int | None:
    if not isinstance(timestamps, dict):
        return None
    start = timestamps.get("started_at") or timestamps.get("created_at") or timestamps.get("start")
    end = (
        timestamps.get("completed_at")
        or timestamps.get("finished_at")
        or timestamps.get("end")
        or timestamps.get("updated_at")
    )
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    delta_ms = int(round((end_dt - start_dt).total_seconds() * 1000))
    return delta_ms if delta_ms >= 0 else None


def _bridge_usage(*sources: Any) -> dict[str, Any] | None:
    """Token-only usage passthrough from any bridge artifact. NEVER emits USD."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        usage = source.get("usage") or source.get("tokens") or source.get("token_usage")
        if isinstance(usage, dict) and usage:
            return {
                key: value
                for key, value in usage.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            } or None
    return None


def _bridge_assistant_text(*sources: Any) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("assistant_text", "output_text", "summary", "message", "final_message"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def map_bridge_artifacts(run_dir: Path) -> dict[str, Any]:
    """Map bridge goal-delegator-* artifacts onto the Lite runner contract.

    Mirrors B4's mapping: reads job_envelope.json / worker.status.json /
    supervisor_verdict.json and returns returncode + status + elapsed_ms +
    token usage (NEVER USD) + assistant text + route metadata.
    """
    job = read_optional_json(run_dir / BRIDGE_JOB_ENVELOPE_NAME)
    worker_status = read_optional_json(run_dir / BRIDGE_WORKER_STATUS_NAME)
    verdict = read_optional_json(run_dir / BRIDGE_SUPERVISOR_VERDICT_NAME)
    report = read_optional_json(run_dir / "delegation-report.json")

    status = None
    for source, key in ((verdict, "status"), (job, "status"), (worker_status, "lifecycle")):
        value = source.get(key) if isinstance(source, dict) else None
        if isinstance(value, str) and value.strip():
            status = value.strip()
            break
    if status is None:
        status = "unknown"
    passed = status.lower() in BRIDGE_PASS_STATUSES
    returncode = 0 if passed else 1

    route = job.get("route") if isinstance(job.get("route"), dict) else {}
    provider = route.get("provider") if isinstance(route.get("provider"), str) else None
    model = route.get("model") if isinstance(route.get("model"), str) else None
    variant = route.get("variant") if isinstance(route.get("variant"), str) else None
    elapsed_ms = _elapsed_ms_from_timestamps(job.get("timestamps"))
    usage = _bridge_usage(verdict, job, worker_status, report, route)
    assistant_text = _bridge_assistant_text(verdict, report, job, worker_status)
    return {
        "returncode": returncode,
        "status": status,
        "passed": passed,
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "assistant_text": assistant_text,
        "provider": provider,
        "model": model,
        "variant": variant,
    }


def write_bridge_telemetry_artifacts(packet_dir: Path, label: str, mapped: dict[str, Any]) -> Path:
    """Write the synthetic events-<label>.jsonl that extract_telemetry consumes.

    No USD/price keys are ever written; only token usage + timing + route.
    """
    event_path = packet_dir / f"events-{label}.jsonl"
    event_record: dict[str, Any] = {
        "elapsed_ms": mapped.get("elapsed_ms"),
        "output_nonempty": bool(str(mapped.get("assistant_text") or "").strip()),
        "usage": mapped.get("usage"),
        "provider": mapped.get("provider"),
        "model": mapped.get("model"),
        "variant": mapped.get("variant"),
        "status": mapped.get("status"),
    }
    event_path.write_text(json.dumps(event_record, separators=(",", ":")) + "\n", encoding="utf-8")
    return event_path


def run_bridge_delegate(
    config: dict[str, Any],
    inputs: dict[str, Any],
    *,
    packet_dir: Path,
    prompt_path: Path,
    raw_path: Path,
    label: str,
) -> tuple[int, dict[str, Any]]:
    """Drive pool-acquire -> start -> delegate -> stop -> pool-release on the bridge.

    Read-only (permission-profile read-only), single attempt, no worker
    status.json gate: a Lite advisor only routes context and can never satisfy a
    gate. The delegate's assistant text is written to advice.raw.txt for the
    existing marker parser. Returns (returncode, mapped_artifacts).
    """
    control_path = resolve_bridge_control_path(config, inputs)
    cwd = Path(string_value(config, "base_dir"))
    run_dir_rel = optional_string_value(config, "event_label") or label
    run_dir = packet_dir / "bridge" / run_dir_rel
    pool_dir = packet_dir / "bridge" / "pool"
    run_dir.mkdir(parents=True, exist_ok=True)
    pool_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / BRIDGE_WORKER_STATE_NAME
    task_path = run_dir / "task.md"
    task_path.write_text(prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "", encoding="utf-8")

    provider = optional_string_value(config, "provider") or "deepseek"
    model = string_value(config, "model")
    variant = string_value(config, "variant")
    profile = string_value(config, "permission_profile")
    worker_id = string_value(config, "packet_id")
    max_workers = BRIDGE_DEFAULT_POOL_MAX_WORKERS

    acquired = False
    rc = 1
    try:
        acquire_rc = run_bridge_subcommand(
            config,
            control_path,
            "pool-acquire",
            ["--pool-dir", pool_dir.as_posix(), "--max-workers", str(max_workers), "--worker-id", worker_id],
            cwd=cwd,
            stdout_path=run_dir / "pool-acquire.log",
        )
        if acquire_rc != 0:
            raw_path.write_text("bridge pool capacity limit reached; scheduler should refill later\n", encoding="utf-8")
            return acquire_rc, {"status": "blocked", "passed": False, "assistant_text": ""}
        acquired = True
        run_bridge_subcommand(
            config,
            control_path,
            "start",
            [
                "--state",
                state_path.as_posix(),
                "--cwd",
                cwd.as_posix(),
                "--pool-dir",
                pool_dir.as_posix(),
                "--pool-worker-id",
                worker_id,
            ],
            cwd=cwd,
            stdout_path=run_dir / "start.log",
        )
        rc = run_bridge_subcommand(
            config,
            control_path,
            "delegate",
            [
                "--state",
                state_path.as_posix(),
                "--run-dir",
                run_dir.as_posix(),
                "--job-id",
                worker_id,
                "--prompt-file",
                task_path.as_posix(),
                "--provider",
                provider,
                "--model",
                model,
                "--variant",
                variant,
                "--permission-profile",
                profile,
                "--report",
                (run_dir / "delegation-report.json").as_posix(),
            ],
            cwd=cwd,
            stdout_path=run_dir / "delegate.log",
        )
        run_bridge_subcommand(
            config,
            control_path,
            "stop",
            ["--state", state_path.as_posix(), "--run-dir", run_dir.as_posix()],
            cwd=cwd,
            stdout_path=run_dir / "stop.log",
        )
    finally:
        if acquired:
            run_bridge_subcommand(
                config,
                control_path,
                "pool-release",
                ["--pool-dir", pool_dir.as_posix(), "--worker-id", worker_id],
                cwd=cwd,
                stdout_path=run_dir / "pool-release.log",
            )

    mapped = map_bridge_artifacts(run_dir)
    write_bridge_telemetry_artifacts(packet_dir, label, mapped)
    assistant_text = str(mapped.get("assistant_text") or "")
    raw_path.write_text(assistant_text, encoding="utf-8")
    effective_rc = rc if rc != 0 else (0 if mapped.get("passed") else 1)
    return effective_rc, mapped


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

    debug_name = config.get("telemetry_debug_name")
    if isinstance(debug_name, str) and debug_name.strip():
        with contextlib.suppress(FileNotFoundError):
            (packet_dir / debug_name).unlink()
    for path in [output_path, raw_path, telemetry_path]:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    control_value = inputs.get("bridge_control_script")
    bridge_message_key = "bridge_stale"
    if not isinstance(control_value, str) or not control_value.strip():
        bridge_message_key = "bridge_unavailable"
    checks = [
        (verify_inputs_current(config, inputs), terminal_message(config, "inputs_stale")),
        (
            verify_file_hash(prompt_path, inputs.get("prompt_sha256"), "prompt"),
            terminal_message(config, "prompt_stale"),
        ),
        (verify_file_hash(task_path, inputs.get("task_sha256"), "task"), terminal_message(config, "task_stale")),
        (verify_bridge_control(config, inputs), terminal_message(config, bridge_message_key)),
    ]
    for (ok, detail), message in checks:
        if not ok:
            blocker = detail if detail.startswith(message) else f"{message} {detail}".strip()
            write_terminal_advice(packet_dir, config, "blocked", blocker)
            write_telemetry(packet_dir, config)
            return 0

    label = optional_string_value(config, "event_label") or "ds-flash-max"
    rc, _mapped = run_bridge_delegate(
        config,
        inputs,
        packet_dir=packet_dir,
        prompt_path=prompt_path,
        raw_path=raw_path,
        label=label,
    )
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
    config = read_json(packet_dir / CONFIG_NAME)
    started = time.monotonic()
    append_debug_event(packet_dir, config, {"phase": "lite_advisor", "event": "start"})
    try:
        rc = run_packet(packet_dir)
    except BaseException:
        append_debug_event(
            packet_dir,
            config,
            {
                "phase": "lite_advisor",
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
            "phase": "lite_advisor",
            "event": "end",
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": "ok" if rc == 0 else "nonzero",
            "exit_status": rc,
        },
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
