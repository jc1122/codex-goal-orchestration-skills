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


_MODULE_CACHE: dict[Path, ModuleType] = {}
_SAFE_SKILL_DIRS = {
    "_goal_shared",
    "goal-branch-orchestrator",
    "goal-main-orchestrator",
    "goal-plan-amender",
    "goal-preflight",
}


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
