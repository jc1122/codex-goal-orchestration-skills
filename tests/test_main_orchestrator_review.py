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
from pathlib import Path

import pytest
from conftest import REPO, load_module

rgb = load_module("skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py", "rgb_mo")
ams = load_module("skills/goal-main-orchestrator/scripts/assemble_main_status.py", "ams_mo")
vpa = load_module("skills/goal-main-orchestrator/scripts/validate_prompt_audit.py", "vpa_mo")
dpa = load_module("skills/goal-main-orchestrator/scripts/deterministic_prompt_audit.py", "dpa_mo")
stl = load_module("skills/goal-main-orchestrator/scripts/summarize_telemetry.py", "stl_mo")
cap = load_module("skills/goal-main-orchestrator/scripts/create_audit_packet.py", "cap_mo")


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
def test_iter_debug_trace_skips_unreadable_event_file(tmp_path):
    worker_dir = tmp_path / "workers" / "B01-W01"
    worker_dir.mkdir(parents=True)
    broken = worker_dir / stl.DEBUG_EVENTS_FILENAME
    broken.symlink_to(tmp_path / "does-not-exist.jsonl")  # broken symlink: exists()->glob, read->error
    events = stl.iter_debug_event_trace_events(tmp_path)  # must not raise
    assert any(e.get("event_type") == "trace_defect" for e in events)
