#!/usr/bin/env python3
"""Shared helpers for deterministic fixture and smoke scripts."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable


_MODULE_CACHE: dict[Path, ModuleType] = {}
_SAFE_SKILL_DIRS = {
    "_goal_shared",
    "goal-branch-orchestrator",
    "goal-main-orchestrator",
    "goal-plan-amender",
    "goal-preflight",
}
OFFLINE_GEMINI_PATH = "/usr/bin/gemini-fixture"
OFFLINE_GEMINI_VERSION = "gemini-fixture 0.0.0"
OFFLINE_GEMINI_SHA256 = "sha256:" + ("0" * 64)


@contextlib.contextmanager
def _temporary_process_state(
    *,
    argv: list[str],
    cwd: Path,
    env: dict[str, str] | None,
) -> object:
    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    old_env = os.environ.copy()
    try:
        sys.argv = argv[:]
        os.chdir(cwd)
        if env is not None:
            os.environ.clear()
            os.environ.update(env)
        yield
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


def _script_path(command: list[str], *, root: Path, cwd: Path) -> Path | None:
    if len(command) < 2:
        return None
    executable = Path(command[0]).name
    if executable not in {"python", "python3"} and not executable.startswith("python3."):
        return None
    raw = Path(command[1])
    candidates = [raw] if raw.is_absolute() else [cwd / raw, root / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.suffix == ".py":
            if _is_safe_repo_or_installed_skill_script(resolved, root):
                return resolved
    return None


def _is_safe_repo_or_installed_skill_script(script: Path, root: Path) -> bool:
    try:
        script.relative_to(root)
        return True
    except ValueError:
        pass
    parts = script.parts
    for index, part in enumerate(parts[:-2]):
        if part == "skills" and parts[index + 1] in _SAFE_SKILL_DIRS and parts[index + 2] == "scripts":
            return True
    return False


def _load_module(script: Path) -> ModuleType:
    cached = _MODULE_CACHE.get(script)
    if cached is not None:
        return cached
    module_name = f"_goal_fixture_cli_{hashlib.sha256(script.as_posix().encode()).hexdigest()}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot import fixture script: {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    old_path = sys.path[:]
    try:
        sys.path.insert(0, script.parent.as_posix())
        spec.loader.exec_module(module)
    finally:
        sys.path = old_path
    _MODULE_CACHE[script] = module
    return module


def _run_python_cli_in_process(
    command: list[str],
    *,
    root: Path,
    cwd: Path,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str] | None:
    if os.environ.get("GOAL_FIXTURE_SUBPROCESS_ONLY") == "1":
        return None
    script = _script_path(command, root=root, cwd=cwd)
    if script is None:
        return None
    module = _load_module(script)
    main = getattr(module, "main", None)
    if not callable(main):
        return None

    stdout = io.StringIO()
    argv = [script.as_posix(), *command[2:]]
    code = 0
    old_path = sys.path[:]
    with _temporary_process_state(argv=argv, cwd=cwd, env=env), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
        try:
            sys.path.insert(0, script.parent.as_posix())
            result = main()
            if isinstance(result, int):
                code = result
        except SystemExit as exc:
            if isinstance(exc.code, int):
                code = exc.code
            elif exc.code is None:
                code = 0
            else:
                print(exc.code)
                code = 1
        finally:
            sys.path = old_path
    return subprocess.CompletedProcess(command, code, stdout.getvalue())


def run_command(
    command: list[str],
    *,
    root: Path,
    expect: int = 0,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    working_dir = (cwd or root).resolve()
    result = _run_python_cli_in_process(command, root=root.resolve(), cwd=working_dir, env=env)
    if result is None:
        result = subprocess.run(
            command,
            cwd=working_dir,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if result.returncode != expect:
        print(f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(1)
    return result


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def offline_gemini_env(env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    merged.update(
        {
            "GOAL_LITE_OFFLINE_GEMINI_METADATA": "1",
            "GOAL_LITE_GEMINI_PATH": OFFLINE_GEMINI_PATH,
            "GOAL_LITE_GEMINI_VERSION": OFFLINE_GEMINI_VERSION,
            "GOAL_LITE_GEMINI_SHA256": OFFLINE_GEMINI_SHA256,
        }
    )
    return merged


def make_scheduler_event(runtime_ref: str) -> Callable[..., dict]:
    def scheduler_event(seq: int, event: str, **kwargs) -> dict:
        return {
            "seq": seq,
            "timestamp": f"2026-05-29T00:00:{seq:02d}Z",
            "runtime_ref": runtime_ref,
            "event": event,
            **kwargs,
        }

    return scheduler_event


def attempt(
    *,
    alias: str,
    provider: str,
    model: str,
    command: str,
    timeout_seconds: int,
    called: bool,
    accepted: bool,
    effort: str | None = None,
) -> dict:
    return {
        "alias": alias,
        "provider": provider,
        "model": model,
        "effort": effort,
        "command": command,
        "timeout_seconds": timeout_seconds,
        "called": called,
        "accepted": accepted,
        "event_logs": [],
        "probe_logs": [],
        "usage": None,
    }


def telemetry(packet_id: str, role: str, output_name: str, *, accepted_alias: str | None, attempts: list[dict]) -> dict:
    called_count = sum(1 for item in attempts if item.get("called") is True)
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": role,
        "output_artifact": output_name,
        "prompt_artifact": "prompt.md",
        "prompt_chars": 1,
        "prompt_bytes": 1,
        "output_chars": 1,
        "output_bytes": 1,
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "accepted_alias": accepted_alias,
        "attempts": attempts,
        "totals": {
            "attempts_declared": len(attempts),
            "attempts_called": called_count,
            "event_log_chars": 0,
            "event_log_bytes": 0,
            "known_usage": None,
        },
    }


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"{label} missing expected text: {needle}")


def assert_all_contains(text: str, needles: list[str], label: str) -> None:
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise SystemExit(f"{label} missing expected text: {', '.join(missing)}")


def assert_any_contains(text: str, needles: list[str], label: str) -> None:
    if not any(needle in text for needle in needles):
        raise SystemExit(f"{label} missing any expected text: {', '.join(needles)}")


def assert_not_contains(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise SystemExit(f"{label} contains forbidden text: {needle}")


def assert_shell_syntax(path: Path) -> None:
    result = subprocess.run(
        ["bash", "-n", path.as_posix()],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(f"shell syntax check failed: {path}")


def assert_compact_runtime_launcher(
    packet_dir: Path,
    role: str,
    timeout_seconds: int | None = None,
) -> dict:
    launch = (packet_dir / "launch.sh").read_text(encoding="utf-8")
    assert_shell_syntax(packet_dir / "launch.sh")
    assert_contains(launch, "runtime_packet_runner.py", f"{role} launcher")
    if len(launch) > 800:
        raise SystemExit(f"{role} launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != role:
        raise SystemExit(f"{role} launch-config role mismatch: {config.get('role')!r}")
    if timeout_seconds is not None and config.get("attempt_timeout_seconds") != timeout_seconds:
        raise SystemExit(f"{role} launch-config timeout mismatch: {config.get('attempt_timeout_seconds')!r}")
    return config


def assert_compact_lite_launcher(packet_dir: Path) -> dict:
    launch = (packet_dir / "launch.sh").read_text(encoding="utf-8")
    assert_shell_syntax(packet_dir / "launch.sh")
    assert_contains(launch, "runtime_lite_runner.py", "Lite launcher")
    assert_not_contains(launch, '-p "$(cat "$prompt_path")"', "Lite launcher")
    if len(launch) > 800:
        raise SystemExit(f"Lite launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != "lite_advisor":
        raise SystemExit(f"Lite launch-config role mismatch: {config.get('role')!r}")
    if config.get("attempt_timeout_seconds") != 600:
        raise SystemExit(f"Lite launch-config timeout mismatch: {config.get('attempt_timeout_seconds')!r}")
    if config.get("timeout_kill_after_seconds") != 30:
        raise SystemExit(f"Lite launch-config kill-after mismatch: {config.get('timeout_kill_after_seconds')!r}")
    if config.get("telemetry_name") != "telemetry.json":
        raise SystemExit(f"Lite launch-config telemetry name mismatch: {config.get('telemetry_name')!r}")
    if config.get("runner_prompt") != "Follow the complete Lite advisory packet instructions provided on stdin.":
        raise SystemExit("Lite launch-config should preserve the stdin runner prompt")
    if not isinstance(config.get("avoids_action"), str) or not config.get("avoids_action"):
        raise SystemExit("Lite launch-config missing avoids_action")
    if not isinstance(config.get("expected_savings_reason"), str) or not config.get("expected_savings_reason"):
        raise SystemExit("Lite launch-config missing expected_savings_reason")
    if not str(config.get("validation_script", "")).endswith("validate_lite_advice.py"):
        raise SystemExit(f"Lite launch-config validation script mismatch: {config.get('validation_script')!r}")
    if not str(config.get("telemetry_script", "")).endswith("extract_telemetry.py"):
        raise SystemExit(f"Lite launch-config telemetry script mismatch: {config.get('telemetry_script')!r}")
    if config.get("status_begin") != "BEGIN_LITE_ADVICE_JSON" or config.get("status_end") != "END_LITE_ADVICE_JSON":
        raise SystemExit("Lite launch-config marker mismatch")
    attempts = config.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise SystemExit(f"Lite launch-config should contain exactly one attempt: {attempts!r}")
    attempt_item = attempts[0]
    if attempt_item.get("alias") != "gemini-lite":
        raise SystemExit(f"Lite launch-config attempt alias mismatch: {attempt_item.get('alias')!r}")
    if attempt_item.get("event_logs") != ["advice.raw.txt"]:
        raise SystemExit(f"Lite launch-config event logs mismatch: {attempt_item.get('event_logs')!r}")
    if attempt_item.get("timeout_seconds") != 600:
        raise SystemExit(f"Lite attempt timeout mismatch: {attempt_item.get('timeout_seconds')!r}")
    terminal_messages = config.get("terminal_messages")
    if not isinstance(terminal_messages, dict) or "invalid_output" not in terminal_messages:
        raise SystemExit("Lite launch-config terminal messages missing")
    return config


def assert_compact_audit_launcher(packet_dir: Path) -> dict:
    launch = (packet_dir / "launch.sh").read_text(encoding="utf-8")
    assert_shell_syntax(packet_dir / "launch.sh")
    assert_contains(launch, "runtime_prompt_audit_runner.py", "audit launcher")
    if len(launch) > 800:
        raise SystemExit(f"audit launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != "prompt-auditor":
        raise SystemExit(f"audit launch-config role mismatch: {config.get('role')!r}")
    if config.get("attempt_timeout_seconds") != 1200:
        raise SystemExit(f"audit launch-config timeout mismatch: {config.get('attempt_timeout_seconds')!r}")
    if config.get("timeout_kill_after_seconds") != 30:
        raise SystemExit(f"audit launch-config kill-after mismatch: {config.get('timeout_kill_after_seconds')!r}")
    if config.get("telemetry_name") != "telemetry.json":
        raise SystemExit(f"audit launch-config telemetry name mismatch: {config.get('telemetry_name')!r}")
    if not str(config.get("validation_script", "")).endswith("validate_prompt_audit.py"):
        raise SystemExit(f"audit launch-config validation script mismatch: {config.get('validation_script')!r}")
    if not str(config.get("telemetry_script", "")).endswith("extract_telemetry.py"):
        raise SystemExit(f"audit launch-config telemetry script mismatch: {config.get('telemetry_script')!r}")
    attempts = config.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 2:
        raise SystemExit(f"audit launch-config should contain two attempts: {attempts!r}")
    aliases = [attempt_item.get("alias") for attempt_item in attempts if isinstance(attempt_item, dict)]
    if aliases != ["gpt-5.5", "gpt-5.4"]:
        raise SystemExit(f"audit launch-config aliases mismatch: {aliases!r}")
    event_logs = [
        log
        for attempt_item in attempts
        if isinstance(attempt_item, dict)
        for log in attempt_item.get("event_logs", [])
    ]
    if event_logs != ["events-primary.jsonl", "events-fallback.jsonl"]:
        raise SystemExit(f"audit launch-config event logs mismatch: {event_logs!r}")
    terminal_messages = config.get("terminal_messages")
    if not isinstance(terminal_messages, dict) or "invalid_output" not in terminal_messages:
        raise SystemExit("audit launch-config terminal messages missing")
    return config
