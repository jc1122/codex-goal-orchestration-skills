"""Regression tests for the 2026-06-18 deep-review fixes (goal-plan-amender).

Pins the verified defects:
- amendment_lib.load_json_object fails closed on malformed JSON (the shared helper that
  every amender script — validate_proposal / create_adaptation_packet / create_amendment_
  decision / create_blocker_repair_packet / validate_manifest_amendment — reads through);
- create_blocker_repair_packet.safe_path no longer silently drops `.github/` paths.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-plan-amender" / "scripts"))

amendment_lib = load_module("skills/goal-plan-amender/scripts/amendment_lib.py", "amlib_review")
cbr = load_module("skills/goal-plan-amender/scripts/create_blocker_repair_packet.py", "cbr_review")
cap = load_module("skills/goal-plan-amender/scripts/create_adaptation_packet.py", "cap_review")
rec = load_module("skills/goal-plan-amender/scripts/recommend_amendment_decision.py", "rec_review")
vap = load_module("skills/goal-plan-amender/scripts/validate_amender_packet.py", "vap_review")


# --- 2026-06-18 convergence pass 2: the protected-branch inference helpers convert a malformed
#     ledger/status artifact into a ValueError (so validate_proposal's `except ValueError` records a
#     defect) — `except Exception` could not catch the SystemExit load_json_object raises ---
def test_scheduler_state_raises_valueerror_on_malformed_ledger(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "scheduler.json").write_text("{ not json", encoding="utf-8")
    manifest = {"parallelization": {"scheduler_path": "scheduler.json"}}
    with pytest.raises(ValueError):  # NOT a SystemExit escaping past the wrapper
        amendment_lib.scheduler_state(manifest_path, manifest)


def test_status_file_terminal_state_raises_valueerror_on_malformed_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("{ not json", encoding="utf-8")
    manifest = {"branches": [{"id": "B01", "status_path": "branches/B01.status.json"}]}
    with pytest.raises(ValueError):
        amendment_lib.status_file_terminal_state(manifest_path, manifest)


# --- 2026-06-18 convergence pass 2: recommend's status-file loader skips a malformed status file
#     instead of crashing (the `except Exception: continue` could not skip a SystemExit) ---
def test_load_terminal_status_files_skips_malformed(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("{ not json", encoding="utf-8")
    branches = [{"id": "B01", "status_path": "branches/B01.status.json"}]
    assert rec.load_terminal_status_files(tmp_path / "job.manifest.json", branches) == {}  # must not raise


# --- 2026-06-18 convergence pass 2: review_evidence_record raises SystemExit on a malformed review
#     file — the reason create_packet's tolerant wrapper had to broaden to (Exception, SystemExit) ---
def test_review_evidence_record_fails_closed_on_malformed(tmp_path):
    bad = tmp_path / "review.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cbr.review_evidence_record(bad)


# --- 2026-06-18 convergence pass 2: _validate_ladder_telemetry records a defect (no traceback)
#     when amender_telemetry_attempts cannot derive expected attempts (e.g. tampered selected_ladder) ---
def test_validate_ladder_telemetry_fails_closed_on_attempt_error(monkeypatch):
    def boom(*_a, **_k):
        raise SystemExit("tampered selected_ladder")

    monkeypatch.setattr(vap, "amender_telemetry_attempts", boom)
    defects: list[str] = []
    vap._validate_ladder_telemetry(
        defects,
        {"accepted_alias": None},
        [],
        selected=["ds-pro-max"],
        manifest={},
        manifest_path=Path("/abs/job.manifest.json"),
    )
    assert any("could not derive expected plan-amender attempts" in d for d in defects), defects


# --- 2026-06-18 convergence pass: _reconcile_protected_ids wraps protected_ids' ValueError as a
#     clean SystemExit, matching its three sibling scripts (was the lone unguarded outlier) ---
def test_reconcile_protected_ids_wraps_valueerror(monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("scheduler ledger events must be an array for protected branch inference")

    monkeypatch.setattr(cap, "protected_ids", boom)
    args = SimpleNamespace(active_branch=[], terminal_branch=[])
    inputs = SimpleNamespace(manifest_path=Path("/abs/job.manifest.json"), manifest={})
    with pytest.raises(SystemExit):
        cap._reconcile_protected_ids(args, inputs, {})


# --- 2026-06-18 convergence pass 6 (proactive sweep): a non-list active/terminal_branch_ids in the
#     decision artifact yields a clean SystemExit (mismatch), not a TypeError on the comprehension ---
def test_reconcile_protected_ids_tolerates_non_list_decision_ids(monkeypatch):
    monkeypatch.setattr(cap, "protected_ids", lambda *_a, **_k: (["B01"], [], {}))
    args = SimpleNamespace(active_branch=[], terminal_branch=[])
    inputs = SimpleNamespace(manifest_path=Path("/abs/job.manifest.json"), manifest={})
    with pytest.raises(SystemExit):  # clean mismatch, NOT TypeError on the non-list comprehension
        cap._reconcile_protected_ids(args, inputs, {"active_branch_ids": 5, "terminal_branch_ids": []})


# --- 2026-06-18 convergence pass 4: create_blocker_repair branch iterations tolerate a non-list
#     `branches`/`obsolete_branches` (.get(k, []) only defaults on an ABSENT key, not a present null) ---
def test_blocker_repair_branch_iterations_tolerate_non_list():
    assert cbr.all_owned_paths({"branches": None}) == {}  # used to raise TypeError
    assert isinstance(cbr.next_branch_id({"branches": 5, "obsolete_branches": None}, 0), str)


# --- 2026-06-18 convergence pass 4: generate_proposal fails closed on a malformed --emit-proposal
#     input-files.json (direct dict subscripts used to KeyError) ---
def test_generate_proposal_requires_fields():
    with pytest.raises(SystemExit):
        cbr.generate_proposal({"repo_root": "/r", "amendment_id": "A1", "job_id": "j1"})  # missing manifest


# --- 2026-06-18 convergence pass 5: the remaining nested-field iterations in create_blocker_repair
#     (blockers/worker_statuses/owned_paths/recovers_from) also route through _as_list ---
def test_blockers_from_status_tolerates_non_list():
    assert cbr.blockers_from_status({"blockers": 5, "worker_statuses": 7}) == []  # used to raise TypeError
    assert cbr.blockers_from_status({"blockers": ["b1"], "worker_statuses": [{"blockers": 9}]}) == ["b1"]
    assert cbr.all_owned_paths({"branches": [{"id": "B01", "owned_paths": 5}]}) == {}  # non-list owned_paths


# --- 2026-06-18 convergence pass 6: generate_proposal tolerates a present non-list
#     terminal_branch_ids in input-files.json (.get(k, []) only defaults on an ABSENT key) ---
def test_generate_proposal_tolerates_non_list_terminal_ids(tmp_path):
    manifest = tmp_path / "job.manifest.json"
    manifest.write_text(json.dumps({"job_id": "j1", "branches": []}), encoding="utf-8")
    inp = {
        "manifest": str(manifest),
        "repo_root": str(tmp_path),
        "amendment_id": "A1",
        "job_id": "j1",
        "terminal_branch_ids": None,  # used to raise TypeError in the comprehension
    }
    assert isinstance(cbr.generate_proposal(inp), dict)  # must not raise


# --- shared loader fails closed (SystemExit) on malformed JSON, not a raw traceback ---
def test_load_json_object_fails_closed(tmp_path):
    bad = tmp_path / "job.manifest.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        amendment_lib.load_json_object(bad)
    # a valid non-object still fails closed with the existing clean message
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        amendment_lib.load_json_object(arr)
    # a well-formed object loads
    good = tmp_path / "good.json"
    good.write_text('{"k": 1}', encoding="utf-8")
    assert amendment_lib.load_json_object(good) == {"k": 1}
    # Pass-3: a non-UTF-8 file (UnicodeDecodeError, a ValueError) also fails closed
    nonutf8 = tmp_path / "bad-bytes.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        amendment_lib.load_json_object(nonutf8)


# --- safe_path keeps .github/ paths (leading dot no longer stripped) ---
def test_safe_path_preserves_github_and_strips_wrapping():
    # .github support is intentional (FILE_RE / ALLOWED_PREFIXES include it) and must not be dropped.
    assert cbr.safe_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"
    # wrapping punctuation and trailing prose dots are still stripped
    assert cbr.safe_path("`src/app.py`,") == "src/app.py"
    assert cbr.safe_path("src/app.py.") == "src/app.py"
    # traversal / absolute paths are still rejected
    assert cbr.safe_path("../escape.py") is None
    assert cbr.safe_path("/etc/passwd") is None
