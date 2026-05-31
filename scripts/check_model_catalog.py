#!/usr/bin/env python3
"""Repository wrapper for the installed-skill model catalog checker."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SHARED_SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "_goal_shared" / "scripts" / "check_model_catalog.py"


def main() -> int:
    spec = importlib.util.spec_from_file_location("goal_shared_check_model_catalog", SHARED_SCRIPT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared model catalog checker: {SHARED_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
