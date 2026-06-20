"""Regression tests for the 2026-06-17 deep-review fixes (goal-main-orchestrator).

Each test pins a specific defect found during the repo-audit + agent review so the
fix cannot silently regress. Fixes covered:

- C1  render_branch_worktree: literal ``{branch_id}`` leaked into the CLI launch prompt.
- B1  validate_main_status: core gate crashed on malformed JSON instead of failing closed.
- B2  assemble_main_status: crashed on malformed/non-object runtime artifacts.
- A2  validate_prompt_audit: required ``actionability_verdict`` was never enforced.
- A4  deterministic_prompt_audit: crashed iterating a non-list ``branches``.
- D1  summarize_telemetry: crashed on an unreadable ``debug.events.jsonl``.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from conftest import REPO, load_module

rgb = load_module("skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py", "rgb_mo")
ams = load_module("skills/goal-main-orchestrator/scripts/assemble_main_status.py", "ams_mo")
vpa = load_module("skills/goal-main-orchestrator/scripts/validate_prompt_audit.py", "vpa_mo")
dpa = load_module("skills/goal-main-orchestrator/scripts/deterministic_prompt_audit.py", "dpa_mo")
stl = load_module("skills/goal-main-orchestrator/scripts/summarize_telemetry.py", "stl_mo")
cap = load_module("skills/goal-main-orchestrator/scripts/create_audit_packet.py", "cap_mo")
vms = load_module("skills/goal-main-orchestrator/scripts/validate_main_status.py", "vms_mo")
rpar = load_module("skills/goal-main-orchestrator/scripts/runtime_prompt_audit_runner.py", "rpar_mo")


# --- 2026-06-18 convergence pass 13: validate_decision_artifact tolerates an unhashable element in
#     a decision artifact's terminal_branch_ids (was TypeError on set(...)) ---
def test_validate_decision_artifact_tolerates_unhashable_terminal_ids(tmp_path):
    defects: list[str] = []
    data = {
        "schema_version": 1,
        "amendment_id": "A1",
        "decision": "skip",
        "active_branch_ids": [],
        "terminal_branch_ids": [["nested-list"]],  # unhashable element
        "terminal_branch_statuses": {},
    }
    vms.validate_decision_artifact(
        defects, data, "$.d", amendment_id="A1", manifest_path=tmp_path / "job.manifest.json"
    )
    assert isinstance(defects, list)  # must not raise TypeError on set(terminal_branch_ids)


# --- 2026-06-18 convergence pass 14: membership tests guard a non-string (unhashable) scalar field
#     (a tampered list-valued `status` no longer raises TypeError on `not in {set}`) ---
def test_validate_branch_summary_tolerates_unhashable_status():
    defects: list[str] = []
    vms.validate_branch_summary(defects, {"status": ["pass"], "branch_id": "B01"}, "$.b")  # must not raise
    assert any("status" in d for d in defects), defects


# --- 2026-06-18 re-review residual: create_audit_packet.load_manifest fails closed ---
def test_create_audit_packet_load_manifest_fails_closed(tmp_path):
    bad = tmp_path / "job.manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cap.load_manifest(bad)
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        cap.load_manifest(arr)  # non-dict manifest must fail closed, not AttributeError later


# --- 2026-06-18 convergence pass: render_branch_worktree load_json fails closed on a
#     non-dict top-level value, matching its twin render_worker_schedule.load_json (helper drift) ---
def test_render_branch_worktree_load_json_rejects_non_dict(tmp_path):
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")  # valid JSON, but not an object
    with pytest.raises(SystemExit):
        rgb.load_json(arr)  # used to return a list -> AttributeError on the caller's .get()
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        rgb.load_json(bad)
    good = tmp_path / "good.json"
    good.write_text('{"schema_version": 1}', encoding="utf-8")
    assert rgb.load_json(good) == {"schema_version": 1}


# --- 2026-06-18 convergence pass: validate_manifest_waves fails closed (SystemExit) on a
#     malformed waves/branches shape instead of an AttributeError/TypeError traceback ---
def test_validate_manifest_waves_fails_closed_on_malformed_shape():
    # non-list waves (the model-audit-pass path skips lint_goal_bundle, so this is reachable)
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves({"waves": "abc", "branches": [{"id": "B01"}]}, [])
    # present-null waves must fail closed; missing waves retain the legacy default below.
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves({"waves": None, "branches": [{"id": "B01"}]}, ["serial reason"])
    # non-dict branch entry used to raise AttributeError on branch.get("id")
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves({"branches": ["not-a-dict"]}, [])


def test_validate_manifest_waves_rejects_non_string_manifest_branch_id():
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves(
            {"branches": [{"id": ["B01"]}], "waves": []},
            ["serial"],
        )
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves(
            {"branches": [{"id": {"value": "B01"}}], "waves": []},
            ["serial"],
        )


def test_validate_branch_dependencies_rejects_unhashable_branch_id():
    for branch_id in (["B01"], {"value": "B01"}):
        with pytest.raises(SystemExit):
            rgb.validate_branch_dependencies([{"id": branch_id, "depends_on": []}])


def test_validate_branch_dependencies_preserves_prior_dependency_ordering():
    assert rgb.validate_branch_dependencies(
        [
            {"id": "B01", "depends_on": []},
            {"id": "B02", "depends_on": ["B01"]},
        ]
    ) == {"B01": [], "B02": ["B01"]}
    with pytest.raises(SystemExit):
        rgb.validate_branch_dependencies(
            [
                {"id": "B01", "depends_on": ["B02"]},
                {"id": "B02", "depends_on": []},
            ]
        )


def _write_main_scheduler_ledger(tmp_path: Path, *, item_ids: list[str], events: list[dict]) -> None:
    scheduler_path = tmp_path / rgb.CONTRACT.MAIN_SCHEDULER_PATH
    scheduler_path.parent.mkdir(parents=True, exist_ok=True)
    scheduler_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "scheduler_kind": "main-branch-pool",
                "scheduler_path": rgb.CONTRACT.MAIN_SCHEDULER_PATH,
                "capacity": 1,
                "item_ids": item_ids,
                "events": events,
            }
        ),
        encoding="utf-8",
    )


def test_main_scheduler_replay_rejects_stale_item_ids_and_unknown_launch(tmp_path):
    _write_main_scheduler_ledger(tmp_path, item_ids=["B99"], events=[{"event": "launch", "id": "B99"}])
    with pytest.raises(SystemExit):
        rgb.scheduler_state_from_ledger(tmp_path / "job.manifest.json", ["B01"], 1)


def test_main_scheduler_replay_preserves_valid_states(tmp_path):
    _write_main_scheduler_ledger(
        tmp_path,
        item_ids=["B01", "B02", "B03"],
        events=[
            {"event": "launch", "id": "B01"},
            {"event": "finish", "id": "B01", "status": "pass"},
            {"event": "close", "id": "B01"},
            {"event": "launch", "id": "B02"},
            {"event": "finish", "id": "B02", "status": "blocked"},
            {"event": "blocked", "id": "B02", "reason_code": "timeout"},
            {"event": "close", "id": "B02"},
            {"event": "launch", "id": "B03"},
        ],
    )

    active, passed, non_pass, relaunchable = rgb.scheduler_state_from_ledger(
        tmp_path / "job.manifest.json", ["B01", "B02", "B03"], 1
    )
    assert active == {"B03"}
    assert passed == {"B01"}
    assert non_pass == {"B02"}
    assert relaunchable == {"B02"}


# --- 2026-06-18 convergence pass 12: a non-string element inside a wave's `branches` fails closed
#     (was a TypeError on set(wave_branch_ids) with an unhashable element) ---
def test_validate_manifest_waves_rejects_non_string_wave_branch_element():
    manifest = {
        "branches": [{"id": "B01"}, {"id": "B02"}],
        "waves": [{"id": "W1", "branches": [{"nested": "dict"}, "B02"]}],
    }
    with pytest.raises(SystemExit):
        rgb.validate_manifest_waves(manifest, ["serial reason"])


# --- 2026-06-18 convergence pass 3: create_audit_packet.render_prompt fails closed on a malformed
#     manifest (the first audit step; was a KeyError/AttributeError traceback) ---
def test_create_audit_packet_render_prompt_fails_closed(tmp_path):
    # missing main_prompt -> was KeyError
    with pytest.raises(SystemExit):
        cap.render_prompt(tmp_path / "job.manifest.json", tmp_path, {"branches": [{"id": "B01"}]})
    # non-dict branch entry -> was AttributeError on branch.get(...)
    with pytest.raises(SystemExit):
        cap.render_prompt(tmp_path / "job.manifest.json", tmp_path, {"main_prompt": 5, "branches": ["x"]})


# --- 2026-06-18 convergence pass 4: render_prompt coerces non-string depends_on elements in the
#     join (the sibling worker_packet_ids/types joins already used str(); depends_on did not) ---
def test_create_audit_packet_render_prompt_coerces_depends_on(tmp_path):
    manifest = {
        "main_prompt": "main.prompt.md",
        "max_active_branch_agents": 1,
        "waves": [],
        "branches": [
            {
                "id": "B01",
                "prompt": "b.prompt.md",
                "branch_name": "phaseX-B01",
                "worktree_path": "wt/B01",
                "status_path": "b.status.json",
                "review_path": "b.review.json",
                "depends_on": [1, 2],  # non-string elements used to crash str.join
                "work_items": [],
            }
        ],
    }
    out = cap.render_prompt(tmp_path / "job.manifest.json", tmp_path, manifest)  # must not raise
    assert "depends_on=1,2" in out


# --- C1: the generated branch launch prompt interpolates the real branch id ---
def test_cli_launch_prompt_interpolates_branch_id(tmp_path):
    branch = {
        "id": "B01",
        "prompt": "branches/B01.prompt.md",
        "worktree_path": ".worktrees/job-B01",
    }
    plan = rgb.bounded_cli_launch_plan(
        branch,
        manifest_path=tmp_path / "job.manifest.json",
        repo_root=tmp_path,
        cli_branch_model=None,
        cli_branch_model_source="default",
    )
    blob = json.dumps(plan)
    assert "{branch_id}" not in blob, "literal {branch_id} placeholder leaked into the launch plan"
    assert "branches/B01.status.json" in blob, "branch id was not interpolated into the status-file guidance"


# --- B1: the core validator gate fails closed (structured) on malformed JSON ---
def test_validate_main_status_cli_fails_closed_on_malformed_json(tmp_path):
    status_path = tmp_path / "main.status.json"
    status_path.write_text("not valid json {", encoding="utf-8")
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills" / "goal-main-orchestrator" / "scripts" / "validate_main_status.py"),
            "--status",
            str(status_path),
            "--manifest",
            str(manifest_path),
        ],
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in proc.stderr, f"validator crashed instead of failing closed:\n{proc.stderr}"
    assert "readable JSON" in combined, combined


# --- B2: conservative loader + the artifact readers degrade to blockers, never crash ---
def test_safe_load_object_handles_malformed_and_non_object(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text('{"status": "pass"}', encoding="utf-8")

    blockers: list[str] = []
    assert ams.safe_load_object(bad, blockers, "x") == {}
    assert ams.safe_load_object(arr, blockers, "y") == {}
    assert ams.safe_load_object(good, blockers, "z") == {"status": "pass"}
    assert any("readable JSON" in b for b in blockers)
    assert any("must be a JSON object" in b for b in blockers)


def test_audit_status_blocks_on_malformed_artifact(tmp_path):
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "prompt-audit.json").write_text("{ not json", encoding="utf-8")
    blockers: list[str] = []
    assert ams.audit_status(tmp_path, blockers) == "blocked"
    assert blockers


def test_assemble_nonempty_str_list_guards_non_list():
    # 2026-06-18 convergence pass 6 (proactive sweep): the id-list helper tolerates a non-list
    # branch_parallelism.active_ids (semi-trusted branch artifact) instead of TypeError.
    assert ams._nonempty_str_list(5) == []
    assert ams._nonempty_str_list(None) == []
    assert ams._nonempty_str_list(["a", "", 3, "b"]) == ["a", "b"]


def test_branch_summaries_tolerates_non_list_blockers(tmp_path):
    # 2026-06-18 convergence pass 6: a branch status artifact with a non-list `blockers`
    # used to raise TypeError in branch_summaries; now it is skipped.
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(
        json.dumps({"status": "failed", "blockers": 5}), encoding="utf-8"
    )
    branches = [{"id": "B01", "status_path": "branches/B01.status.json", "review_path": "branches/B01.review.json"}]
    blockers: list[str] = []
    summaries = ams.branch_summaries(tmp_path, branches, blockers)  # must not raise
    assert summaries[0]["status"] == "failed"


def test_branch_summaries_blocks_on_malformed_status(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text("][", encoding="utf-8")
    branches = [
        {
            "id": "B01",
            "status_path": "branches/B01.status.json",
            "review_path": "branches/B01.review.json",
        }
    ]
    blockers: list[str] = []
    summaries = ams.branch_summaries(tmp_path, branches, blockers)  # must not raise
    assert summaries[0]["status"] == "failed"
    assert any("readable JSON" in b for b in blockers)


# --- A2: the standalone --require-pass gate now enforces actionability_verdict ---
def _write_audit(path: Path, manifest_path: Path, repo_root: Path, *, verdict: bool) -> None:
    audit = {
        "manifest": manifest_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "status": "failed",
        "can_start": False,
        "checked_files": ["job.manifest.json"],
        "commands_run": ["python3 deterministic_prompt_audit.py"],
        "missing_dod_items": [],
        "defects": [],
        "summary": "audit summary",
    }
    if verdict:
        audit["actionability_verdict"] = "failed"
    path.write_text(json.dumps(audit), encoding="utf-8")


def test_validate_prompt_audit_requires_actionability_verdict(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    repo_root = tmp_path
    audit_path = tmp_path / "prompt-audit.json"

    _write_audit(audit_path, manifest_path, repo_root, verdict=False)
    defects = vpa.validate_prompt_audit(audit_path, manifest_path, repo_root, require_pass=False)
    assert any("$.actionability_verdict" in d for d in defects), defects

    _write_audit(audit_path, manifest_path, repo_root, verdict=True)
    defects = vpa.validate_prompt_audit(audit_path, manifest_path, repo_root, require_pass=False)
    assert not any("$.actionability_verdict" in d for d in defects), defects


# --- A4: deterministic auditor tolerates a non-list branches value (no TypeError) ---
def test_checked_prompt_files_tolerates_non_list_branches(tmp_path):
    paths = dpa.checked_prompt_files(tmp_path, {"branches": 5})  # must not raise
    names = {p.name for p in paths}
    assert "job.manifest.json" in names
    assert "goal-bootloader.md" in names


# --- D1: debug trace builder skips an unreadable debug.events.jsonl instead of crashing ---
def test_iter_debug_trace_skips_unreadable_event_file(tmp_path, monkeypatch):
    worker_dir = tmp_path / "workers" / "B01-W01"
    worker_dir.mkdir(parents=True)
    unreadable = worker_dir / stl.DEBUG_EVENTS_FILENAME
    unreadable.write_text("{}", encoding="utf-8")

    original_read_text = Path.read_text

    def fail_debug_event_read(path: Path, *args, **kwargs):
        if path == unreadable:
            raise OSError("not readable")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_debug_event_read)
    events = stl.iter_debug_event_trace_events(tmp_path)  # must not raise
    assert any(e.get("event_type") == "trace_defect" for e in events)


# --- 2026-06-18 convergence pass 16: resolve_selected_ids fails closed on a malformed wave whose
#     `branches` is not an array of strings (was `set([...])` -> TypeError: unhashable type). ---
def test_resolve_selected_ids_rejects_non_string_wave_branches():
    args = types.SimpleNamespace(branch=None, wave="w1")
    waves = [{"id": "w1", "branches": [{"id": "b"}]}]  # branches must be strings, not objects
    sets = rgb.SchedulerSets([], set(), set(), set(), set(), set())
    with pytest.raises(SystemExit):
        rgb.resolve_selected_ids(args, waves, {}, sets, 1)


# --- 2026-06-18 fresh-audit pass: residual unguarded set/frozenset membership over a non-string
#     (unhashable) scalar that survived the pass-15 sweep in goal-main-orchestrator. Each site read
#     a value from a semi-trusted artifact and did `x (not) in {set}` without an isinstance guard, so
#     a tampered list/dict-valued field raised `TypeError: unhashable type` instead of failing closed.
#     The fail-closed convention requires a structured defect/blocker, never an uncaught traceback. ---


# C1: validate_branch_summary review_status (line 91 already guarded status; review_status was not).
def test_validate_branch_summary_tolerates_unhashable_review_status():
    defects: list[str] = []
    vms.validate_branch_summary(
        defects,
        {"branch_id": "B01", "status": "partial", "status_path": "a", "review_path": "b", "review_status": ["x"]},
        "$.b",
    )  # must not raise TypeError on `review_status not in REVIEW_STATUSES`
    assert any("review_status" in d for d in defects), defects


# C2: validate_review_artifact verdict.
def test_validate_review_artifact_tolerates_unhashable_verdict():
    defects: list[str] = []
    vms.validate_review_artifact(
        defects,
        {"verdict": ["mergeable"], "role": "reviewer", "findings": []},
        "mergeable",
        "$.review",
        manifest={},
        branch_id="B01",
    )  # must not raise on `verdict not in REVIEW_STATUSES - {"missing"}`
    assert any("verdict" in d for d in defects), defects


# C3: validate_amendment_decision_blockers iterates branch_statuses and tests `item.get("status") in STATUSES`.
def test_validate_amendment_decision_blockers_tolerates_unhashable_branch_status():
    defects: list[str] = []
    root = {
        "amendment_decision_blockers": [],
        "branch_statuses": [{"branch_id": "B01", "status": {"nested": "dict"}}],  # unhashable status
    }
    vms.validate_amendment_decision_blockers(defects, root, status="partial")  # must not raise
    assert isinstance(defects, list)


# C4: validate_prompt_audit status.
def test_validate_prompt_audit_tolerates_unhashable_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    audit_path = tmp_path / "prompt-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "manifest": manifest_path.as_posix(),
                "repo_root": tmp_path.as_posix(),
                "status": ["pass"],  # unhashable status
                "can_start": True,
                "checked_files": [],
                "commands_run": [],
                "missing_dod_items": [],
                "defects": [],
                "summary": "s",
                "actionability_verdict": "failed",
            }
        ),
        encoding="utf-8",
    )
    defects = vpa.validate_prompt_audit(audit_path, manifest_path, tmp_path, require_pass=False)  # must not raise
    assert any("$.status" in d for d in defects), defects


# C5: assemble branch_summaries status/review_status.
def test_branch_summaries_tolerates_unhashable_status(tmp_path):
    (tmp_path / "branches").mkdir()
    (tmp_path / "branches" / "B01.status.json").write_text(
        json.dumps({"status": ["pass"], "review_status": {"x": 1}}), encoding="utf-8"
    )
    branches = [{"id": "B01", "status_path": "branches/B01.status.json", "review_path": "branches/B01.review.json"}]
    blockers: list[str] = []
    summaries = ams.branch_summaries(tmp_path, branches, blockers)  # must not raise TypeError
    assert summaries[0]["status"] == "failed"
    assert any("invalid status" in b for b in blockers)


# C6: assemble current_amendment_records manifest_sha256.
def test_current_amendment_records_tolerates_unhashable_manifest_sha(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    (tmp_path / "amendments").mkdir()
    (tmp_path / "amendments" / "A1.decision.json").write_text(
        json.dumps({"amendment_id": "A1", "decision": "launch", "manifest_sha256": ["sha256:abc"]}),
        encoding="utf-8",
    )
    blockers: list[str] = []
    records, covered, ignored = ams.current_amendment_records(manifest_path, [], {}, blockers)  # must not raise
    assert any("manifest sha256 mismatch" in r for r in ignored), ignored


def test_current_amendment_records_tolerates_unhashable_branch_status(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    (tmp_path / "amendments").mkdir()
    (tmp_path / "amendments" / "A1.decision.json").write_text(
        json.dumps(
            {
                "amendment_id": "A1",
                "decision": "launch",
                "manifest": manifest_path.as_posix(),
                "manifest_sha256": ams.sha256_file(manifest_path),
                "terminal_branch_ids": ["B01"],
                "terminal_branch_statuses": {"B01": "pass"},
            }
        ),
        encoding="utf-8",
    )

    blockers: list[str] = []
    records, covered, ignored = ams.current_amendment_records(
        manifest_path,
        [{"branch_id": "B01", "status": ["pass"]}],
        {},
        blockers,
    )

    assert records == []
    assert covered == set()
    assert any("terminal_branch_ids not present in current branch summaries: B01" in r for r in ignored), ignored

    records, covered, ignored = ams.current_amendment_records(
        manifest_path,
        [{"branch_id": "B01", "status": "pass"}],
        {},
        [],
    )
    assert [record["amendment_id"] for record in records] == ["A1"]
    assert covered == {"B01"}
    assert ignored == []


# C7: failure_summary's event-log-name fallback must match the run loop's `attempt-{index+1}` fallback
#     (previously fell back to the bare "attempt", so an alias-less attempt mis-reported the log as missing).
def test_failure_summary_log_name_fallback_matches_run_loop(tmp_path):
    config = {"attempts": [{}]}  # one attempt, no alias, no event_logs
    summary = rpar.failure_summary(tmp_path, config)
    assert "events-attempt-1.jsonl" in summary, summary
    assert "events-attempt.jsonl" not in summary, summary


# --- 2026-06-18 RE-AUDIT pass: render_branch_worktree_commands._validate_work_item_fields must accept
#     the legacy worker_type alias "research" and fail closed (clean SystemExit, not TypeError) on a
#     non-string worker_type — the sibling of the render_worker_schedule fix. ---
def _rbwc_item(worker_type):
    return {
        "id": "W01",
        "packet_id": "B01-W01",
        "objective": "do the thing",
        "worker_type": worker_type,
        "owned_paths": ["src/a.py"],
        "verification": ["pytest"],
        "dod": ["done"],
        "context_files": [],
        "depends_on": [],
    }


def test_rbwc_validate_work_item_fields_accepts_research_alias():
    rgb._validate_work_item_fields([_rbwc_item("research")], "B01")  # must not raise


def test_rbwc_research_alias_requires_research_worker_policy():
    with pytest.raises(SystemExit, match="research_worker_policy is required"):
        rgb.validate_research_worker_policy({}, [{"work_items": [_rbwc_item("research")]}])


def test_rbwc_validate_work_item_fields_fails_closed_on_unhashable_worker_type():
    with pytest.raises(SystemExit):
        rgb._validate_work_item_fields([_rbwc_item(["worker"])], "B01")


# --- 2026-06-18 RE-AUDIT pass (final verification): validate_prompt_audit pass-branch crashed on a
#     defect whose `severity` is a list/dict (item was dict-guarded but severity was not str-guarded
#     before the `severity in {"critical","major"}` set-membership). ---
def test_validate_prompt_audit_tolerates_unhashable_defect_severity(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    audit_path = tmp_path / "prompt-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "manifest": manifest_path.as_posix(),
                "repo_root": tmp_path.as_posix(),
                "status": "pass",
                "can_start": True,
                "checked_files": ["job.manifest.json"],
                "commands_run": ["python3 deterministic_prompt_audit.py"],
                "missing_dod_items": [],
                "actionability_verdict": "pass",
                "defects": [{"severity": ["critical"], "file": "f.py", "message": "m"}],  # unhashable severity
                "summary": "s",
            }
        ),
        encoding="utf-8",
    )
    defects = vpa.validate_prompt_audit(audit_path, manifest_path, tmp_path, require_pass=False)  # must not raise
    assert isinstance(defects, list)
