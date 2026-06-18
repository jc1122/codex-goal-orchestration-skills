#!/usr/bin/env python3
"""Create, run, validate, and summarize the main prompt-audit phase."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CREATE_AUDIT_PACKET = SCRIPT_DIR / "create_audit_packet.py"
DETERMINISTIC_PROMPT_AUDIT = SCRIPT_DIR / "deterministic_prompt_audit.py"
VALIDATE_PROMPT_AUDIT = SCRIPT_DIR / "validate_prompt_audit.py"
ACTIVE_PROCESS: subprocess.Popen[str] | None = None


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


def tail_text(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def resolve_path(value: str, label: str, *, must_exist: bool) -> Path:
    path = Path(value).expanduser().resolve()
    if must_exist and not path.exists():
        raise SystemExit(f"{label} does not exist: {path}")
    return path


def command_record(command: list[str], rc: int, stdout: str, stderr: str, *, timed_out: bool = False) -> dict[str, Any]:
    return {
        "command": command,
        "returncode": rc,
        "timed_out": timed_out,
        "stdout_tail": tail_text(stdout),
        "stderr_tail": tail_text(stderr),
    }


def terminate_process(process: subprocess.Popen[str], *, kill_after_seconds: int = 5) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        process.terminate()
    try:
        process.wait(timeout=kill_after_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            process.kill()


def handle_interrupt(signum: int, _frame: object) -> None:
    process = ACTIVE_PROCESS
    if process is not None:
        terminate_process(process)
    raise SystemExit(128 + signum)


def run_command(command: list[str], *, cwd: Path | None = None, timeout_seconds: int | None = None) -> dict[str, Any]:
    global ACTIVE_PROCESS
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    ACTIVE_PROCESS = process
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return command_record(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        terminate_process(process)
        stdout, stderr = process.communicate()
        return command_record(command, 124, stdout, stderr, timed_out=True)
    finally:
        ACTIVE_PROCESS = None


def validation_command(audit_path: Path, manifest_path: Path, repo_root: Path, *, require_pass: bool) -> list[str]:
    command = [
        sys.executable,
        VALIDATE_PROMPT_AUDIT.as_posix(),
        "--audit",
        audit_path.as_posix(),
        "--manifest",
        manifest_path.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--json",
    ]
    if require_pass:
        command.append("--require-pass")
    return command


def parsed_validation(command_result: dict[str, Any]) -> dict[str, Any] | None:
    stdout = command_result.get("stdout_tail")
    if not isinstance(stdout, str) or not stdout.strip():
        return None
    try:
        data = json.loads(stdout)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def audit_snapshot(audit_path: Path) -> dict[str, Any]:
    if not audit_path.exists():
        return {"exists": False}
    audit = read_json(audit_path)
    return {
        "exists": True,
        "status": audit.get("status"),
        "can_start": audit.get("can_start"),
        "summary": audit.get("summary"),
        "defect_count": len(audit.get("defects", [])) if isinstance(audit.get("defects"), list) else None,
        "missing_dod_count": (
            len(audit.get("missing_dod_items", [])) if isinstance(audit.get("missing_dod_items"), list) else None
        ),
    }


def telemetry_snapshot(telemetry_path: Path) -> dict[str, Any]:
    if not telemetry_path.exists():
        return {"exists": False}
    telemetry = read_json(telemetry_path)
    attempts = telemetry.get("attempts")
    aliases = []
    called = []
    accepted = []
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            alias = attempt.get("alias")
            if isinstance(alias, str):
                aliases.append(alias)
                if attempt.get("called") is True:
                    called.append(alias)
                if attempt.get("accepted") is True:
                    accepted.append(alias)
    return {
        "exists": True,
        "aliases": aliases,
        "called": called,
        "accepted": accepted,
    }


def phase_status(
    *,
    create_rc: int,
    launch_rc: int | None,
    basic_validation_rc: int | None,
    require_pass_validation_rc: int | None,
    audit: dict[str, Any],
) -> str:
    if create_rc != 0:
        return "failed"
    if basic_validation_rc == 0 and require_pass_validation_rc == 0:
        return "pass"
    if basic_validation_rc == 0 and audit.get("status") == "blocked":
        return "blocked"
    if launch_rc is not None and launch_rc in {124, 130, 143} and basic_validation_rc == 0:
        return "blocked"
    return "failed"


def next_action(status: str) -> str:
    if status == "pass":
        return "prompt audit passed; branch scheduling may start"
    if status == "blocked":
        return "do not create branches; preserve prompt-audit-phase.json, prompt-audit.json, telemetry.json, and event logs"
    return "do not create branches; inspect prompt-audit-phase.json and repair only reported deterministic defects"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--require-pass", action="store_true", help="Return success only when prompt audit can start branches."
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Use deterministic bundle and prompt checks instead of model audit attempts.",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--attempt-timeout-seconds",
        type=int,
        default=None,
        help="Override per-model audit attempt timeout when creating the packet.",
    )
    parser.add_argument(
        "--launch-timeout-seconds",
        type=int,
        default=None,
        help="Optional outer cap for audit/launch.sh; normally rely on packet attempt timeouts.",
    )
    args = parser.parse_args()
    if args.attempt_timeout_seconds is not None and args.attempt_timeout_seconds <= 0:
        raise SystemExit("--attempt-timeout-seconds must be positive")
    if args.launch_timeout_seconds is not None and args.launch_timeout_seconds <= 0:
        raise SystemExit("--launch-timeout-seconds must be positive")

    signal.signal(signal.SIGTERM, handle_interrupt)
    signal.signal(signal.SIGINT, handle_interrupt)

    manifest_path = resolve_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_path(args.repo_root, "--repo-root", must_exist=True)
    audit_dir = resolve_path(args.audit_dir, "--audit-dir", must_exist=False)
    phase_report_path = audit_dir / "prompt-audit-phase.json"
    audit_path = audit_dir / "prompt-audit.json"
    telemetry_path = audit_dir / "telemetry.json"
    launch_path = audit_dir / "launch.sh"

    if audit_path.exists() and not args.replace:
        basic_validation = run_command(validation_command(audit_path, manifest_path, repo_root, require_pass=False))
        require_pass_validation = run_command(
            validation_command(audit_path, manifest_path, repo_root, require_pass=True)
        )
        audit = audit_snapshot(audit_path)
        telemetry = telemetry_snapshot(telemetry_path)
        status = phase_status(
            create_rc=0,
            launch_rc=None,
            basic_validation_rc=int(basic_validation["returncode"]),
            require_pass_validation_rc=int(require_pass_validation["returncode"]),
            audit=audit,
        )
        commands: dict[str, Any] = {
            "reuse_existing_audit": {
                "status": "attempted",
                "reason": "prompt-audit.json already exists and --replace was not requested",
            },
            "create": None,
            "deterministic_audit": None,
            "launch": None,
            "validate": basic_validation,
            "validate_require_pass": require_pass_validation,
        }
        result = {
            "schema_version": 1,
            "status": status,
            "manifest": manifest_path.as_posix(),
            "repo_root": repo_root.as_posix(),
            "audit_dir": audit_dir.as_posix(),
            "audit": audit,
            "telemetry": telemetry,
            "validation": {
                "basic": parsed_validation(basic_validation),
                "require_pass": parsed_validation(require_pass_validation),
            },
            "commands": commands,
            "next_action": next_action(status),
        }
        audit_dir.mkdir(parents=True, exist_ok=True)
        write_json(phase_report_path, result)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"status={status}")
            print(f"phase_report={phase_report_path}")
            print(f"next_action={result['next_action']}")
        # A blocked or failed audit must never exit 0: a caller gating only on the
        # exit code would otherwise proceed past a blocked audit. --require-pass was
        # already strict (non-pass -> 1); make the default fail closed too.
        return 0 if status == "pass" else 1

    create_command = [
        sys.executable,
        CREATE_AUDIT_PACKET.as_posix(),
        "--manifest",
        manifest_path.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--out-dir",
        audit_dir.as_posix(),
    ]
    if args.replace:
        create_command.append("--replace")
    if args.attempt_timeout_seconds is not None:
        create_command.extend(["--attempt-timeout-seconds", str(args.attempt_timeout_seconds)])

    commands: dict[str, Any] = {"create": run_command(create_command)}
    deterministic_result: dict[str, Any] | None = None
    launch_result: dict[str, Any] | None = None
    basic_validation: dict[str, Any] | None = None
    require_pass_validation: dict[str, Any] | None = None

    if commands["create"]["returncode"] == 0 and args.deterministic:
        deterministic_command = [
            sys.executable,
            DETERMINISTIC_PROMPT_AUDIT.as_posix(),
            "--manifest",
            manifest_path.as_posix(),
            "--repo-root",
            repo_root.as_posix(),
            "--audit-dir",
            audit_dir.as_posix(),
        ]
        deterministic_result = run_command(deterministic_command)
        commands["deterministic_audit"] = deterministic_result
        commands["launch"] = None
    elif commands["create"]["returncode"] == 0 and launch_path.exists():
        launch_result = run_command(
            [launch_path.as_posix()], cwd=audit_dir, timeout_seconds=args.launch_timeout_seconds
        )
        commands["launch"] = launch_result
    else:
        commands["deterministic_audit"] = None
        commands["launch"] = None

    if audit_path.exists():
        basic_validation = run_command(validation_command(audit_path, manifest_path, repo_root, require_pass=False))
        commands["validate"] = basic_validation
        require_pass_validation = run_command(
            validation_command(audit_path, manifest_path, repo_root, require_pass=True)
        )
        commands["validate_require_pass"] = require_pass_validation
    else:
        commands["validate"] = None
        commands["validate_require_pass"] = None

    audit = audit_snapshot(audit_path)
    telemetry = telemetry_snapshot(telemetry_path)
    status = phase_status(
        create_rc=int(commands["create"]["returncode"]),
        launch_rc=int(launch_result["returncode"]) if launch_result is not None else None,
        basic_validation_rc=int(basic_validation["returncode"]) if basic_validation is not None else None,
        require_pass_validation_rc=(
            int(require_pass_validation["returncode"]) if require_pass_validation is not None else None
        ),
        audit=audit,
    )
    result = {
        "schema_version": 1,
        "status": status,
        "manifest": manifest_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "audit_dir": audit_dir.as_posix(),
        "audit": audit,
        "telemetry": telemetry,
        "validation": {
            "basic": parsed_validation(basic_validation) if basic_validation is not None else None,
            "require_pass": (
                parsed_validation(require_pass_validation) if require_pass_validation is not None else None
            ),
        },
        "commands": commands,
        "next_action": next_action(status),
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    write_json(phase_report_path, result)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={status}")
        print(f"phase_report={phase_report_path}")
        print(f"next_action={result['next_action']}")
    # A blocked or failed audit must never exit 0 (mirrors the reuse path above): a caller
    # gating only on the exit code would otherwise proceed past a blocked audit.
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
