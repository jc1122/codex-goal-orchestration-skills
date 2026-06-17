#!/usr/bin/env python3
"""Deterministic harness-kind contract gate.

Catches the class of legacy/functionally-dead code that the dead-code lane
cannot: a config-declarable harness *kind* that no longer has a live runtime
consumer. A kind that goal-config can emit MUST be dispatchable by the runtime
packet launcher (`runtime_packet_runner`) or it SystemExits at dispatch time.

It enforces three invariants and forbids re-introduction of the pre-bridge
direct-"opencode" subsystem (opencode session-db smoke readback + provider
model-list discovery) removed in the opencode-worker-bridge migration:

  1. scan_configurables.supported_kinds is wired to the single source of truth
     (orchestration_contract.SUPPORTED_HARNESS_KINDS), not a divergent literal.
  2. The runtime packet launcher dispatches exactly SUPPORTED_HARNESS_KINDS.
  3. No legacy direct-"opencode" tokens remain in the goal-config surface.

Exit 0 = contract holds; 1 = violation(s); 2 = wiring/IO error.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills" / "_goal_shared" / "scripts"
GOAL_CONFIG = ROOT / "skills" / "goal-config" / "scripts"
AMENDER = ROOT / "skills" / "goal-plan-amender" / "scripts"
RUNTIME = ROOT / "skills" / "goal-branch-orchestrator" / "scripts" / "runtime_packet_runner.py"

# Canonical (non-mirrored) sources to scan for legacy direct-"opencode" tokens.
SCAN_FILES = [
    SHARED / "orchestration_contract.py",
    SHARED / "runtime_phase_manifest.py",
    GOAL_CONFIG / "check_goal_config.py",
    GOAL_CONFIG / "create_goal_config.py",
    GOAL_CONFIG / "scan_configurables.py",
    AMENDER / "amendment_lib.py",
]

# Tokens that signal the removed direct-"opencode" harness subsystem. They are
# deliberately specific so the live "opencode-bridge" kind never matches.
FORBIDDEN = (
    "opencode_session_db",
    "opencode_db",
    "opencode_db_path",
    "--opencode-db",
    "discover_available_routes",
    "discover_provider",
    "discover-provider",
    "discover_harness",
    "discover-harness",
    'kind == "opencode"',
    ":opencode:",  # legacy --role-model ROLE:opencode:... token
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runtime_dispatch_kinds(text: str) -> set[str]:
    """Harness kinds the runtime launcher actually dispatches on."""
    kinds = set(re.findall(r'provider == "([a-z][a-z0-9-]*)"', text))
    if "provider == BRIDGE_HARNESS_KIND" in text:
        kinds.add("opencode-bridge")
    return kinds


def main() -> int:
    violations: list[str] = []
    try:
        contract = _load(SHARED / "orchestration_contract.py", "orchestration_contract")
        supported = set(contract.SUPPORTED_HARNESS_KINDS)
        scan_src = (GOAL_CONFIG / "scan_configurables.py").read_text()
        runtime_src = RUNTIME.read_text()
    except (OSError, RuntimeError, AttributeError) as exc:
        print(f"status=error reason={exc!r}")
        return 2

    # Invariant 1: scan supported_kinds wired to the contract constant.
    if "list(contract.SUPPORTED_HARNESS_KINDS)" not in scan_src:
        violations.append(
            "scan_configurables.supported_kinds is not wired to "
            "orchestration_contract.SUPPORTED_HARNESS_KINDS (divergent literal risks drift)"
        )

    # Invariant 2: runtime dispatch set == contract.
    dispatch = _runtime_dispatch_kinds(runtime_src)
    if dispatch != supported:
        violations.append(
            f"runtime launcher dispatch kinds {sorted(dispatch)} != "
            f"SUPPORTED_HARNESS_KINDS {sorted(supported)} "
            "(a config-declarable kind the runtime cannot dispatch SystemExits at runtime)"
        )

    # Invariant 3: no legacy direct-"opencode" tokens in the goal-config surface.
    for path in SCAN_FILES:
        try:
            text = path.read_text()
        except OSError as exc:
            print(f"status=error reason={exc!r} path={path}")
            return 2
        for token in FORBIDDEN:
            if token in text:
                rel = path.relative_to(ROOT)
                violations.append(f"legacy direct-opencode token {token!r} found in {rel}")

    if violations:
        print(f"status=failed violations={len(violations)}")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(f"status=pass supported_kinds={sorted(supported)} runtime_dispatch={sorted(dispatch)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
