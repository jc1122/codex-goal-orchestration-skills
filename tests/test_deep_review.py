"""Regression tests for the 2026-06-17 deep-review fixes (goal-preflight)."""

import json
import shlex
import subprocess
import shutil
import sys
from pathlib import Path

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-preflight" / "scripts"))
sys.path.insert(0, str(REPO / "scripts"))

lpb = load_module("skills/goal-preflight/scripts/lint_preflight_brief.py", "lpb_dr")
rgb = load_module("skills/goal-preflight/scripts/render_goal_bootloader.py", "rgb_dr")
pgb = load_module("skills/goal-preflight/scripts/prepare_goal_bundle.py", "pgb_dr")
lgb = load_module("skills/goal-preflight/scripts/lint_goal_bundle.py", "lgb_dr")
cgb = load_module("skills/goal-preflight/scripts/create_goal_bundle.py", "cgb_dr")
cpf = load_module("scripts/check_preparedness_fixtures.py", "cpf_dr")


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


# --- 2026-06-18 convergence pass 13: _lint_waves tolerates an unhashable per-branch `wave` field
#     (was TypeError on `branch_wave not in declared_wave_ids` set membership) ---
def test_lint_waves_tolerates_unhashable_branch_wave():
    defects: list[str] = []

    def _defect(_file, _sev, msg):
        defects.append(msg)

    manifest = {
        "waves": [{"id": "W1", "branches": ["B01"]}],
        "branches": [{"id": "B01", "wave": ["unhashable"]}],
    }
    lgb._lint_waves(_defect, manifest, manifest["branches"], ["B01"], 4, True)  # must not raise TypeError
    assert any("wave must be a string" in m for m in defects), defects


@pytest.mark.parametrize(
    "field, value",
    [
        ("id", {}),
        ("id", []),
        ("branch_name", {}),
        ("branch_name", []),
        ("worktree_path", {}),
        ("worktree_path", []),
    ],
)
def test_lint_branch_identity_handles_unhashable_identity_fields(field, value):
    defects: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append((file, severity, message))

    branch = {"id": "B01", "branch_name": "branch-a", "worktree_path": "branches/B01.worktree"}
    branch[field] = value
    lgb._lint_branch_identity(defect, [branch])
    assert len(defects) == 1
    assert defects[0][1] == "critical"
    field_label = "branch_name" if field == "branch_name" else field
    if field_label == "branch_name":
        field_label = "branch name"
    assert field_label in defects[0][2]


def test_lint_reserved_bundle_paths_rejects_runtime_and_goal_config_artifacts():
    defects: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append((file, severity, message))

    branches = [
        {
            "id": "B01",
            "prompt": "runtime.index.json",
            "status_path": "goal.config.json",
            "review_path": "goal-config.check.json",
            "pre_review_gate_path": "branches/B01.pre-review-gate.md",
        }
    ]
    lgb._lint_reserved_bundle_paths(defect, branches)
    assert any("reserved bundle file: runtime.index.json" in msg for _, _, msg in defects), defects
    assert any("reserved bundle file: goal.config.json" in msg for _, _, msg in defects), defects
    assert any("reserved bundle file: goal-config.check.json" in msg for _, _, msg in defects), defects


# --- 2026-06-18 convergence pass 10: render_branch_source_contract tolerates a non-list
#     required_evidence/final_dod (standalone create path) instead of TypeError ---
def test_render_branch_source_contract_tolerates_non_list_fields():
    out = cgb.render_branch_source_contract({"required_evidence": None, "final_dod": 5}, "/bundle")
    assert isinstance(out, str)  # was TypeError on the str.join genexp


def test_build_ownership_feasibility_tolerates_unhashable_depends_on_values():
    branches = [
        {
            "id": "B01",
            "owned_paths": ["src/one.py"],
            "depends_on": [{"other": "id"}],
            "work_items": [],
        },
        {
            "id": "B02",
            "owned_paths": ["src/two.py"],
            "depends_on": [[], "B01"],
            "work_items": [],
        },
    ]
    result = cgb.build_ownership_feasibility(branches, repo_root=None)
    assert result["status"] in {"pass", "needs_review"}
    assert isinstance(result["dependency_recommendations"], list)


# --- 2026-06-18 convergence pass 6: normalize_brief fails closed on a non-string job_id
#     (slug() used to AttributeError on a dict/list job_id on the standalone create path) ---
def test_normalize_brief_rejects_non_string_job_id():
    with pytest.raises(SystemExit):
        cgb.normalize_brief({"job_id": {"x": 1}, "branches": [{"id": "B01"}]}, validate_base_ref=False)


@pytest.mark.parametrize(
    "path_field,path_value",
    [
        ("prompt", "runtime.index.json"),
        ("status_path", "goal.config.json"),
        ("review_path", "goal-config.check.json"),
    ],
)
def test_create_goal_bundle_rejects_reserved_bundle_artifact_paths(path_field, path_value):
    valid_work_item = {
        "id": "W01",
        "objective": "Run focused checks.",
        "owned_paths": ["src/work.py"],
        "context_files": ["src/work.py"],
        "verification": ["pytest tests/test_goal.py"],
        "depends_on": [],
        "dod": ["tests pass", "coverage holds"],
    }
    branch = {
        "id": "B01",
        "objective": "Shared path correction.",
        "work_items": [valid_work_item],
        "prompt": "branches/B01.prompt.md",
        "status_path": "branches/B01.status.json",
        "review_path": "branches/B01.review.json",
        "pre_review_gate_path": "branches/B01.pre-review-gate.md",
        path_field: path_value,
    }
    with pytest.raises(SystemExit):
        cgb.normalize_brief(
            {
                "job_id": "toy",
                "branches": [branch],
            },
            validate_base_ref=False,
        )


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


def test_report_matches_config_legacy_report_without_sha_fails_closed(tmp_path):
    config = tmp_path / "goal.config.json"
    config.write_text('{"a": 1}\n', encoding="utf-8")
    legacy = {"config_path": str(config)}
    assert pgb.report_matches_config(legacy, config) is False


def test_find_reusable_route_verified_check_rejects_legacy_report_without_sha(tmp_path):
    config = tmp_path / "goal.config.json"
    config.write_text('{"a": 1}\n', encoding="utf-8")
    legacy_check = tmp_path / "goal-config-smoke.json"
    _write_json(
        legacy_check,
        {
            "status": "pass",
            "config_path": str(config),
            "summary": {"route_verification_status": "routes_verified"},
        },
    )

    assert pgb.find_reusable_route_verified_check(config, legacy_check, tmp_path) is None


def test_find_reusable_route_verified_check_reuses_hash_mismatch_with_matching_route_evidence(tmp_path):
    config = tmp_path / "goal.config.json"
    payload = _minimal_goal_config_payload()
    payload["models"] = {
        "lite_agent": {
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-5.3-codex-spark",
        }
    }
    config.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    route = {
        "role": "lite_agent",
        "alias": "lite_agent",
        "harness": "codex",
        "provider": "openai",
        "model": "gpt-5.3-codex-spark",
    }
    check = tmp_path / "goal-config-smoke.json"
    _write_json(
        check,
        {
            "status": "pass",
            "mode": "smoke",
            "check_mode": "smoke",
            "config_path": config.as_posix(),
            "config_sha256": "stale-but-route-evidence-is-current",
            "checked_roles": ["lite_agent"],
            "accepted_routes": [route],
            "rejected_routes": [],
            "failures": [],
            "harnesses": [
                {
                    **route,
                    "harness_kind": "codex",
                    "model_check": {"status": "pass"},
                    "smoke": {"status": "pass"},
                }
            ],
            "summary": {
                "route_verification_status": "routes_verified",
                "accepted_route_count": 1,
                "checked_role_count": 1,
                "harness_count": 1,
                "failure_count": 0,
                "rejected_route_count": 0,
            },
        },
    )

    assert pgb.find_reusable_route_verified_check(config, check, tmp_path) == check


def _stale_route_regression_config() -> dict:
    model = {
        "harness": "local_echo",
        "provider": "local",
        "model": "echo-model",
        "alias": "local-echo",
    }
    route_classes = {name: ["lite_agent"] for name in [*cgb.MANIFEST_WORKER_ROUTE_CLASSES, "custom"]}
    return {
        "schema_version": 1,
        "profile": "stale-route-evidence-regression",
        "aggressiveness": {
            "max_active_branch_agents": 1,
            "max_active_worker_packets": 1,
            "max_waves": 1,
            "total_branch_cap": 1,
        },
        "validation": {"mode": "smoke"},
        "telemetry": {
            "schema_version": 1,
            "mode": "standard",
            "raw_text": False,
            "collect": ["route_decisions", "token_usage", "timings"],
        },
        "usage_units": {
            "token_counts": ["input", "output"],
            "text_counts": ["stdout_chars", "stderr_chars"],
            "time_counts": ["elapsed_ms"],
        },
        "models": {"lite_agent": model},
        "harnesses": {
            "local_echo": {
                "kind": "generic-cli",
                "command": "/bin/echo",
                "smoke_args": ["{prompt}"],
            }
        },
        "harness_smokes": {
            "lite_agent": {
                "prompt": "GOAL_STALE_ROUTE_REUSE_OK",
                "expect": "GOAL_STALE_ROUTE_REUSE_OK",
                "timeout_seconds": 5,
            }
        },
        "model_ladders": {
            "worker": ["lite_agent"],
            "reviewer": ["lite_agent"],
            "amender": ["lite_agent"],
            "lite": ["lite_agent"],
        },
        "model_policies": {
            "worker_model_policy": {
                "default_ladder": ["lite_agent"],
                "allowed_routes": ["lite_agent"],
                "branch_may_select_worker_route": True,
                "selection_reason_required": True,
                "ordering_rule": "Use the first available accepted route.",
                "route_classes": route_classes,
            },
            "review_model_policy": {
                "default_tier": "standard",
                "routes": {"light": ["lite_agent"], "standard": ["lite_agent"], "heavy": ["lite_agent"]},
            },
            "amender_model_policy": {
                "default_ladder": ["lite_agent"],
                "allowed_routes": ["lite_agent"],
            },
            "lite_model_policy": {
                "default_ladder": ["lite_agent"],
                "allowed_routes": ["lite_agent"],
                "model_map": {"lite_agent": model["model"]},
            },
        },
    }


def _stale_route_regression_smoke(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    route = {"role": "lite_agent", **config["models"]["lite_agent"]}
    return {
        "schema_version": 1,
        "status": "pass",
        "mode": "smoke",
        "check_mode": "smoke",
        "config_path": config_path.as_posix(),
        "config_sha256": "stale-but-complete-route-evidence",
        "checked_roles": ["lite_agent"],
        "accepted_routes": [route],
        "rejected_routes": [],
        "skipped_routes": [],
        "unvisited_routes": [],
        "failures": [],
        "harnesses": [
            {
                **route,
                "harness_kind": "generic-cli",
                "model_check": {"status": "pass"},
                "smoke": {"status": "pass", "contains_expected": True},
            }
        ],
        "summary": {
            "route_verification_status": "routes_verified",
            "accepted_route_count": 1,
            "checked_role_count": 1,
            "harness_count": 1,
            "failure_count": 0,
            "rejected_route_count": 0,
        },
    }


def test_prepare_goal_bundle_uses_fresh_check_when_reusing_stale_route_evidence(tmp_path):
    config_path = tmp_path / "goal.config.json"
    _write_json(config_path, _stale_route_regression_config())
    stale_smoke_path = tmp_path / "goal-config-smoke.json"
    _write_json(stale_smoke_path, _stale_route_regression_smoke(config_path))
    brief_path = tmp_path / "brief.json"
    brief = _minimal_goal_config_brief()
    brief["source_summary"] = (
        "Regression uses a current goal.config.json plus a stale goal-config-smoke.json whose accepted route "
        "evidence exactly matches the current config route set."
    )
    brief["required_evidence"] = [
        "prepare_goal_bundle.py completes create_bundle and writes bundled goal-config.check.json with matching config_sha256"
    ]
    brief["final_dod"] = [
        "goal-config-selection.json selects the fresh preflight check and records stale smoke evidence separately"
    ]
    branch = brief["branches"][0]
    branch["objective"] = (
        "Prove stale route-verified smoke evidence is reused only to produce a fresh authoritative config check."
    )
    branch["work_items"][0]["objective"] = (
        "Run prepare_goal_bundle.py with a stale explicit smoke report and inspect selection artifacts."
    )
    branch["work_items"][0]["dod"] = [
        "Copied goal-config.check.json config_sha256 equals the selected source goal.config.json sha256",
        "Selected check path is the fresh preflight check, not the stale smoke report",
    ]
    _write_json(brief_path, brief)
    out_dir = tmp_path / "bundle"

    completed = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills" / "goal-preflight" / "scripts" / "prepare_goal_bundle.py"),
            "--brief",
            str(brief_path),
            "--repo-root",
            str(REPO),
            "--out-dir",
            str(out_dir),
            "--goal-config",
            str(config_path),
            "--goal-config-check",
            str(stale_smoke_path),
            "--allow-blocked-readiness",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    pipeline = json.loads((out_dir / "preflight.pipeline.json").read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr
    assert pipeline.get("phase") != "create_bundle", pipeline
    assert (out_dir / "create-bundle-result.json").is_file()

    selection = json.loads((out_dir / "goal-config-selection.json").read_text(encoding="utf-8"))
    selected = selection["candidates"][selection["selected_index"]]
    assert selected["selected_check_path"] == selected["original_check_path"]
    assert selected["selected_check_path"] != stale_smoke_path.as_posix()
    assert selected["reused_route_evidence_path"] == stale_smoke_path.as_posix()

    fresh_check = json.loads(Path(selected["selected_check_path"]).read_text(encoding="utf-8"))
    assert fresh_check["config_sha256"] == pgb.sha256_file(config_path)
    assert fresh_check["reused_smoke_reports"][0]["path"] == stale_smoke_path.resolve().as_posix()

    copied_check = json.loads((out_dir / "goal-config.check.json").read_text(encoding="utf-8"))
    assert copied_check["config_sha256"] == pgb.sha256_file(config_path)


def test_find_reusable_route_verified_check_rejects_hash_mismatch_without_complete_route_evidence(tmp_path):
    config = tmp_path / "goal.config.json"
    payload = _minimal_goal_config_payload()
    payload["models"] = {
        "lite_agent": {
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-5.3-codex-spark",
        },
        "worker_primary": {
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-5.4-mini",
        },
    }
    config.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lite_route = {
        "role": "lite_agent",
        "alias": "lite_agent",
        "harness": "codex",
        "provider": "openai",
        "model": "gpt-5.3-codex-spark",
    }
    full_routes = [
        lite_route,
        {
            "role": "worker_primary",
            "alias": "worker_primary",
            "harness": "codex",
            "provider": "openai",
            "model": "gpt-5.4-mini",
        },
    ]

    def write_report(name: str, **overrides) -> Path:
        report = {
            "status": "pass",
            "mode": "smoke",
            "check_mode": "smoke",
            "config_path": config.as_posix(),
            "config_sha256": "stale-but-present",
            "checked_roles": ["lite_agent", "worker_primary"],
            "accepted_routes": full_routes,
            "rejected_routes": [],
            "failures": [],
            "harnesses": [
                {
                    **route,
                    "harness_kind": route["harness"],
                    "model_check": {"status": "pass"},
                    "smoke": {"status": "pass"},
                }
                for route in full_routes
            ],
            "summary": {
                "route_verification_status": "routes_verified",
                "accepted_route_count": 2,
                "checked_role_count": 2,
                "harness_count": 2,
                "failure_count": 0,
                "rejected_route_count": 0,
            },
        }
        report.update(overrides)
        path = tmp_path / name
        _write_json(path, report)
        return path

    missing = write_report("missing-harness-smoke.json", harnesses=[])
    partial = write_report("partial-route-smoke.json", accepted_routes=[lite_route])
    nonmatching_route = {**full_routes[1], "model": "gpt-5.5"}
    nonmatching = write_report("nonmatching-route-smoke.json", accepted_routes=[lite_route, nonmatching_route])

    assert pgb.find_reusable_route_verified_check(config, missing, tmp_path) is None
    assert pgb.find_reusable_route_verified_check(config, partial, tmp_path) is None
    assert pgb.find_reusable_route_verified_check(config, nonmatching, tmp_path) is None


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


def test_lint_work_item_cross_checks_blocks_unhashable_work_item_ids():
    captured: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        captured.append((file, severity, message))

    branch = {"id": "B01", "owned_paths": []}
    work_items = [
        {"id": ["bad"], "packet_id": "B01-1", "owned_paths": ["src/a.py"], "depends_on": ["A"]},
    ]
    lgb._lint_work_item_cross_checks(defect, branch, work_items, ["src/a.py"])

    assert any(item[1] == "critical" for item in captured), captured
    assert any("work_items[0]" in item[2] for item in captured), captured


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


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _minimal_goal_config_payload() -> dict:
    route_classes = {name: ["lite_agent"] for name in cgb.MANIFEST_WORKER_ROUTE_CLASSES}
    worker_policy = {
        "default_ladder": ["lite_agent"],
        "allowed_routes": ["lite_agent"],
        "branch_may_select_worker_route": True,
        "selection_reason_required": True,
        "ordering_rule": "Use the first available accepted route.",
        "route_classes": route_classes,
    }
    return {
        "schema_version": 1,
        "profile": "stale-check-regression",
        "aggressiveness": {"max_active_branch_agents": 1, "max_active_worker_packets": 1},
        "validation": {"mode": "smoke"},
        "telemetry": {"raw_text": False},
        "models": {"lite_agent": {"harness": "codex"}},
        "harnesses": {"codex": {"type": "codex"}},
        "model_ladders": {
            "worker": ["lite_agent"],
            "review": ["lite_agent"],
            "amender": ["lite_agent"],
            "lite": ["lite_agent"],
        },
        "model_policies": {
            "worker_model_policy": worker_policy,
            "review_model_policy": {"default_ladder": ["lite_agent"]},
            "amender_model_policy": {"default_ladder": ["lite_agent"]},
            "lite_model_policy": {"default_ladder": ["lite_agent"]},
        },
    }


def _minimal_goal_config_brief() -> dict:
    return {
        "job_id": "stale-check-regression",
        "title": "Stale check regression",
        "goal": "Exercise direct goal config pairing.",
        "source_summary": "Regression fixture.",
        "max_active_branch_agents": 1,
        "parallelization_rationale": "Single branch regression.",
        "parallelization": {"serial_reasons": ["single branch regression"]},
        "required_evidence": ["pytest regression passes"],
        "final_dod": ["stale check is rejected"],
        "branches": [
            {
                "id": "B01",
                "branch_name": "stale-check-regression",
                "objective": "Add stale check regression.",
                "worktree_path": ".worktrees/stale-check-regression",
                "worker_serial_reasons": ["single worker regression"],
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Exercise the stale check guard.",
                        "owned_paths": ["tests/test_deep_review.py"],
                        "context_files": ["tests/test_deep_review.py"],
                        "verification": ["pytest tests/test_deep_review.py -q"],
                        "dod": ["regression passes"],
                    }
                ],
            }
        ],
    }


def _write_goal_config_pair(tmp_path: Path, *, config_sha256: str | None = None) -> tuple[Path, Path]:
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(
        json.dumps(_minimal_goal_config_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    check_path = tmp_path / "goal-config.check.json"
    check = {
        "status": "pass",
        "mode": "smoke",
        "failures": [],
        "config_path": config_path.as_posix(),
        "config_sha256": config_sha256 if config_sha256 is not None else cgb.sha256_file(config_path),
        "summary": {"route_verification_status": "routes_verified", "accepted_route_count": 1},
        "accepted_routes": [{"role": "lite_agent", "alias": "lite_agent"}],
    }
    check_path.write_text(json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path, check_path


def test_preflight_compatibility_summary_rejects_non_string_validation_mode_without_typeerror():
    config = _minimal_goal_config_payload()
    config["validation"]["mode"] = ["debug"]

    summary = cgb.preflight_compatibility_summary(
        config,
        {"status": "pass", "mode": "smoke", "failures": []},
    )

    assert summary["status"] == "failed"
    assert any("validation.mode" in defect and "must be a string" in defect for defect in summary["defects"])


def test_preflight_compatibility_summary_rejects_non_string_check_mode_without_typeerror():
    config = _minimal_goal_config_payload()
    config["validation"]["mode"] = "smoke"

    summary = cgb.preflight_compatibility_summary(config, {"status": "pass", "mode": [], "failures": []})

    assert summary["status"] == "failed"
    assert any("check mode" in defect for defect in summary["defects"])


def _copy_smoke_bundle(tmp_path: Path) -> Path:
    source_bundle = (
        REPO / "maintenance" / "reports" / "toyoptimization-20260531" / "bundles" / "toyoptimization-v044-smoke"
    )
    bundle = tmp_path / "bundle"
    shutil.copytree(source_bundle, bundle)
    return bundle


def _run_bundle_lint(bundle: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(REPO / "skills" / "goal-preflight" / "scripts" / "lint_goal_bundle.py"),
            "--bundle-dir",
            str(bundle),
            "--json",
            "--no-write",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _build_minimal_ready_bundle(
    base: Path, *, telemetry_policy=None, malformed_brief=True, include_repo_status: bool = True
) -> Path:
    manifest = {
        "goal_config_path": "goal.config.json",
        "goal_config_check_summary": {"status": "pass", "accepted_route_count": 1},
        "max_active_branch_agents": 1,
        "branches": [],
    }
    if include_repo_status:
        manifest["repo_status"] = {
            "repo_is_git": True,
            "repo_root": ".",
            "base_ref_status": "exists",
        }
    if telemetry_policy is not None:
        manifest["telemetry_policy"] = telemetry_policy

    _write_json(base / "job.manifest.json", manifest)
    _write_json(
        base / "preflight.brief.lint.json", {"schema_lint_status": [1]} if malformed_brief else {"status": "pass"}
    )
    _write_json(base / "preflight.lint.json", {"status": "pass", "defects": [], "defect_count": 0})
    _write_json(base / "repair-gate.json", {"status": "pass"})
    return base


@pytest.mark.parametrize(
    "field, expected_blocker",
    [
        ("branches", "manifest.branches must be an array"),
        ("waves", "manifest.waves must be an array"),
        ("preflight_warnings", "manifest.preflight_warnings must be an array"),
    ],
)
def test_render_readiness_json_blocks_present_null_collections(tmp_path, field, expected_blocker):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest[field] = None
    _write_json(bundle / "job.manifest.json", manifest)

    payload = json.loads(rgb.render_readiness_json(bundle))

    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert expected_blocker in payload["launch_blockers"]


def test_render_readiness_json_blocks_missing_required_manifest_sections(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _write_json(
        bundle / "job.manifest.json",
        {
            "branches": [],
            "max_active_branch_agents": 1,
            "repo_status": {
                "repo_is_git": True,
                "repo_root": str(REPO),
                "base_ref_status": "exists",
            },
        },
    )
    _write_json(bundle / "preflight.brief.lint.json", {"status": "pass", "defects": [], "defect_count": 0})
    _write_json(bundle / "preflight.lint.json", {"status": "pass", "defects": [], "defect_count": 0})
    _write_json(bundle / "repair-gate.json", {"status": "pass", "actions": []})
    (bundle / "goal-bootloader.md").write_text("Use $goal-main-orchestrator\n", encoding="utf-8")

    payload = json.loads(rgb.render_readiness_json(bundle))

    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert "manifest missing required key: waves" in payload["launch_blockers"]
    assert "manifest missing required key: worker_model_policy" in payload["launch_blockers"]
    assert "manifest missing required key: parallelization" in payload["launch_blockers"]


@pytest.mark.parametrize(
    "field, expected_message",
    [
        ("branches", "branches must be a JSON array"),
        ("waves", "waves must be a JSON array"),
        ("preflight_warnings", "preflight_warnings must be a JSON array"),
    ],
)
def test_lint_reports_present_null_collections(tmp_path, field, expected_message):
    bundle = _copy_smoke_bundle(tmp_path)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = None
    _write_json(manifest_path, manifest)

    result = lgb.lint(bundle)

    assert result["schema_lint_status"] == "failed"
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and expected_message in defect["message"]
        for defect in result["defects"]
    ), result["defects"]


def test_render_readiness_and_json_fail_closed_with_non_string_schema_status(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle)

    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text
    assert "brief lint malformed" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any("brief lint malformed" in str(item) for item in payload["launch_blockers"])


@pytest.mark.parametrize("schema_lint_status", [None, False, 0, ""])
def test_render_readiness_fail_closed_with_falsey_malformed_schema_status_and_stale_pass(tmp_path, schema_lint_status):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    _write_json(
        bundle / "preflight.brief.lint.json",
        {
            "schema_lint_status": schema_lint_status,
            "status": "pass",
            "defects": [],
            "defect_count": 0,
        },
    )

    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text
    assert "brief lint malformed" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any("brief lint malformed" in str(item) for item in payload["launch_blockers"])


def test_render_readiness_and_json_fail_closed_with_missing_repo_status(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, include_repo_status=False)

    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert any("repo_status" in str(item) for item in payload["launch_blockers"])


@pytest.mark.parametrize(
    "field, value, blocker_hint",
    [
        ("max_active_branch_agents", True, "manifest.max_active_branch_agents"),
        ("parallelization.max_branches_per_wave", True, "manifest.parallelization.max_branches_per_wave"),
        ("parallelization.max_waves", True, "manifest.parallelization.max_waves"),
    ],
)
def test_render_readiness_and_json_blocks_bool_branch_cap_fields(tmp_path, field, value, blocker_hint):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    if field == "max_active_branch_agents":
        manifest["max_active_branch_agents"] = value
    else:
        manifest.setdefault("parallelization", {})
        if field.endswith("max_branches_per_wave"):
            manifest["parallelization"]["max_branches_per_wave"] = value
        else:
            manifest["parallelization"]["max_waves"] = value
    (bundle / "job.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any(blocker_hint in str(item) for item in payload["launch_blockers"]), payload["launch_blockers"]


@pytest.mark.parametrize(
    "field, value, blocker_hint",
    [
        ("max_active_branch_agents", 0, "manifest.max_active_branch_agents"),
        ("max_active_branch_agents", 5, "manifest.max_active_branch_agents"),
        ("parallelization.max_branches_per_wave", 5, "manifest.parallelization.max_branches_per_wave"),
        ("parallelization.max_waves", 6, "manifest.parallelization.max_waves"),
        ("parallelization.max_waves", -1, "manifest.parallelization.max_waves"),
    ],
)
def test_render_readiness_and_json_blocks_out_of_contract_branch_caps(tmp_path, field, value, blocker_hint):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    if field == "max_active_branch_agents":
        manifest["max_active_branch_agents"] = value
    else:
        manifest.setdefault("parallelization", {})
        if field.endswith("max_branches_per_wave"):
            manifest["parallelization"]["max_branches_per_wave"] = value
        else:
            manifest["parallelization"]["max_waves"] = value
    (bundle / "job.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any(blocker_hint in str(item) for item in payload["launch_blockers"]), payload["launch_blockers"]


@pytest.mark.parametrize(
    "malformed_section, malformed_payload, blocker_hint",
    [
        ("telemetry_policy", {"mode": []}, "manifest.telemetry_policy.mode"),
        ("worker_model_policy", {"default_ladder": "lite_agent"}, "manifest.worker_model_policy.default_ladder"),
        (
            "parallelization",
            {"serial_reasons": "not-a-list", "max_branches_per_wave": []},
            "manifest.parallelization",
        ),
    ],
)
def test_render_readiness_and_json_fail_closed_with_malformed_manifest_sections(
    tmp_path, malformed_section, malformed_payload, blocker_hint
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    manifest = {
        "goal_config_path": "goal.config.json",
        "goal_config_check_summary": {"status": "pass", "accepted_route_count": 1},
        "max_active_branch_agents": 1,
        "branches": [],
        "repo_status": {
            "repo_is_git": True,
            "repo_root": ".",
            "base_ref_status": "exists",
        },
        malformed_section: malformed_payload,
    }
    _write_json(bundle / "job.manifest.json", manifest)
    _write_json(bundle / "preflight.brief.lint.json", {"status": "pass", "defects": [], "defect_count": 0})
    _write_json(bundle / "preflight.lint.json", {"status": "pass", "defects": [], "defect_count": 0})
    _write_json(bundle / "repair-gate.json", {"status": "pass"})

    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert any(blocker_hint in str(item) for item in payload["launch_blockers"]), payload["launch_blockers"]


@pytest.mark.parametrize(
    "telemetry_policy, blocker_hint",
    [
        ({"schema_version": 1, "mode": "bogus", "raw_text": False, "collect": []}, "telemetry_policy.mode"),
        ({"schema_version": 1, "mode": "standard", "raw_text": True, "collect": []}, "telemetry_policy.raw_text"),
        (
            {"schema_version": 1, "mode": "standard", "raw_text": False, "collect": ["unknown_metric"]},
            "telemetry_policy.collect",
        ),
    ],
)
def test_render_readiness_blocks_invalid_telemetry_policy_values_with_stale_pass_artifacts(
    tmp_path, telemetry_policy, blocker_hint
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, telemetry_policy=telemetry_policy, malformed_brief=False)

    payload = json.loads(rgb.render_readiness_json(bundle))

    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert payload["telemetry_mode"] == "standard"
    assert any(blocker_hint in str(item) for item in payload["launch_blockers"]), payload["launch_blockers"]


@pytest.mark.parametrize(
    "branches, waves, blocker_hint",
    [
        (
            [{"id": "B01", "depends_on": ["B99"], "wave": "W01"}],
            [{"id": "W01", "branches": ["B01"]}],
            "depends on unknown branch",
        ),
        (
            [{"id": "B01", "depends_on": ["B02"], "wave": "W01"}, {"id": "B02", "depends_on": [], "wave": "W02"}],
            [{"id": "W01", "branches": ["B01"]}, {"id": "W02", "branches": ["B02"]}],
            "prior branch ids",
        ),
        (
            [{"id": "B01", "depends_on": [], "wave": "W02"}, {"id": "B02", "depends_on": ["B01"], "wave": "W01"}],
            [{"id": "W01", "branches": ["B02"]}, {"id": "W02", "branches": ["B01"]}],
            "wave of its dependency",
        ),
    ],
)
def test_render_readiness_blocks_invalid_branch_dependencies_with_stale_pass_artifacts(
    tmp_path, branches, waves, blocker_hint
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["branches"] = branches
    manifest["waves"] = waves
    _write_json(bundle / "job.manifest.json", manifest)

    payload = json.loads(rgb.render_readiness_json(bundle))

    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any(blocker_hint in str(item) for item in payload["launch_blockers"]), payload["launch_blockers"]


def test_render_readiness_and_json_fail_closed_with_list_telemetry_policy(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, telemetry_policy=[], malformed_brief=False)

    # Pre-fix: AttributeError: 'list' object has no attribute 'get'
    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text
    assert "manifest.telemetry_policy must be an object" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert "manifest.telemetry_policy must be an object" in payload["launch_blockers"]


def test_render_readiness_and_json_fail_closed_with_string_worker_model_policy(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["telemetry_policy"] = {"mode": "standard"}
    manifest["worker_model_policy"] = "bad"
    manifest["parallelization"] = {}
    _write_json(bundle / "job.manifest.json", manifest)

    # Pre-fix: AttributeError: 'str' object has no attribute 'get'
    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text
    assert "manifest.worker_model_policy must be an object" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert "manifest.worker_model_policy must be an object" in payload["launch_blockers"]


def test_render_readiness_and_json_fail_closed_with_string_parallelization(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "telemetry_policy": {"mode": "standard"},
            "worker_model_policy": {},
            "amender_model_policy": {},
            "lite_model_policy": {},
            "review_model_policy": {},
            "parallelization": "bad",
        }
    )
    _write_json(bundle / "job.manifest.json", manifest)

    # Pre-fix: AttributeError: 'str' object has no attribute 'get'
    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text
    assert "manifest.parallelization must be an object" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert "manifest.parallelization must be an object" in payload["launch_blockers"]


def test_render_readiness_and_json_fail_closed_with_scalar_branch_depends_on(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["branches"] = [{"id": "B01", "wave": "W01", "depends_on": 7}]
    manifest["waves"] = [{"id": "W01", "branches": ["B01"], "dependency_level": 1}]
    _write_json(bundle / "job.manifest.json", manifest)

    # Pre-fix: TypeError: 'int' object is not iterable
    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any("depends_on" in blocker for blocker in payload["launch_blockers"]), payload["launch_blockers"]


@pytest.mark.parametrize(
    "repo_status",
    [
        {"repo_is_git": "yes", "base_ref_status": []},
        {"repo_is_git": True, "base_ref_status": []},
    ],
)
def test_repo_runtime_gate_blocks_non_shape_repo_status(repo_status):
    manifest = {
        "job_id": "job",
        "goal": "go",
        "title": "t",
        "source_summary": "s",
        "required_evidence": [],
        "final_dod": [],
        "main_prompt": "main.prompt.md",
        "runtime_rules_path": "runtime-rules.md",
        "runtime_rules_sha256": "x",
        "runtime_index_path": "runtime.index.json",
        "runtime_index_sha256": "x",
        "base_ref": "main",
        "artifact_policy": {},
        "cleanup_policy": {},
        "branches": [{"id": "B01", "branch_name": "B01", "worktree_path": "branches/B01"}],
        "waves": [{"id": "W01", "branches": ["B01"]}],
        "max_active_branch_agents": 1,
        "parallelization": {},
        "adaptation_policy": "goal_preflight",
        "worker_model_policy": {},
        "amender_model_policy": {},
        "lite_model_policy": {},
        "lite_advisor_policy": {},
        "review_model_policy": {},
        "research_worker_policy": {},
        "route_contract": {},
        "route_contract_sha256": "x",
        "execution_strategy": {},
        "ownership_feasibility": {"status": "needs_review"},
        "orchestration_watchdog": {},
        "preflight_lite_advice": [],
        "preflight_input_precedence": {},
        "repo_status": repo_status,
    }
    gate = rgb._repo_runtime_gate(manifest)
    assert gate["status"] == "blocked"
    assert gate["reason"].startswith("manifest.repo_status")


def test_lint_and_readiness_block_invalid_base_ref_status(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["repo_status"]["base_ref_status"] = "present"
    _write_json(bundle / "job.manifest.json", manifest)

    linted = lgb.lint(bundle)
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and "manifest.repo_status.base_ref_status" in defect["message"]
        for defect in linted["defects"]
    )

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any("manifest.repo_status.base_ref_status" in str(item) for item in payload["launch_blockers"]), payload[
        "launch_blockers"
    ]


def test_render_readiness_blocks_empty_repo_status(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["repo_status"] = {}
    (bundle / "job.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    text = rgb.render_readiness(bundle)
    assert "status=blocked" in text

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any("manifest.repo_status.repo_is_git" in str(item) for item in payload["launch_blockers"])


def test_render_readiness_blocks_wave_overrun_with_stale_lint_pass_artifacts(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _build_minimal_ready_bundle(bundle, malformed_brief=False)
    manifest = json.loads((bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["max_active_branch_agents"] = 1
    manifest["branches"] = [
        {
            "id": "B01",
            "depends_on": [],
            "prompt": "branches/B01.prompt.md",
            "status_path": "branches/B01.status.json",
            "review_path": "branches/B01.review.json",
            "pre_review_gate_path": "branches/B01.pre-review-gate.md",
        },
        {
            "id": "B02",
            "depends_on": [],
            "prompt": "branches/B02.prompt.md",
            "status_path": "branches/B02.status.json",
            "review_path": "branches/B02.review.json",
            "pre_review_gate_path": "branches/B02.pre-review-gate.md",
        },
    ]
    manifest["waves"] = [{"id": "W01", "branches": ["B01", "B02"]}]
    _write_json(bundle / "job.manifest.json", manifest)

    payload = json.loads(rgb.render_readiness_json(bundle))
    assert payload["status"] == "blocked"
    assert payload["launch_allowed"] is False
    assert any(
        "manifest wave" in str(item) and "max_active_branch_agents" in str(item) for item in payload["launch_blockers"]
    ), payload["launch_blockers"]


def test_lint_manifest_reports_invalid_repo_status_types(tmp_path):
    source_bundle = (
        REPO / "maintenance" / "reports" / "toyoptimization-20260531" / "bundles" / "toyoptimization-v044-smoke"
    )
    bundle = tmp_path / "bundle"
    shutil.copytree(source_bundle, bundle)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["repo_status"] = {"repo_is_git": "yes", "base_ref_status": []}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = lgb.lint(bundle)
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and ("repo_status.repo_is_git" in defect["message"] or "repo_status.base_ref_status" in defect["message"])
        for defect in result["defects"]
    )


def test_lint_manifest_missing_repo_status_reports_critical_defect(tmp_path):
    source_bundle = (
        REPO / "maintenance" / "reports" / "toyoptimization-20260531" / "bundles" / "toyoptimization-v044-smoke"
    )
    bundle = tmp_path / "bundle"
    shutil.copytree(source_bundle, bundle)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("repo_status", None)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = lgb.lint(bundle)
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and "repo_status" in defect["message"]
        for defect in result["defects"]
    )


def test_lint_manifest_reports_missing_repo_status_fields(tmp_path):
    source_bundle = (
        REPO / "maintenance" / "reports" / "toyoptimization-20260531" / "bundles" / "toyoptimization-v044-smoke"
    )
    bundle = tmp_path / "bundle"
    shutil.copytree(source_bundle, bundle)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["repo_status"] = {}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = lgb.lint(bundle)
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and "repo_status.repo_is_git" in defect["message"]
        for defect in result["defects"]
    )
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and "repo_status.base_ref_status" in defect["message"]
        for defect in result["defects"]
    )


def test_prepare_preparedness_fixtures_guards_non_string_action_kinds():
    with pytest.raises(SystemExit, match="action kind must be a string"):
        cpf._ensure_no_blocking_amender_action({"actions": [{"kind": {"invalid": "kind"}}]})


def test_prepare_preparedness_fixtures_rejects_non_list_actions_payload():
    with pytest.raises(SystemExit, match="actions must be a list"):
        cpf._ensure_no_blocking_amender_action({"actions": {"kind": "amendment_eligibility"}})


def test_prepare_preparedness_fixtures_guards_non_string_recovered_branch_id():
    with pytest.raises(SystemExit, match="branch report branch_id must be a string"):
        cpf._require_blocked_recovered_branch_id(
            {"branch_id": {"id": "B01"}, "runtime_status": "blocked"},
            {"B01"},
        )


def test_collect_string_values_filters_non_string_payload_values():
    payload = [
        {"severity": "warning"},
        {"severity": ["warning"]},  # malformed list should be ignored
        {"severity": {"x": "warning"}},  # malformed dict should be ignored
        5,
    ]
    assert cpf._collect_string_values(payload, "severity") == {"warning"}


def test_collect_dict_items_by_string_field_filters_invalid_keys():
    payload = [
        {"path": "readiness.json"},
        {"path": ["readiness.json"]},
        {"path": {"value": "readiness.json"}},
        {"path": 5},
        123,
    ]
    assert set(cpf._collect_dict_items_by_string_field(payload, "path")) == {"readiness.json"}


def test_lint_work_item_cross_checks_rejects_non_string_depends_on_member(tmp_path):
    captured: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        captured.append((file, severity, message))

    branch = {"id": "B01", "owned_paths": ["src/a.py"]}
    work_items = [
        {
            "id": "W01",
            "packet_id": "B01-W01",
            "depends_on": [{"bad": 1}],
            "owned_paths": ["src/a.py"],
        }
    ]
    lgb._lint_work_item_cross_checks(defect, branch, work_items, ["src/a.py"])

    assert any("depends_on element must be a string" in message for _, _, message in captured)


def test_lint_tolerates_unhashable_work_item_depends_on_in_bundle_lint(tmp_path):
    source_bundle = (
        REPO / "maintenance" / "reports" / "toyoptimization-20260531" / "bundles" / "toyoptimization-v044-smoke"
    )
    bundle = tmp_path / "bundle"
    shutil.copytree(source_bundle, bundle)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["branches"][0]["work_items"][0]["depends_on"] = [{"bad": 1}]
    _write_json(manifest_path, manifest)

    result = lgb.lint(bundle)
    assert any("depends_on" in item["message"] and "must be a string" in item["message"] for item in result["defects"])


def test_check_preparedness_fixtures_rejects_non_string_wave_branch_id():
    serial_manifest = {"waves": [{"id": "W01", "branches": [{"bad": 1}]}]}
    with pytest.raises(SystemExit, match="branches must contain string branch ids"):
        cpf._collect_serial_wave_by_branch(serial_manifest)


# --- pass22: telemetry_policy.schema_version must reject bool consistently with readiness ---
def test_normalize_telemetry_policy_rejects_bool_schema_version():
    with pytest.raises(SystemExit, match="telemetry_policy.schema_version must be 1"):
        cgb.normalize_telemetry_policy({"schema_version": True, "mode": "standard", "raw_text": False, "collect": []})


def test_direct_create_rejects_stale_goal_config_check_sha(tmp_path):
    config_path, check_path = _write_goal_config_pair(tmp_path, config_sha256="0" * 64)

    with pytest.raises(SystemExit, match="goal config check config_sha256 does not match goal config"):
        cgb.create_bundle(
            _minimal_goal_config_brief(),
            REPO,
            tmp_path / "bundle",
            goal_config_inputs=cgb.GoalConfigInputs(
                config=cgb.load_goal_config(config_path),
                check=cgb.load_goal_config_check(check_path),
                config_source=config_path,
                check_source=check_path,
            ),
        )


def test_direct_create_rejects_goal_config_check_without_sha(tmp_path):
    config_path, check_path = _write_goal_config_pair(tmp_path)
    check = json.loads(check_path.read_text(encoding="utf-8"))
    check.pop("config_sha256")
    _write_json(check_path, check)

    with pytest.raises(SystemExit, match="goal config check config_sha256 must be a non-empty string"):
        cgb.create_bundle(
            _minimal_goal_config_brief(),
            REPO,
            tmp_path / "bundle",
            goal_config_inputs=cgb.GoalConfigInputs(
                config=cgb.load_goal_config(config_path),
                check=cgb.load_goal_config_check(check_path),
                config_source=config_path,
                check_source=check_path,
            ),
        )


def test_direct_create_accepts_matching_goal_config_check_sha(tmp_path):
    config_path, check_path = _write_goal_config_pair(tmp_path)

    bundle = cgb.create_bundle(
        _minimal_goal_config_brief(),
        REPO,
        tmp_path / "bundle",
        goal_config_inputs=cgb.GoalConfigInputs(
            config=cgb.load_goal_config(config_path),
            check=cgb.load_goal_config_check(check_path),
            config_source=config_path,
            check_source=check_path,
        ),
    )

    assert (bundle / "goal.config.json").is_file()
    assert (bundle / "goal-config.check.json").is_file()


def test_lint_rejects_copied_stale_goal_config_check_sha_pair(tmp_path):
    config_path, check_path = _write_goal_config_pair(tmp_path)
    bundle = cgb.create_bundle(
        _minimal_goal_config_brief(),
        REPO,
        tmp_path / "bundle",
        goal_config_inputs=cgb.GoalConfigInputs(
            config=cgb.load_goal_config(config_path),
            check=cgb.load_goal_config_check(check_path),
            config_source=config_path,
            check_source=check_path,
        ),
    )
    copied_check = json.loads((bundle / "goal-config.check.json").read_text(encoding="utf-8"))
    copied_check["config_sha256"] = "0" * 64
    _write_json(bundle / "goal-config.check.json", copied_check)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fresh_check_hash = lgb.sha256_file(bundle / "goal-config.check.json")
    manifest["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract"]["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract_sha256"] = lgb.sha256_json(manifest["route_contract"])
    _write_json(manifest_path, manifest)

    result = lgb.lint(bundle)

    assert any(
        defect["file"] == "goal-config.check.json"
        and defect["severity"] == "critical"
        and "config_sha256 does not match goal.config.json" in defect["message"]
        for defect in result["defects"]
    ), result["defects"]


@pytest.mark.parametrize("check_config_sha", [42, ""])
def test_lint_rejects_copied_malformed_goal_config_check_sha_pair(tmp_path, check_config_sha):
    config_path, check_path = _write_goal_config_pair(tmp_path)
    bundle = cgb.create_bundle(
        _minimal_goal_config_brief(),
        REPO,
        tmp_path / "bundle",
        goal_config_inputs=cgb.GoalConfigInputs(
            config=cgb.load_goal_config(config_path),
            check=cgb.load_goal_config_check(check_path),
            config_source=config_path,
            check_source=check_path,
        ),
    )
    copied_check = json.loads((bundle / "goal-config.check.json").read_text(encoding="utf-8"))
    copied_check["config_sha256"] = check_config_sha
    _write_json(bundle / "goal-config.check.json", copied_check)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fresh_check_hash = lgb.sha256_file(bundle / "goal-config.check.json")
    manifest["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract"]["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract_sha256"] = lgb.sha256_json(manifest["route_contract"])
    _write_json(manifest_path, manifest)

    result = lgb.lint(bundle)

    assert any(
        defect["file"] == "goal-config.check.json"
        and defect["severity"] == "critical"
        and "config_sha256 must be a non-empty string" in defect["message"]
        for defect in result["defects"]
    ), result["defects"]


def test_lint_rejects_copied_absent_goal_config_check_sha_pair(tmp_path):
    config_path, check_path = _write_goal_config_pair(tmp_path)
    bundle = cgb.create_bundle(
        _minimal_goal_config_brief(),
        REPO,
        tmp_path / "bundle",
        goal_config_inputs=cgb.GoalConfigInputs(
            config=cgb.load_goal_config(config_path),
            check=cgb.load_goal_config_check(check_path),
            config_source=config_path,
            check_source=check_path,
        ),
    )
    copied_check = json.loads((bundle / "goal-config.check.json").read_text(encoding="utf-8"))
    copied_check.pop("config_sha256")
    _write_json(bundle / "goal-config.check.json", copied_check)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fresh_check_hash = lgb.sha256_file(bundle / "goal-config.check.json")
    manifest["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract"]["goal_config_check_sha256"] = fresh_check_hash
    manifest["route_contract_sha256"] = lgb.sha256_json(manifest["route_contract"])
    _write_json(manifest_path, manifest)

    result = lgb.lint(bundle)

    assert any(
        defect["file"] == "goal-config.check.json"
        and defect["severity"] == "critical"
        and "config_sha256 must be a non-empty string" in defect["message"]
        for defect in result["defects"]
    ), result["defects"]


def test_cli_lint_rejects_bool_telemetry_policy_schema_version_without_traceback(tmp_path):
    bundle = _copy_smoke_bundle(tmp_path)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["telemetry_policy"] = {
        "schema_version": True,
        "mode": "standard",
        "raw_text": False,
        "collect": [],
    }
    _write_json(manifest_path, manifest)

    completed = _run_bundle_lint(bundle)
    payload = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert payload["schema_lint_status"] == "failed"
    assert not completed.stderr
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and defect["message"] == "telemetry_policy.schema_version must be 1"
        for defect in payload["defects"]
    )


def test_cli_lint_rejects_non_string_goal_config_model_ladder_entry_without_traceback(tmp_path):
    config_source, check_source = _write_goal_config_pair(tmp_path)
    bundle = cgb.create_bundle(
        _minimal_goal_config_brief(),
        REPO,
        tmp_path / "bundle",
        goal_config_inputs=cgb.GoalConfigInputs(
            config=cgb.load_goal_config(config_source),
            check=cgb.load_goal_config_check(check_source),
            config_source=config_source,
            check_source=check_source,
        ),
    )
    config_path = bundle / "goal.config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["model_ladders"]["worker"] = [[]]
    _write_json(config_path, config)

    completed = _run_bundle_lint(bundle)
    payload = json.loads(completed.stdout)

    assert completed.returncode == 1
    assert payload["schema_lint_status"] == "failed"
    assert not completed.stderr
    assert any(
        defect["file"] == "job.manifest.json"
        and defect["severity"] == "critical"
        and defect["message"] == "goal_config.model_ladders.worker entries must be strings"
        for defect in payload["defects"]
    )


# --- 2026-06-18 convergence pass 17: _lint_source_attachments fails closed on a non-int `bytes`
#     (was `attachment.get("bytes", 0) < 8192` -> TypeError comparing str/list with int). ---
def test_lint_source_attachments_tolerates_non_int_bytes():
    captured: list[tuple] = []

    def defect(*args):
        captured.append(args)

    manifest = {
        "source_attachments": [
            {"label": "x", "path": "src/a.py", "promoted_from_context_files": True, "bytes": "huge"},
        ]
    }
    lgb._lint_source_attachments(defect, manifest)  # must not raise on non-int bytes
    assert any("large-source threshold" in a[2] for a in captured), captured


def test_cli_lint_reports_unhashable_branch_id_without_typeerror(tmp_path):
    bundle = _copy_smoke_bundle(tmp_path)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["branches"][0]["id"] = {"bad": 1}
    _write_json(manifest_path, manifest)

    completed = _run_bundle_lint(bundle)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["schema_lint_status"] == "failed"
    assert not completed.stderr
    assert any("branch id is not safe" in defect["message"] for defect in payload["defects"])


def test_cli_lint_reports_unhashable_branch_depends_on_without_typeerror(tmp_path):
    bundle = _copy_smoke_bundle(tmp_path)
    manifest_path = bundle / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["branches"][0]["depends_on"] = [{"bad": 1}]
    _write_json(manifest_path, manifest)

    completed = _run_bundle_lint(bundle)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert payload["schema_lint_status"] == "failed"
    assert not completed.stderr
    assert any("depends_on[0] is not a safe branch id" in defect["message"] for defect in payload["defects"])


# --- 2026-06-18 fresh-audit pass ---


# D1: create must not auto-inject an untracked package __init__.py into context_files, because the
#     bundle linter rejects any untracked context_files entry as a `major` defect (an un-launchable
#     bundle the user never authored). In a git repo, only git-tracked skeletons are injected.
def test_skeleton_injection_skips_untracked_init(tmp_path):
    import subprocess

    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "sub" / "__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "pkg/sub/mod.py"], check=True)  # __init__.py untracked

    items = [{"owned_paths": ["pkg/sub/mod.py"], "context_files": []}]
    cgb.assign_package_skeleton_context_files(items, repo_root=tmp_path)
    assert items[0]["context_files"] == [], "untracked __init__.py skeleton must not be injected"

    subprocess.run(["git", "-C", str(tmp_path), "add", "pkg/__init__.py", "pkg/sub/__init__.py"], check=True)
    items2 = [{"owned_paths": ["pkg/sub/mod.py"], "context_files": []}]
    cgb.assign_package_skeleton_context_files(items2, repo_root=tmp_path)
    assert "pkg/__init__.py" in items2[0]["context_files"], "tracked skeleton should still be injected"
    assert "pkg/sub/__init__.py" in items2[0]["context_files"]


# D2: supplied waves are checked for dependency-vs-wave ordering and get a dependency_level stamp
#     (previously the supplied-waves path diverged from the auto dependency_waves path).
def test_resolve_waves_rejects_inverted_supplied_ordering():
    branches = [{"id": "B01", "depends_on": []}, {"id": "B02", "depends_on": ["B01"]}]
    inverted = {"waves": [{"id": "w1", "branches": ["B02"]}, {"id": "w2", "branches": ["B01"]}]}
    with pytest.raises(SystemExit):  # B02 depends on B01 but is scheduled in an earlier wave
        cgb._resolve_waves(inverted, branches, 4)


def test_normalize_brief_rejects_inverted_supplied_wave_order_after_dependency_canonicalization():
    branches = [{"id": "B01", "depends_on": []}, {"id": "B02", "depends_on": ["b01"]}]
    with pytest.raises(SystemExit):
        cgb.normalize_brief(
            {
                "job_id": "toy",
                "branches": branches,
                "waves": [{"id": "w1", "branches": ["B02"]}, {"id": "w2", "branches": ["B01"]}],
            },
            validate_base_ref=False,
        )


def test_resolve_waves_stamps_dependency_level_on_supplied_waves():
    branches = [{"id": "B01", "depends_on": []}, {"id": "B02", "depends_on": ["B01"]}]
    correct = {"waves": [{"id": "w1", "branches": ["B01"]}, {"id": "w2", "branches": ["B02"]}]}
    waves, _ = cgb._resolve_waves(correct, branches, 4)
    by_id = {wave["id"]: wave for wave in waves}
    assert by_id["w1"]["dependency_level"] == 1
    assert by_id["w2"]["dependency_level"] == 2


def test_lint_waves_rejects_inverted_supplied_ordering_for_case_mismatch_depends_on():
    defects: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append((file, severity, message))

    manifest = {"waves": [{"id": "w1", "branches": ["B02"]}, {"id": "w2", "branches": ["B01"]}]}
    branches = [{"id": "B01", "depends_on": []}, {"id": "B02", "depends_on": ["b01"]}]
    ids = lgb._lint_branch_identity(defect, branches)
    lgb._lint_waves(defect, manifest, branches, ids, manifest.get("max_active_branch_agents", 4), True)
    assert any("must come after the wave of its dependency" in msg for _, _, msg in defects), defects


def test_lint_waves_rejects_case_mismatched_branch_wave_declaration():
    defects: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append((file, severity, message))

    manifest = {"waves": [{"id": "W01", "branches": ["B01"]}]}
    branches = [
        {
            "id": "B01",
            "branch_name": "branch-b01",
            "worktree_path": "branches/B01.worktree",
            "wave": "w01",
            "depends_on": [],
        }
    ]
    ids = lgb._lint_branch_identity(defect, branches)
    lgb._lint_waves(defect, manifest, branches, ids, manifest.get("max_active_branch_agents", 4), True)

    assert any(
        file == "job.manifest.json"
        and severity == "critical"
        and message == "branch 'B01' declares wave 'w01' but is listed under wave 'W01'"
        for file, severity, message in defects
    ), defects


def test_lint_rejects_wave_over_manifest_branch_cap():
    defects: list[tuple[str, str, str]] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append((file, severity, message))

    manifest = {
        "max_active_branch_agents": 1,
        "waves": [{"id": "W01", "branches": ["B01", "B02"]}],
        "branches": [
            {"id": "B01", "depends_on": []},
            {"id": "B02", "depends_on": []},
        ],
    }
    lgb._lint_waves(
        defect,
        manifest,
        manifest["branches"],
        ["B01", "B02"],
        manifest["max_active_branch_agents"],
        True,
    )
    assert any(
        item[0] == "job.manifest.json" and item[1] == "critical" and "more than 1 branches" in item[2]
        for item in defects
    ), defects


# D3: per-work-item validation (path-safety etc.) runs even when the work_items count is out of range,
#     so a malformed bundle gets the complete defect set instead of only the count defect.
def test_lint_branches_validates_items_even_when_count_out_of_range(tmp_path):
    captured: list[str] = []

    def defect(_file, _sev, msg):
        captured.append(msg)

    branch = {
        "id": "B01",
        "max_active_worker_packets": 1,
        "owned_paths": ["src/a.py"],
        "work_items": [
            {
                "id": f"W0{i}",
                "packet_id": f"B01-W0{i}",
                "objective": "do the thing",
                "owned_paths": ["../escape.py"] if i == 0 else ["src/a.py"],
                "verification": ["pytest"],
                "dod": ["done"],
            }
            for i in range(5)  # 5 > 4: count out of range
        ],
    }
    lgb._lint_branches(defect, tmp_path, {"branches": [branch]}, [branch], ["B01"], set(), {"repo_is_git": False}, None)
    assert any("1 to 4 worker packets" in m for m in captured), "count defect missing"
    assert any(
        "escape.py" in m or "traversal" in m or "relative" in m.lower() for m in captured
    ), "per-item path-safety defect missing -> D3 regressed (validation skipped on out-of-range count)"


# D4: a brief whose normalization fails (e.g. non-list depends_on) must not also emit the spurious
#     "must supply artifact_policy/cleanup_policy" major defects (the tool would have defaulted them).
def test_lint_brief_no_spurious_policy_defects_when_normalization_fails():
    brief = {
        "goal": "x",
        "branches": [{"id": "B01", "objective": "o", "owned_paths": ["src/a.py"], "depends_on": "W00"}],
    }
    defects = lpb.lint_brief(brief, repo_root=None)
    paths = [d.get("path") for d in defects]
    assert "$.artifact_policy" not in paths, f"spurious artifact_policy defect: {defects}"
    assert "$.cleanup_policy" not in paths, f"spurious cleanup_policy defect: {defects}"
    assert any(d.get("severity") == "critical" for d in defects), "the real normalization defect must remain"
