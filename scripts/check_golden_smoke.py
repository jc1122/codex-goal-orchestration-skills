#!/usr/bin/env python3
"""Run an offline golden smoke for the goal orchestration workflow."""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import os
import shutil
import re
from pathlib import Path

from fixture_support import (
    assert_all_contains,
    assert_any_contains,
    assert_codex_mini_worker_route,
    assert_compact_audit_launcher,
    assert_compact_lite_launcher,
    assert_compact_runtime_launcher,
    assert_contains,
    assert_lean_codex_attempts,
    assert_mixed_worker_route,
    assert_research_worker_preserves_user_config,
    assert_shell_syntax,
    attempt,
    make_scheduler_event,
    offline_gemini_env,
    read_json,
    run_command,
    run_runtime_packet,
    sha256_file,
    telemetry,
    write_json,
)


CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = CHECKOUT_ROOT / "skills"
REPO_ROOT = CHECKOUT_ROOT
JOB_ID = "golden-offline-smoke"
BRANCH_ID = "B01"
BRANCH_NAME = "golden-offline-smoke"
WORKER_PACKET = "B01-W01"
RESEARCH_PACKET = "B01-W02"
REVIEW_PACKET = "B01-R01"
LITE_PACKET = "B01-L01"
scheduler_event = make_scheduler_event("golden-offline-smoke")


def run(
    command: list[str],
    *,
    expect: int = 0,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_command(command, root=CHECKOUT_ROOT, expect=expect, cwd=cwd, env=env)


def skill_script(skill: str, script: str) -> str:
    return (SKILLS_ROOT / skill / "scripts" / script).as_posix()


def create_runtime_packet(
    *,
    role: str,
    packet_id: str,
    out_dir: Path,
    task_file: Path,
    branch: str = BRANCH_NAME,
    worktree: Path | None = None,
    owned_files: list[str] | None = None,
    context_files: list[Path] | None = None,
    manifest: Path | None = None,
    pre_review_gate: Path | None = None,
    worker_route: list[str] | None = None,
    selection_reason: str | None = None,
    extra_args: list[str] | None = None,
    expect: int = 0,
) -> subprocess.CompletedProcess[str]:
    return run_runtime_packet(
        root=CHECKOUT_ROOT,
        script=skill_script("goal-branch-orchestrator", "create_runtime_packet.py"),
        role=role,
        packet_id=packet_id,
        branch=branch,
        worktree=REPO_ROOT if worktree is None else worktree,
        out_dir=out_dir,
        task_file=task_file,
        owned_files=owned_files,
        context_files=context_files,
        manifest=manifest,
        pre_review_gate=pre_review_gate,
        worker_route=worker_route,
        selection_reason=selection_reason,
        extra_args=extra_args,
        expect=expect,
    )


def install_temp_skills(tmp_path: Path) -> Path:
    skills_root = tmp_path / "skills"
    run(["node", (CHECKOUT_ROOT / "bin" / "install-goal-skills.js").as_posix(), "--dest", skills_root.as_posix(), "--force"])
    for name in ["_goal_shared", "goal-preflight", "goal-main-orchestrator", "goal-branch-orchestrator", "goal-plan-amender"]:
        if not (skills_root / name).is_dir():
            raise SystemExit(f"temp skill install missing {name}: {skills_root}")
    return skills_root.resolve()


def create_temp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    run(["git", "init", "-b", "main", repo.as_posix()])
    (repo / "README.md").write_text("# Golden Smoke Repo\n\nFixture README for installed-skill smoke testing.\n", encoding="utf-8")
    run(["git", "-C", repo.as_posix(), "config", "user.email", "golden-smoke@example.invalid"])
    run(["git", "-C", repo.as_posix(), "config", "user.name", "Golden Smoke"])
    run(["git", "-C", repo.as_posix(), "add", "README.md"])
    run(["git", "-C", repo.as_posix(), "commit", "-m", "initial fixture"])
    return repo.resolve()


def _expand_prompt_path(value: str, variables: dict[str, str]) -> str:
    token = value.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"\"", "'"}:
        token = token[1:-1]
    changed = True
    while changed:
        previous = token
        for name, replacement in variables.items():
            token = token.replace(f"${{{name}}}", replacement)
            token = token.replace(f"${name}", replacement)
        changed = token != previous
    return token


def _collect_render_command_from_prompt(prompt_path: Path, bundle: Path) -> tuple[str, str]:
    lines = prompt_path.read_text(encoding="utf-8").splitlines()
    render_line: str | None = None
    for line in lines:
        if "render_branch_worktree_commands.py" in line and "python3" in line:
            render_line = line.strip()
            break
    if render_line is None:
        raise SystemExit("main prompt did not include the branch render command")

    assignment_re = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(\".*?\"|'.*?'|[^\s#]+)\s*$")
    variables: dict[str, str] = {}
    for line in lines:
        match = assignment_re.match(line.strip())
        if match:
            variable = match.group(2)
            if len(variable) >= 2 and variable[0] == variable[-1] and variable[0] in {"\"", "'"}:
                variable = variable[1:-1]
            variables[match.group(1)] = variable

    command_parts = shlex.split(render_line)
    try:
        manifest_idx = command_parts.index("--manifest")
        audit_idx = command_parts.index("--audit")
    except ValueError as exc:
        raise SystemExit("render command in main prompt missing --manifest or --audit") from exc

    if manifest_idx + 1 >= len(command_parts) or audit_idx + 1 >= len(command_parts):
        raise SystemExit("render command in main prompt is missing --manifest/--audit values")

    manifest_path = _expand_prompt_path(command_parts[manifest_idx + 1], variables)
    audit_path = _expand_prompt_path(command_parts[audit_idx + 1], variables)
    manifest_path = manifest_path.replace("/absolute/path/to/bundle", bundle.as_posix())
    audit_path = audit_path.replace("/absolute/path/to/bundle", bundle.as_posix())

    if not manifest_path.startswith("/"):
        raise SystemExit("main prompt render command must pass an absolute --manifest path")
    if not audit_path.startswith("/"):
        raise SystemExit("main prompt render command must pass an absolute --audit path")
    return manifest_path, audit_path


def assert_prompt_render_command_uses_absolute_paths(bundle: Path) -> None:
    manifest_path, audit_path = _collect_render_command_from_prompt(bundle / "main.prompt.md", bundle)
    render_script = skill_script("goal-main-orchestrator", "render_branch_worktree_commands.py")

    ready = run(
        [
            "python3",
            render_script,
            "--manifest",
            manifest_path,
            "--repo-root",
            REPO_ROOT.as_posix(),
            "--audit",
            audit_path,
            "--list-ready",
            "--limit",
            "4",
        ],
        cwd=Path("/tmp"),
    )
    if ready.stdout.strip().splitlines() != ["B01"]:
        raise SystemExit(f"main prompt render command did not resolve B01 as branch-ready: {ready.stdout!r}")

    def _assert_bad_relative_render_command(label: str, manifest_value: str, audit_value: str, expected_diag: str) -> None:
        bad = subprocess.run(
            [
                "python3",
                render_script,
                "--manifest",
                manifest_value,
                "--repo-root",
                REPO_ROOT.as_posix(),
                "--audit",
                audit_value,
                "--list-ready",
                "--limit",
                "4",
            ],
            cwd=bundle,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if bad.returncode == 0:
            raise SystemExit(f"known-bad {label} render command unexpectedly succeeded")
        message = bad.stdout.strip()
        if expected_diag not in message:
            raise SystemExit(
                f"known-bad {label} render command produced unexpected diagnostic: {message!r}"
            )

    _assert_bad_relative_render_command(
        "manifest-relative",
        "job.manifest.json",
        audit_path,
        "--manifest must be an absolute path",
    )
    _assert_bad_relative_render_command(
        "audit-relative",
        manifest_path,
        "audit/prompt-audit.json",
        "--audit must be an absolute path",
    )


def write_scheduler_ledgers(bundle: Path) -> None:
    run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "scheduler_tick.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--scope",
            "worker",
            "--branch-id",
            BRANCH_ID,
            "--runtime-ref",
            "golden-offline-smoke",
            "--timestamp",
            "2026-05-29T00:00:01Z",
            "--init",
            "--record-ready",
            "--launch",
            WORKER_PACKET,
            "--launch",
            RESEARCH_PACKET,
            "--finish",
            WORKER_PACKET,
            "--finish",
            RESEARCH_PACKET,
            "--status",
            "pass",
            "--close",
            WORKER_PACKET,
            "--close",
            RESEARCH_PACKET,
            "--validate-final",
        ]
    )
    run(
        [
            "python3",
            skill_script("goal-main-orchestrator", "scheduler_tick.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--scope",
            "main",
            "--runtime-ref",
            "golden-offline-smoke",
            "--timestamp",
            "2026-05-29T00:00:01Z",
            "--init",
            "--record-ready",
            "--launch",
            BRANCH_ID,
            "--finish",
            BRANCH_ID,
            "--status",
            "pass",
            "--close",
            BRANCH_ID,
            "--validate-final",
        ]
    )


def create_amendment_decision(
    bundle: Path,
    amendment_id: str,
    *,
    decision: str,
    reason_code: str,
    reason: str,
    terminal: list[str] | None = None,
    replace: bool = False,
) -> dict:
    command = [
        "python3",
        skill_script("goal-plan-amender", "create_amendment_decision.py"),
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
    for branch_id in terminal or [BRANCH_ID]:
        command.extend(["--terminal-branch", branch_id])
    if replace:
        command.append("--replace")
    run(command)
    return {
        "amendment_id": amendment_id,
        "decision": decision,
        "decision_path": f"amendments/{amendment_id}.decision.json",
        "packet_validation_path": f"amendments/{amendment_id}.packet/packet.validation.json" if decision == "launch" else None,
    }


def recommend_amendment_decision(bundle: Path, amendment_id: str) -> dict:
    result = run(
        [
            "python3",
            skill_script("goal-plan-amender", "recommend_amendment_decision.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            amendment_id,
            "--write-decision",
            "--json",
        ]
    )
    recommendation = json.loads(result.stdout)
    return {
        "amendment_id": amendment_id,
        "decision": recommendation["decision"],
        "decision_path": f"amendments/{amendment_id}.decision.json",
        "packet_validation_path": f"amendments/{amendment_id}.packet/packet.validation.json"
        if recommendation["decision"] == "launch"
        else None,
    }


def golden_brief() -> dict:
    return {
        "job_id": JOB_ID,
        "base_ref": "main",
        "goal": "Validate the complete golden offline orchestration path with deterministic synthetic artifacts.",
        "source_summary": "Golden smoke uses a temporary repository, README context, static packet outputs, and generated telemetry artifacts.",
        "required_evidence": [
            "audit, worker, research-worker, reviewer, Lite, scheduler, and telemetry artifacts validate offline"
        ],
        "final_dod": [
            "golden smoke bundle passes deterministic lint and status validators without launching live model CLIs"
        ],
        "max_active_branch_agents": 1,
        "serial_reasons": ["Single-branch golden smoke keeps the offline gate small."],
        "artifact_policy": "Preserve partial, blocked, failed, and pass smoke artifacts for deterministic fixture inspection.",
        "cleanup_policy": "Preserve unresolved, negative, partial, blocked, and failed runtime evidence until the fixture completes.",
        "branches": [
            {
                "id": BRANCH_ID,
                "title": "Golden Offline Smoke",
                "objective": "Validate a complete synthetic pass path without launching model CLIs.",
                "branch_name": BRANCH_NAME,
                "worktree_path": ".worktrees/golden-offline-smoke",
                "max_active_worker_packets": 4,
                "work_items": [
                    {
                        "id": "W01",
                        "worker_type": "worker",
                        "objective": "Static normal-worker artifact for the golden offline smoke.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "verification": ["git diff --check main...HEAD"],
                        "route_class": "normal-code",
                        "route_class_reason": "Golden smoke fixture intentionally exercises a normal-code worker route even though the owned path is documentation-like.",
                        "dod": ["normal worker artifact validates with route and timeout telemetry"],
                    },
                    {
                        "id": "W02",
                        "worker_type": "research-worker",
                        "objective": "Static research-worker artifact for the golden offline smoke.",
                        "owned_paths": ["research/golden-smoke.md"],
                        "context_files": ["README.md"],
                        "verification": ["git diff --check main...HEAD"],
                        "dod": ["research worker artifact validates with read-only evidence and timeout telemetry"],
                    },
                ],
            }
        ],
    }


def write_lite_advice(packet_dir: Path) -> dict:
    inputs = read_json(packet_dir / "input-files.json")
    source_files = inputs.get("source_files")
    if not isinstance(source_files, list):
        raise SystemExit("golden Lite input-files.json did not contain source_files")
    gemini_path = str(inputs.get("gemini_path", ""))
    command = (
        f"{gemini_path if gemini_path else 'gemini'} "
        "--model gemini-3.1-flash-lite-preview --approval-mode plan --skip-trust --output-format text"
    )
    advice = {
        "packet_id": LITE_PACKET,
        "role": "lite_advisor",
        "purpose": "branch-packet-planning",
        "avoids_action": inputs.get("avoids_action"),
        "expected_savings_reason": inputs.get("expected_savings_reason"),
        "status": "blocked",
        "source_files": source_files,
        "recommended_reads": [],
        "risk_flags": [],
        "advice": {},
        "summary": "Golden offline smoke records a valid Lite envelope without using live Lite output.",
        "blockers": ["Golden smoke is offline and does not launch the Lite model."],
        "commands_run": [command],
    }
    write_json(packet_dir / "advice.json", advice)
    write_json(
        packet_dir / "telemetry.json",
        telemetry(
            LITE_PACKET,
            "lite_advisor",
            "advice.json",
            accepted_alias=None,
            attempts=[
                attempt(
                    alias="gemini-lite",
                    provider="gemini",
                    model="gemini-3.1-flash-lite-preview",
                    command=command,
                    timeout_seconds=600,
                    called=False,
                    accepted=False,
                )
            ],
        ),
    )
    validate_command = [
        "python3",
        skill_script("goal-branch-orchestrator", "validate_lite_advice.py"),
        "--advice",
        (packet_dir / "advice.json").as_posix(),
        "--inputs",
        (packet_dir / "input-files.json").as_posix(),
    ]
    run(validate_command)
    return {
        "packet_id": LITE_PACKET,
        "purpose": "branch-packet-planning",
        "avoids_action": inputs.get("avoids_action"),
        "expected_savings_reason": inputs.get("expected_savings_reason"),
        "status": "blocked",
        "disposition": "ignored",
        "advice_path": (packet_dir / "advice.json").as_posix(),
        "inputs_path": (packet_dir / "input-files.json").as_posix(),
        "source_files": [
            {
                "path": item.get("path"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
                "reason": item.get("reason"),
            }
            for item in source_files
            if isinstance(item, dict)
        ],
        "validation_command": shlex.join(validate_command),
        "validation_status": "pass",
        "validation_defects": [],
        "reason": "Golden offline smoke verifies the Lite artifact envelope but ignores blocked Lite advice.",
    }


def write_audit(bundle: Path) -> None:
    audit_dir = bundle / "audit"
    audit = {
        "manifest": (bundle / "job.manifest.json").as_posix(),
        "repo_root": REPO_ROOT.as_posix(),
        "status": "pass",
        "can_start": True,
        "checked_files": ["job.manifest.json", "main.prompt.md", "branches/B01.prompt.md"],
        "commands_run": [
            "python3 <installed-goal-preflight>/scripts/lint_goal_bundle.py --bundle-dir <bundle> --no-write"
        ],
        "missing_dod_items": [],
        "defects": [],
        "summary": "Synthetic prompt audit pass for golden offline smoke.",
    }
    write_json(audit_dir / "prompt-audit.json", audit)
    write_json(
        audit_dir / "telemetry.json",
        telemetry(
            "prompt-audit",
            "prompt-auditor",
            "prompt-audit.json",
            accepted_alias="gpt-5.5",
            attempts=[
                attempt(
                    alias="gpt-5.5",
                    provider="codex",
                    model="gpt-5.5",
                    command="codex exec --ephemeral -m gpt-5.5 -s read-only",
                    timeout_seconds=1200,
                    called=True,
                    accepted=True,
                ),
                attempt(
                    alias="gpt-5.4",
                    provider="codex",
                    model="gpt-5.4",
                    command="codex exec --ephemeral -m gpt-5.4 -s read-only",
                    timeout_seconds=1200,
                    called=False,
                    accepted=False,
                ),
            ],
        ),
    )


def worker_status(bundle: Path) -> dict:
    manifest_hash = sha256_file(bundle / "job.manifest.json")
    route_id = f"{WORKER_PACKET}:normal-code:codex-mini"
    return {
        "packet_id": WORKER_PACKET,
        "role": "worker",
        "status": "pass",
        "branch_id": BRANCH_ID,
        "work_item_id": "W01",
        "manifest_hash": manifest_hash,
        "manifest_epoch": "current",
        "worktree_path": REPO_ROOT.as_posix(),
        "route_id": route_id,
        "evidence_summary": "Synthetic normal-worker pass evidence for golden offline smoke.",
        "branch": BRANCH_NAME,
        "worktree": REPO_ROOT.as_posix(),
        "route_class": "normal-code",
        "selected_ladder": ["codex-mini"],
        "selection_reason": "Golden smoke uses the cheapest deterministic route alias.",
        "changed_files": [],
        "commands_run": ["git diff --check main...HEAD"],
        "tests": ["bash -n generated worker launchers"],
        "blockers": [],
        "handoff": "Synthetic normal-worker pass artifact for golden offline smoke.",
    }


def research_status() -> dict:
    return {
        "packet_id": RESEARCH_PACKET,
        "role": "research-worker",
        "status": "pass",
        "branch": BRANCH_NAME,
        "worktree": REPO_ROOT.as_posix(),
        "search_queries": ["golden offline smoke research-worker contract"],
        "source_urls": ["https://example.com/golden-smoke"],
        "tools_used": ["local-shell", "local-git", "local-sed", "codex-native-search"],
        "local_files_read": ["README.md"],
        "commands_run": [
            "pwd",
            "git status --short --branch",
            "sed -n '1,80p' README.md",
            "curl -I https://example.com/golden-smoke",
        ],
        "findings": ["Synthetic research finding with URL and local-file evidence."],
        "blockers": [],
        "handoff": "Synthetic research-worker pass artifact for golden offline smoke.",
    }


def write_worker_artifacts(bundle: Path) -> tuple[dict, dict]:
    worker = worker_status(bundle)
    worker_dir = bundle / "workers" / WORKER_PACKET
    write_json(worker_dir / "status.json", worker)
    write_json(
        worker_dir / "route.json",
        {
            "schema_version": 1,
            "packet_id": WORKER_PACKET,
            "role": "worker",
            "branch_id": BRANCH_ID,
            "branch": BRANCH_NAME,
            "route_class": worker["route_class"],
            "selected_ladder": worker["selected_ladder"],
            "selection_reason": worker["selection_reason"],
            "policy_router": "golden-smoke",
            "policy_version": "goal-route-policy-v2",
            "route_policy_version": "goal-route-policy-v2",
            "default_ladder": ["codex-mini"],
            "allowed_aliases": ["codex-mini"],
            "route_catalog_sha256": None,
            "route_catalog_source": None,
            "model_catalog": {},
        },
    )
    write_json(
        worker_dir / "telemetry.json",
        telemetry(
            WORKER_PACKET,
            "worker",
            "status.json",
            accepted_alias="codex-mini",
            attempts=[
                attempt(
                    alias="codex-mini",
                    provider="codex",
                    model="gpt-5.4-mini",
                    command="codex exec --ephemeral --ignore-user-config --ignore-rules -m gpt-5.4-mini -s workspace-write",
                    timeout_seconds=3600,
                    called=True,
                    accepted=True,
                )
            ],
        ),
    )
    write_json(
        worker_dir / "launcher-state.json",
        {
            "schema_version": 1,
            "packet_id": WORKER_PACKET,
            "role": "worker",
            "state_machine": "active -> timeout|fail-clean|fail-dirty|pass|blocked",
            "terminal_state": "pass",
            "events": [
                {
                    "seq": 1,
                    "state": "pass",
                    "attempt_index": 0,
                    "alias": "codex-mini",
                    "provider": "codex",
                    "model": "gpt-5.4-mini",
                    "returncode": 0,
                    "dirty": False,
                    "output_nonempty": True,
                    "rendered_command": "codex exec --ephemeral --ignore-user-config --ignore-rules -m gpt-5.4-mini -s workspace-write",
                    "executed_command": "codex exec --ephemeral --ignore-user-config --ignore-rules -m gpt-5.4-mini -s workspace-write",
                }
            ],
        },
    )
    write_json(
        worker_dir / "packet.summary.json",
        {
            "schema_version": 1,
            "packet_id": WORKER_PACKET,
            "role": "worker",
            "route_class": worker["route_class"],
            "selected_ladder": worker["selected_ladder"],
            "selection_reason": worker["selection_reason"],
            "worktree": REPO_ROOT.as_posix(),
            "output_path": "status.json",
            "output_exists": True,
            "output_status": "pass",
            "changed_files": [],
            "blockers": [],
            "telemetry_path": "telemetry.json",
            "telemetry_exists": True,
            "launcher_state_path": "launcher-state.json",
            "launcher_state_exists": True,
            "terminal_state": "pass",
            "attempts": [{"attempt_index": 0, "alias": "codex-mini", "state": "pass", "failure_class": "none"}],
            "next_action": "validate_and_collect",
        },
    )

    research = research_status()
    research_dir = bundle / "research" / RESEARCH_PACKET
    write_json(research_dir / "research.json", research)
    write_json(
        research_dir / "telemetry.json",
        telemetry(
            RESEARCH_PACKET,
            "research-worker",
            "research.json",
            accepted_alias="codex-research",
            attempts=[
                attempt(
                    alias="codex-research",
                    provider="codex",
                    model="gpt-5.4",
                    command="codex --search exec --ephemeral -m gpt-5.4 -s read-only",
                    timeout_seconds=1200,
                    called=True,
                    accepted=True,
                ),
                attempt(
                    alias="codex-research-mini",
                    provider="codex",
                    model="gpt-5.4-mini",
                    command="codex --search exec --ephemeral -m gpt-5.4-mini -s read-only",
                    timeout_seconds=1200,
                    called=False,
                    accepted=False,
                ),
            ],
        ),
    )
    write_json(
        research_dir / "launcher-state.json",
        {
            "schema_version": 1,
            "packet_id": RESEARCH_PACKET,
            "role": "research-worker",
            "state_machine": "active -> timeout|fail-clean|fail-dirty|pass|blocked",
            "terminal_state": "pass",
            "events": [
                {
                    "seq": 1,
                    "state": "pass",
                    "attempt_index": 0,
                    "alias": "codex-research",
                    "provider": "codex",
                    "model": "gpt-5.4",
                    "returncode": 0,
                    "dirty": False,
                    "output_nonempty": True,
                }
            ],
        },
    )
    write_json(
        research_dir / "packet.summary.json",
        {
            "schema_version": 1,
            "packet_id": RESEARCH_PACKET,
            "role": "research-worker",
            "selected_ladder": ["codex-research", "codex-research-mini"],
            "selection_reason": "Golden smoke research-worker route.",
            "worktree": REPO_ROOT.as_posix(),
            "output_path": "research.json",
            "output_exists": True,
            "output_status": "pass",
            "changed_files": [],
            "blockers": [],
            "telemetry_path": "telemetry.json",
            "telemetry_exists": True,
            "launcher_state_path": "launcher-state.json",
            "launcher_state_exists": True,
            "terminal_state": "pass",
            "attempts": [{"attempt_index": 0, "alias": "codex-research", "state": "pass", "failure_class": "none"}],
            "next_action": "validate_and_collect",
        },
    )
    return worker, research


def write_pre_review_gate(bundle: Path) -> dict[str, str]:
    run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "create_pre_review_gate.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--review-packet-id",
            REVIEW_PACKET,
            "--test-evidence",
            "bash -n generated launchers",
            "--test-evidence",
            "installed validators passed",
            "--dod-item",
            "normal worker artifact validates",
            "--dod-item",
            "research-worker artifact validates",
            "--dod-item",
            "scheduler ledger validates",
        ]
    )
    gate = read_json(bundle / "branches" / "B01.pre_review_gate.json")
    semantic_hashes = gate.get("semantic_input_hashes")
    if not isinstance(semantic_hashes, dict):
        raise SystemExit("pre-review gate did not write semantic_input_hashes")
    return {key: value for key, value in semantic_hashes.items() if isinstance(key, str) and isinstance(value, str)}


def write_review(bundle: Path, input_hashes: dict[str, str]) -> None:
    route = read_json(bundle / "reviewers" / REVIEW_PACKET / "route.json")
    selected_ladder = route.get("selected_ladder") if isinstance(route.get("selected_ladder"), list) else ["gpt-5.4", "gpt-5.5"]
    model_by_alias = {
        "gpt-5.4-mini": "gpt-5.4-mini",
        "gpt-5.4": "gpt-5.4",
        "gpt-5.5": "gpt-5.5",
    }
    reviewer_attempts = [
        attempt(
            alias=str(alias),
            provider="codex",
            model=model_by_alias[str(alias)],
            command=f"codex exec --ephemeral --ignore-user-config --ignore-rules -m {model_by_alias[str(alias)]} -s read-only",
            timeout_seconds=1800,
            called=index == 0,
            accepted=index == 0,
        )
        for index, alias in enumerate(selected_ladder)
        if str(alias) in model_by_alias
    ]
    accepted_alias = reviewer_attempts[0]["alias"] if reviewer_attempts else None
    review = {
        "packet_id": REVIEW_PACKET,
        "role": "reviewer",
        "verdict": "mergeable",
        "findings": ["Golden offline smoke review artifact is synthetically mergeable."],
        "commands_run": ["git diff --check main...HEAD"],
        "verification_gaps": [],
        "residual_risks": ["Synthetic smoke does not exercise live model CLIs."],
        "semantic_input_hashes": input_hashes,
        "reuse_policy": {
            "mode": "new",
            "accepted": False,
            "semantic_hashes_match": False,
            "source_review_path": None,
            "source_telemetry_path": None,
        },
        "summary": "Synthetic reviewer pass for golden offline smoke.",
    }
    write_json(bundle / "branches" / "B01.review.json", review)
    write_json(bundle / "reviewers" / REVIEW_PACKET / "review.json", review)
    write_json(
        bundle / "reviewers" / REVIEW_PACKET / "telemetry.json",
        telemetry(
            REVIEW_PACKET,
            "reviewer",
            "review.json",
            accepted_alias=accepted_alias,
            attempts=reviewer_attempts,
        ),
    )


def write_pre_review_branch_status(bundle: Path, worker: dict, research: dict, lite_record: dict) -> None:
    worker_rollup = {
        **worker,
        "status_path": (bundle / "workers" / WORKER_PACKET / "status.json").as_posix(),
    }
    research_rollup = {
        **research,
        "status_path": (bundle / "research" / RESEARCH_PACKET / "research.json").as_posix(),
    }
    write_json(
        bundle / "branches" / "B01.status.json",
        {
            "branch_id": BRANCH_ID,
            "status": "partial",
            "schema_status": "pass",
            "runtime_status": "partial",
            "dod_status": "incomplete",
            "resume_action": "reuse_terminal_status",
            "branch": BRANCH_NAME,
            "worktree": REPO_ROOT.as_posix(),
            "worker_statuses": [worker_rollup, research_rollup],
            "worker_parallelism": {
                "scheduler_path": "schedulers/B01.worker.scheduler.json",
                "max_worker_packets_per_branch": 4,
                "max_active_worker_packets": 4,
                "max_observed_active_worker_packets": 2,
                "max_observed_active": 2,
                "concurrent_launch_default": True,
                "rolling_refill_default": True,
                "scheduling_mode": "rolling",
                "launched_ids": [WORKER_PACKET, RESEARCH_PACKET],
                "finished_ids": [WORKER_PACKET, RESEARCH_PACKET],
                "active_ids": [],
                "blocked_ids": [],
                "deferred_ids": [],
                "serialized_workers": [],
                "deferred_workers": [],
                "serial_reasons": [],
                "refill_events": [],
            },
            "lite_advice": [lite_record],
            "review_status": "missing",
            "review_waiver_path": "branches/B01.review-waiver.json",
            "changed_files": [],
            "commands_run": ["git diff --check main...HEAD"],
            "tests": ["bash -n generated launchers", "installed validators passed"],
            "dod_checklist": [
                "normal worker artifact validates",
                "research-worker artifact validates",
                "Lite artifact envelope validates",
            ],
            "blockers": ["Reviewer has not run yet; pre-review status is intentionally partial."],
            "handoff": "Golden offline smoke pre-review branch status.",
        },
    )
    write_json(
        bundle / "branches" / "B01.review-waiver.json",
        {
            "schema_version": 1,
            "kind": "review-waiver",
            "branch_id": BRANCH_ID,
            "branch_status": "partial",
            "review_status": "missing",
            "review_path": "branches/B01.review.json",
            "reviewer_launch_skipped": True,
            "reason_code": "branch_non_pass_terminal_blocker",
            "reason": "Golden smoke pre-review status is partial until synthetic reviewer artifact is written.",
            "validated_by": "check_golden_smoke.py",
            "blockers": ["Reviewer has not run yet; pre-review status is intentionally partial."],
            "branch_status_path": "branches/B01.status.json",
        },
    )


def write_branch_and_main_status(bundle: Path) -> None:
    run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "assemble_branch_status.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--allow-pass",
            "--replace",
            "--test-evidence",
            "bash -n generated launchers",
            "--test-evidence",
            "installed validators passed",
            "--dod-item",
            "normal worker artifact validates",
            "--dod-item",
            "research-worker artifact validates",
            "--dod-item",
            "reviewer artifact validates",
            "--dod-item",
            "Lite artifact envelope validates",
            "--handoff",
            "Golden offline smoke branch status.",
        ]
    )
    recommend_amendment_decision(bundle, "A000")
    run(
        [
            "python3",
            skill_script("goal-main-orchestrator", "assemble_main_status.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--out",
            (bundle / "main.status.json").as_posix(),
            "--replace",
            "--summary",
            "Golden offline smoke main status.",
        ]
    )


def validate_branch(bundle: Path, *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    return run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "validate_branch_status.py"),
            "--manifest",
            (bundle / "job.manifest.json").as_posix(),
            "--status",
            (bundle / "branches" / "B01.status.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--branch",
            BRANCH_NAME,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--json",
        ],
        expect=expect,
    )


def rewrite_copied_branch_paths(bundle: Path) -> None:
    status_path = bundle / "branches" / "B01.status.json"
    if not status_path.exists():
        return
    status = read_json(status_path)
    for item in status.get("worker_statuses", []):
        if not isinstance(item, dict) or not isinstance(item.get("packet_id"), str):
            continue
        packet_id = item["packet_id"]
        if item.get("role") == "research-worker":
            item["status_path"] = (bundle / "research" / packet_id / "research.json").as_posix()
        else:
            item["status_path"] = (bundle / "workers" / packet_id / "status.json").as_posix()
    for item in status.get("lite_advice", []):
        if not isinstance(item, dict) or item.get("packet_id") != LITE_PACKET:
            continue
        advice = bundle / "lite" / LITE_PACKET / "advice.json"
        inputs = bundle / "lite" / LITE_PACKET / "input-files.json"
        item["advice_path"] = advice.as_posix()
        item["inputs_path"] = inputs.as_posix()
        item["validation_command"] = shlex.join(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "validate_lite_advice.py"),
                "--advice",
                advice.as_posix(),
                "--inputs",
                inputs.as_posix(),
            ]
        )
    write_json(status_path, status)


def make_reuse_bundle(source_bundle: Path, target_bundle: Path, input_hashes: dict[str, str]) -> None:
    shutil.copytree(source_bundle, target_bundle)
    rewrite_copied_branch_paths(target_bundle)
    source_review_dir = target_bundle / "reviewers" / "B01-R00"
    source_review_dir.mkdir(parents=True, exist_ok=True)
    source_review = read_json(target_bundle / "branches" / "B01.review.json")
    source_review["packet_id"] = "B01-R00"
    write_json(source_review_dir / "review.json", source_review)
    source_telemetry = read_json(target_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json")
    source_telemetry["packet_id"] = "B01-R00"
    write_json(source_review_dir / "telemetry.json", source_telemetry)

    review = read_json(target_bundle / "branches" / "B01.review.json")
    review["semantic_input_hashes"] = input_hashes
    review["reuse_policy"] = {
        "mode": "reuse",
        "accepted": True,
        "semantic_hashes_match": True,
        "source_review_path": "reviewers/B01-R00/review.json",
        "source_telemetry_path": "reviewers/B01-R00/telemetry.json",
    }
    write_json(target_bundle / "branches" / "B01.review.json", review)
    write_json(target_bundle / "reviewers" / REVIEW_PACKET / "review.json", review)
    (target_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json").unlink()


def make_partial_branch_bundle(source_bundle: Path, target_bundle: Path, worker: dict) -> None:
    shutil.copytree(source_bundle, target_bundle)
    rewrite_copied_branch_paths(target_bundle)
    manifest_sha = sha256_file(target_bundle / "job.manifest.json")
    write_json(
        target_bundle / "schedulers" / "B01.worker.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "branch-worker-pool",
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "manifest_sha256": manifest_sha,
            "capacity": 4,
            "item_ids": [WORKER_PACKET, RESEARCH_PACKET],
            "events": [
                scheduler_event(1, "ready", id=WORKER_PACKET),
                scheduler_event(2, "ready", id=RESEARCH_PACKET),
                scheduler_event(3, "launch", id=WORKER_PACKET),
                scheduler_event(4, "blocked", id=RESEARCH_PACKET, reason_code="operator_requested", reason="Partial fixture leaves research packet unlaunched with structured evidence."),
                scheduler_event(5, "finish", id=WORKER_PACKET, status="pass"),
                scheduler_event(6, "close", id=WORKER_PACKET),
            ],
        },
    )
    worker_rollup = {
        **worker,
        "status_path": (target_bundle / "workers" / WORKER_PACKET / "status.json").as_posix(),
    }
    branch_status = read_json(target_bundle / "branches" / "B01.status.json")
    branch_status.update(
        {
            "status": "partial",
            "runtime_status": "partial",
            "dod_status": "incomplete",
            "worker_statuses": [worker_rollup],
            "worker_parallelism": {
                **branch_status["worker_parallelism"],
                "launched_ids": [WORKER_PACKET],
                "finished_ids": [WORKER_PACKET],
                "blocked_ids": [RESEARCH_PACKET],
                "max_observed_active_worker_packets": 1,
                "max_observed_active": 1,
            },
            "review_status": "missing",
            "review_waiver_path": "branches/B01.review-waiver.json",
            "blockers": ["Partial fixture intentionally leaves B01-W02 unlaunched with scheduler evidence."],
            "handoff": "Partial branch fixture.",
        }
    )
    write_json(target_bundle / "branches" / "B01.status.json", branch_status)
    write_json(
        target_bundle / "branches" / "B01.review-waiver.json",
        {
            "schema_version": 1,
            "kind": "review-waiver",
            "branch_id": BRANCH_ID,
            "branch_status": "partial",
            "review_status": "missing",
            "review_path": "branches/B01.review.json",
            "reviewer_launch_skipped": True,
            "reason_code": "branch_non_pass_terminal_blocker",
            "reason": "Partial fixture intentionally leaves research packet unlaunched.",
            "validated_by": "check_golden_smoke.py",
            "blockers": branch_status["blockers"],
            "branch_status_path": "branches/B01.status.json",
        },
    )
    (target_bundle / "branches" / "B01.review.json").unlink()


def make_refill_assembly_bundle(source_bundle: Path, target_bundle: Path) -> None:
    shutil.copytree(source_bundle, target_bundle)
    rewrite_copied_branch_paths(target_bundle)
    manifest = read_json(target_bundle / "job.manifest.json")
    branch = manifest["branches"][0]
    branch["max_active_worker_packets"] = 1
    branch["worker_parallelism"]["max_active_worker_packets"] = 1
    branch["worker_parallelism"]["serial_reasons"] = ["Golden smoke serializes workers to exercise refill status assembly."]
    write_json(target_bundle / "job.manifest.json", manifest)
    manifest_sha = sha256_file(target_bundle / "job.manifest.json")
    worker_artifact = read_json(target_bundle / "workers" / WORKER_PACKET / "status.json")
    worker_artifact["manifest_hash"] = manifest_sha
    write_json(target_bundle / "workers" / WORKER_PACKET / "status.json", worker_artifact)
    write_json(
        target_bundle / "schedulers" / "B01.worker.scheduler.json",
        {
            "schema_version": 2,
            "scheduler_kind": "branch-worker-pool",
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "manifest_sha256": manifest_sha,
            "capacity": 1,
            "item_ids": [WORKER_PACKET, RESEARCH_PACKET],
            "events": [
                scheduler_event(1, "ready", id=WORKER_PACKET),
                scheduler_event(2, "ready", id=RESEARCH_PACKET),
                scheduler_event(3, "launch", id=WORKER_PACKET),
                scheduler_event(4, "finish", id=WORKER_PACKET, status="pass"),
                scheduler_event(5, "close", id=WORKER_PACKET),
                scheduler_event(6, "refill", eligible_ids=[RESEARCH_PACKET]),
                scheduler_event(7, "launch", id=RESEARCH_PACKET),
                scheduler_event(8, "finish", id=RESEARCH_PACKET, status="pass"),
                scheduler_event(9, "close", id=RESEARCH_PACKET),
            ],
        },
    )
    (target_bundle / "branches" / "B01.review.json").unlink()
    reviewer_output = target_bundle / "reviewers" / REVIEW_PACKET / "review.json"
    if reviewer_output.exists():
        reviewer_output.unlink()
    run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "assemble_branch_status.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--replace",
            "--test-evidence",
            "refill assembly fixture validates",
            "--dod-item",
            "assembler records scheduler refill events",
            "--handoff",
            "Refill assembly fixture.",
        ]
    )
    status = read_json(target_bundle / "branches" / "B01.status.json")
    refill_events = status.get("worker_parallelism", {}).get("refill_events")
    if refill_events != ["seq:6:B01-W02"]:
        raise SystemExit(f"assembler did not copy scheduler refill events, got {refill_events!r}")


def golden_amendment_branch() -> dict:
    return {
        "id": "B02",
        "title": "Golden Amendment Follow-up",
        "objective": "Validate accepted amendment scheduling from the amended manifest.",
        "scope": "Future unstarted smoke work only.",
        "branch_name": "golden-offline-smoke-b02",
        "worktree_path": ".worktrees/golden-offline-smoke-b02",
        "depends_on": ["B01"],
        "max_active_worker_packets": 1,
        "worker_serial_reasons": ["Single amended smoke packet."],
        "work_items": [
            {
                "id": "W01",
                "objective": "Static amended worker item.",
                "owned_paths": ["docs/golden-amendment.md"],
                "context_files": ["README.md"],
                "verification": ["git diff --check main...HEAD"],
                "dod": ["amended smoke item is schedulable"],
            }
        ],
        "tests": ["git diff --check main...HEAD"],
        "dod": ["amended branch prompt is generated and linted"],
    }


def run_amendment_smoke(source_bundle: Path, target_bundle: Path) -> None:
    shutil.copytree(source_bundle, target_bundle)
    rewrite_copied_branch_paths(target_bundle)
    audit = read_json(target_bundle / "audit" / "prompt-audit.json")
    audit["manifest"] = (target_bundle / "job.manifest.json").as_posix()
    audit["repo_root"] = REPO_ROOT.as_posix()
    write_json(target_bundle / "audit" / "prompt-audit.json", audit)
    create_amendment_decision(
        target_bundle,
        "A000",
        decision="skip",
        reason_code="no_adaptation_needed",
        reason="Copied golden smoke terminal pass checkpoint is current to this bundle.",
        replace=True,
    )
    a001_decision = create_amendment_decision(
        target_bundle,
        "A001",
        decision="launch",
        reason_code="remaining_work_dod_gap",
        reason="Golden smoke adds one future branch after validated terminal evidence.",
    )

    run(
        [
            "python3",
            skill_script("goal-plan-amender", "create_adaptation_packet.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (target_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            REPO_ROOT.as_posix(),
            "--amendment-id",
            "A001",
            "--terminal-branch",
            BRANCH_ID,
        ]
    )
    packet_dir = target_bundle / "amendments" / "A001.packet"
    assert_shell_syntax(packet_dir / "launch.sh")
    route = read_json(packet_dir / "route.json")
    if route.get("selected_ladder") != ["gpt-5.4", "gpt-5.4-mini"]:
        raise SystemExit("golden amender packet did not record the default route")
    proposal = {
        "schema_version": 1,
        "amendment_id": "A001",
        "job_id": JOB_ID,
        "rationale": "Golden smoke adds one future branch after validated terminal evidence.",
        "operations": [{"op": "add_branch", "branch": golden_amendment_branch()}],
    }
    proposal_path = target_bundle / "amendments" / "A001.proposal.json"
    validation_path = target_bundle / "amendments" / "A001.validation.json"
    write_json(proposal_path, proposal)
    write_json(
        packet_dir / "telemetry.json",
        telemetry(
            "A001",
            "plan_amender",
            "../A001.proposal.json",
            accepted_alias="gpt-5.4",
            attempts=[
                attempt(
                    alias="gpt-5.4",
                    provider="codex",
                    model="gpt-5.4",
                    command="codex exec --ephemeral -m gpt-5.4 -s read-only",
                    timeout_seconds=1200,
                    called=True,
                    accepted=True,
                ),
                attempt(
                    alias="gpt-5.4-mini",
                    provider="codex",
                    model="gpt-5.4-mini",
                    command="codex exec --ephemeral -m gpt-5.4-mini -s read-only",
                    timeout_seconds=1200,
                    called=False,
                    accepted=False,
                ),
            ],
        ),
    )
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "validate_amender_packet.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A001",
            "--json",
        ]
    )
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "validate_manifest_amendment.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            proposal_path.as_posix(),
            "--output",
            validation_path.as_posix(),
            "--terminal-branch",
            BRANCH_ID,
        ]
    )
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "apply_manifest_amendment.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            proposal_path.as_posix(),
            "--validation",
            validation_path.as_posix(),
        ]
    )
    for rel_path in [
        "amendments/A001.accepted.json",
        "amendments/A001.lineage.json",
        "amendments/A001.job.manifest.before.json",
        "branches/B02.prompt.md",
    ]:
        if not (target_bundle / rel_path).exists():
            raise SystemExit(f"amendment smoke missing {rel_path}")
    accepted = read_json(target_bundle / "amendments" / "A001.accepted.json")
    lineage_path = target_bundle / "amendments" / "A001.lineage.json"
    if accepted.get("lineage_path") != lineage_path.as_posix():
        raise SystemExit(f"golden amendment accepted artifact did not record lineage path: {accepted!r}")
    lineage = read_json(lineage_path)
    stages = [item.get("stage") for item in lineage.get("stages", []) if isinstance(item, dict)]
    expected_tail = ["final_proposal", "validation", "manifest_before", "manifest_after", "acceptance"]
    if stages[-5:] != expected_tail:
        raise SystemExit(f"golden amendment lineage tail mismatch: {stages!r}")
    for index in range(1, len(stages)):
        current = lineage["stages"][index]
        previous = lineage["stages"][index - 1]
        if not isinstance(current, dict) or not isinstance(previous, dict):
            raise SystemExit(f"golden amendment lineage stage entry invalid: {lineage.get('stages')!r}")
        current_parent = current.get("parent_sha256")
        previous_sha = previous.get("sha256")
        if current_parent is not None and current_parent != previous_sha:
            raise SystemExit(
                f"golden amendment lineage parent hash broken at {current.get('stage')!r}: "
                f"{current_parent!r} != {previous_sha!r}"
            )
    preflight_report = (target_bundle / "PREFLIGHT_REPORT.md").read_text(encoding="utf-8")
    assert_all_contains(
        preflight_report,
        ["Status: initial_epoch_only", "Accepted amendment: A001", "runtime.index.json"],
        "amended preflight report epoch notice",
    )
    run(["python3", skill_script("goal-preflight", "lint_goal_bundle.py"), "--bundle-dir", target_bundle.as_posix(), "--no-write"])
    ready = run(
        [
            "python3",
            skill_script("goal-main-orchestrator", "render_branch_worktree_commands.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--repo-root",
            REPO_ROOT.as_posix(),
            "--audit",
            (target_bundle / "audit" / "prompt-audit.json").as_posix(),
            "--list-ready",
            "--completed-branch",
            BRANCH_ID,
        ]
    )
    if "B02" not in ready.stdout.splitlines():
        raise SystemExit("amended golden smoke did not continue scheduling from amended manifest")

    strict_branch = run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "validate_branch_status.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--status",
            (target_bundle / "branches" / "B01.status.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--branch",
            BRANCH_NAME,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--json",
        ],
        expect=1,
    )
    assert_any_contains(strict_branch.stdout, ["manifest_sha256", "semantic_input_hashes.job.manifest.json"], "strict branch stale manifest fixture")
    run(
        [
            "python3",
            skill_script("goal-branch-orchestrator", "validate_branch_status.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--status",
            (target_bundle / "branches" / "B01.status.json").as_posix(),
            "--branch-id",
            BRANCH_ID,
            "--branch",
            BRANCH_NAME,
            "--worktree",
            REPO_ROOT.as_posix(),
            "--allow-archived-manifest-hashes",
            "--json",
        ]
    )

    scheduler_path = target_bundle / "schedulers" / "main.scheduler.json"
    scheduler = read_json(scheduler_path)
    scheduler["manifest_sha256"] = sha256_file(target_bundle / "job.manifest.json")
    scheduler["item_ids"] = [BRANCH_ID, "B02"]
    scheduler["events"].append(scheduler_event(5, "refill", eligible_ids=["B02"]))
    scheduler["events"].append(
        scheduler_event(
            6,
            "under_capacity",
            eligible_ids=["B02"],
            reason_code="operator_requested",
            reason="Golden smoke leaves amended branch unlaunched while validating archived terminal evidence.",
        )
    )
    write_json(scheduler_path, scheduler)
    main_status = read_json(target_bundle / "main.status.json")
    main_status["status"] = "partial"
    main_status["runtime_status"] = "partial"
    main_status["dod_status"] = "incomplete"
    main_status["resume_action"] = "resume_or_repair"
    main_status["branch_parallelism"] = {
        "scheduler_path": "schedulers/main.scheduler.json",
        "launched_ids": [BRANCH_ID],
        "finished_ids": [BRANCH_ID],
        "active_ids": [],
        "blocked_ids": [],
        "deferred_ids": ["B02"],
        "max_observed_active": 1,
    }
    main_status["amendment_decisions"] = [*main_status.get("amendment_decisions", []), a001_decision]
    main_status["blockers"] = ["B02 was added by A001 and intentionally left unlaunched in this smoke fixture."]
    main_status["summary"] = "Golden offline smoke validates archived terminal evidence after amendment."
    write_json(target_bundle / "main.status.json", main_status)
    run(
        [
            "python3",
            skill_script("goal-main-orchestrator", "summarize_telemetry.py"),
            "--bundle-dir",
            target_bundle.as_posix(),
        ]
    )
    run(
        [
            "python3",
            skill_script("goal-main-orchestrator", "validate_main_status.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--status",
            (target_bundle / "main.status.json").as_posix(),
            "--job-id",
            JOB_ID,
            "--json",
        ]
    )
    run(["python3", skill_script("goal-main-orchestrator", "summarize_telemetry.py"), "--bundle-dir", target_bundle.as_posix()])
    summary = read_json(target_bundle / "telemetry.summary.json")
    if "amendments/A001.packet/telemetry.json" not in summary.get("telemetry_files", []):
        raise SystemExit("amended golden smoke telemetry summary omitted plan-amender telemetry")


def run_blocker_repair_smoke(source_bundle: Path, target_bundle: Path) -> None:
    shutil.copytree(source_bundle, target_bundle)
    rewrite_copied_branch_paths(target_bundle)
    audit = read_json(target_bundle / "audit" / "prompt-audit.json")
    audit["manifest"] = (target_bundle / "job.manifest.json").as_posix()
    audit["repo_root"] = REPO_ROOT.as_posix()
    write_json(target_bundle / "audit" / "prompt-audit.json", audit)

    status_path = target_bundle / "branches" / f"{BRANCH_ID}.status.json"
    status = read_json(status_path)
    status["status"] = "blocked"
    status["review_status"] = "missing"
    status["blockers"] = [
        "B01-W01 is blocked: required verification test files tests/test_golden_missing.py are absent from the integration branch and outside W01 owned paths.",
        "B01-W01 is blocked: API tests require unowned runtime dependency `marketnn.golden_missing` that is absent from this branch.",
    ]
    write_json(status_path, status)
    review_path = target_bundle / "branches" / f"{BRANCH_ID}.review.json"
    review = read_json(review_path)
    review["verdict"] = "blocked"
    review["findings"] = [
        "Reviewer finding: src/marketnn/reviewer_missing.py is required by the blocked integration path.",
        "Reviewer finding: tests/test_reviewer_missing.py must cover the reviewer-derived repair path.",
    ]
    write_json(review_path, review)
    write_json(target_bundle / "reviewers" / REVIEW_PACKET / "review.json", review)

    create_amendment_decision(
        target_bundle,
        "A002",
        decision="launch",
        reason_code="terminal_blocker_repair",
        reason="Golden smoke deterministically repairs terminal missing-file blockers.",
    )
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "create_blocker_repair_packet.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--main-prompt",
            (target_bundle / "main.prompt.md").as_posix(),
            "--repo-root",
            REPO_ROOT.as_posix(),
            "--amendment-id",
            "A002",
            "--terminal-branch",
            BRANCH_ID,
        ]
    )
    packet_dir = target_bundle / "amendments" / "A002.packet"
    assert_shell_syntax(packet_dir / "launch.sh")
    packet_input = read_json(packet_dir / "input-files.json")
    source_labels = [
        item.get("label")
        for item in packet_input.get("source_files", [])
        if isinstance(item, dict)
    ]
    if f"terminal branch review {BRANCH_ID}" not in source_labels:
        raise SystemExit("deterministic repair packet did not record terminal branch review as a source file")
    review_record = packet_input.get("terminal_branch_reviews", {}).get(BRANCH_ID)
    if not isinstance(review_record, dict) or review_record.get("source_review_path") != review_path.as_posix():
        raise SystemExit(f"deterministic repair packet did not record source review evidence: {review_record!r}")
    run([str(packet_dir / "launch.sh")])
    proposal = read_json(target_bundle / "amendments" / "A002.proposal.json")
    operations = proposal.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        raise SystemExit("deterministic blocker repair should create one repair branch operation")
    branch = operations[0].get("branch") if isinstance(operations[0], dict) else None
    if not isinstance(branch, dict):
        raise SystemExit("deterministic blocker repair operation did not include a branch")
    owned_paths = []
    for work_item in branch.get("work_items", []):
        if isinstance(work_item, dict):
            owned_paths.extend(path for path in work_item.get("owned_paths", []) if isinstance(path, str))
    expected_repair_paths = {
        "src/marketnn/golden_missing.py",
        "tests/test_golden_missing.py",
        "src/marketnn/reviewer_missing.py",
        "tests/test_reviewer_missing.py",
    }
    missing_repair_paths = sorted(expected_repair_paths - set(owned_paths))
    if missing_repair_paths:
        raise SystemExit(f"deterministic repair branch missed expected blocker paths: {owned_paths!r}")
    work_item_text = json.dumps(branch.get("work_items", []), sort_keys=True)
    if "Reviewer finding addressed" not in work_item_text or "reviewer_missing.py" not in work_item_text:
        raise SystemExit("deterministic repair branch did not promote reviewer findings into worker DOD")
    if branch.get("recovers_from") != [BRANCH_ID]:
        raise SystemExit("deterministic repair branch must cite recovers_from terminal branch")
    if branch.get("supersedes") != [BRANCH_ID] or branch.get("recovery_mode") != "replacement_branch":
        raise SystemExit("deterministic repair branch must record replacement recovery semantics")
    route = read_json(packet_dir / "route.json")
    if route.get("mode") != "deterministic_blocker_repair":
        raise SystemExit("deterministic repair packet route did not record deterministic mode")
    if route.get("source_review_paths") != [review_path.as_posix()]:
        raise SystemExit("deterministic repair packet route did not record source review path")
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "validate_amender_packet.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--amendment-id",
            "A002",
            "--json",
        ]
    )
    validation_path = target_bundle / "amendments" / "A002.validation.json"
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "validate_manifest_amendment.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            (target_bundle / "amendments" / "A002.proposal.json").as_posix(),
            "--output",
            validation_path.as_posix(),
            "--terminal-branch",
            BRANCH_ID,
        ]
    )
    run(
        [
            "python3",
            skill_script("goal-plan-amender", "apply_manifest_amendment.py"),
            "--manifest",
            (target_bundle / "job.manifest.json").as_posix(),
            "--proposal",
            (target_bundle / "amendments" / "A002.proposal.json").as_posix(),
            "--validation",
            validation_path.as_posix(),
        ]
    )
    accepted = read_json(target_bundle / "amendments" / "A002.accepted.json")
    if accepted.get("changed_branch_ids") != ["B02"]:
        raise SystemExit(f"deterministic repair should add B02, got {accepted.get('changed_branch_ids')!r}")
    if not (target_bundle / "branches" / "B02.prompt.md").exists():
        raise SystemExit("deterministic blocker repair did not regenerate B02 prompt")
    lineage_path = target_bundle / "amendments" / "A002.lineage.json"
    if not lineage_path.exists():
        raise SystemExit("deterministic blocker repair missing lineage artifact")
    lineage = read_json(lineage_path)
    stages = [item.get("stage") for item in lineage.get("stages", []) if isinstance(item, dict)]
    required = ["generated_proposal", "deterministic_repair", "final_proposal", "validation", "manifest_before", "manifest_after", "acceptance"]
    if stages[-7:] != required:
        raise SystemExit(f"deterministic blocker repair lineage tail mismatch: {stages!r}")
    if accepted.get("lineage_path") != lineage_path.as_posix():
        raise SystemExit(f"deterministic repair acceptance did not preserve lineage path: {accepted!r}")
    for index in range(1, len(stages)):
        current = lineage["stages"][index]
        previous = lineage["stages"][index - 1]
        if not isinstance(current, dict) or not isinstance(previous, dict):
            raise SystemExit(f"deterministic repair lineage stage entry invalid: {lineage.get('stages')!r}")
        current_parent = current.get("parent_sha256")
        previous_sha = previous.get("sha256")
        if current_parent is not None and current_parent != previous_sha:
            raise SystemExit(
                f"deterministic repair lineage parent hash broken at {current.get('stage')!r}: "
                f"{current_parent!r} != {previous_sha!r}"
            )
    run(["python3", skill_script("goal-preflight", "lint_goal_bundle.py"), "--bundle-dir", target_bundle.as_posix(), "--no-write"])


def assert_summary(bundle: Path) -> None:
    summary = read_json(bundle / "telemetry.summary.json")
    if summary.get("defects"):
        raise SystemExit(f"telemetry summary reported defects: {summary['defects']}")
    files = summary.get("telemetry_files")
    if not isinstance(files, list) or len(files) < 5:
        raise SystemExit("telemetry summary must include audit, worker, research, reviewer, and Lite telemetry")
    if summary.get("telemetry_count") != len(files):
        raise SystemExit("telemetry summary telemetry_count must match telemetry_files length")
    totals = summary.get("totals")
    if not isinstance(totals, dict) or totals.get("packet_count") != len(files):
        raise SystemExit("telemetry summary packet_count must match telemetry_files length")


def main() -> int:
    global REPO_ROOT, SKILLS_ROOT
    with tempfile.TemporaryDirectory(prefix="goal-golden-smoke-") as tmp:
        tmp_path = Path(tmp)
        SKILLS_ROOT = install_temp_skills(tmp_path)
        REPO_ROOT = create_temp_repo(tmp_path)
        bundle = tmp_path / "bundle"
        brief = tmp_path / "brief.json"
        task_file = tmp_path / "task.md"
        write_json(brief, golden_brief())
        task_file.write_text("Golden offline smoke task.\n", encoding="utf-8")

        run(
            [
                "python3",
                skill_script("goal-preflight", "lint_preflight_brief.py"),
                "--brief",
                brief.as_posix(),
                "--repo-root",
                REPO_ROOT.as_posix(),
            ]
        )
        run(
            [
                "python3",
                skill_script("goal-preflight", "create_goal_bundle.py"),
                "--brief",
                brief.as_posix(),
                "--repo-root",
                REPO_ROOT.as_posix(),
                "--out-dir",
                bundle.as_posix(),
            ]
        )
        run(["python3", skill_script("goal-preflight", "lint_goal_bundle.py"), "--bundle-dir", bundle.as_posix(), "--no-write"])

        run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "create_audit_packet.py"),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--repo-root",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "audit").as_posix(),
            ]
        )
        assert_compact_audit_launcher(bundle / "audit")

        create_runtime_packet(
            role="worker",
            packet_id=WORKER_PACKET,
            out_dir=bundle / "workers",
            owned_files=["README.md"],
            context_files=[bundle / "branches" / "B01.prompt.md"],
            task_file=task_file,
            worker_route=["codex-mini"],
            selection_reason="Golden smoke uses the cheapest deterministic route alias.",
        )
        assert_shell_syntax(bundle / "workers" / WORKER_PACKET / "launch.sh")
        worker_config = assert_compact_runtime_launcher(bundle / "workers" / WORKER_PACKET, "worker")
        assert_codex_mini_worker_route(worker_config, "Golden smoke uses the cheapest deterministic route alias.")

        mixed_worker = bundle / "workers" / "B01-W99"
        create_runtime_packet(
            role="worker",
            packet_id="B01-W99",
            out_dir=bundle / "workers",
            owned_files=["README.md"],
            context_files=[bundle / "branches" / "B01.prompt.md"],
            task_file=task_file,
            worker_route=["gemini-pro", "codex-spark", "codex-mini"],
            selection_reason="Golden smoke preserves mixed route probe and log metadata.",
        )
        mixed_config = assert_compact_runtime_launcher(mixed_worker, "worker")
        assert_mixed_worker_route(mixed_config, "mixed worker")

        create_runtime_packet(
            role="research-worker",
            packet_id=RESEARCH_PACKET,
            out_dir=bundle / "research",
            owned_files=["README.md"],
            context_files=[bundle / "branches" / "B01.prompt.md"],
            task_file=task_file,
        )
        research_config = assert_compact_runtime_launcher(bundle / "research" / RESEARCH_PACKET, "research-worker")
        assert_research_worker_preserves_user_config(research_config)

        run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_lite_advice_packet.py"),
                "--packet-id",
                LITE_PACKET,
                "--purpose",
                "branch-packet-planning",
                "--base-dir",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "lite").as_posix(),
                "--input-file",
                (REPO_ROOT / "README.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ],
            env=offline_gemini_env(),
        )
        assert_compact_lite_launcher(bundle / "lite" / LITE_PACKET)

        lite_record = write_lite_advice(bundle / "lite" / LITE_PACKET)
        write_audit(bundle)
        run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "validate_prompt_audit.py"),
                "--audit",
                (bundle / "audit" / "prompt-audit.json").as_posix(),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--repo-root",
                REPO_ROOT.as_posix(),
                "--require-pass",
            ]
        )
        assert_prompt_render_command_uses_absolute_paths(bundle)
        worker, research = write_worker_artifacts(bundle)
        write_scheduler_ledgers(bundle)
        write_pre_review_branch_status(bundle, worker, research, lite_record)
        input_hashes = write_pre_review_gate(bundle)
        create_runtime_packet(
            role="reviewer",
            packet_id=REVIEW_PACKET,
            out_dir=bundle / "reviewers",
            manifest=bundle / "job.manifest.json",
            pre_review_gate=bundle / "branches" / "B01.pre_review_gate.json",
            context_files=[
                bundle / "branches" / "B01.prompt.md",
                bundle / "branches" / "B01.pre_review_gate.json",
            ],
            task_file=task_file,
        )
        reviewer_config = assert_compact_runtime_launcher(bundle / "reviewers" / REVIEW_PACKET, "reviewer")
        if reviewer_config.get("attempt_timeout_seconds") != 1800:
            raise SystemExit("reviewer launch-config should preserve the 1800 second attempt timeout")
        assert_lean_codex_attempts(reviewer_config.get("attempts", []), "reviewer Codex attempts")
        write_review(bundle, input_hashes)
        write_branch_and_main_status(bundle)

        validate_branch(bundle)
        run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "summarize_telemetry.py"),
                "--bundle-dir",
                bundle.as_posix(),
            ]
        )
        assert_summary(bundle)
        run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "validate_main_status.py"),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--status",
                (bundle / "main.status.json").as_posix(),
                "--job-id",
                JOB_ID,
                "--json",
            ]
        )
        reconcile_result = run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "reconcile_goal_run.py"),
                "--manifest",
                (bundle / "job.manifest.json").as_posix(),
                "--write",
                "--require-pass",
                "--json",
            ]
        )
        reconcile_report = json.loads(reconcile_result.stdout)
        if reconcile_report.get("status") != "pass" or reconcile_report.get("final_state_validation", {}).get("status") != "pass":
            raise SystemExit(f"reconcile_goal_run should pass on golden bundle: {reconcile_report!r}")
        if reconcile_report.get("resume_action") != "reuse_terminal_status":
            raise SystemExit(f"reconcile_goal_run did not expose reusable terminal resume action: {reconcile_report!r}")
        if reconcile_report.get("schema_status") != "pass" or reconcile_report.get("runtime_status") != "pass":
            raise SystemExit(f"reconcile_goal_run did not expose top-level status dimensions: {reconcile_report!r}")
        golden_state = reconcile_report.get("current_state", {})
        if golden_state.get("terminal_branch_ids") != [BRANCH_ID] or golden_state.get("safe_to_reuse_branch_ids") != [BRANCH_ID]:
            raise SystemExit(f"reconcile_goal_run did not summarize reusable terminal branch state: {golden_state!r}")
        for rel_path in ["orchestration.state.json", "resume.report.json"]:
            if not (bundle / rel_path).exists():
                raise SystemExit(f"reconcile_goal_run --write did not create {rel_path}")

        unpromoted_bundle = tmp_path / "reconcile-unpromoted-review"
        shutil.copytree(bundle, unpromoted_bundle)
        rewrite_copied_branch_paths(unpromoted_bundle)
        (unpromoted_bundle / "main.status.json").unlink()
        (unpromoted_bundle / "branches" / "B01.review.json").unlink()
        unpromoted_result = run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "reconcile_goal_run.py"),
                "--manifest",
                (unpromoted_bundle / "job.manifest.json").as_posix(),
                "--write",
                "--json",
            ]
        )
        unpromoted_report = json.loads(unpromoted_result.stdout)
        codes = {
            item.get("code")
            for key in ["missing_artifacts", "stale_or_unreconciled"]
            for item in unpromoted_report.get(key, [])
            if isinstance(item, dict)
        }
        if "unpromoted_review" not in codes:
            raise SystemExit(f"reconcile_goal_run did not report unpromoted review: {unpromoted_report!r}")
        if not (unpromoted_bundle / "main.status.json").exists():
            raise SystemExit("reconcile_goal_run --write should materialize conservative main.status.json")
        if not (unpromoted_bundle / "resume.report.json").exists():
            raise SystemExit("reconcile_goal_run --write did not write blocked resume.report.json")
        if unpromoted_report.get("final_state_validation", {}).get("status") != "failed":
            raise SystemExit(f"reconcile_goal_run should fail final validation for missing review: {unpromoted_report!r}")
        if unpromoted_report.get("safe_to_reuse", {}).get("overall") is not False:
            raise SystemExit(f"reconcile_goal_run must not mark failed final state reusable: {unpromoted_report!r}")

        missing_branch_status_bundle = tmp_path / "reconcile-missing-branch-status"
        shutil.copytree(bundle, missing_branch_status_bundle)
        rewrite_copied_branch_paths(missing_branch_status_bundle)
        (missing_branch_status_bundle / "main.status.json").unlink()
        (missing_branch_status_bundle / "branches" / "B01.status.json").unlink()
        missing_branch_status_result = run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "reconcile_goal_run.py"),
                "--manifest",
                (missing_branch_status_bundle / "job.manifest.json").as_posix(),
                "--write",
                "--json",
            ]
        )
        missing_branch_status_report = json.loads(missing_branch_status_result.stdout)
        if missing_branch_status_report.get("status") != "blocked":
            raise SystemExit(f"missing branch status should reconcile to blocked: {missing_branch_status_report!r}")
        if missing_branch_status_report.get("resume_action") != "launch_or_resume_branches":
            raise SystemExit(f"missing branch status should route to branch launch/resume: {missing_branch_status_report!r}")
        missing_state = missing_branch_status_report.get("current_state", {})
        if BRANCH_ID not in missing_state.get("missing_branch_ids", []):
            raise SystemExit(f"missing branch status should be listed in current_state.missing_branch_ids: {missing_state!r}")
        if missing_branch_status_report.get("final_state_validation", {}).get("status") != "failed":
            raise SystemExit(f"missing branch status should fail final validation: {missing_branch_status_report!r}")
        if missing_branch_status_report.get("safe_to_reuse", {}).get("overall") is not False:
            raise SystemExit(f"missing branch status must not be reported reusable: {missing_branch_status_report!r}")

        mismatch_bundle = tmp_path / "reviewer-hash-mismatch"
        shutil.copytree(bundle, mismatch_bundle)
        rewrite_copied_branch_paths(mismatch_bundle)
        mismatch_review = read_json(mismatch_bundle / "branches" / "B01.review.json")
        mismatch_review["semantic_input_hashes"]["branches/B01.prompt.md"] = "sha256:" + "1" * 64
        write_json(mismatch_bundle / "branches" / "B01.review.json", mismatch_review)
        write_json(mismatch_bundle / "reviewers" / REVIEW_PACKET / "review.json", mismatch_review)
        mismatch_result = run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "validate_branch_status.py"),
                "--manifest",
                (mismatch_bundle / "job.manifest.json").as_posix(),
                "--status",
                (mismatch_bundle / "branches" / "B01.status.json").as_posix(),
                "--branch-id",
                BRANCH_ID,
                "--branch",
                BRANCH_NAME,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--json",
            ],
            expect=1,
        )
        assert_contains(mismatch_result.stdout, "semantic_input_hashes", "reviewer reuse/hash mismatch fixture")

        route_mismatch_bundle = tmp_path / "reviewer-route-telemetry-mismatch"
        shutil.copytree(bundle, route_mismatch_bundle)
        rewrite_copied_branch_paths(route_mismatch_bundle)
        telemetry_data = read_json(route_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json")
        telemetry_data["attempts"][0]["alias"] = "gpt-5.5"
        telemetry_data["accepted_alias"] = "gpt-5.5"
        write_json(route_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json", telemetry_data)
        route_mismatch_result = validate_branch(route_mismatch_bundle, expect=1)
        assert_any_contains(route_mismatch_result.stdout, ["route.json selected_ladder", "must be one of"], "reviewer route/telemetry mismatch fixture")

        model_mismatch_bundle = tmp_path / "reviewer-alias-model-mismatch"
        shutil.copytree(bundle, model_mismatch_bundle)
        rewrite_copied_branch_paths(model_mismatch_bundle)
        launch_config = read_json(model_mismatch_bundle / "reviewers" / REVIEW_PACKET / "launch-config.json")
        launch_config["attempts"][0]["model"] = "gpt-5.5"
        write_json(model_mismatch_bundle / "reviewers" / REVIEW_PACKET / "launch-config.json", launch_config)
        telemetry_model_data = read_json(model_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json")
        telemetry_model_data["attempts"][0]["model"] = "gpt-5.5"
        write_json(model_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json", telemetry_model_data)
        model_mismatch_result = validate_branch(model_mismatch_bundle, expect=1)
        assert_contains(model_mismatch_result.stdout, "for alias", "reviewer alias/model mismatch fixture")

        worker_cost_misuse_bundle = tmp_path / "worker-route-class-cost-misuse"
        shutil.copytree(bundle, worker_cost_misuse_bundle)
        rewrite_copied_branch_paths(worker_cost_misuse_bundle)
        expensive_ladder = ["gemini-pro"]
        expensive_reason = "Golden smoke intentionally misroutes normal code work through a premium ladder."
        worker_artifact = read_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "status.json")
        worker_artifact["selected_ladder"] = expensive_ladder
        worker_artifact["selection_reason"] = expensive_reason
        write_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "status.json", worker_artifact)
        worker_route = read_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "route.json")
        worker_route["selected_ladder"] = expensive_ladder
        worker_route["selection_reason"] = expensive_reason
        write_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "route.json", worker_route)
        worker_telemetry = read_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "telemetry.json")
        worker_telemetry["accepted_alias"] = "gemini-pro"
        worker_telemetry["attempts"] = [
            {
                "alias": "gemini-pro",
                "provider": "gemini",
                "model": "gemini-3.1-pro-preview",
                "effort": None,
                "command": "gemini --model gemini-3.1-pro-preview --approval-mode default",
                "timeout_seconds": 3600,
                "called": True,
                "accepted": True,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            }
        ]
        worker_telemetry["totals"]["attempts_declared"] = 1
        worker_telemetry["totals"]["attempts_called"] = 1
        write_json(worker_cost_misuse_bundle / "workers" / WORKER_PACKET / "telemetry.json", worker_telemetry)
        worker_cost_status = read_json(worker_cost_misuse_bundle / "branches" / "B01.status.json")
        for item in worker_cost_status.get("worker_statuses", []):
            if isinstance(item, dict) and item.get("packet_id") == WORKER_PACKET:
                item["selected_ladder"] = expensive_ladder
                item["selection_reason"] = expensive_reason
        write_json(worker_cost_misuse_bundle / "branches" / "B01.status.json", worker_cost_status)
        worker_cost_result = validate_branch(worker_cost_misuse_bundle, expect=1)
        assert_all_contains(worker_cost_result.stdout, ["route_class 'normal-code'", "premium/full"], "worker route-class cost misuse fixture")

        missing_worker_gate_bundle = tmp_path / "pre-review-gate-missing-worker-evidence"
        shutil.copytree(bundle, missing_worker_gate_bundle)
        rewrite_copied_branch_paths(missing_worker_gate_bundle)
        missing_worker_gate = read_json(missing_worker_gate_bundle / "branches" / "B01.pre_review_gate.json")
        missing_worker_gate["checks"].pop("worker_evidence", None)
        write_json(missing_worker_gate_bundle / "branches" / "B01.pre_review_gate.json", missing_worker_gate)
        missing_worker_gate_result = validate_branch(missing_worker_gate_bundle, expect=1)
        assert_contains(missing_worker_gate_result.stdout, "worker_evidence", "pre-review gate missing worker evidence fixture")

        missing_launch_config_bundle = tmp_path / "missing-launch-config"
        shutil.copytree(bundle, missing_launch_config_bundle)
        rewrite_copied_branch_paths(missing_launch_config_bundle)
        (missing_launch_config_bundle / "workers" / WORKER_PACKET / "launch-config.json").unlink()
        missing_launch_config_result = validate_branch(missing_launch_config_bundle, expect=1)
        assert_contains(missing_launch_config_result.stdout, "launch config", "missing launch-config fixture")

        missing_debug_events_bundle = tmp_path / "missing-debug-events"
        shutil.copytree(bundle, missing_debug_events_bundle)
        rewrite_copied_branch_paths(missing_debug_events_bundle)
        worker_config = read_json(missing_debug_events_bundle / "workers" / WORKER_PACKET / "launch-config.json")
        worker_config["debug_events_name"] = "debug-events.jsonl"
        write_json(missing_debug_events_bundle / "workers" / WORKER_PACKET / "launch-config.json", worker_config)
        missing_debug_events_result = validate_branch(missing_debug_events_bundle, expect=1)
        assert_contains(missing_debug_events_result.stdout, "debug events", "missing debug events fixture")

        reuse_bundle = tmp_path / "reviewer-reuse-valid"
        make_reuse_bundle(bundle, reuse_bundle, input_hashes)
        validate_branch(reuse_bundle)

        missing_reuse_source_bundle = tmp_path / "reviewer-reuse-missing-source-telemetry"
        shutil.copytree(reuse_bundle, missing_reuse_source_bundle)
        rewrite_copied_branch_paths(missing_reuse_source_bundle)
        (missing_reuse_source_bundle / "reviewers" / "B01-R00" / "telemetry.json").unlink()
        missing_reuse_result = validate_branch(missing_reuse_source_bundle, expect=1)
        assert_contains(missing_reuse_result.stdout, "source_telemetry_path", "reviewer reuse missing telemetry fixture")

        partial_branch_bundle = tmp_path / "partial-branch-subset"
        make_partial_branch_bundle(bundle, partial_branch_bundle, worker)
        validate_branch(partial_branch_bundle)
        partial_gate_result = run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_pre_review_gate.py"),
                "--manifest",
                (partial_branch_bundle / "job.manifest.json").as_posix(),
                "--branch-id",
                BRANCH_ID,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--review-packet-id",
                REVIEW_PACKET,
                "--replace",
                "--test-evidence",
                "partial branch fixture should not reach reviewer",
                "--dod-item",
                "partial worker evidence is rejected before reviewer launch",
                "--json",
            ],
            expect=1,
        )
        assert_contains(partial_gate_result.stdout, "before reviewer launch", "partial worker evidence fixture")

        refill_bundle = tmp_path / "assembler-refill-events"
        make_refill_assembly_bundle(bundle, refill_bundle)

        amendment_bundle = tmp_path / "amended-plan"
        run_amendment_smoke(bundle, amendment_bundle)
        blocker_repair_bundle = tmp_path / "blocker-repair-plan"
        run_blocker_repair_smoke(bundle, blocker_repair_bundle)

        stale_bundle = tmp_path / "stale-telemetry-summary"
        shutil.copytree(bundle, stale_bundle)
        rewrite_copied_branch_paths(stale_bundle)
        os.utime(stale_bundle / "workers" / WORKER_PACKET / "telemetry.json", None)
        stale_result = run(
            [
                "python3",
                skill_script("goal-main-orchestrator", "validate_main_status.py"),
                "--manifest",
                (stale_bundle / "job.manifest.json").as_posix(),
                "--status",
                (stale_bundle / "main.status.json").as_posix(),
                "--job-id",
                JOB_ID,
                "--json",
            ],
            expect=1,
        )
        assert_contains(stale_result.stdout, "stale", "stale telemetry fixture")

        freshness_bundle = tmp_path / "worktree-freshness-stale"
        shutil.copytree(bundle, freshness_bundle)
        rewrite_copied_branch_paths(freshness_bundle)
        original_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        (REPO_ROOT / "README.md").write_text(original_readme + "\nUnreviewed freshness fixture change.\n", encoding="utf-8")
        freshness_result = validate_branch(freshness_bundle, expect=1)
        assert_contains(freshness_result.stdout, "worktree_freshness", "worktree freshness fixture")

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
