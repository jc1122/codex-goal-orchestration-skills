"""Regression tests for the 2026-06-17 round-2 correctness fixes."""

import sys

from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-main-orchestrator" / "scripts"))

oc = load_module("skills/_goal_shared/scripts/orchestration_contract.py", "oc_r2")
ams = load_module("skills/goal-main-orchestrator/scripts/assemble_main_status.py", "ams_r2")


# --- normalize_route_ladder: deterministic + correct order even with a frozenset allowed set ---
def test_route_ladder_accepts_in_order_subsequence_with_frozenset():
    out = oc.normalize_route_ladder(
        ["ds-flash-max", "codex-spark"],
        default_ladder=oc.DEFAULT_WORKER_LADDER,
        allowed_routes=oc.ALLOWED_WORKER_ROUTES,  # a frozenset
        route_name="worker",
    )
    assert out == ["ds-flash-max", "codex-spark"]


def test_route_ladder_rejects_out_of_order_with_frozenset():
    import pytest

    with pytest.raises(ValueError, match="standard ladder order"):
        oc.normalize_route_ladder(
            ["codex-spark", "ds-flash-max"],
            default_ladder=oc.DEFAULT_WORKER_LADDER,
            allowed_routes=oc.ALLOWED_WORKER_ROUTES,
            route_name="worker",
        )


# --- aggregate_review_status: a "reject" verdict rolls up as blocked, not "missing" ---
def test_aggregate_review_status_reject_is_blocked():
    branch_statuses = [{"branch_id": "B01", "status": "partial", "review_status": "reject"}]
    assert ams.aggregate_review_status(branch_statuses, expected_branch_count=1) == "blocked"


def test_aggregate_review_status_all_mergeable():
    branch_statuses = [{"branch_id": "B01", "status": "pass", "review_status": "mergeable"}]
    assert ams.aggregate_review_status(branch_statuses, expected_branch_count=1) == "mergeable"
