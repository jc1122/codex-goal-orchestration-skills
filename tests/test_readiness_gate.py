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


def test_missing_bootloader_blocks_readiness_even_with_stale_pass_reports(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "job.manifest.json").write_text(
        json.dumps(
            {
                "branches": [],
                "repo_status": {
                    "repo_is_git": True,
                    "repo_root": str(REPO),
                    "base_ref_status": "exists",
                },
            }
        ),
        encoding="utf-8",
    )
    (bundle / "preflight.brief.lint.json").write_text('{"status": "pass"}', encoding="utf-8")
    (bundle / "preflight.lint.json").write_text('{"status": "pass", "defects": []}', encoding="utf-8")
    (bundle / "repair-gate.json").write_text('{"status": "pass", "actions": []}', encoding="utf-8")

    readiness = _run(
        [
            "skills/goal-preflight/scripts/render_goal_bootloader.py",
            "--bundle-dir",
            str(bundle),
            "--readiness",
            "--json",
        ]
    )
    assert readiness.returncode == 0, readiness.stderr
    data = json.loads(readiness.stdout)
    assert data["bootloader_exists"] is False
    assert data["launch_allowed"] is False
    assert "goal-bootloader.md missing" in data["launch_blockers"]
