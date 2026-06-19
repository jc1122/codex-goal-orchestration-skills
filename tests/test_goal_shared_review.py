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
import json
import os
import shutil
from pathlib import Path

import pytest
from conftest import REPO, load_module

scheduler_tick = load_module("skills/_goal_shared/scripts/scheduler_tick.py", "gs_scheduler_tick")
append_event = load_module("skills/_goal_shared/scripts/append_scheduler_event.py", "gs_append_event")
check_model_catalog = load_module("skills/_goal_shared/scripts/check_model_catalog.py", "gs_check_model_catalog")
cmc = check_model_catalog
sync_goal_shared = load_module("scripts/sync_goal_shared.py", "gs_sync_goal_shared")
crel = load_module("scripts/check_release.py", "gs_check_release")
repair_gate = load_module("skills/_goal_shared/scripts/script_only_repair_gate.py", "gs_repair_gate")
lite_prompt = load_module("skills/_goal_shared/scripts/lite_prompt.py", "gs_lite_prompt")
extract_telemetry = load_module("skills/_goal_shared/scripts/extract_telemetry.py", "gs_extract_telemetry")
reconcile = load_module("skills/_goal_shared/scripts/reconcile_goal_run.py", "gs_reconcile")
path_rules = load_module("skills/_goal_shared/scripts/path_rules.py", "gs_path_rules")
contract = load_module("skills/_goal_shared/scripts/orchestration_contract.py", "gs_contract")
vla = load_module("skills/_goal_shared/scripts/validate_lite_advice.py", "gs_validate_lite_advice")
rlr = load_module("skills/_goal_shared/scripts/runtime_lite_runner.py", "gs_runtime_lite_runner")
status_validation = load_module("skills/_goal_shared/scripts/status_validation.py", "gs_status_validation")


def test_sync_goal_shared_detects_non_executable_generated_script_wrapper(tmp_path, monkeypatch):
    wrapper = tmp_path / "skills" / "goal-config" / "scripts" / "append_scheduler_event.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(sync_goal_shared.SCRIPT_WRAPPER_TEMPLATE, encoding="utf-8")
    wrapper.chmod(0o644)

    monkeypatch.setattr(sync_goal_shared, "ROOT", tmp_path)
    monkeypatch.setattr(sync_goal_shared, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(sync_goal_shared, "SHARED_ROOT", tmp_path / "skills/_goal_shared")
    monkeypatch.setattr(sync_goal_shared, "expected_files", lambda: {wrapper: sync_goal_shared.SCRIPT_WRAPPER_TEMPLATE})
    monkeypatch.setattr(sys, "argv", ["sync_goal_shared.py"])

    assert sync_goal_shared.main() == 1


def test_sync_goal_shared_write_restores_generated_wrapper_execute_bit(tmp_path, monkeypatch):
    wrapper = tmp_path / "skills" / "goal-config" / "scripts" / "append_scheduler_event.py"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(sync_goal_shared.SCRIPT_WRAPPER_TEMPLATE, encoding="utf-8")
    wrapper.chmod(0o644)

    monkeypatch.setattr(sync_goal_shared, "ROOT", tmp_path)
    monkeypatch.setattr(sync_goal_shared, "SKILLS_ROOT", tmp_path / "skills")
    monkeypatch.setattr(sync_goal_shared, "SHARED_ROOT", tmp_path / "skills/_goal_shared")
    monkeypatch.setattr(sync_goal_shared, "expected_files", lambda: {wrapper: sync_goal_shared.SCRIPT_WRAPPER_TEMPLATE})
    monkeypatch.setattr(sys, "argv", ["sync_goal_shared.py", "--write"])

    assert sync_goal_shared.main() == 0
    assert wrapper.stat().st_mode & 0o111


def test_check_release_rejects_non_executable_goal_shared_script(tmp_path, monkeypatch):
    rel = Path("skills/goal-config/scripts/append_scheduler_event.py")
    script = tmp_path / rel
    script.parent.mkdir(parents=True)
    script.write_text("print('hi')", encoding="utf-8")
    script.chmod(0o644)

    monkeypatch.setattr(crel, "_goal_shared_executable_script_paths", lambda *_, **__: [rel])
    with pytest.raises(SystemExit):
        crel.check_goal_shared_executable_scripts(tmp_path, scope="source tree")

    script.chmod(0o755)
    crel.check_goal_shared_executable_scripts(tmp_path, scope="source tree")


# --- 2026-06-18 convergence pass 6 (proactive sweep): iterations over semi-trusted artifact list
#     fields tolerate a present non-list value instead of TypeError ---
def test_reconcile_stale_active_branch_ids_tolerates_non_list():
    assert reconcile.stale_active_branch_ids({"active": 5}, []) == []  # was TypeError on non-list active


def test_runtime_lite_runner_verify_inputs_tolerates_non_list_source_files(tmp_path):
    ok, msg = rlr.verify_inputs_current({"base_dir": str(tmp_path)}, {"source_files": 5})
    assert ok is False
    assert "source_files shape" in msg


def test_script_only_repair_gate_check_amendments_and_blockers_tolerates_unhashable_status(tmp_path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    status_path = tmp_path / "terminal.status.json"
    status_path.write_text(json.dumps({"status": ["blocked"], "blockers": []}), encoding="utf-8")

    checks: list[dict] = []
    actions: list[dict] = []
    repair_gate.check_amendments_and_blockers(
        actions,
        checks,
        bundle_dir=bundle_dir,
        repo_root=None,
        status_path=status_path,
        branch_id=None,
        scope="main",
    )

    assert checks and checks[-1]["name"] == "amendment_and_blocker_repair"
    assert checks[-1]["status"] == "actionable"
    assert checks[-1]["blocker_count"] == 0
    assert any(action["kind"] == "amendment_eligibility" for action in actions)


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


def test_reconcile_main_status_rejects_non_string_without_type_error(tmp_path):
    resume = reconcile._compute_resume_state(
        pre_dispatch=False,
        main_status_data={"status": ["blocked"]},
        branch_reports=[],
        missing_artifacts=[],
        stale_or_unreconciled=[],
        validation_defects=[],
    )
    assert resume.status == "blocked"

    completion = reconcile._derive_completion_state(
        status=resume.status,
        final_state_status=resume.final_state_status,
        pre_dispatch=False,
        branches=[],
        branch_reports=[],
        branch_reuse=resume.branch_reuse,
        missing_artifacts=[],
        stale_or_unreconciled=[],
        effective_missing_artifacts=[],
        effective_stale_or_unreconciled=[],
    )
    scan = reconcile._BundleScan(
        manifest_checks=[],
        telemetry={"summary_exists": False},
        stale_index={},
        branch_ids=[],
        main_scheduler_rel="main.scheduler.json",
        branch_reports=[],
        pre_dispatch=False,
    )
    report = reconcile._assemble_report(
        bundle_dir=tmp_path,
        manifest_path=tmp_path / "job.manifest.json",
        manifest={},
        scan=scan,
        main_status_path=tmp_path / "main.status.json",
        main_status_data={"status": ["blocked"]},
        main_status_value=["blocked"],
        main_validation_defects=[],
        main_scheduler={},
        branch_reports=[],
        resume=resume,
        completion=completion,
        pre_dispatch=False,
        validation_defects=[],
        missing_artifacts=[],
        stale_or_unreconciled=[],
        recovery_commands=[],
        next_commands=[],
    )
    assert report["status"] == "blocked"
    assert report["main_status"]["runtime_status"] == "missing"


def test_recovered_branch_ids_tolerates_non_string_runtime_status():
    branches = [
        {
            "id": "B01",
            "status": "pass",
            "review_status": "mergeable",
            "recovers_from": ["B01"],
            "terminal_status": "pass",
        }
    ]
    branch_reports = [
        {"branch_id": "B01", "runtime_status": [], "review_status": "mergeable", "validation": {"status": "pass"}},
        {
            "branch_id": "B02",
            "runtime_status": {"status": "failed"},
            "review_status": "mergeable",
            "validation": {"status": "pass"},
        },
        {
            "branch_id": "B03",
            "runtime_status": "failed",
            "review_status": "mergeable",
            "validation": {"status": "pass"},
        },
    ]
    assert reconcile.recovered_branch_ids(branches, branch_reports) == set()


def test_derive_completion_state_tolerates_non_string_runtime_status(tmp_path):
    branch_reports = [
        {"branch_id": "B01", "runtime_status": ["failed"], "status_path": {"exists": True}, "validation": {}},
        {"branch_id": "B02", "runtime_status": {"status": "pass"}, "status_path": {"exists": False}, "validation": {}},
    ]
    completion = reconcile._derive_completion_state(
        status="pass",
        final_state_status="pass",
        pre_dispatch=False,
        branches=[{"id": "B01", "status": "pass", "terminal_status": "pass"}],
        branch_reports=branch_reports,
        branch_reuse={"B01": True, "B02": True},
        missing_artifacts=[],
        stale_or_unreconciled=[],
        effective_missing_artifacts=[],
        effective_stale_or_unreconciled=[],
    )
    assert completion.terminal_branch_ids == []
    assert completion.blocked_branches == []
    assert completion.missing_branch_ids == ["B02"]


@pytest.mark.parametrize("purpose", [["review"], {}])
def test_discover_unrecorded_lite_packets_rejects_non_string_purpose(purpose, tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    lite_packet = tmp_path / "lite" / "pkt-main"
    lite_packet.mkdir(parents=True)
    (lite_packet / "input-files.json").write_text(
        json.dumps(
            {
                "packet_id": "pkt-main",
                "purpose": purpose,
                "status": "pass",
                "skill": "goal-main-orchestrator",
            }
        ),
        encoding="utf-8",
    )

    defects: list[str] = []
    status_validation.discover_unrecorded_lite_packets(
        defects,
        "$.lite_runtime",
        manifest_path=manifest_path,
        reported_ids=set(),
        allowed_purposes={"main-summary"},
        skill_name="goal-main-orchestrator",
        scope_label="runtime",
        malformed_packet_prefix="",
    )
    assert any("unrecorded manifest-owned runtime Lite packet" in defect for defect in defects), defects


def test_discover_unrecorded_lite_packets_rejects_non_object_inputs_json(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    lite_packet = tmp_path / "lite" / "pkt-main"
    lite_packet.mkdir(parents=True)
    (lite_packet / "input-files.json").write_text("[]", encoding="utf-8")

    defects: list[str] = []
    status_validation.discover_unrecorded_lite_packets(
        defects,
        "$.lite_runtime",
        manifest_path=manifest_path,
        reported_ids=set(),
        allowed_purposes={"main-summary"},
        skill_name="goal-main-orchestrator",
        scope_label="runtime",
        malformed_packet_prefix="",
    )
    assert any("must be an object" in defect and "pkt-main" in defect for defect in defects), defects


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


def test_check_goal_skill_availability_includes_all_sync_goal_shared_wrappers():
    for skill in sync_goal_shared.SKILLS:
        required = set(csa.REQUIRED_FILES[skill])
        for shared_script in sync_goal_shared.SHARED_SCRIPTS:
            rel_path = f"scripts/{shared_script}"
            assert rel_path in required, f"{skill} missing shared wrapper {rel_path}"


def test_check_goal_skill_availability_requires_amender_blocker_repair_packet(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(REPO / "skills", installed)
    blocker_repair_packet = installed / "goal-plan-amender" / "scripts" / "create_blocker_repair_packet.py"
    blocker_repair_packet.unlink()

    result = csa.inspect_skill(installed, "goal-plan-amender")

    assert result["available"] is False
    assert "scripts/create_blocker_repair_packet.py" in result["missing"]


def test_check_goal_skill_availability_in_installed_tree_does_not_need_sync_script(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(REPO / "skills", installed)
    assert not (tmp_path / "scripts" / "sync_goal_shared.py").exists()

    shared_check = installed / "_goal_shared" / "scripts" / "check_goal_skill_availability.py"
    shared_result = subprocess.run(
        [sys.executable, str(shared_check), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    shared_output = shared_result.stdout + shared_result.stderr
    assert shared_result.returncode == 0, shared_output
    shared_payload = json.loads(shared_result.stdout)
    assert shared_payload["status"] == "pass"

    local_wrapper = installed / "goal-config" / "scripts" / "check_goal_skill_availability.py"
    wrapper_result = subprocess.run(
        [sys.executable, str(local_wrapper), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    wrapper_output = wrapper_result.stdout + wrapper_result.stderr
    assert wrapper_result.returncode == 0, wrapper_output
    wrapper_payload = json.loads(wrapper_result.stdout)
    assert wrapper_payload["status"] == "pass"


# --- csa hardening: path normalizer, candidate roots, and skill discovery behaviors ---
def test_check_goal_skill_availability_normalize_absolute_root_guardrails(monkeypatch):
    absolute_tmp = Path("/tmp")
    assert isinstance(csa.normalize_absolute_root(absolute_tmp, "--skills-root", fail_on_relative=False), Path)
    assert csa.normalize_absolute_root(absolute_tmp, "--skills-root", fail_on_relative=False) == absolute_tmp.resolve(
        strict=False
    )
    assert csa.normalize_absolute_root(Path("relative/dir"), "--skills-root", fail_on_relative=False) is None
    with pytest.raises(SystemExit):
        csa.normalize_absolute_root("relative/dir", "--skills-root", fail_on_relative=True)
    with pytest.raises(SystemExit):
        csa.normalize_absolute_root(r"C:\\tmp\\x", "--skills-root", fail_on_relative=True)


def test_check_goal_skill_availability_candidate_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    roots = csa.candidate_roots([], allow_fallback_roots=True)
    assert any(r.name == "skills" for r in roots)
    assert csa.candidate_roots([str(tmp_path / "cli")], allow_fallback_roots=False) == [(tmp_path / "cli").resolve()]


def test_check_goal_skill_availability_inspect_skill_returns_availability_and_missing(tmp_path):
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "goal-config"
    skill_root.mkdir(parents=True)
    for rel_path in csa.REQUIRED_FILES["goal-config"]:
        path = skill_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    skill_root.joinpath("SKILL.md").write_text("name: goal-config\n", encoding="utf-8")
    for rel_path in csa.REQUIRED_SUPPORT_FILES:
        path = skills_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    result = csa.inspect_skill(tmp_path / "skills", "goal-config")
    assert result["available"] is True
    assert result["missing"] == []
    missing = csa.inspect_skill(tmp_path / "skills", "missing-skill")
    assert missing["available"] is False
    assert missing["missing"] == ["skill directory"]


def test_check_goal_skill_availability_support_file_is_required(tmp_path):
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "goal-config"
    skill_root.mkdir(parents=True)
    for rel_path in csa.REQUIRED_FILES["goal-config"]:
        path = skill_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    skill_root.joinpath("SKILL.md").write_text("name: goal-config\n", encoding="utf-8")
    for rel_path in csa.REQUIRED_SUPPORT_FILES:
        if rel_path.endswith("lite_prompt.py"):
            continue
        path = skills_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")

    result = csa.inspect_skill(tmp_path / "skills", "goal-config")
    missing_lite_prompt = "support:_goal_shared/scripts/lite_prompt.py"
    assert result["available"] is False
    assert result["missing"] == [missing_lite_prompt]


def test_check_goal_skill_availability_find_skill_prefers_first_available(tmp_path):
    skills_root = tmp_path / "r1"
    available_root = tmp_path / "r1" / "goal-config"
    available_root.mkdir(parents=True)
    for rel_path in csa.REQUIRED_FILES["goal-config"]:
        path = available_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    (available_root / "SKILL.md").write_text("name: goal-config\n", encoding="utf-8")
    for rel_path in csa.REQUIRED_SUPPORT_FILES:
        path = skills_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    result = csa.find_skill([tmp_path / "r1", tmp_path / "r2"], "goal-config")
    assert result["status"] == "pass"
    assert result["selected"]["root"] == str((tmp_path / "r1").resolve())


def _write_goal_config_tree(base: Path, *, missing_support: str | None = None) -> None:
    skill_root = base / "goal-config"
    skill_root.mkdir(parents=True)
    for rel_path in csa.REQUIRED_FILES["goal-config"]:
        path = skill_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")
    (skill_root / "SKILL.md").write_text("name: goal-config\n", encoding="utf-8")
    for rel_path in csa.REQUIRED_SUPPORT_FILES:
        if rel_path == missing_support:
            continue
        path = base / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok", encoding="utf-8")


# --- 2026-06-19 convergence pass 15: stale first root support defects must fail open ---
def test_check_goal_skill_availability_main_rejects_stale_root_even_with_good_source_root(tmp_path, capsys):
    installed_root = tmp_path / "installed"
    source_root = tmp_path / "source"
    missing_support = "_goal_shared/scripts/context_pack.py"
    _write_goal_config_tree(installed_root, missing_support=missing_support)
    _write_goal_config_tree(source_root, missing_support=None)

    sys.argv = [
        "check_goal_skill_availability.py",
        "--skills-root",
        str(installed_root),
        "--skills-root",
        str(source_root),
        "--require",
        "goal-config",
        "--json",
    ]
    assert csa.main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "blocked"
    item = report["skills"]["goal-config"]
    assert item["status"] == "missing"
    assert item["selected"]["root"] == str(installed_root.resolve())
    assert f"support:{missing_support}" in item["selected"]["missing"]


def test_check_goal_skill_availability_main_reports_mixed_roots(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(csa, "candidate_roots", lambda *_: [tmp_path / "a", tmp_path / "b"])

    def _fake_find_skill(_roots, skill, **_kwargs):
        root = (tmp_path / "a") if skill == "goal-config" else (tmp_path / "b")
        return {
            "status": "pass",
            "selected": {"skill": skill, "root": str(root), "declared_name": skill, "missing": []},
            "attempts": [],
        }

    monkeypatch.setattr(csa, "find_skill", _fake_find_skill)
    monkeypatch.setattr(csa.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    sys.argv = [
        "check_goal_skill_availability.py",
        "--require",
        "goal-config",
        "--require",
        "goal-main-orchestrator",
        "--json",
    ]
    assert csa.main() == 2
    data = json.loads(capsys.readouterr().out)
    assert "mixed-skill-roots" in data["blockers"]


# --- check_model_catalog: catalog source fallback and route/manifest coverage ---
def test_check_model_catalog_run_catalog_and_load_catalog_fallback(monkeypatch):
    calls: list[bool] = []

    def _fake_run_catalog(bundled: bool):
        calls.append(bundled)
        if not bundled:
            return None, "live unavailable"
        return {"models": [{"slug": "ds-flash-mini"}]}, ""

    monkeypatch.setattr(cmc.shutil, "which", lambda _cmd: "/usr/bin/codex")
    monkeypatch.setattr(cmc, "run_catalog", _fake_run_catalog)
    catalog, source, warnings = cmc.load_catalog("auto")
    assert source == "bundled"
    assert catalog == {"models": [{"slug": "ds-flash-mini"}]}
    assert calls == [False, True]
    assert warnings == ["live catalog unavailable: live unavailable"]


def test_check_model_catalog_run_catalog_timeout(monkeypatch):
    def _fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["codex", "debug", "models"], 30)

    monkeypatch.setattr(cmc.subprocess, "run", _fake_run)
    catalog, warning = cmc.run_catalog(bundled=False)
    assert catalog is None
    assert warning.startswith("timed out after 30s")


def test_check_model_catalog_model_rows_and_policy_routes():
    rows = cmc.model_rows([{"slug": "z"}, {"slug": "a", "display_name": "A"}, {"slug": ""}, 123])
    assert rows == [
        {"slug": "a", "display_name": "A", "supported_in_api": None, "visibility": None},
        {"slug": "z", "display_name": None, "supported_in_api": None, "visibility": None},
    ]
    aliases: set[str] = set()
    cmc.collect_policy_aliases({"nested": ["x", {"a": "a", "b": "x"}, ["a"], "x"]}, {"a", "x"}, aliases)
    assert aliases == {"a", "x"}


def test_check_model_catalog_routes_and_manifest_paths(tmp_path, monkeypatch):
    manifest = tmp_path / "job.manifest.json"
    config_path = tmp_path / "config.json"
    check_path = tmp_path / "check.json"
    check_path.write_text(
        json.dumps(
            {
                "harnesses": [
                    {"role": "r1", "model_check": {"status": "pass"}, "smoke": {"status": "pass"}},
                ],
                "status": "pass",
            }
        ),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps({"goal_config_path": "config.json", "goal_config_check_path": "check.json"}),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "models": {"r1": {"harness": "h1", "model": "m1", "provider": "p1"}},
                "harnesses": {"h1": {"kind": "codex"}},
            }
        ),
        encoding="utf-8",
    )

    class FakeContract:
        CODEX_ROUTE_MODELS = {"r1": "m1"}
        BRIDGE_ROUTE_ALIASES = {"bridge"}

        @staticmethod
        def is_bridge_alias(alias: str) -> bool:
            return alias in FakeContract.BRIDGE_ROUTE_ALIASES

        @staticmethod
        def bridge_model(alias: str) -> str:
            return "bridge-" + alias

        BRIDGE_PROVIDER_ID = "bridge"
        BRIDGE_HARNESS_KIND = "bridge-kind"

    contract = FakeContract()
    monkeypatch.setattr(cmc, "load_contract", lambda: contract)
    monkeypatch.setattr(cmc, "load_catalog", lambda *_: ({"models": [{"slug": "m1"}]}, "bundled", []))
    report = cmc.build_report(source="auto", require_codex=False, manifest=manifest)
    assert report["status"] == "pass"
    assert report["manifest_path"] == manifest.as_posix()
    assert report["goal_config_path"] == config_path.as_posix()
    assert report["goal_config_check_path"] == check_path.as_posix()


def test_check_model_catalog_main_blocks_conflicting_modes(monkeypatch):
    sys.argv = ["check_model_catalog.py", "--json", "--check"]
    with pytest.raises(SystemExit):
        cmc.main()


# --- runtime_lite_runner: CLI-path and bridge runtime guard coverage ---
def test_runtime_lite_runner_validator_helpers():
    with pytest.raises(SystemExit):
        rlr.string_value({}, "missing")
    with pytest.raises(SystemExit):
        rlr.int_value({}, "missing")
    with pytest.raises(SystemExit):
        rlr.list_value({}, "missing")


def test_runtime_lite_runner_verify_inputs_stale_and_hash(tmp_path):
    config = {"base_dir": str(tmp_path)}
    (tmp_path / "src.py").write_text("data", encoding="utf-8")
    expected_hash = "sha256:bad"
    inputs = {"source_files": [{"path": "src.py", "sha256": expected_hash, "size_bytes": 999}]}
    ok, msg = rlr.verify_inputs_current(config, inputs)
    assert ok is False
    assert msg.startswith("Lite input stale")
    inputs = {"source_files": [{"path": "../bad.py", "sha256": "x", "size_bytes": 1}]}
    ok, msg = rlr.verify_inputs_current(config, inputs)
    assert ok is False
    assert "escaped" in msg


def test_runtime_lite_runner_verify_file_hash_rejects_missing_and_stale(tmp_path):
    ok, msg = rlr.verify_file_hash(tmp_path / "missing.txt", "sha256:" + "0" * 64, "prompt")
    assert ok is False
    assert "missing" in msg
    source = tmp_path / "source.txt"
    source.write_text("x", encoding="utf-8")
    digest = rlr.sha256_file(source)
    assert digest is not None
    ok, msg = rlr.verify_file_hash(source, "sha256:" + "1" * 64, "prompt")
    assert ok is False
    assert "stale" in msg


def test_runtime_lite_runner_bridge_control_validation(tmp_path):
    good = {"bridge_control_script": str(tmp_path / "opencode_worker.py"), "bridge_control_version": "v1"}
    (tmp_path / "opencode_worker.py").write_text("ok", encoding="utf-8")
    ok, _ = rlr.verify_bridge_control({}, good)
    assert ok is True
    bad = {"bridge_control_script": "../bad.py", "bridge_control_version": "v1"}
    ok, msg = rlr.verify_bridge_control({}, bad)
    assert ok is False and "unavailable" in msg


def test_runtime_lite_runner_run_bridge_subcommand_rejects_missing_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(rlr.shutil, "which", lambda _: None)
    rc = rlr.run_bridge_subcommand(
        {"timeout_kill_after_seconds": 1, "attempt_timeout_seconds": 1},
        Path("/tmp/opencode_worker.py"),
        "delegate",
        [],
        cwd=tmp_path,
        stdout_path=tmp_path / "out.log",
    )
    assert rc == 127
    assert (tmp_path / "out.log").read_text(encoding="utf-8") == rlr.TIMEOUT_NOT_FOUND


def test_runtime_lite_runner_extract_advice_json_marker_contract(tmp_path):
    cfg = {"status_begin": "<BEGIN>", "status_end": "<END>"}
    raw = tmp_path / "raw.txt"
    out = tmp_path / "out.json"
    raw.write_text('x<BEGIN>{"answer": 1}<END>y', encoding="utf-8")
    assert rlr.extract_advice_json(raw, out, cfg) is True
    assert json.loads(out.read_text(encoding="utf-8")) == {"answer": 1}


def test_runtime_lite_runner_map_bridge_artifacts_reads_jsonl(tmp_path):
    run_dir = tmp_path / "bridge-run"
    run_dir.mkdir()
    (run_dir / rlr.BRIDGE_JOB_ENVELOPE_NAME).write_text(
        json.dumps(
            {
                "status": "passed",
                "timestamps": {"started_at": "2026-06-18T10:00:00Z", "completed_at": "2026-06-18T10:00:01Z"},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / rlr.BRIDGE_WORKER_STATUS_NAME).write_text(json.dumps({"lifecycle": "done"}), encoding="utf-8")
    (run_dir / rlr.BRIDGE_SUPERVISOR_VERDICT_NAME).write_text(
        json.dumps({"status": "done", "usage": {"tokens": 7}}), encoding="utf-8"
    )
    (run_dir / "delegation-report.json").write_text(json.dumps({"assistant_text": "ok"}), encoding="utf-8")
    mapped = rlr.map_bridge_artifacts(run_dir)
    assert mapped["returncode"] == 0
    assert mapped["passed"] is True
    assert mapped["usage"] == {"tokens": 7}


def test_runtime_lite_runner_main_flow_blocks_on_stale_prompt(tmp_path, monkeypatch):
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    prompt = packet_dir / "prompt.md"
    task = packet_dir / "task.md"
    prompt.write_text("prompt text", encoding="utf-8")
    task.write_text("task text", encoding="utf-8")
    control = packet_dir / "opencode_worker.py"
    control.write_text("ok", encoding="utf-8")
    inputs = {
        "source_files": [],
        "prompt_sha256": "sha256:" + "0" * 64,
        "task_sha256": vla.sha256_text(task.read_text(encoding="utf-8")),
        "bridge_control_script": str(control),
        "bridge_control_version": "1",
    }
    config = {
        "schema_version": 1,
        "role": "lite_advisor",
        "packet_id": "pack1",
        "purpose": "main-summary",
        "base_dir": str(tmp_path),
        "inputs_name": "input-files.json",
        "prompt_name": "prompt.md",
        "task_name": "task.md",
        "output_name": "advice.json",
        "raw_name": "advice.raw.txt",
        "telemetry_name": "telemetry.json",
        "validation_script": "validate.py",
        "telemetry_script": "telemetry.py",
        "status_begin": "<BEGIN>",
        "status_end": "<END>",
        "attempts": [{"provider_id": "deepseek"}],
        "permission_profile": "read-only",
        "model": "x",
        "variant": "max",
    }
    (packet_dir / "launch-config.json").write_text(json.dumps(config), encoding="utf-8")
    (packet_dir / "input-files.json").write_text(json.dumps(inputs), encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run(args, **kwargs):
        calls.append(list(args))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(rlr.subprocess, "run", _fake_run)
    rc = rlr.run_packet(packet_dir)
    assert rc == 0
    output = json.loads((packet_dir / "advice.json").read_text(encoding="utf-8"))
    assert output["status"] == "blocked"


# --- validate_lite_advice: override-aware purpose checks and happy-path contract validation ---
def test_validate_lite_advice_allows_unavailable_control_when_blocked():
    defects: list[str] = []
    vla.validate_bridge_envelope(
        defects,
        {
            "bridge_control_script": "",
            "bridge_control_version": "unavailable",
            "provider": vla.BRIDGE_PROVIDER_ID,
            "variant": vla.LITE_VARIANT,
            "permission_profile": vla.LITE_PERMISSION_PROFILE,
        },
        lite_status="blocked",
    )
    assert defects == []


def test_validate_lite_advice_enforces_control_for_non_blocked():
    defects: list[str] = []
    vla.validate_bridge_envelope(
        defects,
        {
            "bridge_control_script": "",
            "bridge_control_version": "unavailable",
            "provider": vla.BRIDGE_PROVIDER_ID,
            "variant": vla.LITE_VARIANT,
            "permission_profile": vla.LITE_PERMISSION_PROFILE,
        },
        lite_status="ok",
    )
    assert "may be unavailable only for blocked Lite advice" in defects[-1]


def test_validate_lite_advice_validate_telemetry_path_required(monkeypatch):
    defects: list[str] = []
    vla.validate_telemetry(defects, None, packet_id="p1", lite_status="ok")
    assert defects == ["telemetry.json: requires --inputs so packet telemetry can be verified"]


def test_validate_lite_advice_valid_packet_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path), os.environ.get("PATH", "")]))
    vla.SKILL_NAME_OVERRIDE = "goal-main-orchestrator"
    control = tmp_path / "opencode_worker.py"
    control.write_text("ok", encoding="utf-8")
    task_text = "Task text"
    task_path = tmp_path / "task.md"
    prompt_path = tmp_path / "prompt.md"
    task_path.write_text(task_text, encoding="utf-8")

    packet_id = "pkt-main-1"
    base_inputs = {
        "packet_id": packet_id,
        "purpose": "main-summary",
        "avoids_action": "edits",
        "expected_savings_reason": "route speed",
        "base_dir": str(tmp_path),
        "source_files": [],
        "bridge_control_script": str(control),
        "bridge_control_version": "1.0",
        "provider": vla.BRIDGE_PROVIDER_ID,
        "variant": vla.LITE_VARIANT,
        "permission_profile": vla.LITE_PERMISSION_PROFILE,
        "model": vla.LITE_MODEL,
        "alias": vla.LITE_ROUTE_ALIAS,
        "skill": "goal-main-orchestrator",
    }
    base_inputs["task_sha256"] = vla.sha256_text(task_text)
    prompt_text = vla.build_lite_prompt(
        packet_id,
        "main-summary",
        str(tmp_path),
        [],
        task_text,
        skill="goal-main-orchestrator",
        model=vla.LITE_MODEL,
        provider=vla.BRIDGE_PROVIDER_ID,
        variant=vla.LITE_VARIANT,
        control_script=str(control),
        control_version="1.0",
        permission_profile=vla.LITE_PERMISSION_PROFILE,
        task_sha256=base_inputs["task_sha256"],
        avoids_action="edits",
        expected_savings_reason="route speed",
    )
    base_inputs["prompt_sha256"] = vla.sha256_text(prompt_text)
    prompt_path.write_text(prompt_text, encoding="utf-8")

    advice = {
        "packet_id": packet_id,
        "role": "lite_advisor",
        "purpose": "main-summary",
        "avoids_action": "edits",
        "expected_savings_reason": "route speed",
        "status": "ok",
        "source_files": [],
        "recommended_reads": [],
        "risk_flags": [],
        "advice": {},
        "summary": "ok",
        "blockers": [],
        "commands_run": [vla.advice_command(str(control))],
    }
    telemetry = {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": "lite_advisor",
        "prompt_chars": 3,
        "prompt_bytes": 3,
        "output_chars": 3,
        "output_bytes": 3,
        "event_log_chars": 3,
        "event_log_bytes": 3,
        "attempts": [
            {
                "alias": vla.LITE_ROUTE_ALIAS,
                "provider": vla.BRIDGE_HARNESS_KIND,
                "model": vla.LITE_MODEL,
                "variant": vla.LITE_VARIANT,
                "called": True,
                "accepted": False,
            }
        ],
    }
    inputs_path = tmp_path / "input-files.json"
    inputs_path.write_text(json.dumps(base_inputs), encoding="utf-8")
    (tmp_path / "task.md").write_text(task_text, encoding="utf-8")
    (tmp_path / "prompt.md").write_text(prompt_text, encoding="utf-8")
    (tmp_path / "telemetry.json").write_text(json.dumps(telemetry), encoding="utf-8")
    defects = vla.validate(
        advice,
        packet_id=packet_id,
        purpose="main-summary",
        expected_sources=[],
        inputs=base_inputs,
        inputs_path=inputs_path,
    )
    assert defects == []
