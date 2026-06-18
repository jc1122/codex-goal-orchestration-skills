"""Regression tests for the 2026-06-18 deep-review fixes (goal-config).

Pins the verified defects:
- check_goal_config.load_json fails closed (SystemExit) on malformed/missing config;
- config-supplied timeouts no longer crash with int() ValueError;
- opencode-bridge roles validate provider/model against the known bridge routes
  (contract MUST that was previously unenforced);
- create_goal_config file-input loaders fail closed on malformed/missing JSON.
"""

import pytest
from conftest import REPO, load_module

cgc = load_module("skills/goal-config/scripts/check_goal_config.py", "cgc_review")
crc = load_module("skills/goal-config/scripts/create_goal_config.py", "crc_review")


# --- 2026-06-18 convergence pass: docs must not advertise a check_goal_config.py flag that
#     build_parser does not define (following the doc gave an argparse error). ---
def test_docs_do_not_reference_nonexistent_check_flag():
    parser_flags = {opt for action in cgc.build_parser()._actions for opt in action.option_strings}
    assert "--include-raw-errors" not in parser_flags  # confirms reality: the flag does not exist
    for rel in ("skills/goal-config/SKILL.md", "skills/goal-config/references/configuration-contract.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "--include-raw-errors" not in text, f"{rel} still references the nonexistent flag"


# --- check_goal_config: malformed / missing config fails closed (SystemExit, not traceback) ---
def test_check_load_json_fails_closed(tmp_path):
    bad = tmp_path / "goal.config.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cgc.load_json(bad)
    with pytest.raises(SystemExit):
        cgc.load_json(tmp_path / "does-not-exist.json")


# --- config-supplied non-numeric timeout no longer crashes the smoke path ---
def test_run_harness_smoke_tolerates_non_numeric_timeout():
    # Missing prompt/expect short-circuits to a failed result; the int() on a non-numeric
    # timeout used to raise ValueError before reaching that return.
    result, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
        {"timeout_seconds": "not-an-int"},
        harness={"kind": "codex", "command": "true"},
    )
    assert result["status"] == "failed"
    assert failures  # missing prompt/expect recorded, no crash


# --- opencode-bridge route validation (contract MUST) ---
def test_bridge_route_validation():
    assert cgc._bridge_route_failures({"model": "deepseek-v4-flash"}) == []
    assert cgc._bridge_route_failures({"model": "deepseek-v4-pro"}) == []
    # nested provider id: trailing model segment must match
    assert cgc._bridge_route_failures({"model": "openrouter/deepseek/deepseek-v4-pro"}) == []
    # an unknown bridge model is rejected
    assert cgc._bridge_route_failures({"model": "deepseek-v9-imaginary"})
    # missing model is deferred to the basic check (no duplicate failure here)
    assert cgc._bridge_route_failures({}) == []


def test_check_model_for_harness_rejects_bad_bridge_route():
    result, failures = cgc.check_model_for_harness(
        {"provider": "deepseek", "model": "deepseek-v9-imaginary"},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "true"},
    )
    assert result["status"] == "failed"
    assert any("not a known bridge route" in f for f in failures)


# --- create_goal_config: file-input loaders fail closed ---
def test_create_loaders_fail_closed(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        crc.load_discovery_report(bad)
    with pytest.raises(SystemExit):
        crc.load_discovery_report(tmp_path / "missing.json")
    with pytest.raises(SystemExit):
        crc.load_harness_spec("{not valid json")
    with pytest.raises(SystemExit):
        crc.load_harness_spec(str(tmp_path / "missing-harness.json"))
