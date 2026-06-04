#!/usr/bin/env python3
"""Validate static preparedness fixtures for the goal orchestration skills."""

from __future__ import annotations

import json
import importlib.util
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from fixture_support import (
    assert_all_contains,
    assert_codex_mini_worker_route,
    assert_compact_audit_launcher,
    assert_compact_lite_launcher,
    assert_compact_runtime_launcher,
    assert_contains,
    assert_lean_codex_attempts,
    assert_mixed_worker_route,
    assert_not_contains,
    assert_openai_strict_schema,
    assert_research_worker_preserves_user_config,
    assert_shell_syntax,
    make_scheduler_event,
    offline_gemini_env,
    read_json,
    run_command,
    run_runtime_packet,
    sha256_file,
    telemetry,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "fixtures" / "preparedness" / "research-worker-brief.json"
scheduler_event = make_scheduler_event("preparedness-fixture")


def run(command: list[str], *, expect: int = 0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run_command(command, root=ROOT, expect=expect, env=env)


def write_worker_scheduler(bundle: Path) -> None:
    write_json(
        bundle / "schedulers" / "B01.worker.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "branch-worker-pool",
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "manifest_sha256": sha256_file(bundle / "job.manifest.json"),
            "capacity": 1,
            "item_ids": ["B01-W01"],
            "events": [
                scheduler_event(1, "ready", id="B01-W01"),
                scheduler_event(2, "launch", id="B01-W01"),
                scheduler_event(3, "finish", id="B01-W01", status="pass"),
                scheduler_event(4, "close", id="B01-W01"),
            ],
        },
    )


def write_pre_review_gate(bundle: Path) -> None:
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--replace",
            "--skip-tests",
            "--test-skip-reason",
            "Static preparedness fixture does not run branch tests.",
            "--dod-item",
            "research-worker fixture validates",
        ]
    )


def run_pre_review_manifest_command_fixture(tmp_path: Path, bundle: Path) -> None:
    command_bundle = tmp_path / "pre-review-manifest-command"
    shutil.copytree(bundle, command_bundle)
    write_valid_research_fixture(command_bundle)
    manifest = read_json(command_bundle / "job.manifest.json")
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not isinstance(branches[0], dict):
        raise SystemExit("fixture manifest must contain at least one branch")
    manifest_command = "python3 -c 'import pathlib; assert pathlib.Path(\"README.md\").exists()'"
    branches[0]["tests"] = [manifest_command]
    write_json(command_bundle / "job.manifest.json", manifest)
    write_worker_scheduler(command_bundle)
    assemble_branch_status(command_bundle)
    result = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (command_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--dod-item",
            "manifest-declared branch validator passed",
            "--json",
        ]
    )
    gate = read_json(command_bundle / "branches" / "B01.pre_review_gate.json")
    test_check = gate.get("checks", {}).get("tests", {})
    if result.returncode != 0 or test_check.get("status") != "pass":
        raise SystemExit(f"manifest command pre-review fixture failed: {result.stdout!r} {gate!r}")
    if manifest_command not in test_check.get("manifest_commands", []):
        raise SystemExit(f"pre-review gate omitted manifest command evidence: {test_check!r}")
    if manifest_command not in gate.get("commands_run", []):
        raise SystemExit(f"pre-review gate omitted manifest command from commands_run: {gate.get('commands_run')!r}")

    declared_only_bundle = tmp_path / "pre-review-declared-only"
    shutil.copytree(bundle, declared_only_bundle)
    write_valid_research_fixture(declared_only_bundle)
    write_worker_scheduler(declared_only_bundle)
    assemble_branch_status(declared_only_bundle)
    declared_status = read_json(declared_only_bundle / "branches" / "B01.status.json")
    declared_status["tests"] = ["declared pytest passed in worker status"]
    declared_status["dod_checklist"] = ["declared DoD checklist item from branch status"]
    write_json(declared_only_bundle / "branches" / "B01.status.json", declared_status)
    declared_only = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (declared_only_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--json",
        ],
        expect=1,
    )
    declared_gate = read_json(declared_only_bundle / "branches" / "B01.pre_review_gate.json")
    declared_tests = declared_gate.get("checks", {}).get("tests", {})
    declared_dod = declared_gate.get("checks", {}).get("dod_evidence", {})
    if declared_tests.get("status") != "failed" or declared_tests.get("declared_status_tests_are_evidence") is not False:
        raise SystemExit(f"declared status tests must not pass pre-review gate: {declared_tests!r}")
    if declared_dod.get("status") != "failed" or declared_dod.get("declared_items_are_evidence") is not False:
        raise SystemExit(f"declared DoD checklist must not pass pre-review gate: {declared_dod!r}")
    assert_contains(declared_only.stdout, "informational", "declared-only pre-review fixture")

    missing_probe_bundle = tmp_path / "pre-review-missing-semantic-probe"
    shutil.copytree(bundle, missing_probe_bundle)
    write_valid_research_fixture(missing_probe_bundle)
    manifest = read_json(missing_probe_bundle / "job.manifest.json")
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not isinstance(branches[0], dict):
        raise SystemExit("fixture manifest must contain at least one branch")
    branches[0].pop("tests", None)
    branches[0]["dod"] = ["Branch-level API compatibility is verified before reviewer launch."]
    write_json(missing_probe_bundle / "job.manifest.json", manifest)
    write_worker_scheduler(missing_probe_bundle)
    assemble_branch_status(missing_probe_bundle)
    missing_probe = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (missing_probe_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--test-evidence",
            "text-only evidence is not a semantic probe",
            "--dod-item",
            "API compatibility claim needs command-backed probe",
            "--json",
        ],
        expect=1,
    )
    assert_contains(missing_probe.stdout, "command-backed semantic probe", "missing semantic probe fixture")

    self_probe_bundle = tmp_path / "pre-review-self-referential-semantic-probe"
    shutil.copytree(bundle, self_probe_bundle)
    write_valid_research_fixture(self_probe_bundle)
    manifest = read_json(self_probe_bundle / "job.manifest.json")
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not isinstance(branches[0], dict):
        raise SystemExit("fixture manifest must contain at least one branch")
    branches[0]["tests"] = ["python3 -c 'print(\"self check only\")'"]
    branches[0]["dod"] = ["Branch-level API compatibility with goal_fixture_api is verified before reviewer launch."]
    write_json(self_probe_bundle / "job.manifest.json", manifest)
    write_worker_scheduler(self_probe_bundle)
    assemble_branch_status(self_probe_bundle)
    self_probe = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (self_probe_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--dod-item",
            "self-referential command must not satisfy cross-contract compatibility",
            "--json",
        ],
        expect=1,
    )
    assert_contains(self_probe.stdout, "declared target: goal_fixture_api", "self-referential semantic probe fixture")

    target_probe_bundle = tmp_path / "pre-review-target-semantic-probe"
    shutil.copytree(bundle, target_probe_bundle)
    write_valid_research_fixture(target_probe_bundle)
    manifest = read_json(target_probe_bundle / "job.manifest.json")
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not isinstance(branches[0], dict):
        raise SystemExit("fixture manifest must contain at least one branch")
    target_command = "python3 -c 'print(\"goal_fixture_api compatibility probe\")'"
    branches[0]["tests"] = [target_command]
    branches[0]["dod"] = ["Branch-level API compatibility with goal_fixture_api is verified before reviewer launch."]
    write_json(target_probe_bundle / "job.manifest.json", manifest)
    write_worker_scheduler(target_probe_bundle)
    assemble_branch_status(target_probe_bundle)
    target_probe_result = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (target_probe_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--dod-item",
            "targeted command satisfies cross-contract compatibility",
            "--json",
        ]
    )
    _target_result = json.loads(target_probe_result.stdout)
    target_gate = read_json(target_probe_bundle / "branches" / "B01.pre_review_gate.json")
    target_semantic_probes = target_gate.get("checks", {}).get("semantic_probes", {})
    if target_semantic_probes.get("status") != "pass" or target_semantic_probes.get("targets") != ["goal_fixture_api"]:
        raise SystemExit(f"targeted semantic probe fixture should pass with declared target: {target_semantic_probes!r}")

    capitalization_bundle = tmp_path / "pre-review-capitalization-not-api"
    shutil.copytree(bundle, capitalization_bundle)
    write_valid_research_fixture(capitalization_bundle)
    manifest = read_json(capitalization_bundle / "job.manifest.json")
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not isinstance(branches[0], dict):
        raise SystemExit("fixture manifest must contain at least one branch")
    branches[0].pop("tests", None)
    branches[0]["objective"] = "Review capitalization consistency in generated headings."
    branches[0]["dod"] = ["Capitalization follows the editorial style guide."]
    write_json(capitalization_bundle / "job.manifest.json", manifest)
    write_worker_scheduler(capitalization_bundle)
    assemble_branch_status(capitalization_bundle)
    capitalization_result = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (capitalization_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--test-evidence",
            "manual editorial evidence covers capitalization",
            "--dod-item",
            "Capitalization follows the editorial style guide.",
            "--json",
        ]
    )
    capitalization_gate = json.loads(capitalization_result.stdout)
    semantic_probes = capitalization_gate.get("checks", {}).get("semantic_probes", {})
    if semantic_probes.get("requirements") not in ([], None):
        raise SystemExit(f"capitalization must not trigger API semantic probe requirement: {semantic_probes!r}")


def assemble_branch_status(bundle: Path) -> None:
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/assemble_branch_status.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--replace",
            "--test-evidence",
            "static preparedness fixture validates research-worker evidence",
            "--dod-item",
            "research-worker fixture validates with source URL, tools_used, and timeout telemetry",
            "--blocker",
            "Partial static fixture intentionally omits reviewer artifacts.",
            "--handoff",
            "Static branch fixture assembled from deterministic research-worker artifacts.",
        ]
    )


def research_status(*, bad_command: str | None = None) -> dict:
    commands = [
        "pwd",
        "git status --short --branch",
        "sed -n '1,80p' README.md",
        "curl -I https://example.com",
    ]
    if bad_command:
        commands.append(bad_command)
    return {
        "packet_id": "B01-W01",
        "role": "research-worker",
        "status": "pass",
        "branch": "preparedness-research-fixture",
        "worktree": ROOT.as_posix(),
        "search_queries": ["preparedness fixture source query"],
        "source_urls": ["https://example.com"],
        "tools_used": ["local-shell", "local-git", "local-sed", "shell-curl"],
        "local_files_read": ["README.md"],
        "commands_run": commands,
        "findings": ["Static fixture finding backed by https://example.com and README.md."],
        "blockers": [],
        "handoff": "Static research-worker fixture.",
    }


def branch_status(bundle: Path, research: dict, *, status: str = "partial") -> dict:
    return {
        "branch_id": "B01",
        "status": status,
        "schema_status": "pass",
        "runtime_status": status,
        "dod_status": "incomplete" if status != "pass" else "pass",
        "resume_action": "reuse_terminal_status",
        "branch": "preparedness-research-fixture",
        "worktree": ROOT.as_posix(),
        "worker_statuses": [
            {
                **research,
                "status_path": (bundle / "research" / "B01-W01" / "research.json").as_posix(),
            }
        ],
        "worker_parallelism": {
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "max_worker_packets_per_branch": 4,
            "max_active_worker_packets": 1,
            "max_observed_active_worker_packets": 1,
            "max_observed_active": 1,
            "concurrent_launch_default": True,
            "rolling_refill_default": True,
            "scheduling_mode": "rolling",
            "launched_ids": ["B01-W01"],
            "finished_ids": ["B01-W01"],
            "active_ids": [],
            "blocked_ids": [],
            "deferred_ids": [],
            "serialized_workers": [],
            "deferred_workers": [],
            "serial_reasons": ["Single static fixture packet."],
            "refill_events": [],
        },
        "lite_advice": [],
        "review_status": "missing",
        "review_waiver_path": "branches/B01.review-waiver.json",
        "changed_files": [],
        "commands_run": ["pwd", "git status --short --branch"],
        "tests": [],
        "dod_checklist": ["research-worker fixture validates with source URL, tools_used, and timeout telemetry"],
        "blockers": ["Partial static fixture intentionally omits reviewer artifacts."],
        "handoff": "Static branch fixture for research-worker validation.",
    }


def write_valid_research_fixture(bundle: Path) -> None:
    research = research_status()
    packet_dir = bundle / "research" / "B01-W01"
    telemetry_data = telemetry(
        "B01-W01",
        "research-worker",
        "research.json",
        accepted_alias="codex-research",
        attempts=[
            {
                "alias": "codex-research",
                "provider": "codex",
                "model": "gpt-5.4",
                "effort": None,
                "command": "codex --search exec --ephemeral -m gpt-5.4 -s read-only",
                "timeout_seconds": 1200,
                "called": True,
                "accepted": True,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            },
            {
                "alias": "codex-research-mini",
                "provider": "codex",
                "model": "gpt-5.4-mini",
                "effort": None,
                "command": "codex --search exec --ephemeral -m gpt-5.4-mini -s read-only",
                "timeout_seconds": 1200,
                "called": False,
                "accepted": False,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            },
        ],
    )
    write_json(
        packet_dir / "research.schema.json",
        {
            "type": "object",
            "additionalProperties": True,
            "properties": {},
        },
    )
    write_json(
        packet_dir / "launch-config.json",
        {
            "schema_version": 1,
            "packet_id": "B01-W01",
            "role": "research-worker",
            "branch": "preparedness-research-fixture",
            "worktree": ROOT.as_posix(),
            "schema_name": "research.schema.json",
            "output_name": "research.json",
            "telemetry_script": (ROOT / "skills" / "goal-branch-orchestrator" / "scripts" / "extract_telemetry.py").as_posix(),
            "attempts": [
                {
                    "alias": "codex-research",
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "command": "codex --search exec --ephemeral -m gpt-5.4 -s read-only",
                    "rendered_command": "codex --search exec --ephemeral -m gpt-5.4 -s read-only",
                    "route_policy_version": "goal-route-policy-v2",
                    "telemetry_capability": {
                        "token_usage": "best_effort",
                        "source": "provider_or_harness_output",
                    },
                    "sandbox": "read-only",
                    "timeout_seconds": 1200,
                    "event_logs": ["events-primary.jsonl"],
                    "probe_logs": [],
                },
                {
                    "alias": "codex-research-mini",
                    "provider": "codex",
                    "model": "gpt-5.4-mini",
                    "command": "codex --search exec --ephemeral -m gpt-5.4-mini -s read-only",
                    "rendered_command": "codex --search exec --ephemeral -m gpt-5.4-mini -s read-only",
                    "route_policy_version": "goal-route-policy-v2",
                    "telemetry_capability": {
                        "token_usage": "best_effort",
                        "source": "provider_or_harness_output",
                    },
                    "sandbox": "read-only",
                    "timeout_seconds": 1200,
                    "event_logs": ["events-fallback.jsonl"],
                    "probe_logs": [],
                },
            ],
        },
    )
    write_json(packet_dir / "research.json", research)
    write_json(packet_dir / "telemetry.json", telemetry_data)
    write_json(
        packet_dir / "launcher-state.json",
        {
            "schema_version": 1,
            "packet_id": "B01-W01",
            "terminal_state": "pass",
            "events": [
                {"attempt_index": 0, "state": "pass", "returncode": 0, "dirty": False, "output_nonempty": True}
            ],
        },
    )
    packet_summary = {
        "schema_version": 1,
        "packet_id": "B01-W01",
        "role": "research-worker",
        "output_path": "research.json",
        "output_exists": True,
        "output_status": "pass",
        "telemetry_path": "telemetry.json",
        "telemetry_exists": True,
        "launcher_state_path": "launcher-state.json",
        "launcher_state_exists": True,
        "terminal_state": "pass",
        "next_action": "validate_and_collect",
        "attempts": [{"attempt_index": 0, "alias": "codex-research", "state": "pass", "failure_class": "none"}],
    }
    write_json(packet_dir / "packet.summary.json", packet_summary)
    attempt_dir = packet_dir / "attempts" / "attempt-001"
    write_json(attempt_dir / "research.json", research)
    write_json(attempt_dir / "telemetry.json", telemetry_data)
    write_json(attempt_dir / "launcher-state.json", read_json(packet_dir / "launcher-state.json"))
    write_json(
        attempt_dir / "packet.summary.json",
        {
            **packet_summary,
            "attempt_snapshot": "attempt-001",
        },
    )
    status = branch_status(bundle, research)
    write_json(bundle / "branches" / "B01.status.json", status)
    write_json(
        bundle / "branches" / "B01.review-waiver.json",
        {
            "schema_version": 1,
            "kind": "review-waiver",
            "branch_id": "B01",
            "branch_status": status["status"],
            "review_status": "missing",
            "review_path": "branches/B01.review.json",
            "reviewer_launch_skipped": True,
            "reason_code": "branch_non_pass_terminal_blocker",
            "reason": "Static partial fixture intentionally omits reviewer launch.",
            "validated_by": "check_preparedness_fixtures.py",
            "blockers": status["blockers"],
            "branch_status_path": "branches/B01.status.json",
        },
    )


def validate_branch(bundle: Path, *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/validate_branch_status.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--status",
            (bundle / "branches" / "B01.status.json").as_posix(),
            "--branch-id",
            "B01",
            "--branch",
            "preparedness-research-fixture",
            "--worktree",
            ROOT.as_posix(),
            "--json",
        ],
        expect=expect,
    )


def write_review_gate_variant(bundle: Path, packet_id: str, *, tier: str | None = None, diff_stats: dict | None = None) -> Path:
    gate = json.loads((bundle / "branches" / "B01.pre_review_gate.json").read_text(encoding="utf-8"))
    gate["review_packet_id"] = packet_id
    if tier is not None:
        gate["review_tier"] = tier
    if diff_stats is not None:
        gate["diff_stats"] = diff_stats
    path = bundle / "branches" / f"B01.{packet_id}.pre_review_gate.json"
    write_json(path, gate)
    return path


def write_mergeable_reviewer_packet(bundle: Path, packet_id: str, semantic_hashes: dict) -> None:
    packet_dir = bundle / "reviewers" / packet_id
    route = read_json(packet_dir / "route.json")
    aliases = [
        item
        for item in route.get("selected_ladder", [])
        if isinstance(item, str) and item.strip()
    ] or ["gpt-5.4"]
    attempts = []
    for index, alias in enumerate(aliases):
        attempts.append(
            {
                "alias": alias,
                "provider": "codex",
                "model": alias,
                "effort": "high",
                "command": f"codex exec -m {alias} --json",
                "timeout_seconds": 1800,
                "called": index == 0,
                "accepted": index == 0,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            }
        )
    write_json(
        packet_dir / "review.json",
        {
            "packet_id": packet_id,
            "role": "reviewer",
            "verdict": "mergeable",
            "findings": [],
            "finding_classes": ["no_issue"],
            "commands_run": ["git diff --check main...HEAD"],
            "verification_gaps": [],
            "residual_risks": [],
            "semantic_input_hashes": semantic_hashes,
            "reuse_policy": {
                "mode": "new",
                "accepted": False,
                "semantic_hashes_match": False,
                "source_review_path": None,
                "source_telemetry_path": None,
            },
            "summary": "Deterministic mergeable reviewer fixture.",
        },
    )
    write_json(
        packet_dir / "telemetry.json",
        telemetry(packet_id, "reviewer", "review.json", accepted_alias=aliases[0], attempts=attempts),
    )


def assert_reviewer_route(packet_root: Path, packet_id: str, expected: list[str]) -> None:
    route = json.loads((packet_root / packet_id / "route.json").read_text(encoding="utf-8"))
    actual = route.get("selected_ladder")
    if actual != expected:
        raise SystemExit(f"{packet_id} route mismatch: expected {expected}, got {actual}")


def create_runtime_packet(
    *,
    role: str,
    packet_id: str,
    branch: str,
    out_dir: Path,
    task_file: Path,
    worktree: Path = ROOT,
    owned_files: list[str] | None = None,
    context_files: list[Path] | None = None,
    manifest: Path | None = None,
    pre_review_gate: Path | None = None,
    worker_route: list[str] | None = None,
    selection_reason: str | None = None,
    model_catalog: Path | None = None,
    extra_args: list[str] | None = None,
    expect: int = 0,
) -> subprocess.CompletedProcess[str]:
    return run_runtime_packet(
        root=ROOT,
        script="skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
        role=role,
        packet_id=packet_id,
        branch=branch,
        worktree=worktree,
        out_dir=out_dir,
        task_file=task_file,
        owned_files=owned_files,
        context_files=context_files,
        manifest=manifest,
        pre_review_gate=pre_review_gate,
        worker_route=worker_route,
        selection_reason=selection_reason,
        model_catalog=model_catalog,
        extra_args=extra_args,
        expect=expect,
    )


def branch_fixture(branch_id: str, owned_path: str, *, depends_on: list[str] | None = None) -> dict:
    return {
        "id": branch_id,
        "title": f"Topology {branch_id}",
        "objective": f"Topology fixture for {branch_id}.",
        "branch_name": f"topology-{branch_id.lower()}",
        "worktree_path": f".worktrees/topology-{branch_id.lower()}",
        "max_active_worker_packets": 1,
        "worker_serial_reasons": ["Single topology fixture worker."],
        "depends_on": depends_on or [],
        "work_items": [
            {
                "id": "W01",
                "objective": "Topology worker.",
                "owned_paths": [owned_path],
                "context_files": ["README.md"],
                "verification": ["git diff --check main...HEAD"],
                "dod": ["topology fixture validates"],
            }
        ],
    }


def run_topology_fixtures(tmp_path: Path) -> None:
    default_rationale_brief = {
        "job_id": "topology-default-rationale",
        "base_ref": "main",
        "max_active_branch_agents": 2,
        "parallelization_rationale": "Generic rationale must not excuse under-capacity.",
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "skills/goal-preflight/SKILL.md"),
        ],
    }
    brief_path = tmp_path / "topology-default-rationale.json"
    write_json(brief_path, default_rationale_brief)
    topology_default_bundle = tmp_path / "topology-default-rationale"
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            topology_default_bundle.as_posix(),
        ]
    )
    default_manifest = read_json(topology_default_bundle / "job.manifest.json")
    if not default_manifest.get("parallelization", {}).get("serial_reasons"):
        raise SystemExit("default rationale topology fixture did not synthesize serial_reasons")

    one_worker_brief = {
        "job_id": "topology-one-worker",
        "base_ref": "main",
        "serial_reasons": ["Single branch fixture."],
        "branches": [
            {
                **branch_fixture("B01", "README.md"),
                "max_active_worker_packets": 4,
                "worker_serial_reasons": [],
            }
        ],
    }
    brief_path = tmp_path / "topology-one-worker.json"
    write_json(brief_path, one_worker_brief)
    one_worker_bundle = tmp_path / "topology-one-worker"
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            one_worker_bundle.as_posix(),
        ]
    )
    one_worker_manifest = read_json(one_worker_bundle / "job.manifest.json")
    if not one_worker_manifest["branches"][0]["worker_parallelism"].get("serial_reasons"):
        raise SystemExit("one-worker topology fixture did not synthesize worker serial_reasons")

    serial_chain_brief = {
        "job_id": "topology-serial-chain",
        "base_ref": "main",
        "goal": "Validate serial branch topology warnings.",
        "source_summary": "A three-branch dependency chain is used to prove low branch utilization reporting.",
        "required_evidence": ["The bundle linter reports a low-utilization warning."],
        "final_dod": ["Readiness reports max_ready_width=1 for the serial DAG."],
        "parallelization_rationale": "The branches can run concurrently as a saturated pool.",
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "skills/goal-preflight/SKILL.md", depends_on=["B01"]),
            branch_fixture("B03", "skills/goal-main-orchestrator/SKILL.md", depends_on=["B02"]),
        ],
    }
    brief_path = tmp_path / "topology-serial-chain.json"
    write_json(brief_path, serial_chain_brief)
    serial_chain_bundle = tmp_path / "topology-serial-chain"
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            serial_chain_bundle.as_posix(),
        ]
    )
    serial_manifest = read_json(serial_chain_bundle / "job.manifest.json")
    serial_reasons = serial_manifest.get("parallelization", {}).get("serial_reasons", [])
    if not serial_reasons or not any("dependency" in item for item in serial_reasons):
        raise SystemExit(f"serial-chain topology fixture did not synthesize dependency serial_reasons: {serial_reasons!r}")
    serial_waves = serial_manifest.get("waves", [])
    serial_wave_by_branch = {
        branch_id: {"wave": wave.get("id"), "dependency_level": wave.get("dependency_level")}
        for wave in serial_waves
        for branch_id in wave.get("branches", [])
    }
    if serial_wave_by_branch.get("B01", {}).get("wave") == serial_wave_by_branch.get("B02", {}).get("wave"):
        raise SystemExit(f"dependency-aware waves should not put dependent branches in the same wave: {serial_waves!r}")
    if serial_wave_by_branch.get("B03", {}).get("dependency_level") != 3:
        raise SystemExit(f"dependency-aware waves should preserve topological dependency levels: {serial_waves!r}")
    serial_lint = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/lint_goal_bundle.py",
                "--bundle-dir",
                serial_chain_bundle.as_posix(),
                "--no-write",
            ]
        ).stdout
    )
    if not any("max ready width" in item.get("message", "") for item in serial_lint.get("defects", [])):
        raise SystemExit(f"serial branch DAG should produce a low-utilization lint warning: {serial_lint!r}")
    serial_readiness = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/render_goal_bootloader.py",
                "--bundle-dir",
                serial_chain_bundle.as_posix(),
                "--readiness",
                "--json",
            ]
        ).stdout
    )
    if serial_readiness.get("branch_utilization", {}).get("max_ready_width") != 1:
        raise SystemExit(f"readiness should report serial branch max_ready_width=1: {serial_readiness!r}")
    if not any(item.get("code") == "parallelization_rationale_dag_mismatch" for item in serial_readiness.get("warnings", [])):
        raise SystemExit(f"readiness should warn when rationale overstates serial DAG parallelism: {serial_readiness!r}")

    overlap_brief = {
        "job_id": "topology-cross-overlap",
        "base_ref": "main",
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "README.md"),
        ],
    }
    overlap_brief_path = tmp_path / "topology-cross-overlap.json"
    overlap_bundle = tmp_path / "topology-cross-overlap"
    write_json(overlap_brief_path, overlap_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            overlap_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            overlap_bundle.as_posix(),
        ]
    )
    result = run(
        ["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", overlap_bundle.as_posix(), "--no-write"],
        expect=1,
    )
    assert_contains(result.stdout, "branch owned_paths overlap", "cross-branch overlap topology fixture")

    verification_owner_brief = {
        "job_id": "verification-owner-topology",
        "base_ref": "main",
        "branches": [
            {
                **branch_fixture("B01", "src/core.py"),
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Deliberately reference future-owned verification paths.",
                        "owned_paths": ["src/core.py"],
                        "context_files": ["README.md"],
                        "verification": ["python -m src.cli", "pytest tests/test_cli.py"],
                        "dod": ["verification ownership fixture is rejected by bundle lint"],
                    }
                ],
            },
            branch_fixture("B02", "src/cli.py"),
            branch_fixture("B03", "tests/test_cli.py"),
        ],
    }
    verification_owner_brief_path = tmp_path / "verification-owner-topology.json"
    verification_owner_bundle = tmp_path / "verification-owner-topology"
    write_json(verification_owner_brief_path, verification_owner_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            verification_owner_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            verification_owner_bundle.as_posix(),
        ]
    )
    owner_result = run(
        ["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", verification_owner_bundle.as_posix(), "--no-write"],
        expect=1,
    )
    assert_all_contains(
        owner_result.stdout,
        [
            "references python module src.cli",
            "owned by B02",
            "references path tests/test_cli.py",
            "owned by B03",
        ],
        "verification ownership topology fixture",
    )

    dependency_context_brief = {
        "job_id": "dependency-context-topology",
        "base_ref": "main",
        "goal": "Validate dependency context metadata for dependent branches without direct context files.",
        "source_summary": "The fixture uses README-backed topology branches and checks that dependency artifacts are named as the runtime context source.",
        "required_evidence": ["The manifest contains dependency_context_reason for the dependent branch."],
        "final_dod": ["The dependency-context fixture bundle passes preflight bundle lint."],
        "max_active_branch_agents": 1,
        "serial_reasons": ["Sequential dependency-context fixture."],
        "branches": [
            branch_fixture("B01", "src/base.py"),
            {
                **branch_fixture("B02", "src/followup.py", depends_on=["B01"]),
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Use dependency status artifacts instead of direct context files.",
                        "owned_paths": ["src/followup.py"],
                        "context_files": [],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["dependency context reason is explicit in the manifest"],
                    }
                ],
            },
        ],
    }
    dependency_context_brief_path = tmp_path / "dependency-context-topology.json"
    dependency_context_bundle = tmp_path / "dependency-context-topology"
    write_json(dependency_context_brief_path, dependency_context_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            dependency_context_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            dependency_context_bundle.as_posix(),
        ]
    )
    dependency_manifest = read_json(dependency_context_bundle / "job.manifest.json")
    dependency_branch = next(branch for branch in dependency_manifest["branches"] if branch["id"] == "B02")
    if not dependency_branch.get("dependency_context_reason"):
        raise SystemExit("dependent branch without context_files should get dependency_context_reason")
    assert_contains(
        (dependency_context_bundle / "branches" / "B02.prompt.md").read_text(encoding="utf-8"),
        "Dependency context:",
        "dependency context branch prompt",
    )
    run(["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", dependency_context_bundle.as_posix(), "--no-write"])


def run_preflight_brief_lint_fixtures(tmp_path: Path) -> None:
    valid_brief = {
        "job_id": "brief-lint-valid",
        "base_ref": "main",
        "goal": "Validate the preflight brief linter with a concrete deterministic fixture.",
        "source_summary": "This fixture exercises the brief linter against an existing repository file and exact commands.",
        "required_evidence": ["The linter reports pass for concrete source-backed branch and worker evidence."],
        "final_dod": ["The generated linter result is pass with no major or critical defects."],
        "max_active_branch_agents": 1,
        "serial_reasons": ["Single-branch lint fixture."],
        "branches": [
            {
                "id": "B01",
                "objective": "Validate the brief lint happy path with concrete commands.",
                "max_active_worker_packets": 1,
                "worker_serial_reasons": ["Single lint fixture work item."],
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Read the README and validate deterministic lint behavior.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "verification": ["python3 -m py_compile skills/goal-preflight/scripts/lint_preflight_brief.py"],
                        "dod": ["brief linter accepts concrete paths commands and falsifiable evidence"],
                    }
                ],
            }
        ],
    }
    valid_path = tmp_path / "brief-lint-valid.json"
    write_json(valid_path, valid_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            valid_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
        ]
    )
    defaulted_brief = {
        "job_id": "brief-lint-defaults",
        "base_ref": "main",
        "goal": "Validate deterministic preflight defaults for underfilled worker topology.",
        "source_summary": "This fixture omits policy and serial reason fields so scripts must synthesize deterministic defaults.",
        "required_evidence": ["The linter accepts deterministic policy and serial reason defaults from normalization."],
        "final_dod": ["The generated brief lint result is pass without manual repair fields."],
        "branches": [
            {
                "id": "B01",
                "objective": "Validate dependency-based worker serial reason defaults.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Read the README and make no changes.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["README context remains available for deterministic checks."],
                    },
                    {
                        "id": "W02",
                        "depends_on": ["W01"],
                        "objective": "Read the same README after the first packet.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["Second packet dependency is represented deterministically."],
                    },
                ],
            },
            {
                "id": "B02",
                "objective": "Validate single-worker serial reason defaults.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Read the branch skill documentation summary.",
                        "owned_paths": ["skills/goal-branch-orchestrator/SKILL.md"],
                        "context_files": ["skills/goal-branch-orchestrator/SKILL.md"],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["Single worker branch is accepted with deterministic serial reason."],
                    }
                ],
            },
        ],
    }
    defaulted_path = tmp_path / "brief-lint-defaults.json"
    write_json(defaulted_path, defaulted_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            defaulted_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
        ]
    )
    combined_policy_brief = {
        **valid_brief,
        "job_id": "brief-lint-combined-policy",
        "artifact_policy": "Keep generated bundle artifacts under the selected orchestration directory.",
        "cleanup_policy": "On partial, blocked, or failed runs, preserve branch worktrees, packets, and logs for inspection.",
    }
    combined_policy_path = tmp_path / "brief-lint-combined-policy.json"
    write_json(combined_policy_path, combined_policy_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            combined_policy_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--json",
        ]
    )
    invalid_brief = {
        **valid_brief,
        "job_id": "brief-lint-invalid",
        "goal": "TODO",
        "artifact_policy": "TODO",
        "cleanup_policy": "clean up later",
        "branches": [
            {
                "id": "B01",
                "objective": "Fix stuff",
                "max_active_worker_packets": 1,
                "worker_serial_reasons": ["Single lint fixture work item."],
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "TODO",
                        "owned_paths": ["README.md"],
                        "context_files": ["missing-lint-context.md"],
                        "verification": [],
                        "dod": ["done"],
                    }
                ],
            }
        ],
    }
    invalid_path = tmp_path / "brief-lint-invalid.json"
    write_json(invalid_path, invalid_brief)
    invalid = run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            invalid_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
        ],
        expect=1,
    )
    assert_all_contains(
        invalid.stdout,
        [
            "contains placeholder text",
            "concrete top-level goal",
            "context file must already exist",
            "future writable outputs in owned_paths",
            "must include at least one exact verification command",
        ],
        "invalid brief lint fixture",
    )
    owned_only_missing_brief = {
        "job_id": "brief-lint-owned-missing",
        "base_ref": "main",
        "goal": "Validate brief lint behavior for writable-owned path targets.",
        "source_summary": "This fixture confirms expected new file paths do not have to pre-exist in the repo.",
        "required_evidence": ["The linter accepts missing owned paths when they are declared as writable targets."],
        "final_dod": ["The owned path is not required to pre-exist and is treated as a writable target."],
        "branches": [
            {
                "id": "B01",
                "objective": "Validate owned paths are treated as writable targets and do not precondition file existence.",
                "max_active_worker_packets": 1,
                "worker_serial_reasons": ["Single lint fixture work item."],
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Read the repository and propose a new file.",
                        "owned_paths": ["owned_target_fixture_does_not_exist.md"],
                        "context_files": ["README.md"],
                        "verification": ["python3 -m py_compile skills/goal-preflight/scripts/lint_preflight_brief.py"],
                        "dod": ["Missing owned path is allowed because the worker owns it."],
                    }
                ],
            }
        ],
    }
    owned_only_missing_path = tmp_path / "brief-lint-owned-missing.json"
    write_json(owned_only_missing_path, owned_only_missing_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            owned_only_missing_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--json",
        ]
    )

    exact_source_missing_brief = {
        **valid_brief,
        "job_id": "brief-lint-exact-source-missing",
        "goal": "Use the exact FT10 instance provided in this brief and solve it within the chosen runtime cap.",
        "source_summary": "The exact operation list is intentionally absent so source-fidelity lint must reject this fixture.",
        "runtime_cap": None,
    }
    exact_source_missing_path = tmp_path / "brief-lint-exact-source-missing.json"
    write_json(exact_source_missing_path, exact_source_missing_brief)
    exact_source_missing = run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            exact_source_missing_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--json",
        ],
        expect=1,
    )
    assert_all_contains(
        exact_source_missing.stdout,
        [
            "exact source/instance/list is referenced",
            "no concrete runtime_cap",
        ],
        "exact source/runtime cap brief lint fixture",
    )

    exact_source_attached_brief = {
        **valid_brief,
        "job_id": "brief-lint-exact-source-attached",
        "goal": "Use the exact fixture instance provided in this brief attachment and finish within the chosen runtime cap.",
        "source_summary": "README.md is attached as the source-of-truth fixture payload for this lint check.",
        "source_attachments": [{"path": "README.md", "label": "Exact fixture source", "kind": "benchmark-data"}],
        "runtime_cap": {"seconds": 30, "cli_flag": "--time-limit-seconds 30", "require_cap_reporting": True},
    }
    exact_source_attached_path = tmp_path / "brief-lint-exact-source-attached.json"
    write_json(exact_source_attached_path, exact_source_attached_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            exact_source_attached_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--json",
        ]
    )
    invalid_runtime_cap_brief = {
        **valid_brief,
        "job_id": "brief-lint-invalid-runtime-cap",
        "runtime_cap": [30],
    }
    invalid_runtime_cap_path = tmp_path / "brief-lint-invalid-runtime-cap.json"
    write_json(invalid_runtime_cap_path, invalid_runtime_cap_brief)
    invalid_runtime_cap = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/lint_preflight_brief.py",
                "--brief",
                invalid_runtime_cap_path.as_posix(),
                "--repo-root",
                ROOT.as_posix(),
                "--json",
            ],
            expect=1,
        ).stdout
    )
    if not any(item.get("path") == "$.runtime_cap" for item in invalid_runtime_cap.get("defects", [])):
        raise SystemExit(f"runtime_cap normalization defects should be path-specific: {invalid_runtime_cap!r}")


def amendment_branch(branch_id: str, owned_path: str, *, depends_on: list[str] | None = None, branch_name: str | None = None) -> dict:
    return {
        "id": branch_id,
        "title": f"Amendment {branch_id}",
        "objective": f"Amendment fixture for {branch_id}.",
        "scope": "Future unstarted work only.",
        "branch_name": branch_name or f"amendment-{branch_id.lower()}",
        "worktree_path": f".worktrees/amendment-{branch_id.lower()}",
        "depends_on": depends_on or [],
        "max_active_worker_packets": 1,
        "worker_serial_reasons": ["Single amendment fixture worker."],
        "work_items": [
            {
                "id": "W01",
                "objective": "Amendment worker.",
                "owned_paths": [owned_path],
                "context_files": ["README.md"],
                "verification": ["git diff --check main...HEAD"],
                "dod": ["amendment fixture validates"],
            }
        ],
        "tests": ["git diff --check main...HEAD"],
        "dod": ["amendment branch validates"],
    }


def amendment_proposal(amendment_id: str, job_id: str, operations: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": job_id,
        "rationale": "Preparedness fixture exercises deterministic amendment validation.",
        "operations": operations,
    }


def create_amendment_bundle(tmp_path: Path, name: str) -> Path:
    brief = {
        "job_id": name,
        "base_ref": "main",
        "goal": "Exercise deterministic amendment validation against future unstarted branches.",
        "source_summary": "The amendment fixture uses generated branch prompts and README-backed work items.",
        "required_evidence": ["Amendment validation keeps active and terminal branch evidence immutable."],
        "final_dod": ["Generated amendment fixture bundle passes preflight lint before amendment operations."],
        "max_active_branch_agents": 1,
        "serial_reasons": ["Amendment fixture intentionally runs one branch at a time."],
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "skills/goal-preflight/SKILL.md", depends_on=["B01"]),
        ],
    }
    brief_path = tmp_path / f"{name}.json"
    bundle = tmp_path / name
    write_json(brief_path, brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            bundle.as_posix(),
        ]
    )
    run(["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", bundle.as_posix(), "--no-write"])
    return bundle


def validate_amendment(
    bundle: Path,
    proposal_path: Path,
    *,
    amendment_id: str,
    expect: int = 0,
    active: list[str] | None = None,
    terminal: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "python3",
        "skills/goal-plan-amender/scripts/validate_manifest_amendment.py",
        "--manifest",
        (bundle / "job.manifest.json").as_posix(),
        "--proposal",
        proposal_path.as_posix(),
        "--output",
        (bundle / "amendments" / f"{amendment_id}.validation.json").as_posix(),
        "--json",
    ]
    for branch_id in active or []:
        command.extend(["--active-branch", branch_id])
    for branch_id in terminal or []:
        command.extend(["--terminal-branch", branch_id])
    return run(command, expect=expect)


def create_amendment_decision(
    bundle: Path,
    amendment_id: str,
    *,
    decision: str = "launch",
    reason_code: str = "operator_requested",
    reason: str = "Preparedness fixture records a deterministic amender launch decision.",
    terminal: list[str] | None = None,
) -> None:
    command = [
        "python3",
        "skills/goal-plan-amender/scripts/create_amendment_decision.py",
        "--manifest",
        (bundle / "job.manifest.json").as_posix(),
        "--amendment-id",
        amendment_id,
        "--decision",
        decision,
        "--reason-code",
        reason_code,
        "--reason",
        reason,
    ]
    for branch_id in terminal or ["B01"]:
        command.extend(["--terminal-branch", branch_id])
    run(command)


def write_amender_telemetry(bundle: Path, amendment_id: str, selected_ladder: list[str]) -> None:
    attempts = []
    for index, alias in enumerate(selected_ladder):
        attempts.append(
            {
                "alias": alias,
                "provider": "codex",
                "model": alias,
                "effort": None,
                "command": f"codex exec --ephemeral -m {alias} -s read-only",
                "timeout_seconds": 1200,
                "called": index == 0,
                "accepted": index == 0,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            }
        )
    write_json(
        bundle / "amendments" / f"{amendment_id}.packet" / "telemetry.json",
        telemetry(
            amendment_id,
            "plan_amender",
            f"../{amendment_id}.proposal.json",
            accepted_alias=selected_ladder[0],
            attempts=attempts,
        ),
    )


def run_python_interpreter_normalization_fixture(tmp_path: Path) -> None:
    brief = {
        "job_id": "python-interpreter-normalization",
        "goal": "Normalize Python verification commands to the available interpreter.",
        "source_summary": "Fixture checks deterministic verification command normalization.",
        "required_evidence": ["Manifest verification command uses python3."],
        "final_dod": ["Generated bundle uses the detected Python interpreter."],
        "branches": [
            {
                "id": "B01",
                "objective": "Create a tiny pytest fixture.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Add a package module and pytest file.",
                        "owned_paths": ["ft10_solver/instance.py", "tests/test_python_interpreter_normalization.py"],
                        "verification": [
                            "python -m pytest tests/test_python_interpreter_normalization.py -q && "
                            "python -m mypy ft10_solver > typecheck.log"
                        ],
                        "dod": ["pytest command uses the usable Python interpreter"],
                    }
                ],
            }
        ],
    }
    brief_path = tmp_path / "python-interpreter-normalization.json"
    bundle = tmp_path / "python-interpreter-normalization-bundle"
    write_json(brief_path, brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            bundle.as_posix(),
        ]
    )
    manifest = read_json(bundle / "job.manifest.json")
    work_item = manifest["branches"][0]["work_items"][0]
    command = work_item["verification"][0]
    expected_command = (
        "python3 -m pytest tests/test_python_interpreter_normalization.py -q && "
        "python -m mypy ft10_solver > typecheck.log"
    )
    if command != expected_command:
        raise SystemExit(f"python interpreter normalization fixture mismatch: {command!r}")
    if "ft10_solver/__init__.py" in work_item.get("owned_paths", []):
        raise SystemExit(f"package skeleton should be context-only unless explicitly owned: {work_item.get('owned_paths')!r}")
    assert_contains((bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8"), command, "python interpreter prompt")


def run_package_skeleton_context_fixture(tmp_path: Path) -> None:
    package_repo = tmp_path / "package-skeleton-context"
    package_repo.mkdir(parents=True, exist_ok=True)
    (package_repo / "src").mkdir()
    (package_repo / "src" / "fixture_pkg").mkdir()
    (package_repo / "src" / "fixture_pkg" / "__init__.py").write_text("# fixture package\n", encoding="utf-8")
    (package_repo / "src" / "fixture_pkg" / "worker.py").write_text("# fixture module\n", encoding="utf-8")
    (package_repo / "README.md").write_text("Package skeleton fixture.\n", encoding="utf-8")
    brief = {
        "job_id": "package-skeleton-context-fixture",
        "base_ref": "main",
        "goal": "Validate package skeletons are added as context only when pre-existing.",
        "source_summary": "Worker-owned modules in existing packages should infer parent __init__.py only as context evidence.",
        "required_evidence": ["Generated manifest lists the existing package skeleton in context_files and not owned_paths."],
        "final_dod": ["Package skeleton context inference should preserve deterministic ownership separation."],
        "max_active_branch_agents": 1,
        "branches": [
            {
                "id": "B01",
                "objective": "Validate existing package skeleton context inference.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Create fixture file edits under an existing package.",
                        "owned_paths": ["src/fixture_pkg/worker.py"],
                        "context_files": ["README.md"],
                        "verification": ["python3 -m py_compile src/fixture_pkg/worker.py"],
                        "dod": ["package skeleton is context-only inference"],
                    },
                    {
                        "id": "W02",
                        "objective": "Create a second fixture file edit under the same existing package.",
                        "owned_paths": ["src/fixture_pkg/second_worker.py"],
                        "context_files": ["README.md"],
                        "verification": ["python3 -m py_compile src/fixture_pkg/second_worker.py"],
                        "dod": ["package skeleton context is available to every worker that edits the package"],
                    }
                ],
            }
        ],
    }
    brief_path = tmp_path / "package-skeleton-context.json"
    bundle = tmp_path / "package-skeleton-context-bundle"
    write_json(brief_path, brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            package_repo.as_posix(),
            "--out-dir",
            bundle.as_posix(),
        ]
    )
    manifest = read_json(bundle / "job.manifest.json")
    for work_item in manifest["branches"][0]["work_items"]:
        if "src/fixture_pkg/__init__.py" not in work_item.get("context_files", []):
            raise SystemExit(f"missing inferred package skeleton context file: {work_item.get('context_files')!r}")
        if "src/fixture_pkg/__init__.py" in work_item.get("owned_paths", []):
            raise SystemExit(f"inferred package skeleton was incorrectly owned: {work_item.get('owned_paths')!r}")


def run_amender_route_selection_fixtures(tmp_path: Path) -> None:
    custom_route_bundle = create_amendment_bundle(tmp_path, "amendment-custom-route-fixture")
    create_amendment_decision(
        custom_route_bundle,
        "A002",
        reason_code="remaining_work_dod_gap",
        reason="Preparedness fixture records a deterministic premium route selection decision.",
    )
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
            "--manifest",
            (custom_route_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (custom_route_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A002",
            "--terminal-branch",
            "B01",
            "--amender-route",
            "gpt-5.5,gpt-5.4",
            "--selection-reason",
            "Fixture exercises premium recovery-planning route.",
        ]
    )
    custom_route = json.loads((custom_route_bundle / "amendments" / "A002.packet" / "route.json").read_text(encoding="utf-8"))
    if custom_route.get("selected_ladder") != ["gpt-5.5", "gpt-5.4"]:
        raise SystemExit("custom amender route was not recorded")
    missing_reason = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
            "--manifest",
            (custom_route_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (custom_route_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A003",
            "--amender-route",
            "gpt-5.5",
        ],
        expect=1,
    )
    assert_contains(missing_reason.stdout, "selection-reason", "missing amender route reason")
    skip_launch_reason = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_amendment_decision.py",
            "--manifest",
            (custom_route_bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A004",
            "--decision",
            "skip",
            "--reason-code",
            "no_eligible_branch",
            "--reason",
            "Invalid fixture skip with a launch-only reason.",
            "--terminal-branch",
            "B01",
        ],
        expect=1,
    )
    assert_contains(skip_launch_reason.stdout, "not valid for a skip decision", "skip launch-only reason fixture")


def run_negative_amendment_fixtures(tmp_path: Path) -> None:
    no_infer_bundle = create_amendment_bundle(tmp_path, "amendment-no-infer-fixture")
    no_infer_proposal = no_infer_bundle / "amendments" / "A009.proposal.json"
    write_json(
        no_infer_proposal,
        amendment_proposal(
            "A009",
            "amendment-no-infer-fixture",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment-no-infer.md")}],
        ),
    )
    no_infer = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/validate_manifest_amendment.py",
            "--manifest",
            (no_infer_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            no_infer_proposal.as_posix(),
            "--terminal-branch",
            "B01",
            "--no-infer-scheduler",
            "--json",
        ],
        expect=1,
    )
    assert_contains(no_infer.stdout, "scheduler/status inference is mandatory", "no-infer safety fixture")

    invalid_bundle = create_amendment_bundle(tmp_path, "amendment-invalid-fixture")
    write_json(invalid_bundle / "branches" / "B01.status.json", {"branch_id": "B01", "status": "blocked"})
    invalid_manifest_before = sha256_file(invalid_bundle / "job.manifest.json")
    invalid_cases = [
        (
            "A010",
            [{"op": "add_work_item_to_unstarted_branch", "branch_id": "B01", "work_item": {"id": "W02"}}],
            "protected branch ids",
            {"terminal": ["B01"]},
        ),
        (
            "A011",
            [{"op": "add_dependency_to_unstarted_branch", "branch_id": "B02", "depends_on": ["B01"]}],
            "protected branch ids",
            {"active": ["B02"]},
        ),
        (
            "A012",
            [{"op": "add_branch", "branch": amendment_branch("B02", "README.md")}],
            "duplicates existing branch",
            {},
        ),
        (
            "A013",
            [{"op": "add_branch", "branch": amendment_branch("B03", "../bad")}],
            "must not contain",
            {},
        ),
        (
            "A014",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment.md", branch_name="../bad")}],
            "safe git branch name",
            {},
        ),
        (
            "A015",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment.md", depends_on=["B99"])}],
            "unknown branch",
            {},
        ),
        (
            "A016",
            [
                {
                    "op": "add_branch",
                    "branch": {
                        **amendment_branch("B03", "docs/amendment.md"),
                        "max_active_worker_packets": 4,
                        "worker_serial_reasons": [],
                        "work_items": [
                            {
                                "id": "W01",
                                "objective": "First overlapping item.",
                                "owned_paths": ["docs/amendment.md"],
                                "context_files": ["README.md"],
                                "verification": ["git diff --check main...HEAD"],
                                "dod": ["first item validates"],
                            },
                            {
                                "id": "W02",
                                "objective": "Second overlapping item.",
                                "owned_paths": ["docs/amendment.md"],
                                "context_files": ["README.md"],
                                "verification": ["git diff --check main...HEAD"],
                                "dod": ["second item validates"],
                            },
                        ],
                    },
                }
            ],
            "owned_paths overlap",
            {},
        ),
        (
            "A017",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment.md", depends_on=["B01"])}],
            "depends_on non-pass terminal branch ids",
            {"terminal": ["B01"]},
        ),
    ]
    for amendment_id, operations, expected, extra in invalid_cases:
        path = invalid_bundle / "amendments" / f"{amendment_id}.proposal.json"
        write_json(path, amendment_proposal(amendment_id, "amendment-invalid-fixture", operations))
        result = validate_amendment(
            invalid_bundle,
            path,
            amendment_id=amendment_id,
            expect=1,
            active=extra.get("active", []),
            terminal=extra.get("terminal", []),
        )
        assert_contains(result.stdout, expected, f"invalid amendment {amendment_id}")
        if invalid_manifest_before != sha256_file(invalid_bundle / "job.manifest.json"):
            raise SystemExit("invalid amendment validation mutated the live manifest")

    stale_bundle = create_amendment_bundle(tmp_path, "amendment-stale-status-fixture")
    stale_before = sha256_file(stale_bundle / "job.manifest.json")
    write_json(stale_bundle / "branches" / "B02.status.json", {"branch_id": "B02", "status": "pass"})
    write_json(
        stale_bundle / "schedulers" / "main.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "main-branch-pool",
            "scheduler_path": "schedulers/main.scheduler.json",
            "manifest_sha256": stale_before,
            "capacity": 1,
            "item_ids": ["B01", "B02"],
            "events": [
                scheduler_event(1, "ready", id="B01"),
                scheduler_event(2, "launch", id="B01"),
                scheduler_event(3, "finish", id="B01", status="pass"),
                scheduler_event(4, "close", id="B01"),
                scheduler_event(5, "refill", eligible_ids=["B02"]),
                scheduler_event(6, "launch", id="B02"),
            ],
        },
    )
    stale_proposal = stale_bundle / "amendments" / "A018.proposal.json"
    write_json(
        stale_proposal,
        amendment_proposal(
            "A018",
            "amendment-stale-status-fixture",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment-stale.md")}],
        ),
    )
    stale_result = validate_amendment(stale_bundle, stale_proposal, amendment_id="A018", expect=1)
    assert_contains(stale_result.stdout, "stale status overlap", "stale status overlap fixture")
    if stale_before != sha256_file(stale_bundle / "job.manifest.json"):
        raise SystemExit("stale status validation mutated the live manifest")

    missing_packet_bundle = create_amendment_bundle(tmp_path, "amendment-missing-packet-fixture")
    missing_packet_proposal = missing_packet_bundle / "amendments" / "A019.proposal.json"
    write_json(
        missing_packet_proposal,
        amendment_proposal(
            "A019",
            "amendment-missing-packet-fixture",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment-missing-packet.md")}],
        ),
    )
    create_amendment_decision(
        missing_packet_bundle,
        "A019",
        reason_code="remaining_work_dod_gap",
        reason="Preparedness fixture records a launch decision without packet validation.",
    )
    validate_amendment(missing_packet_bundle, missing_packet_proposal, amendment_id="A019", terminal=["B01"])
    missing_packet_before = sha256_file(missing_packet_bundle / "job.manifest.json")
    missing_packet_apply = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/apply_manifest_amendment.py",
            "--manifest",
            (missing_packet_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            missing_packet_proposal.as_posix(),
            "--validation",
            (missing_packet_bundle / "amendments" / "A019.validation.json").as_posix(),
        ],
        expect=1,
    )
    assert_contains(missing_packet_apply.stdout, "missing route-bound amender packet validation", "missing packet validation apply fixture")
    if missing_packet_before != sha256_file(missing_packet_bundle / "job.manifest.json"):
        raise SystemExit("missing packet validation apply mutated the live manifest")

    drift_bundle = create_amendment_bundle(tmp_path, "amendment-active-drift-fixture")
    create_amendment_decision(
        drift_bundle,
        "A020",
        reason_code="remaining_work_dod_gap",
        reason="Preparedness fixture records a launch decision before active drift.",
    )
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
            "--manifest",
            (drift_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (drift_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A020",
            "--terminal-branch",
            "B01",
        ]
    )
    drift_proposal = drift_bundle / "amendments" / "A020.proposal.json"
    write_json(
        drift_proposal,
        amendment_proposal(
            "A020",
            "amendment-active-drift-fixture",
            [
                {
                    "op": "add_work_item_to_unstarted_branch",
                    "branch_id": "B02",
                    "work_item": {
                        "id": "W02",
                        "objective": "Follow-up work item that is safe only while B02 is unstarted.",
                        "owned_paths": ["docs/amendment-drift.md"],
                        "context_files": ["README.md"],
                        "depends_on": ["W01"],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["drift work item validates"],
                    },
                }
            ],
        ),
    )
    write_amender_telemetry(drift_bundle, "A020", ["gpt-5.4", "gpt-5.4-mini"])
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/validate_amender_packet.py",
            "--manifest",
            (drift_bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A020",
            "--json",
        ]
    )
    validate_amendment(drift_bundle, drift_proposal, amendment_id="A020", terminal=["B01"])
    drift_before = sha256_file(drift_bundle / "job.manifest.json")
    write_json(
        drift_bundle / "schedulers" / "main.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "main-branch-pool",
            "scheduler_path": "schedulers/main.scheduler.json",
            "manifest_sha256": drift_before,
            "capacity": 1,
            "item_ids": ["B01", "B02"],
            "events": [
                scheduler_event(1, "ready", id="B01"),
                scheduler_event(2, "launch", id="B01"),
                scheduler_event(3, "finish", id="B01", status="pass"),
                scheduler_event(4, "close", id="B01"),
                scheduler_event(5, "refill", eligible_ids=["B02"]),
                scheduler_event(6, "launch", id="B02"),
            ],
        },
    )
    drift_apply = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/apply_manifest_amendment.py",
            "--manifest",
            (drift_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            drift_proposal.as_posix(),
            "--validation",
            (drift_bundle / "amendments" / "A020.validation.json").as_posix(),
        ],
        expect=1,
    )
    assert_contains(drift_apply.stdout, "fresh amendment validation failed", "active drift apply fixture")
    if drift_before != sha256_file(drift_bundle / "job.manifest.json"):
        raise SystemExit("active drift apply mutated the live manifest")

    rollback_bundle = create_amendment_bundle(tmp_path, "amendment-runtime-index-rollback-fixture")
    create_amendment_decision(
        rollback_bundle,
        "A021",
        reason_code="remaining_work_dod_gap",
        reason="Preparedness fixture records a launch decision before a post-index apply failure.",
    )
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
            "--manifest",
            (rollback_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (rollback_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A021",
            "--terminal-branch",
            "B01",
        ]
    )
    rollback_proposal = rollback_bundle / "amendments" / "A021.proposal.json"
    write_json(
        rollback_proposal,
        amendment_proposal(
            "A021",
            "amendment-runtime-index-rollback-fixture",
            [{"op": "add_branch", "branch": amendment_branch("B03", "docs/amendment-runtime-index-rollback.md")}],
        ),
    )
    write_amender_telemetry(rollback_bundle, "A021", ["gpt-5.4", "gpt-5.4-mini"])
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/validate_amender_packet.py",
            "--manifest",
            (rollback_bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A021",
            "--json",
        ]
    )
    validate_amendment(rollback_bundle, rollback_proposal, amendment_id="A021", terminal=["B01"])
    rollback_manifest_before = sha256_file(rollback_bundle / "job.manifest.json")
    rollback_index_before = sha256_file(rollback_bundle / "runtime.index.json")
    lint_path = rollback_bundle / "preflight.lint.json"
    if lint_path.exists():
        lint_path.unlink()
    lint_path.mkdir()
    try:
        run(
            [
                "python3",
                "skills/goal-plan-amender/scripts/apply_manifest_amendment.py",
                "--manifest",
                (rollback_bundle / "job.manifest.json").as_posix(),
                "--proposal",
                rollback_proposal.as_posix(),
                "--validation",
                (rollback_bundle / "amendments" / "A021.validation.json").as_posix(),
            ]
        )
    except IsADirectoryError:
        pass
    else:
        raise SystemExit("runtime index rollback fixture should fail after candidate runtime.index.json is written")
    if rollback_manifest_before != sha256_file(rollback_bundle / "job.manifest.json"):
        raise SystemExit("post-index failed amendment apply did not restore job.manifest.json")
    if rollback_index_before != sha256_file(rollback_bundle / "runtime.index.json"):
        raise SystemExit("post-index failed amendment apply did not restore runtime.index.json")


def run_amendment_fixtures(tmp_path: Path) -> None:
    bundle = create_amendment_bundle(tmp_path, "amendment-fixture")
    write_json(bundle / "branches" / "B01.status.json", {"branch_id": "B01", "status": "blocked"})
    amendment_lib = load_script_module("preparedness_amendment_lib", "skills/goal-plan-amender/scripts/amendment_lib.py")
    runtime_metadata_bundle = tmp_path / "amendment-runtime-metadata-fixture"
    shutil.copytree(bundle, runtime_metadata_bundle)
    runtime_metadata_manifest = read_json(runtime_metadata_bundle / "job.manifest.json")
    custom_execution_strategy = {
        "schema_version": 1,
        "id": "fixture-runtime-strategy",
        "strategy": "custom",
        "reason": "Fixture verifies amender normalization preserves bundle runtime metadata.",
        "setup_commands": ["python3 -m pip install -e ."],
        "validation_env": {"AMENDMENT_FIXTURE": "1"},
    }
    runtime_metadata_manifest["execution_strategy"] = custom_execution_strategy
    runtime_metadata_manifest["repo_status"] = {
        **runtime_metadata_manifest.get("repo_status", {}),
        "repo_root": ROOT.as_posix(),
        "repo_is_git": True,
        "base_ref_status": "exists",
    }
    runtime_goal_config = {
        "schema_version": 1,
        "models": {"lite_agent": {"alias": "lite_agent", "provider": "codex", "model": "gpt-5.4-mini"}},
        "harnesses": {"codex": {"kind": "codex", "command": "codex"}},
        "model_ladders": {"worker": ["lite_agent"], "reviewer": ["lite_agent"], "amender": ["lite_agent"], "lite": ["lite_agent"]},
        "model_policies": {
            "worker_model_policy": runtime_metadata_manifest["worker_model_policy"],
            "review_model_policy": runtime_metadata_manifest["review_model_policy"],
            "amender_model_policy": runtime_metadata_manifest["amender_model_policy"],
            "lite_model_policy": runtime_metadata_manifest["lite_model_policy"],
        },
        "telemetry": {"raw_text": False},
    }
    runtime_goal_config_check = {
        "status": "pass",
        "summary": {
            "accepted_route_count": 1,
            "rejected_route_count": 0,
            "route_verification_status": "routes_verified",
        },
    }
    write_json(runtime_metadata_bundle / "goal.config.json", runtime_goal_config)
    write_json(runtime_metadata_bundle / "goal-config.check.json", runtime_goal_config_check)
    runtime_metadata_manifest["goal_config_path"] = "goal.config.json"
    runtime_metadata_manifest["goal_config_check_path"] = "goal-config.check.json"
    runtime_metadata_manifest["goal_config_sha256"] = sha256_file(runtime_metadata_bundle / "goal.config.json").removeprefix("sha256:")
    runtime_metadata_manifest["goal_config_check_sha256"] = sha256_file(runtime_metadata_bundle / "goal-config.check.json").removeprefix("sha256:")
    normalized_metadata_manifest, _normalized_metadata_brief = amendment_lib.normalize_candidate_manifest(
        runtime_metadata_manifest,
        runtime_metadata_bundle,
    )
    if normalized_metadata_manifest.get("execution_strategy") != custom_execution_strategy:
        raise SystemExit(f"amender normalization must preserve existing execution_strategy: {normalized_metadata_manifest.get('execution_strategy')!r}")
    if normalized_metadata_manifest.get("repo_status") != runtime_metadata_manifest["repo_status"]:
        raise SystemExit(f"amender normalization must preserve repo_status: {normalized_metadata_manifest.get('repo_status')!r}")
    if normalized_metadata_manifest.get("goal_config_check_sha256") != runtime_metadata_manifest["goal_config_check_sha256"]:
        raise SystemExit(f"amender normalization must preserve goal-config check provenance: {normalized_metadata_manifest!r}")
    if normalized_metadata_manifest.get("route_contract", {}).get("route_recommendations_enabled") is not True:
        raise SystemExit(f"amender normalization must keep configured route verification: {normalized_metadata_manifest.get('route_contract')!r}")
    if "goal_config" in normalized_metadata_manifest or "goal_config_check" in normalized_metadata_manifest:
        raise SystemExit("amender normalization must not embed full goal-config artifacts in job.manifest.json")
    recommendation_result = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/recommend_amendment_decision.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--terminal-branch",
            "B01",
            "--json",
        ]
    )
    recommendation = json.loads(recommendation_result.stdout)
    if recommendation.get("decision") != "launch" or recommendation.get("reason_code") != "blocker_stalls_downstream":
        raise SystemExit("amendment recommendation fixture should launch for blocked downstream dependency")
    create_amendment_decision(
        bundle,
        "A000",
        reason_code="terminal_blocker_repair",
        reason="Preparedness fixture proves empty deterministic repair proposals become no-op records.",
    )
    no_op_result = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_blocker_repair_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A000",
            "--terminal-branch",
            "B01",
        ]
    )
    if "A000.no-op.json" not in no_op_result.stdout:
        raise SystemExit(f"empty blocker repair should return a no-op artifact path: {no_op_result.stdout!r}")
    no_op_record = read_json(bundle / "amendments" / "A000.no-op.json")
    if no_op_record.get("status") != "no_legal_future_work" or no_op_record.get("generated_operations_count") != 0:
        raise SystemExit(f"empty blocker repair should write a terminal no-op record: {no_op_record!r}")
    no_op_decision = read_json(bundle / "amendments" / "A000.decision.json")
    if no_op_decision.get("decision") != "skip" or no_op_decision.get("reason_code") != "no_legal_future_work":
        raise SystemExit(f"empty blocker repair should rewrite launch decision to skip/no_legal_future_work: {no_op_decision!r}")
    if (bundle / "amendments" / "A000.packet").exists():
        raise SystemExit("empty blocker repair must not leave a launched packet directory")
    create_amendment_decision(
        bundle,
        "A001",
        reason_code="no_eligible_branch",
        reason="Preparedness fixture launches the amender after terminal B01 evidence.",
    )
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--amendment-id",
            "A001",
            "--terminal-branch",
            "B01",
        ]
    )
    packet_dir = bundle / "amendments" / "A001.packet"
    assert_contains((packet_dir / "task.md").read_text(encoding="utf-8"), "Terminal branch ids: B01", "adaptation packet")
    assert_contains((packet_dir / "task.md").read_text(encoding="utf-8"), "Selected amender ladder: gpt-5.4, gpt-5.4-mini", "adaptation packet route")
    assert_shell_syntax(packet_dir / "launch.sh")
    route = json.loads((packet_dir / "route.json").read_text(encoding="utf-8"))
    if route.get("selected_ladder") != ["gpt-5.4", "gpt-5.4-mini"] or route.get("role") != "plan_amender":
        raise SystemExit("adaptation packet default route did not match amender_model_policy")

    proposal_path = bundle / "amendments" / "A001.proposal.json"
    write_json(
        proposal_path,
        amendment_proposal(
            "A001",
            "amendment-fixture",
            [
                {
                    "op": "add_branch",
                    "branch": {
                        **amendment_branch("B03", "skills/goal-plan-amender/SKILL.md"),
                        "recovers_from": ["B01"],
                    },
                }
            ],
        ),
    )
    write_amender_telemetry(bundle, "A001", ["gpt-5.4", "gpt-5.4-mini"])
    missing_accepted_telemetry = json.loads((packet_dir / "telemetry.json").read_text(encoding="utf-8"))
    missing_accepted_telemetry["accepted_alias"] = None
    for attempt_item in missing_accepted_telemetry.get("attempts", []):
        if isinstance(attempt_item, dict):
            attempt_item["accepted"] = False
    write_json(packet_dir / "telemetry.json", missing_accepted_telemetry)
    missing_accepted = run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/validate_amender_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A001",
            "--json",
        ],
        expect=1,
    )
    assert_contains(missing_accepted.stdout, "accepted plan-amender attempt", "amender telemetry accepted attempt fixture")
    write_amender_telemetry(bundle, "A001", ["gpt-5.4", "gpt-5.4-mini"])
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/validate_amender_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A001",
            "--json",
        ]
    )
    validate_amendment(bundle, proposal_path, amendment_id="A001", terminal=["B01"])
    old_runtime_index_sha = read_json(bundle / "job.manifest.json").get("runtime_index_sha256")
    b01_prompt_before = (bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    b02_prompt_before = (bundle / "branches" / "B02.prompt.md").read_text(encoding="utf-8")
    if not isinstance(old_runtime_index_sha, str) or old_runtime_index_sha not in b02_prompt_before:
        raise SystemExit("pre-amendment B02 prompt should embed the original runtime index sha")
    before_sha = sha256_file(bundle / "job.manifest.json")
    run(
        [
            "python3",
            "skills/goal-plan-amender/scripts/apply_manifest_amendment.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--proposal",
            proposal_path.as_posix(),
            "--validation",
            (bundle / "amendments" / "A001.validation.json").as_posix(),
        ]
    )
    if before_sha == sha256_file(bundle / "job.manifest.json"):
        raise SystemExit("accepted amendment did not update manifest")
    amended_manifest = read_json(bundle / "job.manifest.json")
    new_runtime_index_sha = amended_manifest.get("runtime_index_sha256")
    b01_prompt_after = (bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    b02_prompt_after = (bundle / "branches" / "B02.prompt.md").read_text(encoding="utf-8")
    if b01_prompt_after != b01_prompt_before:
        raise SystemExit("accepted amendment must not rewrite terminal branch prompts")
    if not isinstance(new_runtime_index_sha, str) or new_runtime_index_sha == old_runtime_index_sha:
        raise SystemExit(f"accepted amendment should update runtime_index_sha256: {new_runtime_index_sha!r}")
    if new_runtime_index_sha not in b02_prompt_after or old_runtime_index_sha in b02_prompt_after:
        raise SystemExit("accepted amendment should refresh unchanged future branch prompts with the new runtime index sha")
    for rel_path in [
        "amendments/A001.accepted.json",
        "amendments/A001.lineage.json",
        "amendments/A001.job.manifest.before.json",
        "branches/B03.prompt.md",
    ]:
        if not (bundle / rel_path).exists():
            raise SystemExit(f"accepted amendment missing artifact: {rel_path}")
    accepted = read_json(bundle / "amendments" / "A001.accepted.json")
    if accepted.get("lineage_path") != (bundle / "amendments" / "A001.lineage.json").as_posix():
        raise SystemExit(f"accepted amendment did not preserve lineage path: {accepted!r}")
    if "branches/B02.prompt.md" not in accepted.get("regenerated_prompts", []) or "branches/B01.prompt.md" in accepted.get("regenerated_prompts", []):
        raise SystemExit(f"accepted amendment should report regenerated future prompts only: {accepted!r}")
    lineage = read_json(bundle / "amendments" / "A001.lineage.json")
    stages = [item.get("stage") for item in lineage.get("stages", []) if isinstance(item, dict)]
    expected_tail = ["final_proposal", "validation", "manifest_before", "manifest_after", "acceptance"]
    if stages[-5:] != expected_tail:
        raise SystemExit(f"A001 lineage tail should record final proposal -> validation -> manifest mutation -> acceptance, got {stages!r}")
    validation_stage = next((item for item in lineage.get("stages", []) if isinstance(item, dict) and item.get("stage") == "validation"), None)
    if not isinstance(validation_stage, dict) or validation_stage.get("sha256") != sha256_file(bundle / "amendments" / "A001.validation.json"):
        raise SystemExit(f"A001 lineage validation stage must hash the written validation artifact: {lineage!r}")
    for index in range(1, len(stages)):
        current = lineage["stages"][index]
        previous = lineage["stages"][index - 1]
        if not isinstance(current, dict) or not isinstance(previous, dict):
            raise SystemExit(f"A001 lineage stage entry is invalid: {lineage.get('stages')!r}")
        current_parent = current.get("parent_sha256")
        previous_sha = previous.get("sha256")
        if current_parent is not None and current_parent != previous_sha:
            raise SystemExit(f"A001 lineage parent hash is broken at stage {current.get('stage')!r}: {current_parent!r} != {previous_sha!r}")
    run(["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", bundle.as_posix(), "--no-write"])

    run_amender_route_selection_fixtures(tmp_path)
    run_negative_amendment_fixtures(tmp_path)


def load_status_validation():
    path = ROOT / "skills" / "_goal_shared" / "scripts" / "status_validation.py"
    spec = importlib.util.spec_from_file_location("fixture_status_validation", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load status_validation.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_scheduler_fixture(
    status_validation,
    label: str,
    ledger: dict,
    *,
    expected_ids: list[str],
    dependencies: dict[str, list[str]],
    capacity: int,
    expect_pass: bool,
    manifest_path: Path | None = None,
    require_all_launched: bool = True,
) -> None:
    defects: list[str] = []
    status_validation.validate_scheduler_ledger(
        defects,
        ledger,
        f"fixture.{label}",
        scheduler_kind=ledger.get("scheduler_kind", "main-branch-pool"),
        expected_path=ledger.get("scheduler_path", "schedulers/main.scheduler.json"),
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=capacity,
        manifest_path=manifest_path,
        require_all_launched=require_all_launched,
    )
    if expect_pass and defects:
        raise SystemExit(f"{label} scheduler fixture should pass, got defects: {defects}")
    if not expect_pass and not defects:
        raise SystemExit(f"{label} scheduler fixture should fail")


def run_scheduler_fixtures(manifest_path: Path) -> None:
    status_validation = load_status_validation()
    manifest_sha = sha256_file(manifest_path)
    base = {
        "schema_version": 2,
        "scheduler_kind": "main-branch-pool",
        "scheduler_path": "schedulers/main.scheduler.json",
        "manifest_sha256": manifest_sha,
        "capacity": 2,
        "item_ids": ["B01", "B02", "B03"],
    }
    main_ids = ["B01", "B02", "B03"]
    main_deps = {"B01": [], "B02": [], "B03": []}
    multi_branch_refill = {
        **base,
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "ready", id="B03"),
            scheduler_event(4, "launch", id="B01"),
            scheduler_event(5, "launch", id="B02"),
            scheduler_event(6, "finish", id="B01", status="pass"),
            scheduler_event(7, "close", id="B01"),
            scheduler_event(8, "refill", eligible_ids=["B03"]),
            scheduler_event(9, "launch", id="B03"),
            scheduler_event(10, "finish", id="B02", status="pass"),
            scheduler_event(11, "close", id="B02"),
            scheduler_event(12, "finish", id="B03", status="pass"),
            scheduler_event(13, "close", id="B03"),
        ],
    }
    stuck_branch_continues = {
        **base,
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "ready", id="B03"),
            scheduler_event(4, "launch", id="B01"),
            scheduler_event(5, "launch", id="B02"),
            scheduler_event(6, "blocked", id="B01", reason_code="process_exited_blocked", reason="B01 launcher returned blocked with captured status."),
            scheduler_event(7, "finish", id="B01", status="blocked"),
            scheduler_event(8, "close", id="B01"),
            scheduler_event(9, "refill", eligible_ids=["B03"]),
            scheduler_event(10, "launch", id="B03"),
            scheduler_event(11, "finish", id="B02", status="pass"),
            scheduler_event(12, "close", id="B02"),
            scheduler_event(13, "finish", id="B03", status="pass"),
            scheduler_event(14, "close", id="B03"),
        ],
    }
    worker_base = {
        **base,
        "scheduler_kind": "branch-worker-pool",
        "scheduler_path": "schedulers/B01.worker.scheduler.json",
        "item_ids": ["B01-W01", "B01-W02", "B01-W03"],
    }
    worker_ids = ["B01-W01", "B01-W02", "B01-W03"]
    worker_deps = {"B01-W01": [], "B01-W02": [], "B01-W03": []}
    stuck_worker_continues = {
        **worker_base,
        "events": [
            scheduler_event(1, "ready", id="B01-W01"),
            scheduler_event(2, "ready", id="B01-W02"),
            scheduler_event(3, "ready", id="B01-W03"),
            scheduler_event(4, "launch", id="B01-W01"),
            scheduler_event(5, "launch", id="B01-W02"),
            scheduler_event(6, "blocked", id="B01-W01", reason_code="process_exited_blocked", reason="Worker returned blocked but slot was closed."),
            scheduler_event(7, "finish", id="B01-W01", status="blocked"),
            scheduler_event(8, "close", id="B01-W01"),
            scheduler_event(9, "refill", eligible_ids=["B01-W03"]),
            scheduler_event(10, "launch", id="B01-W03"),
            scheduler_event(11, "finish", id="B01-W02", status="pass"),
            scheduler_event(12, "close", id="B01-W02"),
            scheduler_event(13, "refill", eligible_ids=["B01-W01"]),
            scheduler_event(14, "under_capacity", eligible_ids=["B01-W01"], reason_code="operator_requested", reason="Fixture preserves W01 as terminal blocked while continuing independent workers."),
            scheduler_event(15, "finish", id="B01-W03", status="pass"),
            scheduler_event(16, "close", id="B01-W03"),
        ],
    }
    reviewer_repair_relaunch = {
        **worker_base,
        "capacity": 1,
        "item_ids": ["B01-W01"],
        "events": [
            scheduler_event(1, "ready", id="B01-W01"),
            scheduler_event(2, "launch", id="B01-W01"),
            scheduler_event(3, "blocked", id="B01-W01", reason_code="process_exited_blocked", reason="First attempt needs reviewer-feedback repair."),
            scheduler_event(4, "finish", id="B01-W01", status="blocked"),
            scheduler_event(5, "close", id="B01-W01"),
            scheduler_event(6, "refill", eligible_ids=["B01-W01"]),
            scheduler_event(7, "launch", id="B01-W01"),
            scheduler_event(8, "finish", id="B01-W01", status="pass"),
            scheduler_event(9, "close", id="B01-W01"),
        ],
    }
    missing_refill = {
        **base,
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "ready", id="B03"),
            scheduler_event(4, "launch", id="B01"),
            scheduler_event(5, "launch", id="B02"),
            scheduler_event(6, "finish", id="B01", status="pass"),
            scheduler_event(7, "close", id="B01"),
            scheduler_event(8, "launch", id="B03"),
            scheduler_event(9, "finish", id="B02", status="pass"),
            scheduler_event(10, "close", id="B02"),
            scheduler_event(11, "finish", id="B03", status="pass"),
            scheduler_event(12, "close", id="B03"),
        ],
    }
    under_capacity_without_reason = {
        **base,
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "launch", id="B01"),
            scheduler_event(4, "finish", id="B01", status="pass"),
            scheduler_event(5, "close", id="B01"),
            scheduler_event(6, "launch", id="B02"),
            scheduler_event(7, "finish", id="B02", status="pass"),
            scheduler_event(8, "close", id="B02"),
            scheduler_event(9, "ready", id="B03"),
            scheduler_event(10, "launch", id="B03"),
            scheduler_event(11, "finish", id="B03", status="pass"),
            scheduler_event(12, "close", id="B03"),
        ],
    }
    stale_scheduler = {
        **multi_branch_refill,
        "manifest_sha256": "sha256:" + "0" * 64,
    }
    new_schema_event_missing_fields = {
        **base,
        "item_ids": ["B01"],
        "events": [
            {**scheduler_event(1, "ready", id="B01"), "schema_version": 2},
            scheduler_event(2, "launch", id="B01"),
            scheduler_event(3, "finish", id="B01", status="pass"),
            scheduler_event(4, "close", id="B01"),
        ],
    }
    new_schema_event_bad_manifest = {
        **base,
        "item_ids": ["B01"],
        "events": [
            {
                **scheduler_event(1, "ready", id="B01"),
                "schema_version": 2,
                "manifest_sha256": "sha256:" + "f" * 64,
                "manifest_epoch": "current",
            },
            scheduler_event(2, "launch", id="B01"),
            scheduler_event(3, "finish", id="B01", status="pass"),
            scheduler_event(4, "close", id="B01"),
        ],
    }
    missing_wall_clock = {
        **multi_branch_refill,
        "events": [
            {key: value for key, value in event.items() if key != "wall_clock_timestamp"}
            if index == 0
            else event
            for index, event in enumerate(multi_branch_refill["events"])
        ],
    }
    vague_reason = {
        **base,
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "launch", id="B01"),
            scheduler_event(3, "blocked", id="B01", reason="free-form reason without reason_code"),
            scheduler_event(4, "finish", id="B01", status="blocked"),
            scheduler_event(5, "close", id="B01"),
            scheduler_event(6, "blocked", id="B02", reason_code="operator_requested", reason="not reached in negative fixture"),
            scheduler_event(7, "blocked", id="B03", reason_code="operator_requested", reason="not reached in negative fixture"),
        ],
    }
    dependency_failed = {
        **base,
        "item_ids": ["B01", "B02"],
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "launch", id="B01"),
            scheduler_event(3, "blocked", id="B01", reason_code="process_exited_blocked", reason="B01 returned blocked."),
            scheduler_event(4, "finish", id="B01", status="blocked"),
            scheduler_event(5, "close", id="B01"),
            scheduler_event(6, "blocked", id="B02", reason_code="dependency_failed", reason="B02 depends on non-pass B01."),
        ],
    }
    dependency_failed_wrong_reason = {
        **dependency_failed,
        "events": [
            *dependency_failed["events"][:5],
            scheduler_event(6, "blocked", id="B02", reason_code="dependency_pending", reason="Wrong reason for non-pass dependency."),
        ],
    }
    stale_active_closeout = {
        **base,
        "capacity": 1,
        "item_ids": ["B01", "B02"],
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "launch", id="B01"),
            scheduler_event(4, "blocked", id="B01", reason_code="stale_active", reason="Native agent state was unreachable after watchdog limit."),
            scheduler_event(5, "finish", id="B01", status="blocked"),
            scheduler_event(6, "close", id="B01"),
            scheduler_event(7, "refill", eligible_ids=["B02"]),
            scheduler_event(8, "launch", id="B02"),
            scheduler_event(9, "finish", id="B02", status="pass"),
            scheduler_event(10, "close", id="B02"),
        ],
    }
    partial_subset = {
        **base,
        "item_ids": ["B01", "B02", "B03"],
        "events": [
            scheduler_event(1, "ready", id="B01"),
            scheduler_event(2, "ready", id="B02"),
            scheduler_event(3, "ready", id="B03"),
            scheduler_event(4, "launch", id="B01"),
            scheduler_event(5, "blocked", id="B02", reason_code="operator_requested", reason="Partial fixture leaves B02 unlaunched with terminal evidence."),
            scheduler_event(6, "blocked", id="B03", reason_code="operator_requested", reason="Partial fixture leaves B03 unlaunched with terminal evidence."),
            scheduler_event(7, "finish", id="B01", status="pass"),
            scheduler_event(8, "close", id="B01"),
        ],
    }
    scheduler_cases = [
        ("multi-branch-refill", multi_branch_refill, main_ids, main_deps, 2, True, True),
        ("stuck-branch-continued-scheduling", stuck_branch_continues, main_ids, main_deps, 2, True, True),
        ("stuck-worker-continued-scheduling", stuck_worker_continues, worker_ids, worker_deps, 2, True, True),
        ("reviewer-feedback-worker-relaunch", reviewer_repair_relaunch, ["B01-W01"], {"B01-W01": []}, 1, True, True),
        ("missing-refill-event", missing_refill, main_ids, main_deps, 2, False, True),
        ("under-capacity-without-reason", under_capacity_without_reason, main_ids, main_deps, 2, False, True),
        ("stale-scheduler-manifest-hash", stale_scheduler, main_ids, main_deps, 2, False, True),
        ("new-schema-missing-event-hash", new_schema_event_missing_fields, ["B01"], {"B01": []}, 2, False, False),
        ("new-schema-event-hash-conflict", new_schema_event_bad_manifest, ["B01"], {"B01": []}, 2, False, False),
        ("missing-wall-clock-timestamp", missing_wall_clock, main_ids, main_deps, 2, False, True),
        ("vague-reason-code-rejection", vague_reason, main_ids, main_deps, 2, False, False),
        ("dependency-failed-blocking", dependency_failed, ["B01", "B02"], {"B01": [], "B02": ["B01"]}, 2, True, False),
        ("dependency-failed-wrong-reason", dependency_failed_wrong_reason, ["B01", "B02"], {"B01": [], "B02": ["B01"]}, 2, False, False),
        ("stale-active-closeout-refill", stale_active_closeout, ["B01", "B02"], {"B01": [], "B02": []}, 1, True, True),
        ("partial-subset-structured-closeout", partial_subset, main_ids, main_deps, 2, True, False),
    ]
    for label, ledger, expected_ids, dependencies, capacity, expect_pass, require_all_launched in scheduler_cases:
        assert_scheduler_fixture(
            status_validation,
            label,
            ledger,
            expected_ids=expected_ids,
            dependencies=dependencies,
            capacity=capacity,
            expect_pass=expect_pass,
            manifest_path=manifest_path,
            require_all_launched=require_all_launched,
        )


def run_scheduler_tick_fixture(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "scheduler-tick"
    manifest_path = fixture_dir / "job.manifest.json"
    write_json(
        manifest_path,
        {
            "max_active_branch_agents": 2,
            "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
            "branches": [
                {"id": "B01", "depends_on": []},
                {"id": "B02", "depends_on": []},
                {"id": "B03", "depends_on": ["B01"]},
            ],
        },
    )
    common = [
        "python3",
        "skills/goal-main-orchestrator/scripts/scheduler_tick.py",
        "--manifest",
        manifest_path.as_posix(),
        "--scope",
        "main",
        "--runtime-ref",
        "scheduler-tick-fixture",
    ]
    run(
        [
            *common,
            "--timestamp",
            "2026-05-29T00:00:01Z",
            "--init",
            "--record-ready",
            "--launch",
            "B01",
            "--launch",
            "B02",
        ]
    )
    run(
        [
            *common,
            "--timestamp",
            "2026-05-29T00:00:02Z",
            "--finish",
            "B01",
            "--status",
            "pass",
            "--close",
            "B01",
        ]
    )
    ledger = json.loads((fixture_dir / "schedulers" / "main.scheduler.json").read_text(encoding="utf-8"))
    refill_events = [event for event in ledger.get("events", []) if isinstance(event, dict) and event.get("event") == "refill"]
    if not refill_events or refill_events[-1].get("eligible_ids") != ["B03"]:
        raise SystemExit("scheduler_tick fixture did not emit refill evidence for B03")
    run(
        [
            *common,
            "--timestamp",
            "2026-05-29T00:00:03Z",
            "--launch",
            "B03",
            "--finish",
            "B02",
            "--finish",
            "B03",
            "--status",
            "pass",
            "--close",
            "B02",
            "--close",
            "B03",
            "--validate-final",
        ]
    )
    deterministic_ledgers = []
    for suffix in ["a", "b"]:
        repeat_dir = tmp_path / f"scheduler-tick-repeat-{suffix}"
        repeat_manifest = repeat_dir / "job.manifest.json"
        write_json(
            repeat_manifest,
            {
                "max_active_branch_agents": 1,
                "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
                "branches": [{"id": "B01", "depends_on": []}],
            },
        )
        repeat_common = [
            "python3",
            "skills/goal-main-orchestrator/scripts/scheduler_tick.py",
            "--manifest",
            repeat_manifest.as_posix(),
            "--scope",
            "main",
            "--runtime-ref",
            "scheduler-tick-determinism-fixture",
        ]
        run(
            [
                *repeat_common,
                "--init",
                "--record-ready",
                "--launch",
                "B01",
                "--finish",
                "B01",
                "--status",
                "pass",
                "--close",
                "B01",
                "--validate-final",
            ]
        )
        deterministic_ledger = read_json(repeat_dir / "schedulers" / "main.scheduler.json")
        for event in deterministic_ledger.get("events", []):
            if isinstance(event, dict):
                event.pop("wall_clock_timestamp", None)
        deterministic_ledgers.append(json.dumps(deterministic_ledger, indent=2, sort_keys=True))
    if deterministic_ledgers[0] != deterministic_ledgers[1]:
        raise SystemExit("scheduler_tick without --timestamp must produce deterministic ledger content")
    auto_dir = tmp_path / "scheduler-tick-artifacts"
    auto_manifest = auto_dir / "job.manifest.json"
    write_json(
        auto_manifest,
        {
            "max_active_branch_agents": 2,
            "parallelization": {"scheduler_path": "schedulers/main.scheduler.json"},
            "branches": [
                {"id": "B01", "depends_on": [], "status_path": "branches/B01.status.json"},
                {"id": "B02", "depends_on": [], "status_path": "branches/B02.status.json"},
            ],
        },
    )
    write_json(auto_dir / "branches" / "B01.status.json", {"status": "pass"})
    write_json(auto_dir / "branches" / "B02.status.json", {"status": "partial"})
    auto_result = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/scheduler_tick.py",
            "--manifest",
            auto_manifest.as_posix(),
            "--scope",
            "main",
            "--runtime-ref",
            "scheduler-tick-artifact-fixture",
            "--init",
            "--record-ready",
            "--close-from-artifacts",
            "--validate-final",
            "--json",
        ]
    )
    auto_data = json.loads(auto_result.stdout)
    if auto_data["state"]["finished_status"] != {"B01": "pass", "B02": "partial"}:
        raise SystemExit(f"scheduler_tick artifact closeout status mismatch: {auto_data['state']['finished_status']!r}")
    if auto_data["state"]["max_observed_active"] != 2:
        raise SystemExit("scheduler_tick artifact closeout did not preserve saturated main launch evidence")
    auto_worker_dir = tmp_path / "scheduler-tick-worker-artifacts"
    worker_manifest = auto_worker_dir / "job.manifest.json"
    write_json(
        worker_manifest,
        {
            "branches": [
                {
                    "id": "B01",
                    "max_active_worker_packets": 4,
                    "worker_parallelism": {
                        "scheduler_path": "schedulers/B01.worker.scheduler.json",
                        "max_worker_packets_per_branch": 4,
                    },
                    "work_items": [
                        {
                            "id": "W01",
                            "packet_id": "B01-W01",
                            "objective": "Closed pass worker.",
                            "owned_paths": ["README.md"],
                            "context_files": ["README.md"],
                            "depends_on": [],
                            "verification": ["git diff --check main...HEAD"],
                            "dod": ["pass worker artifact is available"],
                        },
                        {
                            "id": "W02",
                            "packet_id": "B01-W02",
                            "objective": "Closed blocked worker.",
                            "owned_paths": ["skills/goal-preflight/SKILL.md"],
                            "context_files": ["README.md"],
                            "depends_on": [],
                            "verification": ["git diff --check main...HEAD"],
                            "dod": ["blocked worker artifact is available"],
                        },
                    ],
                }
            ]
        },
    )
    (auto_worker_dir / "workers").mkdir(parents=True, exist_ok=True)
    write_json(auto_worker_dir / "workers" / "B01-W01" / "status.json", {"status": "pass"})
    write_json(auto_worker_dir / "workers" / "B01-W02" / "status.json", {"status": "blocked"})
    auto_worker_result = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/scheduler_tick.py",
            "--manifest",
            worker_manifest.as_posix(),
            "--scope",
            "worker",
            "--branch-id",
            "B01",
            "--runtime-ref",
            "scheduler-tick-worker-artifact-fixture",
            "--init",
            "--record-ready",
            "--close-from-artifacts",
            "--validate-final",
            "--json",
        ]
    )
    auto_worker_data = json.loads(auto_worker_result.stdout)
    if auto_worker_data["state"]["finished_status"] != {"B01-W01": "pass", "B01-W02": "blocked"}:
        raise SystemExit(
            f"scheduler_tick worker closeout status mismatch: {auto_worker_data['state']['finished_status']!r}"
        )
    auto_worker_ledger = read_json(auto_worker_dir / "schedulers" / "B01.worker.scheduler.json")
    events = auto_worker_ledger.get("events", [])
    finish_events = [event for event in events if isinstance(event, dict) and event.get("event") == "finish"]
    if len(finish_events) < 2:
        raise SystemExit(f"scheduler_tick worker closeout should emit two finish events, got {len(finish_events)}")
    if auto_worker_data["state"]["max_observed_active"] != 2:
        raise SystemExit("scheduler_tick worker closeout should close all terminal artifacts under open worker capacity")
    append_ledgers = []
    for suffix in ["a", "b"]:
        append_dir = tmp_path / f"append-scheduler-event-repeat-{suffix}"
        ledger_path = append_dir / "schedulers" / "main.scheduler.json"
        write_json(
            ledger_path,
            {
                "schema_version": 2,
                "scheduler_kind": "main-branch-pool",
                "scheduler_path": "schedulers/main.scheduler.json",
                "manifest_sha256": "sha256:" + "0" * 64,
                "capacity": 1,
                "item_ids": ["B01"],
                "events": [],
            },
        )
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/append_scheduler_event.py",
                "--ledger",
                ledger_path.as_posix(),
                "--event",
                "ready",
                "--id",
                "B01",
                "--runtime-ref",
                "append-scheduler-event-determinism-fixture",
            ]
        )
        append_ledgers.append(ledger_path.read_text(encoding="utf-8"))
    if append_ledgers[0] != append_ledgers[1]:
        raise SystemExit("append_scheduler_event without --timestamp must produce deterministic ledger content")


def write_prompt_audit_fixture(bundle: Path) -> Path:
    audit_path = bundle / "audit" / "prompt-audit.json"
    write_json(
        audit_path,
        {
            "manifest": (bundle / "job.manifest.json").as_posix(),
            "repo_root": ROOT.as_posix(),
            "status": "pass",
            "can_start": True,
            "checked_files": ["job.manifest.json", "main.prompt.md"],
            "commands_run": ["python3 skills/goal-preflight/scripts/lint_goal_bundle.py --bundle-dir fixture --no-write"],
            "defects": [],
            "missing_dod_items": [],
        },
    )
    write_json(
        bundle / "audit" / "telemetry.json",
        telemetry(
            "prompt-audit",
            "prompt-auditor",
            "prompt-audit.json",
            accepted_alias="gpt-5.5",
            attempts=[
                {
                    "alias": "gpt-5.5",
                    "provider": "codex",
                    "model": "gpt-5.5",
                    "effort": None,
                    "command": "codex exec --ephemeral -m gpt-5.5 -s read-only",
                    "timeout_seconds": 1200,
                    "called": True,
                    "accepted": True,
                    "event_logs": [],
                    "probe_logs": [],
                    "usage": None,
                },
                {
                    "alias": "gpt-5.4",
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "effort": None,
                    "command": "codex exec --ephemeral -m gpt-5.4 -s read-only",
                    "timeout_seconds": 1200,
                    "called": False,
                    "accepted": False,
                    "event_logs": [],
                    "probe_logs": [],
                    "usage": None,
                },
            ],
        ),
    )
    return audit_path


def run_launch_ready_helper_fixtures(tmp_path: Path) -> None:
    worker_dir = tmp_path / "launch-ready-worker"
    worker_manifest = worker_dir / "job.manifest.json"
    write_json(
        worker_manifest,
        {
            "branches": [
                {
                    "id": "B01",
                    "max_active_worker_packets": 1,
                    "worker_parallelism": {
                        "parallelism_default": True,
                        "scheduling_mode": "rolling",
                        "max_active_worker_packets": 1,
                        "max_worker_packets_per_branch": 4,
                        "slot_refill": "Launch replacement packets as soon as capacity frees.",
                        "dependency_policy": "Launch only work items whose depends_on entries passed.",
                    },
                    "work_items": [
                        {
                            "id": "W01",
                            "packet_id": "B01-W01",
                            "objective": "Initial repairable fixture worker.",
                            "owned_paths": ["README.md"],
                            "context_files": ["README.md"],
                            "verification": ["git diff --check main...HEAD"],
                            "dod": ["first worker fixture validates"],
                        },
                        {
                            "id": "W02",
                            "packet_id": "B01-W02",
                            "objective": "Dependent fixture worker.",
                            "owned_paths": ["docs/launch-ready-worker.md"],
                            "context_files": ["README.md"],
                            "depends_on": ["W01"],
                            "verification": ["git diff --check main...HEAD"],
                            "dod": ["dependent worker fixture validates"],
                        },
                    ],
                }
            ]
        },
    )
    worker_tick = [
        "python3",
        "skills/goal-branch-orchestrator/scripts/scheduler_tick.py",
        "--manifest",
        worker_manifest.as_posix(),
        "--scope",
        "worker",
        "--branch-id",
        "B01",
        "--runtime-ref",
        "launch-ready-worker-fixture",
    ]
    run([*worker_tick, "--init", "--record-ready", "--launch", "B01-W01"])
    run([*worker_tick, "--finish", "B01-W01", "--status", "blocked", "--close", "B01-W01"])
    tick_ready = run([*worker_tick, "--list-ready"])
    if tick_ready.stdout.splitlines() != ["B01-W01"]:
        raise SystemExit("scheduler_tick did not list closed non-pass worker packet for repair relaunch")
    schedule_ready = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/render_worker_schedule.py",
            "--manifest",
            worker_manifest.as_posix(),
            "--branch-id",
            "B01",
            "--list-ready",
        ]
    )
    if schedule_ready.stdout.splitlines() != ["B01-W01"]:
        raise SystemExit("render_worker_schedule did not prefer repair relaunch over dependent worker")
    schedule_ready_clamped = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/render_worker_schedule.py",
            "--manifest",
            worker_manifest.as_posix(),
            "--branch-id",
            "B01",
            "--list-ready",
            "--limit",
            "4",
        ]
    )
    if schedule_ready_clamped.stdout.splitlines() != ["B01-W01"]:
        raise SystemExit("render_worker_schedule should clamp --limit to remaining worker capacity")
    completed_non_pass = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/render_worker_schedule.py",
            "--manifest",
            worker_manifest.as_posix(),
            "--branch-id",
            "B01",
            "--list-ready",
            "--completed-worker",
            "B01-W01",
        ],
        expect=1,
    )
    assert_contains(completed_non_pass.stdout, "non-pass", "worker completed non-pass fixture")

    main_brief = {
        "job_id": "launch-ready-main",
        "base_ref": "main",
        "max_active_branch_agents": 2,
        "serial_reasons": ["B02 intentionally depends on B01 for launch-ready fixture coverage."],
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "skills/goal-preflight/SKILL.md", depends_on=["B01"]),
        ],
    }
    main_brief_path = tmp_path / "launch-ready-main.json"
    main_bundle = tmp_path / "launch-ready-main"
    write_json(main_brief_path, main_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            main_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            main_bundle.as_posix(),
        ]
    )
    audit_path = write_prompt_audit_fixture(main_bundle)
    write_json(main_bundle / "branches" / "B01.status.json", {"branch_id": "B01", "status": "blocked"})
    completed_blocked = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--list-ready",
            "--completed-branch",
            "B01",
        ],
        expect=1,
    )
    assert_contains(completed_blocked.stdout, "non-pass", "main completed non-pass status fixture")
    write_json(main_bundle / "branches" / "B01.status.json", {"branch_id": "B01", "status": "pass"})
    main_tick = [
        "python3",
        "skills/goal-main-orchestrator/scripts/scheduler_tick.py",
        "--manifest",
        (main_bundle / "job.manifest.json").as_posix(),
        "--scope",
        "main",
        "--runtime-ref",
        "launch-ready-main-fixture",
    ]
    run([*main_tick, "--init", "--record-ready", "--launch", "B01"])
    run([*main_tick, "--finish", "B01", "--status", "pass", "--close", "B01"])
    main_ready = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--list-ready",
        ]
    )
    if main_ready.stdout.splitlines() != ["B02"]:
        raise SystemExit("render_branch_worktree_commands did not infer completed branch from scheduler ledger")
    main_ready_clamped = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--list-ready",
            "--limit",
            "4",
        ]
    )
    if main_ready_clamped.stdout.splitlines() != ["B02"]:
        raise SystemExit("render_branch_worktree_commands should clamp --limit to remaining branch capacity")
    native_plan_result = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--branch",
            "B02",
            "--native-agent-available",
            "--json",
        ]
    )
    native_plan = json.loads(native_plan_result.stdout)
    if native_plan.get("preferred_delegation") != "native_agent" or native_plan.get("selected_delegation") != "native_agent":
        raise SystemExit(f"native delegation should be preferred when available: {native_plan!r}")
    native_branch = native_plan.get("branches", [{}])[0]
    if native_branch.get("selected_delegation") != "native_agent" or native_branch.get("cli_fallback_reason") is not None:
        raise SystemExit(f"native branch plan should not carry a fallback reason: {native_branch!r}")
    native_text_result = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--branch",
            "B02",
            "--native-agent-available",
        ]
    )
    native_text_lines = native_text_result.stdout.splitlines()
    if any(line.startswith("git worktree add ") for line in native_text_lines):
        raise SystemExit(f"native delegation stdout must not emit runnable CLI fallback commands: {native_text_result.stdout!r}")
    if not any(line.startswith("# git worktree add -b ") for line in native_text_lines):
        raise SystemExit(f"native delegation stdout should preserve commented CLI fallback context: {native_text_result.stdout!r}")
    fallback_report = main_bundle / "branches" / "B02.delegation.json"
    fallback_result = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (main_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            audit_path.as_posix(),
            "--branch",
            "B02",
            "--delegation-report",
            fallback_report.as_posix(),
        ]
    )
    fallback_plan = read_json(fallback_report)
    if fallback_plan.get("selected_delegation") != "cli_worktree":
        raise SystemExit(f"CLI should be an explicit fallback when native delegation is unavailable: {fallback_plan!r}")
    if fallback_plan.get("cli_fallback_reason") != "native_agent_delegation_unavailable":
        raise SystemExit(f"CLI fallback plan should record a reason: {fallback_plan!r}")
    if not fallback_result.stdout.strip().startswith("git worktree add -b "):
        raise SystemExit(f"CLI fallback should still render the worktree command: {fallback_result.stdout!r}")
    existing_branch_manifest = read_json(main_bundle / "job.manifest.json")
    existing_branch_name = "goal-fixture-existing-branch-test"
    existing_worktree_path = ".worktrees/goal-fixture-existing-branch-test"
    if subprocess.run(["git", "branch", "--list", existing_branch_name], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.strip():
        raise SystemExit(f"temporary fixture branch already exists: {existing_branch_name}")
    for branch in existing_branch_manifest["branches"]:
        if branch.get("id") == "B02":
            branch["branch_name"] = existing_branch_name
            branch["worktree_path"] = existing_worktree_path
    write_json(main_bundle / "job.manifest.json", existing_branch_manifest)
    try:
        run(["git", "branch", existing_branch_name, "HEAD"])
        existing_branch_result = run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
                "--manifest",
                (main_bundle / "job.manifest.json").as_posix(),
                "--repo-root",
                ROOT.as_posix(),
                "--audit",
                audit_path.as_posix(),
                "--branch",
                "B02",
                "--delegation-mode",
                "cli",
            ]
        )
    finally:
        subprocess.run(["git", "branch", "-D", existing_branch_name], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    existing_branch_command = existing_branch_result.stdout.strip()
    if not existing_branch_command.startswith("git worktree add ") or " -b " in existing_branch_command:
        raise SystemExit(f"existing branch without attached worktree should render non-creating add command: {existing_branch_result.stdout!r}")


def load_script_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {relative_path} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_state_classifier(tmp_path: Path) -> None:
    path = ROOT / "skills" / "goal-branch-orchestrator" / "scripts" / "runtime_packet_runner.py"
    spec = importlib.util.spec_from_file_location("preparedness_runtime_packet_runner", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load runtime_packet_runner.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        (124, False, False, "timeout"),
        (1, False, False, "fail-clean"),
        (1, False, True, "fail-dirty"),
        (0, True, False, "pass"),
    ]
    for returncode, output_nonempty, dirty, expected in cases:
        actual = module.classify_attempt_state(returncode, output_nonempty=output_nonempty, dirty=dirty)
        if actual != expected:
            raise SystemExit(
                f"launcher state classifier mismatch for rc={returncode}, output={output_nonempty}, dirty={dirty}: {actual!r}"
            )
    try:
        module.validate_packet_post_constraints(
            {
                "packet_id": "B01-R01",
                "role": "reviewer",
                "verdict": "mergeable",
                "verification_gaps": ["direct rerun failed in this fixture"],
            },
            {"role": "reviewer"},
        )
    except ValueError as exc:
        if "verification_gaps" not in str(exc):
            raise SystemExit(f"reviewer post-constraint should explain verification gap rejection: {exc}") from exc
    else:
        raise SystemExit("reviewer post-constraint should reject mergeable verdicts with verification gaps")
    cleanup_repo = tmp_path / "generated-cleanup-tracked-repo"
    cleanup_repo.mkdir()
    run(["git", "init", cleanup_repo.as_posix()])
    tracked_egg_info = cleanup_repo / "src" / "pkg.egg-info" / "PKG-INFO"
    tracked_egg_info.parent.mkdir(parents=True)
    tracked_egg_info.write_text("tracked original\n", encoding="utf-8")
    run(["git", "-C", cleanup_repo.as_posix(), "add", "src/pkg.egg-info/PKG-INFO"])
    tracked_egg_info.write_text("tracked modified\n", encoding="utf-8")
    untracked_pycache = cleanup_repo / "pkg" / "__pycache__"
    untracked_pycache.mkdir(parents=True)
    (untracked_pycache / "module.pyc").write_bytes(b"generated")
    cleanup = module.cleanup_generated_artifacts(
        cleanup_repo.as_posix(),
        attempt_index=0,
        attempt={"alias": "fixture", "provider": "fixture", "model": "fixture"},
    )
    if "src/pkg.egg-info" in cleanup.get("removed", []):
        raise SystemExit(f"cleanup must not remove tracked cache-like paths: {cleanup!r}")
    if not tracked_egg_info.exists() or tracked_egg_info.read_text(encoding="utf-8") != "tracked modified\n":
        raise SystemExit("cleanup removed or modified a tracked .egg-info file")
    if untracked_pycache.exists() or "pkg/__pycache__" not in cleanup.get("removed", []):
        raise SystemExit(f"cleanup should remove only the untracked generated artifact root: {cleanup!r}")
    cache_cases = {
        "cache/store.py": False,
        "cache": False,
        ".runtime-cache/events.log": True,
        ".runtime-cache": True,
        ".pytest_cache/v/cache": True,
        ".ruff_cache/0.13.0/cache": True,
        "pkg/__pycache__/module.pyc": True,
        "pkg/module.pyc": True,
        "src/pkg.egg-info/PKG-INFO": True,
        "src/pkg.egg-info": True,
    }
    cache_modules = [
        module,
        load_script_module(
            "preparedness_validate_branch_status_cache",
            "skills/goal-branch-orchestrator/scripts/validate_branch_status.py",
        ),
        load_script_module(
            "preparedness_assemble_branch_status_cache",
            "skills/goal-branch-orchestrator/scripts/assemble_branch_status.py",
        ),
        load_script_module(
            "preparedness_create_pre_review_gate_cache",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
        ),
    ]
    for cache_module in cache_modules:
        for rel_path, expected_cache in cache_cases.items():
            actual_cache = cache_module.is_runtime_cache_path(rel_path)
            if actual_cache is not expected_cache:
                raise SystemExit(
                    f"{cache_module.__name__}.is_runtime_cache_path({rel_path!r}) "
                    f"expected {expected_cache}, got {actual_cache}"
                )
    cache_repo = tmp_path / "runtime-cache-status-repo"
    cache_repo.mkdir()
    run(["git", "init", cache_repo.as_posix()])
    cache_file = cache_repo / ".pytest_cache" / "v" / "cache"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text("generated\n", encoding="utf-8")
    if not module.is_worktree_dirty(cache_repo.as_posix()):
        raise SystemExit("runtime cache fixture should make raw worktree status dirty")
    if module.is_worktree_dirty(cache_repo.as_posix(), ignore_runtime_cache=True):
        raise SystemExit("runtime cache-only dirt should not be actionable dirty")
    opencode_packet_dir = tmp_path / "opencode-wal-classifier"
    opencode_packet_dir.mkdir()
    extra_env, db_path = module.opencode_packet_env(opencode_packet_dir)
    if not extra_env.get("XDG_DATA_HOME", "").startswith(opencode_packet_dir.as_posix()):
        raise SystemExit(f"opencode runtime should use packet-local XDG data: {extra_env!r}")
    if db_path != Path(extra_env["XDG_DATA_HOME"]) / "opencode" / "opencode.db":
        raise SystemExit(f"opencode readback db should use packet-local XDG data: {db_path!r}")
    stderr_path = opencode_packet_dir / "events-lite_agent.jsonl.stderr"
    stderr_path.write_text("Failed to run the query 'PRAGMA journal_mode = WAL'\n", encoding="utf-8")
    parse_report = module.opencode_wal_failure_report({"stderr_path": stderr_path.name}, opencode_packet_dir)
    if not isinstance(parse_report, dict) or parse_report.get("failure_subclass") != module.OPENCODE_SQLITE_WAL_SUBCLASS:
        raise SystemExit(f"opencode WAL failure should be classified as harness infrastructure: {parse_report!r}")
    failure_class = module.attempt_failure_class(
        {"state": "fail-clean", "returncode": 1},
        {"harness_kind": "opencode"},
        parse_report=parse_report,
    )
    if failure_class != "harness_unavailable":
        raise SystemExit(f"opencode WAL failure should be reported as harness_unavailable: {failure_class!r}")


def test_configured_reviewer_route_policy() -> None:
    path = ROOT / "skills" / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py"
    spec = importlib.util.spec_from_file_location("preparedness_validate_branch_status", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load validate_branch_status.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    review_policy = {
        "source": "goal_config",
        "router": "goal-config-v1",
        "default_tier": "standard",
        "routes": {
            "light": ["demanding_agent"],
            "standard": ["demanding_agent"],
            "heavy": ["demanding_agent"],
        },
        "heavy_triggers": [],
    }
    manifest = {
        "review_model_policy": review_policy,
        "goal_config": {
            "model_policies": {
                "review_model_policy": review_policy,
            },
        },
    }
    route = {
        "schema_version": 1,
        "packet_id": "B01-R01",
        "role": "reviewer",
        "tier": "standard",
        "selected_ladder": ["demanding_agent"],
        "selection_reason": "configured reviewer route fixture",
        "policy_router": "goal-config-v1",
        "policy_version": "goal-route-policy-v2",
        "route_policy_version": "goal-route-policy-v2",
        "policy_routes": review_policy["routes"],
        "heavy_triggers": [],
    }
    defects: list[str] = []
    selected = module.validate_reviewer_route_artifact(
        defects,
        route,
        "$.review_status.route_path",
        packet_id="B01-R01",
        manifest=manifest,
        manifest_path=None,
    )
    if defects or selected != ["demanding_agent"]:
        raise SystemExit(f"configured reviewer route policy should validate: selected={selected!r} defects={defects!r}")


def run_runtime_packet_fixtures(tmp_path: Path, bundle: Path) -> tuple[Path, Path]:
    packet_root = tmp_path / "packets"
    task_file = tmp_path / "task.md"
    task_file.write_text("Static timeout launcher fixture.\n", encoding="utf-8")
    create_runtime_packet(
        role="worker",
        packet_id="B01-W02",
        branch="preparedness-research-fixture-W02",
        out_dir=packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        worker_route=["codex-mini"],
        selection_reason="Fixture route selected to inspect generated timeout wrapper.",
    )
    assert_shell_syntax(packet_root / "B01-W02" / "launch.sh")
    worker_config = assert_compact_runtime_launcher(packet_root / "B01-W02", "worker")
    assert_openai_strict_schema(packet_root / "B01-W02" / "status.schema.json", "worker status schema")
    assert_codex_mini_worker_route(worker_config, "Fixture route selected to inspect generated timeout wrapper.")
    missing_manifest_context_bundle = tmp_path / "missing-runtime-manifest-context"
    shutil.copytree(bundle, missing_manifest_context_bundle)
    missing_manifest = read_json(missing_manifest_context_bundle / "job.manifest.json")
    missing_manifest["branches"][0]["work_items"][0]["context_files"] = ["missing-runtime-context.md"]
    write_json(missing_manifest_context_bundle / "job.manifest.json", missing_manifest)
    missing_context_result = create_runtime_packet(
        role="worker",
        packet_id="B01-W01",
        branch="B01",
        out_dir=packet_root,
        owned_files=["README.md"],
        task_file=task_file,
        manifest=missing_manifest_context_bundle / "job.manifest.json",
        worker_route=["codex-mini"],
        selection_reason="Fixture route selected to validate pre-launch context checks.",
        expect=1,
    )
    assert_all_contains(
        missing_context_result.stdout,
        ["runtime packet pre-launch validation failed", "missing-runtime-context.md", "does not exist as a file"],
        "missing runtime context pre-launch validation",
    )
    if (packet_root / "B01-W01" / "launch.sh").exists():
        raise SystemExit("missing context pre-launch validation must fail before writing launch.sh")
    create_runtime_packet(
        role="worker",
        packet_id="B01-W04",
        branch="preparedness-research-fixture-W04",
        out_dir=packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        worker_route=["gemini-pro", "codex-spark", "codex-mini"],
        selection_reason="Fixture route selected to validate worker probe metadata.",
    )
    mixed_config = assert_compact_runtime_launcher(packet_root / "B01-W04", "worker")
    assert_mixed_worker_route(mixed_config, "mixed worker", selection_reason="Fixture route selected to validate worker probe metadata.")
    packet_module = load_script_module("preparedness_create_runtime_packet", "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py")
    packet_module.validate_launch_config_adapter(mixed_config)
    missing_probe_config = json.loads(json.dumps(mixed_config))
    gemini_attempt = next(
        (item for item in missing_probe_config.get("attempts", []) if isinstance(item, dict) and item.get("provider") == "gemini"),
        None,
    )
    if not isinstance(gemini_attempt, dict):
        raise SystemExit(f"mixed route fixture should include a Gemini attempt: {missing_probe_config!r}")
    gemini_attempt.pop("probe_model", None)
    missing_probe_config["attempts"] = [gemini_attempt]
    missing_probe_config["selected_ladder"] = [gemini_attempt.get("alias")]
    try:
        packet_module.validate_launch_config_adapter(missing_probe_config)
    except SystemExit as exc:
        if "probe_model" not in str(exc):
            raise SystemExit(f"adapter validation should report missing probe_model: {exc}") from exc
    else:
        raise SystemExit("adapter validation should fail before launch when a selected Gemini route lacks probe_model")
    configured_gemini_config = packet_module.compact_launch_config(
        "worker",
        "B01-W12",
        "preparedness-configured-gemini",
        ROOT.as_posix(),
        "status.schema.json",
        "status.json",
        owned_files=["README.md"],
        selected_ladder=["configured_gemini"],
        route_class="normal-code",
        selection_reason="Fixture validates configured Gemini route probe metadata.",
        goal_config={
            "schema_version": 1,
            "models": {
                "configured_gemini": {
                    "alias": "configured-gemini",
                    "role": "configured_gemini",
                    "harness": "gemini",
                    "provider": "gemini",
                    "model": "gemini-3-flash-preview",
                }
            },
            "harnesses": {
                "gemini": {
                    "kind": "gemini",
                    "command": "gemini",
                    "run_args": ["--model", "{model}", "--approval-mode", "yolo"],
                    "run_readback": "stdout",
                }
            },
        },
    )
    configured_gemini_attempt = configured_gemini_config.get("attempts", [{}])[0]
    if (
        configured_gemini_attempt.get("probe_model") != "gemini-3-flash-preview"
        or configured_gemini_attempt.get("probe_timeout_seconds") is None
        or configured_gemini_attempt.get("probe_logs") != ["events-configured_gemini-probe.log"]
    ):
        raise SystemExit(f"configured Gemini attempts should carry probe metadata: {configured_gemini_config!r}")
    packet_module.validate_launch_config_adapter(configured_gemini_config)
    discontinued_route = create_runtime_packet(
        role="worker",
        packet_id="B01-W07",
        branch="preparedness-research-fixture-W07",
        out_dir=packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        worker_route=["copilot-gpt-5.4"],
        selection_reason="Fixture intentionally selects discontinued Copilot route.",
        expect=1,
    )
    if "unsupported worker route alias: 'copilot-gpt-5.4'" not in discontinued_route.stdout:
        raise SystemExit(f"discontinued Copilot worker route should be rejected: {discontinued_route.stdout}")
    path_validation_manifest = tmp_path / "runtime-path-validation"
    path_validation_manifest.mkdir()
    write_json(
        path_validation_manifest / "job.manifest.json",
        {
            "job_id": "runtime-path-validation",
            "base_ref": "main",
            "branches": [
                {
                    "id": "B01",
                    "title": "Runtime path validation",
                    "objective": "Validate runtime packet path pre-launch checks.",
                    "branch_name": "runtime-path-validation",
                    "worktree_path": ".worktrees/runtime-path-validation",
                    "depends_on": ["B00"],
                    "max_active_worker_packets": 1,
                    "work_items": [
                        {
                            "id": "W01",
                            "packet_id": "B01-W10",
                            "objective": "Manifest work item used for dependency path validation.",
                            "owned_paths": ["README.md"],
                            "context_files": ["README.md"],
                            "verification": ["pwd"],
                            "dod": ["runtime path validation context"],
                        }
                    ],
                },
                {
                    "id": "B00",
                    "title": "Runtime dependency branch",
                    "objective": "Provide manifest dependency path references.",
                    "branch_name": "runtime-dependency-branch",
                    "worktree_path": ".worktrees/runtime-dependency-branch",
                    "max_active_worker_packets": 1,
                    "status_path": "branches/B00.status.json",
                    "review_path": "branches/B00.review.json",
                    "pre_review_gate_path": "branches/B00.pre_review_gate.json",
                    "work_items": [
                        {
                            "id": "W01",
                            "packet_id": "B00-W01",
                            "objective": "Seed dependency branch for path checks.",
                            "owned_paths": ["README.md"],
                            "context_files": ["README.md"],
                            "verification": ["pwd"],
                            "dod": ["dependency fixture seed"],
                        }
                    ],
                },
            ],
        },
    )
    dependency_check = create_runtime_packet(
        role="worker",
        packet_id="B01-W11",
        branch="B01",
        out_dir=packet_root,
        worktree=ROOT,
        task_file=task_file,
        manifest=path_validation_manifest / "job.manifest.json",
        worker_route=["codex-mini"],
        selection_reason="Fixture validates dependency status artifact path exists before launch.",
        expect=1,
    )
    if "runtime packet pre-launch validation failed" not in dependency_check.stdout:
        raise SystemExit(f"dependency pre-launch validation should fail before launch: {dependency_check.stdout}")
    if "dependency B00 status_path does not exist as a file" not in dependency_check.stdout:
        raise SystemExit(f"dependency status-path pre-launch validation should include missing file: {dependency_check.stdout}")
    worker_model_catalog = tmp_path / "worker-model-catalog.json"
    write_json(
        worker_model_catalog,
        {
            "schema_version": 1,
            "status": "pass",
            "source": "live",
            "warnings": [],
            "models": [],
            "route_models": [
                {
                    "alias": "codex-spark",
                    "model": "gpt-5.3-codex-spark",
                    "present": True,
                    "supported_in_api": False,
                    "visibility": "list",
                },
                {
                    "alias": "codex-mini",
                    "model": "gpt-5.4-mini",
                    "present": True,
                    "supported_in_api": True,
                    "visibility": "list",
                },
            ],
            "missing_route_models": [],
        },
    )
    catalog_packet_root = tmp_path / "catalog-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W05",
        branch="preparedness-research-fixture-W05",
        out_dir=catalog_packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        model_catalog=worker_model_catalog,
    )
    catalog_config = assert_compact_runtime_launcher(catalog_packet_root / "B01-W05", "worker")
    expected_catalog_ladder = ["codex-mini"]
    if catalog_config.get("selected_ladder") != expected_catalog_ladder:
        raise SystemExit(f"worker model catalog did not prune Spark from default ladder: {catalog_config.get('selected_ladder')!r}")
    catalog_meta = catalog_config.get("model_catalog", {})
    filtered_aliases = [
        item.get("alias")
        for item in catalog_meta.get("filtered_aliases", [])
        if isinstance(item, dict)
    ]
    if filtered_aliases != ["codex-spark"]:
        raise SystemExit(f"worker model catalog filtered aliases mismatch: {filtered_aliases!r}")
    explicit_spark = create_runtime_packet(
        role="worker",
        packet_id="B01-W06",
        branch="preparedness-research-fixture-W06",
        out_dir=catalog_packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        worker_route=["codex-spark"],
        selection_reason="Fixture intentionally selects unsupported Spark.",
        model_catalog=worker_model_catalog,
        expect=1,
    )
    if "model catalog rejects selected worker route" not in explicit_spark.stdout:
        raise SystemExit("explicit unsupported Spark route did not fail with model catalog guidance")
    manifest_packet_root = tmp_path / "manifest-packets"
    clean_worker_worktree = tmp_path / "clean-worker-worktree"
    clean_worker_worktree.mkdir()
    run(["git", "init", clean_worker_worktree.as_posix()])
    (clean_worker_worktree / "README.md").write_text("Preparedness worker context.\n", encoding="utf-8")
    create_runtime_packet(
        role="worker",
        packet_id="B01-W01",
        branch="B01",
        out_dir=manifest_packet_root,
        worktree=clean_worker_worktree,
        task_file=bundle / "branches" / "B01.prompt.md",
        manifest=bundle / "job.manifest.json",
        worker_route=["codex-mini"],
        selection_reason="Fixture exercises marker-wrapped Codex output.",
    )
    manifest_packet_dir = manifest_packet_root / "B01-W01"
    manifest_worker_prompt = (manifest_packet_dir / "prompt.md").read_text(encoding="utf-8")
    manifest_worker_config = read_json(manifest_packet_dir / "launch-config.json")
    if manifest_worker_config.get("branch") != "preparedness-research-fixture":
        raise SystemExit(f"worker --manifest did not normalize branch id to branch_name: {manifest_worker_config.get('branch')!r}")
    if manifest_worker_config.get("owned_files") != ["README.md"]:
        raise SystemExit(f"worker --manifest did not propagate manifest owned paths: {manifest_worker_config.get('owned_files')!r}")
    manifest_worker_schema = read_json(manifest_packet_dir / "status.schema.json")
    assert_openai_strict_schema(manifest_packet_dir / "status.schema.json", "manifest worker status schema")
    branch_const = manifest_worker_schema.get("properties", {}).get("branch", {}).get("const")
    if branch_const != "preparedness-research-fixture":
        raise SystemExit(f"worker --manifest status schema branch mismatch: {branch_const!r}")
    selected_ladder_schema = manifest_worker_schema.get("properties", {}).get("selected_ladder", {})
    if "const" in selected_ladder_schema:
        raise SystemExit(f"worker status schema must not use array const for selected_ladder: {selected_ladder_schema!r}")
    selected_ladder_config = manifest_worker_config.get("selected_ladder")
    if selected_ladder_schema.get("minItems") != len(selected_ladder_config) or selected_ladder_schema.get("maxItems") != len(selected_ladder_config):
        raise SystemExit(f"worker status schema should preserve exact selected_ladder length: {selected_ladder_schema!r}")
    selected_ladder_enum = selected_ladder_schema.get("items", {}).get("enum")
    if selected_ladder_enum != selected_ladder_config:
        raise SystemExit(f"worker status schema should enumerate selected_ladder aliases: {selected_ladder_schema!r}")
    if not (manifest_packet_dir / "packet-context.json").exists():
        raise SystemExit("worker --manifest did not create compact packet-context.json")
    manifest_packet_context = read_json(manifest_packet_dir / "packet-context.json")
    context_budget = manifest_packet_context.get("context_budget")
    if (
        not isinstance(context_budget, dict)
        or context_budget.get("status") != "pass"
        or context_budget.get("load_policy") != "bounded_context_pack_and_path_manifest"
        or context_budget.get("prompt_chars", 0) <= 0
    ):
        raise SystemExit(f"worker --manifest did not persist a passing context budget: {manifest_packet_context!r}")
    if manifest_worker_config.get("context_budget") != context_budget:
        raise SystemExit(f"worker launch-config should carry the packet context budget: {manifest_worker_config!r}")
    manifest_worker_route = read_json(manifest_packet_dir / "route.json")
    if manifest_worker_route.get("context_budget") != context_budget:
        raise SystemExit(f"worker route metadata should carry the packet context budget: {manifest_worker_route!r}")
    assert_contains(manifest_worker_prompt, "Compact Worker Task", "worker --manifest prompt")
    oversized_task = tmp_path / "oversized-runtime-task.md"
    oversized_task.write_text("# Oversized Task\n\n" + ("x" * 50000), encoding="utf-8")
    oversized_root = tmp_path / "oversized-runtime-packets"
    oversized_result = create_runtime_packet(
        role="worker",
        packet_id="B01-W98",
        branch="preparedness-research-fixture",
        out_dir=oversized_root,
        task_file=oversized_task,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture verifies packet context budget enforcement.",
        expect=1,
    )
    assert_contains(oversized_result.stdout, "runtime packet context budget exceeded before launch", "oversized packet budget fixture")
    if (oversized_root / "B01-W98" / "launch.sh").exists():
        raise SystemExit("oversized packet context budget must fail before writing launch.sh")
    replace_failure_root = tmp_path / "replace-failure-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W97",
        branch="preparedness-research-fixture",
        out_dir=replace_failure_root,
        task_file=task_file,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture creates a reusable packet before failed replacement.",
    )
    replace_failure_dir = replace_failure_root / "B01-W97"
    (replace_failure_dir / "current-marker.txt").write_text("must survive failed replacement\n", encoding="utf-8")
    replace_failure = create_runtime_packet(
        role="worker",
        packet_id="B01-W97",
        branch="preparedness-research-fixture",
        out_dir=replace_failure_root,
        task_file=oversized_task,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture verifies failed replacement keeps current packet artifacts.",
        extra_args=["--replace"],
        expect=1,
    )
    assert_contains(replace_failure.stdout, "runtime packet context budget exceeded before launch", "failed replace budget fixture")
    if not (replace_failure_dir / "launch.sh").exists() or not (replace_failure_dir / "current-marker.txt").exists():
        raise SystemExit("failed packet replacement must leave the current packet directory intact")
    if (replace_failure_dir / "attempts" / "attempt-001").exists():
        raise SystemExit("failed packet replacement must not archive the current packet before budget validation")
    excerpt_packet_root = tmp_path / "excerpt-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W99",
        branch="preparedness-research-fixture",
        out_dir=excerpt_packet_root,
        task_file=bundle / "branches" / "B01.prompt.md",
        owned_files=["README.md"],
        context_files=[ROOT / "README.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture exercises bounded worktree context excerpts.",
        extra_args=["--include-worktree-context-excerpts"],
    )
    excerpt_worker_prompt = (excerpt_packet_root / "B01-W99" / "prompt.md").read_text(encoding="utf-8")
    assert_contains(excerpt_worker_prompt, "BEGIN_CONTEXT_EXCERPT context-1: README.md", "worker excerpt prompt")
    create_runtime_packet(
        role="worker",
        packet_id="B01-W99",
        branch="preparedness-research-fixture",
        out_dir=excerpt_packet_root,
        task_file=bundle / "branches" / "B01.prompt.md",
        owned_files=["README.md"],
        context_files=[ROOT / "README.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture exercises packet replacement stale index.",
        extra_args=["--replace"],
    )
    stale_index = read_json(excerpt_packet_root / "B01-W99" / "stale-artifacts.index.json")
    stale_entries = stale_index.get("entries", [])
    if not stale_entries or not all(item.get("excluded_from_current_evidence") is True for item in stale_entries if isinstance(item, dict)):
        raise SystemExit(f"packet replacement should write stale-artifact exclusion index: {stale_index!r}")
    if any(
        not item.get("superseding_artifact") and not item.get("terminal_reason")
        for item in stale_entries
        if isinstance(item, dict)
    ):
        raise SystemExit(f"packet replacement stale entries should carry lineage: {stale_index!r}")
    fake_codex_dir = tmp_path / "fake-codex-worker"
    fake_codex_dir.mkdir()
    fake_codex = fake_codex_dir / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "prompt = sys.stdin.read()\n"
        "if not prompt.strip():\n"
        "    print('No prompt provided via stdin.', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "cfg = json.loads((out.parent / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "status = {\n"
        "  'packet_id': 'B01-W01',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'fake codex marker-wrapped pass evidence',\n"
        "  'branch': 'preparedness-research-fixture',\n"
        f"  'worktree': {clean_worker_worktree.as_posix()!r},\n"
        "  'route_class': 'normal-code',\n"
        "  'selected_ladder': ['codex-mini'],\n"
        "  'selection_reason': 'Fixture exercises marker-wrapped Codex output.',\n"
        "  'changed_files': [],\n"
        "  'commands_run': ['pwd', 'git status --short --branch'],\n"
        "  'tests': ['fixture fake codex'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'fake codex marker-wrapped pass'\n"
        "}\n"
        "out.write_text('BEGIN_WORKER_STATUS_JSON\\n' + json.dumps(status) + '\\nEND_WORKER_STATUS_JSON\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 321, 'cached_input_tokens': 123, 'output_tokens': 45}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    run(
        [(manifest_packet_dir / "launch.sh").as_posix()],
        env={**os.environ, "PATH": fake_codex_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    manifest_worker_status = read_json(manifest_packet_dir / "status.json")
    if manifest_worker_status.get("branch") != "preparedness-research-fixture":
        raise SystemExit(f"worker runtime did not preserve normalized branch label: {manifest_worker_status!r}")
    raw_status_text = (manifest_packet_dir / "status.json.raw").read_text(encoding="utf-8")
    if "BEGIN_WORKER_STATUS_JSON" not in raw_status_text:
        raise SystemExit("worker runtime did not preserve raw marker-wrapped Codex output")
    manifest_worker_telemetry = read_json(manifest_packet_dir / "telemetry.json")
    usage = manifest_worker_telemetry.get("totals", {}).get("known_usage", {})
    if manifest_worker_telemetry.get("accepted_alias") != "codex-mini" or usage.get("input_tokens") != 321:
        raise SystemExit(f"worker runtime did not extract accepted Codex telemetry after marker normalization: {manifest_worker_telemetry!r}")
    if manifest_worker_telemetry.get("route_class") != "normal-code":
        raise SystemExit(f"worker telemetry did not preserve route_class: {manifest_worker_telemetry!r}")
    manifest_worker_summary = read_json(manifest_packet_dir / "packet.summary.json")
    if (
        manifest_worker_summary.get("output_status") != "pass"
        or manifest_worker_summary.get("terminal_state") != "pass"
        or manifest_worker_summary.get("next_action") != "validate_and_collect"
    ):
        raise SystemExit(f"worker packet summary did not capture terminal pass: {manifest_worker_summary!r}")
    if (
        manifest_worker_summary.get("attempts", [{}])[0].get("alias") != "codex-mini"
        or manifest_worker_summary.get("attempts", [{}])[0].get("failure_class") != "none"
    ):
        raise SystemExit(f"worker packet summary omitted route attempt metadata: {manifest_worker_summary!r}")

    standalone_blocked_root = tmp_path / "standalone-blocked-packets"
    standalone_worktree = tmp_path / "standalone-blocked-worktree"
    standalone_worktree.mkdir()
    run(["git", "init", standalone_worktree.as_posix()])
    (standalone_worktree / "README.md").write_text("Standalone worker fixture.\n", encoding="utf-8")
    create_runtime_packet(
        role="worker",
        packet_id="B99-W01",
        branch="standalone-no-manifest",
        out_dir=standalone_blocked_root,
        worktree=standalone_worktree,
        task_file=task_file,
        owned_files=["README.md"],
        worker_route=["codex-mini"],
        selection_reason="Fixture verifies no-manifest worker terminal fallback.",
    )
    standalone_packet = standalone_blocked_root / "B99-W01"
    standalone_config = read_json(standalone_packet / "launch-config.json")
    if standalone_config.get("work_item_id") != "" or standalone_config.get("manifest_hash") != "":
        raise SystemExit(f"standalone worker fixture should have empty manifest attribution fields: {standalone_config!r}")
    fake_failed_codex_dir = tmp_path / "fake-codex-standalone-blocked"
    fake_failed_codex_dir.mkdir()
    fake_failed_codex = fake_failed_codex_dir / "codex"
    fake_failed_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "print('standalone no-manifest worker failed cleanly')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    fake_failed_codex.chmod(0o755)
    run(
        [(standalone_packet / "launch.sh").as_posix()],
        env={**os.environ, "PATH": fake_failed_codex_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
        expect=1,
    )
    standalone_status = read_json(standalone_packet / "status.json")
    if (
        standalone_status.get("status") != "blocked"
        or standalone_status.get("work_item_id") != ""
        or standalone_status.get("manifest_hash") != ""
    ):
        raise SystemExit(f"standalone no-manifest worker should write blocked status with empty attribution fields: {standalone_status!r}")
    standalone_summary = read_json(standalone_packet / "packet.summary.json")
    if standalone_summary.get("output_status") != "blocked" or standalone_summary.get("terminal_state") != "blocked":
        raise SystemExit(f"standalone no-manifest worker should write blocked packet summary: {standalone_summary!r}")

    cache_fallback_worktree = tmp_path / "cache-fallback-worktree"
    cache_fallback_worktree.mkdir()
    run(["git", "init", cache_fallback_worktree.as_posix()])
    cache_fallback_root = tmp_path / "cache-fallback-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W08",
        branch="preparedness-cache-fallback",
        out_dir=cache_fallback_root,
        worktree=cache_fallback_worktree,
        task_file=bundle / "branches" / "B01.prompt.md",
        owned_files=["README.md"],
        worker_route=["codex-spark", "codex-mini"],
        selection_reason="Fixture verifies runtime cache-only dirt does not block fallback.",
    )
    cache_fallback_packet = cache_fallback_root / "B01-W08"
    cache_fallback_fake_dir = tmp_path / "fake-codex-cache-fallback"
    cache_fallback_fake_dir.mkdir()
    cache_fallback_fake = cache_fallback_fake_dir / "codex"
    cache_fallback_state = cache_fallback_fake_dir / "calls.txt"
    cache_fallback_fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"state = pathlib.Path({cache_fallback_state.as_posix()!r})\n"
        "count = int(state.read_text(encoding='utf-8')) + 1 if state.exists() else 1\n"
        "state.write_text(str(count), encoding='utf-8')\n"
        "worktree = pathlib.Path(args[args.index('-C') + 1])\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "cfg = json.loads((out.parent / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "if count == 1:\n"
        "    cache = worktree / '.pytest_cache' / 'v' / 'cache'\n"
        "    cache.parent.mkdir(parents=True, exist_ok=True)\n"
        "    cache.write_text('generated cache only\\n', encoding='utf-8')\n"
        "    pycache = worktree / 'pkg' / '__pycache__'\n"
        "    pycache.mkdir(parents=True, exist_ok=True)\n"
        "    (pycache / 'module.cpython-312.pyc').write_bytes(b'generated pyc')\n"
        "    egg_info = worktree / 'src' / 'pkg.egg-info'\n"
        "    egg_info.mkdir(parents=True, exist_ok=True)\n"
        "    (egg_info / 'PKG-INFO').write_text('generated egg-info\\n', encoding='utf-8')\n"
        "    (worktree / 'stray.pyc').write_bytes(b'generated pyc')\n"
        "    sys.exit(1)\n"
        "status = {\n"
        "  'packet_id': 'B01-W08',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'fallback passed after cache-only failed attempt',\n"
        "  'branch': 'preparedness-cache-fallback',\n"
        f"  'worktree': {cache_fallback_worktree.as_posix()!r},\n"
        "  'route_class': 'custom',\n"
        "  'selected_ladder': ['codex-spark', 'codex-mini'],\n"
        "  'selection_reason': 'Fixture verifies runtime cache-only dirt does not block fallback.',\n"
        "  'changed_files': [],\n"
        "  'commands_run': ['git status --short'],\n"
        "  'tests': ['fixture fake codex fallback'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'fallback passed after cache-only failed attempt'\n"
        "}\n"
        "out.write_text('BEGIN_WORKER_STATUS_JSON\\n' + json.dumps(status) + '\\nEND_WORKER_STATUS_JSON\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 111, 'cached_input_tokens': 0, 'output_tokens': 22}}))\n",
        encoding="utf-8",
    )
    cache_fallback_fake.chmod(0o755)
    run(
        [(cache_fallback_packet / "launch.sh").as_posix()],
        env={**os.environ, "PATH": cache_fallback_fake_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    cache_fallback_status = read_json(cache_fallback_packet / "status.json")
    if cache_fallback_status.get("status") != "pass":
        raise SystemExit(f"cache-only fallback worker should pass: {cache_fallback_status!r}")
    cache_fallback_launcher = read_json(cache_fallback_packet / "launcher-state.json")
    if cache_fallback_launcher.get("terminal_state") != "pass":
        raise SystemExit(f"cache-only fallback should finish on second attempt: {cache_fallback_launcher!r}")
    pass_events = [
        event
        for event in cache_fallback_launcher.get("events", [])
        if isinstance(event, dict) and event.get("state") == "pass"
    ]
    if len(pass_events) != 1 or pass_events[0].get("attempt_index") != 1:
        raise SystemExit(f"cache-only fallback should pass on second attempt: {cache_fallback_launcher!r}")
    cache_fallback_summary = read_json(cache_fallback_packet / "packet.summary.json")
    if cache_fallback_summary.get("terminal_state") != "pass" or len(cache_fallback_summary.get("attempts", [])) != 2:
        raise SystemExit(f"cache-only fallback packet summary mismatch: {cache_fallback_summary!r}")
    if cache_fallback_summary.get("attempts", [{}])[0].get("failure_class") != "codex_failure":
        raise SystemExit(f"cache-only fallback summary should classify first failed route: {cache_fallback_summary!r}")
    cleanup_summary = cache_fallback_summary.get("generated_artifact_cleanup", {})
    if (
        not cache_fallback_summary.get("generated_artifact_cleanup_exists")
        or cleanup_summary.get("status") != "pass"
        or cleanup_summary.get("removed_count", 0) < 4
        or cleanup_summary.get("failed_count") != 0
    ):
        raise SystemExit(f"cache-only fallback should summarize generated-artifact cleanup: {cache_fallback_summary!r}")
    first_attempt_cleanup = cache_fallback_summary.get("attempts", [{}])[0].get("generated_artifact_cleanup", {})
    if first_attempt_cleanup.get("removed_count", 0) < 4 or first_attempt_cleanup.get("generated_artifacts_only") is not True:
        raise SystemExit(f"first fallback attempt should record generated-only cleanup: {cache_fallback_summary!r}")
    cleanup_artifact = read_json(cache_fallback_packet / "generated-artifact-cleanup.json")
    cleanup_records = cleanup_artifact.get("attempts", [])
    removed_paths = {
        path
        for record in cleanup_records
        if isinstance(record, dict)
        for path in record.get("removed", [])
        if isinstance(path, str)
    }
    expected_removed = {".pytest_cache", "pkg/__pycache__", "src/pkg.egg-info", "stray.pyc"}
    if not expected_removed.issubset(removed_paths):
        raise SystemExit(f"generated cleanup did not remove expected artifact roots: {cleanup_artifact!r}")
    for relative_path in expected_removed:
        if (cache_fallback_worktree / relative_path).exists():
            raise SystemExit(f"generated artifact survived runner cleanup: {relative_path}")

    stale_output_worktree = tmp_path / "stale-output-fallback-worktree"
    stale_output_worktree.mkdir()
    run(["git", "init", stale_output_worktree.as_posix()])
    stale_output_root = tmp_path / "stale-output-fallback-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W13",
        branch="preparedness-stale-output-fallback",
        out_dir=stale_output_root,
        worktree=stale_output_worktree,
        task_file=bundle / "branches" / "B01.prompt.md",
        owned_files=["README.md"],
        worker_route=["codex-spark", "codex-mini"],
        selection_reason="Fixture verifies invalid stale output is cleared before fallback.",
    )
    stale_output_packet = stale_output_root / "B01-W13"
    stale_output_config = read_json(stale_output_packet / "launch-config.json")
    retry_attempt = dict(stale_output_config["attempts"][-1])
    retry_attempt["event_logs"] = ["events-mini-retry.jsonl"]
    stale_output_config["attempts"].append(retry_attempt)
    write_json(stale_output_packet / "launch-config.json", stale_output_config)
    stale_output_fake_dir = tmp_path / "fake-codex-stale-output-fallback"
    stale_output_fake_dir.mkdir()
    stale_output_fake = stale_output_fake_dir / "codex"
    stale_output_state = stale_output_fake_dir / "calls.txt"
    stale_output_fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        f"state = pathlib.Path({stale_output_state.as_posix()!r})\n"
        "count = int(state.read_text(encoding='utf-8')) + 1 if state.exists() else 1\n"
        "state.write_text(str(count), encoding='utf-8')\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "cfg = json.loads((out.parent / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "if count == 1:\n"
        "    out.write_text(json.dumps({'status': 'pass', 'evidence_summary': 'missing required fields'}) + '\\n', encoding='utf-8')\n"
        "    sys.exit(0)\n"
        "if count == 2:\n"
        "    print('fallback exited without writing a fresh status')\n"
        "    sys.exit(1)\n"
        "status = {\n"
        "  'packet_id': 'B01-W13',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'third fallback passed after stale invalid output was cleared',\n"
        "  'branch': 'preparedness-stale-output-fallback',\n"
        f"  'worktree': {stale_output_worktree.as_posix()!r},\n"
        "  'route_class': 'custom',\n"
        "  'selected_ladder': cfg['selected_ladder'],\n"
        "  'selection_reason': 'Fixture verifies invalid stale output is cleared before fallback.',\n"
        "  'changed_files': [],\n"
        "  'commands_run': ['git status --short'],\n"
        "  'tests': ['fixture stale output fallback'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'third fallback passed after stale invalid output was cleared'\n"
        "}\n"
        "out.write_text('BEGIN_WORKER_STATUS_JSON\\n' + json.dumps(status) + '\\nEND_WORKER_STATUS_JSON\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 131, 'cached_input_tokens': 0, 'output_tokens': 23}}))\n",
        encoding="utf-8",
    )
    stale_output_fake.chmod(0o755)
    run(
        [(stale_output_packet / "launch.sh").as_posix()],
        env={**os.environ, "PATH": stale_output_fake_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    stale_output_status = read_json(stale_output_packet / "status.json")
    if stale_output_status.get("status") != "pass":
        raise SystemExit(f"stale invalid output fallback should reach third pass attempt: {stale_output_status!r}")
    stale_output_launcher = read_json(stale_output_packet / "launcher-state.json")
    pass_events = [
        event
        for event in stale_output_launcher.get("events", [])
        if isinstance(event, dict) and event.get("state") == "pass"
    ]
    if len(pass_events) != 1 or pass_events[0].get("attempt_index") != 2:
        raise SystemExit(f"stale invalid output fallback should pass on third attempt: {stale_output_launcher!r}")
    stale_output_summary = read_json(stale_output_packet / "packet.summary.json")
    if (
        len(stale_output_summary.get("attempts", [])) != 3
        or stale_output_summary.get("attempts", [{}])[0].get("failure_class") != "schema_or_output_readback"
        or stale_output_summary.get("attempts", [{}, {}, {}])[2].get("state") != "pass"
    ):
        raise SystemExit(f"stale invalid output fallback summary should preserve all route attempts: {stale_output_summary!r}")

    shared_dirty_worktree = tmp_path / "shared-dirty-worktree"
    shared_dirty_worktree.mkdir()
    run(["git", "init", shared_dirty_worktree.as_posix()])
    (shared_dirty_worktree / "foreign.txt").write_text("existing worker output\n", encoding="utf-8")
    shared_dirty_root = tmp_path / "shared-dirty-packets"
    create_runtime_packet(
        role="worker",
        packet_id="B01-W09",
        branch="preparedness-shared-dirty",
        out_dir=shared_dirty_root,
        worktree=shared_dirty_worktree,
        task_file=bundle / "branches" / "B01.prompt.md",
        owned_files=["owned.txt"],
        worker_route=["codex-mini"],
        selection_reason="Fixture verifies shared branch dirt does not block later workers.",
    )
    shared_dirty_packet = shared_dirty_root / "B01-W09"
    shared_dirty_fake_dir = tmp_path / "fake-codex-shared-dirty"
    shared_dirty_fake_dir.mkdir()
    shared_dirty_fake = shared_dirty_fake_dir / "codex"
    shared_dirty_fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "worktree = pathlib.Path(args[args.index('-C') + 1])\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "cfg = json.loads((out.parent / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "(worktree / 'owned.txt').write_text('owned worker output\\n', encoding='utf-8')\n"
        "status = {\n"
        "  'packet_id': 'B01-W09',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'later worker passed with preexisting branch dirt',\n"
        "  'branch': 'preparedness-shared-dirty',\n"
        f"  'worktree': {shared_dirty_worktree.as_posix()!r},\n"
        "  'route_class': 'custom',\n"
        "  'selected_ladder': ['codex-mini'],\n"
        "  'selection_reason': 'Fixture verifies shared branch dirt does not block later workers.',\n"
        "  'changed_files': ['owned.txt'],\n"
        "  'commands_run': ['git status --short'],\n"
        "  'tests': ['fixture fake codex shared dirty'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'later worker passed with preexisting branch dirt'\n"
        "}\n"
        "out.write_text('BEGIN_WORKER_STATUS_JSON\\n' + json.dumps(status) + '\\nEND_WORKER_STATUS_JSON\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 211, 'cached_input_tokens': 0, 'output_tokens': 33}}))\n",
        encoding="utf-8",
    )
    shared_dirty_fake.chmod(0o755)
    run(
        [(shared_dirty_packet / "launch.sh").as_posix()],
        env={**os.environ, "PATH": shared_dirty_fake_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    shared_dirty_status = read_json(shared_dirty_packet / "status.json")
    if shared_dirty_status.get("status") != "pass":
        raise SystemExit(f"shared dirty worker should pass: {shared_dirty_status!r}")
    shared_dirty_summary = read_json(shared_dirty_packet / "packet.summary.json")
    if shared_dirty_summary.get("changed_files") != ["owned.txt"]:
        raise SystemExit(f"shared dirty packet summary should record this packet delta only: {shared_dirty_summary!r}")

    ownership_brief = {
        "job_id": "worker-ownership-blocked-fixture",
        "goal": "Validate blocked ownership evidence preservation.",
        "source_summary": "Fixture builds one worker packet that writes outside owned paths.",
        "required_evidence": ["Blocked ownership status validates as branch evidence."],
        "final_dod": ["Validator preserves non-pass changed_files outside owned paths."],
        "branches": [
            {
                "id": "B01",
                "branch_name": "ownership-fixture",
                "objective": "Exercise worker ownership violation handling.",
                "max_active_worker_packets": 1,
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Write outside owned paths to trigger launcher ownership block.",
                        "owned_paths": ["owned.txt"],
                        "context_files": [],
                        "verification": ["python3 -c 'print(\"ownership fixture\")'"],
                        "dod": ["Ownership violation is preserved as blocked evidence."],
                    }
                ],
            }
        ],
    }
    ownership_brief_path = tmp_path / "worker-ownership-blocked-brief.json"
    ownership_bundle = tmp_path / "worker-ownership-blocked-bundle"
    write_json(ownership_brief_path, ownership_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            ownership_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            ownership_bundle.as_posix(),
        ]
    )
    ownership_worktree = tmp_path / "worker-ownership-blocked-worktree"
    ownership_worktree.mkdir()
    run(["git", "init", ownership_worktree.as_posix()])
    create_runtime_packet(
        role="worker",
        packet_id="B01-W01",
        branch="B01",
        out_dir=ownership_bundle / "workers",
        worktree=ownership_worktree,
        task_file=ownership_bundle / "branches" / "B01.prompt.md",
        manifest=ownership_bundle / "job.manifest.json",
        worker_route=["codex-mini"],
        selection_reason="Fixture intentionally writes outside owned paths.",
    )
    ownership_packet = ownership_bundle / "workers" / "B01-W01"
    ownership_fake_dir = tmp_path / "fake-codex-ownership-blocked"
    ownership_fake_dir.mkdir()
    ownership_fake = ownership_fake_dir / "codex"
    ownership_fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "worktree = pathlib.Path(args[args.index('-C') + 1])\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "cfg = json.loads((out.parent / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "(worktree / 'outside.txt').write_text('outside ownership\\n', encoding='utf-8')\n"
        "status = {\n"
        "  'packet_id': 'B01-W01',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'fake pass should be converted to ownership blocked',\n"
        "  'branch': 'ownership-fixture',\n"
        f"  'worktree': {ownership_worktree.as_posix()!r},\n"
        "  'route_class': 'normal-code',\n"
        "  'selected_ladder': ['codex-mini'],\n"
        "  'selection_reason': 'Fixture intentionally writes outside owned paths.',\n"
        "  'changed_files': ['outside.txt'],\n"
        "  'commands_run': ['git status --short'],\n"
        "  'tests': ['fixture fake codex ownership blocked'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'fake pass should be converted to ownership blocked'\n"
        "}\n"
        "out.write_text('BEGIN_WORKER_STATUS_JSON\\n' + json.dumps(status) + '\\nEND_WORKER_STATUS_JSON\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 311, 'cached_input_tokens': 0, 'output_tokens': 44}}))\n",
        encoding="utf-8",
    )
    ownership_fake.chmod(0o755)
    run(
        [(ownership_packet / "launch.sh").as_posix()],
        expect=2,
        env={**os.environ, "PATH": ownership_fake_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    ownership_status = read_json(ownership_packet / "status.json")
    if ownership_status.get("status") != "blocked" or ownership_status.get("changed_files") != ["outside.txt"]:
        raise SystemExit(f"ownership violation should write blocked status with offending path: {ownership_status!r}")
    ownership_summary = read_json(ownership_packet / "packet.summary.json")
    if ownership_summary.get("output_status") != "blocked" or ownership_summary.get("next_action") != "close_blocked_or_create_repair":
        raise SystemExit(f"ownership packet summary should preserve blocked next action: {ownership_summary!r}")
    if ownership_summary.get("attempts", [{}])[0].get("failure_class") != "ownership":
        raise SystemExit(f"ownership packet summary should classify ownership blocker: {ownership_summary!r}")
    write_json(
        ownership_bundle / "schedulers" / "B01.worker.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "branch-worker-pool",
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "manifest_sha256": sha256_file(ownership_bundle / "job.manifest.json"),
            "capacity": 1,
            "item_ids": ["B01-W01"],
            "events": [
                scheduler_event(1, "ready", id="B01-W01"),
                scheduler_event(2, "launch", id="B01-W01"),
                scheduler_event(
                    3,
                    "blocked",
                    id="B01-W01",
                    reason_code="artifact_invalid",
                    reason="Worker changed files outside owned paths.",
                ),
                scheduler_event(4, "finish", id="B01-W01", status="blocked"),
                scheduler_event(5, "close", id="B01-W01"),
                scheduler_event(6, "refill", eligible_ids=["B01-W01"]),
                scheduler_event(
                    7,
                    "under_capacity",
                    eligible_ids=["B01-W01"],
                    reason_code="operator_requested",
                    reason="Fixture preserves ownership violation as terminal blocked evidence.",
                ),
            ],
        },
    )
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/assemble_branch_status.py",
            "--manifest",
            (ownership_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ownership_worktree.as_posix(),
            "--replace",
        ]
    )
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/validate_branch_status.py",
            "--manifest",
            (ownership_bundle / "job.manifest.json").as_posix(),
            "--status",
            (ownership_bundle / "branches" / "B01.status.json").as_posix(),
            "--branch-id",
            "B01",
            "--branch",
            "ownership-fixture",
            "--worktree",
            ownership_worktree.as_posix(),
        ]
    )

    create_runtime_packet(
        role="research-worker",
        packet_id="B01-W03",
        branch="preparedness-research-fixture-W03",
        out_dir=packet_root,
        owned_files=["README.md"],
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
    )
    research_config = assert_compact_runtime_launcher(packet_root / "B01-W03", "research-worker", 1200)
    assert_research_worker_preserves_user_config(research_config, expected_event_logs=["events-primary.jsonl", "events-fallback.jsonl"])
    return packet_root, task_file


def run_opencode_wal_fallback_fixture(tmp_path: Path) -> None:
    fallback_root = tmp_path / "opencode-wal-fallback-packets"
    fallback_root.mkdir()
    fallback_worktree = tmp_path / "opencode-wal-fallback-worktree"
    fallback_worktree.mkdir()
    run(["git", "init", fallback_worktree.as_posix()])
    fallback_task = tmp_path / "opencode-wal-fallback-task.md"
    fallback_task.write_text("Synthetic task for opencode WAL fallback fixture.\n", encoding="utf-8")
    create_runtime_packet(
        role="worker",
        packet_id="B01-W01",
        branch="opencode-wal-fallback-fixture",
        out_dir=fallback_root,
        worktree=fallback_worktree,
        task_file=fallback_task,
        worker_route=["codex-mini"],
        selection_reason="Fixture validates opencode WAL failure is treated as harness_unavailable and fallback to codex route.",
    )
    packet_dir = fallback_root / "B01-W01"
    launch_config = read_json(packet_dir / "launch-config.json")
    if not isinstance(launch_config, dict):
        raise SystemExit(f"opencode WAL fixture should create launch-config: {launch_config!r}")
    attempts = launch_config.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise SystemExit(f"opencode WAL fixture launch-config should include attempts: {launch_config!r}")
    codex_attempt = attempts[0]
    if not isinstance(codex_attempt, dict):
        raise SystemExit(f"opencode WAL fixture codex attempt should be a mapping: {attempts!r}")
    opencode_attempt = dict(codex_attempt)
    opencode_attempt.update(
        {
            "alias": "wal_opencode",
            "provider": "opencode",
            "provider_id": "deepseek",
            "harness": "opencode",
            "harness_kind": "opencode",
            "command": "opencode run --pure --format json --model deepseek/opencode-wal-fixture --dir <worktree> <prompt>",
            "command_binary": "opencode",
            "event_logs": ["events-wal_opencode.jsonl"],
            "run_args": ["run", "--pure", "--format", "json", "--model", "{model}", "--dir", "{worktree}", "{prompt}"],
            "run_readback": "opencode_session_db",
            "probe_logs": [],
            "telemetry_capability": {
                "source": "provider_or_harness_output",
                "token_usage": "best_effort",
            },
            "model": "deepseek/unsupported",
            "event": "attempt",
            "attempt": codex_attempt.get("alias", "codex-mini"),
        }
    )
    launch_config["attempts"] = [opencode_attempt, codex_attempt]
    launch_config["selected_commands"] = [
        opencode_attempt["command"],
        codex_attempt.get("command", ""),
    ]
    write_json(packet_dir / "launch-config.json", launch_config)
    fake_bin_dir = tmp_path / "fake-opencode-fallback-bins"
    fake_bin_dir.mkdir()
    fake_opencode = fake_bin_dir / "opencode"
    fake_opencode.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write(\"Failed to run the query \'PRAGMA journal_mode = WAL\\'\\n\")\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)
    fake_codex = fake_bin_dir / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "out = None\n"
        "for index, item in enumerate(args):\n"
        "    if item == '-o' and index + 1 < len(args):\n"
        "        out = pathlib.Path(args[index + 1])\n"
        "        break\n"
        "if out is None:\n"
        "    raise SystemExit('missing -o output path')\n"
        "cfg = json.loads((pathlib.Path.cwd() / 'launch-config.json').read_text(encoding='utf-8'))\n"
        "status = {\n"
        "  'packet_id': cfg['packet_id'],\n"
        "  'role': cfg['role'],\n"
        "  'status': 'pass',\n"
        "  'branch_id': cfg['branch_id'],\n"
        "  'work_item_id': cfg['work_item_id'],\n"
        "  'manifest_hash': cfg['manifest_hash'],\n"
        "  'manifest_epoch': cfg['manifest_epoch'],\n"
        "  'worktree_path': cfg['worktree_path'],\n"
        "  'route_id': cfg['route_id'],\n"
        "  'evidence_summary': 'Fake codex route passed after opencode infrastructure failure',\n"
        "  'branch': cfg['branch'],\n"
        "  'worktree': cfg['worktree'],\n"
        "  'route_class': cfg['route_class'],\n"
        "  'selected_ladder': cfg['selected_ladder'],\n"
        "  'selection_reason': cfg['selection_reason'],\n"
        "  'changed_files': [],\n"
        "  'commands_run': ['codex pass after fallback'],\n"
        "  'tests': ['fixture fake codex fallback passed'],\n"
        "  'blockers': [],\n"
        "  'handoff': 'fake codex passed after opencode fallback',\n"
        "}\n"
        "out.write_text(json.dumps(status) + '\\n', encoding='utf-8')\n"
        "print(json.dumps({'usage': {'input_tokens': 111, 'cached_input_tokens': 22, 'output_tokens': 9}}))\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py",
            "--packet-dir",
            packet_dir.as_posix(),
        ],
        env={**os.environ, "PATH": fake_bin_dir.as_posix() + os.pathsep + os.environ.get("PATH", "")},
    )
    summary = read_json(packet_dir / "packet.summary.json")
    if summary.get("terminal_state") != "pass":
        raise SystemExit(f"opencode WAL fallback fixture should pass after codex fallback: {summary!r}")
    attempts = summary.get("attempts")
    if not isinstance(attempts, list) or len(attempts) < 2:
        raise SystemExit(f"opencode WAL fallback fixture should report both attempts: {summary!r}")
    if attempts[0].get("failure_class") != "harness_unavailable":
        raise SystemExit(f"opencode WAL failing attempt should be harness_unavailable: {summary!r}")
    if attempts[1].get("state") != "pass":
        raise SystemExit(f"fallback codex attempt should pass in opencode WAL fixture: {summary!r}")
    launcher = read_json(packet_dir / "launcher-state.json")
    failed_opencode_events = [
        event for event in launcher.get("events", []) if isinstance(event, dict) and event.get("attempt_index") == 0
    ]
    if not any(
        event.get("failure_class") == "harness_unavailable"
        or event.get("failure_subclass") == "opencode_sqlite_wal_failure"
        for event in failed_opencode_events
    ):
        raise SystemExit(f"opencode WAL attempt should be classified as harness_unavailable: {launcher!r}")


def create_goal_fixture_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            BRIEF.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            bundle.as_posix(),
        ]
    )
    brief_lint = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/lint_preflight_brief.py",
                "--brief",
                BRIEF.as_posix(),
                "--repo-root",
                ROOT.as_posix(),
                "--json",
                "--output",
                (bundle / "preflight.brief.lint.json").as_posix(),
            ]
        ).stdout
    )
    if brief_lint.get("status") != "pass":
        raise SystemExit(f"canonical brief lint fixture should pass: {brief_lint!r}")
    external_lint_path = tmp_path / "bundle-lint-outside.json"
    lint_report = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/lint_goal_bundle.py",
                "--bundle-dir",
                bundle.as_posix(),
                "--json",
                "--output",
                external_lint_path.as_posix(),
            ]
        ).stdout
    )
    canonical_lint_path = bundle / "preflight.lint.json"
    if read_json(external_lint_path) != lint_report or read_json(canonical_lint_path) != lint_report:
        raise SystemExit("lint_goal_bundle.py --output should also persist canonical bundle/preflight.lint.json")
    if lint_report.get("bundle_path") != bundle.resolve().as_posix():
        raise SystemExit(f"lint report should include bundle_path: {lint_report.get('bundle_path')!r}")
    if lint_report.get("manifest_path") != (bundle / "job.manifest.json").resolve().as_posix():
        raise SystemExit(f"lint report should include manifest_path: {lint_report.get('manifest_path')!r}")
    if not isinstance(lint_report.get("branch_count"), int) or lint_report.get("branch_count", 0) < 1:
        raise SystemExit(f"lint report should include a positive branch_count: {lint_report.get('branch_count')!r}")
    if lint_report.get("config_check_status") != "not_configured":
        raise SystemExit(f"lint report should expose config_check_status: {lint_report.get('config_check_status')!r}")
    git_repo_status = lint_report.get("git_repo_status")
    if (
        not isinstance(git_repo_status, dict)
        or not isinstance(git_repo_status.get("repo_is_git"), bool)
        or git_repo_status.get("base_ref_status") not in {"exists", "not_checked", "missing", "not_requested"}
    ):
        raise SystemExit(f"lint report should expose git repo status metadata: {git_repo_status!r}")
    if lint_report.get("compatibility_status") != "not_applicable":
        raise SystemExit(f"lint report should expose compatibility status metadata: {lint_report.get('compatibility_status')!r}")
    if lint_report.get("schema_lint_status") != "pass" or lint_report.get("runtime_launch_allowed") is not True:
        raise SystemExit(f"lint report should distinguish schema lint from runtime launch readiness: {lint_report!r}")
    return bundle


def run_repair_gate_fixture(bundle: Path) -> None:
    repair_gate_path = bundle / "repair-gate.json"
    decision = json.loads(
        run(
            [
                "python3",
                "skills/_goal_shared/scripts/script_only_repair_gate.py",
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--bundle-dir",
                bundle.as_posix(),
                "--scope",
                "preflight",
                "--json",
                "--output",
                repair_gate_path.as_posix(),
            ]
        ).stdout
    )
    if read_json(repair_gate_path) != decision:
        raise SystemExit("script_only_repair_gate.py --output should persist canonical repair-gate.json")
    if decision.get("decision") != "pass_no_actions":
        raise SystemExit(f"repair gate should return pass_no_actions on clean fixtures: {decision.get('decision')!r}")
    if decision.get("status") != "pass":
        raise SystemExit(f"repair gate should expose top-level status=pass on clean fixtures: {decision!r}")
    if decision.get("model_launch_allowed") is not True:
        raise SystemExit(f"repair gate should allow model launch when no actions are needed: {decision!r}")
    context_index = next((item for item in decision.get("checks", []) if item.get("name") == "context_index"), None)
    if context_index is None:
        raise SystemExit(f"repair gate should emit context_index check entry: {decision!r}")
    if context_index.get("status") != "skipped":
        raise SystemExit(f"context_index should be skipped without --repo-root: {context_index!r}")

    branch_bundle = bundle.parent / "branch-repair-gate"
    shutil.copytree(bundle, branch_bundle)
    write_worker_scheduler(branch_bundle)
    status_path = branch_bundle / "branches" / "B01.status.json"
    status_data = {
        "branch_id": "B01",
        "status": "blocked",
        "schema_status": "pass",
        "runtime_status": "blocked",
        "dod_status": "incomplete",
        "resume_action": "reuse_terminal_status",
        "branch": "preparedness-research-fixture",
        "worktree": ROOT.as_posix(),
        "worker_statuses": [],
        "worker_parallelism": {},
        "lite_advice": [],
        "review_status": "missing",
        "changed_files": [],
        "commands_run": ["git status --short --branch"],
        "tests": [],
        "dod_checklist": ["branch repair gate fixture"],
        "blockers": ["branch-scope blocker should not invoke plan amender"],
        "handoff": "branch repair gate fixture",
    }
    write_json(status_path, status_data)
    telemetry_path = branch_bundle / "workers" / "B01-W01" / "telemetry.json"
    write_json(telemetry_path, telemetry("B01-W01", "worker", "status.json", accepted_alias=None, attempts=[]))
    branch_decision = json.loads(
        run(
            [
                "python3",
                "skills/_goal_shared/scripts/script_only_repair_gate.py",
                "--manifest",
                (branch_bundle / "job.manifest.json").as_posix(),
                "--bundle-dir",
                branch_bundle.as_posix(),
                "--scope",
                "branch",
                "--branch-id",
                "B01",
                "--status",
                status_path.as_posix(),
                "--json",
            ]
        ).stdout
    )
    if branch_decision.get("decision") != "pass_no_actions":
        raise SystemExit(f"branch repair gate should not emit amender actions: {branch_decision!r}")
    action_kinds = [item.get("kind") for item in branch_decision.get("actions", []) if isinstance(item, dict)]
    if any(kind in {"amendment_eligibility", "blocker_repair_candidate"} for kind in action_kinds):
        raise SystemExit(f"branch repair gate leaked amender actions: {branch_decision!r}")
    telemetry_check = next((item for item in branch_decision.get("checks", []) if item.get("name") == "telemetry_summary"), None)
    if not telemetry_check or telemetry_check.get("status") != "pass" or "branch scope" not in telemetry_check.get("reason", ""):
        raise SystemExit(f"branch repair gate should skip bundle-wide telemetry freshness: {branch_decision!r}")
    if context_index.get("severity") != "info":
        raise SystemExit(f"context_index skip must be severity info: {context_index!r}")


def run_validator_command_fixtures(tmp_path: Path, bundle: Path) -> None:
    manifest_arg = (bundle / "job.manifest.json").as_posix()
    branch_status_glob = (bundle / "branches" / "Bxx.status.json").as_posix()
    main_status_path = (bundle / "main.status.json").as_posix()
    branch_status_path = (bundle / "branches" / "B01.status.json").as_posix()
    wrong_branch_status_path = (bundle / "branches" / "B99.status.json").as_posix()
    stale_validator_bundle = tmp_path / "stale-validator-command"
    shutil.copytree(bundle, stale_validator_bundle)
    stale_prompt = stale_validator_bundle / "main.prompt.md"
    stale_text = stale_prompt.read_text(encoding="utf-8")
    stale_text = stale_text.replace(
        f"validate_branch_status.py --manifest {manifest_arg} --status {branch_status_glob}",
        f"validate_branch_status.py --manifest {manifest_arg}",
        1,
    )
    stale_text = stale_text.replace(
        f"validate_main_status.py --manifest {manifest_arg} --status {main_status_path}",
        f"validate_main_status.py --manifest {manifest_arg}",
        1,
    )
    stale_prompt.write_text(stale_text, encoding="utf-8")
    stale_lint = run(
        ["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", stale_validator_bundle.as_posix(), "--no-write"],
        expect=1,
    )
    assert_all_contains(stale_lint.stdout, ["validate_branch_status.py command snippet must include", "validate_main_status.py command snippet must include"], "stale validator command lint")
    wrong_target_validator_bundle = tmp_path / "wrong-target-validator-command"
    shutil.copytree(bundle, wrong_target_validator_bundle)
    wrong_target_prompt = wrong_target_validator_bundle / "main.prompt.md"
    wrong_target_text = wrong_target_prompt.read_text(encoding="utf-8")
    wrong_target_text = wrong_target_text.replace(
        f"validate_branch_status.py --manifest {manifest_arg} --status {branch_status_glob}",
        f"validate_branch_status.py --manifest {manifest_arg} --status {main_status_path}",
        1,
    )
    wrong_target_text = wrong_target_text.replace(
        f"validate_main_status.py --manifest {manifest_arg} --status {main_status_path}",
        f"validate_main_status.py --manifest {manifest_arg} --status {(bundle / 'branches' / 'main.status.json').as_posix()}",
        1,
    )
    wrong_target_prompt.write_text(wrong_target_text, encoding="utf-8")
    wrong_target_branch_prompt = wrong_target_validator_bundle / "branches" / "B01.prompt.md"
    wrong_target_branch_text = wrong_target_branch_prompt.read_text(encoding="utf-8").replace(
        f"validate_branch_status.py --manifest {manifest_arg} --status {branch_status_path}",
        f"validate_branch_status.py --manifest {manifest_arg} --status {wrong_branch_status_path}",
        1,
    )
    wrong_target_branch_prompt.write_text(wrong_target_branch_text, encoding="utf-8")
    wrong_target_lint = run(
        ["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", wrong_target_validator_bundle.as_posix(), "--no-write"],
        expect=1,
    )
    assert_all_contains(wrong_target_lint.stdout, ["branches/Bxx.status.json", "branches/B01.status.json", "validate_main_status.py command snippet"], "wrong validator status target")
    stale_audit_dir = tmp_path / "stale-validator-audit"
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/run_prompt_audit_phase.py",
            "--manifest",
            (stale_validator_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit-dir",
            stale_audit_dir.as_posix(),
            "--deterministic",
            "--require-pass",
        ],
        expect=1,
    )
    stale_audit = read_json(stale_audit_dir / "prompt-audit.json")
    if stale_audit.get("status") != "failed" or stale_audit.get("can_start") is not False:
        raise SystemExit(f"deterministic prompt audit should fail stale validator snippets: {stale_audit!r}")
    warning_brief = {
        "job_id": "prompt-audit-warning-only",
        "base_ref": "main",
        "goal": "Exercise warning-only deterministic prompt audit validation.",
        "source_summary": "The fixture uses generated branch prompts with a serial dependency warning.",
        "required_evidence": ["Warning-only deterministic audit artifacts validate."],
        "final_dod": ["The prompt audit validator accepts normalized non-blocking severities."],
        "max_active_branch_agents": 2,
        "serial_reasons": ["Fixture intentionally serializes B02 after B01."],
        "branches": [
            branch_fixture("B01", "README.md"),
            branch_fixture("B02", "skills/goal-preflight/SKILL.md", depends_on=["B01"]),
        ],
    }
    warning_brief_path = tmp_path / "prompt-audit-warning-only.json"
    warning_bundle = tmp_path / "prompt-audit-warning-only"
    write_json(warning_brief_path, warning_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            warning_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            warning_bundle.as_posix(),
        ]
    )
    warning_audit_dir = warning_bundle / "audit"
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/deterministic_prompt_audit.py",
            "--manifest",
            (warning_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit-dir",
            warning_audit_dir.as_posix(),
        ]
    )
    warning_audit = read_json(warning_audit_dir / "prompt-audit.json")
    warning_severities = {item.get("severity") for item in warning_audit.get("defects", []) if isinstance(item, dict)}
    if warning_audit.get("status") != "pass" or "warning" in warning_severities or "minor" not in warning_severities:
        raise SystemExit(f"deterministic prompt audit should normalize warning-only defects to minor: {warning_audit!r}")
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/validate_prompt_audit.py",
            "--audit",
            (warning_audit_dir / "prompt-audit.json").as_posix(),
            "--manifest",
            (warning_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--require-pass",
        ]
    )


def run_phase_manifest_and_schema_fixtures(bundle: Path) -> None:
    preflight_phase_manifest = run(
        ["python3", "skills/goal-preflight/scripts/runtime_phase_manifest.py", "--markdown"]
    ).stdout
    full_phase_manifest = run(
        ["python3", "skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py", "--markdown"]
    ).stdout
    main_phase_manifest = run(
        ["python3", "skills/goal-main-orchestrator/scripts/runtime_phase_manifest.py", "--markdown"]
    ).stdout
    compact_phase_manifest = run(
        ["python3", "skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py", "--compact", "--markdown"]
    ).stdout
    if len(compact_phase_manifest) >= len(full_phase_manifest):
        raise SystemExit("compact runtime phase manifest should be shorter than default markdown")
    assert_all_contains(compact_phase_manifest, ["--manifest /abs/bundle/job.manifest.json", "rg/grep"], "compact phase manifest")
    assert_all_contains(preflight_phase_manifest, ["telemetry_mode=debug", "debug mode"], "preflight phase manifest")
    assert_all_contains(main_phase_manifest, ["watchdog", "orchestration_watchdog.main_no_completion_wait_limit", "reconcile_goal_run.py"], "main phase manifest")
    assert_all_contains(full_phase_manifest, ["watchdog", "orchestration_watchdog.branch_no_completion_wait_limit"], "branch phase manifest")
    readiness = run(
        [
            "python3",
            "skills/goal-preflight/scripts/render_goal_bootloader.py",
            "--bundle-dir",
            bundle.as_posix(),
            "--readiness",
        ]
    ).stdout
    assert_all_contains(
        readiness,
        [
            "status=",
            "config_compatibility=",
            "route_policy=",
            "caps:",
            "telemetry_mode=",
            "branch_dag:",
            "bootloader=",
            "git_status=",
            "runtime_gate=",
            "repair_gate=",
            "next_command=",
        ],
        "preflight readiness summary",
    )
    readiness_json = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/render_goal_bootloader.py",
                "--bundle-dir",
                bundle.as_posix(),
                "--readiness",
                "--json",
            ]
        ).stdout
    )
    readiness_output = bundle.parent / f"{bundle.name}-readiness.json"
    readiness_with_output = run(
        [
            "python3",
            "skills/goal-preflight/scripts/render_goal_bootloader.py",
            "--bundle-dir",
            bundle.as_posix(),
            "--readiness",
            "--json",
            "--output",
            readiness_output.as_posix(),
        ]
    ).stdout
    if json.loads(readiness_with_output) != json.loads(readiness_output.read_text(encoding="utf-8")):
        raise SystemExit("render_goal_bootloader.py --output should write the same readiness JSON it prints")
    for key in [
            "status",
            "launch_allowed",
            "launch_blockers",
            "bundle_dir",
            "bootloader_exists",
            "route_policy",
            "verified_routes",
            "prompt_size_report",
            "caps",
        "branch_dag",
        "lint_status",
        "repair_gate",
        "runtime_gate",
        "repo_status",
        "cleanup_plan",
        "next_commands",
    ]:
        if key not in readiness_json:
            raise SystemExit(f"readiness JSON missing {key}")
    if readiness_json.get("status") != "pass":
        raise SystemExit(f"canonical fixture readiness should pass: {readiness_json!r}")
    if readiness_json.get("launch_allowed") is not True or readiness_json.get("launch_blockers") != []:
        raise SystemExit(f"canonical fixture readiness should be launch-allowed: {readiness_json!r}")
    if "accepted_routes" in readiness_json:
        raise SystemExit("readiness JSON should not call unverified route policy accepted_routes")
    cleanup_plan = readiness_json.get("cleanup_plan", {})
    if not isinstance(cleanup_plan, dict) or "config_artifacts" not in cleanup_plan or "generated_artifact_roots" not in cleanup_plan:
        raise SystemExit(f"readiness cleanup plan should distinguish config and generated runtime artifacts: {cleanup_plan!r}")
    if readiness_json.get("lint_status", {}).get("brief_lint", {}).get("status") != "pass":
        raise SystemExit(f"readiness should find canonical preflight.brief.lint.json: {readiness_json.get('lint_status')!r}")
    if readiness_json.get("repair_gate", {}).get("status") != "pass":
        raise SystemExit(f"readiness should find canonical repair-gate.json: {readiness_json.get('repair_gate')!r}")
    manifest = read_json(bundle / "job.manifest.json")
    runtime_index = read_json(bundle / "runtime.index.json")
    if manifest.get("runtime_index_path") != "runtime.index.json":
        raise SystemExit(f"manifest must point at runtime.index.json: {manifest.get('runtime_index_path')!r}")
    if manifest.get("runtime_index_sha256") != sha256_file(bundle / "runtime.index.json").removeprefix("sha256:"):
        raise SystemExit("manifest runtime_index_sha256 must match runtime.index.json")
    if runtime_index.get("kind") != "goal-runtime-index":
        raise SystemExit(f"runtime index must expose goal-runtime-index kind: {runtime_index!r}")
    if runtime_index.get("counts", {}).get("branch_count") != len(manifest.get("branches", [])):
        raise SystemExit(f"runtime index branch count must match manifest: {runtime_index.get('counts')!r}")
    if runtime_index.get("route_contract", {}).get("sha256") != manifest.get("route_contract_sha256"):
        raise SystemExit("runtime index route contract hash must match manifest")
    indexed_workers = runtime_index.get("branches", [{}])[0].get("workers", [])
    indexed_worker = indexed_workers[0] if indexed_workers and isinstance(indexed_workers[0], dict) else {}
    expected_research_paths = {
        "worker_type": "research-worker",
        "status_path": "research/B01-W01/research.json",
        "summary_path": "research/B01-W01/packet.summary.json",
        "telemetry_path": "research/B01-W01/telemetry.json",
    }
    for key, expected in expected_research_paths.items():
        if indexed_worker.get(key) != expected:
            raise SystemExit(f"runtime index should point research workers at research artifacts: {indexed_worker!r}")
    if not isinstance(manifest.get("route_contract", {}).get("catalog_refresh_required"), bool):
        raise SystemExit(f"route contract must expose catalog refresh requirement: {manifest.get('route_contract')!r}")
    if manifest.get("ownership_feasibility", {}).get("status") not in {"pass", "needs_review"}:
        raise SystemExit(f"manifest must expose ownership feasibility status: {manifest.get('ownership_feasibility')!r}")
    if manifest.get("execution_strategy", {}).get("schema_version") != 1:
        raise SystemExit(f"manifest execution strategy must be schema v1: {manifest.get('execution_strategy')!r}")
    prompt_size = readiness_json.get("prompt_size_report", {})
    prompt_paths = [item.get("path") for item in prompt_size.get("files", []) if isinstance(item, dict)]
    if "runtime-rules.md" not in prompt_paths:
        raise SystemExit(f"readiness prompt-size report should include runtime-rules.md: {prompt_size!r}")
    if prompt_size.get("duplicated_section_counts", {}).get("shared_runtime_rules_extracted") is not True:
        raise SystemExit(f"readiness should report extracted shared runtime rules: {prompt_size!r}")
    assert_all_contains(json.dumps(readiness_json["lint_status"], sort_keys=True), ["bundle_lint", "brief_lint"], "readiness lint keys")
    assert_all_contains(
        (bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8"),
        ["Waves are dependency-aware scheduling/order groups", "Runtime readiness gate: pass", "depends_on", "branch"],
        "preflight report wave semantics",
    )
    branch_prompt = (bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    assert_all_contains(
        branch_prompt,
        [
            "## Additional Validators",
            "Declared worker packets:",
            "Configured package max worker packets per branch:",
            "Branch scheduler serial/under-capacity reasons:",
            "Worker scheduler serial/under-capacity reasons:",
            "Route-class ladders:",
            "Recommended ladder:",
            "Runtime index:",
            "Route contract sha256:",
            "Execution strategy:",
            "Execution setup commands:",
            "Ownership feasibility:",
        ],
        "branch prompt compact validator/cap wording",
    )
    assert_not_contains(branch_prompt, "## Tests And Validators", "branch prompt stale validators heading")
    assert_not_contains(branch_prompt, "small-edit/normal-code -> Codex Spark then Codex mini", "branch prompt stale route aliases")
    dod_tail = branch_prompt.split("## Definition of Done", 1)[1]
    assert_not_contains(dod_tail, "\n- none", "branch prompt DoD stray none")
    brief_schema = json.loads(
        run(["python3", "skills/goal-preflight/scripts/create_goal_bundle.py", "--brief-schema-json"]).stdout
    )
    if "work_item_required" not in brief_schema or "commands" not in brief_schema:
        raise SystemExit("brief schema output is missing required agent guidance")
    if "current git branch" not in str(brief_schema.get("top_level_optional", {}).get("base_ref", "")):
        raise SystemExit("brief schema should describe current-branch base_ref default")
    top_level_optional = brief_schema.get("top_level_optional", {})
    if "telemetry_mode" not in top_level_optional or "debug_telemetry" not in top_level_optional:
        raise SystemExit("brief schema should expose lean debug telemetry shorthands")
    if "source_attachments" not in top_level_optional:
        raise SystemExit("brief schema should expose structured source attachments")
    if "runtime_cap" not in top_level_optional:
        raise SystemExit("brief schema should expose runtime cap guidance")
    branch_optional = brief_schema.get("branch_optional", {})
    if "dependency_context_reason" not in branch_optional:
        raise SystemExit("brief schema should expose dependency_context_reason guidance")
    context_guidance = str(brief_schema.get("work_item_optional", {}).get("context_files", ""))
    if "must already exist" not in context_guidance or "owned_paths" not in context_guidance:
        raise SystemExit(f"brief schema should distinguish context_files from future owned outputs: {context_guidance!r}")
    lint_schema = json.loads(
        run(["python3", "skills/goal-preflight/scripts/lint_preflight_brief.py", "--brief-schema-json"]).stdout
    )
    if lint_schema != brief_schema:
        raise SystemExit("brief schema output drifted between create and lint helpers")


def run_base_ref_default_fixture(tmp_path: Path) -> None:
    branch_default_repo = tmp_path / "branch-default-repo"
    branch_default_repo.mkdir()
    run(["git", "-C", branch_default_repo.as_posix(), "init", "-q", "--initial-branch", "trunk"])
    (branch_default_repo / "README.md").write_text("branch default fixture\n", encoding="utf-8")
    run(["git", "-C", branch_default_repo.as_posix(), "add", "README.md"])
    run(
        [
            "git",
            "-c",
            "user.email=ci@example.com",
            "-c",
            "user.name=CI",
            "-C",
            branch_default_repo.as_posix(),
            "commit",
            "-q",
            "-m",
            "Add branch default fixture README",
        ]
    )
    branch_default_brief = {
        "job_id": "branch-default-fixture",
        "branches": [
            {
                "id": "B01",
                "objective": "Verify base_ref default.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "No-op fixture.",
                        "owned_paths": ["README.md"],
                        "verification": ["true"],
                        "dod": ["Manifest base_ref defaults to the current branch."],
                    }
                ],
            }
        ],
    }
    branch_default_brief_path = tmp_path / "branch-default-brief.json"
    branch_default_bundle = tmp_path / "branch-default-bundle"
    write_json(branch_default_brief_path, branch_default_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            branch_default_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            branch_default_bundle.as_posix(),
        ]
    )
    branch_default_manifest = read_json(branch_default_bundle / "job.manifest.json")
    if branch_default_manifest.get("base_ref") != "trunk":
        raise SystemExit(f"create_goal_bundle should default base_ref to current git branch: {branch_default_manifest.get('base_ref')!r}")

    pipeline_brief = json.loads(json.dumps(branch_default_brief))
    pipeline_brief.update(
        {
            "job_id": "preflight-pipeline-fixture",
            "goal": "Run the canonical one-shot preflight pipeline fixture.",
            "source_summary": "The pipeline fixture uses a tiny git repository with README.md as concrete context.",
            "required_evidence": ["Canonical brief lint, bundle lint, repair gate, readiness, and bootloader artifacts exist."],
            "final_dod": ["The one-shot preflight pipeline returns status pass."],
            "source_attachments": [
                {"path": "README.md", "label": "Fixture README", "kind": "benchmark-data"},
            ],
        }
    )
    pipeline_work_item = pipeline_brief["branches"][0]["work_items"][0]
    pipeline_brief["branches"][0]["objective"] = (
        "Verify that guided preflight defaults base_ref to the current git branch "
        "and persists canonical pipeline artifacts."
    )
    pipeline_work_item["objective"] = "Inspect README.md and validate the guided preflight pipeline artifact contract."
    pipeline_work_item["context_files"] = ["README.md"]
    pipeline_work_item["verification"] = ["git diff --check trunk...HEAD"]
    pipeline_brief_path = tmp_path / "preflight-pipeline-brief.json"
    pipeline_bundle = tmp_path / "preflight-pipeline-bundle"
    write_json(pipeline_brief_path, pipeline_brief)
    failing_pipeline_brief = {
        **pipeline_brief,
        "job_id": "preflight-pipeline-failing-brief",
        "runtime_cap": [30],
    }
    failing_pipeline_brief_path = tmp_path / "preflight-pipeline-failing-brief.json"
    failing_pipeline_bundle = tmp_path / "preflight-pipeline-failing-bundle"
    write_json(failing_pipeline_brief_path, failing_pipeline_brief)
    failing_pipeline = subprocess.run(
        [
            "python3",
            "skills/goal-preflight/scripts/prepare_goal_bundle.py",
            "--brief",
            failing_pipeline_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            failing_pipeline_bundle.as_posix(),
            "--no-goal-config",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if failing_pipeline.returncode != 1:
        raise SystemExit(f"failing preflight pipeline should return 1: {failing_pipeline.returncode} {failing_pipeline.stdout} {failing_pipeline.stderr}")
    assert_all_contains(
        failing_pipeline.stderr + failing_pipeline.stdout,
        ["goal-preflight pipeline failed: phase=brief_lint", "pipeline_result=", "top_defects:", "$.runtime_cap"],
        "prepare_goal_bundle failed brief-lint UX",
    )
    pipeline_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                pipeline_bundle.as_posix(),
                "--no-goal-config",
                "--json",
            ]
        ).stdout
    )
    if pipeline_result.get("status") != "pass" or pipeline_result.get("readiness", {}).get("status") != "pass":
        raise SystemExit(f"prepare_goal_bundle.py should pass for a git-backed fixture: {pipeline_result!r}")
    if pipeline_result.get("launch_allowed") is not True:
        raise SystemExit(f"prepare_goal_bundle.py should expose launch_allowed=true for git-backed fixture: {pipeline_result!r}")
    if "readiness_full" in pipeline_result or "config_selection_full" in pipeline_result:
        raise SystemExit(f"prepare_goal_bundle.py should keep default pipeline output compact: {pipeline_result!r}")
    degraded_telemetry_bundle = tmp_path / "preflight-degraded-token-telemetry"
    shutil.copytree(pipeline_bundle, degraded_telemetry_bundle)
    degraded_manifest_path = degraded_telemetry_bundle / "job.manifest.json"
    degraded_manifest = read_json(degraded_manifest_path)
    degraded_manifest["goal_config_summary"] = {"profile": "fixture"}
    degraded_manifest["goal_config_check_summary"] = {
        "status": "pass",
        "accepted_route_count": 2,
        "route_verification_status": "routes_verified",
        "token_telemetry": {
            "available_routes": 1,
            "unavailable_routes": 1,
            "by_harness": {"codex": {"available": 1, "unavailable": 1}},
        },
    }
    write_json(degraded_manifest_path, degraded_manifest)
    degraded_readiness = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/render_goal_bootloader.py",
                "--bundle-dir",
                degraded_telemetry_bundle.as_posix(),
                "--readiness",
                "--json",
            ]
        ).stdout
    )
    if degraded_readiness.get("status") != "blocked" or degraded_readiness.get("launch_allowed") is not False:
        raise SystemExit(f"partial token telemetry should block readiness without waiver: {degraded_readiness!r}")
    if "route_token_telemetry_degraded_without_waiver" not in degraded_readiness.get("launch_blockers", []):
        raise SystemExit(f"partial token telemetry should be an explicit launch blocker: {degraded_readiness!r}")
    if degraded_readiness.get("verified_routes", {}).get("telemetry_capability_status") != "partial":
        raise SystemExit(f"readiness should expose partial route telemetry capability: {degraded_readiness!r}")
    degraded_manifest["route_policy_degraded_telemetry_waiver"] = {
        "accepted": True,
        "reason": "Fixture explicitly accepts partial token telemetry for launch readiness.",
    }
    write_json(degraded_manifest_path, degraded_manifest)
    waived_readiness = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/render_goal_bootloader.py",
                "--bundle-dir",
                degraded_telemetry_bundle.as_posix(),
                "--readiness",
                "--json",
            ]
        ).stdout
    )
    if "route_token_telemetry_degraded_without_waiver" in waived_readiness.get("launch_blockers", []):
        raise SystemExit(f"explicit degraded telemetry waiver should remove launch blocker: {waived_readiness!r}")
    commands = pipeline_result.get("commands", [])
    expected_phases = ["brief_lint", "runtime_gate", "config_selection", "create_bundle", "bundle_lint", "repair_gate", "readiness"]
    if [item.get("phase") for item in commands] != expected_phases:
        raise SystemExit(f"prepare_goal_bundle.py should record every canonical phase: {commands!r}")
    for item in commands:
        for key in ["elapsed_ms", "stdout_bytes", "stderr_bytes", "artifact_delta"]:
            if key not in item:
                raise SystemExit(f"pipeline command telemetry missing {key}: {item!r}")
        if item.get("phase") not in {"runtime_gate", "config_selection"} and not item.get("command_hash"):
            raise SystemExit(f"command phase should record a command_hash: {item!r}")
    for artifact in [
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "readiness.json",
        "goal-config-selection.json",
        "preflight.pipeline.json",
        "goal-bootloader.md",
        "runtime-rules.md",
    ]:
        if not (pipeline_bundle / artifact).exists():
            raise SystemExit(f"prepare_goal_bundle.py did not persist canonical artifact {artifact}")
    for runtime_dir in ["workers", "research", "reviewers", "audit", "lite", "schedulers", "amendments"]:
        if (pipeline_bundle / runtime_dir).exists():
            raise SystemExit(f"preflight should lazy-create runtime-only directory, not create {runtime_dir}/")
    pipeline_manifest = read_json(pipeline_bundle / "job.manifest.json")
    source_attachments = pipeline_manifest.get("source_attachments", [])
    if not source_attachments or source_attachments[0].get("path") != "README.md" or not source_attachments[0].get("sha256"):
        raise SystemExit(f"source_attachments should be normalized and hashed in manifest: {source_attachments!r}")
    assert_all_contains(
        (pipeline_bundle / "main.prompt.md").read_text(encoding="utf-8"),
        ["## Source Attachments", "Fixture README", "sha256="],
        "source attachments prompt section",
    )
    assert_not_contains((pipeline_bundle / "main.prompt.md").read_text(encoding="utf-8"), "/absolute/path/to/", "main prompt concrete paths")
    runtime_rules = (pipeline_bundle / "runtime-rules.md").read_text(encoding="utf-8")
    assert_all_contains(runtime_rules, ["Shared Branch Runtime Rules", "Worker Parallelism", "Reviewer Requirement", "Lite Advisors"], "runtime rules appendix")
    if pipeline_manifest.get("runtime_rules_path") != "runtime-rules.md" or not pipeline_manifest.get("runtime_rules_sha256"):
        raise SystemExit(f"manifest should pin runtime-rules.md by path and sha: {pipeline_manifest!r}")
    pipeline_prompt = (pipeline_bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    assert_contains(pipeline_prompt, "deferred - route availability is unverified", "no-goal-config route recommendations should be deferred")
    assert_not_contains(pipeline_prompt, "/absolute/path/to/", "branch prompt concrete paths")
    pipeline_readiness = read_json(pipeline_bundle / "readiness.json")
    if pipeline_readiness.get("route_policy", {}).get("worker_recommendations_suppressed") is not True:
        raise SystemExit(f"readiness should suppress worker route recommendations when route availability is unverified: {pipeline_readiness!r}")
    if pipeline_readiness.get("route_policy", {}).get("worker") != []:
        raise SystemExit(f"readiness active worker route policy should omit unverified aliases: {pipeline_readiness.get('route_policy')!r}")
    if not pipeline_readiness.get("route_policy", {}).get("unverified_config_aliases", {}).get("worker"):
        raise SystemExit(f"readiness should preserve unverified worker aliases separately: {pipeline_readiness.get('route_policy')!r}")
    optional_artifacts = pipeline_readiness.get("artifact_size_report", {}).get("optional_machine_artifacts", {})
    present_optional = optional_artifacts.get("present", [])
    if "readiness.json" not in present_optional or "preflight.pipeline.json" not in present_optional:
        raise SystemExit(f"final readiness should count final optional artifacts when present: {optional_artifacts!r}")
    if any(item.get("exists") is False for item in pipeline_readiness.get("artifact_size_report", {}).get("machine_artifacts", [])):
        raise SystemExit(f"artifact size report should not count absent optional artifacts as missing entries: {pipeline_readiness!r}")
    machine_artifacts = {
        item.get("path"): item
        for item in pipeline_readiness.get("artifact_size_report", {}).get("machine_artifacts", [])
        if isinstance(item, dict)
    }
    for rel_path in ["preflight.pipeline.json", "readiness.json"]:
        entry = machine_artifacts.get(rel_path)
        if not entry or entry.get("chars") != len((pipeline_bundle / rel_path).read_text(encoding="utf-8")):
            raise SystemExit(f"final readiness size should match {rel_path}: {entry!r}")
    if pipeline_readiness.get("caps", {}).get("max_active_worker_packets_by_branch", {}).get("B01") != 4:
        raise SystemExit(f"readiness caps should report branch worker caps, not branch wave caps: {pipeline_readiness.get('caps')!r}")
    spaced_repo = tmp_path / "branch default repo with spaces"
    spaced_bundle = tmp_path / "preflight pipeline bundle with spaces"
    shutil.copytree(branch_default_repo, spaced_repo)
    spaced_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                spaced_repo.as_posix(),
                "--out-dir",
                spaced_bundle.as_posix(),
                "--no-goal-config",
                "--json",
            ]
        ).stdout
    )
    if spaced_result.get("status") != "pass":
        raise SystemExit(f"prepare_goal_bundle.py should pass when repo/bundle paths contain spaces: {spaced_result!r}")
    spaced_main_prompt = (spaced_bundle / "main.prompt.md").read_text(encoding="utf-8")
    spaced_branch_prompt = (spaced_bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    assert_all_contains(
        spaced_main_prompt,
        [
            f"B={shlex.quote(spaced_bundle.as_posix())}",
            f"--repo-root {shlex.quote(spaced_repo.as_posix())}",
            f"--manifest {shlex.quote((spaced_bundle / 'job.manifest.json').as_posix())}",
            f"--status {shlex.quote((spaced_bundle / 'branches' / 'Bxx.status.json').as_posix())}",
            f"--bundle-dir {shlex.quote(spaced_bundle.as_posix())}",
            f"--status {shlex.quote((spaced_bundle / 'main.status.json').as_posix())}",
        ],
        "main prompt quoted concrete paths with spaces",
    )
    assert_all_contains(
        spaced_branch_prompt,
        [
            "check_model_catalog.py --json --require-codex",
            (spaced_bundle / "runtime-rules.md").as_posix(),
            f"--manifest {shlex.quote((spaced_bundle / 'job.manifest.json').as_posix())}",
            f"--status {shlex.quote((spaced_bundle / 'branches' / 'B01.status.json').as_posix())}",
        ],
        "branch prompt quoted concrete paths with spaces",
    )
    quiet_readiness = tmp_path / "quiet-readiness.txt"
    quiet_result = run(
        [
            "python3",
            "skills/goal-preflight/scripts/render_goal_bootloader.py",
            "--bundle-dir",
            pipeline_bundle.as_posix(),
            "--readiness",
            "--output",
            quiet_readiness.as_posix(),
        ]
    )
    if quiet_result.stdout.strip() != quiet_readiness.as_posix():
        raise SystemExit(f"render_goal_bootloader.py --output should print only the output path by default: {quiet_result.stdout!r}")
    if not quiet_readiness.read_text(encoding="utf-8").startswith("Compact readiness summary:"):
        raise SystemExit("render_goal_bootloader.py --output did not write the readiness payload")

    inside_repo_bundle = branch_default_repo / "goal preflight $(cleanup) inside worktree"
    inside_repo_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                inside_repo_bundle.as_posix(),
                "--no-goal-config",
                "--json",
            ]
        ).stdout
    )
    if inside_repo_result.get("status") != "pass":
        raise SystemExit(f"inside-repo preflight bundle should still pass with git-ignore warning: {inside_repo_result!r}")
    inside_lint = read_json(inside_repo_bundle / "preflight.lint.json")
    if not any("not ignored" in item.get("message", "") for item in inside_lint.get("defects", [])):
        raise SystemExit(f"lint JSON should surface the git-ignore warning: {inside_lint!r}")
    inside_readiness = read_json(inside_repo_bundle / "readiness.json")
    if not any(item.get("code") == "bundle_inside_git_worktree_not_ignored" for item in inside_readiness.get("warnings", [])):
        raise SystemExit(f"readiness JSON should surface the git-ignore warning: {inside_readiness!r}")
    cleanup_commands = inside_readiness.get("cleanup_plan", {}).get("cleanup_commands", [])
    expected_ignore_command = (
        "printf '%s\\n' "
        f"{shlex.quote(inside_repo_bundle.relative_to(branch_default_repo).as_posix() + '/')} >> .git/info/exclude"
    )
    expected_cleanup_command = f"rm -rf {shlex.quote(inside_repo_bundle.as_posix())}"
    if expected_ignore_command not in cleanup_commands or expected_cleanup_command not in cleanup_commands:
        raise SystemExit(f"readiness cleanup commands must shell-quote unsafe paths: {cleanup_commands!r}")

    large_source = branch_default_repo / "large-source.txt"
    large_source.write_text("exact operation list fixture\n" + ("0123456789abcdef\n" * 700), encoding="utf-8")
    run(["git", "-C", branch_default_repo.as_posix(), "add", "large-source.txt"])
    promoted_brief = {
        "job_id": "promoted-large-source-fixture",
        "goal": "Use the exact source list in the repeated large context file without repeating it under every worker.",
        "source_summary": "The exact source list is stored in large-source.txt and referenced by two workers.",
        "required_evidence": ["The manifest declares large-source.txt once as a source attachment."],
        "final_dod": ["Work items refer to the promoted source attachment by label."],
        "branches": [
            {
                "id": "B01",
                "objective": "Exercise repeated large context promotion for exact source data.",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Read the exact source list and perform the first fixture check.",
                        "owned_paths": ["README.md"],
                        "context_files": ["large-source.txt"],
                        "verification": ["true"],
                        "dod": ["The first worker uses the promoted large source attachment."],
                    },
                    {
                        "id": "W02",
                        "objective": "Read the exact source list and perform the second fixture check.",
                        "owned_paths": ["fixture-output.txt"],
                        "context_files": ["large-source.txt"],
                        "verification": ["true"],
                        "dod": ["The second worker uses the promoted large source attachment."],
                    },
                ],
            }
        ],
    }
    promoted_brief_path = tmp_path / "promoted-large-source-brief.json"
    promoted_bundle = tmp_path / "promoted-large-source-bundle"
    write_json(promoted_brief_path, promoted_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/prepare_goal_bundle.py",
            "--brief",
            promoted_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            promoted_bundle.as_posix(),
            "--no-goal-config",
            "--json",
        ]
    )
    promoted_manifest = read_json(promoted_bundle / "job.manifest.json")
    promotions = promoted_manifest.get("source_attachment_promotions", [])
    if not promotions or promotions[0].get("path") != "large-source.txt":
        raise SystemExit(f"large repeated context file should be promoted: {promotions!r}")
    promoted_attachments = promoted_manifest.get("source_attachments", [])
    promoted_label = next((item.get("label") for item in promoted_attachments if item.get("path") == "large-source.txt"), None)
    if not promoted_label:
        raise SystemExit(f"promoted source attachment missing label: {promoted_attachments!r}")
    for item in promoted_manifest["branches"][0]["work_items"]:
        if "large-source.txt" in item.get("context_files", []):
            raise SystemExit(f"promoted large source should not remain in work item context_files: {item!r}")
        if promoted_label not in item.get("source_attachment_refs", []):
            raise SystemExit(f"work item should reference promoted source attachment label: {item!r}")
    assert_contains((promoted_bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8"), "Source attachment refs:", "promoted source branch prompt")

    debug_mode_brief = {**branch_default_brief, "job_id": "debug-mode-shorthand-fixture", "telemetry_mode": "debug"}
    debug_mode_brief_path = tmp_path / "debug-mode-brief.json"
    debug_mode_bundle = tmp_path / "debug-mode-bundle"
    write_json(debug_mode_brief_path, debug_mode_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            debug_mode_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            debug_mode_bundle.as_posix(),
        ]
    )
    debug_policy = read_json(debug_mode_bundle / "job.manifest.json").get("telemetry_policy", {})
    expected_collect = [
        "route_decisions",
        "token_usage",
        "timings",
        "scheduler_utilization",
        "context_pack_stats",
        "validator_runs",
        "artifact_hashes",
    ]
    if debug_policy.get("mode") != "debug" or debug_policy.get("raw_text") is not False or debug_policy.get("collect") != expected_collect:
        raise SystemExit(f"telemetry_mode=debug did not expand to full safe debug policy: {debug_policy!r}")

    debug_pipeline_brief = json.loads(json.dumps(pipeline_brief))
    debug_pipeline_brief.update({"job_id": "debug-preflight-pipeline-fixture", "telemetry_mode": "debug"})
    debug_pipeline_brief_path = tmp_path / "debug-preflight-pipeline-brief.json"
    debug_pipeline_bundle = tmp_path / "debug-preflight-pipeline-bundle"
    write_json(debug_pipeline_brief_path, debug_pipeline_brief)
    debug_pipeline_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                debug_pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                debug_pipeline_bundle.as_posix(),
                "--no-goal-config",
                "--json",
            ]
        ).stdout
    )
    if debug_pipeline_result.get("status") != "pass":
        raise SystemExit(f"debug preflight pipeline fixture should pass: {debug_pipeline_result!r}")
    run(["python3", "skills/goal-main-orchestrator/scripts/summarize_telemetry.py", "--bundle-dir", debug_pipeline_bundle.as_posix(), "--debug"])
    debug_pipeline_summary = read_json(debug_pipeline_bundle / "telemetry.debug.summary.json")
    debug_pipeline_preflight = debug_pipeline_summary.get("preflight_pipeline", {})
    if debug_pipeline_preflight.get("phase_count") != len(debug_pipeline_result.get("commands", [])):
        raise SystemExit(f"debug summary should include preflight pipeline phase telemetry: {debug_pipeline_preflight!r}")
    if debug_pipeline_summary.get("time_metrics", {}).get("preflight_phase_count") != debug_pipeline_preflight.get("phase_count"):
        raise SystemExit(f"debug time metrics should expose preflight phase count: {debug_pipeline_summary.get('time_metrics')!r}")
    debug_trace = debug_pipeline_summary.get("trace", {})
    if debug_trace.get("event_types", {}).get("preflight_phase") != debug_pipeline_preflight.get("phase_count"):
        raise SystemExit(f"debug run trace should include preflight phase events: {debug_trace!r}")
    debug_trace_events = [
        json.loads(line)
        for line in (debug_pipeline_bundle / "run.trace.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not any(event.get("event_type") == "preflight_phase" for event in debug_trace_events if isinstance(event, dict)):
        raise SystemExit("debug preflight-only run.trace.jsonl should not be empty")
    if any("raw_text" in event for event in debug_trace_events if isinstance(event, dict)):
        raise SystemExit("debug preflight pipeline trace must not include raw_text payloads")

    debug_bool_brief = {**branch_default_brief, "job_id": "debug-bool-shorthand-fixture", "debug_telemetry": True}
    debug_bool_brief_path = tmp_path / "debug-bool-brief.json"
    debug_bool_bundle = tmp_path / "debug-bool-bundle"
    write_json(debug_bool_brief_path, debug_bool_brief)
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            debug_bool_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            debug_bool_bundle.as_posix(),
        ]
    )
    debug_bool_policy = read_json(debug_bool_bundle / "job.manifest.json").get("telemetry_policy", {})
    if debug_bool_policy.get("mode") != "debug" or debug_bool_policy.get("collect") != expected_collect:
        raise SystemExit(f"debug_telemetry=true did not expand to full safe debug policy: {debug_bool_policy!r}")

    config_debug_path = tmp_path / "goal-config-debug-intent.json"
    config_debug_check_path = tmp_path / "goal-config-debug-intent-check.json"
    config_debug_bundle = tmp_path / "goal-config-debug-intent-bundle"
    config_debug_brief_path = tmp_path / "goal-config-debug-intent-brief.json"
    write_json(config_debug_brief_path, {**branch_default_brief, "job_id": "goal-config-debug-intent-fixture"})
    run(
        [
            "python3",
            "skills/goal-config/scripts/create_goal_config.py",
            "--preset",
            "current-default",
            "--effort-profile",
            "thorough",
            "--validation-mode",
            "debug",
            "--output",
            config_debug_path.as_posix(),
        ]
    )
    run(
        [
            "python3",
            "skills/goal-config/scripts/check_goal_config.py",
            "--config",
            config_debug_path.as_posix(),
            "--for-preflight",
            "--smoke",
            "--stdout",
            "none",
            "--output",
            config_debug_check_path.as_posix(),
        ]
    )
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            config_debug_brief_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            config_debug_bundle.as_posix(),
            "--goal-config",
            config_debug_path.as_posix(),
            "--goal-config-check",
            config_debug_check_path.as_posix(),
        ]
    )
    config_debug_manifest = read_json(config_debug_bundle / "job.manifest.json")
    config_debug_policy = config_debug_manifest.get("telemetry_policy", {})
    if config_debug_policy.get("mode") != "debug" or config_debug_policy.get("raw_text") is not False:
        raise SystemExit(f"goal_config preflight_intent telemetry_mode=debug should produce safe debug policy: {config_debug_policy!r}")
    config_debug_compat = config_debug_manifest.get("preflight_compatibility", {}).get("telemetry", {})
    if config_debug_compat.get("policy_transformation") != "raw_text_true_is_sanitized_to_manifest_raw_text_false":
        raise SystemExit(f"goal_config raw_text policy transformation should be recorded: {config_debug_compat!r}")

    colocated_smoke_debug_check = tmp_path / "goal-config-smoke.json"
    if colocated_smoke_debug_check.exists():
        colocated_smoke_debug_check.unlink()
    write_json(
        colocated_smoke_debug_check,
        {
            "status": "pass",
            "mode": "smoke",
            "check_mode": "smoke",
            "config_validation_mode": "smoke",
            "config_path": config_debug_path.as_posix(),
            "summary": {
                "route_verification_status": "routes_verified",
                "accepted_route_count": 7,
                "checked_role_count": 1,
                "harness_count": 1,
                "failure_count": 0,
            },
        },
    )
    colocated_smoke_bundle = tmp_path / "preflight-colocated-smoke-bundle"
    colocated_smoke_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                colocated_smoke_bundle.as_posix(),
                "--goal-config",
                config_debug_path.as_posix(),
                "--json",
                "--allow-blocked-readiness",
            ]
        ).stdout
    )
    colocated_selection = colocated_smoke_result.get("config_selection", {})
    if colocated_selection.get("selected_check_path") != colocated_smoke_debug_check.as_posix():
        raise SystemExit(
            f"prepare should auto-reuse colocated route-verified smoke check: {colocated_selection.get('selected_check_path')!r}"
        )
    if colocated_selection.get("route_model_availability_verified") is not True:
        raise SystemExit(f"prepare should expose colocated smoke route verification as verified: {colocated_selection!r}")
    colocated_readiness = read_json(colocated_smoke_bundle / "readiness.json")
    if "config_schema_pass_routes_unverified" in colocated_readiness.get("launch_blockers", []):
        raise SystemExit(f"colocated route-verified smoke check should prevent schema_pass_routes_unverified: {colocated_readiness!r}")

    aliased_goal_config_path = tmp_path / "goal-config-debug-alias.json"
    shutil.copyfile(config_debug_path, aliased_goal_config_path)
    alias_smoke_bundle = tmp_path / "preflight-colocated-alias-smoke-bundle"
    alias_smoke_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                alias_smoke_bundle.as_posix(),
                "--goal-config",
                aliased_goal_config_path.as_posix(),
                "--goal-config-check",
                colocated_smoke_debug_check.as_posix(),
                "--json",
            ]
        ).stdout
    )
    alias_selection = alias_smoke_result.get("config_selection", {})
    if alias_selection.get("selected_config_path") != aliased_goal_config_path.as_posix():
        raise SystemExit(
            f"prepare should retain selected alias config path when reusing verified smoke by hash: {alias_selection.get('selected_config_path')!r}"
        )
    if alias_selection.get("selected_check_path") != colocated_smoke_debug_check.as_posix():
        raise SystemExit(
            f"prepare should reuse explicit verified smoke check for byte-identical alias config: {alias_selection.get('selected_check_path')!r}"
        )
    if alias_selection.get("route_model_availability_verified") is not True:
        raise SystemExit(f"prepare should expose hash-matched route verification for alias config: {alias_selection!r}")
    alias_readiness = read_json(alias_smoke_bundle / "readiness.json")
    if alias_readiness.get("status") != "pass":
        raise SystemExit(f"byte-identical alias config with verified smoke should pass readiness: {alias_readiness!r}")
    if alias_readiness.get("launch_allowed") is not True:
        raise SystemExit(f"byte-identical alias config with verified smoke should be launch-allowed: {alias_readiness!r}")

    shutil.copyfile(config_debug_path, branch_default_repo / "goal.preflight.config.json")
    shutil.copyfile(config_debug_path, branch_default_repo / "goal.config.json")
    auto_config_bundle = tmp_path / "preflight-auto-config-first-compatible"
    auto_config_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                auto_config_bundle.as_posix(),
                "--json",
            ],
            expect=1,
        ).stdout
    )
    auto_selection = auto_config_result.get("config_selection", {})
    if auto_selection.get("status") != "pass" or auto_selection.get("candidate_count") != 1:
        raise SystemExit(f"auto config selection should stop after the first compatible candidate by default: {auto_selection!r}")
    if auto_config_result.get("status") != "blocked" or auto_config_result.get("readiness_status") != "blocked":
        raise SystemExit(f"auto config selection with unverified routes should produce blocked readiness: {auto_config_result!r}")
    persisted_auto_selection = read_json(auto_config_bundle / "goal-config-selection.json")
    if persisted_auto_selection.get("candidate_audit_mode") != "first_compatible" or len(persisted_auto_selection.get("candidates", [])) != 1:
        raise SystemExit(f"persisted config selection should record first_compatible mode: {persisted_auto_selection!r}")

    bad_pipeline_config_path = tmp_path / "goal-config-pipeline-remediation.json"
    bad_pipeline_config = read_json(config_debug_path)
    bad_pipeline_config["aggressiveness"]["max_active_branch_agents"] = 6
    bad_pipeline_config["aggressiveness"]["max_active_worker_packets"] = 6
    bad_pipeline_config["aggressiveness"]["max_waves"] = 8
    bad_pipeline_config["aggressiveness"]["total_branch_cap"] = 48
    bad_pipeline_config["telemetry"]["collect"] = ["route_decisions", "unsupported_raw_payload"]
    write_json(bad_pipeline_config_path, bad_pipeline_config)
    config_pipeline_bundle = tmp_path / "preflight-pipeline-config-remediation-bundle"
    config_pipeline_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                pipeline_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                config_pipeline_bundle.as_posix(),
                "--goal-config",
                bad_pipeline_config_path.as_posix(),
                "--json",
            ],
            expect=1,
        ).stdout
    )
    config_selection = config_pipeline_result.get("config_selection", {})
    if config_pipeline_result.get("status") != "blocked" or config_selection.get("status") != "pass":
        raise SystemExit(f"prepare_goal_bundle.py should remediate and select compatible config: {config_pipeline_result!r}")
    if config_pipeline_result.get("readiness_status") != "blocked":
        raise SystemExit(f"prepare_goal_bundle.py should fail closed on unverified route availability: {config_pipeline_result!r}")
    if "remediated" not in str(config_selection.get("reason", "")):
        raise SystemExit(f"config selection should explain remediated config use: {config_selection!r}")
    persisted_selection = read_json(config_pipeline_bundle / "goal-config-selection.json")
    if "selected" in persisted_selection:
        raise SystemExit(f"goal-config-selection.json should not duplicate selected candidate: {persisted_selection!r}")
    candidates = persisted_selection.get("candidates", [])
    selected_candidates = [item for item in candidates if isinstance(item, dict) and item.get("selected") is True]
    if len(selected_candidates) != 1:
        raise SystemExit(f"config selection candidates should mark exactly one selected entry: {persisted_selection!r}")
    selected = selected_candidates[0]
    if persisted_selection.get("selected_index") != candidates.index(selected):
        raise SystemExit(f"config selection should point selected_index at the selected candidate: {persisted_selection!r}")
    if selected.get("eligible") is not True or selected.get("remediated_passed") is not True:
        raise SystemExit(f"selected remediated config should be eligible/remediated_passed: {selected!r}")
    if selected.get("remediation", {}).get("status") != "remediated":
        raise SystemExit(f"config remediation should expose status=remediated: {selected!r}")
    selected_config_path = Path(selected.get("selected_config_path", ""))
    selected_config = read_json(selected_config_path)
    if selected_config.get("aggressiveness", {}).get("max_waves") != 5:
        raise SystemExit(f"selected remediated config should clamp max_waves: {selected_config!r}")
    if (config_pipeline_bundle / "goal-config-selection.json").exists() is not True:
        raise SystemExit("prepare_goal_bundle.py should persist goal-config-selection.json")
    config_pipeline_manifest = read_json(config_pipeline_bundle / "job.manifest.json")
    if config_pipeline_manifest.get("title") in (None, ""):
        raise SystemExit(f"manifest should preserve a non-null title: {config_pipeline_manifest.get('title')!r}")
    if not config_pipeline_manifest.get("required_evidence") or not config_pipeline_manifest.get("final_dod"):
        raise SystemExit("manifest should preserve required_evidence and final_dod as structured fields")
    branch_manifest = config_pipeline_manifest.get("branches", [{}])[0]
    if not branch_manifest.get("objective") or not branch_manifest.get("scope"):
        raise SystemExit(f"branch manifest should preserve objective and scope: {branch_manifest!r}")
    precedence = config_pipeline_manifest.get("preflight_input_precedence", {})
    if precedence.get("max_active_branch_agents", {}).get("source") != "goal_config.aggressiveness.max_active_branch_agents":
        raise SystemExit(f"manifest should report config cap precedence: {precedence!r}")
    provenance = config_pipeline_manifest.get("goal_config_provenance", {}).get("config", {})
    if provenance.get("source_path_type") != "bundle_relative" or "remediated" not in str(provenance.get("source_path")):
        raise SystemExit(f"manifest should preserve remediated config source provenance separately: {provenance!r}")
    goal_config_summary = config_pipeline_manifest.get("goal_config_summary", {})
    if goal_config_summary.get("manifest_telemetry_policy", {}).get("raw_text") is not False:
        raise SystemExit(f"goal_config_summary should expose authoritative manifest telemetry policy: {goal_config_summary!r}")
    config_pipeline_readiness = read_json(config_pipeline_bundle / "readiness.json")
    if config_pipeline_readiness.get("config_compatibility") != "config_schema_pass_routes_unverified":
        raise SystemExit(f"readiness should distinguish config schema pass from route availability: {config_pipeline_readiness!r}")
    if config_pipeline_readiness.get("launch_allowed") is not False:
        raise SystemExit(f"unverified route availability should block runtime launch: {config_pipeline_readiness!r}")
    if "config_schema_pass_routes_unverified" not in config_pipeline_readiness.get("launch_blockers", []):
        raise SystemExit(f"readiness should record unverified route availability as a launch blocker: {config_pipeline_readiness!r}")
    if not any(item.get("code") == "route_availability_unverified" for item in config_pipeline_readiness.get("warnings", [])):
        raise SystemExit(f"readiness should warn on config route availability ambiguity: {config_pipeline_readiness!r}")
    if config_pipeline_readiness.get("route_policy", {}).get("worker") != []:
        raise SystemExit(f"config readiness should omit unverified active worker aliases: {config_pipeline_readiness.get('route_policy')!r}")
    if not config_pipeline_readiness.get("route_policy", {}).get("unverified_config_aliases", {}).get("worker"):
        raise SystemExit(f"config readiness should preserve unverified aliases under unverified_config_aliases: {config_pipeline_readiness.get('route_policy')!r}")
    report_text = (config_pipeline_bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8")
    assert_all_contains(
        report_text,
        ["preflight config check status=pass", "Goal config route availability:", "Config precedence:"],
        "config preflight report status labels",
    )
    assert_not_contains(report_text, "harness check status is pass", "config preflight report stale harness pass label")

    explicit_cap_brief = json.loads(json.dumps(pipeline_brief))
    explicit_cap_brief["job_id"] = "preflight-explicit-cap-precedence"
    explicit_cap_brief["max_active_branch_agents"] = 2
    explicit_cap_brief["branches"][0]["max_active_worker_packets"] = 2
    explicit_cap_brief_path = tmp_path / "preflight-explicit-cap-brief.json"
    explicit_cap_bundle = tmp_path / "preflight-explicit-cap-bundle"
    write_json(explicit_cap_brief_path, explicit_cap_brief)
    explicit_cap_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                explicit_cap_brief_path.as_posix(),
                "--repo-root",
                branch_default_repo.as_posix(),
                "--out-dir",
                explicit_cap_bundle.as_posix(),
                "--goal-config",
                bad_pipeline_config_path.as_posix(),
                "--json",
            ],
            expect=1,
        ).stdout
    )
    if explicit_cap_result.get("status") != "blocked" or explicit_cap_result.get("readiness_status") != "blocked":
        raise SystemExit(f"explicit cap pipeline should remediate config but block unverified route launch: {explicit_cap_result!r}")
    explicit_cap_manifest = read_json(explicit_cap_bundle / "job.manifest.json")
    if explicit_cap_manifest.get("max_active_branch_agents") != 2:
        raise SystemExit(f"explicit max_active_branch_agents should override config default: {explicit_cap_manifest!r}")
    explicit_branch = explicit_cap_manifest.get("branches", [{}])[0]
    if explicit_branch.get("max_active_worker_packets") != 2:
        raise SystemExit(f"explicit branch worker cap should override config default: {explicit_branch!r}")
    explicit_precedence = explicit_cap_manifest.get("preflight_input_precedence", {})
    if explicit_precedence.get("max_active_branch_agents", {}).get("source") != "brief":
        raise SystemExit(f"global cap precedence should report source=brief: {explicit_precedence!r}")
    if explicit_precedence.get("max_active_worker_packets_by_branch", {}).get("B01", {}).get("source") != "brief":
        raise SystemExit(f"branch cap precedence should report source=brief: {explicit_precedence!r}")

    git_repo_base_ref = {**branch_default_brief, "job_id": "branch-default-invalid-git-ref"}
    git_repo_base_ref["branches"][0]["work_items"][0]["owned_paths"] = ["README.md"]
    git_repo_base_ref["base_ref"] = "nonexistent-base-ref"
    git_repo_base_ref_path = tmp_path / "branch-default-git-invalid-base-ref.json"
    write_json(git_repo_base_ref_path, git_repo_base_ref)
    git_ref_invalid = run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            git_repo_base_ref_path.as_posix(),
            "--repo-root",
            branch_default_repo.as_posix(),
            "--out-dir",
            (tmp_path / "branch-default-git-invalid-ref").as_posix(),
        ],
        expect=1,
    )
    assert_contains(git_ref_invalid.stdout, "base_ref does not exist", "git repo base_ref existence check fixture")

    non_git_repo = tmp_path / "non-git-repo"
    non_git_repo.mkdir()
    (non_git_repo / "README.md").write_text("non-git fixture\n", encoding="utf-8")
    non_git_brief = {
        "job_id": "branch-default-non-git-ref-fixture",
        "base_ref": "nonexistent-base-ref",
        "goal": "Validate non-git runtime readiness handling.",
        "source_summary": "This fixture is intentionally outside a git work tree.",
        "required_evidence": ["Readiness reports non-git runtime branch orchestration as blocked."],
        "final_dod": ["Non-git readiness is explicit before runtime launch."],
        "branches": [
            {
                "id": "B01",
                "objective": "Validate base_ref is accepted in non-git repo when unknown branch is supplied.",
                "max_active_worker_packets": 1,
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Exercise a deterministic non-git base_ref path.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "verification": ["python3 -c 'from pathlib import Path; assert Path(\"README.md\").exists()'"],
                        "dod": ["Bundle creation should not validate base_ref in non-git repo."],
                    }
                ],
            }
        ],
    }
    non_git_bundle = tmp_path / "branch-default-non-git-ref-bundle"
    non_git_brief_path = tmp_path / "branch-default-non-git-ref.json"
    non_git_create_result_path = tmp_path / "branch-default-non-git-ref-create-result.json"
    write_json(non_git_brief_path, non_git_brief)
    non_git_create_result = run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            non_git_brief_path.as_posix(),
            "--repo-root",
            non_git_repo.as_posix(),
            "--out-dir",
            non_git_bundle.as_posix(),
            "--json",
            "--output",
            non_git_create_result_path.as_posix(),
        ]
    ).stdout
    if json.loads(non_git_create_result) != read_json(non_git_create_result_path):
        raise SystemExit("create_goal_bundle.py --json --output should write and print matching command result JSON")
    non_git_manifest = read_json(non_git_bundle / "job.manifest.json")
    if non_git_manifest.get("base_ref") != "nonexistent-base-ref":
        raise SystemExit(f"non-git repo should not validate base_ref and should preserve provided branch: {non_git_manifest.get('base_ref')!r}")
    non_git_pipeline_bundle = tmp_path / "branch-default-non-git-pipeline-bundle"
    non_git_pipeline_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                non_git_brief_path.as_posix(),
                "--repo-root",
                non_git_repo.as_posix(),
                "--out-dir",
                non_git_pipeline_bundle.as_posix(),
                "--no-goal-config",
                "--allow-blocked-readiness",
                "--json",
            ]
        ).stdout
    )
    runtime_gate = non_git_pipeline_result.get("runtime_gate", {})
    if non_git_pipeline_result.get("status") != "blocked" or runtime_gate.get("status") != "blocked":
        raise SystemExit(f"non-git pipeline should stop at the early runtime gate by default: {non_git_pipeline_result!r}")
    if non_git_pipeline_result.get("launch_allowed") is not False:
        raise SystemExit(f"non-git pipeline should expose aggregate launch_allowed=false: {non_git_pipeline_result!r}")
    if non_git_pipeline_result.get("result_kind") != "blocked_runtime_gate_preflight" or non_git_pipeline_result.get("usable_bundle") is not False:
        raise SystemExit(f"non-git pipeline should avoid building a usable runtime bundle by default: {non_git_pipeline_result!r}")
    next_commands = non_git_pipeline_result.get("next_commands", [])
    if not any("Correct runtime gate" in command for command in next_commands):
        raise SystemExit(f"non-git readiness next commands should be corrective, not a launch handoff: {next_commands!r}")
    if any(command.endswith("/goal") for command in next_commands):
        raise SystemExit(f"non-git readiness next commands must not include /goal: {next_commands!r}")
    if (non_git_pipeline_bundle / "goal-bootloader.md").exists():
        raise SystemExit("default non-git prepare_goal_bundle.py should not generate launch prompts or bootloader without --build-blocked-bundle")

    non_git_blocked_bundle = tmp_path / "branch-default-non-git-pipeline-inspect-bundle"
    non_git_blocked_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-preflight/scripts/prepare_goal_bundle.py",
                "--brief",
                non_git_brief_path.as_posix(),
                "--repo-root",
                non_git_repo.as_posix(),
                "--out-dir",
                non_git_blocked_bundle.as_posix(),
                "--no-goal-config",
                "--build-blocked-bundle",
                "--allow-blocked-readiness",
                "--json",
            ]
        ).stdout
    )
    inspect_gate = non_git_blocked_result.get("readiness", {}).get("runtime_gate", {})
    if non_git_blocked_result.get("status") != "blocked" or inspect_gate.get("status") != "blocked":
        raise SystemExit(f"explicit blocked bundle should still block runtime readiness: {non_git_blocked_result!r}")
    if non_git_blocked_result.get("launch_allowed") is not False or non_git_blocked_result.get("readiness", {}).get("launch_allowed") is not False:
        raise SystemExit(f"explicit blocked bundle should expose aggregate launch_allowed=false: {non_git_blocked_result!r}")
    if non_git_blocked_result.get("result_kind") != "blocked_readiness_usable_bundle" or non_git_blocked_result.get("usable_bundle") is not True:
        raise SystemExit(f"explicit blocked bundle should report a usable but readiness-blocked inspection bundle: {non_git_blocked_result!r}")
    non_git_bootloader = (non_git_blocked_bundle / "goal-bootloader.md").read_text(encoding="utf-8")
    assert_all_contains(non_git_bootloader, ["BLOCKED READINESS", "do not launch /goal yet"], "non-git blocked bootloader warning")
    assert_not_contains(non_git_bootloader, "Use $goal-main-orchestrator", "blocked bootloader launch handoff")
    assert_not_contains(non_git_bootloader, "run_prompt_audit_phase.py", "blocked bootloader prompt-audit command")
    non_git_report = (non_git_blocked_bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8")
    assert_contains(non_git_report, "Runtime readiness gate: blocked", "non-git preflight report blocker")


def run_context_pack_fixtures(tmp_path: Path) -> None:
    context_pack_out = tmp_path / "context-pack.md"
    context_pack_result = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/context_pack.py",
            "--worktree",
            ROOT.as_posix(),
            "--context-file",
            (ROOT / "README.md").as_posix(),
            "--markdown",
            "--output",
            context_pack_out.as_posix(),
        ]
    )
    if context_pack_result.stdout.strip() != context_pack_out.as_posix() or not context_pack_out.exists():
        raise SystemExit("context_pack.py --output should write the rendered pack and print its path")
    context_pack_text = context_pack_out.read_text(encoding="utf-8")
    assert_all_contains(context_pack_text, ["Context files to read first:", "- README.md"], "default path-only context pack output")
    assert_not_contains(context_pack_text, "BEGIN_CONTEXT_EXCERPT", "default path-only context pack output")
    context_pack_excerpt_out = tmp_path / "context-pack-excerpt.md"
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/context_pack.py",
            "--worktree",
            ROOT.as_posix(),
            "--context-file",
            (ROOT / "README.md").as_posix(),
            "--per-file-chars",
            "80",
            "--total-chars",
            "80",
            "--include-worktree-excerpts",
            "--markdown",
            "--output",
            context_pack_excerpt_out.as_posix(),
        ]
    )
    context_pack_excerpt_text = context_pack_excerpt_out.read_text(encoding="utf-8")
    assert_all_contains(context_pack_excerpt_text, ["Deterministic context excerpts:", "BEGIN_CONTEXT_EXCERPT context-1: README.md"], "worktree excerpt context pack output")
    assert_not_contains(context_pack_excerpt_text, "Context files to read first:\n- README.md", "worktree excerpt context pack output")


def run_telemetry_summary_fixture(tmp_path: Path) -> None:
    telemetry_bundle = tmp_path / "telemetry-pressure"
    telemetry_packet = telemetry_bundle / "workers" / "B01-W01"
    telemetry_packet.mkdir(parents=True)
    write_json(
        telemetry_bundle / "job.manifest.json",
        {
            "schema_version": 1,
            "job_id": "telemetry-pressure",
            "telemetry_policy": {"schema_version": 1, "mode": "debug"},
        },
    )
    prompt_path = telemetry_packet / "prompt.md"
    prompt_path.write_text("telemetry pressure fixture prompt\n", encoding="utf-8")
    status_path = telemetry_packet / "status.json"
    status_path.write_text(json.dumps({"status": "pass"}, sort_keys=True) + "\n", encoding="utf-8")
    (telemetry_packet / "events-primary.jsonl").write_text(
        json.dumps({"usage": {"input_tokens": 25000, "cached_input_tokens": 24000, "output_tokens": 100}}, sort_keys=True) + "\n"
        + json.dumps({"type": "done"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    telemetry_attempt_json = json.dumps(
        {
            "alias": "codex-mini",
            "provider": "codex",
            "model": "gpt-5.4-mini",
            "effort": "medium",
            "command": "codex exec --ignore-user-config --ignore-rules",
            "timeout_seconds": 1200,
            "event_logs": ["events-primary.jsonl"],
        }
    )
    telemetry_fallback_attempt_json = json.dumps(
        {
            "alias": "codex-spark",
            "provider": "codex",
            "model": "gpt-5.3-codex-spark",
            "effort": "medium",
            "command": "codex exec --ignore-user-config --ignore-rules",
            "timeout_seconds": 1200,
            "event_logs": ["events-fallback.jsonl"],
        }
    )
    run(
        [
            "python3",
            "skills/_goal_shared/scripts/extract_telemetry.py",
            "--packet-dir",
            telemetry_packet.as_posix(),
            "--packet-id",
            "B01-W01",
            "--role",
            "worker",
            "--output-name",
            "status.json",
            "--attempt-json",
            telemetry_attempt_json,
            "--attempt-json",
            telemetry_fallback_attempt_json,
            "--debug",
        ]
    )
    if not (telemetry_packet / "telemetry.debug.json").exists():
        raise SystemExit("extract_telemetry.py --debug must write telemetry.debug.json")
    (telemetry_packet / "debug.events.jsonl").write_text(
        json.dumps({"schema_version": 1, "timestamp": "2026-01-01T00:00:00+00:00", "packet_id": "B01-W01", "role": "worker", "phase": "packet", "event": "start"}, sort_keys=True) + "\n"
        + json.dumps({"schema_version": 1, "timestamp": "2026-01-01T00:00:03+00:00", "packet_id": "B01-W01", "role": "worker", "phase": "packet", "event": "end", "elapsed_ms": 3000, "status": "ok", "exit_status": 0}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_json(
        telemetry_packet / "launcher-state.json",
        {
            "schema_version": 1,
            "packet_id": "B01-W01",
            "role": "worker",
            "state_machine": "active -> timeout|fail-clean|fail-dirty|pass|blocked",
            "terminal_state": "pass",
            "events": [
                {"seq": 1, "state": "active", "attempt_index": 0, "alias": "codex-mini", "provider": "codex", "model": "gpt-5.4-mini"},
                {"seq": 2, "state": "pass", "attempt_index": 0, "alias": "codex-mini", "provider": "codex", "model": "gpt-5.4-mini", "returncode": 0, "dirty": False, "output_nonempty": True},
            ],
        },
    )
    run(
        [
            "python3",
            "skills/_goal_shared/scripts/extract_telemetry.py",
            "--packet-dir",
            telemetry_packet.as_posix(),
            "--packet-id",
            "B01-W01",
            "--role",
            "worker",
            "--output-name",
            "status.json",
            "--attempt-json",
            telemetry_attempt_json,
            "--attempt-json",
            telemetry_fallback_attempt_json,
            "--debug",
        ]
    )
    debug_packet = read_json(telemetry_packet / "telemetry.debug.json")
    debug_attempts = debug_packet.get("model_usage", {}).get("attempts", [])
    debug_timing = debug_attempts[0].get("timing", {}) if debug_attempts else {}
    if debug_timing.get("timed_out") is not False or debug_timing.get("elapsed_seconds") != 3:
        raise SystemExit(f"debug telemetry should use exit-0 packet timing instead of timeout inference: {debug_timing!r}")
    debug_fallback = debug_attempts[1] if len(debug_attempts) > 1 else {}
    if debug_fallback.get("called") is not False or debug_fallback.get("not_executed_reason") != "fallback_not_needed":
        raise SystemExit(f"debug telemetry should explain skipped fallback candidates: {debug_attempts!r}")
    debug_fallback_timing = debug_fallback.get("timing", {})
    if (
        debug_fallback_timing.get("started_at") is not None
        or debug_fallback_timing.get("completed_at") is not None
        or debug_fallback_timing.get("elapsed_seconds") is not None
        or debug_fallback_timing.get("timing_source") != "unknown"
    ):
        raise SystemExit(f"debug telemetry must not assign packet timing to skipped fallback candidates: {debug_attempts!r}")
    (telemetry_packet / "events-primary.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timestamp": "2026-01-01T00:00:01+00:00",
                "packet_id": "B01-W01",
                "role": "worker",
                "event": "timeout",
                "message": "attempt timed out",
                "timed_out": True,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (telemetry_packet / "events-fallback.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "timestamp": "2026-01-01T00:00:03+00:00",
                "packet_id": "B01-W01",
                "role": "worker",
                "event": "end",
                "status": "ok",
                "exit_status": 0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    run(
        [
            "python3",
            "skills/_goal_shared/scripts/extract_telemetry.py",
            "--packet-dir",
            telemetry_packet.as_posix(),
            "--packet-id",
            "B01-W01",
            "--role",
            "worker",
            "--output-name",
            "status.json",
            "--attempt-json",
            telemetry_attempt_json,
            "--attempt-json",
            telemetry_fallback_attempt_json,
            "--debug",
        ]
    )
    timeout_debug = read_json(telemetry_packet / "telemetry.debug.json")
    timeout_attempts = timeout_debug.get("model_usage", {}).get("attempts", [])
    if len(timeout_attempts) < 2 or timeout_attempts[0].get("timing", {}).get("timed_out") is not True:
        raise SystemExit(f"debug telemetry must preserve per-attempt timeout even when packet completed: {timeout_attempts!r}")
    if timeout_attempts[1].get("timing", {}).get("timed_out") is not False:
        raise SystemExit(f"debug telemetry should preserve fallback completion timing: {timeout_attempts!r}")
    (telemetry_packet / "events-primary.jsonl").write_text(
        json.dumps({"usage": {"input_tokens": 25000, "cached_input_tokens": 24000, "output_tokens": 100}}, sort_keys=True) + "\n"
        + json.dumps({"type": "done"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (telemetry_packet / "events-fallback.jsonl").unlink(missing_ok=True)
    run(
        [
            "python3",
            "skills/_goal_shared/scripts/extract_telemetry.py",
            "--packet-dir",
            telemetry_packet.as_posix(),
            "--packet-id",
            "B01-W01",
            "--role",
            "worker",
            "--output-name",
            "status.json",
            "--attempt-json",
            telemetry_attempt_json,
            "--attempt-json",
            telemetry_fallback_attempt_json,
            "--debug",
        ]
    )
    scheduler_dir = telemetry_bundle / "schedulers"
    scheduler_dir.mkdir(parents=True)
    write_json(
        scheduler_dir / "main.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "main-branch-pool",
            "scheduler_path": "schedulers/main.scheduler.json",
            "manifest_sha256": "fixture",
            "capacity": 1,
            "item_ids": ["B01"],
            "events": [
                {
                    "seq": 1,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "wall_clock_timestamp": "2026-01-01T00:00:00+00:00",
                    "runtime_ref": "fixture",
                    "event": "ready",
                    "id": "B01",
                },
                {
                    "seq": 2,
                    "timestamp": "2026-01-01T00:00:01+00:00",
                    "wall_clock_timestamp": "2026-01-01T00:00:01+00:00",
                    "runtime_ref": "fixture",
                    "event": "launch",
                    "id": "B01",
                },
                {
                    "seq": 3,
                    "timestamp": "2026-01-01T00:00:02+00:00",
                    "wall_clock_timestamp": "2026-01-01T00:00:02+00:00",
                    "runtime_ref": "fixture",
                    "event": "finish",
                    "id": "B01",
                    "status": "pass",
                },
                {
                    "seq": 4,
                    "timestamp": "2026-01-01T00:00:03+00:00",
                    "wall_clock_timestamp": "2026-01-01T00:00:03+00:00",
                    "runtime_ref": "fixture",
                    "event": "close",
                    "id": "B01",
                },
            ],
        },
    )
    write_json(telemetry_bundle / "main.status.json", {"job_id": "telemetry-pressure", "status": "pass"})
    run(["python3", "skills/goal-main-orchestrator/scripts/summarize_telemetry.py", "--bundle-dir", telemetry_bundle.as_posix()])
    telemetry_summary = read_json(telemetry_bundle / "telemetry.summary.json")
    if telemetry_summary.get("telemetry_count") != 1:
        raise SystemExit("summarize_telemetry.py should expose top-level telemetry_count")
    pressure_warnings = telemetry_summary.get("token_pressure", {}).get("warnings")
    if not isinstance(pressure_warnings, list) or not pressure_warnings or pressure_warnings[0].get("packet_id") != "B01-W01":
        raise SystemExit("summarize_telemetry.py should report warning-only token pressure for oversized child-session input")
    if not (telemetry_bundle / "telemetry.debug.summary.json").exists() or not (telemetry_bundle / "run.trace.jsonl").exists():
        raise SystemExit("summarize_telemetry.py must auto-write debug summary and run trace when manifest telemetry is debug")
    run(["python3", "skills/goal-main-orchestrator/scripts/summarize_telemetry.py", "--bundle-dir", telemetry_bundle.as_posix(), "--debug"])
    debug_summary = read_json(telemetry_bundle / "telemetry.debug.summary.json")
    if not isinstance(debug_summary, dict) or "model_usage" not in debug_summary:
        raise SystemExit("debug telemetry summary must expose model_usage")
    normal_cost = telemetry_summary.get("cost_summary", {})
    debug_model_usage = debug_summary.get("model_usage", {})
    if telemetry_summary.get("telemetry_count") != debug_summary.get("telemetry_count"):
        raise SystemExit("normal and debug telemetry summaries should agree on telemetry_count")
    for normal_key, debug_key in [
        ("declared_attempts", "attempts_declared"),
        ("called_attempts", "attempts_called"),
        ("candidate_attempts", "candidate_attempts"),
        ("executed_attempts", "executed_attempts"),
    ]:
        if normal_cost.get(normal_key) != debug_model_usage.get(debug_key):
            raise SystemExit(f"normal/debug telemetry summary count mismatch for {normal_key}/{debug_key}")
    if normal_cost.get("token_totals_status") != "complete":
        raise SystemExit(f"standard telemetry summary should mark complete token totals for fully covered executed attempts: {normal_cost!r}")
    if normal_cost.get("attempt_totals", {}).get("candidate_attempts") != 2 or normal_cost.get("attempt_totals", {}).get("executed_attempts") != 1:
        raise SystemExit(f"standard telemetry summary should separate candidate and executed attempts: {normal_cost!r}")
    packets = telemetry_summary.get("packets", [])
    packet_attempts = packets[0].get("attempts", []) if packets else []
    if len(packet_attempts) < 2 or packet_attempts[1].get("not_executed_reason") != "fallback_not_needed":
        raise SystemExit(f"standard telemetry summary should preserve skipped fallback reasons: {packets!r}")
    normal_accepted = sum(normal_cost.get("accepted_aliases", {}).values()) if isinstance(normal_cost.get("accepted_aliases"), dict) else None
    if normal_accepted != debug_model_usage.get("accepted_attempts"):
        raise SystemExit("normal and debug telemetry summaries should agree on accepted attempts")
    if not isinstance(debug_summary.get("text_metrics"), dict) or "debug_overhead_chars" not in debug_summary["text_metrics"]:
        raise SystemExit("debug telemetry summary must expose text metrics and debug_overhead_chars")
    if not isinstance(debug_summary.get("time_metrics"), dict) or "timeout_rate" not in debug_summary["time_metrics"]:
        raise SystemExit("debug telemetry summary must expose timeout_rate")
    determinism = debug_summary.get("determinism")
    if not isinstance(determinism, dict) or "drift_count" not in determinism:
        raise SystemExit("debug telemetry summary must expose determinism drift count")
    trace = debug_summary.get("trace")
    if not isinstance(trace, dict) or trace.get("path") != "run.trace.jsonl" or not isinstance(trace.get("event_count"), int):
        raise SystemExit("debug telemetry summary must expose run trace metadata")
    trace_path = telemetry_bundle / "run.trace.jsonl"
    if not trace_path.exists():
        raise SystemExit("debug telemetry summary must write run.trace.jsonl")
    trace_events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    trace_types = {event.get("event_type") for event in trace_events if isinstance(event, dict)}
    for expected_type in ["scheduler_event", "packet_debug_event", "launcher_state", "model_attempt", "packet_telemetry", "terminal_artifact", "state_change"]:
        if expected_type not in trace_types:
            raise SystemExit(f"run.trace.jsonl missing expected event type {expected_type}: {trace_types!r}")
    state_changes = [event for event in trace_events if isinstance(event, dict) and event.get("event_type") == "state_change"]
    if not state_changes:
        raise SystemExit("run.trace.jsonl should include canonical state_change events")
    required_state_change_keys = {"action_type", "actor", "artifact_paths", "artifact_hashes", "manifest_epoch", "new_state"}
    if not all(required_state_change_keys.issubset(event) for event in state_changes):
        raise SystemExit(f"state_change trace events should include action/actor/artifact hash/state fields: {state_changes!r}")
    if not any(event.get("action_type") == "worker_status_write" for event in state_changes):
        raise SystemExit(f"state_change trace should include worker status writes: {state_changes!r}")
    if any("raw_text" in event for event in trace_events if isinstance(event, dict)):
        raise SystemExit("run.trace.jsonl must not include raw_text payloads")


def run_example_brief_fixtures(tmp_path: Path) -> None:
    example_brief_path = tmp_path / "example-brief.json"
    example_brief_path.write_text(
        run(["python3", "skills/goal-preflight/scripts/create_goal_bundle.py", "--example-brief"]).stdout,
        encoding="utf-8",
    )
    lint_example = json.loads(
        run(["python3", "skills/goal-preflight/scripts/lint_preflight_brief.py", "--example-brief"]).stdout
    )
    if lint_example != json.loads(example_brief_path.read_text(encoding="utf-8")):
        raise SystemExit("brief example output drifted between create and lint helpers")
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/lint_preflight_brief.py",
            "--brief",
            example_brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--fail-on",
            "critical",
        ]
    )
    string_bullet_brief = json.loads(example_brief_path.read_text(encoding="utf-8"))
    string_bullet_brief["branches"][0]["stop_conditions"] = "Stop if public behavior tests fail."
    string_bullet_brief["branches"][0]["dod"] = "Branch-level behavior remains compatible."
    string_bullet_path = tmp_path / "string-bullet-brief.json"
    string_bullet_path.write_text(json.dumps(string_bullet_brief, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    string_bullet_bundle = tmp_path / "string-bullet-bundle"
    run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            string_bullet_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            string_bullet_bundle.as_posix(),
        ]
    )
    string_bullet_prompt = (string_bullet_bundle / "branches" / "B01.prompt.md").read_text(encoding="utf-8")
    assert_all_contains(string_bullet_prompt, ["- Stop if public behavior tests fail.", "- Branch-level behavior remains compatible."], "string bullet branch prompt")
    if "- S\n- t\n- o\n- p" in string_bullet_prompt or "- B\n- r\n- a\n- n\n- c\n- h" in string_bullet_prompt:
        raise SystemExit("branch prompt split a string field into one bullet per character")


def run_prompt_audit_packet_fixtures(tmp_path: Path, bundle: Path) -> None:
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/create_audit_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            (tmp_path / "audit").as_posix(),
        ]
    )
    assert_compact_audit_launcher(tmp_path / "audit")
    fake_bin = tmp_path / "fake-codex-bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\necho fake codex failure >&2\nexit 42\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    audit_env = {**os.environ, "PATH": fake_bin.as_posix() + os.pathsep + os.environ.get("PATH", "")}
    run([(tmp_path / "audit" / "launch.sh").as_posix()], expect=1, env=audit_env)
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/validate_prompt_audit.py",
            "--audit",
            (tmp_path / "audit" / "prompt-audit.json").as_posix(),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
        ]
    )
    blocked_audit = read_json(tmp_path / "audit" / "prompt-audit.json")
    if blocked_audit.get("status") != "blocked" or blocked_audit.get("can_start") is not False:
        raise SystemExit(f"fake-codex audit launcher did not write a terminal blocked audit: {blocked_audit!r}")

    deterministic_audit_phase = tmp_path / "audit-deterministic-phase"
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/run_prompt_audit_phase.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit-dir",
            deterministic_audit_phase.as_posix(),
            "--deterministic",
            "--require-pass",
        ]
    )
    deterministic_phase_report = read_json(deterministic_audit_phase / "prompt-audit-phase.json")
    if deterministic_phase_report.get("status") != "pass":
        raise SystemExit(f"deterministic prompt-audit phase should pass a lint-clean bundle: {deterministic_phase_report!r}")
    deterministic_audit = read_json(deterministic_audit_phase / "prompt-audit.json")
    if deterministic_audit.get("status") != "pass" or deterministic_audit.get("can_start") is not True:
        raise SystemExit(f"deterministic prompt-audit did not write a passing audit: {deterministic_audit!r}")
    deterministic_telemetry = read_json(deterministic_audit_phase / "telemetry.json")
    if deterministic_telemetry.get("accepted_alias") != "deterministic-prompt-audit":
        raise SystemExit(f"deterministic prompt-audit telemetry mismatch: {deterministic_telemetry!r}")
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/validate_prompt_audit.py",
            "--audit",
            (deterministic_audit_phase / "prompt-audit.json").as_posix(),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--require-pass",
        ]
    )
    ready_after_deterministic_audit = run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit",
            (deterministic_audit_phase / "prompt-audit.json").as_posix(),
            "--list-ready",
            "--limit",
            "1",
        ]
    ).stdout.strip().splitlines()
    if ready_after_deterministic_audit != ["B01"]:
        raise SystemExit(f"deterministic prompt-audit telemetry did not unlock branch scheduling: {ready_after_deterministic_audit!r}")

    audit_phase = tmp_path / "audit-phase"
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/run_prompt_audit_phase.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--audit-dir",
            audit_phase.as_posix(),
            "--attempt-timeout-seconds",
            "7",
            "--require-pass",
        ],
        expect=1,
        env=audit_env,
    )
    phase_report = read_json(audit_phase / "prompt-audit-phase.json")
    if phase_report.get("status") != "blocked":
        raise SystemExit(f"prompt-audit phase wrapper should preserve structured blocked state: {phase_report!r}")
    phase_audit = read_json(audit_phase / "prompt-audit.json")
    if phase_audit.get("status") != "blocked" or phase_audit.get("can_start") is not False:
        raise SystemExit(f"prompt-audit phase wrapper did not write blocked audit: {phase_audit!r}")
    phase_config = read_json(audit_phase / "launch-config.json")
    if phase_config.get("attempt_timeout_seconds") != 7:
        raise SystemExit(f"prompt-audit phase wrapper did not pass timeout override: {phase_config!r}")

    audit_signal = tmp_path / "audit-signal"
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/create_audit_packet.py",
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            audit_signal.as_posix(),
        ]
    )
    fake_codex_ready = audit_signal / "fake-codex-ready"
    fake_codex.write_text(f"#!/usr/bin/env bash\nprintf ready > {shlex.quote(fake_codex_ready.as_posix())}\nsleep 30\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    process = subprocess.Popen(
        [(audit_signal / "launch.sh").as_posix()],
        cwd=ROOT,
        env=audit_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    for _ in range(50):
        if fake_codex_ready.exists():
            break
        time.sleep(0.02)
    else:
        process.kill()
        raise SystemExit("fake audit command did not start before SIGTERM fixture")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        raise SystemExit("audit launcher did not exit after SIGTERM")
    run(
        [
            "python3",
            "skills/goal-main-orchestrator/scripts/validate_prompt_audit.py",
            "--audit",
            (audit_signal / "prompt-audit.json").as_posix(),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            ROOT.as_posix(),
        ]
    )
    interrupted_audit = read_json(audit_signal / "prompt-audit.json")
    interrupted_summary = str(interrupted_audit.get("summary", ""))
    if interrupted_audit.get("status") != "blocked" or "interrupted" not in interrupted_summary:
        raise SystemExit(f"SIGTERM audit launcher did not write interrupted blocked audit: {interrupted_audit!r}")


def run_lite_advice_packet_fixture(tmp_path: Path, task_file: Path) -> None:
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_lite_advice_packet.py",
            "--packet-id",
            "B01-L01",
            "--purpose",
            "branch-packet-planning",
            "--base-dir",
            ROOT.as_posix(),
            "--out-dir",
            (tmp_path / "lite").as_posix(),
            "--input-file",
            (ROOT / "README.md").as_posix(),
            "--task-file",
            task_file.as_posix(),
        ],
        env=offline_gemini_env(),
    )
    assert_compact_lite_launcher(tmp_path / "lite" / "B01-L01")


def run_reviewer_packet_fixtures(tmp_path: Path, bundle: Path, packet_root: Path, task_file: Path) -> None:
    write_valid_research_fixture(bundle)
    write_worker_scheduler(bundle)
    stale_terminal_bundle = tmp_path / "pre-review-stale-packet-terminal"
    shutil.copytree(bundle, stale_terminal_bundle)
    launcher = read_json(stale_terminal_bundle / "research" / "B01-W01" / "launcher-state.json")
    launcher["terminal_state"] = "blocked"
    write_json(stale_terminal_bundle / "research" / "B01-W01" / "launcher-state.json", launcher)
    summary = read_json(stale_terminal_bundle / "research" / "B01-W01" / "packet.summary.json")
    summary["terminal_state"] = "blocked"
    summary["output_status"] = "pass"
    write_json(stale_terminal_bundle / "research" / "B01-W01" / "packet.summary.json", summary)
    stale_gate = run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (stale_terminal_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--replace",
            "--skip-tests",
            "--test-skip-reason",
            "Static preparedness fixture does not run branch tests.",
            "--dod-item",
            "research-worker fixture validates",
            "--json",
        ],
        expect=1,
    )
    assert_contains(stale_gate.stdout, "launcher-state terminal_state", "stale packet terminal pre-review fixture")
    write_pre_review_gate(bundle)
    gate = read_json(bundle / "branches" / "B01.pre_review_gate.json")
    semantic_hashes = gate.get("semantic_input_hashes") if isinstance(gate.get("semantic_input_hashes"), dict) else {}
    for rel_path in [
        "research/B01-W01/launcher-state.json",
        "research/B01-W01/packet.summary.json",
    ]:
        if rel_path not in semantic_hashes:
            raise SystemExit(f"pre-review gate omitted packet terminal freshness hash for {rel_path}: {semantic_hashes!r}")
    diagnostic_hashes = (
        gate.get("volatile_input_hashes", {}).get("diagnostic_artifacts", {})
        if isinstance(gate.get("volatile_input_hashes"), dict)
        else {}
    )
    for rel_path in [
        "research/B01-W01/attempts/attempt-001/launcher-state.json",
        "research/B01-W01/attempts/attempt-001/packet.summary.json",
        "research/B01-W01/attempts/attempt-001/research.json",
        "research/B01-W01/attempts/attempt-001/telemetry.json",
    ]:
        if rel_path in semantic_hashes:
            raise SystemExit(f"pre-review gate included diagnostic attempt snapshot in semantic hashes for {rel_path}: {semantic_hashes!r}")
        if rel_path not in diagnostic_hashes:
            raise SystemExit(f"pre-review gate omitted diagnostic attempt hash for {rel_path}: {diagnostic_hashes!r}")
    create_runtime_packet(
        role="reviewer",
        packet_id="B01-R01",
        branch="preparedness-research-fixture",
        out_dir=packet_root,
        manifest=bundle / "job.manifest.json",
        pre_review_gate=bundle / "branches" / "B01.pre_review_gate.json",
        context_files=[
            bundle / "branches" / "B01.prompt.md",
            bundle / "branches" / "B01.pre_review_gate.json",
        ],
        task_file=task_file,
    )
    reviewer_config = assert_compact_runtime_launcher(packet_root / "B01-R01", "reviewer", 1800)
    assert_openai_strict_schema(packet_root / "B01-R01" / "review.schema.json", "reviewer schema")
    reviewer_packet_root = packet_root / "B01-R01"
    reviewer_packet_context = reviewer_packet_root / "packet-context.json"
    reviewer_prompt = (reviewer_packet_root / "prompt.md").read_text(encoding="utf-8")
    if not reviewer_packet_context.exists():
        raise SystemExit("reviewer packet-context.json was not created")
    reviewer_context_text = reviewer_packet_context.read_text(encoding="utf-8")
    if "compact_reviewer_context" not in reviewer_context_text:
        raise SystemExit(f"reviewer packet context should be compact: {reviewer_context_text!r}")
    if f"Packet context to read first:\n- {reviewer_packet_context.as_posix()}" not in reviewer_prompt:
        raise SystemExit("reviewer prompt should read compact_reviewer_context first")
    reviewer_attempts = assert_lean_codex_attempts(reviewer_config.get("attempts"), "reviewer Codex attempts")
    reviewer_aliases = [attempt.get("alias") for attempt in reviewer_attempts if isinstance(attempt, dict)]
    if reviewer_aliases != ["gpt-5.5", "gpt-5.4"]:
        raise SystemExit(f"reviewer launch-config route mismatch: {reviewer_aliases!r}")
    if not reviewer_config.get("semantic_input_hashes"):
        raise SystemExit("reviewer launch-config omitted semantic_input_hashes")
    if not reviewer_config.get("reuse_policy"):
        raise SystemExit("reviewer launch-config omitted reuse_policy")
    assert_reviewer_route(packet_root, "B01-R01", ["gpt-5.5", "gpt-5.4"])
    for packet_id, tier, expected in [
        ("B01-R02", "standard", ["gpt-5.4", "gpt-5.5"]),
        ("B01-R03", "heavy", ["gpt-5.5", "gpt-5.4"]),
    ]:
        gate_path = write_review_gate_variant(bundle, packet_id, tier=tier)
        create_runtime_packet(
            role="reviewer",
            packet_id=packet_id,
            branch="preparedness-research-fixture",
            out_dir=packet_root,
            manifest=bundle / "job.manifest.json",
            pre_review_gate=gate_path,
            context_files=[bundle / "branches" / "B01.prompt.md"],
            task_file=task_file,
        )
        assert_reviewer_route(packet_root, packet_id, expected)
    heavy_diff_gate = write_review_gate_variant(bundle, "B01-R04", diff_stats={"files_changed": 25, "lines_changed": 40})
    create_runtime_packet(
        role="reviewer",
        packet_id="B01-R04",
        branch="preparedness-research-fixture",
        out_dir=packet_root,
        manifest=bundle / "job.manifest.json",
        pre_review_gate=heavy_diff_gate,
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
    )
    assert_reviewer_route(packet_root, "B01-R04", ["gpt-5.5", "gpt-5.4"])
    failed_gate = json.loads((bundle / "branches" / "B01.pre_review_gate.json").read_text(encoding="utf-8"))
    failed_gate["status"] = "failed"
    failed_gate["checks"]["tests"] = {"status": "failed", "reason": "Negative fixture blocks reviewer launch."}
    write_json(bundle / "branches" / "B01.failed_pre_review_gate.json", failed_gate)
    failed_gate_result = create_runtime_packet(
        role="reviewer",
        packet_id="B01-R01",
        branch="preparedness-research-fixture",
        out_dir=tmp_path / "blocked-reviewer",
        manifest=bundle / "job.manifest.json",
        pre_review_gate=bundle / "branches" / "B01.failed_pre_review_gate.json",
        context_files=[bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
        expect=1,
    )
    assert_contains(failed_gate_result.stdout, "pre-review gate failed", "failed pre-review gate fixture")


def run_branch_status_negative_fixtures(tmp_path: Path, bundle: Path, task_file: Path) -> None:
    mergeable_gaps_bundle = tmp_path / "branch-status-mergeable-gaps"
    shutil.copytree(bundle, mergeable_gaps_bundle)
    write_json(
        mergeable_gaps_bundle / "branches" / "B01.review.json",
        {
            "packet_id": "B01-R01",
            "role": "reviewer",
            "verdict": "mergeable",
            "findings": [],
            "finding_classes": ["verification_gap"],
            "commands_run": ["pytest -q"],
            "verification_gaps": ["direct rerun failed in this fixture"],
            "residual_risks": [],
            "semantic_input_hashes": {},
            "reuse_policy": {
                "mode": "new",
                "accepted": False,
                "semantic_hashes_match": False,
                "source_review_path": None,
                "source_telemetry_path": None,
            },
            "summary": "Mergeable review with verification gaps fixture",
        },
    )
    assemble_branch_status(mergeable_gaps_bundle)
    mergeable_gaps_branch_status = read_json(mergeable_gaps_bundle / "branches" / "B01.status.json")
    if mergeable_gaps_branch_status.get("review_status") != "missing":
        raise SystemExit("mergeable-with-gaps review should be downgraded to missing review status")
    waiver = read_json(mergeable_gaps_bundle / "branches" / "B01.review-waiver.json")
    if waiver.get("review_artifact_rejected") is not True:
        raise SystemExit("mergeable-with-gaps review waiver should record rejected review artifact")
    mergeable_gaps_status = json.loads(validate_branch(mergeable_gaps_bundle).stdout)
    if mergeable_gaps_status.get("artifact_valid") is not True:
        raise SystemExit("mergeable-with-gaps review should normalize to non-validator-failing branch status")

    assemble_branch_status(bundle)
    missing_waiver_bundle = tmp_path / "branch-status-missing-review-waiver"
    shutil.copytree(bundle, missing_waiver_bundle)
    missing_waiver_status = read_json(missing_waiver_bundle / "branches" / "B01.status.json")
    missing_waiver_status.pop("review_waiver_path", None)
    write_json(missing_waiver_bundle / "branches" / "B01.status.json", missing_waiver_status)
    missing_waiver_result = validate_branch(missing_waiver_bundle, expect=1)
    assert_contains(missing_waiver_result.stdout, "$.review_waiver_path", "missing review waiver fixture")

    dirty_worktree = tmp_path / "pre-review-dirty-worker-worktree"
    dirty_worktree.mkdir()
    run(["git", "init", "-b", "main", dirty_worktree.as_posix()])
    run(["git", "-C", dirty_worktree.as_posix(), "config", "user.email", "fixture@example.com"])
    run(["git", "-C", dirty_worktree.as_posix(), "config", "user.name", "Fixture"])
    (dirty_worktree / "README.md").write_text("initial fixture content\n", encoding="utf-8")
    run(["git", "-C", dirty_worktree.as_posix(), "add", "README.md"])
    run(["git", "-C", dirty_worktree.as_posix(), "commit", "-m", "initial fixture"])
    (dirty_worktree / "README.md").write_text("accepted worker edit left dirty\n", encoding="utf-8")
    dirty_gate_bundle = tmp_path / "pre-review-dirty-worker-gate"
    shutil.copytree(bundle, dirty_gate_bundle)
    write_valid_research_fixture(dirty_gate_bundle)
    write_worker_scheduler(dirty_gate_bundle)
    assemble_branch_status(dirty_gate_bundle)
    dirty_status = read_json(dirty_gate_bundle / "branches" / "B01.status.json")
    dirty_status["worktree"] = dirty_worktree.as_posix()
    dirty_status["changed_files"] = ["README.md"]
    dirty_status["worker_statuses"][0]["changed_files"] = ["README.md"]
    write_json(dirty_gate_bundle / "branches" / "B01.status.json", dirty_status)
    run(
        [
            "python3",
            "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
            "--manifest",
            (dirty_gate_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            "B01",
            "--worktree",
            dirty_worktree.as_posix(),
            "--review-packet-id",
            "B01-R01",
            "--replace",
            "--skip-tests",
            "--test-skip-reason",
            "Static dirty-worker fixture does not run branch tests.",
            "--dod-item",
            "dirty worker changes must be integrated before review",
            "--json",
        ],
        expect=1,
    )
    dirty_gate_artifact = read_json(dirty_gate_bundle / "branches" / "B01.pre_review_gate.json")
    integration = dirty_gate_artifact.get("checks", {}).get("worktree_integration", {})
    if integration.get("status") != "failed" or integration.get("dirty_worker_changed_files") != ["README.md"]:
        raise SystemExit(f"dirty worker pre-review gate should fail with README.md integration defect: {integration!r}")

    stale_canonical_bundle = tmp_path / "branch-status-stale-canonical-review"
    shutil.copytree(bundle, stale_canonical_bundle)
    write_valid_research_fixture(stale_canonical_bundle)
    write_worker_scheduler(stale_canonical_bundle)
    assemble_branch_status(stale_canonical_bundle)
    write_pre_review_gate(stale_canonical_bundle)
    gate = read_json(stale_canonical_bundle / "branches" / "B01.pre_review_gate.json")
    gate["review_packet_id"] = "B01-R02"
    write_json(stale_canonical_bundle / "branches" / "B01.pre_review_gate.json", gate)
    create_runtime_packet(
        role="reviewer",
        packet_id="B01-R02",
        branch="preparedness-research-fixture",
        out_dir=stale_canonical_bundle / "reviewers",
        manifest=stale_canonical_bundle / "job.manifest.json",
        pre_review_gate=stale_canonical_bundle / "branches" / "B01.pre_review_gate.json",
        context_files=[stale_canonical_bundle / "branches" / "B01.prompt.md"],
        task_file=task_file,
    )
    semantic_hashes = gate.get("semantic_input_hashes") if isinstance(gate.get("semantic_input_hashes"), dict) else {}
    write_mergeable_reviewer_packet(stale_canonical_bundle, "B01-R02", semantic_hashes)
    write_json(
        stale_canonical_bundle / "branches" / "B01.review.json",
        {
            "packet_id": "B01-R01",
            "role": "reviewer",
            "verdict": "blocked",
            "findings": ["stale canonical review fixture"],
            "finding_classes": ["orchestration_bug"],
            "commands_run": ["git diff --check main...HEAD"],
            "verification_gaps": ["stale canonical artifact"],
            "residual_risks": [],
            "semantic_input_hashes": {"branches/B01.prompt.md": "sha256:" + "3" * 64},
            "reuse_policy": {
                "mode": "new",
                "accepted": False,
                "semantic_hashes_match": False,
                "source_review_path": None,
                "source_telemetry_path": None,
            },
            "summary": "Stale canonical review fixture.",
        },
    )
    promoted_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/assemble_branch_status.py",
                "--manifest",
                (stale_canonical_bundle / "job.manifest.json").as_posix(),
                "--branch-id",
                "B01",
                "--worktree",
                ROOT.as_posix(),
                "--allow-pass",
                "--replace",
                "--test-evidence",
                "stale canonical review promotion fixture tests pass",
                "--dod-item",
                "stale canonical review promotion fixture DoD evidence",
                "--json",
            ]
        ).stdout
    )
    if promoted_result.get("branch_status") != "pass" or promoted_result.get("review_status") != "mergeable":
        raise SystemExit(f"stale canonical review should be replaced by current reviewer output: {promoted_result!r}")
    promoted_review = read_json(stale_canonical_bundle / "branches" / "B01.review.json")
    if promoted_review.get("packet_id") != "B01-R02":
        raise SystemExit(f"canonical review should promote current B01-R02 artifact: {promoted_review!r}")
    validate_branch(stale_canonical_bundle)

    write_json(
        bundle / "schedulers" / "main.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "main-branch-pool",
            "scheduler_path": "schedulers/main.scheduler.json",
            "manifest_sha256": sha256_file(bundle / "job.manifest.json"),
            "capacity": 1,
            "item_ids": ["B01"],
            "events": [
                scheduler_event(1, "ready", id="B01"),
                scheduler_event(2, "launch", id="B01"),
                scheduler_event(3, "finish", id="B01", status="partial"),
                scheduler_event(4, "close", id="B01"),
            ],
        },
    )
    branch_validation = json.loads(validate_branch(bundle).stdout)
    if branch_validation.get("artifact_valid") is not True or branch_validation.get("runtime_success") is not False:
        raise SystemExit(f"branch validator should distinguish valid artifact from non-pass runtime outcome: {branch_validation!r}")
    if branch_validation.get("dod_complete") is not False or branch_validation.get("review_complete") is not False:
        raise SystemExit(f"branch validator should expose incomplete DoD/review lanes: {branch_validation!r}")
    main_status_result = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/assemble_main_status.py",
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--replace",
                "--json",
            ]
        ).stdout
    )
    if main_status_result.get("artifact_valid") is not True or main_status_result.get("runtime_success") is not False:
        raise SystemExit(f"assembled main status should distinguish artifact validity from runtime success: {main_status_result!r}")
    main_validation = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/validate_main_status.py",
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--status",
                (bundle / "main.status.json").as_posix(),
                "--json",
            ]
        ).stdout
    )
    if main_validation.get("artifact_valid") is not True or main_validation.get("runtime_success") is not False:
        raise SystemExit(f"main validator should distinguish valid artifact from non-pass runtime outcome: {main_validation!r}")
    if main_validation.get("dod_complete") is not False or main_validation.get("review_complete") is not False:
        raise SystemExit(f"main validator should expose incomplete DoD/review lanes: {main_validation!r}")
    main_assembler = load_script_module("preparedness_assemble_main_status", "skills/goal-main-orchestrator/scripts/assemble_main_status.py")
    main_validator = load_script_module("preparedness_validate_main_status", "skills/goal-main-orchestrator/scripts/validate_main_status.py")
    main_reconciler = load_script_module("preparedness_reconcile_goal_run", "skills/_goal_shared/scripts/reconcile_goal_run.py")
    recovered = {
        "branch_id": "B01",
        "status": "blocked",
        "status_path": "branches/B01.status.json",
        "review_path": "branches/B01.review.json",
        "review_status": "missing",
        "recovered_by": ["B02"],
        "recovery_status": "recovered",
    }
    recovery = {
        "branch_id": "B02",
        "status": "pass",
        "status_path": "branches/B02.status.json",
        "review_path": "branches/B02.review.json",
        "review_status": "mergeable",
    }
    recovery_by_id = {"B01": recovered, "B02": recovery}
    manifest_recovery_by_id = {"B01": {"id": "B01"}, "B02": {"id": "B02", "recovers_from": ["B01"]}}
    stale_manifest_recovery_by_id = {"B01": {"id": "B01"}, "B02": {"id": "B02"}}
    if main_assembler.choose_status("pass", [recovered, recovery], 2, []) != "pass":
        raise SystemExit("main assembler should allow pass when a non-pass branch is explicitly recovered by a mergeable replacement")
    if main_assembler.aggregate_review_status([recovered, recovery], 2) != "mergeable":
        raise SystemExit("main assembler should treat explicitly recovered non-pass branches as review-complete")
    if main_validator.branch_summary_recovered(recovered, recovery_by_id, manifest_recovery_by_id) is not True:
        raise SystemExit("main validator should recognize recovered branch summaries with a passing mergeable recovered_by branch")
    if main_validator.branch_summary_recovered(recovered, recovery_by_id, stale_manifest_recovery_by_id) is not False:
        raise SystemExit("main validator must reject recovered_by links that are not declared by the manifest")
    pending_recovery = {**recovered, "recovered_by": ["B03"]}
    if main_assembler.choose_status("pass", [pending_recovery, recovery], 2, []) == "pass":
        raise SystemExit("main assembler must not pass when recovered_by does not point to a passing mergeable branch")
    if main_validator.branch_summary_recovered(pending_recovery, recovery_by_id, manifest_recovery_by_id) is not False:
        raise SystemExit("main validator must not accept dangling recovery references")
    recovery_branch_reports = [
        {"branch_id": "B01", "runtime_status": "blocked", "review_status": "missing", "validation": {"status": "pass"}},
        {"branch_id": "B02", "runtime_status": "pass", "review_status": "mergeable", "validation": {"status": "pass"}},
    ]
    if main_reconciler.recovered_branch_ids(list(manifest_recovery_by_id.values()), recovery_branch_reports) != {"B01"}:
        raise SystemExit("reconcile_goal_run.py should derive recovered branches from manifest-declared successful replacements")
    if main_reconciler.recovered_branch_ids(list(stale_manifest_recovery_by_id.values()), recovery_branch_reports):
        raise SystemExit("reconcile_goal_run.py must not treat unrelated passing branches as recoveries")
    unrecovered_blocked = [
        item
        for item in recovery_branch_reports
        if item.get("runtime_status") in {"partial", "blocked", "failed"}
        and item.get("branch_id") not in main_reconciler.recovered_branch_ids(list(manifest_recovery_by_id.values()), recovery_branch_reports)
    ]
    if unrecovered_blocked:
        raise SystemExit(f"recovered non-pass branches must be excluded from remaining blockers: {unrecovered_blocked!r}")
    pre_dispatch_bundle = tmp_path / "reconcile-pre-branch-dispatch"
    shutil.copytree(bundle, pre_dispatch_bundle)
    pre_dispatch_manifest = read_json(pre_dispatch_bundle / "job.manifest.json")
    for branch in pre_dispatch_manifest.get("branches", []):
        if isinstance(branch, dict):
            branch["worktree_path"] = f".worktrees/pre-dispatch-{branch.get('id')}"
    write_json(pre_dispatch_bundle / "job.manifest.json", pre_dispatch_manifest)
    for rel_path in [
        "main.status.json",
        "branches/B01.status.json",
        "branches/B01.review.json",
        "branches/B01.pre_review_gate.json",
        "telemetry.summary.json",
    ]:
        path = pre_dispatch_bundle / rel_path
        if path.exists():
            path.unlink()
    shutil.rmtree(pre_dispatch_bundle / "workers", ignore_errors=True)
    shutil.rmtree(pre_dispatch_bundle / "research", ignore_errors=True)
    shutil.rmtree(pre_dispatch_bundle / "reviewers", ignore_errors=True)
    shutil.rmtree(pre_dispatch_bundle / "lite", ignore_errors=True)
    shutil.rmtree(pre_dispatch_bundle / "amendments", ignore_errors=True)
    shutil.rmtree(pre_dispatch_bundle / "schedulers", ignore_errors=True)
    (pre_dispatch_bundle / "audit").mkdir(parents=True, exist_ok=True)
    write_prompt_audit_fixture(pre_dispatch_bundle)
    write_json(
        pre_dispatch_bundle / "audit" / "prompt-audit-phase.json",
        {"schema_version": 1, "status": "pass", "can_start": True},
    )
    pre_dispatch_report = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/reconcile_goal_run.py",
                "--manifest",
                (pre_dispatch_bundle / "job.manifest.json").as_posix(),
                "--repo-root",
                ROOT.as_posix(),
                "--write",
                "--json",
            ]
        ).stdout
    )
    if pre_dispatch_report.get("execution_phase") != "pre_branch_dispatch":
        raise SystemExit(f"pre-dispatch reconcile should expose launch-pending phase: {pre_dispatch_report!r}")
    if pre_dispatch_report.get("status") != "incomplete":
        raise SystemExit(f"pre-dispatch reconcile should be incomplete, not terminal blocked: {pre_dispatch_report!r}")
    if pre_dispatch_report.get("final_state_validation", {}).get("status") != "incomplete":
        raise SystemExit(f"pre-dispatch final validation should be deferred: {pre_dispatch_report!r}")
    if (pre_dispatch_bundle / "main.status.json").exists():
        raise SystemExit("pre-dispatch reconcile --write should not materialize a conservative main.status.json")
    if not any("render_branch_worktree_commands.py" in command for command in pre_dispatch_report.get("next_commands", [])):
        raise SystemExit(f"pre-dispatch next commands should render branch launch commands: {pre_dispatch_report!r}")
    render_commands = [
        command
        for command in pre_dispatch_report.get("next_commands", [])
        if isinstance(command, str) and "render_branch_worktree_commands.py" in command
    ]
    if not render_commands or "audit/prompt-audit.json" not in render_commands[0] or "prompt-audit-phase.json" in render_commands[0]:
        raise SystemExit(f"pre-dispatch branch render command should use canonical prompt-audit.json: {render_commands!r}")
    run(shlex.split(render_commands[0]))
    blocked_codes = {
        item.get("code")
        for item in pre_dispatch_report.get("blocked_work_remaining", [])
        if isinstance(item, dict)
    }
    if {"missing_main_status", "missing_branch_status", "missing_branch_worktree"} & blocked_codes:
        raise SystemExit(f"pre-dispatch missing launch artifacts should be pending work, not blocked work: {pre_dispatch_report!r}")
    resume_report = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/reconcile_goal_run.py",
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--json",
            ]
        ).stdout
    )
    if resume_report.get("artifact_reuse_safe") != resume_report.get("safe_to_reuse", {}).get("overall"):
        raise SystemExit(f"resume report artifact_reuse_safe should mirror safe_to_reuse.overall: {resume_report!r}")
    if resume_report.get("goal_complete") is not False:
        raise SystemExit(f"resume report should separate artifact reuse safety from goal completion: {resume_report!r}")
    if not resume_report.get("blocked_branches") or not resume_report.get("blocked_work_remaining"):
        raise SystemExit(f"resume report should make blocked branch work prominent: {resume_report!r}")
    if resume_report.get("next_required_action") != resume_report.get("resume_action"):
        raise SystemExit(f"resume report next_required_action should mirror deterministic resume_action: {resume_report!r}")
    stale_archive_bundle = tmp_path / "stale-artifact-index"
    shutil.copytree(bundle, stale_archive_bundle)
    write_json(stale_archive_bundle / "branches" / "stale" / "B01.status.old.json", {"branch_id": "B01", "status": "blocked"})
    write_json(stale_archive_bundle / "reviewers.stale" / "B01-R01" / "review.old.json", {"packet_id": "B01-R01", "verdict": "needs_changes"})
    stale_before = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/reconcile_goal_run.py",
                "--manifest",
                (stale_archive_bundle / "job.manifest.json").as_posix(),
                "--json",
            ]
        ).stdout
    )
    if not any(item.get("code") == "missing_stale_artifact_index" for item in stale_before.get("stale_or_unreconciled", [])):
        raise SystemExit(f"stale archive without an index should be reported: {stale_before!r}")
    stale_after = json.loads(
        run(
            [
                "python3",
                "skills/goal-main-orchestrator/scripts/reconcile_goal_run.py",
                "--manifest",
                (stale_archive_bundle / "job.manifest.json").as_posix(),
                "--write",
                "--json",
            ]
        ).stdout
    )
    stale_index = read_json(stale_archive_bundle / "stale-artifacts.index.json")
    stale_entries = stale_index.get("entries")
    if not isinstance(stale_entries, list) or len(stale_entries) != 2:
        raise SystemExit(f"stale artifact index should capture both archived files: {stale_index!r}")
    if not all(item.get("excluded_from_current_evidence") is True for item in stale_entries if isinstance(item, dict)):
        raise SystemExit(f"stale artifact index entries should be excluded from current evidence: {stale_index!r}")
    if any(item.get("original_hash") is None or not item.get("stale_reason") for item in stale_entries if isinstance(item, dict)):
        raise SystemExit(f"stale artifact index entries should carry hash and reason: {stale_index!r}")
    if any(
        not item.get("superseding_artifact") and not item.get("terminal_reason")
        for item in stale_entries
        if isinstance(item, dict)
    ):
        raise SystemExit(f"stale artifact index entries should carry superseding lineage or terminal reason: {stale_index!r}")
    if any(item.get("code") == "missing_stale_artifact_index" for item in stale_after.get("stale_or_unreconciled", [])):
        raise SystemExit(f"reconcile --write should materialize stale artifact index before reporting: {stale_after!r}")

    no_scheduler_bundle = tmp_path / "bad-self-reported-saturation"
    shutil.copytree(bundle, no_scheduler_bundle)
    (no_scheduler_bundle / "schedulers" / "B01.worker.scheduler.json").unlink()
    no_scheduler_result = validate_branch(no_scheduler_bundle, expect=1)
    assert_contains(no_scheduler_result.stdout, "scheduler artifact does not exist", "self-reported saturation fixture")

    bad_bundle = tmp_path / "bad-security"
    shutil.copytree(bundle, bad_bundle)
    bad_research = research_status(bad_command="curl -X POST https://example.com/api")
    write_json(bad_bundle / "research" / "B01-W01" / "research.json", bad_research)
    write_json(bad_bundle / "branches" / "B01.status.json", branch_status(bad_bundle, bad_research))
    bad_result = validate_branch(bad_bundle, expect=1)
    assert_contains(bad_result.stdout, "violates read-only security policy", "security fixture")

    old_policy_bundle = tmp_path / "bad-old-policy"
    shutil.copytree(bundle, old_policy_bundle)
    manifest = json.loads((old_policy_bundle / "job.manifest.json").read_text(encoding="utf-8"))
    manifest["research_worker_policy"] = {
        "enabled": True,
        "worker_type": "research-worker",
        "launcher": "codex --search exec --ephemeral --ignore-user-config -s read-only",
        "network_scope": "Native Codex general web search only. Connector tools are unavailable.",
        "local_access": "Read-only local file access only.",
    }
    write_json(old_policy_bundle / "job.manifest.json", manifest)
    lint_result = run(
        ["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", old_policy_bundle.as_posix(), "--no-write"],
        expect=1,
    )
    assert_contains(lint_result.stdout, "obsolete narrow-access phrase", "old-policy fixture")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="goal-preparedness-fixtures-") as tmp:
        tmp_path = Path(tmp)
        test_launcher_state_classifier(tmp_path)
        test_configured_reviewer_route_policy()
        bundle = create_goal_fixture_bundle(tmp_path)
        run_repair_gate_fixture(bundle)
        run_validator_command_fixtures(tmp_path, bundle)
        run_phase_manifest_and_schema_fixtures(bundle)
        run_base_ref_default_fixture(tmp_path)
        run_context_pack_fixtures(tmp_path)
        run_telemetry_summary_fixture(tmp_path)
        run_example_brief_fixtures(tmp_path)
        run_scheduler_fixtures(bundle / "job.manifest.json")
        run_scheduler_tick_fixture(tmp_path)
        run_launch_ready_helper_fixtures(tmp_path)
        run_preflight_brief_lint_fixtures(tmp_path)
        run_python_interpreter_normalization_fixture(tmp_path)
        run_package_skeleton_context_fixture(tmp_path)
        run_topology_fixtures(tmp_path)
        run_amendment_fixtures(tmp_path)
        run_pre_review_manifest_command_fixture(tmp_path, bundle)

        packet_root, task_file = run_runtime_packet_fixtures(tmp_path, bundle)

        run_prompt_audit_packet_fixtures(tmp_path, bundle)

        run_lite_advice_packet_fixture(tmp_path, task_file)

        run_reviewer_packet_fixtures(tmp_path, bundle, packet_root, task_file)
        run_opencode_wal_fallback_fixture(tmp_path)
        run_branch_status_negative_fixtures(tmp_path, bundle, task_file)

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
