#!/usr/bin/env python3
"""Run an offline golden smoke for the goal orchestration workflow."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import tempfile
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
    for name in ["_goal_shared", "goal-preflight", "goal-main-orchestrator", "goal-branch-orchestrator"]:
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
        "max_active_branch_agents": 1,
        "serial_reason": "Single-branch golden smoke keeps the offline gate small.",
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
                        "owned_paths": ["README.md"],
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


def write_review(bundle: Path) -> None:
    review = {
        "packet_id": REVIEW_PACKET,
        "role": "reviewer",
        "verdict": "mergeable",
        "findings": ["Golden offline smoke review artifact is synthetically mergeable."],
        "commands_run": ["git diff --check main...HEAD"],
        "verification_gaps": [],
        "residual_risks": ["Synthetic smoke does not exercise live model CLIs."],
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
            accepted_alias="gpt-5.5",
            attempts=[
                attempt(
                    alias="gpt-5.5",
                    provider="codex",
                    model="gpt-5.5",
                    command="codex exec --ephemeral -m gpt-5.5 -s read-only",
                    timeout_seconds=1800,
                    called=True,
                    accepted=True,
                ),
                attempt(
                    alias="gpt-5.4",
                    provider="codex",
                    model="gpt-5.4",
                    command="codex exec --ephemeral -m gpt-5.4 -s read-only",
                    timeout_seconds=1800,
                    called=False,
                    accepted=False,
                ),
            ],
        ),
    )


def write_branch_and_main_status(bundle: Path, worker: dict, research: dict, lite_record: dict) -> None:
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
            "status": "pass",
            "branch": BRANCH_NAME,
            "worktree": REPO_ROOT.as_posix(),
            "worker_statuses": [worker_rollup, research_rollup],
            "worker_parallelism": {
                "max_worker_packets_per_branch": 4,
                "max_active_worker_packets": 4,
                "max_observed_active_worker_packets": 2,
                "concurrent_launch_default": True,
                "rolling_refill_default": True,
                "scheduling_mode": "rolling",
                "serialized_workers": [],
                "deferred_workers": [],
                "serial_reasons": [],
                "refill_events": [],
            },
            "lite_advice": [lite_record],
            "review_status": "mergeable",
            "changed_files": [],
            "commands_run": ["git diff --check main...HEAD"],
            "tests": ["bash -n generated launchers", "installed validators passed"],
            "dod_checklist": [
                "normal worker artifact validates",
                "research-worker artifact validates",
                "reviewer artifact validates",
                "Lite artifact envelope validates",
            ],
            "blockers": [],
            "handoff": "Golden offline smoke branch status.",
        },
    )
    write_json(
        bundle / "main.status.json",
        {
            "job_id": JOB_ID,
            "status": "pass",
            "audit_status": "pass",
            "branch_statuses": [
                {
                    "branch_id": BRANCH_ID,
                    "status": "pass",
                    "status_path": "branches/B01.status.json",
                    "review_path": "branches/B01.review.json",
                    "review_status": "mergeable",
                }
            ],
            "lite_advice": [],
            "commands_run": ["git diff --check main...HEAD", "installed validators passed"],
            "dod_checklist": ["golden offline smoke validates main, branch, packet, Lite, and telemetry artifacts"],
            "blockers": [],
            "summary": "Golden offline smoke main status.",
        },
    )


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
        assert_shell_syntax(bundle / "audit" / "launch.sh")

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
        assert_shell_syntax(bundle / "research" / RESEARCH_PACKET / "launch.sh")

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
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        assert_shell_syntax(bundle / "reviewers" / REVIEW_PACKET / "launch.sh")

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
        assert_shell_syntax(bundle / "lite" / LITE_PACKET / "launch.sh")

        lite_record = write_lite_advice(bundle / "lite" / LITE_PACKET)
        write_audit(bundle)
        worker, research = write_worker_artifacts(bundle)
        write_review(bundle)
        write_branch_and_main_status(bundle, worker, research, lite_record)

        run(
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
            ]
        )
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

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
