"""Regression tests pinning the behaviors fixed in the 2026-06-17 review.

Each test would FAIL on the pre-fix code; verify red-green per the plan.
"""

import pytest

from conftest import load_module

et = load_module("skills/_goal_shared/scripts/extract_telemetry.py")
cg = load_module("skills/goal-config/scripts/create_goal_config.py")
rpr = load_module("skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py")


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
