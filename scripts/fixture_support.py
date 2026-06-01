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
from typing import Any, Callable


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
CODEX_MINI_WORKER_COMMAND = "codex exec --ephemeral --ignore-user-config --ignore-rules -m gpt-5.4-mini -s workspace-write"


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


def runtime_packet_command(
    *,
    script: str,
    role: str,
    packet_id: str,
    branch: str,
    worktree: Path,
    out_dir: Path,
    task_file: Path,
    owned_files: list[str] | None = None,
    context_files: list[Path] | None = None,
    manifest: Path | None = None,
    pre_review_gate: Path | None = None,
    worker_route: list[str] | None = None,
    selection_reason: str | None = None,
    model_catalog: Path | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        "python3",
        script,
        "--role",
        role,
        "--packet-id",
        packet_id,
        "--branch",
        branch,
        "--worktree",
        worktree.as_posix(),
        "--out-dir",
        out_dir.as_posix(),
    ]
    if manifest is not None:
        command.extend(["--manifest", manifest.as_posix()])
    if pre_review_gate is not None:
        command.extend(["--pre-review-gate", pre_review_gate.as_posix()])
    for owned_file in owned_files or []:
        command.extend(["--owned-file", owned_file])
    for context_file in context_files or []:
        command.extend(["--context-file", context_file.as_posix()])
    command.extend(["--task-file", task_file.as_posix()])
    if worker_route:
        command.append("--worker-route")
        command.extend(worker_route)
    if selection_reason is not None:
        command.extend(["--selection-reason", selection_reason])
    if model_catalog is not None:
        command.extend(["--model-catalog", model_catalog.as_posix()])
    if extra_args:
        command.extend(extra_args)
    return command


def run_runtime_packet(
    *,
    root: Path,
    expect: int = 0,
    **packet_kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    return run_command(
        runtime_packet_command(**packet_kwargs),
        root=root,
        expect=expect,
    )


def launch_attempts(config: dict, label: str) -> list[dict]:
    attempts = config.get("attempts")
    if not isinstance(attempts, list):
        raise SystemExit(f"{label} launch-config attempts should be a list: {attempts!r}")
    if not all(isinstance(attempt_item, dict) for attempt_item in attempts):
        raise SystemExit(f"{label} launch-config attempts should contain only objects: {attempts!r}")
    return attempts


def launch_attempt_logs(config: dict, key: str, label: str) -> list[str]:
    logs: list[str] = []
    for attempt_item in launch_attempts(config, label):
        raw_logs = attempt_item.get(key, [])
        if not isinstance(raw_logs, list) or not all(isinstance(log, str) for log in raw_logs):
            raise SystemExit(f"{label} launch-config {key} should be a list of strings: {raw_logs!r}")
        logs.extend(raw_logs)
    return logs


def launch_attempts_by_provider(config: dict, provider: str, label: str) -> list[dict]:
    return [attempt_item for attempt_item in launch_attempts(config, label) if attempt_item.get("provider") == provider]


def assert_lean_codex_attempts(attempts: object, label: str, *, expected_count: int | None = None) -> list[dict]:
    if not isinstance(attempts, list) or not all(isinstance(attempt_item, dict) for attempt_item in attempts):
        raise SystemExit(f"{label} should be a list of launch-config attempt objects: {attempts!r}")
    if expected_count is not None and len(attempts) != expected_count:
        raise SystemExit(f"{label} count mismatch: expected {expected_count}, got {len(attempts)}: {attempts!r}")
    if not attempts:
        raise SystemExit(f"{label} should include at least one attempt")
    if any(attempt_item.get("ignore_user_config") is not True or attempt_item.get("ignore_rules") is not True for attempt_item in attempts):
        raise SystemExit(f"{label} should use lean Codex startup flags: {attempts!r}")
    return attempts


def assert_attempts_preserve_user_config(attempts: object, label: str) -> list[dict]:
    if not isinstance(attempts, list) or not all(isinstance(attempt_item, dict) for attempt_item in attempts):
        raise SystemExit(f"{label} should be a list of launch-config attempt objects: {attempts!r}")
    if any(
        "ignore_user_config" in attempt_item
        or "ignore_rules" in attempt_item
        or "--ignore-user-config" in str(attempt_item.get("command", ""))
        for attempt_item in attempts
    ):
        raise SystemExit(f"{label} must keep user config/search access: {attempts!r}")
    return attempts


def assert_codex_mini_worker_route(config: dict, selection_reason: str, label: str = "worker") -> None:
    if config.get("attempt_timeout_seconds") != 3600:
        raise SystemExit(f"{label} launch-config should preserve the 3600 second attempt timeout")
    if config.get("selected_ladder") != ["codex-mini"]:
        raise SystemExit(f"{label} launch-config ladder mismatch: {config.get('selected_ladder')!r}")
    if config.get("selection_reason") != selection_reason:
        raise SystemExit(f"{label} launch-config selection reason mismatch: {config.get('selection_reason')!r}")
    event_logs = launch_attempt_logs(config, "event_logs", label)
    probe_logs = launch_attempt_logs(config, "probe_logs", label)
    if event_logs != ["events-mini.jsonl"]:
        raise SystemExit(f"{label} launch-config event logs mismatch: {event_logs!r}")
    if probe_logs:
        raise SystemExit(f"{label} launch-config should not include probe logs for codex-mini-only route: {probe_logs!r}")
    if config.get("selected_commands") != [CODEX_MINI_WORKER_COMMAND]:
        raise SystemExit(f"{label} launch-config selected commands mismatch: {config.get('selected_commands')!r}")
    assert_lean_codex_attempts(config.get("attempts", []), f"{label} Codex attempt", expected_count=1)


def assert_mixed_worker_route(config: dict, label: str, *, selection_reason: str | None = None) -> None:
    expected_ladder = ["gemini-pro", "codex-spark", "codex-mini"]
    if config.get("selected_ladder") != expected_ladder:
        raise SystemExit(f"{label} launch-config route mismatch: {config.get('selected_ladder')!r}")
    if selection_reason is not None and config.get("selection_reason") != selection_reason:
        raise SystemExit(f"{label} launch-config selection reason mismatch: {config.get('selection_reason')!r}")
    event_logs = launch_attempt_logs(config, "event_logs", label)
    probe_logs = launch_attempt_logs(config, "probe_logs", label)
    if event_logs != ["events-gemini-pro.log", "events-spark.jsonl", "events-mini.jsonl"]:
        raise SystemExit(f"{label} launch-config event log mismatch: {event_logs!r}")
    if probe_logs != ["events-gemini-pro-probe.log"]:
        raise SystemExit(f"{label} launch-config probe log mismatch: {probe_logs!r}")
    codex_attempts = launch_attempts_by_provider(config, "codex", label)
    assert_lean_codex_attempts(codex_attempts, f"{label} Codex attempt", expected_count=2)


def assert_research_worker_preserves_user_config(
    config: dict,
    label: str = "research-worker",
    *,
    expected_event_logs: list[str] | None = None,
) -> None:
    if config.get("attempt_timeout_seconds") != 1200:
        raise SystemExit(f"{label} launch-config should preserve the 1200 second attempt timeout")
    if expected_event_logs is not None:
        event_logs = launch_attempt_logs(config, "event_logs", label)
        if event_logs != expected_event_logs:
            raise SystemExit(f"{label} launch-config event log mismatch: {event_logs!r}")
    assert_attempts_preserve_user_config(config.get("attempts", []), f"{label} attempts")


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
