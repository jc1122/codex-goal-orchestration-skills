"""Regression tests for the 2026-06-18 deep-review fixes (goal-plan-amender).

Pins the verified defects:
- amendment_lib.load_json_object fails closed on malformed JSON (the shared helper that
  every amender script — validate_proposal / create_adaptation_packet / create_amendment_
  decision / create_blocker_repair_packet / validate_manifest_amendment — reads through);
- create_blocker_repair_packet.safe_path no longer silently drops `.github/` paths.
"""

import sys

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-plan-amender" / "scripts"))

amendment_lib = load_module("skills/goal-plan-amender/scripts/amendment_lib.py", "amlib_review")
cbr = load_module("skills/goal-plan-amender/scripts/create_blocker_repair_packet.py", "cbr_review")


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
