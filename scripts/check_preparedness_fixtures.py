#!/usr/bin/env python3
"""Validate static preparedness fixtures for the goal orchestration skills."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
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
            "max_worker_packets_per_branch": 4,
            "max_active_worker_packets": 1,
            "max_observed_active_worker_packets": 1,
            "concurrent_launch_default": True,
            "rolling_refill_default": True,
            "scheduling_mode": "rolling",
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


def assert_shell_syntax(path: Path) -> None:
    run(["bash", "-n", path.as_posix()])


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
        research_launch = (packet_root / "B01-W03" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(packet_root / "B01-W03" / "launch.sh")
        assert_contains(research_launch, "timeout --foreground", "research launcher")
        assert_contains(research_launch, "attempt_timeout_seconds=1200", "research launcher")

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
                "--context-file",
                (bundle / "branches" / "B01.prompt.md").as_posix(),
                "--task-file",
                task_file.as_posix(),
            ]
        )
        reviewer_launch = (packet_root / "B01-R01" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(packet_root / "B01-R01" / "launch.sh")
        assert_contains(reviewer_launch, "timeout --foreground", "reviewer launcher")
        assert_contains(reviewer_launch, "attempt_timeout_seconds=1800", "reviewer launcher")

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

        write_valid_research_fixture(bundle)
        validate_branch(bundle)

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
