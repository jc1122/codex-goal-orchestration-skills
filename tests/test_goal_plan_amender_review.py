"""Regression tests for the 2026-06-18 deep-review fixes (goal-plan-amender).

Pins the verified defects:
- amendment_lib.load_json_object fails closed on malformed JSON (the shared helper that
  every amender script — validate_proposal / create_adaptation_packet / create_amendment_
  decision / create_blocker_repair_packet / validate_manifest_amendment — reads through);
- create_blocker_repair_packet.safe_path no longer silently drops `.github/` paths.
"""

import json
import sys
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-plan-amender" / "scripts"))

amendment_lib = load_module("skills/goal-plan-amender/scripts/amendment_lib.py", "amlib_review")
cbr = load_module("skills/goal-plan-amender/scripts/create_blocker_repair_packet.py", "cbr_review")
cap = load_module("skills/goal-plan-amender/scripts/create_adaptation_packet.py", "cap_review")
cam = load_module("skills/goal-plan-amender/scripts/create_amendment_decision.py", "cam_review")
rec = load_module("skills/goal-plan-amender/scripts/recommend_amendment_decision.py", "rec_review")
vap = load_module("skills/goal-plan-amender/scripts/validate_amender_packet.py", "vap_review")
ama = load_module("skills/goal-plan-amender/scripts/apply_manifest_amendment.py", "ama_review")
vma = load_module("skills/goal-plan-amender/scripts/validate_manifest_amendment.py", "vma_review")


_BOOL_NOT_SET = object()


def _write_amender_packet_bundle(
    tmp_path: Path,
    *,
    scheduler_inference_enabled: object = _BOOL_NOT_SET,
    prompt_artifact: str = "input-files.json",
) -> tuple[Path, Path, Path]:
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    amendment_id = "A1"
    packet_dir = tmp_path / "amendments" / f"{amendment_id}.packet"
    packet_dir.mkdir(parents=True)
    decision_path = tmp_path / "amendments" / f"{amendment_id}.decision.json"
    decision_path.parent.mkdir(exist_ok=True)

    decision = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": "J1",
        "decision": "launch",
        "reason_code": "no_eligible_branch",
        "reason": "test",
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": amendment_lib.sha256_file(manifest_path),
        "terminal_branch_ids": ["B01"],
        "active_branch_ids": [],
        "terminal_branch_statuses": {"B01": "failed"},
    }
    if scheduler_inference_enabled is not _BOOL_NOT_SET:
        decision["scheduler_inference_enabled"] = scheduler_inference_enabled
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    proposal_path = tmp_path / "amendments" / f"{amendment_id}.proposal.json"
    proposal = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": "J1",
        "rationale": "test",
        "operations": [{"op": "add_branch", "branch": {"id": "B99", "title": "test"}}],
    }
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    deterministic_alias = getattr(amendment_lib.CONTRACT, "DETERMINISTIC_AMENDER_ALIAS", "deterministic-blocker-repair")
    route = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": amendment_lib.CONTRACT.AMENDER_ROLE,
        "mode": "deterministic_blocker_repair",
        "selected_ladder": [],
        "selection_reason": "deterministic",
        "policy": copy.deepcopy(manifest["amender_model_policy"]),
    }
    route_path = packet_dir / "route.json"
    route_path.write_text(json.dumps(route), encoding="utf-8")

    input_files = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "manifest": manifest_path.as_posix(),
        "decision_path": decision_path.as_posix(),
        "selected_ladder": [],
        "selection_reason": "deterministic",
        "active_branch_ids": [],
        "terminal_branch_ids": ["B01"],
        "terminal_branch_statuses": {"B01": "failed"},
        "source_files": [
            {
                "path": manifest_path.as_posix(),
                "sha256": amendment_lib.sha256_file(manifest_path),
            },
            {
                "path": decision_path.as_posix(),
                "sha256": amendment_lib.sha256_file(decision_path),
            },
        ],
    }
    input_path = packet_dir / "input-files.json"
    input_path.write_text(json.dumps(input_files), encoding="utf-8")
    (packet_dir / "prompt.md").write_text("amender prompt", encoding="utf-8")

    telemetry = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": amendment_lib.CONTRACT.AMENDER_ROLE,
        "output_artifact": "../A1.proposal.json",
        "prompt_artifact": prompt_artifact,
        "attempts": [
            {
                "alias": deterministic_alias,
                "provider": "local-script",
                "model": "goal-plan-amender.deterministic-blocker-repair",
                "timeout_seconds": 1,
                "called": True,
                "accepted": True,
            }
        ],
        "accepted_alias": deterministic_alias,
        "totals": {"attempts_declared": 1, "attempts_called": 1},
    }
    telemetry_path = packet_dir / "telemetry.json"
    telemetry_path.write_text(json.dumps(telemetry), encoding="utf-8")
    return manifest_path, packet_dir, proposal_path


# --- 2026-06-18 convergence pass 10: validate_input_files records a defect (not IsADirectoryError)
#     when a source_files entry's path is a directory ---
def test_validate_input_files_rejects_directory_source(tmp_path):
    defects: list[str] = []
    data = {
        "schema_version": 1,
        "amendment_id": "A1",
        "source_files": [{"path": str(tmp_path), "sha256": "sha256:" + "0" * 64}],
    }
    vap.validate_input_files(
        defects,
        data,
        amendment_id="A1",
        manifest_path=tmp_path / "job.manifest.json",
        decision_path=tmp_path / "A1.decision.json",
        route={},
    )  # must not raise IsADirectoryError
    assert any("regular file" in d or "does not exist" in d for d in defects), defects


# --- 2026-06-18 convergence pass 2: the protected-branch inference helpers convert a malformed
#     ledger/status artifact into a ValueError (so validate_proposal's `except ValueError` records a
#     defect) — `except Exception` could not catch the SystemExit load_json_object raises ---
def test_scheduler_state_raises_valueerror_on_malformed_ledger(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "scheduler.json").write_text("{ not json", encoding="utf-8")
    manifest = {"parallelization": {"scheduler_path": "scheduler.json"}}
    with pytest.raises(ValueError):  # NOT a SystemExit escaping past the wrapper
        amendment_lib.scheduler_state(manifest_path, manifest)


def test_scheduler_state_rejects_unknown_branch_ids(tmp_path):
    scheduler_dir = tmp_path / "schedulers"
    scheduler_dir.mkdir()
    (scheduler_dir / "main.scheduler.json").write_text(
        json.dumps({"events": [{"event": "launch", "id": "B99"}]}),
        encoding="utf-8",
    )
    manifest = {"branches": [{"id": "B01"}], "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"}}
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown scheduler/manifest branch ids: B99"):
        amendment_lib.scheduler_state(manifest_path, manifest)


def test_scheduler_state_considers_live_events_for_obsolete_manifest_ids(tmp_path):
    scheduler_dir = tmp_path / "schedulers"
    scheduler_dir.mkdir()
    (scheduler_dir / "main.scheduler.json").write_text(
        json.dumps(
            {
                "events": [
                    {"event": "launch", "id": "B01"},
                    {"event": "finish", "id": "B01", "status": "failed"},
                    {"event": "close", "id": "B01"},
                    {"event": "launch", "id": "B02"},
                    {"event": "finish", "id": "B02", "status": "pass"},
                    {"event": "close", "id": "B02"},
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "obsolete_branches": [{"branch_id": "B02"}],
        "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    active, terminal = amendment_lib.scheduler_state(manifest_path, manifest)
    assert active == set()
    assert terminal == {"B01": "failed", "B02": "pass"}


def test_validate_proposal_fails_closed_on_malformed_obsolete_branches(tmp_path):
    # Pre-fix: this case could crash with TypeError while iterating
    # `manifest.obsolete_branches` when it is present but not a list.
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "obsolete_branches": None,
        "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "schedulers").mkdir()
    (tmp_path / "schedulers" / "main.scheduler.json").write_text(json.dumps({"events": []}), encoding="utf-8")

    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "noop",
                "operations": [],
            }
        ),
        encoding="utf-8",
    )

    validation, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        terminal_branch_ids=["B01"],
        terminal_branch_statuses={"B01": "pass"},
        infer_scheduler=True,
        run_lint=False,
    )
    assert validation["status"] == "failed"
    assert any("manifest.obsolete_branches must be an array when present" in defect for defect in validation["defects"])


def test_status_file_terminal_state_raises_valueerror_on_malformed_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("{ not json", encoding="utf-8")
    manifest = {"branches": [{"id": "B01", "status_path": "branches/B01.status.json"}]}
    with pytest.raises(ValueError):
        amendment_lib.status_file_terminal_state(manifest_path, manifest)


def test_status_file_terminal_state_raises_valueerror_on_non_string_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(json.dumps({"status": []}), encoding="utf-8")
    manifest = {"branches": [{"id": "B01", "status_path": "branches/B01.status.json"}]}
    with pytest.raises(ValueError):
        amendment_lib.status_file_terminal_state(manifest_path, manifest)


def test_protected_ids_raises_valueerror_on_non_string_terminal_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(json.dumps({"status": {}}), encoding="utf-8")
    manifest = {"branches": [{"id": "B01", "status_path": "branches/B01.status.json"}]}
    with pytest.raises(ValueError):
        amendment_lib.protected_ids(manifest_path, manifest, terminal_ids=["B01"])


def test_validate_proposal_rejects_add_branch_obsolete_id_reuse(tmp_path):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "obsolete_branches": [{"branch_id": "B99"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "reuse archived id",
                "operations": [{"op": "add_branch", "branch": {"id": "B99"}}],
            }
        ),
        encoding="utf-8",
    )

    record, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=[],
        terminal_branch_ids=["B02"],
        terminal_branch_statuses={"B02": "pass"},
        infer_scheduler=False,
        run_lint=False,
    )
    assert record["status"] == "failed"
    assert any("duplicates obsolete branch id: B99" in defect for defect in record["defects"])


def test_validate_proposal_rejects_replace_branch_obsolete_id_reuse(tmp_path):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "obsolete_branches": [{"branch_id": "B99"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "replace archived id",
                "operations": [
                    {
                        "op": "replace_unstarted_branch",
                        "branch_id": "B01",
                        "branch": {"id": "B99", "operation": "replace"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    record, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=[],
        terminal_branch_ids=["B02"],
        terminal_branch_statuses={"B02": "pass"},
        infer_scheduler=False,
        run_lint=False,
    )
    assert record["status"] == "failed"
    assert any("duplicates obsolete branch id: B99" in defect for defect in record["defects"])


def test_validate_proposal_rejects_split_branch_obsolete_id_reuse(tmp_path):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "obsolete_branches": [{"branch_id": "B99"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "split to archived id",
                "operations": [
                    {"op": "split_unstarted_branch", "branch_id": "B01", "branches": [{"id": "B99"}, {"id": "B98"}]}
                ],
            }
        ),
        encoding="utf-8",
    )

    record, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=[],
        terminal_branch_ids=["B02"],
        terminal_branch_statuses={"B02": "pass"},
        infer_scheduler=False,
        run_lint=False,
    )
    assert record["status"] == "failed"
    assert any("duplicates obsolete branch id: B99" in defect for defect in record["defects"])


def test_protected_ids_preserves_explicit_no_infer_terminal_statuses(tmp_path):
    manifest = {
        "branches": [
            {
                "id": "B01",
                "status_path": "branches/B01.status.json",
            }
        ]
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text('{"status": "failed"}', encoding="utf-8")

    active, terminal, terminal_status = amendment_lib.protected_ids(
        manifest_path,
        manifest,
        terminal_ids=["B01"],
        terminal_statuses={"B01": "pass"},
        infer_scheduler=False,
    )

    assert active == set()
    assert terminal == {"B01"}
    assert terminal_status == {"B01": "pass"}


def test_protected_ids_no_infer_ignores_malformed_terminal_status_path(tmp_path):
    manifest = {
        "branches": [
            {
                "id": "B01",
                "status_path": "branches/B01.status.json",
            }
        ]
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("{ malformed", encoding="utf-8")

    active, terminal, terminal_status = amendment_lib.protected_ids(
        manifest_path,
        manifest,
        terminal_ids=["B01"],
        terminal_statuses={"B01": "pass"},
        infer_scheduler=False,
    )

    assert active == set()
    assert terminal == {"B01"}
    assert terminal_status == {"B01": "pass"}


# --- 2026-06-18 convergence pass 2: recommend's status-file loader skips a malformed status file
#     instead of crashing (the `except Exception: continue` could not skip a SystemExit) ---
def test_load_terminal_status_files_skips_malformed(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("{ not json", encoding="utf-8")
    branches = [{"id": "B01", "status_path": "branches/B01.status.json"}]
    assert rec.load_terminal_status_files(tmp_path / "job.manifest.json", branches) == {}  # must not raise


@pytest.mark.parametrize("status", [[], {}])
def test_load_terminal_status_files_skips_non_string_status(tmp_path, status):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    branches = [{"id": "B01", "status_path": "branches/B01.status.json"}]
    assert rec.load_terminal_status_files(tmp_path / "job.manifest.json", branches) == {}


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


# --- 2026-06-18 convergence pass 8: validate_proposal records a defect (no SystemExit escape) when
#     ensure_amendment_id->require_safe_id raises SystemExit on a malformed proposal amendment_id ---
def test_validate_proposal_absorbs_systemexit_on_bad_amendment_id(tmp_path):
    manifest = tmp_path / "job.manifest.json"
    manifest.write_text(json.dumps({"job_id": "j1"}), encoding="utf-8")
    proposal = tmp_path / "A1.proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "bad id!",  # fails require_safe_id -> SystemExit
                "job_id": "j1",
                "rationale": "x",
                "operations": [{"op": "add_branch"}],
            }
        ),
        encoding="utf-8",
    )
    record, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest,
        proposal_path=proposal,
        active_branch_ids=[],
        terminal_branch_ids=["B01"],
        infer_scheduler=False,
        run_lint=False,
    )
    assert isinstance(record, dict)  # must not raise SystemExit; bad id surfaced as a defect


def test_validate_proposal_preserves_explicit_terminal_statuses_on_fallback(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "job_id": "j1",
                "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
                "branches": [{"id": "B01"}],
            }
        ),
        encoding="utf-8",
    )
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "j1",
                "rationale": "x",
                "operations": [{"op": "add_branch", "branch": {"id": "B02"}}],
            }
        ),
        encoding="utf-8",
    )

    record, _candidate, _brief = amendment_lib.validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=[],
        terminal_branch_ids=["B01"],
        terminal_branch_statuses={"B01": "pass", "B99": "failed"},
        infer_scheduler=False,
        run_lint=False,
    )

    assert record["status"] == "failed"
    assert record["terminal_branch_statuses"] == {"B01": "pass"}
    assert any(
        "terminal_statuses must include exactly requested terminal branch ids" in defect for defect in record["defects"]
    )


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
    # Pass-12: a directory path (IsADirectoryError, an OSError) also fails closed
    with pytest.raises(SystemExit):
        amendment_lib.load_json_object(tmp_path)


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


# --- 2026-06-18 fresh-audit pass ---


# E1: validate_manifest_amendment.main() must emit the failed validation artifact (not crash with a
#     path-rule SystemExit and write nothing) when the proposal's amendment_id is malformed.
def test_validate_manifest_amendment_emits_failed_validation_on_bad_id(tmp_path, monkeypatch):
    vma = load_module("skills/goal-plan-amender/scripts/validate_manifest_amendment.py", "vma_review")
    manifest = tmp_path / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    proposal = tmp_path / "123.proposal.json"
    proposal.write_text("{}", encoding="utf-8")
    out = tmp_path / "out.json"
    failed = {
        "status": "failed",
        "amendment_id": "123",  # malformed (must match ^[A-Z][A-Z0-9_-]{1,31}$)
        "defects": ["amendment_id must match ^[A-Z][A-Z0-9_-]{1,31}$: '123'"],
        "proposal_sha256": "sha256:x",
    }
    monkeypatch.setattr(vma, "validate_proposal", lambda **kw: (failed, None, None))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_manifest_amendment.py",
            "--manifest",
            str(manifest),
            "--proposal",
            str(proposal),
            "--output",
            str(out),
        ],
    )
    assert vma.main() == 1  # must not raise SystemExit out of ensure_amendment_id
    assert out.exists(), "failed validation artifact must still be written"
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "failed"


def test_validate_manifest_amendment_rejects_absolute_id_without_filesystem_escape(tmp_path, monkeypatch):
    vma = load_module("skills/goal-plan-amender/scripts/validate_manifest_amendment.py", "vma_escape_review")
    manifest = tmp_path / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    proposal = tmp_path / "A1.proposal.json"
    proposal.write_text("{}", encoding="utf-8")
    escaped_stem = tmp_path.parent / "amender_escape_19B"
    escaped_validation = escaped_stem.with_name(f"{escaped_stem.name}.validation.json")
    escaped_lineage = escaped_stem.with_name(f"{escaped_stem.name}.lineage.json")
    escaped_validation.unlink(missing_ok=True)
    escaped_lineage.unlink(missing_ok=True)
    failed = {
        "status": "failed",
        "amendment_id": escaped_stem.as_posix(),
        "defects": ["amendment_id must be safe"],
        "proposal_sha256": "sha256:x",
    }
    monkeypatch.setattr(vma, "validate_proposal", lambda **kw: (failed, None, None))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_manifest_amendment.py",
            "--manifest",
            str(manifest),
            "--proposal",
            str(proposal),
        ],
    )

    assert vma.main() == 1
    assert not escaped_validation.exists()
    assert not escaped_lineage.exists()
    safe_validation = tmp_path / "amendments" / "INVALID_AMENDMENT_ID.validation.json"
    safe_lineage = tmp_path / "amendments" / "INVALID_AMENDMENT_ID.lineage.json"
    assert safe_validation.exists()
    assert safe_lineage.exists()
    assert json.loads(safe_validation.read_text(encoding="utf-8"))["status"] == "failed"


def test_validate_manifest_amendment_records_scheduler_inference_flag(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "branches": [],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal = tmp_path / "A1.proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "test",
                "operations": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.json"
    called = {}

    def fake_validate_proposal(**kwargs):
        called["infer_scheduler"] = kwargs["infer_scheduler"]
        return (
            {
                "status": "pass",
                "schema_version": 1,
                "amendment_id": "A1",
                "manifest": manifest_path.as_posix(),
                "proposal": proposal.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal),
                "active_branch_ids": [],
                "terminal_branch_ids": [],
                "terminal_statuses": {},
                "terminal_branch_statuses": {},
                "protected_branch_ids": [],
                "changed_branch_ids": [],
                "candidate_manifest_sha256": amendment_lib.canonical_sha256({"schema_version": 1}),
            },
            {"schema_version": 1},
            {},
        )

    monkeypatch.setattr(vma, "validate_proposal", fake_validate_proposal)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal),
            "--output",
            str(out),
            "--no-infer-scheduler",
        ],
    )
    assert vma.main() == 0
    assert called["infer_scheduler"] is False
    assert json.loads(out.read_text(encoding="utf-8"))["scheduler_inference_enabled"] is False


# --- 2026-06-20 pass15: create_amendment_decision omitted scheduler_inference_enabled, so
#     apply defaulted it to True and silently re-enabled inference during protected branch
#     reconstruction. This regression test now locks the writer and downstream consumer behavior.
def test_recommend_amendment_decision_records_no_infer_scheduler(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    decision_path = tmp_path / "amendments" / "A1.decision.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recommend_amendment_decision.py",
            "--manifest",
            str(manifest_path),
            "--amendment-id",
            "A1",
            "--terminal-branch",
            "B01",
            "--no-infer-scheduler",
            "--write-decision",
            "--replace",
        ],
    )
    assert rec.main() == 0
    assert decision_path.exists()
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["scheduler_inference_enabled"] is False


def test_recommend_amendment_decision_no_infer_ignores_unrequested_status_files(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B02.status.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [
            {"id": "B01"},
            {"id": "B02", "status_path": "branches/B02.status.json"},
        ],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    no_infer = rec.recommendation(
        manifest_path,
        manifest,
        active_ids=[],
        terminal_ids=["B01"],
        infer_scheduler=False,
    )
    assert no_infer["scheduler_inference_enabled"] is False
    assert no_infer["terminal_branch_ids"] == ["B01"]
    assert set(no_infer["terminal_branch_statuses"]) == {"B01"}

    inferred = rec.recommendation(
        manifest_path,
        manifest,
        active_ids=[],
        terminal_ids=["B01"],
        infer_scheduler=True,
    )
    assert inferred["terminal_branch_ids"] == ["B01", "B02"]
    assert inferred["terminal_branch_statuses"]["B02"] == "pass"


def test_recommend_amendment_decision_preserves_scheduler_terminal_status_over_stale_file(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    scheduler_dir = tmp_path / "schedulers"
    scheduler_dir.mkdir()
    (scheduler_dir / "main.scheduler.json").write_text(
        json.dumps(
            {
                "events": [
                    {"event": "launch", "id": "B01"},
                    {"event": "finish", "id": "B01", "status": "failed"},
                    {"event": "close", "id": "B01"},
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [
            {"id": "B01", "status_path": "branches/B01.status.json"},
            {"id": "B02", "depends_on": ["B01"]},
        ],
        "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _active, terminal, terminal_status = amendment_lib.protected_ids(
        manifest_path,
        manifest,
        active_ids=[],
        terminal_ids=[],
        infer_scheduler=True,
    )
    assert terminal == {"B01"}
    assert terminal_status == {"B01": "failed"}

    recommendation = rec.recommendation(
        manifest_path,
        manifest,
        active_ids=[],
        terminal_ids=[],
        infer_scheduler=True,
    )
    assert recommendation["decision"] == "launch"
    assert recommendation["reason_code"] == "blocker_stalls_downstream"
    assert recommendation["terminal_branch_statuses"]["B01"] == "failed"
    assert recommendation["stalled_branch_ids"] == ["B02"]


def test_validate_packet_rejects_missing_scheduler_inference_enabled(tmp_path):
    manifest_path, packet_dir, _proposal_path = _write_amender_packet_bundle(
        tmp_path, scheduler_inference_enabled=_BOOL_NOT_SET
    )
    result = vap.validate_packet(manifest_path=manifest_path, amendment_id="A1", packet_dir=packet_dir)
    assert result["status"] == "failed"
    assert any("scheduler_inference_enabled" in item for item in result["defects"])


def test_validate_packet_rejects_malformed_scheduler_inference_enabled(tmp_path):
    manifest_path, packet_dir, _proposal_path = _write_amender_packet_bundle(
        tmp_path, scheduler_inference_enabled="true"
    )
    result = vap.validate_packet(manifest_path=manifest_path, amendment_id="A1", packet_dir=packet_dir)
    assert result["status"] == "failed"
    assert any("scheduler_inference_enabled" in item for item in result["defects"])


def test_validate_packet_rejects_unsafe_prompt_artifact_traversal(tmp_path):
    manifest_path, packet_dir, _proposal_path = _write_amender_packet_bundle(
        tmp_path,
        scheduler_inference_enabled=True,
        prompt_artifact="../../outside.txt",
    )
    result = vap.validate_packet(manifest_path=manifest_path, amendment_id="A1", packet_dir=packet_dir)
    assert result["status"] == "failed"
    assert any("$.telemetry.prompt_artifact" in item for item in result["defects"])


def test_apply_branch_prompt_paths_skips_malformed_branch_id(tmp_path):
    candidate = {"branches": [{"id": [], "prompt": "branches/bad.prompt.md"}]}

    assert ama.branch_prompt_paths(tmp_path, candidate, {"B01"}) == []


def test_apply_manifest_amendment_acceptance_skips_malformed_regenerated_branch_id(tmp_path, monkeypatch):
    manifest_path = tmp_path / "job.manifest.json"
    proposal_path = tmp_path / "amendments" / "A1.proposal.json"
    validation_path = tmp_path / "amendments" / "A1.validation.json"
    proposal_path.parent.mkdir()
    original_manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "branches": [{"id": "B01", "prompt": "branches/B01.prompt.md"}],
    }
    manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
    proposal_path.write_text(json.dumps({"amendment_id": "A1"}), encoding="utf-8")
    validation_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "amendment_id": "A1",
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.prompt.md").write_text("old prompt", encoding="utf-8")
    candidate = {
        "schema_version": 1,
        "job_id": "J1",
        "branches": [
            {"id": {}, "prompt": "branches/bad.prompt.md"},
            {"id": "B01", "prompt": "branches/B01.prompt.md"},
        ],
    }
    candidate_sha = amendment_lib.canonical_sha256(candidate)
    monkeypatch.setattr(
        ama,
        "require_launch_packet_validation",
        lambda *_args, **_kwargs: {
            "active_branch_ids": [],
            "terminal_branch_ids": [],
            "terminal_branch_statuses": {},
            "scheduler_inference_enabled": False,
        },
    )
    monkeypatch.setattr(
        ama,
        "validate_proposal",
        lambda **_kwargs: (
            {
                "status": "pass",
                "protected_branch_ids": [],
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "candidate_manifest_sha256": candidate_sha,
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "changed_branch_ids": ["B01"],
            },
            candidate,
            {"branches": []},
        ),
    )
    monkeypatch.setattr(ama, "load_lineage", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ama, "latest_lineage_sha", lambda *_args, **_kwargs: "sha256:" + "0" * 64)
    monkeypatch.setattr(ama, "add_lineage_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ama, "prompt_regeneration_branch_ids", lambda *_args, **_kwargs: ["B01"])
    monkeypatch.setattr(ama, "write_runtime_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ama, "enrich_brief_runtime_metadata", lambda brief, *_args, **_kwargs: brief)
    monkeypatch.setattr(ama.PREFLIGHT, "write_bundle_prompts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ama.PREFLIGHT, "lint_bundle", lambda *_args, **_kwargs: {"status": "pass"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--validation",
            str(validation_path),
        ],
    )

    assert ama.main() == 0
    accepted_path = tmp_path / "amendments" / "A1.accepted.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    assert accepted["regenerated_prompts"] == ["branches/B01.prompt.md"]


def test_apply_manifest_amendment_preserves_terminal_status_prompts_and_refreshes_future_prompts(tmp_path, monkeypatch):
    manifest_path = tmp_path / "job.manifest.json"
    proposal_path = tmp_path / "amendments" / "A1.proposal.json"
    validation_path = tmp_path / "amendments" / "A1.validation.json"
    proposal_path.parent.mkdir()
    original_manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "branches": [
            {"id": "B01", "prompt": "branches/B01.prompt.md", "status_path": "branches/B01.status.json"},
            {"id": "B02", "prompt": "branches/B02.prompt.md", "status_path": "branches/B02.status.json"},
            {"id": "B03", "prompt": "branches/B03.prompt.md", "status_path": "branches/B03.status.json"},
        ],
    }
    manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8")
    proposal_path.write_text(json.dumps({"amendment_id": "A1"}), encoding="utf-8")
    validation_path.write_text(
        json.dumps(
            {
                "status": "pass",
                "amendment_id": "A1",
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
            }
        ),
        encoding="utf-8",
    )
    branches_dir = tmp_path / "branches"
    branches_dir.mkdir()
    (branches_dir / "B01.prompt.md").write_text("terminal B01 prompt", encoding="utf-8")
    (branches_dir / "B02.prompt.md").write_text("terminal B02 prompt", encoding="utf-8")
    (branches_dir / "B03.prompt.md").write_text("future B03 prompt", encoding="utf-8")
    (branches_dir / "B01.status.json").write_text(
        json.dumps({"branch_id": "B01", "status": "pass"}),
        encoding="utf-8",
    )
    (branches_dir / "B02.status.json").write_text(
        json.dumps({"branch_id": "B02", "status": "pass"}),
        encoding="utf-8",
    )
    candidate = {
        "schema_version": 1,
        "job_id": "J1",
        "branches": [
            {"id": "B01", "prompt": "branches/B01.prompt.md", "status_path": "branches/B01.status.json"},
            {"id": "B02", "prompt": "branches/B02.prompt.md", "status_path": "branches/B02.status.json"},
            {"id": "B03", "prompt": "branches/B03.prompt.md", "status_path": "branches/B03.status.json"},
            {"id": "B04", "prompt": "branches/B04.prompt.md", "status_path": "branches/B04.status.json"},
        ],
    }
    candidate_sha = amendment_lib.canonical_sha256(candidate)

    monkeypatch.setattr(
        ama,
        "require_launch_packet_validation",
        lambda *_args, **_kwargs: {
            "active_branch_ids": [],
            "terminal_branch_ids": [],
            "terminal_branch_statuses": {},
            "scheduler_inference_enabled": False,
        },
    )
    monkeypatch.setattr(
        ama,
        "validate_proposal",
        lambda **_kwargs: (
            {
                "status": "pass",
                "protected_branch_ids": [],
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "candidate_manifest_sha256": candidate_sha,
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "changed_branch_ids": ["B04"],
            },
            candidate,
            {"branches": []},
        ),
    )
    monkeypatch.setattr(ama, "load_lineage", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(ama, "latest_lineage_sha", lambda *_args, **_kwargs: "sha256:" + "0" * 64)
    monkeypatch.setattr(ama, "add_lineage_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ama, "prompt_regeneration_branch_ids", lambda *_args, **_kwargs: ["B01", "B02", "B03", "B04"])
    monkeypatch.setattr(ama, "write_runtime_index", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ama, "enrich_brief_runtime_metadata", lambda brief, *_args, **_kwargs: brief)

    def fake_write_bundle_prompts(_brief, bundle_dir, *, branch_ids, write_main):
        assert write_main is False
        for branch_id in branch_ids:
            (Path(bundle_dir) / "branches" / f"{branch_id}.prompt.md").write_text(
                f"regenerated {branch_id}",
                encoding="utf-8",
            )

    monkeypatch.setattr(ama.PREFLIGHT, "write_bundle_prompts", fake_write_bundle_prompts)
    monkeypatch.setattr(ama.PREFLIGHT, "lint_bundle", lambda *_args, **_kwargs: {"status": "pass"})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--validation",
            str(validation_path),
        ],
    )

    assert ama.main() == 0
    accepted = json.loads((tmp_path / "amendments" / "A1.accepted.json").read_text(encoding="utf-8"))
    assert accepted["changed_branch_ids"] == ["B04"]
    assert accepted["regenerated_prompts"] == ["branches/B03.prompt.md", "branches/B04.prompt.md"]
    assert (branches_dir / "B01.prompt.md").read_text(encoding="utf-8") == "terminal B01 prompt"
    assert (branches_dir / "B02.prompt.md").read_text(encoding="utf-8") == "terminal B02 prompt"
    assert (branches_dir / "B03.prompt.md").read_text(encoding="utf-8") == "regenerated B03"
    assert (branches_dir / "B04.prompt.md").read_text(encoding="utf-8") == "regenerated B04"


def test_validate_packet_rejects_absolute_prompt_artifact(tmp_path):
    manifest_path, packet_dir, _proposal_path = _write_amender_packet_bundle(
        tmp_path,
        scheduler_inference_enabled=True,
        prompt_artifact="/tmp/outside.txt",
    )
    result = vap.validate_packet(manifest_path=manifest_path, amendment_id="A1", packet_dir=packet_dir)
    assert result["status"] == "failed"
    assert any("$.telemetry.prompt_artifact" in item for item in result["defects"])


@pytest.mark.parametrize("prompt_artifact", ["prompt.md", "input-files.json"])
def test_validate_packet_accepts_emitted_safe_prompt_artifacts(tmp_path, prompt_artifact):
    manifest_path, packet_dir, _proposal_path = _write_amender_packet_bundle(
        tmp_path,
        scheduler_inference_enabled=True,
        prompt_artifact=prompt_artifact,
    )
    result = vap.validate_packet(manifest_path=manifest_path, amendment_id="A1", packet_dir=packet_dir)
    assert result["status"] == "pass"


@pytest.mark.parametrize("flag", [False, True])
def test_create_adaptation_packet_reconciles_no_infer_scheduler_from_decision(tmp_path, monkeypatch, flag):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    bundle_dir = tmp_path
    manifest_path = bundle_dir / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    amendment_id = "A1"
    amendments_dir = bundle_dir / "amendments"
    amendments_dir.mkdir()
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": amendment_id,
                "job_id": "J1",
                "decision": "launch",
                "reason_code": "no_eligible_branch",
                "reason": "test",
                "manifest": manifest_path.as_posix(),
                "manifest_sha256": amendment_lib.sha256_file(manifest_path),
                "terminal_branch_ids": ["B01"],
                "active_branch_ids": [],
                "terminal_branch_statuses": {"B01": "failed"},
                "scheduler_inference_enabled": flag,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    called: dict[str, bool] = {}

    def fake_protected_ids(
        _manifest_path: Path,
        _manifest: dict,
        active_ids: list[str],
        terminal_ids: list[str],
        terminal_statuses: dict[str, str] | None = None,
        infer_scheduler: bool = True,
        run_lint: bool = True,
    ):
        called["infer_scheduler"] = infer_scheduler
        return set(), {"B01"}, {"B01": "failed"}

    monkeypatch.setattr(cap, "protected_ids", fake_protected_ids)
    args = SimpleNamespace(active_branch=[], terminal_branch=[])
    inputs = cap.ResolvedInputs(
        manifest_path=manifest_path,
        main_prompt=(bundle_dir / "prompt.md").as_posix(),
        repo_root=(bundle_dir / "repo").as_posix(),
        amendment_id=amendment_id,
        manifest=manifest,
        policy={},
        selected_ladder=[],
        selection_reason="default",
    )

    active, terminal, terminal_status = cap._reconcile_protected_ids(
        args, inputs, json.loads(decision_path.read_text())
    )
    assert called["infer_scheduler"] is flag
    assert active == set()
    assert terminal == {"B01"}
    assert terminal_status == {"B01": "failed"}


@pytest.mark.parametrize(
    "scheduler_inference_enabled",
    ["malformed", 1, _BOOL_NOT_SET],
    ids=["string", "int", "missing"],
)
def test_create_adaptation_packet_rejects_invalid_scheduler_inference_enabled(
    tmp_path, monkeypatch, scheduler_inference_enabled
):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    amendments_dir = tmp_path / "amendments"
    amendments_dir.mkdir()
    decision_path = amendments_dir / "A1.decision.json"
    decision = {
        "schema_version": 1,
        "amendment_id": "A1",
        "job_id": "J1",
        "decision": "launch",
        "reason_code": "no_eligible_branch",
        "reason": "test",
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": amendment_lib.sha256_file(manifest_path),
        "terminal_branch_ids": ["B01"],
        "active_branch_ids": [],
        "terminal_branch_statuses": {"B01": "failed"},
    }
    if scheduler_inference_enabled is not _BOOL_NOT_SET:
        decision["scheduler_inference_enabled"] = scheduler_inference_enabled
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    args = SimpleNamespace(active_branch=[], terminal_branch=[])
    inputs = cap.ResolvedInputs(
        manifest_path=manifest_path,
        main_prompt=(tmp_path / "prompt.md").as_posix(),
        repo_root=(tmp_path / "repo").as_posix(),
        amendment_id="A1",
        manifest=manifest,
        policy={},
        selected_ladder=[],
        selection_reason="default",
    )

    with pytest.raises(SystemExit):
        cap._reconcile_protected_ids(args, inputs, decision)


def test_create_adaptation_packet_replace_preserves_existing_packet_on_invalid_decision(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    main_prompt = tmp_path / "main_prompt.md"
    main_prompt.write_text("main", encoding="utf-8")

    amendment_id = "A1"
    amendments_dir = tmp_path / "amendments"
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    packet_dir.mkdir(parents=True)
    sentinel = packet_dir / "keep.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": amendment_id,
                "job_id": "J1",
                "decision": "launch",
                "reason_code": "no_eligible_branch",
                "reason": "test",
                "manifest": manifest_path.as_posix(),
                "manifest_sha256": amendment_lib.sha256_file(manifest_path),
                "terminal_branch_ids": ["B01"],
                "active_branch_ids": [],
                "terminal_branch_statuses": {"B01": "failed"},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_adaptation_packet.py",
            "--manifest",
            manifest_path.as_posix(),
            "--main-prompt",
            main_prompt.as_posix(),
            "--repo-root",
            tmp_path.as_posix(),
            "--amendment-id",
            amendment_id,
            "--replace",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        cap.main()
    assert str(excinfo.value) == "scheduler_inference_enabled must be a boolean"
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_create_adaptation_packet_replace_recreates_existing_packet_after_valid_decision(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    main_prompt = tmp_path / "main_prompt.md"
    main_prompt.write_text("main", encoding="utf-8")

    amendment_id = "A1"
    amendments_dir = tmp_path / "amendments"
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    packet_dir.mkdir(parents=True)
    sentinel = packet_dir / "keep.txt"
    sentinel.write_text("replace me", encoding="utf-8")
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": amendment_id,
                "job_id": "J1",
                "decision": "launch",
                "reason_code": "no_eligible_branch",
                "reason": "test",
                "manifest": manifest_path.as_posix(),
                "manifest_sha256": amendment_lib.sha256_file(manifest_path),
                "terminal_branch_ids": ["B01"],
                "active_branch_ids": [],
                "terminal_branch_statuses": {"B01": "failed"},
                "scheduler_inference_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_adaptation_packet.py",
            "--manifest",
            manifest_path.as_posix(),
            "--main-prompt",
            main_prompt.as_posix(),
            "--repo-root",
            tmp_path.as_posix(),
            "--amendment-id",
            amendment_id,
            "--replace",
        ],
    )

    assert cap.main() == 0
    assert not sentinel.exists()
    assert (packet_dir / "input-files.json").exists()


@pytest.mark.parametrize("flag", [False, True])
def test_create_blocker_repair_packet_reconciles_no_infer_scheduler_from_decision(tmp_path, monkeypatch, flag):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    bundle_dir = tmp_path
    manifest_path = bundle_dir / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    amendment_id = "A1"
    amendments_dir = bundle_dir / "amendments"
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    packet_dir.mkdir(parents=True)
    sentinel = packet_dir / "keep.txt"
    sentinel.write_text("replace me", encoding="utf-8")
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": "J1",
        "decision": "launch",
        "reason_code": "no_eligible_branch",
        "reason": "test",
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": amendment_lib.sha256_file(manifest_path),
        "terminal_branch_ids": ["B01"],
        "active_branch_ids": [],
        "terminal_branch_statuses": {"B01": "failed"},
        "scheduler_inference_enabled": flag,
    }
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    (bundle_dir / "main_prompt.md").write_text("main", encoding="utf-8")
    (bundle_dir / "audit").mkdir()
    (bundle_dir / "audit" / "prompt-audit.json").write_text("{}", encoding="utf-8")

    called: dict[str, bool] = {}

    def fake_protected_ids(
        _manifest_path: Path,
        _manifest: dict,
        active_ids: list[str],
        terminal_ids: list[str],
        terminal_statuses: dict[str, str] | None = None,
        infer_scheduler: bool = True,
        run_lint: bool = True,
    ):
        called["infer_scheduler"] = infer_scheduler
        return set(), {"B01"}, {"B01": "failed"}

    def fake_generate_proposal(packet: dict):
        return {"operations": [{"op": "no-op", "branch": {"id": "B99"}}]}

    monkeypatch.setattr(cbr, "protected_ids", fake_protected_ids)
    monkeypatch.setattr(cbr, "generate_proposal", fake_generate_proposal)
    monkeypatch.setattr(cbr, "validate_amender_model_policy", lambda *_a, **_k: None)

    args = SimpleNamespace(
        manifest=manifest_path.as_posix(),
        main_prompt=(bundle_dir / "main_prompt.md").as_posix(),
        repo_root=bundle_dir.as_posix(),
        amendment_id=amendment_id,
        prompt_audit=(bundle_dir / "audit" / "prompt-audit.json").as_posix(),
        active_branch=[],
        terminal_branch=[],
        replace=True,
    )

    path = cbr.create_packet(args)
    assert path == amendments_dir / f"{amendment_id}.packet"
    assert called["infer_scheduler"] is flag
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "scheduler_inference_enabled",
    ["malformed", 1, _BOOL_NOT_SET],
    ids=["string", "int", "missing"],
)
def test_create_blocker_repair_packet_rejects_invalid_scheduler_inference_enabled(
    tmp_path, monkeypatch, scheduler_inference_enabled
):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    bundle_dir = tmp_path
    manifest_path = bundle_dir / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    amendment_id = "A1"
    amendments_dir = bundle_dir / "amendments"
    amendments_dir.mkdir()
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": "J1",
        "decision": "launch",
        "reason_code": "no_eligible_branch",
        "reason": "test",
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": amendment_lib.sha256_file(manifest_path),
        "terminal_branch_ids": ["B01"],
        "active_branch_ids": [],
        "terminal_branch_statuses": {"B01": "failed"},
    }
    if scheduler_inference_enabled is not _BOOL_NOT_SET:
        decision["scheduler_inference_enabled"] = scheduler_inference_enabled
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    (bundle_dir / "main_prompt.md").write_text("main", encoding="utf-8")
    (bundle_dir / "audit").mkdir()
    (bundle_dir / "audit" / "prompt-audit.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cbr,
        "protected_ids",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should fail before protected_ids")),
    )

    args = SimpleNamespace(
        manifest=manifest_path.as_posix(),
        main_prompt=(bundle_dir / "main_prompt.md").as_posix(),
        repo_root=bundle_dir.as_posix(),
        amendment_id=amendment_id,
        prompt_audit=(bundle_dir / "audit" / "prompt-audit.json").as_posix(),
        active_branch=[],
        terminal_branch=[],
        replace=True,
    )

    with pytest.raises(SystemExit):
        cbr.create_packet(args)


def test_create_blocker_repair_packet_replace_preserves_existing_packet_on_invalid_decision(tmp_path):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    bundle_dir = tmp_path
    manifest_path = bundle_dir / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    main_prompt = bundle_dir / "main_prompt.md"
    main_prompt.write_text("main", encoding="utf-8")
    audit_dir = bundle_dir / "audit"
    audit_dir.mkdir()
    prompt_audit = audit_dir / "prompt-audit.json"
    prompt_audit.write_text("{}", encoding="utf-8")

    amendment_id = "A1"
    amendments_dir = bundle_dir / "amendments"
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    packet_dir.mkdir(parents=True)
    sentinel = packet_dir / "keep.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    decision_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": amendment_id,
                "job_id": "J1",
                "decision": "launch",
                "reason_code": "no_eligible_branch",
                "reason": "test",
                "manifest": manifest_path.as_posix(),
                "manifest_sha256": amendment_lib.sha256_file(manifest_path),
                "terminal_branch_ids": ["B01"],
                "active_branch_ids": [],
                "terminal_branch_statuses": {"B01": "failed"},
            }
        ),
        encoding="utf-8",
    )

    args = SimpleNamespace(
        manifest=manifest_path.as_posix(),
        main_prompt=main_prompt.as_posix(),
        repo_root=bundle_dir.as_posix(),
        amendment_id=amendment_id,
        prompt_audit=prompt_audit.as_posix(),
        active_branch=[],
        terminal_branch=[],
        replace=True,
    )

    with pytest.raises(SystemExit) as excinfo:
        cbr.create_packet(args)
    assert str(excinfo.value) == "scheduler_inference_enabled must be a boolean"
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_create_amendment_decision_roundtrip_to_apply_preserves_no_infer_scheduler(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "test",
                "operations": [{"op": "add_branch", "branch": {"id": "B02"}}],
            }
        ),
        encoding="utf-8",
    )

    # Keep the validation artifact as the source-of-truth input for this path.
    v_validation = tmp_path / "v.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--terminal-branch",
            "B01",
            "--no-infer-scheduler",
            "--output",
            str(v_validation),
        ],
    )
    vma.main()
    validation = json.loads(v_validation.read_text(encoding="utf-8"))
    assert validation["scheduler_inference_enabled"] is False

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_amendment_decision.py",
            "--manifest",
            str(manifest_path),
            "--amendment-id",
            "A1",
            "--decision",
            "launch",
            "--reason-code",
            "no_eligible_branch",
            "--reason",
            "no eligible branch",
            "--terminal-branch",
            "B01",
            "--no-infer-scheduler",
            "--replace",
        ],
    )
    assert cam.main() == 0
    decision = json.loads((tmp_path / "amendments" / "A1.decision.json").read_text(encoding="utf-8"))
    assert decision["scheduler_inference_enabled"] is False

    validation.update(
        {
            "status": "pass",
            "candidate_manifest_sha256": amendment_lib.canonical_sha256(manifest),
            "active_branch_ids": [],
            "terminal_branch_ids": ["B01"],
            "terminal_statuses": {"B01": "blocked"},
            "terminal_branch_statuses": {"B01": "blocked"},
            "protected_branch_ids": ["B01"],
            "changed_branch_ids": ["B02"],
            "proposal": proposal_path.as_posix(),
            "manifest": manifest_path.as_posix(),
            "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
            "proposal_sha256": amendment_lib.sha256_file(proposal_path),
            "scheduler_inference_enabled": False,
        }
    )
    called: dict = {}

    def fake_validate_proposal(
        manifest_path,
        proposal_path,
        active_branch_ids=None,
        terminal_branch_ids=None,
        terminal_branch_statuses=None,
        infer_scheduler=True,
        run_lint=True,
    ):
        called["infer_scheduler"] = infer_scheduler
        if not infer_scheduler:
            return (
                {
                    "status": "pass",
                    "schema_version": 1,
                    "manifest": manifest_path.as_posix(),
                    "proposal": proposal_path.as_posix(),
                    "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                    "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                    "candidate_manifest_sha256": amendment_lib.canonical_sha256(manifest),
                    "active_branch_ids": active_branch_ids or [],
                    "terminal_branch_ids": terminal_branch_ids or [],
                    "terminal_statuses": terminal_branch_statuses or {},
                    "terminal_branch_statuses": terminal_branch_statuses or {},
                    "protected_branch_ids": ["B01"],
                    "changed_branch_ids": ["B02"],
                },
                copy.deepcopy(manifest),
                {},
            )
        return ({"status": "failed", "schema_version": 1, "defects": ["should be False"]}, {}, None)

    monkeypatch.setattr(ama, "require_launch_packet_validation", lambda *_a, **_k: decision)
    monkeypatch.setattr(ama, "validate_proposal", fake_validate_proposal)
    monkeypatch.setattr(ama, "write_runtime_index", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "write_bundle_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "lint_bundle", lambda *_a, **_k: {"status": "pass"})
    monkeypatch.setattr(ama, "mark_preflight_report_initial_epoch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--validation",
            str(v_validation),
        ],
    )
    v_validation.write_text(json.dumps(validation), encoding="utf-8")
    assert ama.main() == 0
    assert called["infer_scheduler"] is False


def test_apply_manifest_amendment_rejects_hand_edited_validation_protection_inputs(tmp_path, monkeypatch):
    # Pre-fix: hand-editing the validation artifact could clear protected IDs and
    # disable scheduler inference, letting apply return 0.
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "amender_model_policy": copy.deepcopy(amendment_lib.CONTRACT.AMENDER_MODEL_POLICY),
        "branches": [{"id": "B01"}, {"id": "B02"}],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "replace active branch",
                "operations": [
                    {
                        "op": "replace_unstarted_branch",
                        "branch_id": "B01",
                        "branch": {"id": "B03"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    candidate = copy.deepcopy(manifest)
    candidate_sha = amendment_lib.canonical_sha256(candidate)
    validation_path = tmp_path / "A1.validation.json"
    validation_path.write_text(
        amendment_lib.json_text(
            {
                "schema_version": 1,
                "status": "pass",
                "amendment_id": "A1",
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "candidate_manifest_sha256": candidate_sha,
                # Hand edit from an attacker: clear active ids and disable inference.
                "active_branch_ids": [],
                "terminal_branch_ids": ["B02"],
                "terminal_statuses": {"B02": "pass"},
                "terminal_branch_statuses": {"B02": "pass"},
                "scheduler_inference_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    called = {}

    def fake_validate_proposal(
        manifest_path,
        proposal_path,
        active_branch_ids=None,
        terminal_branch_ids=None,
        terminal_branch_statuses=None,
        infer_scheduler=True,
        run_lint=True,
    ):
        called["active_branch_ids"] = list(active_branch_ids or [])
        called["terminal_branch_ids"] = list(terminal_branch_ids or [])
        called["infer_scheduler"] = infer_scheduler
        # The decision-bound path protects B01. If we use hand-edited validation values,
        # this call comes in with no active IDs and no inference and would pass.
        if infer_scheduler and "B01" in called["active_branch_ids"]:
            return (
                {
                    "status": "failed",
                    "schema_version": 1,
                    "defects": ["expected protected-id failure"],
                    "manifest": manifest_path.as_posix(),
                    "proposal": proposal_path.as_posix(),
                    "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                    "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                    "candidate_manifest_sha256": candidate_sha,
                },
                {},
                None,
            )
        if not infer_scheduler:
            return (
                {
                    "status": "pass",
                    "schema_version": 1,
                    "manifest": manifest_path.as_posix(),
                    "proposal": proposal_path.as_posix(),
                    "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                    "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                    "terminal_branch_ids": ["B02"],
                    "terminal_branch_statuses": {"B02": "pass"},
                    "active_branch_ids": [],
                    "candidate_manifest_sha256": candidate_sha,
                },
                candidate,
                {},
            )
        return (
            {
                "status": "failed",
                "schema_version": 1,
                "defects": ["apply not bound to decision protection"],
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "candidate_manifest_sha256": candidate_sha,
            },
            {},
            None,
        )

    monkeypatch.setattr(
        ama,
        "require_launch_packet_validation",
        lambda *_a, **_k: {
            "active_branch_ids": ["B01"],
            "terminal_branch_ids": ["B02"],
            "terminal_branch_statuses": {"B02": "pass"},
            "scheduler_inference_enabled": True,
        },
    )
    monkeypatch.setattr(ama, "validate_proposal", fake_validate_proposal)
    monkeypatch.setattr(ama, "write_runtime_index", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "write_bundle_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "lint_bundle", lambda *_a, **_k: {"status": "pass"})
    monkeypatch.setattr(ama, "mark_preflight_report_initial_epoch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--validation",
            str(validation_path),
        ],
    )
    with pytest.raises(SystemExit):
        ama.main()
    assert called["infer_scheduler"] is True
    assert called["active_branch_ids"] == ["B01"]
    assert called["terminal_branch_ids"] == ["B02"]


def test_apply_manifest_amendment_preserves_no_infer_scheduler_from_packet_decision(tmp_path, monkeypatch):
    manifest = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "branches": [],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proposal_path = tmp_path / "A1.proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "amendment_id": "A1",
                "job_id": "J1",
                "rationale": "test",
                "operations": [],
            }
        ),
        encoding="utf-8",
    )
    candidate = {
        "schema_version": 1,
        "job_id": "J1",
        "adaptation_policy": amendment_lib.CONTRACT.ADAPTATION_POLICY,
        "branches": [],
    }
    candidate_sha = amendment_lib.canonical_sha256(candidate)
    validation_path = tmp_path / "A1.validation.json"
    validation_path.write_text(
        amendment_lib.json_text(
            {
                "schema_version": 1,
                "status": "pass",
                "amendment_id": "A1",
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "candidate_manifest_sha256": candidate_sha,
                "active_branch_ids": [],
                "terminal_branch_ids": [],
                "terminal_statuses": {},
                "terminal_branch_statuses": {},
                "protected_branch_ids": [],
                "changed_branch_ids": [],
                "scheduler_inference_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    called = {}

    def fake_validate_proposal(
        manifest_path,
        proposal_path,
        active_branch_ids=None,
        terminal_branch_ids=None,
        terminal_branch_statuses=None,
        infer_scheduler=True,
        run_lint=True,
    ):
        called["infer_scheduler"] = infer_scheduler
        if infer_scheduler:
            return (
                {
                    "status": "failed",
                    "schema_version": 1,
                    "defects": ["did not preserve scheduler inference"],
                    "manifest": manifest_path.as_posix(),
                    "proposal": proposal_path.as_posix(),
                    "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                    "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                    "candidate_manifest_sha256": candidate_sha,
                },
                {},
                None,
            )
        return (
            {
                "status": "pass",
                "schema_version": 1,
                "manifest": manifest_path.as_posix(),
                "proposal": proposal_path.as_posix(),
                "manifest_sha256_before": amendment_lib.sha256_file(manifest_path),
                "proposal_sha256": amendment_lib.sha256_file(proposal_path),
                "active_branch_ids": [],
                "terminal_branch_ids": [],
                "terminal_statuses": {},
                "terminal_branch_statuses": {},
                "protected_branch_ids": [],
                "changed_branch_ids": [],
                "candidate_manifest_sha256": candidate_sha,
            },
            candidate,
            {},
        )

    monkeypatch.setattr(
        ama,
        "require_launch_packet_validation",
        lambda *_a, **_k: {"scheduler_inference_enabled": False},
    )
    monkeypatch.setattr(ama, "validate_proposal", fake_validate_proposal)
    monkeypatch.setattr(ama, "write_runtime_index", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "write_bundle_prompts", lambda *_a, **_k: None)
    monkeypatch.setattr(ama.PREFLIGHT, "lint_bundle", lambda *_a, **_k: {"status": "pass"})
    monkeypatch.setattr(ama, "mark_preflight_report_initial_epoch", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_manifest_amendment.py",
            "--manifest",
            str(manifest_path),
            "--proposal",
            str(proposal_path),
            "--validation",
            str(validation_path),
        ],
    )
    assert ama.main() == 0
    assert called["infer_scheduler"] is False


# E3: amender_model_policy returns a deepcopy of the shared default, so a caller mutating the result
#     cannot corrupt CONTRACT.AMENDER_MODEL_POLICY process-wide.
def test_amender_model_policy_returns_deepcopy():
    first = amendment_lib.amender_model_policy(None)
    first["__mutated__"] = True
    second = amendment_lib.amender_model_policy(None)
    assert "__mutated__" not in second
    assert "__mutated__" not in amendment_lib.CONTRACT.AMENDER_MODEL_POLICY


# E4: the split duplicate-id guard tracks whether THIS loop appended a defect (count delta), instead
#     of the fragile ctx.defects[-1].startswith(path) check.
def test_split_rejects_replacement_id_duplicating_existing_branch():
    branches = [{"id": "B01"}, {"id": "B02"}, {"id": "B03"}]
    ctx = amendment_lib.OperationContext(branches=branches, obsolete_entries=[], changed_branch_ids=set(), defects=[])
    operation = {"branches": [{"id": "B01"}, {"id": "B05"}]}  # B01 duplicates the existing branch at index 0
    amendment_lib._apply_split_unstarted_branch(ctx, operation, "$.operations[0]", branch_id="B02", target_index=1)
    assert any("duplicates existing branch" in d for d in ctx.defects)
    assert [b["id"] for b in ctx.branches] == ["B01", "B02", "B03"]  # split not applied


def test_split_valid_applies_and_rewrites_dependents():
    branches = [{"id": "B01"}, {"id": "B02", "depends_on": []}, {"id": "B03", "depends_on": ["B02"]}]
    ctx = amendment_lib.OperationContext(branches=branches, obsolete_entries=[], changed_branch_ids=set(), defects=[])
    operation = {"branches": [{"id": "B04"}, {"id": "B05"}]}
    amendment_lib._apply_split_unstarted_branch(ctx, operation, "$.operations[0]", branch_id="B02", target_index=1)
    assert not ctx.defects
    assert [b["id"] for b in ctx.branches] == ["B01", "B04", "B05", "B03"]
    assert ctx.branches[-1]["depends_on"] == ["B04", "B05"]  # dependent rewired to the replacements


# --- 2026-06-18 RE-AUDIT pass: validate_manifest_amendment.main() must also catch the ValueError that
#     ensure_amendment_id raises for an EMPTY id (the first fix only caught SystemExit, which covers a
#     malformed non-empty id). A proposal named ".proposal.json" yields proposed id "" -> ValueError. ---
def test_validate_manifest_amendment_handles_empty_amendment_id(tmp_path, monkeypatch):
    vma = load_module("skills/goal-plan-amender/scripts/validate_manifest_amendment.py", "vma_empty_review")
    manifest = tmp_path / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    proposal = tmp_path / ".proposal.json"  # leading dot -> name.split(".")[0] == ""
    proposal.write_text("{}", encoding="utf-8")
    out = tmp_path / "out.json"
    failed = {
        "status": "failed",
        "amendment_id": None,
        "defects": ["amendment_id required"],
        "proposal_sha256": "sha256:x",
    }
    monkeypatch.setattr(vma, "validate_proposal", lambda **kw: (failed, None, None))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_manifest_amendment.py",
            "--manifest",
            str(manifest),
            "--proposal",
            str(proposal),
            "--output",
            str(out),
        ],
    )
    assert vma.main() == 1  # ValueError from ensure_amendment_id("") must be caught, not crash
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "failed"


def test_recommendation_launches_when_failed_terminal_is_unrecovered(tmp_path):
    manifest = {
        "job_id": "J1",
        "schema_version": 1,
        "branches": [
            {"id": "B01", "status_path": "B01.status.json"},
            {"id": "B02", "depends_on": ["B01"]},
        ],
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "B01.status.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")

    decision = rec.recommendation(manifest_path, manifest, active_ids=[], terminal_ids=["B01"])
    assert decision["decision"] == "launch"
    assert decision["reason_code"] == "blocker_stalls_downstream"
    assert "B02" in decision["stalled_branch_ids"]
