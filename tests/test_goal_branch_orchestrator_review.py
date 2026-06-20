"""Regression tests for the 2026-06-18 deep-review fixes (goal-branch-orchestrator).

Pins the verified defects:
- validate_branch_status.main() fails closed on malformed status/manifest (the goal-main bug class);
- a manifest branch entry missing review_path is now a defect (was a silent review-evidence bypass);
- assemble_branch_status degrades malformed semi-trusted artifacts to blockers, never aborts;
- create_runtime_packet.load_json fails closed; dead telemetry_function removed;
- promote_worker_repair_evidence tolerates non-list evidence fields;
- the pre-review-gate reviewer-reuse allowlist includes the bridge routes.
"""

import json
import argparse
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO, load_module

vbs = load_module("skills/goal-branch-orchestrator/scripts/validate_branch_status.py", "vbs_review")
asm = load_module("skills/goal-branch-orchestrator/scripts/assemble_branch_status.py", "asm_review")
crp = load_module("skills/goal-branch-orchestrator/scripts/create_runtime_packet.py", "crp_review")
prw = load_module("skills/goal-branch-orchestrator/scripts/promote_worker_repair_evidence.py", "prw_review")
rpr = load_module("skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py", "rpr_review")
cprg = load_module("skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py", "cprg_review")


# --- 2026-06-18 convergence pass 6: pre-review-gate nested-field iterations over semi-trusted
#     branch-status artifacts tolerate non-list values instead of TypeError ---
def test_ownership_check_tolerates_non_list_fields():
    assert isinstance(cprg.ownership_check({"owned_paths": None}, {"changed_files": 5}), dict)  # was TypeError


def test_worker_pass_defects_tolerates_non_list_finished_ids(tmp_path):
    branch_status = {
        "worker_statuses": [],
        "worker_parallelism": {"active_ids": [], "blocked_ids": [], "deferred_ids": [], "finished_ids": None},
    }
    assert isinstance(cprg.worker_pass_defects(tmp_path, {"work_items": []}, branch_status, "B01"), list)


def test_runtime_packet_model_facing_path_schemas_avoid_advanced_regex():
    status_schema = crp.status_schema(
        "B01-W01",
        "feature-branch",
        "/tmp/worktree",
        selected_ladder=["worker_primary"],
        branch_id="B01",
        work_item_id="W01",
        manifest_hash="sha256:" + "0" * 64,
        route_id="B01-W01:normal-code:worker_primary",
    )
    research_schema = crp.research_schema("B01-RW01", "feature-branch", "/tmp/worktree")

    changed_files_item = status_schema["properties"]["changed_files"]["items"]
    local_files_item = research_schema["properties"]["local_files_read"]["items"]

    assert changed_files_item == {"type": "string", "minLength": 1}
    assert local_files_item == {"type": "string", "minLength": 1}


def test_packet_terminal_defects_degrades_malformed_launcher(tmp_path):
    # the read_json SystemExit is now caught at the call site -> conservative defect, not a crash
    pdir = tmp_path / "workers" / "P01"
    pdir.mkdir(parents=True)
    (pdir / "launcher-state.json").write_text("{ not json", encoding="utf-8")
    defects = cprg.packet_terminal_defects(tmp_path, {"work_items": []}, "B01", "P01", {"status": "pass"})
    assert any("not a readable JSON object" in d for d in defects), defects


def test_reviewer_branch_status_context_tolerates_non_list_blockers(tmp_path):
    (tmp_path / "B01.status.json").write_text(json.dumps({"status": "pass", "blockers": 5}), encoding="utf-8")
    ctx = crp.reviewer_branch_status_context(tmp_path, {"status_path": "B01.status.json"}, {"status": "pass"})
    assert isinstance(ctx, dict)  # was TypeError on the non-list blockers comprehension


# --- 2026-06-18 convergence pass 3: create_pre_review_gate.read_json fails closed on a non-UTF-8
#     worker artifact (launcher-state/packet.summary are worker-produced) ---
def test_create_pre_review_gate_read_json_fails_closed_on_non_utf8(tmp_path):
    nonutf8 = tmp_path / "launcher-state.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        cprg.read_json(nonutf8)


# --- 2026-06-18 convergence pass 8: the porcelain-prefix rejection in changed_files paths is
#     exercised (the contract MUST was enforced only by the gate-dark reject_porcelain branch) ---
def test_validate_path_list_rejects_porcelain_prefix():
    defects: list[str] = []
    vbs.validate_path_list(defects, [" M src/foo.py"], "$.changed_files")
    assert any("porcelain" in d.lower() for d in defects), defects
    clean: list[str] = []
    vbs.validate_path_list(clean, ["src/foo.py"], "$.changed_files")
    assert clean == []


def test_attempt_failure_subclass_normalizes_negative_transport_disconnect_count():
    subclass = rpr.attempt_failure_subclass({}, {"transport_disconnect_count": -3}, "fail-clean")
    assert subclass is None
    normalized = rpr._normalize_route_health({"transport_disconnect_count": -3})
    assert normalized["transport_disconnect_count"] == 0


# --- 2026-06-18 convergence pass 10: the git-mutation security gates are not evadable by global
#     git options (git -C dir / git -c k=v) inserted between `git` and the subcommand ---
def test_worker_command_gate_not_evaded_by_git_global_options():
    for cmd in ("git -C /repo commit -m x", "git -c user.name=x commit -m y", "git -C /other push origin HEAD"):
        defects: list[str] = []
        vbs.validate_worker_command_evidence(defects, [cmd], "$.cr")
        assert any("mutating command" in d for d in defects), cmd
    clean: list[str] = []
    vbs.validate_worker_command_evidence(clean, ["git -C /repo status"], "$.cr")  # read-only stays clean
    assert clean == []


def test_research_command_gate_not_evaded_and_secret_path_still_caught():
    defects: list[str] = []
    vbs.validate_research_security(defects, ["git -C /repo commit -m x"], [], "$.research")
    assert any("read-only security policy" in d for d in defects), defects
    # stripping git options for command matching must NOT lose a secret marker inside the command
    secret: list[str] = []
    vbs.validate_research_security(secret, ["cat /home/u/.ssh/id_rsa"], [], "$.research")
    assert any("secret or credential" in d for d in secret), secret


# --- 2026-06-18 convergence pass 11: the git-mutation gate also resists no-argument global flags
#     (--bare/--no-optional-locks/--literal-pathspecs), not just the enumerated arg-taking ones ---
def test_worker_command_gate_not_evaded_by_no_arg_git_global_flags():
    for cmd in ("git --no-optional-locks commit -m x", "git --bare push", "git --literal-pathspecs reset --hard"):
        defects: list[str] = []
        vbs.validate_worker_command_evidence(defects, [cmd], "$.cr")
        assert any("mutating command" in d for d in defects), cmd


# --- 2026-06-18 convergence pass 11: configured_route_commands / configured_telemetry_attempts
#     guard an unhashable (list/dict) goal_config model `harness` value (was TypeError on dict.get) ---
def test_configured_route_commands_tolerates_unhashable_harness():
    cmds = crp.configured_route_commands(
        ["ds-pro-max"], {"models": {"ds-pro-max": {"harness": ["x"], "model": "m"}}, "harnesses": {}}
    )
    assert isinstance(cmds, list)  # was TypeError: unhashable type


def test_configured_route_commands_tolerates_non_dict_harness_entry():
    cmds = crp.configured_route_commands(
        ["ds-pro-max"],
        {"models": {"ds-pro-max": {"harness": "bad-h"}}, "harnesses": {"bad-h": []}},
    )
    assert cmds == ["bad-h"]


def test_configured_telemetry_attempts_rejects_unhashable_harness():
    with pytest.raises(SystemExit):  # references unknown harness, NOT TypeError on dict.get
        crp.worker_telemetry_attempts(["codex-spark"], {"models": {"codex-spark": {"harness": ["x"]}}, "harnesses": {}})


# --- 2026-06-18 convergence pass 10: configured_route_commands guards a non-dict per-alias model
#     value (sibling configured_telemetry_attempts already did) ---
def test_configured_route_commands_tolerates_non_dict_model():
    cmds = crp.configured_route_commands(["ds-pro-max"], {"models": {"ds-pro-max": "not-a-dict"}, "harnesses": {}})
    assert isinstance(cmds, list)  # was AttributeError on model.get(...)


# --- 2026-06-18 convergence pass 9: load_task tolerates a non-UTF-8 --task-file (errors="replace",
#     matching the validator side) instead of crashing packet creation ---
def test_load_task_tolerates_non_utf8(tmp_path):
    f = tmp_path / "task.md"
    f.write_bytes(b"\xff\xfe task body")
    assert isinstance(crp.load_task(f), str)  # was UnicodeDecodeError


# --- 2026-06-18 convergence pass 7: configured_telemetry_attempts guards a non-dict
#     goal_config.models/harnesses (.get on a non-dict used to AttributeError) ---
def test_worker_telemetry_attempts_tolerates_non_dict_goal_config():
    with pytest.raises(SystemExit):  # missing-role SystemExit, NOT AttributeError on 5.get(...)
        crp.worker_telemetry_attempts(["codex-spark"], {"models": 5, "harnesses": []})


# --- 2026-06-18 convergence pass 7: the research-worker read-only command-policy security branch
#     is exercised (only the secret-marker sibling had coverage before) ---
def test_validate_research_security_flags_forbidden_commands():
    defects: list[str] = []
    vbs.validate_research_security(defects, ["git push origin HEAD"], [], "$.research")
    assert any("read-only security policy" in d for d in defects), defects
    clean: list[str] = []
    vbs.validate_research_security(clean, ["rg foo src/", "cat README.md"], [], "$.research")
    assert clean == []


# --- 2026-06-18 convergence pass 5: the base_ref command-injection gate is exercised (the 3rd
#     sibling security gate; reject branches were gate-dark). Both byte-copies are tested. ---
def test_validate_base_ref_rejects_injection():
    for mod in (cprg, asm):
        assert mod.validate_base_ref("main") == "main"
        assert mod.validate_base_ref(" release/1.2 ") == "release/1.2"
        for bad in ("-rf", "a..b", "main; rm -rf /", "x.lock", "feat/", ""):
            with pytest.raises(SystemExit):
                mod.validate_base_ref(bad)


# --- 2026-06-18 convergence pass 4: nested-field iteration guards — a non-list `tests` /
#     work-item `dod` in a worker/manifest artifact is skipped, not a TypeError ---
def test_collect_worker_tests_tolerates_non_list():
    assert asm.collect_worker_tests([{"tests": 5}]) == []  # used to raise TypeError


def test_collect_manifest_dod_tolerates_non_list_work_item_dod():
    result = asm.collect_manifest_dod({"dod": ["d1"], "work_items": [{"dod": 7}]})  # must not raise
    assert result == ["d1"]


# --- 2026-06-18 convergence pass 4: the worker mutating-command security gate is exercised
#     (was gate-dark, like its research sibling) ---
def test_validate_worker_command_evidence_flags_git_mutation():
    defects: list[str] = []
    vbs.validate_worker_command_evidence(defects, ["git push origin HEAD"], "$.w.commands_run")
    assert any("must not list mutating command evidence" in d for d in defects), defects
    clean: list[str] = []
    vbs.validate_worker_command_evidence(clean, ["pytest -q"], "$.w.commands_run")
    assert clean == []


# --- 2026-06-18 convergence pass 3: the research-worker secret-marker security branches are
#     exercised (were gate-dark — a silent regression would let a worker reading .ssh/id_rsa pass) ---
def test_validate_research_security_flags_secret_markers():
    defects: list[str] = []
    vbs.validate_research_security(defects, ["cat .env"], [".ssh/id_rsa"], "$.research")
    assert sum("secret or credential material" in d for d in defects) >= 2, defects


# --- validate_branch_status: the gate fails closed on malformed JSON (no traceback) ---
def test_validate_branch_status_cli_fails_closed_on_malformed_json(tmp_path):
    status_path = tmp_path / "B01.status.json"
    status_path.write_text("not valid json {", encoding="utf-8")
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills" / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py"),
            "--status",
            str(status_path),
            "--manifest",
            str(manifest_path),
            "--branch-id",
            "B01",
            "--branch",
            "phaseX-B01",
        ],
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, combined
    assert "Traceback" not in proc.stderr, f"validator crashed instead of failing closed:\n{proc.stderr}"
    assert "readable JSON" in combined, combined


# --- a manifest branch entry missing review_path is a defect (no silent review bypass) ---
def test_manifest_branch_identity_requires_review_path(tmp_path):
    defects: list[str] = []
    branch_entry = {
        "branch_name": "phaseX-B01",
        "status_path": "branches/B01.status.json",
        "pre_review_gate_path": "branches/B01.pre_review_gate.json",
        # review_path intentionally omitted
    }
    vbs.validate_manifest_branch_identity(
        defects,
        {"branch": "phaseX-B01"},
        branch_entry,
        branch_id="B01",
        manifest_path=tmp_path / "job.manifest.json",
        status_path=tmp_path / "branches" / "B01.status.json",
    )
    assert any("review_path" in d for d in defects), defects


# --- assemble_branch_status: malformed semi-trusted artifacts degrade, never abort ---
def test_assemble_read_helpers_fail_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ broken", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        asm.read_json(bad)  # malformed JSON now fails clean (SystemExit), not a traceback
    blockers: list[str] = []
    assert asm.read_object_or_blocker(bad, blockers, "worker artifact") is None
    assert asm.read_object_or_blocker(arr, blockers, "worker artifact") is None
    assert len(blockers) == 2


def test_scheduler_rollup_tolerates_malformed_event_name(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    branch = {
        "id": "B01",
        "work_items": [{"id": "W01", "packet_id": "B01-W01", "owned_paths": ["src/a.py"]}],
    }
    manifest_path.write_text(json.dumps({"branches": [branch]}, sort_keys=True), encoding="utf-8")
    scheduler_path = tmp_path / asm.CONTRACT.worker_scheduler_path("B01")
    scheduler_path.parent.mkdir(parents=True)
    scheduler_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "scheduler_kind": "branch-worker-pool",
                "scheduler_path": asm.CONTRACT.worker_scheduler_path("B01"),
                "capacity": asm.CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH,
                "item_ids": ["B01-W01"],
                "events": [
                    {
                        "seq": 1,
                        "timestamp": "2026-06-18T00:00:00Z",
                        "wall_clock_timestamp": "2026-06-18T00:00:00Z",
                        "runtime_ref": "test",
                        "event": [],
                        "id": "B01-W01",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    rollup, defects = asm.scheduler_rollup(manifest_path, branch, "B01")

    assert rollup["serial_reasons"] == []
    assert any("$.worker_parallelism.scheduler_path.events[0].event" in defect for defect in defects), defects
    assert not any("could not read scheduler refill events" in defect for defect in defects), defects


# --- create_runtime_packet: load_json fails closed; dead telemetry_function removed ---
def test_create_runtime_packet_load_json_fails_closed(tmp_path):
    bad = tmp_path / "job.manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        crp.load_json(bad)


def test_validate_launch_config_adapter_rejects_non_string_provider():
    config = {"attempts": [{"provider": ["codex"]}]}
    with pytest.raises(SystemExit) as exc:
        crp.validate_launch_config_adapter(config)
    msg = str(exc.value)
    assert "provider must be a supported route adapter" in msg
    assert "['codex']" in msg


def test_validate_launch_config_adapter_requires_provider_for_generic_cli():
    config = {
        "attempts": [
            {
                "provider": "generic-cli",
                "alias": "generic-cli",
                "model": "cli-model",
                "command": "echo hello",
                "rendered_command": "echo hello",
                "command_binary": "echo",
                "route_policy_version": "goal-route-policy-v2",
                "timeout_seconds": 90,
                "telemetry_capability": {"token_usage": "best_effort"},
            }
        ]
    }
    assert crp.validate_launch_config_adapter(config) is None


def test_validate_launch_config_adapter_and_validator_disagree_without_provider():
    config = {
        "attempts": [
            {
                "harness_kind": "generic-cli",
                "alias": "generic-cli",
                "model": "cli-model",
                "command": "echo hello",
                "rendered_command": "echo hello",
                "command_binary": "echo",
                "route_policy_version": "goal-route-policy-v2",
                "timeout_seconds": 90,
                "telemetry_capability": {"token_usage": "best_effort"},
            }
        ]
    }
    with pytest.raises(SystemExit):
        crp.validate_launch_config_adapter(config)
    defects: list[str] = []
    vbs.validate_launch_config_attempts(defects, config, "$.launch_config", role="worker")
    assert any("provider" in d for d in defects), defects


def test_validate_launch_config_adapter_rejects_generic_cli_without_command_binary():
    config = {
        "attempts": [
            {
                "provider": "generic-cli",
                "alias": "generic-cli",
                "model": "cli-model",
                "command": "echo hello",
                "rendered_command": "echo hello",
                "route_policy_version": "goal-route-policy-v2",
                "timeout_seconds": 90,
                "telemetry_capability": {"token_usage": "best_effort"},
            }
        ]
    }
    with pytest.raises(SystemExit) as exc:
        crp.validate_launch_config_adapter(config)
    assert "command_binary is required for generic-cli attempts" in str(exc.value)


def test_validate_launch_attempt_provider_accepts_generic_cli():
    defects: list[str] = []
    vbs.validate_launch_attempt_provider(
        defects,
        {
            "provider": "generic-cli",
            "alias": "generic-cli",
            "model": "cli-model",
        },
        "$.launch_config.attempts[0]",
        alias="generic-cli",
    )
    assert defects == []


def test_validate_launch_config_attempts_rejects_generic_cli_without_command_binary():
    defects: list[str] = []
    vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "generic-cli",
                    "alias": "generic-cli",
                    "model": "cli-model",
                    "command": "echo hello",
                    "rendered_command": "echo hello",
                    "route_policy_version": "goal-route-policy-v2",
                    "sandbox": "read-only",
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-generic-cli.log"],
                }
            ]
        },
        "$.launch_config",
        role="worker",
    )
    assert any("command_binary" in defect for defect in defects), defects


def test_validate_launch_config_attempts_rejects_opencode_bridge_without_bridge_run_fields():
    defects: list[str] = []
    vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "opencode-bridge",
                    "alias": "ds-pro-max",
                    "model": "deepseek-v4-pro",
                    "command": "echo hello",
                    "rendered_command": "echo hello",
                    "route_policy_version": "goal-route-policy-v2",
                    "sandbox": "workspace-write",
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-opencode-bridge.log"],
                    "bridge": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "variant": "max",
                        "permission_profile": "workspace-write",
                        "run_dir": "bridge/run-01",
                    },
                }
            ]
        },
        "$.launch_config",
        role="worker",
    )
    assert any("run_args" in defect for defect in defects), defects
    assert any("run_readback" in defect for defect in defects), defects
    assert any("bridge.pool_dir" in defect for defect in defects), defects


def test_validate_launch_config_attempts_rejects_non_contract_opencode_bridge_without_bridge_metadata():
    defects: list[str] = []
    vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "opencode-bridge",
                    "alias": "lite_agent",
                    "model": "deepseek-v4-flash",
                    "command": "echo hello",
                    "rendered_command": "echo hello",
                    "route_policy_version": "goal-route-policy-v2",
                    "sandbox": "workspace-write",
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-opencode-bridge.log"],
                    "bridge": {
                        "provider": "deepseek",
                        "model": "deepseek-v4-flash",
                        "variant": "max",
                        "permission_profile": "workspace-write",
                        "run_dir": "bridge/run-01",
                    },
                }
            ]
        },
        "$.launch_config",
        role="worker",
    )
    assert any("run_args" in defect for defect in defects), defects
    assert any("run_readback" in defect for defect in defects), defects
    assert any("bridge.pool_dir" in defect for defect in defects), defects


def test_validate_launch_config_attempts_accepts_generic_cli_with_command_binary():
    defects: list[str] = []
    aliases = vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "generic-cli",
                    "alias": "generic-cli",
                    "model": "cli-model",
                    "command": "echo hello",
                    "rendered_command": "echo hello",
                    "command_binary": "echo",
                    "route_policy_version": "goal-route-policy-v2",
                    "sandbox": "read-only",
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-generic-cli.log"],
                }
            ]
        },
        "$.launch_config",
        role="worker",
    )
    assert defects == []
    assert aliases == ["generic-cli"]


def test_opencode_bridge_supervisor_invocation_carries_route_prompt_and_validator(monkeypatch, tmp_path):
    packet_dir = tmp_path / "reviewers" / "B01-R01"
    packet_dir.mkdir(parents=True)
    (packet_dir / "prompt.md").write_text("review prompt\n", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    bridge_root = tmp_path / "bridge-root"
    calls: list[dict] = []

    def fake_run_bridge_command(**kwargs):
        calls.append(kwargs)
        subcommand = kwargs["subcommand"]
        extra_args = kwargs["extra_args"]
        if subcommand == "supervisor":
            run_dir = Path(extra_args[extra_args.index("--run-dir") + 1])
            run_dir.mkdir(parents=True, exist_ok=True)
            route = {
                "provider": extra_args[extra_args.index("--provider") + 1],
                "model": extra_args[extra_args.index("--model") + 1],
                "variant": extra_args[extra_args.index("--variant") + 1],
            }
            (run_dir / rpr.BRIDGE_JOB_ENVELOPE_NAME).write_text(
                json.dumps({"status": "passed", "route": route, "assistant_text": "review ok"}),
                encoding="utf-8",
            )
            (run_dir / rpr.BRIDGE_WORKER_STATUS_NAME).write_text(
                json.dumps({"lifecycle": "completed"}),
                encoding="utf-8",
            )
            (run_dir / rpr.BRIDGE_SUPERVISOR_VERDICT_NAME).write_text(
                json.dumps({"status": "completed"}),
                encoding="utf-8",
            )
        return {
            "returncode": 0,
            "elapsed_ms": 1,
            "timed_out": False,
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "command": subcommand,
            "command_parts": [subcommand],
        }

    monkeypatch.setattr(rpr, "resolve_bridge_root", lambda: bridge_root)
    monkeypatch.setattr(rpr, "_run_bridge_command", fake_run_bridge_command)

    attempt = {
        "alias": "ds-pro-max",
        "provider": "opencode-bridge",
        "model": "deepseek-v4-pro",
        "variant": "max",
        "timeout_seconds": 10,
        "bridge": {
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "variant": "max",
            "permission_profile": "read-only",
            "run_dir": "bridge/ds-pro-max",
            "pool_dir": "bridge/pool",
            "pool_max_workers": 4,
            "prompt_file": "prompt.md",
            "supervisor": True,
        },
    }
    config = {"role": "reviewer", "packet_id": "B01-R01", "timeout_kill_after_seconds": 1}

    rc, _, _ = rpr.run_opencode_bridge_model(
        attempt,
        packet_dir=packet_dir,
        config=config,
        schema_name="review.schema.json",
        output_name="review.json",
        worktree=worktree.as_posix(),
        label="ds-pro-max",
    )

    assert rc == 0
    supervisor_call = next(call for call in calls if call["subcommand"] == "supervisor")
    args = supervisor_call["extra_args"]
    assert args[args.index("--retry-action") + 1] == "delegate"
    assert args[args.index("--provider") + 1] == "deepseek"
    assert args[args.index("--model") + 1] == "deepseek-v4-pro"
    assert args[args.index("--variant") + 1] == "max"
    assert args[args.index("--permission-profile") + 1] == "read-only"
    assert Path(args[args.index("--prompt-file") + 1]).read_text(encoding="utf-8") == "review prompt\n"
    assert Path(args[args.index("--validator") + 1]).exists()
    assert "--follow-up-file" not in args
    assert "--prompt-file" in attempt["executed_command"]
    assert "--retry-action delegate" in attempt["executed_command"]


def test_validate_launch_config_attempts_rejects_selected_ladder_with_non_string_extra():
    defects: list[str] = []
    vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "codex",
                    "alias": "codex-spark",
                    "model": "gpt-5.3-codex-spark",
                    "command": "codex exec --ephemeral -m gpt-5.3-codex-spark -s read-only",
                    "rendered_command": "codex exec --ephemeral -m gpt-5.3-codex-spark -s read-only",
                    "sandbox": "read-only",
                    "route_policy_version": "goal-route-policy-v2",
                    "ignore_user_config": True,
                    "ignore_rules": True,
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-codex-spark.jsonl"],
                }
            ],
            "selected_ladder": ["codex-spark", 1],
        },
        "$.launch_config",
        role="worker",
    )
    assert any(
        "selected_ladder" in defect and "must match launch attempt aliases" in defect for defect in defects
    ), defects


def test_validate_launch_config_attempts_rejects_non_list_selected_ladder():
    defects: list[str] = []
    vbs.validate_launch_config_attempts(
        defects,
        {
            "attempts": [
                {
                    "provider": "codex",
                    "alias": "codex-spark",
                    "model": "gpt-5.3-codex-spark",
                    "command": "codex exec --ephemeral -m gpt-5.3-codex-spark -s read-only",
                    "rendered_command": "codex exec --ephemeral -m gpt-5.3-codex-spark -s read-only",
                    "route_policy_version": "goal-route-policy-v2",
                    "sandbox": "read-only",
                    "ignore_user_config": True,
                    "ignore_rules": True,
                    "timeout_seconds": 90,
                    "telemetry_capability": {"token_usage": "best_effort", "source": "provider_or_harness_output"},
                    "event_logs": ["events-codex-spark.jsonl"],
                }
            ],
            "selected_ladder": "codex-spark",
        },
        "$.launch_config",
        role="worker",
    )
    assert any("selected_ladder" in defect for defect in defects), defects


def test_validate_launch_attempt_provider_preserves_codex_and_bridge_checks():
    defects: list[str] = []
    vbs.validate_launch_attempt_provider(
        defects,
        {"provider": "codex", "alias": "codex-spark", "model": "wrong-model"},
        "$.launch_config.attempts[0]",
        alias="codex-spark",
    )
    assert any("must be" in defect for defect in defects), defects


def test_resolve_worker_routing_fails_closed_for_manifest_custom_route_class(tmp_path):
    manifest = {
        "branches": [
            {
                "id": "B01",
                "work_items": [
                    {"id": "W01", "packet_id": "B01-W01", "route_class": "custom", "owned_paths": ["src/main.py"]}
                ],
            }
        ]
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    args = argparse.Namespace(
        worker_route=[],
        route_class=None,
        allow_route_pruning=False,
        selection_reason="",
        model_catalog=None,
    )
    with pytest.raises(SystemExit):
        crp.resolve_worker_routing(
            args,
            packet_id="B01-W01",
            manifest_branch_id="B01",
            manifest=None,
            manifest_path=manifest_path,
            telemetry_debug=False,
            context_files=[manifest_path.as_posix()],
        )


def test_resolve_worker_routing_allows_manifest_custom_route_class_with_explicit_route_class(tmp_path):
    manifest = {
        "branches": [
            {
                "id": "B01",
                "work_items": [
                    {"id": "W01", "packet_id": "B01-W01", "route_class": "custom", "owned_paths": ["src/main.py"]}
                ],
            }
        ]
    }
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    args = argparse.Namespace(
        worker_route=[],
        route_class="custom",
        allow_route_pruning=False,
        selection_reason="",
        model_catalog=None,
    )
    routing = crp.resolve_worker_routing(
        args,
        packet_id="B01-W01",
        manifest_branch_id="B01",
        manifest=None,
        manifest_path=manifest_path,
        telemetry_debug=False,
        context_files=[manifest_path.as_posix()],
    )
    assert routing.route_class == "custom"


def test_dead_telemetry_function_removed():
    assert not hasattr(crp, "telemetry_function")


# --- promote_worker_repair_evidence: non-list evidence fields do not crash ---
def test_evidence_commands_tolerates_non_list_fields():
    # Non-list local_validation / commands_run / tests used to raise TypeError; now they are
    # handled gracefully and the function reaches its normal clean validation (SystemExit).
    with pytest.raises(SystemExit) as exc:
        prw.evidence_commands({"local_validation": 5, "commands_run": None, "tests": True})
    assert "git diff --check" in str(exc.value)  # clean validation, not a TypeError crash
    # With valid git-diff + test commands and non-list siblings, it returns without crashing.
    commands, tests = prw.evidence_commands(
        {
            "local_validation": [{"command": "git diff --check main...HEAD"}, {"command": "pytest tests/test_x.py"}],
            "commands_run": None,
            "tests": True,
        }
    )
    assert "git diff --check main...HEAD" in commands and any("pytest" in t for t in tests)


# --- 2026-06-18 convergence pass: a malformed referenced goal_config file records a defect
#     instead of crashing the defect-collecting validator with a raw JSONDecodeError ---
def test_goal_config_from_manifest_fails_closed_on_malformed_config(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text('{"goal_config_path": "goal-config.json"}', encoding="utf-8")
    (tmp_path / "goal-config.json").write_text("{ not json", encoding="utf-8")
    manifest_root = {"goal_config_path": "goal-config.json"}
    defects: list[str] = []
    # Must NOT raise: a valid manifest pointing at a malformed config used to escape main()
    # as an unhandled JSONDecodeError; now it is a structured defect.
    result = vbs.goal_config_from_manifest(defects, manifest_root, manifest_path)
    assert result is None
    assert any("goal_config_path" in d and "readable JSON" in d for d in defects), defects
    # A well-formed referenced config is still returned unchanged.
    (tmp_path / "goal-config.json").write_text('{"model_policies": {}}', encoding="utf-8")
    ok_defects: list[str] = []
    assert vbs.goal_config_from_manifest(ok_defects, manifest_root, manifest_path) == {"model_policies": {}}
    assert ok_defects == []


# --- pre-review-gate reviewer-reuse allowlist includes the bridge routes ---
def test_reviewer_allowed_aliases_include_bridge_routes():
    assert "ds-pro-max" in vbs.REVIEWER_ALLOWED_ALIASES
    assert "ds-flash-max" in vbs.REVIEWER_ALLOWED_ALIASES


# --- 2026-06-18 convergence pass: create_runtime_packet's tolerant readers catch the
#     SystemExit that load_json raises (except Exception alone could not), so a malformed/non-dict
#     runtime artifact degrades instead of crashing packet creation ---
def test_create_runtime_packet_tolerant_readers_absorb_systemexit(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")  # valid JSON, non-dict -> load_json SystemExit
    # read_json_or_none must return (None, msg), never propagate SystemExit
    data, err = crp.read_json_or_none(bad)
    assert data is None and err
    data2, err2 = crp.read_json_or_none(arr)
    assert data2 is None and err2
    # the reviewer-artifact summariser degrades to a structured "invalid json" marker
    assert crp._summarize_reviewer_artifact(bad) == {"exists": False, "reason": "invalid json"}
    # the scheduler closed-pass probe degrades to False, never crashes
    assert crp.scheduler_closed_pass_for_packet(bad, "P01") is False


# --- 2026-06-18 convergence pass: the debug-events reader tolerates a non-UTF-8 artifact
#     (errors="replace") instead of escaping the gate as a UnicodeDecodeError ---
def test_validate_launch_config_debug_events_tolerates_non_utf8(tmp_path):
    (tmp_path / "debug.events.jsonl").write_bytes(b"\xff\xfe garbage\n")
    defects: list[str] = []
    # must not raise UnicodeDecodeError; unparseable content becomes a structured defect
    vbs.validate_launch_config_debug_events(
        defects,
        {"debug_events_name": "debug.events.jsonl"},
        tmp_path,
        "$.launch_config",
        packet_id="P01",
    )
    assert any("debug_events_name" in d for d in defects), defects


# --- 2026-06-18 convergence pass: stale pre-bridge reviewer constants + transitively-dead
#     route maps were removed (no consumer; verified repo-wide) ---
def test_stale_reviewer_constants_removed():
    for name in (
        "REVIEWER_MODEL",
        "REVIEWER_FALLBACK_MODEL",
        "REVIEWER_MINI_MODEL",
        "RESEARCH_MODEL",
        "RESEARCH_FALLBACK_MODEL",
        "WORKER_ROUTE_LABELS",
        "WORKER_ROUTE_COMMANDS",
        "REVIEW_ROUTE_MODELS",
        "SPARK_MODEL",
        "MINI_MODEL",
    ):
        assert not hasattr(crp, name), name
    assert not hasattr(rpr, "BRIDGE_ISSUE_IDS")
    # Pass-2: ALLOWED_WORKER_ROUTES in create_runtime_packet was the lone leftover dead const
    assert not hasattr(crp, "ALLOWED_WORKER_ROUTES")
    # live siblings remain
    assert hasattr(crp, "WORKER_ROUTE_EVENT_LABELS")
    assert hasattr(crp, "CODEX_LEAN_EXEC_FLAGS_TEXT")


def test_record_bundle_route_failure_tolerates_malformed_persisted_counter(tmp_path):
    packet_dir = tmp_path / "workers" / "B01-W01"
    packet_dir.mkdir(parents=True)
    route_health_path = tmp_path / rpr.ROUTE_HEALTH_NAME
    route_health_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {
                    "codex-spark": {
                        "failures": {rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS: ["bad-counter"]},
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    attempt = {
        "alias": "codex-spark",
        "provider": "codex",
        "model": "gpt-5.3-codex-spark",
        "failure_subclass": rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS,
    }

    rpr.record_bundle_route_failure(packet_dir, attempt)

    data = json.loads(route_health_path.read_text(encoding="utf-8"))
    route = data["routes"]["codex-spark"]
    assert route["failures"][rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS] == 1
    assert route.get("degraded") is not True


def test_record_bundle_route_failure_clamps_negative_persisted_counter(tmp_path):
    packet_dir = tmp_path / "workers" / "B01-W01"
    packet_dir.mkdir(parents=True)
    route_health_path = tmp_path / rpr.ROUTE_HEALTH_NAME
    route_health_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {
                    "ds-pro-max": {
                        "failures": {rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS: -100},
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    attempt = {
        "alias": "ds-pro-max",
        "provider": "opencode-bridge",
        "model": "deepseek-v4-pro",
        "failure_subclass": rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS,
    }

    rpr.record_bundle_route_failure(packet_dir, attempt)
    rpr.record_bundle_route_failure(packet_dir, attempt)

    data = json.loads(route_health_path.read_text(encoding="utf-8"))
    route = data["routes"]["ds-pro-max"]
    assert route["failures"][rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS] == rpr.ROUTE_DEGRADE_EMPTY_OUTPUT_THRESHOLD
    assert route["degraded"] is True
    assert route["degraded_reason"] == rpr.OPENCODE_EMPTY_OUTPUT_SUBCLASS
    assert route["degraded_after_count"] == rpr.ROUTE_DEGRADE_EMPTY_OUTPUT_THRESHOLD


def test_record_generated_artifact_cleanup_tolerates_malformed_persisted_counts(tmp_path):
    packet_dir = tmp_path
    cleanup_path = packet_dir / rpr.GENERATED_CLEANUP_NAME
    cleanup_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "attempts": [
                    {
                        "status": "pass",
                        "candidates_count": ["bad"],
                        "removed_count": {"bad": "counter"},
                        "failed_count": True,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    attempt: dict[str, object] = {}
    cleanup = {
        "status": "pass",
        "generated_artifacts_only": True,
        "candidates_count": 2,
        "removed_count": "1",
        "failed_count": "0",
    }

    rpr.record_generated_artifact_cleanup(packet_dir, attempt, cleanup)

    data = json.loads(cleanup_path.read_text(encoding="utf-8"))
    assert data["candidates_count"] == 2
    assert data["removed_count"] == 1
    assert data["failed_count"] == 0
    assert data["status"] == "pass"
    assert attempt["generated_artifact_cleanup_path"] == rpr.GENERATED_CLEANUP_NAME


# --- 2026-06-18 convergence pass 2: the assembler's tolerant reader degrades a non-UTF-8 artifact
#     to a blocker instead of escaping as a UnicodeDecodeError (read_object_or_blocker only caught
#     SystemExit; read_json now fails closed on non-UTF-8 too) ---
def test_assemble_read_helpers_tolerate_non_utf8(tmp_path):
    nonutf8 = tmp_path / "status.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        asm.read_json(nonutf8)
    blockers: list[str] = []
    assert asm.read_object_or_blocker(nonutf8, blockers, "worker artifact") is None
    assert blockers


# --- 2026-06-18 convergence pass 15: set-membership over a tampered (unhashable) status field
#     fails closed with a defect instead of `TypeError: unhashable type` (`status not in STATUSES`
#     hashes the LHS). ---
def test_validate_worker_status_tolerates_unhashable_status():
    defects: list[str] = []
    vbs.validate_worker_status(defects, {"status": ["pass"], "packet_id": "p"}, "$.w")  # must not raise
    assert any("status" in d for d in defects), defects


def test_packet_next_action_fail_closed_for_unhashable_status_and_terminal_state():
    assert rpr.packet_next_action(["pass"], "pass") == "inspect_packet_artifacts"
    assert rpr.packet_next_action("pass", ["pass"]) == "inspect_packet_artifacts"
    assert rpr.packet_next_action({"status": "pass"}, {"terminal_state": "blocked"}) == "inspect_packet_artifacts"


def test_write_packet_summary_fails_closed_with_unhashable_output_and_terminal_state(tmp_path):
    output_name = "worker-output.json"
    config = {
        "packet_id": "B01-P01",
        "role": "worker",
        "worktree": str(tmp_path),
        "output_name": output_name,
        "attempts": [{}],
    }
    (tmp_path / output_name).write_text(json.dumps({"status": ["pass"]}), encoding="utf-8")
    state = {"terminal_state": {"state": "pass"}, "events": []}
    (tmp_path / rpr.state_artifact_name(config)).write_text(json.dumps(state), encoding="utf-8")
    rpr.write_packet_summary(tmp_path, config)
    summary = json.loads((tmp_path / "packet.summary.json").read_text(encoding="utf-8"))
    assert summary["next_action"] == "inspect_packet_artifacts"
    assert summary["output_status"] == ["pass"]
    assert summary["terminal_state"] == {"state": "pass"}


def test_write_packet_summary_fails_closed_with_malformed_route_health(tmp_path):
    output_name = "worker-output.json"
    config = {
        "packet_id": "B01-P01",
        "role": "worker",
        "worktree": str(tmp_path),
        "output_name": output_name,
        "attempts": [{}],
    }
    (tmp_path / output_name).write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    state = {
        "terminal_state": "fail-clean",
        "events": [
            {
                "attempt_index": 0,
                "state": "fail-clean",
                "route_health": {"transport_disconnect_count": ["x"]},
            }
        ],
    }
    (tmp_path / rpr.state_artifact_name(config)).write_text(json.dumps(state), encoding="utf-8")

    rpr.write_packet_summary(tmp_path, config)

    summary = json.loads((tmp_path / "packet.summary.json").read_text(encoding="utf-8"))
    assert summary["attempts"][0]["route_health"]["transport_disconnect_count"] == 0
    assert summary["next_action"] == "close_blocked_or_create_repair"


def test_run_packet_fails_closed_with_malformed_launch_route_health(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=worktree, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    packet_dir = tmp_path / "workers" / "B01-P01"
    packet_dir.mkdir(parents=True)
    telemetry_script = packet_dir / "telemetry.py"
    telemetry_script.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--packet-dir', required=True)\n"
        "parser.add_argument('--attempt-json', action='append', default=[])\n"
        "parser.add_argument('--packet-id')\n"
        "parser.add_argument('--role')\n"
        "parser.add_argument('--output-name')\n"
        "parser.add_argument('--prompt-name')\n"
        "args = parser.parse_args()\n"
        "attempts = [json.loads(item) for item in args.attempt_json]\n"
        "Path(args.packet_dir, 'telemetry.json').write_text(\n"
        "    json.dumps({'schema_version': 1, 'attempts': attempts}, sort_keys=True), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    config = {
        "schema_version": 1,
        "packet_id": "B01-P01",
        "role": "worker",
        "branch_id": "B01",
        "work_item_id": "P01",
        "manifest_hash": "",
        "manifest_epoch": "epoch-1",
        "worktree_path": str(worktree),
        "worktree": str(worktree),
        "branch": "test-branch",
        "route_id": "generic-cli",
        "route_class": "implementation",
        "selected_ladder": ["generic-cli"],
        "selection_reason": "regression",
        "sandbox": "read-only",
        "evidence_summary": "runtime route health regression",
        "output_name": "worker-output.json",
        "schema_name": "worker.schema.json",
        "telemetry_script": str(telemetry_script),
        "terminal_message": "Worker attempts failed.",
        "timeout_kill_after_seconds": 1,
        "attempts": [
            {
                "provider": "generic-cli",
                "alias": "generic-cli",
                "model": "missing-cli",
                "command_binary": "definitely-missing-goal-cli-binary",
                "timeout_seconds": 1,
                "route_health": {"transport_disconnect_count": ["x"]},
            }
        ],
    }
    (packet_dir / rpr.CONFIG_NAME).write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    (packet_dir / "prompt.md").write_text("do work\n", encoding="utf-8")
    (packet_dir / "worker.schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")

    rc = rpr.run_packet(packet_dir)

    assert rc == 1
    output = json.loads((packet_dir / "worker-output.json").read_text(encoding="utf-8"))
    assert output["status"] == "blocked"
    launcher = json.loads((packet_dir / "launcher-state.json").read_text(encoding="utf-8"))
    assert launcher["terminal_state"] == "blocked"
    assert launcher["events"][-1]["state"] == "blocked"
    summary = json.loads((packet_dir / "packet.summary.json").read_text(encoding="utf-8"))
    assert summary["next_action"] == "close_blocked_or_create_repair"
    assert summary["attempts"][0]["route_health"]["transport_disconnect_count"] == 0


def test_run_packet_fails_closed_with_malformed_persisted_bundle_route_health(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=worktree, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    packet_dir = tmp_path / "workers" / "B01-P01"
    packet_dir.mkdir(parents=True)
    telemetry_script = packet_dir / "telemetry.py"
    telemetry_script.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--packet-dir', required=True)\n"
        "parser.add_argument('--attempt-json', action='append', default=[])\n"
        "parser.add_argument('--packet-id')\n"
        "parser.add_argument('--role')\n"
        "parser.add_argument('--output-name')\n"
        "parser.add_argument('--prompt-name')\n"
        "args = parser.parse_args()\n"
        "attempts = [json.loads(item) for item in args.attempt_json]\n"
        "Path(args.packet_dir, 'telemetry.json').write_text(\n"
        "    json.dumps({'schema_version': 1, 'attempts': attempts}, sort_keys=True), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )
    config = {
        "schema_version": 1,
        "packet_id": "B01-P01",
        "role": "worker",
        "branch_id": "B01",
        "work_item_id": "P01",
        "manifest_hash": "",
        "manifest_epoch": "epoch-1",
        "worktree_path": str(worktree),
        "worktree": str(worktree),
        "branch": "test-branch",
        "route_id": "generic-cli",
        "route_class": "implementation",
        "selected_ladder": ["generic-cli"],
        "selection_reason": "regression",
        "sandbox": "read-only",
        "evidence_summary": "runtime persisted route health regression",
        "output_name": "worker-output.json",
        "schema_name": "worker.schema.json",
        "telemetry_script": str(telemetry_script),
        "terminal_message": "Worker attempts failed.",
        "timeout_kill_after_seconds": 1,
        "attempts": [
            {
                "provider": "generic-cli",
                "alias": "generic-cli",
                "model": "missing-cli",
                "command_binary": "definitely-missing-goal-cli-binary",
                "timeout_seconds": 1,
            }
        ],
    }
    (packet_dir / rpr.CONFIG_NAME).write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    (packet_dir / "prompt.md").write_text("do work\n", encoding="utf-8")
    (packet_dir / "worker.schema.json").write_text(json.dumps({"type": "object"}), encoding="utf-8")
    (tmp_path / rpr.ROUTE_HEALTH_NAME).write_text("{ not json", encoding="utf-8")

    rc = rpr.run_packet(packet_dir)

    assert rc == 1
    output = json.loads((packet_dir / "worker-output.json").read_text(encoding="utf-8"))
    assert output["status"] == "blocked"
    assert any("Worker attempts failed." in blocker for blocker in output["blockers"])
    launcher = json.loads((packet_dir / "launcher-state.json").read_text(encoding="utf-8"))
    assert launcher["terminal_state"] == "blocked"
    assert launcher["events"][-1]["state"] == "blocked"
    telemetry = json.loads((packet_dir / "telemetry.json").read_text(encoding="utf-8"))
    assert telemetry["attempts"][0]["alias"] == "generic-cli"
    summary = json.loads((packet_dir / "packet.summary.json").read_text(encoding="utf-8"))
    assert summary["next_action"] == "close_blocked_or_create_repair"
    assert summary["output_status"] == "blocked"
    route_health = json.loads((tmp_path / rpr.ROUTE_HEALTH_NAME).read_text(encoding="utf-8"))
    assert route_health["routes"] == {}
    assert route_health["warnings"][0]["kind"] == "corrupt_route_health_ignored"


# --- 2026-06-18 fresh-audit pass ---
rws = load_module("skills/goal-branch-orchestrator/scripts/render_worker_schedule.py", "rws_review")


# B1: _review_finalize_no_success used `parse_messages` outside the `isinstance(parse_report, dict)`
#     guard, so a non-dict last-attempt _parse_report raised UnboundLocalError instead of writing the
#     terminal blocked status (the worker sibling nests the use correctly). The writers are stubbed to
#     isolate the message-building block that holds the fix.
def test_review_finalize_no_success_tolerates_non_dict_parse_report(tmp_path, monkeypatch):
    monkeypatch.setattr(rpr, "write_terminal", lambda *a, **k: None)
    monkeypatch.setattr(rpr, "write_launcher_state", lambda *a, **k: None)
    monkeypatch.setattr(rpr, "write_telemetry", lambda *a, **k: None)
    monkeypatch.setattr(rpr, "cleanup_runtime_cache_evidence", lambda *a, **k: None)
    config = {"terminal_message": "Reviewer primary and fallback failed."}
    attempts = [{"_parse_report": "not-a-dict", "alias": "ds-pro-max"}]
    assert rpr._review_finalize_no_success(tmp_path, config, attempts=attempts) == 1  # no UnboundLocalError


# B2: render_worker_schedule.validate_work_items must accept the legacy worker_type alias "research"
#     (every sibling consumer normalizes it) and fail closed (clean SystemExit, not TypeError) on a
#     non-string worker_type.
def _valid_research_branch():
    return {
        "max_active_worker_packets": 1,
        "worker_parallelism": {
            "parallelism_default": True,
            "scheduling_mode": "rolling",
            "max_active_worker_packets": 1,
            "max_worker_packets_per_branch": 4,
            "slot_refill": "launch replacements as slots free",
            "dependency_policy": "respect depends_on",
        },
        "work_items": [
            {
                "id": "W01",
                "packet_id": "B01-W01",
                "worker_type": "research",
                "owned_paths": ["src/a.py"],
                "verification": ["pytest"],
                "dod": ["done"],
                "context_files": [],
                "depends_on": [],
            }
        ],
    }


def test_validate_work_items_accepts_research_alias():
    validated, max_active = rws.validate_work_items(_valid_research_branch(), "B01")  # must not raise
    assert max_active == 1 and len(validated) == 1


def test_completed_research_alias_uses_research_status_path(tmp_path):
    branch = _valid_research_branch()
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(json.dumps({"branches": [{"id": "B01", **branch}]}, sort_keys=True), encoding="utf-8")
    research_dir = tmp_path / "research" / "B01-W01"
    research_dir.mkdir(parents=True)
    (research_dir / "research.json").write_text(json.dumps({"status": "pass"}, sort_keys=True), encoding="utf-8")

    work_items, _ = rws.validate_work_items(branch, "B01")

    rws.validate_completed_worker_statuses(manifest_path, work_items, {"B01-W01"}, set())


def test_validate_work_items_rejects_boolean_work_item_id():
    branch = _valid_research_branch()
    branch["work_items"][0]["id"] = True
    branch["work_items"][0]["packet_id"] = "B01-True"
    with pytest.raises(SystemExit) as exc:
        rws.validate_work_items(branch, "B01")
    assert "branch B01 work_items[0].id must be a string" in str(exc.value)


def test_validate_completed_worker_statuses_rejects_non_string_worker_type_path(tmp_path):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        rws.validate_completed_worker_statuses(
            manifest_path,
            [{"packet_id": "B01-W01", "worker_type": ["research"]}],
            {"B01-W01"},
            set(),
        )
    assert "workers/B01-W01/status.json" in str(exc.value)


def test_validate_work_items_fails_closed_on_non_string_worker_type():
    branch = _valid_research_branch()
    branch["work_items"][0]["worker_type"] = ["worker"]  # unhashable, would TypeError on set membership
    with pytest.raises(SystemExit):
        rws.validate_work_items(branch, "B01")


# --- 2026-06-18 fresh-audit RE-AUDIT pass: validate_branch_status set-membership over an unhashable
#     verdict / review_status / worker_type — the exact sibling of the validate_main_status crashes,
#     missed by the per-skill review AND the prior convergence sweep. Found by the re-audit AST scan. ---
def test_validate_review_artifact_tolerates_unhashable_verdict():
    defects: list[str] = []
    vbs.validate_review_artifact(
        defects,
        {"verdict": ["mergeable"], "role": "reviewer", "findings": []},
        "mergeable",
        "$.r",
        manifest={},
        branch_id="B01",
    )  # must not raise TypeError on `verdict not in REVIEW_STATUSES - {"missing"}`
    assert any("verdict" in d for d in defects), defects


def test_expected_worker_packet_roles_tolerates_unhashable_worker_type():
    defects: list[str] = []
    branch_entry = {"work_items": [{"id": "W01", "packet_id": "B01-W01", "worker_type": ["worker"]}]}
    vbs.expected_worker_packet_roles(defects, branch_entry, "B01")  # must not raise TypeError
    assert any("worker_type" in d for d in defects), defects


def test_validate_branch_review_phase_tolerates_unhashable_review_status(tmp_path):
    defects: list[str] = []
    try:
        vbs.validate_branch_review_phase(
            defects,
            {"review_status": ["mergeable"]},
            {},
            status="partial",
            root_branch_id=None,
            manifest={},
            manifest_path=tmp_path / "m.json",
            worktree=None,
            allow_archived_manifest_hashes=False,
            require_current_worktree_freshness=None,
        )
    except TypeError as exc:  # the regression we are pinning
        pytest.fail(f"review_status membership crashed on an unhashable value: {exc}")
    except Exception:
        pass  # downstream checks on the dummy root may raise other errors; only the TypeError matters here
    assert any("review_status" in d for d in defects), defects


# --- 2026-06-18 RE-AUDIT pass (7th site): validate_branch_status_header records a $.status defect and
#     NORMALIZES a non-string (unhashable) status to "" so the downstream `status in {...}` membership
#     checks (validate_branch_worker_statuses_shape / _trailer) cannot raise TypeError. The helpers are
#     also independently guarded for self-safety. ---
def test_validate_branch_status_header_normalizes_unhashable_status():
    defects: list[str] = []
    status = vbs.validate_branch_status_header(
        defects, {"status": ["pass"]}, branch_id=None, branch=None, worktree=None
    )
    assert status == ""  # normalized non-string status
    assert any("$.status" in d for d in defects), defects


def test_validate_branch_worker_statuses_shape_self_safe_on_unhashable_status():
    defects: list[str] = []
    vbs.validate_branch_worker_statuses_shape(defects, [], ["pass"])  # status is a list -> must not raise TypeError
    assert isinstance(defects, list)
