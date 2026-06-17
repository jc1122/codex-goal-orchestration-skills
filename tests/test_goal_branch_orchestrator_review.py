"""Regression tests for the 2026-06-18 deep-review fixes (goal-branch-orchestrator).

Pins the verified defects:
- validate_branch_status.main() fails closed on malformed status/manifest (the goal-main bug class);
- a manifest branch entry missing review_path is now a defect (was a silent review-evidence bypass);
- assemble_branch_status degrades malformed semi-trusted artifacts to blockers, never aborts;
- create_runtime_packet.load_json fails closed; dead telemetry_function removed;
- promote_worker_repair_evidence tolerates non-list evidence fields;
- the pre-review-gate reviewer-reuse allowlist includes the bridge routes.
"""

import subprocess
import sys

import pytest
from conftest import REPO, load_module

vbs = load_module("skills/goal-branch-orchestrator/scripts/validate_branch_status.py", "vbs_review")
asm = load_module("skills/goal-branch-orchestrator/scripts/assemble_branch_status.py", "asm_review")
crp = load_module("skills/goal-branch-orchestrator/scripts/create_runtime_packet.py", "crp_review")
prw = load_module("skills/goal-branch-orchestrator/scripts/promote_worker_repair_evidence.py", "prw_review")


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


# --- pre-review-gate reviewer-reuse allowlist includes the bridge routes ---
def test_reviewer_allowed_aliases_include_bridge_routes():
    assert "ds-pro-max" in vbs.REVIEWER_ALLOWED_ALIASES
    assert "ds-flash-max" in vbs.REVIEWER_ALLOWED_ALIASES
