"""Regression tests for the 2026-06-18 whole-skillset cross-cutting review.

Pins the cross-stage / consistency fixes that the per-skill passes could not see:
- amendment-decision sha matcher accepts the archived (pre-amendment) manifest sha, so a
  launched+applied amendment no longer blocks main `pass` (HIGH cross-stage bug);
- the two previously gate-dark runtime runners get pure-validator coverage;
- representative fail-closed readers that were missed by the per-skill sweep.
"""

import json
import sys

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-preflight" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-branch-orchestrator" / "scripts"))

ams = load_module("skills/goal-main-orchestrator/scripts/assemble_main_status.py", "ams_ws")
rpa = load_module("skills/goal-main-orchestrator/scripts/runtime_prompt_audit_runner.py", "rpa_ws")
rlr = load_module("skills/_goal_shared/scripts/runtime_lite_runner.py", "rlr_ws")
cprg = load_module("skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py", "cprg_ws")
cgb = load_module("skills/goal-preflight/scripts/create_goal_bundle.py", "cgb_ws")
crel = load_module("scripts/check_release.py", "crel_ws")


# --- 2026-06-18 convergence pass 9: the release gate enforces that the harness-contract gate
#     ships in the package and is wired into npm run check (it could silently fall out before) ---
def test_check_release_enforces_harness_gate():
    assert "scripts/check_harness_contract.py" in crel.REQUIRED_PACKAGE_FILES
    assert "scripts/check_harness_contract.py" in crel.REQUIRED_PACKAGE_FILES_ENTRIES


# --- HIGH: amendment decision keyed to the archived (pre-apply) manifest sha is NOT dropped ---
def test_amendment_decision_accepts_archived_manifest_sha(tmp_path):
    bundle = tmp_path
    manifest = bundle / "job.manifest.json"
    manifest.write_text(json.dumps({"job_id": "phaseX", "v": "current"}), encoding="utf-8")
    amendments = bundle / "amendments"
    amendments.mkdir()
    # the archived pre-amendment manifest has a DIFFERENT sha than the current one
    archived = amendments / "A001.job.manifest.before.json"
    archived.write_text(json.dumps({"job_id": "phaseX", "v": "before"}), encoding="utf-8")
    pre_apply_sha = ams.sha256_file(archived)
    assert pre_apply_sha != ams.sha256_file(manifest)
    (amendments / "A001.decision.json").write_text(
        json.dumps(
            {
                "amendment_id": "A001",
                "decision": "skip",
                "manifest": manifest.as_posix(),
                "manifest_sha256": pre_apply_sha,  # recorded at decision time, before apply
                "terminal_branch_ids": ["B01"],
                "terminal_branch_statuses": {"B01": "pass"},
            }
        ),
        encoding="utf-8",
    )
    branch_statuses = [{"branch_id": "B01", "status": "pass"}]
    blockers: list[str] = []
    records, covered, ignored = ams.current_amendment_records(manifest, branch_statuses, {"active_ids": []}, blockers)
    assert any(r["amendment_id"] == "A001" for r in records), (records, ignored, blockers)
    assert "B01" in covered
    assert not any("ignored stale amendment decision" in b for b in blockers), blockers


# --- runtime_prompt_audit_runner: pure audit validator now covered (was gate-dark) ---
def _valid_audit():
    return {
        "manifest": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "status": "pass",
        "can_start": True,
        "checked_files": ["job.manifest.json"],
        "missing_dod_items": [],
        "commands_run": ["python3 deterministic_prompt_audit.py"],
        "defects": [],
        "actionability_verdict": "pass",
        "summary": "audit ok",
    }


def test_valid_audit_data():
    good = _valid_audit()
    assert rpa.valid_audit_data(good, manifest="/abs/job.manifest.json", repo_root="/abs/repo") is True
    # identity mismatch rejected
    assert rpa.valid_audit_data(good, manifest="/other", repo_root="/abs/repo") is False
    # missing required field rejected
    missing = {k: v for k, v in good.items() if k != "actionability_verdict"}
    assert rpa.valid_audit_data(missing, manifest="/abs/job.manifest.json", repo_root="/abs/repo") is False
    assert rpa.valid_defects([]) is True
    assert rpa.valid_defects("nope") is False


# --- runtime_lite_runner: importable + bridge-artifact mapper degrades on an empty run dir ---
def test_lite_runner_map_bridge_artifacts_no_crash(tmp_path):
    result = rlr.map_bridge_artifacts(tmp_path)  # no artifacts present
    assert isinstance(result, dict)


# --- representative fail-closed readers now fail closed (SystemExit) on malformed JSON ---
def test_readers_fail_closed_on_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cprg.read_json(bad)
    with pytest.raises(SystemExit):
        cgb.load_goal_config(bad)
