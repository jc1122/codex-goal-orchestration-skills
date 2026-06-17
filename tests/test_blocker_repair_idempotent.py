"""Deterministic blocker-repair must be idempotent.

A terminal branch already recovered/superseded by an existing manifest branch (e.g. from a
prior blocker-repair run) must NOT produce a second repair branch with the same
recovers_from/supersedes target — the apply-operations validator does not reject duplicates.
"""

import json
import sys

from conftest import REPO, load_module

# create_blocker_repair_packet.py imports its sibling amendment_lib at module top; when run as a
# script Python adds the script dir to sys.path[0], but importlib.exec_module does not — add it.
sys.path.insert(0, str(REPO / "skills" / "goal-plan-amender" / "scripts"))
cbrp = load_module("skills/goal-plan-amender/scripts/create_blocker_repair_packet.py", "cbrp_idem")


def _proposal(tmp_path, branches):
    manifest = {"schema_version": 1, "job_id": "J1", "branches": branches}
    (tmp_path / "job.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "B1.status.json").write_text(
        json.dumps({"status": "failed", "blockers": ["missing implementation in src/a.py"]}),
        encoding="utf-8",
    )
    inp = {
        "manifest": (tmp_path / "job.manifest.json").as_posix(),
        "repo_root": tmp_path.as_posix(),
        "terminal_branch_ids": ["B1"],
        "amendment_id": "A1",
        "job_id": "J1",
    }
    return cbrp.generate_proposal(inp)


def test_blocker_repair_proposes_when_not_recovered(tmp_path):
    out = _proposal(tmp_path, [{"id": "B1", "status_path": "B1.status.json", "owned_paths": ["src/a.py"]}])
    assert len(out["operations"]) == 1


def test_blocker_repair_idempotent_when_already_recovered(tmp_path):
    out = _proposal(
        tmp_path,
        [
            {"id": "B1", "status_path": "B1.status.json", "owned_paths": ["src/a.py"]},
            {"id": "B2", "recovers_from": ["B1"], "supersedes": ["B1"], "owned_paths": ["src/a.py"]},
        ],
    )
    # pre-fix: a duplicate repair branch (second recovers_from/supersedes B1) was proposed.
    assert out["operations"] == []
