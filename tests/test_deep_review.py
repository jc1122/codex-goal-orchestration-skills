"""Regression tests for the 2026-06-17 deep-review fixes (goal-preflight)."""

import shlex
import sys

from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-preflight" / "scripts"))

lpb = load_module("skills/goal-preflight/scripts/lint_preflight_brief.py", "lpb_dr")
rgb = load_module("skills/goal-preflight/scripts/render_goal_bootloader.py", "rgb_dr")
pgb = load_module("skills/goal-preflight/scripts/prepare_goal_bundle.py", "pgb_dr")


# --- PLACEHOLDER_RE: no longer false-positives on operators / generics-ish / emails ---
def test_placeholder_regex_ignores_operators_and_emails():
    clean = [
        "a < b and c > d",  # comparison operators, not a placeholder
        "x <= 5 >= 1",  # <= / >= operator forms
        "<jakub@example.com>",  # angle-wrapped email address
        "Ship the binary when CI is green.",  # ordinary prose
    ]
    for text in clean:
        assert not lpb.PLACEHOLDER_RE.search(text), f"false positive on: {text!r}"


def test_placeholder_regex_still_catches_real_placeholders():
    placeholders = [
        "<your goal here>",
        "<JOB_ID>",
        "<...>",
        "describe the fix ??? then ship",
        "TODO finish this",
    ]
    for text in placeholders:
        assert lpb.PLACEHOLDER_RE.search(text), f"missed placeholder in: {text!r}"


# --- _cleanup_plan: the generated commands must not delete the config it preserves ---
def test_cleanup_plan_preserves_existing_config(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "goal.config.json").write_text("{}", encoding="utf-8")
    plan = rgb._cleanup_plan(bundle, None, [])
    assert "goal.config.json" in plan["preserve_config_artifacts"]
    blanket = f"rm -rf {shlex.quote(bundle.as_posix())}"
    assert blanket not in plan["cleanup_commands"], "blanket rm -rf would delete the preserved config"


def test_cleanup_plan_removes_all_disposable_root_artifacts(tmp_path):
    # Preserve branch must still remove every disposable bundle-root artifact,
    # including runtime.index.json / create-bundle-result.json (the gap caught
    # by the second-pass review), while never targeting the preserved config.
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "goal.config.json").write_text("{}", encoding="utf-8")  # triggers preserve branch
    disposable = ("runtime.index.json", "create-bundle-result.json", "job.manifest.json")
    for name in disposable:
        (bundle / name).write_text("{}", encoding="utf-8")
    (bundle / "config-checks").mkdir()  # disposable dir written by prepare
    plan = rgb._cleanup_plan(bundle, None, [])
    cmds = "\n".join(plan["cleanup_commands"])
    for name in disposable:
        assert (bundle / name).as_posix() in cmds, f"{name} not removed by cleanup"
    assert (bundle / "config-checks").as_posix() in cmds, "config-checks dir not removed by cleanup"
    assert (bundle / "goal.config.json").as_posix() not in cmds


def test_cleanup_plan_blunt_remove_when_no_config(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    plan = rgb._cleanup_plan(bundle, None, [])
    assert plan["preserve_config_artifacts"] == []
    blanket = f"rm -rf {shlex.quote(bundle.as_posix())}"
    assert blanket in plan["cleanup_commands"]


# --- report_matches_config: recorded check-time sha is the freshness anchor ---
def test_report_matches_config_rejects_stale_by_recorded_sha(tmp_path):
    config = tmp_path / "goal.config.json"
    config.write_text('{"a": 1}\n', encoding="utf-8")
    live = pgb.sha256_file(config)
    # The check recorded a different sha => the config changed since the check => stale.
    stale = {"config_path": str(config), "config_sha256": "deadbeef"}
    assert pgb.report_matches_config(stale, config) is False
    # The check recorded the matching sha => fresh.
    fresh = {"config_path": str(config), "config_sha256": live}
    assert pgb.report_matches_config(fresh, config) is True


def test_report_matches_config_legacy_report_falls_back_to_path(tmp_path):
    config = tmp_path / "goal.config.json"
    config.write_text('{"a": 1}\n', encoding="utf-8")
    # A report written before config_sha256 existed still matches by path/content.
    legacy = {"config_path": str(config)}
    assert pgb.report_matches_config(legacy, config) is True
