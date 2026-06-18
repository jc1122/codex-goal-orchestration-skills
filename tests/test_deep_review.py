"""Regression tests for the 2026-06-17 deep-review fixes (goal-preflight)."""

import shlex
import sys

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-preflight" / "scripts"))

lpb = load_module("skills/goal-preflight/scripts/lint_preflight_brief.py", "lpb_dr")
rgb = load_module("skills/goal-preflight/scripts/render_goal_bootloader.py", "rgb_dr")
pgb = load_module("skills/goal-preflight/scripts/prepare_goal_bundle.py", "pgb_dr")
lgb = load_module("skills/goal-preflight/scripts/lint_goal_bundle.py", "lgb_dr")
cgb = load_module("skills/goal-preflight/scripts/create_goal_bundle.py", "cgb_dr")


# --- 2026-06-18 convergence pass: the preserve-config cleanup plan lists stale-artifacts.index.json
#     (written by reconcile to the bundle root) so it cannot survive a cleanup as an orphan ---
def test_cleanup_plan_includes_stale_artifacts_index(tmp_path):
    plan = rgb._cleanup_plan(tmp_path, None, [])
    assert "stale-artifacts.index.json" in plan["generated_artifacts"]


# --- 2026-06-18 convergence pass: create_goal_bundle._resolve_waves fails closed on a non-list
#     brief `waves` (the standalone entrypoint had no guard; the bundle linter already did) ---
def test_resolve_waves_rejects_non_list_waves():
    with pytest.raises(SystemExit):
        cgb._resolve_waves({"waves": "abc"}, [{"id": "B01"}], 4)


# --- 2026-06-18 convergence pass 2: _resolve_waves also fails closed on malformed wave ELEMENTS
#     (non-dict wave, wave without id, non-string branch entry) — pass-1 only guarded the list itself ---
def test_resolve_waves_rejects_malformed_wave_elements():
    with pytest.raises(SystemExit):  # non-dict wave element -> used to be TypeError(string indices)
        cgb._resolve_waves({"waves": ["BR1"]}, [{"id": "B01"}], 4)
    with pytest.raises(SystemExit):  # wave dict missing id -> used to be KeyError
        cgb._resolve_waves({"waves": [{"branches": ["B01"]}]}, [{"id": "B01"}], 4)
    with pytest.raises(SystemExit):  # non-string branch entry -> used to be TypeError(unhashable dict)
        cgb._resolve_waves({"waves": [{"id": "W1", "branches": [{"nested": 1}]}]}, [{"id": "B01"}], 4)


# --- 2026-06-18 convergence pass 3: create_goal_bundle.load_json fails closed on a non-object
#     brief and on a non-UTF-8 file (standalone create entrypoint had neither guard) ---
def test_create_goal_bundle_load_json_fails_closed(tmp_path):
    arr = tmp_path / "brief.json"
    arr.write_text("[]", encoding="utf-8")  # valid JSON, non-object -> used to TypeError in create_bundle
    with pytest.raises(SystemExit):
        cgb.load_json(arr)
    nonutf8 = tmp_path / "brief2.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        cgb.load_json(nonutf8)


# --- 2026-06-18 convergence pass 4: normalize_brief fails closed on a non-list / list-of-non-dict
#     `branches` (was an AttributeError in _build_branch_record on the standalone create path) ---
def test_normalize_brief_rejects_malformed_branches():
    with pytest.raises(SystemExit):
        cgb.normalize_brief({"job_id": "j1", "branches": "B01"}, validate_base_ref=False)
    with pytest.raises(SystemExit):
        cgb.normalize_brief({"job_id": "j1", "branches": ["not-a-dict"]}, validate_base_ref=False)


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


# --- 2026-06-18 re-review residuals: linter fail-closed on non-list depends_on/owned_paths ---
def test_branch_dependency_levels_tolerates_non_list_depends_on():
    # A non-iterable depends_on used to raise TypeError, aborting the whole linter.
    levels = lgb.branch_dependency_levels([{"id": "B01", "depends_on": 7}])
    assert "B01" in levels  # resolves (no crash) rather than raising TypeError


def test_work_item_cross_checks_tolerates_non_list_fields():
    collected: list = []

    def defect(file, severity, message):
        collected.append((file, severity, message))

    branch = {"id": "B01", "owned_paths": []}
    work_items = [{"id": "W01", "packet_id": "B01-W01", "depends_on": 5, "owned_paths": 9}]
    # Non-list depends_on / owned_paths previously raised TypeError here.
    lgb._lint_work_item_cross_checks(defect, branch, work_items, [])


# --- cleanup plan must remove the runtime status / catalog / trace artifacts too ---
def test_cleanup_plan_removes_runtime_status_and_catalog_artifacts(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "goal.config.json").write_text("{}", encoding="utf-8")  # preserve branch
    extra = ("main.status.json", "model-catalog.json", "run.trace.jsonl", "telemetry.debug.summary.json")
    for name in extra:
        (bundle / name).write_text("{}", encoding="utf-8")
    cmds = "\n".join(rgb._cleanup_plan(bundle, None, [])["cleanup_commands"])
    for name in extra:
        assert (bundle / name).as_posix() in cmds, f"{name} not removed by cleanup"
    assert (bundle / "goal.config.json").as_posix() not in cmds


# --- readiness readers tolerate a malformed (non-list) defects/actions value ---
def test_lint_status_tolerates_non_list_defects(tmp_path):
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "preflight.lint.json").write_text('{"status": "failed", "defects": 5}', encoding="utf-8")
    assert rgb._lint_status(bundle, "schema")["defect_count"] == 0  # must not raise


def test_repair_gate_status_tolerates_non_list_actions(tmp_path):
    bundle = tmp_path / "b"
    bundle.mkdir()
    (bundle / "repair-gate.json").write_text('{"status": "blocked", "actions": 5}', encoding="utf-8")
    assert rgb._repair_gate_status(bundle)["action_count"] == 0  # must not raise
