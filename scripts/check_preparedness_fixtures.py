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
            "--skip-tests",
            "--test-skip-reason",
            "Static preparedness fixture does not run branch tests.",
            "--dod-item",
            "research-worker fixture validates",
        ]
    )


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
    write_json(packet_dir / "research.json", research)
    write_json(
        packet_dir / "telemetry.json",
        telemetry(
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
        ),
    )
    write_json(bundle / "branches" / "B01.status.json", branch_status(bundle, research))


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


def run_amendment_fixtures(tmp_path: Path) -> None:
    bundle = create_amendment_bundle(tmp_path, "amendment-fixture")
    write_json(bundle / "branches" / "B01.status.json", {"branch_id": "B01", "status": "blocked"})
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
    for rel_path in [
        "amendments/A001.accepted.json",
        "amendments/A001.job.manifest.before.json",
        "branches/B03.prompt.md",
    ]:
        if not (bundle / rel_path).exists():
            raise SystemExit(f"accepted amendment missing artifact: {rel_path}")
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
        deterministic_ledgers.append((repeat_dir / "schedulers" / "main.scheduler.json").read_text(encoding="utf-8"))
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


def test_launcher_state_classifier() -> None:
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
    assert_codex_mini_worker_route(worker_config, "Fixture route selected to inspect generated timeout wrapper.")
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
    create_runtime_packet(
        role="worker",
        packet_id="B01-W01",
        branch="B01",
        out_dir=manifest_packet_root,
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
    manifest_worker_schema = read_json(manifest_packet_dir / "status.schema.json")
    branch_const = manifest_worker_schema.get("properties", {}).get("branch", {}).get("const")
    if branch_const != "preparedness-research-fixture":
        raise SystemExit(f"worker --manifest status schema branch mismatch: {branch_const!r}")
    if not (manifest_packet_dir / "packet-context.json").exists():
        raise SystemExit("worker --manifest did not create compact packet-context.json")
    assert_contains(manifest_worker_prompt, "Compact Worker Task", "worker --manifest prompt")
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
    fake_codex_dir = tmp_path / "fake-codex-worker"
    fake_codex_dir.mkdir()
    fake_codex = fake_codex_dir / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "out = pathlib.Path(args[args.index('-o') + 1])\n"
        "status = {\n"
        "  'packet_id': 'B01-W01',\n"
        "  'role': 'worker',\n"
        "  'status': 'pass',\n"
        "  'branch': 'preparedness-research-fixture',\n"
        f"  'worktree': {ROOT.as_posix()!r},\n"
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
    if context_index.get("severity") != "info":
        raise SystemExit(f"context_index skip must be severity info: {context_index!r}")


def run_validator_command_fixtures(tmp_path: Path, bundle: Path) -> None:
    stale_validator_bundle = tmp_path / "stale-validator-command"
    shutil.copytree(bundle, stale_validator_bundle)
    stale_prompt = stale_validator_bundle / "main.prompt.md"
    stale_text = stale_prompt.read_text(encoding="utf-8")
    stale_text = stale_text.replace(
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/Bxx.status.json",
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json",
        1,
    )
    stale_text = stale_text.replace(
        "validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/main.status.json",
        "validate_main_status.py --manifest /absolute/path/to/job.manifest.json",
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
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/Bxx.status.json",
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/main.status.json",
        1,
    )
    wrong_target_text = wrong_target_text.replace(
        "validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/main.status.json",
        "validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/main.status.json",
        1,
    )
    wrong_target_prompt.write_text(wrong_target_text, encoding="utf-8")
    wrong_target_branch_prompt = wrong_target_validator_bundle / "branches" / "B01.prompt.md"
    wrong_target_branch_text = wrong_target_branch_prompt.read_text(encoding="utf-8").replace(
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/B01.status.json",
        "validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/B99.status.json",
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
    assert_all_contains(main_phase_manifest, ["watchdog", "orchestration_watchdog.main_no_completion_wait_limit"], "main phase manifest")
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
    if readiness_json.get("lint_status", {}).get("brief_lint", {}).get("status") != "pass":
        raise SystemExit(f"readiness should find canonical preflight.brief.lint.json: {readiness_json.get('lint_status')!r}")
    if readiness_json.get("repair_gate", {}).get("status") != "pass":
        raise SystemExit(f"readiness should find canonical repair-gate.json: {readiness_json.get('repair_gate')!r}")
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
    for artifact in [
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "readiness.json",
        "goal-config-selection.json",
        "preflight.pipeline.json",
        "goal-bootloader.md",
    ]:
        if not (pipeline_bundle / artifact).exists():
            raise SystemExit(f"prepare_goal_bundle.py did not persist canonical artifact {artifact}")
    pipeline_manifest = read_json(pipeline_bundle / "job.manifest.json")
    source_attachments = pipeline_manifest.get("source_attachments", [])
    if not source_attachments or source_attachments[0].get("path") != "README.md" or not source_attachments[0].get("sha256"):
        raise SystemExit(f"source_attachments should be normalized and hashed in manifest: {source_attachments!r}")
    assert_all_contains(
        (pipeline_bundle / "main.prompt.md").read_text(encoding="utf-8"),
        ["## Source Attachments", "Fixture README", "sha256="],
        "source attachments prompt section",
    )

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
            ]
        ).stdout
    )
    config_selection = config_pipeline_result.get("config_selection", {})
    if config_pipeline_result.get("status") != "pass" or config_selection.get("status") != "pass":
        raise SystemExit(f"prepare_goal_bundle.py should remediate and select compatible config: {config_pipeline_result!r}")
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
    report_text = (config_pipeline_bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8")
    assert_all_contains(
        report_text,
        ["preflight config check status=pass", "Goal config route availability:", "Config precedence:"],
        "config preflight report status labels",
    )
    assert_not_contains(report_text, "harness check status is pass", "config preflight report stale harness pass label")

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
    runtime_gate = non_git_pipeline_result.get("readiness", {}).get("runtime_gate", {})
    if non_git_pipeline_result.get("status") != "blocked" or runtime_gate.get("status") != "blocked":
        raise SystemExit(f"non-git pipeline should block runtime readiness explicitly: {non_git_pipeline_result!r}")
    if non_git_pipeline_result.get("launch_allowed") is not False or non_git_pipeline_result.get("readiness", {}).get("launch_allowed") is not False:
        raise SystemExit(f"non-git pipeline should expose aggregate launch_allowed=false: {non_git_pipeline_result!r}")
    if non_git_pipeline_result.get("result_kind") != "blocked_readiness_usable_bundle" or non_git_pipeline_result.get("usable_bundle") is not True:
        raise SystemExit(f"non-git pipeline should report a usable but readiness-blocked bundle: {non_git_pipeline_result!r}")
    next_commands = non_git_pipeline_result.get("next_commands", [])
    if not any("Correct runtime gate" in command for command in next_commands):
        raise SystemExit(f"non-git readiness next commands should be corrective, not a launch handoff: {next_commands!r}")
    if any(command.endswith("/goal") for command in next_commands):
        raise SystemExit(f"non-git readiness next commands must not include /goal: {next_commands!r}")
    non_git_bootloader = (non_git_pipeline_bundle / "goal-bootloader.md").read_text(encoding="utf-8")
    assert_all_contains(non_git_bootloader, ["BLOCKED READINESS", "do not launch /goal yet"], "non-git blocked bootloader warning")
    assert_not_contains(non_git_bootloader, "Use $goal-main-orchestrator", "blocked bootloader launch handoff")
    assert_not_contains(non_git_bootloader, "run_prompt_audit_phase.py", "blocked bootloader prompt-audit command")
    non_git_report = (non_git_pipeline_bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8")
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
            json.dumps(
                {
                    "alias": "codex-mini",
                    "provider": "codex",
                    "model": "gpt-5.4-mini",
                    "effort": "medium",
                    "command": "codex exec --ignore-user-config --ignore-rules",
                    "timeout_seconds": 1200,
                    "event_logs": ["events-primary.jsonl"],
                }
            ),
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
                {"seq": 1, "timestamp": "2026-01-01T00:00:00+00:00", "runtime_ref": "fixture", "event": "ready", "id": "B01"},
                {"seq": 2, "timestamp": "2026-01-01T00:00:01+00:00", "runtime_ref": "fixture", "event": "launch", "id": "B01"},
                {"seq": 3, "timestamp": "2026-01-01T00:00:02+00:00", "runtime_ref": "fixture", "event": "finish", "id": "B01", "status": "pass"},
                {"seq": 4, "timestamp": "2026-01-01T00:00:03+00:00", "runtime_ref": "fixture", "event": "close", "id": "B01"},
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
    for expected_type in ["scheduler_event", "packet_debug_event", "launcher_state", "model_attempt", "packet_telemetry", "terminal_artifact"]:
        if expected_type not in trace_types:
            raise SystemExit(f"run.trace.jsonl missing expected event type {expected_type}: {trace_types!r}")
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
    write_pre_review_gate(bundle)
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


def run_branch_status_negative_fixtures(tmp_path: Path, bundle: Path) -> None:
    assemble_branch_status(bundle)
    validate_branch(bundle)

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
        test_launcher_state_classifier()
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
        run_topology_fixtures(tmp_path)
        run_amendment_fixtures(tmp_path)

        packet_root, task_file = run_runtime_packet_fixtures(tmp_path, bundle)

        run_prompt_audit_packet_fixtures(tmp_path, bundle)

        run_lite_advice_packet_fixture(tmp_path, task_file)

        run_reviewer_packet_fixtures(tmp_path, bundle, packet_root, task_file)
        run_branch_status_negative_fixtures(tmp_path, bundle)

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
