#!/usr/bin/env python3
"""Dispatch to the shared goal orchestration implementation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_shared():
    script_path = Path(__file__).resolve()
    skill_name = script_path.parents[1].name
    shared_path = script_path.parents[2] / "_goal_shared" / "scripts" / script_path.name
    if not shared_path.exists():
        raise SystemExit(f"missing shared goal script: {shared_path}")
    spec = importlib.util.spec_from_file_location(f"_goal_shared_{script_path.stem}", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared goal script: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.SKILL_NAME_OVERRIDE = skill_name
    module.SCRIPT_DIR_OVERRIDE = script_path.parent
    return module


_SHARED_MODULE = _load_shared()

for _name in dir(_SHARED_MODULE):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_SHARED_MODULE, _name)


if __name__ == "__main__":
    raise SystemExit(_SHARED_MODULE.main())
