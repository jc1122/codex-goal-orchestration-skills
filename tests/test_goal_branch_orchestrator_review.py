"""Regression tests for the 2026-06-18 deep-review fixes (goal-branch-orchestrator).

Pins the verified defects:
- validate_branch_status.main() fails closed on malformed status/manifest (the goal-main bug class);
- a manifest branch entry missing review_path is now a defect (was a silent review-evidence bypass);
- assemble_branch_status degrades malformed semi-trusted artifacts to blockers, never aborts;
- create_runtime_packet.load_json fails closed; dead telemetry_function removed;
- promote_worker_repair_evidence tolerates non-list evidence fields;
- the pre-review-gate reviewer-reuse allowlist includes the bridge routes.
"""

import json
import subprocess
import sys

import pytest
from conftest import REPO, load_module

vbs = load_module("skills/goal-branch-orchestrator/scripts/validate_branch_status.py", "vbs_review")
asm = load_module("skills/goal-branch-orchestrator/scripts/assemble_branch_status.py", "asm_review")
crp = load_module("skills/goal-branch-orchestrator/scripts/create_runtime_packet.py", "crp_review")
prw = load_module("skills/goal-branch-orchestrator/scripts/promote_worker_repair_evidence.py", "prw_review")
rpr = load_module("skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py", "rpr_review")
cprg = load_module("skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py", "cprg_review")


# --- 2026-06-18 convergence pass 6: pre-review-gate nested-field iterations over semi-trusted
#     branch-status artifacts tolerate non-list values instead of TypeError ---
def test_ownership_check_tolerates_non_list_fields():
    assert isinstance(cprg.ownership_check({"owned_paths": None}, {"changed_files": 5}), dict)  # was TypeError


def test_worker_pass_defects_tolerates_non_list_finished_ids(tmp_path):
    branch_status = {
        "worker_statuses": [],
        "worker_parallelism": {"active_ids": [], "blocked_ids": [], "deferred_ids": [], "finished_ids": None},
    }
    assert isinstance(cprg.worker_pass_defects(tmp_path, {"work_items": []}, branch_status, "B01"), list)


def test_packet_terminal_defects_degrades_malformed_launcher(tmp_path):
    # the read_json SystemExit is now caught at the call site -> conservative defect, not a crash
    pdir = tmp_path / "workers" / "P01"
    pdir.mkdir(parents=True)
    (pdir / "launcher-state.json").write_text("{ not json", encoding="utf-8")
    defects = cprg.packet_terminal_defects(tmp_path, {"work_items": []}, "B01", "P01", {"status": "pass"})
    assert any("not a readable JSON object" in d for d in defects), defects


def test_reviewer_branch_status_context_tolerates_non_list_blockers(tmp_path):
    (tmp_path / "B01.status.json").write_text(json.dumps({"status": "pass", "blockers": 5}), encoding="utf-8")
    ctx = crp.reviewer_branch_status_context(tmp_path, {"status_path": "B01.status.json"}, {"status": "pass"})
    assert isinstance(ctx, dict)  # was TypeError on the non-list blockers comprehension


# --- 2026-06-18 convergence pass 3: create_pre_review_gate.read_json fails closed on a non-UTF-8
#     worker artifact (launcher-state/packet.summary are worker-produced) ---
def test_create_pre_review_gate_read_json_fails_closed_on_non_utf8(tmp_path):
    nonutf8 = tmp_path / "launcher-state.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        cprg.read_json(nonutf8)


# --- 2026-06-18 convergence pass 8: the porcelain-prefix rejection in changed_files paths is
#     exercised (the contract MUST was enforced only by the gate-dark reject_porcelain branch) ---
def test_validate_path_list_rejects_porcelain_prefix():
    defects: list[str] = []
    vbs.validate_path_list(defects, [" M src/foo.py"], "$.changed_files")
    assert any("porcelain" in d.lower() for d in defects), defects
    clean: list[str] = []
    vbs.validate_path_list(clean, ["src/foo.py"], "$.changed_files")
    assert clean == []


# --- 2026-06-18 convergence pass 9: load_task tolerates a non-UTF-8 --task-file (errors="replace",
#     matching the validator side) instead of crashing packet creation ---
def test_load_task_tolerates_non_utf8(tmp_path):
    f = tmp_path / "task.md"
    f.write_bytes(b"\xff\xfe task body")
    assert isinstance(crp.load_task(f), str)  # was UnicodeDecodeError


# --- 2026-06-18 convergence pass 7: configured_telemetry_attempts guards a non-dict
#     goal_config.models/harnesses (.get on a non-dict used to AttributeError) ---
def test_worker_telemetry_attempts_tolerates_non_dict_goal_config():
    with pytest.raises(SystemExit):  # missing-role SystemExit, NOT AttributeError on 5.get(...)
        crp.worker_telemetry_attempts(["codex-spark"], {"models": 5, "harnesses": []})


# --- 2026-06-18 convergence pass 7: the research-worker read-only command-policy security branch
#     is exercised (only the secret-marker sibling had coverage before) ---
def test_validate_research_security_flags_forbidden_commands():
    defects: list[str] = []
    vbs.validate_research_security(defects, ["git push origin HEAD"], [], "$.research")
    assert any("read-only security policy" in d for d in defects), defects
    clean: list[str] = []
    vbs.validate_research_security(clean, ["rg foo src/", "cat README.md"], [], "$.research")
    assert clean == []


# --- 2026-06-18 convergence pass 5: the base_ref command-injection gate is exercised (the 3rd
#     sibling security gate; reject branches were gate-dark). Both byte-copies are tested. ---
def test_validate_base_ref_rejects_injection():
    for mod in (cprg, asm):
        assert mod.validate_base_ref("main") == "main"
        assert mod.validate_base_ref(" release/1.2 ") == "release/1.2"
        for bad in ("-rf", "a..b", "main; rm -rf /", "x.lock", "feat/", ""):
            with pytest.raises(SystemExit):
                mod.validate_base_ref(bad)


# --- 2026-06-18 convergence pass 4: nested-field iteration guards — a non-list `tests` /
#     work-item `dod` in a worker/manifest artifact is skipped, not a TypeError ---
def test_collect_worker_tests_tolerates_non_list():
    assert asm.collect_worker_tests([{"tests": 5}]) == []  # used to raise TypeError


def test_collect_manifest_dod_tolerates_non_list_work_item_dod():
    result = asm.collect_manifest_dod({"dod": ["d1"], "work_items": [{"dod": 7}]})  # must not raise
    assert result == ["d1"]


# --- 2026-06-18 convergence pass 4: the worker mutating-command security gate is exercised
#     (was gate-dark, like its research sibling) ---
def test_validate_worker_command_evidence_flags_git_mutation():
    defects: list[str] = []
    vbs.validate_worker_command_evidence(defects, ["git push origin HEAD"], "$.w.commands_run")
    assert any("must not list mutating command evidence" in d for d in defects), defects
    clean: list[str] = []
    vbs.validate_worker_command_evidence(clean, ["pytest -q"], "$.w.commands_run")
    assert clean == []


# --- 2026-06-18 convergence pass 3: the research-worker secret-marker security branches are
#     exercised (were gate-dark — a silent regression would let a worker reading .ssh/id_rsa pass) ---
def test_validate_research_security_flags_secret_markers():
    defects: list[str] = []
    vbs.validate_research_security(defects, ["cat .env"], [".ssh/id_rsa"], "$.research")
    assert sum("secret or credential material" in d for d in defects) >= 2, defects


# --- validate_branch_status: the gate fails closed on malformed JSON (no traceback) ---
def test_validate_branch_status_cli_fails_closed_on_malformed_json(tmp_path):
    status_path = tmp_path / "B01.status.json"
    status_path.write_text("not valid json {", encoding="utf-8")
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills" / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py"),
            "--status",
            str(status_path),
            "--manifest",
            str(manifest_path),
            "--branch-id",
            "B01",
            "--branch",
            "phaseX-B01",
        ],
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in proc.stderr, f"validator crashed instead of failing closed:\n{proc.stderr}"
    assert "readable JSON" in combined, combined


# --- a manifest branch entry missing review_path is a defect (no silent review bypass) ---
def test_manifest_branch_identity_requires_review_path(tmp_path):
    defects: list[str] = []
    branch_entry = {
        "branch_name": "phaseX-B01",
        "status_path": "branches/B01.status.json",
        "pre_review_gate_path": "branches/B01.pre_review_gate.json",
        # review_path intentionally omitted
    }
    vbs.validate_manifest_branch_identity(
        defects,
        {"branch": "phaseX-B01"},
        branch_entry,
        branch_id="B01",
        manifest_path=tmp_path / "job.manifest.json",
        status_path=tmp_path / "branches" / "B01.status.json",
    )
    assert any("review_path" in d for d in defects), defects


# --- assemble_branch_status: malformed semi-trusted artifacts degrade, never abort ---
def test_assemble_read_helpers_fail_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        asm.read_json(bad)  # malformed JSON now fails clean (SystemExit), not a traceback
    blockers: list[str] = []
    assert asm.read_object_or_blocker(bad, blockers, "worker artifact") is None
    assert asm.read_object_or_blocker(arr, blockers, "worker artifact") is None
    assert len(blockers) == 2


# --- create_runtime_packet: load_json fails closed; dead telemetry_function removed ---
def test_create_runtime_packet_load_json_fails_closed(tmp_path):
    bad = tmp_path / "job.manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        crp.load_json(bad)


def test_dead_telemetry_function_removed():
    assert not hasattr(crp, "telemetry_function")


# --- promote_worker_repair_evidence: non-list evidence fields do not crash ---
def test_evidence_commands_tolerates_non_list_fields():
    # Non-list local_validation / commands_run / tests used to raise TypeError; now they are
    # handled gracefully and the function reaches its normal clean validation (SystemExit).
    with pytest.raises(SystemExit) as exc:
        prw.evidence_commands({"local_validation": 5, "commands_run": None, "tests": True})
    assert "git diff --check" in str(exc.value)  # clean validation, not a TypeError crash
    # With valid git-diff + test commands and non-list siblings, it returns without crashing.
    commands, tests = prw.evidence_commands(
        {
            "local_validation": [{"command": "git diff --check main...HEAD"}, {"command": "pytest tests/test_x.py"}],
            "commands_run": None,
            "tests": True,
        }
    )
    assert "git diff --check main...HEAD" in commands and any("pytest" in t for t in tests)


# --- 2026-06-18 convergence pass: a malformed referenced goal_config file records a defect
#     instead of crashing the defect-collecting validator with a raw JSONDecodeError ---
def test_goal_config_from_manifest_fails_closed_on_malformed_config(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text('{"goal_config_path": "goal-config.json"}', encoding="utf-8")
    (tmp_path / "goal-config.json").write_text("{ not json", encoding="utf-8")
    manifest_root = {"goal_config_path": "goal-config.json"}
    defects: list[str] = []
    # Must NOT raise: a valid manifest pointing at a malformed config used to escape main()
    # as an unhandled JSONDecodeError; now it is a structured defect.
    result = vbs.goal_config_from_manifest(defects, manifest_root, manifest_path)
    assert result is None
    assert any("goal_config_path" in d and "readable JSON" in d for d in defects), defects
    # A well-formed referenced config is still returned unchanged.
    (tmp_path / "goal-config.json").write_text('{"model_policies": {}}', encoding="utf-8")
    ok_defects: list[str] = []
    assert vbs.goal_config_from_manifest(ok_defects, manifest_root, manifest_path) == {"model_policies": {}}
    assert ok_defects == []


# --- pre-review-gate reviewer-reuse allowlist includes the bridge routes ---
def test_reviewer_allowed_aliases_include_bridge_routes():
    assert "ds-pro-max" in vbs.REVIEWER_ALLOWED_ALIASES
    assert "ds-flash-max" in vbs.REVIEWER_ALLOWED_ALIASES


# --- 2026-06-18 convergence pass: create_runtime_packet's tolerant readers catch the
#     SystemExit that load_json raises (except Exception alone could not), so a malformed/non-dict
#     runtime artifact degrades instead of crashing packet creation ---
def test_create_runtime_packet_tolerant_readers_absorb_systemexit(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")  # valid JSON, non-dict -> load_json SystemExit
    # read_json_or_none must return (None, msg), never propagate SystemExit
    data, err = crp.read_json_or_none(bad)
    assert data is None and err
    data2, err2 = crp.read_json_or_none(arr)
    assert data2 is None and err2
    # the reviewer-artifact summariser degrades to a structured "invalid json" marker
    assert crp._summarize_reviewer_artifact(bad) == {"exists": False, "reason": "invalid json"}
    # the scheduler closed-pass probe degrades to False, never crashes
    assert crp.scheduler_closed_pass_for_packet(bad, "P01") is False


# --- 2026-06-18 convergence pass: the debug-events reader tolerates a non-UTF-8 artifact
#     (errors="replace") instead of escaping the gate as a UnicodeDecodeError ---
def test_validate_launch_config_debug_events_tolerates_non_utf8(tmp_path):
    (tmp_path / "debug.events.jsonl").write_bytes(b"\xff\xfe garbage\n")
    defects: list[str] = []
    # must not raise UnicodeDecodeError; unparseable content becomes a structured defect
    vbs.validate_launch_config_debug_events(
        defects,
        {"debug_events_name": "debug.events.jsonl"},
        tmp_path,
        "$.launch_config",
        packet_id="P01",
    )
    assert any("debug_events_name" in d for d in defects), defects


# --- 2026-06-18 convergence pass: stale pre-bridge reviewer constants + transitively-dead
#     route maps were removed (no consumer; verified repo-wide) ---
def test_stale_reviewer_constants_removed():
    for name in (
        "REVIEWER_MODEL",
        "REVIEWER_FALLBACK_MODEL",
        "REVIEWER_MINI_MODEL",
        "RESEARCH_MODEL",
        "RESEARCH_FALLBACK_MODEL",
        "WORKER_ROUTE_LABELS",
        "WORKER_ROUTE_COMMANDS",
        "REVIEW_ROUTE_MODELS",
        "SPARK_MODEL",
        "MINI_MODEL",
    ):
        assert not hasattr(crp, name), name
    assert not hasattr(rpr, "BRIDGE_ISSUE_IDS")
    # Pass-2: ALLOWED_WORKER_ROUTES in create_runtime_packet was the lone leftover dead const
    assert not hasattr(crp, "ALLOWED_WORKER_ROUTES")
    # live siblings remain
    assert hasattr(crp, "WORKER_ROUTE_EVENT_LABELS")
    assert hasattr(crp, "CODEX_LEAN_EXEC_FLAGS_TEXT")


# --- 2026-06-18 convergence pass 2: the assembler's tolerant reader degrades a non-UTF-8 artifact
#     to a blocker instead of escaping as a UnicodeDecodeError (read_object_or_blocker only caught
#     SystemExit; read_json now fails closed on non-UTF-8 too) ---
def test_assemble_read_helpers_tolerate_non_utf8(tmp_path):
    nonutf8 = tmp_path / "status.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        asm.read_json(nonutf8)
    blockers: list[str] = []
    assert asm.read_object_or_blocker(nonutf8, blockers, "worker artifact") is None
    assert blockers
