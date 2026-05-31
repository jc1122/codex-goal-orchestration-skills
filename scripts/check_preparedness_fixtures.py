#!/usr/bin/env python3
"""Validate static preparedness fixtures for the goal orchestration skills."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import hashlib
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "fixtures" / "preparedness" / "research-worker-brief.json"


def run(command: list[str], *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode != expect:
        print(f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(1)
    return result


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def scheduler_event(seq: int, event: str, **kwargs) -> dict:
    return {
        "seq": seq,
        "timestamp": f"2026-05-29T00:00:{seq:02d}Z",
        "runtime_ref": "preparedness-fixture",
        "event": event,
        **kwargs,
    }


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


def telemetry(packet_id: str, role: str, output_name: str, *, accepted_alias: str, attempts: list[dict]) -> dict:
    called_count = sum(1 for item in attempts if item.get("called") is True)
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": role,
        "output_artifact": output_name,
        "prompt_artifact": "prompt.md",
        "prompt_chars": 1,
        "prompt_bytes": 1,
        "output_chars": 1,
        "output_bytes": 1,
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "accepted_alias": accepted_alias,
        "attempts": attempts,
        "totals": {
            "attempts_declared": len(attempts),
            "attempts_called": called_count,
            "event_log_chars": 0,
            "event_log_bytes": 0,
            "known_usage": None,
        },
    }


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


def assert_contains(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"{label} missing expected text: {needle}")


def assert_not_contains(text: str, needle: str, label: str) -> None:
    if needle in text:
        raise SystemExit(f"{label} contains forbidden text: {needle}")


def assert_shell_syntax(path: Path) -> None:
    run(["bash", "-n", path.as_posix()])


def assert_compact_runtime_launcher(packet_dir: Path, role: str, timeout_seconds: int) -> dict:
    launch = (packet_dir / "launch.sh").read_text(encoding="utf-8")
    assert_shell_syntax(packet_dir / "launch.sh")
    assert_contains(launch, "runtime_packet_runner.py", f"{role} launcher")
    if len(launch) > 800:
        raise SystemExit(f"{role} launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != role:
        raise SystemExit(f"{role} launch-config role mismatch: {config.get('role')!r}")
    if config.get("attempt_timeout_seconds") != timeout_seconds:
        raise SystemExit(f"{role} launch-config timeout mismatch: {config.get('attempt_timeout_seconds')!r}")
    return config


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
    result = run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            (tmp_path / "topology-default-rationale").as_posix(),
        ],
        expect=1,
    )
    assert_contains(result.stdout, "serial_reasons", "default rationale topology fixture")

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
    result = run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            (tmp_path / "topology-one-worker").as_posix(),
        ],
        expect=1,
    )
    assert_contains(result.stdout, "one worker", "one-worker topology fixture")

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
    result = run(
        [
            "python3",
            "skills/goal-preflight/scripts/create_goal_bundle.py",
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            (tmp_path / "topology-serial-chain").as_posix(),
        ],
        expect=1,
    )
    assert_contains(result.stdout, "serial", "serial-chain topology fixture")

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
    assert_contains(invalid.stdout, "contains placeholder text", "invalid brief lint fixture")
    assert_contains(invalid.stdout, "concrete top-level goal", "invalid brief lint fixture")
    assert_contains(invalid.stdout, "context file does not exist", "invalid brief lint fixture")
    assert_contains(invalid.stdout, "must include at least one exact verification command", "invalid brief lint fixture")


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
    assert_scheduler_fixture(
        status_validation,
        "multi-branch-refill",
        multi_branch_refill,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=True,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "stuck-branch-continued-scheduling",
        stuck_branch_continues,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=True,
        manifest_path=manifest_path,
    )
    worker_base = {
        **base,
        "scheduler_kind": "branch-worker-pool",
        "scheduler_path": "schedulers/B01.worker.scheduler.json",
        "item_ids": ["B01-W01", "B01-W02", "B01-W03"],
    }
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
    assert_scheduler_fixture(
        status_validation,
        "stuck-worker-continued-scheduling",
        stuck_worker_continues,
        expected_ids=["B01-W01", "B01-W02", "B01-W03"],
        dependencies={"B01-W01": [], "B01-W02": [], "B01-W03": []},
        capacity=2,
        expect_pass=True,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "reviewer-feedback-worker-relaunch",
        reviewer_repair_relaunch,
        expected_ids=["B01-W01"],
        dependencies={"B01-W01": []},
        capacity=1,
        expect_pass=True,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "missing-refill-event",
        missing_refill,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=False,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "under-capacity-without-reason",
        under_capacity_without_reason,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=False,
        manifest_path=manifest_path,
    )
    stale_scheduler = {
        **multi_branch_refill,
        "manifest_sha256": "sha256:" + "0" * 64,
    }
    assert_scheduler_fixture(
        status_validation,
        "stale-scheduler-manifest-hash",
        stale_scheduler,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=False,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "vague-reason-code-rejection",
        vague_reason,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=False,
        manifest_path=manifest_path,
        require_all_launched=False,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "dependency-failed-blocking",
        dependency_failed,
        expected_ids=["B01", "B02"],
        dependencies={"B01": [], "B02": ["B01"]},
        capacity=2,
        expect_pass=True,
        manifest_path=manifest_path,
        require_all_launched=False,
    )
    dependency_failed_wrong_reason = {
        **dependency_failed,
        "events": [
            *dependency_failed["events"][:5],
            scheduler_event(6, "blocked", id="B02", reason_code="dependency_pending", reason="Wrong reason for non-pass dependency."),
        ],
    }
    assert_scheduler_fixture(
        status_validation,
        "dependency-failed-wrong-reason",
        dependency_failed_wrong_reason,
        expected_ids=["B01", "B02"],
        dependencies={"B01": [], "B02": ["B01"]},
        capacity=2,
        expect_pass=False,
        manifest_path=manifest_path,
        require_all_launched=False,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "stale-active-closeout-refill",
        stale_active_closeout,
        expected_ids=["B01", "B02"],
        dependencies={"B01": [], "B02": []},
        capacity=1,
        expect_pass=True,
        manifest_path=manifest_path,
    )
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
    assert_scheduler_fixture(
        status_validation,
        "partial-subset-structured-closeout",
        partial_subset,
        expected_ids=["B01", "B02", "B03"],
        dependencies={"B01": [], "B02": [], "B03": []},
        capacity=2,
        expect_pass=True,
        manifest_path=manifest_path,
        require_all_launched=False,
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


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="goal-preparedness-fixtures-") as tmp:
        tmp_path = Path(tmp)
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
        run(["python3", "skills/goal-preflight/scripts/lint_goal_bundle.py", "--bundle-dir", bundle.as_posix(), "--no-write"])
        full_phase_manifest = run(
            ["python3", "skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py", "--markdown"]
        ).stdout
        compact_phase_manifest = run(
            ["python3", "skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py", "--compact", "--markdown"]
        ).stdout
        if len(compact_phase_manifest) >= len(full_phase_manifest):
            raise SystemExit("compact runtime phase manifest should be shorter than default markdown")
        assert_contains(compact_phase_manifest, "--manifest /abs/bundle/job.manifest.json", "compact phase manifest")
        assert_contains(compact_phase_manifest, "rg/grep", "compact phase manifest")
        brief_schema = json.loads(
            run(["python3", "skills/goal-preflight/scripts/create_goal_bundle.py", "--brief-schema-json"]).stdout
        )
        if "work_item_required" not in brief_schema or "commands" not in brief_schema:
            raise SystemExit("brief schema output is missing required agent guidance")
        lint_schema = json.loads(
            run(["python3", "skills/goal-preflight/scripts/lint_preflight_brief.py", "--brief-schema-json"]).stdout
        )
        if lint_schema != brief_schema:
            raise SystemExit("brief schema output drifted between create and lint helpers")
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
        run_scheduler_fixtures(bundle / "job.manifest.json")
        run_scheduler_tick_fixture(tmp_path)
        run_launch_ready_helper_fixtures(tmp_path)
        run_preflight_brief_lint_fixtures(tmp_path)
        run_topology_fixtures(tmp_path)
        run_amendment_fixtures(tmp_path)

        packet_root = tmp_path / "packets"
        task_file = tmp_path / "task.md"
        task_file.write_text("Static timeout launcher fixture.\n", encoding="utf-8")
        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "worker",
                "--packet-id",
                "B01-W02",
                "--branch",
                "preparedness-research-fixture-W02",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                packet_root.as_posix(),
                "--owned-file",
                "README.md",
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
                "--worker-route",
                "codex-mini",
                "--selection-reason",
                "Fixture route selected to inspect generated timeout wrapper.",
            ]
        )
        worker_launch = (packet_root / "B01-W02" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(packet_root / "B01-W02" / "launch.sh")
        assert_contains(worker_launch, "timeout --foreground", "worker launcher")
        assert_contains(worker_launch, "worker_attempt_timeout_seconds=3600", "worker launcher")
        manifest_packet_root = tmp_path / "manifest-packets"
        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "worker",
                "--packet-id",
                "B01-W01",
                "--branch",
                "B01",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                manifest_packet_root.as_posix(),
                "--task-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
            ]
        )
        manifest_worker_prompt = (manifest_packet_root / "B01-W01" / "prompt.md").read_text(encoding="utf-8")
        if not (manifest_packet_root / "B01-W01" / "packet-context.json").exists():
            raise SystemExit("worker --manifest did not create compact packet-context.json")
        assert_contains(manifest_worker_prompt, "Compact Worker Task", "worker --manifest prompt")

        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "research-worker",
                "--packet-id",
                "B01-W03",
                "--branch",
                "preparedness-research-fixture-W03",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                packet_root.as_posix(),
                "--owned-file",
                "README.md",
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        research_config = assert_compact_runtime_launcher(packet_root / "B01-W03", "research-worker", 1200)
        research_event_logs = [
            log
            for attempt in research_config.get("attempts", [])
            if isinstance(attempt, dict)
            for log in attempt.get("event_logs", [])
        ]
        if research_event_logs != ["events-primary.jsonl", "events-fallback.jsonl"]:
            raise SystemExit(f"research launch-config event log mismatch: {research_event_logs!r}")

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
        audit_launch = (tmp_path / "audit" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(tmp_path / "audit" / "launch.sh")
        assert_contains(audit_launch, "timeout --foreground", "audit launcher")
        assert_contains(audit_launch, "attempt_timeout_seconds=1200", "audit launcher")

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
            ]
        )
        lite_launch = (tmp_path / "lite" / "B01-L01" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(tmp_path / "lite" / "B01-L01" / "launch.sh")
        assert_contains(lite_launch, "timeout --foreground", "Lite launcher")
        assert_contains(lite_launch, "attempt_timeout_seconds=600", "Lite launcher")
        assert_contains(lite_launch, "provided on stdin", "Lite launcher")
        assert_not_contains(lite_launch, '-p "$(cat "$prompt_path")"', "Lite launcher")

        write_valid_research_fixture(bundle)
        write_worker_scheduler(bundle)
        write_pre_review_gate(bundle)
        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "reviewer",
                "--packet-id",
                "B01-R01",
                "--branch",
                "preparedness-research-fixture",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                packet_root.as_posix(),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--pre-review-gate",
                (bundle / "branches" / "B01.pre_review_gate.json").as_posix(),
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--context-file",
                (bundle / "branches" / "B01.pre_review_gate.json").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        reviewer_config = assert_compact_runtime_launcher(packet_root / "B01-R01", "reviewer", 1800)
        reviewer_attempts = reviewer_config.get("attempts")
        reviewer_aliases = [attempt.get("alias") for attempt in reviewer_attempts if isinstance(attempt, dict)]
        if reviewer_aliases != ["gpt-5.4-mini", "gpt-5.4"]:
            raise SystemExit(f"reviewer launch-config route mismatch: {reviewer_aliases!r}")
        if not reviewer_config.get("semantic_input_hashes"):
            raise SystemExit("reviewer launch-config omitted semantic_input_hashes")
        if not reviewer_config.get("reuse_policy"):
            raise SystemExit("reviewer launch-config omitted reuse_policy")
        assert_reviewer_route(packet_root, "B01-R01", ["gpt-5.4-mini", "gpt-5.4"])
        for packet_id, tier, expected in [
            ("B01-R02", "standard", ["gpt-5.4", "gpt-5.5"]),
            ("B01-R03", "heavy", ["gpt-5.5", "gpt-5.4"]),
        ]:
            gate_path = write_review_gate_variant(bundle, packet_id, tier=tier)
            run(
                [
                    "python3",
                    "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                    "--role",
                    "reviewer",
                    "--packet-id",
                    packet_id,
                    "--branch",
                    "preparedness-research-fixture",
                    "--worktree",
                    ROOT.as_posix(),
                    "--out-dir",
                    packet_root.as_posix(),
                    "--manifest",
                    (bundle / "job.manifest.json").as_posix(),
                    "--pre-review-gate",
                    gate_path.as_posix(),
                    "--context-file",
                    (bundle / "branches" / "B01.prompt.md").as_posix(),
                    "--task-file",
                    task_file.as_posix(),
                ]
            )
            assert_reviewer_route(packet_root, packet_id, expected)
        heavy_diff_gate = write_review_gate_variant(bundle, "B01-R04", diff_stats={"files_changed": 25, "lines_changed": 40})
        run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "reviewer",
                "--packet-id",
                "B01-R04",
                "--branch",
                "preparedness-research-fixture",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                packet_root.as_posix(),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--pre-review-gate",
                heavy_diff_gate.as_posix(),
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        assert_reviewer_route(packet_root, "B01-R04", ["gpt-5.5", "gpt-5.4"])
        failed_gate = json.loads((bundle / "branches" / "B01.pre_review_gate.json").read_text(encoding="utf-8"))
        failed_gate["status"] = "failed"
        failed_gate["checks"]["tests"] = {"status": "failed", "reason": "Negative fixture blocks reviewer launch."}
        write_json(bundle / "branches" / "B01.failed_pre_review_gate.json", failed_gate)
        failed_gate_result = run(
            [
                "python3",
                "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
                "--role",
                "reviewer",
                "--packet-id",
                "B01-R01",
                "--branch",
                "preparedness-research-fixture",
                "--worktree",
                ROOT.as_posix(),
                "--out-dir",
                (tmp_path / "blocked-reviewer").as_posix(),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--pre-review-gate",
                (bundle / "branches" / "B01.failed_pre_review_gate.json").as_posix(),
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ],
            expect=1,
        )
        assert_contains(failed_gate_result.stdout, "pre-review gate failed", "failed pre-review gate fixture")
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

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
