"""Regression tests for the 2026-06-18 deep-review fixes (_goal_shared).

Pins the verified defects so they cannot silently regress:
- malformed-JSON reads fail closed (SystemExit) instead of a traceback, in
  scheduler_tick / append_scheduler_event / check_model_catalog / script_only_repair_gate;
- the shared Lite prompt builder tolerates malformed recorded source_files
  (the crash that propagated into the runtime status validators);
- extract_telemetry tolerates a non-list `blockers` value;
- reconcile's per-branch reuse filter uses the real branch-id set, not a "B" prefix;
- path_rules rejects option-injection-shaped / control-char branch names;
- the genuinely-dead worker_route_class_ladder accessor was removed.
"""

import pytest
from conftest import load_module

scheduler_tick = load_module("skills/_goal_shared/scripts/scheduler_tick.py", "gs_scheduler_tick")
append_event = load_module("skills/_goal_shared/scripts/append_scheduler_event.py", "gs_append_event")
check_model_catalog = load_module("skills/_goal_shared/scripts/check_model_catalog.py", "gs_check_model_catalog")
repair_gate = load_module("skills/_goal_shared/scripts/script_only_repair_gate.py", "gs_repair_gate")
lite_prompt = load_module("skills/_goal_shared/scripts/lite_prompt.py", "gs_lite_prompt")
extract_telemetry = load_module("skills/_goal_shared/scripts/extract_telemetry.py", "gs_extract_telemetry")
reconcile = load_module("skills/_goal_shared/scripts/reconcile_goal_run.py", "gs_reconcile")
path_rules = load_module("skills/_goal_shared/scripts/path_rules.py", "gs_path_rules")
contract = load_module("skills/_goal_shared/scripts/orchestration_contract.py", "gs_contract")


# --- malformed JSON fails closed (SystemExit), never a raw traceback ---
def test_json_readers_fail_closed_on_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        scheduler_tick.read_json(bad)
    with pytest.raises(SystemExit):
        check_model_catalog.read_json(bad)
    with pytest.raises(SystemExit):
        repair_gate.load_json(bad)
    with pytest.raises(SystemExit):
        append_event.load_ledger(bad)


# --- shared Lite prompt builder tolerates malformed recorded source_files ---
def _build(sources):
    return lite_prompt.build_lite_prompt(
        "M01-L01",
        "main-summary",
        "/bundle",
        sources,
        "task text",
        skill="goal-main-orchestrator",
        model="deepseek-v4-flash",
        provider="deepseek",
        variant="max",
        control_script="/abs/opencode_worker.py",
        control_version="schema_version:1",
        permission_profile="read-only",
        task_sha256="0" * 64,
        avoids_action="edits",
        expected_savings_reason="route context",
    )


def test_lite_prompt_tolerates_malformed_source_files():
    # A non-dict item and a dict missing keys must not crash (would KeyError/TypeError
    # before the fix, and that crash propagated into validate_main_status/branch_status).
    text = _build(["not-a-dict", {"path": "a.py"}])
    assert isinstance(text, str) and "a.py" in text


def test_lite_prompt_well_formed_is_byte_identical_to_get_form():
    # The .get() hardening must not alter output for well-formed packets (the prompt hash
    # is computed over this exact text). Lock the FULL rendered source line, not a prefix.
    sha = "sha256:" + "0" * 64
    item = {"path": "a.py", "sha256": sha, "size_bytes": 12}
    expected_line = f"- a.py ({sha}, 12 bytes)"
    rendered = _build([item])
    assert expected_line in rendered
    # exactly one source line, exactly as the pre-hardening f-string would have produced it
    assert rendered.count("- a.py (") == 1


# --- extract_telemetry tolerates non-list blockers (no TypeError, telemetry still produced) ---
def test_accepted_alias_tolerates_non_list_blockers():
    out = {"status": "failed", "blockers": None}  # blockers: null used to crash accepted_alias
    attempts = [{"alias": "ds-flash-max", "called": True, "accepted": False}]
    # Must not raise; returns either an alias or None.
    result = extract_telemetry.accepted_alias("worker", out, attempts)
    assert result is None or isinstance(result, str)


# --- reconcile per-branch reuse uses the real branch-id set, not a "B" prefix heuristic ---
def test_branch_reuse_map_excludes_stale_non_b_branch():
    branch_reports = [
        {"branch_id": "core", "validation": {"status": "pass"}, "status_path": {"exists": True}},
    ]
    stale = [{"owner": "core", "code": "unpromoted_review"}]
    reuse = reconcile._branch_reuse_map(branch_reports, stale)
    assert reuse["core"] is False, "stale non-'B'-prefixed branch must not be marked reuse-safe"


# --- path_rules rejects option-injection-shaped and control-char branch names ---
def test_safe_branch_name_rejects_leading_dash_and_control_chars():
    assert path_rules.safe_branch_name("phaseX-B01") is True
    assert path_rules.safe_branch_name("-rf") is False
    assert path_rules.safe_branch_name("--upload-pack=x") is False
    assert path_rules.safe_branch_name("bad\x7fname") is False
    assert path_rules.safe_branch_name("bad\x01name") is False


# --- the genuinely-dead accessor was removed; the consumed sibling remains ---
def test_dead_worker_route_class_ladder_removed():
    assert not hasattr(contract, "worker_route_class_ladder")
    assert hasattr(contract, "worker_route_class_reason")
