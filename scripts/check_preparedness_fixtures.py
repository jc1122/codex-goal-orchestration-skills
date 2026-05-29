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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def write_worker_scheduler(bundle: Path) -> None:
    write_json(
        bundle / "schedulers" / "B01.worker.scheduler.json",
        {
            "schema_version": 1,
            "scheduler_kind": "branch-worker-pool",
            "scheduler_path": "schedulers/B01.worker.scheduler.json",
            "manifest_sha256": sha256_file(bundle / "job.manifest.json"),
            "capacity": 1,
            "item_ids": ["B01-W01"],
            "events": [
                {"event": "ready", "id": "B01-W01"},
                {"event": "launch", "id": "B01-W01"},
                {"event": "finish", "id": "B01-W01", "status": "pass"},
                {"event": "close", "id": "B01-W01"},
            ],
        },
    )


def review_input_hashes(bundle: Path) -> dict[str, str]:
    rel_paths = [
        "job.manifest.json",
        "branches/B01.prompt.md",
        "schedulers/B01.worker.scheduler.json",
        "research/B01-W01/research.json",
        "research/B01-W01/telemetry.json",
    ]
    return {rel_path: sha256_file(bundle / rel_path) for rel_path in rel_paths}


def write_pre_review_gate(bundle: Path) -> None:
    input_hashes = review_input_hashes(bundle)
    write_json(
        bundle / "branches" / "B01.pre_review_gate.json",
        {
            "schema_version": 1,
            "branch_id": "B01",
            "status": "pass",
            "review_packet_id": "B01-R01",
            "commands_run": ["git diff --check main...HEAD"],
            "checks": {
                "manifest_validation": {"status": "pass", "command": "python3 lint_goal_bundle.py --bundle-dir <bundle> --no-write"},
                "status_validation": {"status": "pass", "command": "python3 validate_branch_status.py --manifest <manifest> --status <pre-review-status>"},
                "tests": {"status": "skipped", "skip_allowed": True, "reason": "Static preparedness fixture does not run branch tests."},
                "diff_check": {"status": "pass", "command": "git diff --check main...HEAD"},
                "artifacts_fresh": {"status": "pass", "artifacts": sorted(input_hashes)},
                "ownership": {"status": "pass", "changed_files": []},
                "dod_evidence": {"status": "pass", "items": ["research-worker fixture validates"]},
            },
            "input_hashes": input_hashes,
            "reuse_policy": {
                "mode": "new",
                "accepted": False,
                "input_hashes_match": False,
                "source_review_path": None,
            },
        },
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


def assert_shell_syntax(path: Path) -> None:
    run(["bash", "-n", path.as_posix()])


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
        require_all_launched=True,
    )
    if expect_pass and defects:
        raise SystemExit(f"{label} scheduler fixture should pass, got defects: {defects}")
    if not expect_pass and not defects:
        raise SystemExit(f"{label} scheduler fixture should fail")


def run_scheduler_fixtures(manifest_path: Path) -> None:
    status_validation = load_status_validation()
    manifest_sha = sha256_file(manifest_path)
    base = {
        "schema_version": 1,
        "scheduler_kind": "main-branch-pool",
        "scheduler_path": "schedulers/main.scheduler.json",
        "manifest_sha256": manifest_sha,
        "capacity": 2,
        "item_ids": ["B01", "B02", "B03"],
    }
    multi_branch_refill = {
        **base,
        "events": [
            {"event": "ready", "id": "B01"},
            {"event": "ready", "id": "B02"},
            {"event": "ready", "id": "B03"},
            {"event": "launch", "id": "B01"},
            {"event": "launch", "id": "B02"},
            {"event": "finish", "id": "B01", "status": "pass"},
            {"event": "close", "id": "B01"},
            {"event": "refill", "eligible_ids": ["B03"]},
            {"event": "launch", "id": "B03"},
            {"event": "finish", "id": "B02", "status": "pass"},
            {"event": "close", "id": "B02"},
            {"event": "finish", "id": "B03", "status": "pass"},
            {"event": "close", "id": "B03"},
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
            {"event": "ready", "id": "B01"},
            {"event": "ready", "id": "B02"},
            {"event": "ready", "id": "B03"},
            {"event": "launch", "id": "B01"},
            {"event": "launch", "id": "B02"},
            {"event": "blocked", "id": "B01", "reason": "B01 launcher returned blocked with captured status."},
            {"event": "finish", "id": "B01", "status": "blocked"},
            {"event": "close", "id": "B01"},
            {"event": "refill", "eligible_ids": ["B03"]},
            {"event": "launch", "id": "B03"},
            {"event": "finish", "id": "B02", "status": "pass"},
            {"event": "close", "id": "B02"},
            {"event": "finish", "id": "B03", "status": "pass"},
            {"event": "close", "id": "B03"},
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
            {"event": "ready", "id": "B01-W01"},
            {"event": "ready", "id": "B01-W02"},
            {"event": "ready", "id": "B01-W03"},
            {"event": "launch", "id": "B01-W01"},
            {"event": "launch", "id": "B01-W02"},
            {"event": "blocked", "id": "B01-W01", "reason": "Worker returned blocked but slot was closed."},
            {"event": "finish", "id": "B01-W01", "status": "blocked"},
            {"event": "close", "id": "B01-W01"},
            {"event": "refill", "eligible_ids": ["B01-W03"]},
            {"event": "launch", "id": "B01-W03"},
            {"event": "finish", "id": "B01-W02", "status": "pass"},
            {"event": "close", "id": "B01-W02"},
            {"event": "finish", "id": "B01-W03", "status": "pass"},
            {"event": "close", "id": "B01-W03"},
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
    missing_refill = {
        **base,
        "events": [
            {"event": "ready", "id": "B01"},
            {"event": "ready", "id": "B02"},
            {"event": "ready", "id": "B03"},
            {"event": "launch", "id": "B01"},
            {"event": "launch", "id": "B02"},
            {"event": "finish", "id": "B01", "status": "pass"},
            {"event": "close", "id": "B01"},
            {"event": "launch", "id": "B03"},
            {"event": "finish", "id": "B02", "status": "pass"},
            {"event": "close", "id": "B02"},
            {"event": "finish", "id": "B03", "status": "pass"},
            {"event": "close", "id": "B03"},
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
            {"event": "ready", "id": "B01"},
            {"event": "ready", "id": "B02"},
            {"event": "launch", "id": "B01"},
            {"event": "finish", "id": "B01", "status": "pass"},
            {"event": "close", "id": "B01"},
            {"event": "launch", "id": "B02"},
            {"event": "finish", "id": "B02", "status": "pass"},
            {"event": "close", "id": "B02"},
            {"event": "ready", "id": "B03"},
            {"event": "launch", "id": "B03"},
            {"event": "finish", "id": "B03", "status": "pass"},
            {"event": "close", "id": "B03"},
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
        run_scheduler_fixtures(bundle / "job.manifest.json")

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
        reviewer_launch = (packet_root / "B01-R01" / "launch.sh").read_text(encoding="utf-8")
        assert_shell_syntax(packet_root / "B01-R01" / "launch.sh")
        assert_contains(reviewer_launch, "timeout --foreground", "reviewer launcher")
        assert_contains(reviewer_launch, "attempt_timeout_seconds=1800", "reviewer launcher")
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
