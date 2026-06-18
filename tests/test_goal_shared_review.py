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

import subprocess
import sys

import pytest
from conftest import REPO, load_module

scheduler_tick = load_module("skills/_goal_shared/scripts/scheduler_tick.py", "gs_scheduler_tick")
append_event = load_module("skills/_goal_shared/scripts/append_scheduler_event.py", "gs_append_event")
check_model_catalog = load_module("skills/_goal_shared/scripts/check_model_catalog.py", "gs_check_model_catalog")
repair_gate = load_module("skills/_goal_shared/scripts/script_only_repair_gate.py", "gs_repair_gate")
lite_prompt = load_module("skills/_goal_shared/scripts/lite_prompt.py", "gs_lite_prompt")
extract_telemetry = load_module("skills/_goal_shared/scripts/extract_telemetry.py", "gs_extract_telemetry")
reconcile = load_module("skills/_goal_shared/scripts/reconcile_goal_run.py", "gs_reconcile")
path_rules = load_module("skills/_goal_shared/scripts/path_rules.py", "gs_path_rules")
contract = load_module("skills/_goal_shared/scripts/orchestration_contract.py", "gs_contract")
vla = load_module("skills/_goal_shared/scripts/validate_lite_advice.py", "gs_validate_lite_advice")
rlr = load_module("skills/_goal_shared/scripts/runtime_lite_runner.py", "gs_runtime_lite_runner")
status_validation = load_module("skills/_goal_shared/scripts/status_validation.py", "gs_status_validation")


# --- 2026-06-18 convergence pass 6 (proactive sweep): iterations over semi-trusted artifact list
#     fields tolerate a present non-list value instead of TypeError ---
def test_reconcile_stale_active_branch_ids_tolerates_non_list():
    assert reconcile.stale_active_branch_ids({"active": 5}, []) == []  # was TypeError on non-list active


def test_runtime_lite_runner_verify_inputs_tolerates_non_list_source_files(tmp_path):
    ok, _msg = rlr.verify_inputs_current({"base_dir": str(tmp_path)}, {"source_files": 5})  # must not raise
    assert isinstance(ok, bool)


# --- 2026-06-18 convergence pass 15: set/frozenset membership over a tampered (unhashable) scalar
#     field fails closed with a defect instead of crashing with TypeError. `x in {set}` hashes the
#     LHS, so a list-valued `mode`/`status`/etc. raised `TypeError: unhashable type` before. (Tuple
#     membership, by contrast, uses == iteration and was never affected.) ---
def test_validate_reuse_policy_tolerates_unhashable_mode():
    defects: list[str] = []
    status_validation.validate_reuse_policy(defects, {"mode": ["new"], "accepted": True}, "$.x")  # must not raise
    assert any("mode" in d for d in defects), defects


def test_validate_lite_advice_status_membership_tolerates_unhashable():
    # validate() does `root["status"] not in STATUSES` (a set) and `status in {"partial","blocked"}`.
    defects = vla.validate(
        {"status": ["partial"]},
        packet_id=None,
        purpose=None,
        expected_sources=None,
        inputs=None,
        inputs_path=None,
    )  # must not raise TypeError on the unhashable status
    assert isinstance(defects, list) and any("status" in d for d in defects), defects


# --- 2026-06-18 convergence pass 11: a directory source path is "missing", not IsADirectoryError
#     from sha256_file (the .exists()->is_file() family fix) ---
def test_runtime_lite_runner_verify_inputs_tolerates_directory_source(tmp_path):
    (tmp_path / "srcdir").mkdir()
    ok, _msg = rlr.verify_inputs_current(
        {"base_dir": str(tmp_path)},
        {"source_files": [{"path": "srcdir", "sha256": "x", "size_bytes": 0}]},
    )
    assert ok is False  # was IsADirectoryError


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


# --- 2026-06-18 convergence pass 13: sha256_file returns None on a non-file (directory) instead of
#     raising IsADirectoryError; callers comparing it then fail closed (mismatch defect) ---
def test_sha256_file_returns_none_on_directory(tmp_path):
    assert scheduler_tick.sha256_file(tmp_path) is None  # a directory -> None, not IsADirectoryError
    f = tmp_path / "x.txt"
    f.write_text("hi", encoding="utf-8")
    assert isinstance(scheduler_tick.sha256_file(f), str)  # a real file still hashes


# --- 2026-06-18 convergence pass 12: fail-closed JSON readers also fail closed on a DIRECTORY
#     path (IsADirectoryError is an OSError, previously uncaught) ---
def test_json_readers_fail_closed_on_directory(tmp_path):
    for reader in (
        scheduler_tick.read_json,
        check_model_catalog.read_json,
        repair_gate.load_json,
        append_event.load_ledger,
        reconcile.read_manifest,
    ):
        with pytest.raises(SystemExit):
            reader(tmp_path)  # a directory -> clean SystemExit, not IsADirectoryError


# --- 2026-06-18 convergence pass 3: fail-closed JSON readers also fail closed on a non-UTF-8
#     file (UnicodeDecodeError is a ValueError, not JSONDecodeError) ---
def test_json_readers_fail_closed_on_non_utf8(tmp_path):
    nonutf8 = tmp_path / "bad.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    for reader in (
        scheduler_tick.read_json,
        check_model_catalog.read_json,
        repair_gate.load_json,
        append_event.load_ledger,
    ):
        with pytest.raises(SystemExit):
            reader(nonutf8)


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
def test_is_repo_relative_path_rejects_porcelain_prefix():
    # 2026-06-18 convergence pass 8: the reject_porcelain branch enforces the contract MUST that
    # changed_files entries carry no git porcelain status prefix.
    assert path_rules.is_repo_relative_path("src/foo.py", reject_porcelain=True) is True
    assert path_rules.is_repo_relative_path(" M src/foo.py", reject_porcelain=True) is False
    assert path_rules.is_repo_relative_path(" M src/foo.py", reject_porcelain=False) is True


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


# --- 2026-06-18 convergence pass: reconcile's direct manifest reads fail closed via a
#     dedicated read_manifest helper (the bare read_json primitive stays bare for read_json_or_none) ---
def test_reconcile_read_manifest_fails_closed(tmp_path):
    bad = tmp_path / "job.manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        reconcile.read_manifest(bad)
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")  # valid JSON, not an object
    with pytest.raises(SystemExit):
        reconcile.read_manifest(arr)
    # The tolerant wrapper must still degrade (landmine): read_json stays bare so this keeps working.
    data, err = reconcile.read_json_or_none(bad)
    assert data is None and err


# --- 2026-06-18 convergence pass: check_model_catalog.read_json fails closed on a missing
#     manifest path (was an uncaught FileNotFoundError traceback from main's optional --manifest) ---
def test_check_model_catalog_read_json_handles_missing_file(tmp_path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit):
        check_model_catalog.read_json(missing)
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        check_model_catalog.read_json(bad)
    # Pass-2: UnicodeDecodeError (a ValueError, not OSError) must also fail closed.
    nonutf8 = tmp_path / "bad-bytes.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        check_model_catalog.read_json(nonutf8)


# --- 2026-06-18 convergence pass 6: validate_source_files tolerates a non-dict element in the
#     inputs source_files list (the standalone copy diverged from the in-process status_validation one) ---
def test_validate_source_files_tolerates_non_dict_expected():
    defects: list[str] = []
    vla.validate_source_files(defects, [], "$.source_files", ["not-a-dict"])  # must not raise (was AttributeError)


# --- 2026-06-18 convergence pass 2: validate_lite_advice tolerates a non-UTF-8 task.md (no
#     UnicodeDecodeError) — it is also run in-process by status_validation, so a crash here would
#     take down the whole status validator ---
def test_validate_prompt_hash_tolerates_non_utf8_task(tmp_path):
    (tmp_path / "prompt.md").write_text("prompt body", encoding="utf-8")
    (tmp_path / "task.md").write_bytes(b"\xff\xfe not utf8")
    inputs = {"prompt_sha256": "sha256:" + "0" * 64}
    defects: list[str] = []
    vla.validate_prompt_hash(defects, inputs, tmp_path / "input-files.json")  # must not raise
    assert defects  # a stale/mismatch defect is recorded instead of crashing


# --- 2026-06-18 convergence pass 2: the validate_lite_advice CLI fails closed on malformed
#     --inputs (the bare load_json call site is guarded; the primitive stays bare for its wrapper) ---
def test_validate_lite_advice_cli_fails_closed_on_malformed_inputs(tmp_path):
    advice = tmp_path / "advice.json"
    advice.write_text("{}", encoding="utf-8")
    bad_inputs = tmp_path / "input-files.json"
    bad_inputs.write_text("{ not json", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            # invoke via a skill dispatch shim: the _goal_shared canonical refuses direct execution
            str(REPO / "skills" / "goal-branch-orchestrator" / "scripts" / "validate_lite_advice.py"),
            "--advice",
            str(advice),
            "--inputs",
            str(bad_inputs),
        ],
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0, combined
    assert "Traceback" not in proc.stderr, f"crashed instead of failing closed:\n{proc.stderr}"
    assert "--inputs is not valid JSON" in combined, combined


# --- 2026-06-18 fresh-audit pass: A1 accepted_alias must not mark a *blocked* reviewer / research-worker
#     attempt as accepted. The worker/lite_advisor branches key off the authoritative status, but the
#     reviewer/research-worker branches only matched one hardcoded findings string, while the runtime's
#     write_terminal_review/write_terminal_research emit verdict/status:"blocked" with a *variable* message. ---
csa = load_module("skills/_goal_shared/scripts/check_goal_skill_availability.py", "gs_check_skill_availability")


def test_accepted_alias_blocked_reviewer_is_not_accepted():
    attempts = [{"called": True, "alias": "ds-pro-max"}]
    blocked = {"role": "reviewer", "verdict": "blocked", "findings": ["could not complete review"]}
    assert extract_telemetry.accepted_alias("reviewer", blocked, attempts) is None
    # a real review (verdict reject/mergeable) still counts as a landed route
    landed = {"role": "reviewer", "verdict": "reject", "findings": ["needs work"]}
    assert extract_telemetry.accepted_alias("reviewer", landed, attempts) == "ds-pro-max"


def test_accepted_alias_blocked_research_worker_is_not_accepted():
    attempts = [{"called": True, "alias": "ds-flash-max"}]
    blocked = {"role": "research-worker", "status": "blocked", "findings": ["totally failed"]}
    assert extract_telemetry.accepted_alias("research-worker", blocked, attempts) is None
    landed = {"role": "research-worker", "status": "ok", "findings": ["found it"]}
    assert extract_telemetry.accepted_alias("research-worker", landed, attempts) == "ds-flash-max"


# --- A2: check_model_catalog report advertises the manifest-resolved config paths, not hardcoded defaults ---
def test_load_manifest_config_returns_resolved_paths(tmp_path):
    import json as _json

    manifest = tmp_path / "job.manifest.json"
    (tmp_path / "custom.config.json").write_text(_json.dumps({"models": {}}), encoding="utf-8")
    manifest.write_text(
        _json.dumps({"goal_config_path": "custom.config.json", "goal_config_check_path": "custom.check.json"}),
        encoding="utf-8",
    )
    config, check, warnings, config_path, check_path = check_model_catalog.load_manifest_config(manifest)
    assert config is not None and config_path == tmp_path / "custom.config.json"
    assert check is None and check_path == tmp_path / "custom.check.json"  # path resolved even when file absent


# --- A3: declared_skill_name tolerates a non-UTF-8 SKILL.md instead of UnicodeDecodeError ---
def test_declared_skill_name_tolerates_non_utf8(tmp_path):
    skill_md = tmp_path / "SKILL.md"
    skill_md.write_bytes(b"\xff\xfe---\nname: goal-config\n---\n")
    assert csa.declared_skill_name(skill_md) == "goal-config"  # must not raise UnicodeDecodeError
