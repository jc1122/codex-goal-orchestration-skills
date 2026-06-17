"""Regression tests for the harness-kind contract gate.

Locks the config<->runtime harness-kind contract and guards against
re-introduction of the pre-bridge direct-"opencode" subsystem (removed in the
opencode-worker-bridge migration). See scripts/check_harness_contract.py.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts" / "check_harness_contract.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("check_harness_contract", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harness_contract_holds():
    """The live tree must satisfy the contract (config kinds == runtime kinds)."""
    assert _load_gate().main() == 0


def test_runtime_dispatch_parses_all_three_kinds():
    mod = _load_gate()
    text = (
        'if provider == "codex":\n'
        "    ...\n"
        "if provider == BRIDGE_HARNESS_KIND:\n"
        "    ...\n"
        'if provider == "generic-cli":\n'
    )
    assert mod._runtime_dispatch_kinds(text) == {"codex", "opencode-bridge", "generic-cli"}


def test_dispatch_missing_a_supported_kind_is_detectable():
    """A runtime that drops a supported kind is exactly the dead-config trap; the
    gate's dispatch-set comparison must surface it."""
    mod = _load_gate()
    # Only codex handled -> mismatch vs SUPPORTED_HARNESS_KINDS the gate flags.
    assert mod._runtime_dispatch_kinds('if provider == "codex":') == {"codex"}


def test_forbidden_tokens_cover_the_removed_subsystem():
    mod = _load_gate()
    for token in ("opencode_session_db", "opencode_db", "discover_available_routes", 'kind == "opencode"'):
        assert token in mod.FORBIDDEN
