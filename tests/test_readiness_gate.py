"""Authoritative readiness must NOT certify a bundle that was never linted / repair-gated.

`create_goal_bundle.py` alone does not run bundle-lint or the repair gate. Before the fix, the
authoritative `--readiness --json` path stripped the "bundle lint missing" / "repair gate missing"
blockers unconditionally, so a never-validated bundle reported launch_allowed=true.
"""

import json
import subprocess
import sys

from conftest import REPO

BRIEF = REPO / "fixtures" / "preparedness" / "research-worker-brief.json"


def _run(args):
    return subprocess.run([sys.executable, *args], cwd=REPO, capture_output=True, text=True)


def test_unlinted_bundle_is_not_launch_allowed(tmp_path):
    bundle = tmp_path / "bundle"
    create = _run(
        [
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            str(BRIEF),
            "--repo-root",
            str(REPO),
            "--out-dir",
            str(bundle),
            "--json",
        ]
    )
    assert create.returncode == 0, create.stderr
    # create_goal_bundle alone runs neither bundle-lint nor the repair gate.
    assert not (bundle / "preflight.lint.json").exists()
    assert not (bundle / "repair-gate.json").exists()

    readiness = _run(
        [
            "skills/goal-preflight/scripts/render_goal_bootloader.py",
            "--bundle-dir",
            str(bundle),
            "--readiness",
            "--json",
        ]
    )
    data = json.loads(readiness.stdout)
    assert data["launch_allowed"] is False
    assert "bundle lint missing" in data["launch_blockers"]
    assert "repair gate missing" in data["launch_blockers"]
