#!/usr/bin/env python3
"""Run an offline golden smoke for the goal orchestration workflow."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
import hashlib
import os
import shutil
from pathlib import Path


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


def run(command: list[str], *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=CHECKOUT_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode != expect:
        print(f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(1)
    return result


def skill_script(skill: str, script: str) -> str:
    return (SKILLS_ROOT / skill / "scripts" / script).as_posix()


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


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def assert_compact_runtime_launcher(packet_dir: Path, role: str) -> dict:
    launch_path = packet_dir / "launch.sh"
    assert_shell_syntax(launch_path)
    launch = launch_path.read_text(encoding="utf-8")
    if "runtime_packet_runner.py" not in launch:
        raise SystemExit(f"{role} launcher should delegate to runtime_packet_runner.py")
    if len(launch) > 800:
        raise SystemExit(f"{role} launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != role:
        raise SystemExit(f"{role} launch-config role mismatch: {config.get('role')!r}")
    return config


def assert_compact_lite_launcher(packet_dir: Path) -> dict:
    launch_path = packet_dir / "launch.sh"
    assert_shell_syntax(launch_path)
    launch = launch_path.read_text(encoding="utf-8")
    if "runtime_lite_runner.py" not in launch:
        raise SystemExit("Lite launcher should delegate to runtime_lite_runner.py")
    if '-p "$(cat "$prompt_path")"' in launch:
        raise SystemExit("Lite launcher must not expose full prompt through command-line substitution")
    if len(launch) > 800:
        raise SystemExit(f"Lite launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != "lite_advisor":
        raise SystemExit(f"Lite launch-config role mismatch: {config.get('role')!r}")
    if config.get("attempt_timeout_seconds") != 600:
        raise SystemExit(f"Lite launch-config should preserve the 600 second attempt timeout: {config.get('attempt_timeout_seconds')!r}")
    if config.get("timeout_kill_after_seconds") != 30:
        raise SystemExit(f"Lite launch-config kill-after mismatch: {config.get('timeout_kill_after_seconds')!r}")
    if config.get("telemetry_name") != "telemetry.json":
        raise SystemExit(f"Lite launch-config telemetry name mismatch: {config.get('telemetry_name')!r}")
    if config.get("runner_prompt") != "Follow the complete Lite advisory packet instructions provided on stdin.":
        raise SystemExit("Lite launch-config should preserve the stdin runner prompt")
    if not str(config.get("validation_script", "")).endswith("validate_lite_advice.py"):
        raise SystemExit(f"Lite launch-config validation script mismatch: {config.get('validation_script')!r}")
    if not str(config.get("telemetry_script", "")).endswith("extract_telemetry.py"):
        raise SystemExit(f"Lite launch-config telemetry script mismatch: {config.get('telemetry_script')!r}")
    attempts = config.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise SystemExit(f"Lite launch-config should contain exactly one attempt: {attempts!r}")
    attempt = attempts[0]
    if attempt.get("alias") != "gemini-lite":
        raise SystemExit(f"Lite launch-config attempt alias mismatch: {attempt.get('alias')!r}")
    if attempt.get("event_logs") != ["advice.raw.txt"]:
        raise SystemExit(f"Lite launch-config event logs mismatch: {attempt.get('event_logs')!r}")
    if attempt.get("timeout_seconds") != 600:
        raise SystemExit(f"Lite attempt timeout mismatch: {attempt.get('timeout_seconds')!r}")
    terminal_messages = config.get("terminal_messages")
    if not isinstance(terminal_messages, dict) or "command_failed" not in terminal_messages:
        raise SystemExit("Lite launch-config terminal messages missing")
    return config


def assert_compact_audit_launcher(packet_dir: Path) -> dict:
    launch_path = packet_dir / "launch.sh"
    assert_shell_syntax(launch_path)
    launch = launch_path.read_text(encoding="utf-8")
    if "runtime_prompt_audit_runner.py" not in launch:
        raise SystemExit("audit launcher should delegate to runtime_prompt_audit_runner.py")
    if len(launch) > 800:
        raise SystemExit(f"audit launcher should stay compact, got {len(launch)} chars")
    config = read_json(packet_dir / "launch-config.json")
    if config.get("role") != "prompt-auditor":
        raise SystemExit(f"audit launch-config role mismatch: {config.get('role')!r}")
    if config.get("attempt_timeout_seconds") != 1200:
        raise SystemExit(f"audit launch-config should preserve the 1200 second attempt timeout: {config.get('attempt_timeout_seconds')!r}")
    if config.get("timeout_kill_after_seconds") != 30:
        raise SystemExit(f"audit launch-config kill-after mismatch: {config.get('timeout_kill_after_seconds')!r}")
    attempts = config.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 2:
        raise SystemExit(f"audit launch-config should contain two attempts: {attempts!r}")
    aliases = [attempt.get("alias") for attempt in attempts if isinstance(attempt, dict)]
    if aliases != ["gpt-5.5", "gpt-5.4"]:
        raise SystemExit(f"audit launch-config aliases mismatch: {aliases!r}")
    event_logs = [
        log
        for attempt in attempts
        if isinstance(attempt, dict)
        for log in attempt.get("event_logs", [])
    ]
    if event_logs != ["events-primary.jsonl", "events-fallback.jsonl"]:
        raise SystemExit(f"audit launch-config event logs mismatch: {event_logs!r}")
    return config


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def scheduler_event(seq: int, event: str, **kwargs) -> dict:
    return {
        "seq": seq,
        "timestamp": f"2026-05-29T00:00:{seq:02d}Z",
        "runtime_ref": "golden-offline-smoke",
        "event": event,
        **kwargs,
    }


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


def telemetry(packet_id: str, role: str, output_name: str, *, accepted_alias: str | None, attempts: list[dict]) -> dict:
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


def attempt(
    *,
    alias: str,
    provider: str,
    model: str,
    command: str,
    timeout_seconds: int,
    called: bool,
    accepted: bool,
    effort: str | None = None,
) -> dict:
    return {
        "alias": alias,
        "provider": provider,
        "model": model,
        "effort": effort,
        "command": command,
        "timeout_seconds": timeout_seconds,
        "called": called,
        "accepted": accepted,
        "event_logs": [],
        "probe_logs": [],
        "usage": None,
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


def worker_status() -> dict:
    return {
        "packet_id": WORKER_PACKET,
        "role": "worker",
        "status": "pass",
        "branch": BRANCH_NAME,
        "worktree": REPO_ROOT.as_posix(),
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
    worker = worker_status()
    worker_dir = bundle / "workers" / WORKER_PACKET
    write_json(worker_dir / "status.json", worker)
    write_json(
        worker_dir / "route.json",
        {
            "packet_id": WORKER_PACKET,
            "role": "worker",
            "selected_ladder": worker["selected_ladder"],
            "selection_reason": worker["selection_reason"],
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
                    command="codex exec --ephemeral -m gpt-5.4-mini -s workspace-write",
                    timeout_seconds=3600,
                    called=True,
                    accepted=True,
                )
            ],
        ),
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
            command=f"codex exec --ephemeral -m {model_by_alias[str(alias)]} -s read-only",
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
            "blockers": ["Partial fixture intentionally leaves B01-W02 unlaunched with scheduler evidence."],
            "handoff": "Partial branch fixture.",
        }
    )
    write_json(target_bundle / "branches" / "B01.status.json", branch_status)
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
        "amendments/A001.job.manifest.before.json",
        "branches/B02.prompt.md",
    ]:
        if not (target_bundle / rel_path).exists():
            raise SystemExit(f"amendment smoke missing {rel_path}")
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
    if "manifest_sha256" not in strict_branch.stdout and "semantic_input_hashes.job.manifest.json" not in strict_branch.stdout:
        raise SystemExit("strict branch validation did not fail on stale manifest evidence after amendment")
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
    if "src/marketnn/golden_missing.py" not in owned_paths or "tests/test_golden_missing.py" not in owned_paths:
        raise SystemExit(f"deterministic repair branch missed expected blocker paths: {owned_paths!r}")
    if branch.get("recovers_from") != [BRANCH_ID]:
        raise SystemExit("deterministic repair branch must cite recovers_from terminal branch")
    route = read_json(packet_dir / "route.json")
    if route.get("mode") != "deterministic_blocker_repair":
        raise SystemExit("deterministic repair packet route did not record deterministic mode")
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
    run(["python3", skill_script("goal-preflight", "lint_goal_bundle.py"), "--bundle-dir", target_bundle.as_posix(), "--no-write"])


def assert_shell_syntax(path: Path) -> None:
    run(["bash", "-n", path.as_posix()])


def assert_summary(bundle: Path) -> None:
    summary = read_json(bundle / "telemetry.summary.json")
    if summary.get("defects"):
        raise SystemExit(f"telemetry summary reported defects: {summary['defects']}")
    files = summary.get("telemetry_files")
    if not isinstance(files, list) or len(files) < 5:
        raise SystemExit("telemetry summary must include audit, worker, research, reviewer, and Lite telemetry")
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

        run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_runtime_packet.py"),
                "--role",
                "worker",
                "--packet-id",
                WORKER_PACKET,
                "--branch",
                BRANCH_NAME,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "workers").as_posix(),
                "--owned-file",
                "README.md",
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
                "--worker-route",
                "codex-mini",
                "--selection-reason",
                "Golden smoke uses the cheapest deterministic route alias.",
            ]
        )
        assert_shell_syntax(bundle / "workers" / WORKER_PACKET / "launch.sh")
        worker_config = assert_compact_runtime_launcher(bundle / "workers" / WORKER_PACKET, "worker")
        if worker_config.get("attempt_timeout_seconds") != 3600:
            raise SystemExit("worker launch-config should preserve the 3600 second attempt timeout")
        if worker_config.get("selected_ladder") != ["codex-mini"]:
            raise SystemExit(f"worker launch-config selected_ladder mismatch: {worker_config.get('selected_ladder')!r}")
        if worker_config.get("selection_reason") != "Golden smoke uses the cheapest deterministic route alias.":
            raise SystemExit(f"worker launch-config selection_reason mismatch: {worker_config.get('selection_reason')!r}")
        worker_attempts = worker_config.get("attempts", [])
        event_logs = []
        probe_logs = []
        for attempt in worker_attempts:
            if isinstance(attempt, dict):
                event_logs.extend(attempt.get("event_logs", []))
                probe_logs.extend(attempt.get("probe_logs", []))
        if event_logs != ["events-mini.jsonl"]:
            raise SystemExit(f"worker launch-config event log mismatch: {event_logs!r}")
        if probe_logs:
            raise SystemExit(f"worker launch-config probe logs should be empty for codex-mini-only route: {probe_logs!r}")
        if worker_config.get("selected_commands") != ["codex exec --ephemeral -m gpt-5.4-mini -s workspace-write"]:
            raise SystemExit(f"worker launch-config selected command mismatch: {worker_config.get('selected_commands')!r}")

        mixed_worker = bundle / "workers" / "B01-W99"
        run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_runtime_packet.py"),
                "--role",
                "worker",
                "--packet-id",
                "B01-W99",
                "--branch",
                BRANCH_NAME,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "workers").as_posix(),
                "--owned-file",
                "README.md",
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
                "--worker-route",
                "gemini-pro",
                "copilot-gpt-5.4",
                "codex-mini",
                "--selection-reason",
                "Golden smoke preserves mixed route probe and log metadata.",
            ]
        )
        mixed_config = assert_compact_runtime_launcher(mixed_worker, "worker")
        if mixed_config.get("selected_ladder") != ["gemini-pro", "copilot-gpt-5.4", "codex-mini"]:
            raise SystemExit(f"worker launch-config mixed-route ladder mismatch: {mixed_config.get('selected_ladder')!r}")
        mixed_event_logs = [
            log
            for attempt in mixed_config.get("attempts", [])
            if isinstance(attempt, dict)
            for log in attempt.get("event_logs", [])
        ]
        mixed_probe_logs = [
            log
            for attempt in mixed_config.get("attempts", [])
            if isinstance(attempt, dict)
            for log in attempt.get("probe_logs", [])
        ]
        if mixed_event_logs != ["events-gemini-pro.log", "events-copilot.jsonl", "events-mini.jsonl"]:
            raise SystemExit(f"worker launch-config mixed-route event log mismatch: {mixed_event_logs!r}")
        if mixed_probe_logs != [
            "events-gemini-pro-probe.log",
            "events-copilot-probe.jsonl",
            "events-copilot-version.log",
        ]:
            raise SystemExit(f"worker launch-config mixed-route probe log mismatch: {mixed_probe_logs!r}")

        run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_runtime_packet.py"),
                "--role",
                "research-worker",
                "--packet-id",
                RESEARCH_PACKET,
                "--branch",
                BRANCH_NAME,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "research").as_posix(),
                "--owned-file",
                "README.md",
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        research_config = assert_compact_runtime_launcher(bundle / "research" / RESEARCH_PACKET, "research-worker")
        if research_config.get("attempt_timeout_seconds") != 1200:
            raise SystemExit("research launch-config should preserve the 1200 second attempt timeout")

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
            ]
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
        worker, research = write_worker_artifacts(bundle)
        write_scheduler_ledgers(bundle)
        write_pre_review_branch_status(bundle, worker, research, lite_record)
        input_hashes = write_pre_review_gate(bundle)
        run(
            [
                "python3",
                skill_script("goal-branch-orchestrator", "create_runtime_packet.py"),
                "--role",
                "reviewer",
                "--packet-id",
                REVIEW_PACKET,
                "--branch",
                BRANCH_NAME,
                "--worktree",
                REPO_ROOT.as_posix(),
                "--out-dir",
                (bundle / "reviewers").as_posix(),
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
        reviewer_config = assert_compact_runtime_launcher(bundle / "reviewers" / REVIEW_PACKET, "reviewer")
        if reviewer_config.get("attempt_timeout_seconds") != 1800:
            raise SystemExit("reviewer launch-config should preserve the 1800 second attempt timeout")
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
        if "semantic_input_hashes" not in mismatch_result.stdout:
            raise SystemExit("reviewer reuse/hash mismatch fixture did not fail on semantic_input_hashes")

        route_mismatch_bundle = tmp_path / "reviewer-route-telemetry-mismatch"
        shutil.copytree(bundle, route_mismatch_bundle)
        rewrite_copied_branch_paths(route_mismatch_bundle)
        telemetry_data = read_json(route_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json")
        telemetry_data["attempts"][0]["alias"] = "gpt-5.5"
        telemetry_data["accepted_alias"] = "gpt-5.5"
        write_json(route_mismatch_bundle / "reviewers" / REVIEW_PACKET / "telemetry.json", telemetry_data)
        route_mismatch_result = validate_branch(route_mismatch_bundle, expect=1)
        if "route.json selected_ladder" not in route_mismatch_result.stdout and "must be one of" not in route_mismatch_result.stdout:
            raise SystemExit("reviewer route/telemetry mismatch fixture did not fail on route aliases")

        missing_worker_gate_bundle = tmp_path / "pre-review-gate-missing-worker-evidence"
        shutil.copytree(bundle, missing_worker_gate_bundle)
        rewrite_copied_branch_paths(missing_worker_gate_bundle)
        missing_worker_gate = read_json(missing_worker_gate_bundle / "branches" / "B01.pre_review_gate.json")
        missing_worker_gate["checks"].pop("worker_evidence", None)
        write_json(missing_worker_gate_bundle / "branches" / "B01.pre_review_gate.json", missing_worker_gate)
        missing_worker_gate_result = validate_branch(missing_worker_gate_bundle, expect=1)
        if "worker_evidence" not in missing_worker_gate_result.stdout:
            raise SystemExit("pre-review gate fixture did not fail when worker_evidence was missing")

        reuse_bundle = tmp_path / "reviewer-reuse-valid"
        make_reuse_bundle(bundle, reuse_bundle, input_hashes)
        validate_branch(reuse_bundle)

        missing_reuse_source_bundle = tmp_path / "reviewer-reuse-missing-source-telemetry"
        shutil.copytree(reuse_bundle, missing_reuse_source_bundle)
        rewrite_copied_branch_paths(missing_reuse_source_bundle)
        (missing_reuse_source_bundle / "reviewers" / "B01-R00" / "telemetry.json").unlink()
        missing_reuse_result = validate_branch(missing_reuse_source_bundle, expect=1)
        if "source_telemetry_path" not in missing_reuse_result.stdout:
            raise SystemExit("reviewer reuse missing telemetry fixture did not fail on source_telemetry_path")

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
        if "before reviewer launch" not in partial_gate_result.stdout:
            raise SystemExit("partial worker evidence fixture did not fail the pre-review gate")

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
        if "stale" not in stale_result.stdout:
            raise SystemExit("stale telemetry fixture did not fail on stale summary evidence")

        freshness_bundle = tmp_path / "worktree-freshness-stale"
        shutil.copytree(bundle, freshness_bundle)
        rewrite_copied_branch_paths(freshness_bundle)
        original_readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        (REPO_ROOT / "README.md").write_text(original_readme + "\nUnreviewed freshness fixture change.\n", encoding="utf-8")
        freshness_result = validate_branch(freshness_bundle, expect=1)
        if "worktree_freshness" not in freshness_result.stdout:
            raise SystemExit("worktree freshness fixture did not fail on changed current file content")

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
