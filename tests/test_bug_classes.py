"""Regression tests pinning the behaviors fixed in the 2026-06-17 review.

Each test would FAIL on the pre-fix code; verify red-green per the plan.
"""

import subprocess
from pathlib import Path

import pytest

from conftest import load_module

et = load_module("skills/_goal_shared/scripts/extract_telemetry.py")
cg = load_module("skills/goal-config/scripts/create_goal_config.py")
rpr = load_module("skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py")
vb = load_module("skills/goal-branch-orchestrator/scripts/validate_branch_status.py", "vb_ladder")
ab = load_module("skills/goal-branch-orchestrator/scripts/assemble_branch_status.py", "ab_git")
ccg = load_module("skills/goal-config/scripts/check_goal_config.py", "ccg_guard")
_CONTRACT = ccg.load_contract()


# --- extract_telemetry: a blocked lite_advisor must never be marked accepted ---
@pytest.mark.parametrize(
    "blocker",
    [
        "Lite advisor bridge delegate failed. Inspect the bridge run-dir artifacts "
        "for transport, model, permission, or validation errors.",
        "bridge pool capacity limit reached; scheduler should refill later",
        "Lite advisor did not produce valid advice JSON.",
    ],
)
def test_blocked_lite_never_accepted(blocker):
    attempts = [{"alias": "ds-flash-max", "called": True}]
    out = {"status": "blocked", "blockers": [blocker]}
    assert et.accepted_alias("lite_advisor", out, attempts) is None


def test_passing_lite_is_accepted():
    attempts = [{"alias": "ds-flash-max", "called": True}]
    out = {"status": "pass", "blockers": []}
    assert et.accepted_alias("lite_advisor", out, attempts) == "ds-flash-max"


# --- create_goal_config: no doubled provider prefix on a non-implied harness ---
def test_qualified_model_keeps_listed_provider():
    assert cg.normalize_role_model_for_harness("anthropic/claude", "generic-cli", "openai") == (
        "anthropic",
        "anthropic/claude",
    )


def test_bare_model_gets_default_provider_prefix():
    assert cg.normalize_role_model_for_harness("gpt-5", "generic-cli", "openai") == ("openai", "openai/gpt-5")


def test_implied_harness_returns_bare_model():
    # opencode-bridge is an implied-provider harness -> bare model suffix
    assert cg.normalize_role_model_for_harness("deepseek/deepseek-v4-flash", "opencode-bridge", None) == (
        "deepseek",
        "deepseek-v4-flash",
    )


# --- runtime_packet_runner: worker ownership fail-closed on empty owned_files ---
def test_worker_empty_owned_flags_all_changes():
    changed = ["a/b.py", "c/d.py"]
    assert rpr.worker_ownership_violations({"role": "worker"}, changed) == changed


def test_reviewer_empty_owned_flags_nothing():
    assert rpr.worker_ownership_violations({"role": "reviewer"}, ["a/b.py"]) == []


def test_worker_with_owned_flags_only_unowned():
    assert rpr.worker_ownership_violations({"role": "worker", "owned_files": ["a/"]}, ["a/b.py", "c/d.py"]) == [
        "c/d.py"
    ]


# --- validate_worker_ladder: empty allowed + alias not in ladder must not raise (was ValueError) ---
def test_validate_worker_ladder_no_valueerror_on_empty_allowed():
    defects: list[str] = []
    # pre-fix this raised ValueError (allowed.index on an empty list); the guard now returns instead.
    result = vb.validate_worker_ladder(defects, ["x-route"], "$.ladder", allowed_routes=[], default_ladder=["a", "b"])
    assert result == ["x-route"]


# --- assemble_branch_status.changed_files_from_git: git failure records a blocker (fail-closed) ---
def test_changed_files_from_git_failure_records_blocker(monkeypatch):
    monkeypatch.setattr(
        ab,
        "run_git",
        lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=1, stdout="fatal: bad rev", stderr=""),
    )
    blockers: list[str] = []
    result = ab.changed_files_from_git(Path("/nonexistent"), "main", blockers)
    assert result == []
    assert blockers and "failed" in blockers[0].lower()


# --- check_goal_config.validate_for_preflight: a non-dict telemetry must not crash (was AttributeError) ---
def test_validate_for_preflight_nondict_telemetry_no_crash():
    config = {
        "aggressiveness": {"max_active_branch_agents": 1, "max_active_worker_packets": 1, "max_waves": 1},
        "validation": {"mode": "debug"},
        "telemetry": "not-a-dict",
    }
    # pre-fix: config.get("telemetry", {}).get("mode") raised AttributeError on a non-dict telemetry.
    failures = ccg.validate_for_preflight(config, "smoke", _CONTRACT)
    assert any("telemetry" in failure for failure in failures)
