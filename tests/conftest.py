"""Pytest support: load standalone skill scripts by path.

The goal skills are standalone CLIs (not an installed package); they bootstrap
their own siblings via importlib at import time. We load them the same way and
suppress bytecode so test runs never pollute skills/ with __pycache__ (CI asserts
no bytecode under skills/bin/scripts).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]


def load_module(relpath: str, name: str | None = None):
    path = REPO / relpath
    modname = name or f"goalskill_{path.stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module
