#!/usr/bin/env python3
"""Classify deterministic repair work before launching model agents."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path


def _load_path_rules():
    path = Path(__file__).resolve().parent / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
resolve_absolute_path = PATH_RULES.resolve_absolute_path


def current_skill_name() -> str:
    override = globals().get("SKILL_NAME_OVERRIDE")
    if isinstance(override, str) and override:
        return override
    try:
        return Path(__file__).resolve().parents[1].name
    except IndexError:
        return ""


def skills_root() -> Path:
    override = globals().get("SCRIPT_DIR_OVERRIDE")
    if isinstance(override, Path):
        return override.parents[1]
    return Path(__file__).resolve().parents[2]


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def action(
    actions: list[dict], *, kind: str, reason: str, command: str | None = None, severity: str = "repair"
) -> None:
    item = {"kind": kind, "severity": severity, "reason": reason}
    if command:
        item["command"] = command
    actions.append(item)


def check_context_index(actions: list[dict], checks: list[dict], repo_root: Path | None) -> None:
    if repo_root is None:
        checks.append(
            {"name": "context_index", "status": "skipped", "severity": "info", "reason": "--repo-root not supplied"},
        )
        return
    index = repo_root / "maintenance" / "agent-context-index.json"
    package = repo_root / "package.json"
    if not index.exists() or not package.exists():
        checks.append(
            {
                "name": "context_index",
                "status": "skipped",
                "severity": "info",
                "reason": "repo has no maintenance context index",
            },
        )
        return
    result = run(["npm", "run", "check:context", "--silent"], cwd=repo_root)
    checks.append(
        {
            "name": "context_index",
            "status": "pass" if result.returncode == 0 else "failed",
            "severity": "info" if result.returncode == 0 else "critical",
            "command": "npm run check:context --silent",
        }
    )
    if result.returncode != 0:
        action(
            actions,
            kind="stale_context_index",
            reason="context index check failed; regenerate and recheck before launching model agents",
            command="npm run generate:context && npm run check:context",
        )


def check_manifest_fields(actions: list[dict], checks: list[dict], manifest: dict) -> None:
    defects: list[str] = []
    for branch in manifest.get("branches", []) if isinstance(manifest.get("branches"), list) else []:
        if not isinstance(branch, dict):
            continue
        branch_id = branch.get("id", "<unknown>")
        for key in ["status_path", "review_path", "pre_review_gate_path", "work_items"]:
            if key not in branch:
                defects.append(f"branch {branch_id} missing {key}")
        for item in branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []:
            if not isinstance(item, dict):
                continue
            packet_id = item.get("packet_id", item.get("id", "<unknown>"))
            worker_type = item.get("worker_type", "worker")
            if worker_type in {"research", "research-worker"}:
                if (
                    not isinstance(item.get("route_class_reason"), str)
                    or not item.get("route_class_reason", "").strip()
                ):
                    defects.append(f"research work item {packet_id} missing route_class_reason")
                continue
            if not isinstance(item.get("route_class"), str) or not item.get("route_class", "").strip():
                defects.append(f"work item {packet_id} missing route_class")
            if not isinstance(item.get("route_class_reason"), str) or not item.get("route_class_reason", "").strip():
                defects.append(f"work item {packet_id} missing route_class_reason")
    checks.append(
        {"name": "manifest_route_and_status_fields", "status": "pass" if not defects else "failed", "defects": defects}
    )
    if defects:
        action(
            actions,
            kind="missing_route_or_status_fields",
            reason="manifest lacks deterministic route/status fields required by current validators",
            command="recreate or repair the bundle with goal-preflight/scripts/create_goal_bundle.py, then run lint_goal_bundle.py",
        )


def check_bundle_lint(
    actions: list[dict], checks: list[dict], bundle_dir: Path, lint_report_path: Path | None = None
) -> None:
    script = skills_root() / "goal-preflight" / "scripts" / "lint_goal_bundle.py"
    if lint_report_path is not None and lint_report_path.exists():
        report = load_json(lint_report_path)
        schema_status = report.get("schema_lint_status") or report.get("status") if isinstance(report, dict) else None
        defect_count = (
            report.get("defect_count", len(report.get("defects", []) or [])) if isinstance(report, dict) else None
        )
        checks.append(
            {
                "name": "bundle_lint",
                "status": "pass" if schema_status == "pass" else "failed",
                "source": "existing_report",
                "report": lint_report_path.as_posix(),
                "schema_lint_status": schema_status,
                "reported_status": report.get("status") if isinstance(report, dict) else None,
                "defect_count": defect_count,
            }
        )
        if schema_status != "pass":
            action(
                actions,
                kind="bundle_lint_repair",
                reason="persisted bundle lint report has schema/artifact defects that should be repaired before model launch",
                command=f"python3 {script} --bundle-dir {bundle_dir}",
            )
        return
    if not script.exists():
        checks.append({"name": "bundle_lint", "status": "skipped", "reason": f"missing {script}"})
        return
    result = run(["python3", script.as_posix(), "--bundle-dir", bundle_dir.as_posix(), "--no-write"])
    status = "pass" if result.returncode == 0 else "failed"
    checks.append(
        {"name": "bundle_lint", "status": status, "command": f"python3 {script} --bundle-dir {bundle_dir} --no-write"}
    )
    if result.returncode != 0:
        action(
            actions,
            kind="bundle_lint_repair",
            reason="bundle linter reported path, schema, prompt, or provenance defects that should be repaired before model launch",
            command=f"python3 {script} --bundle-dir {bundle_dir}",
        )


def check_scheduler(
    actions: list[dict], checks: list[dict], manifest: dict, bundle_dir: Path, scope: str, branch_id: str | None
) -> None:
    paths: list[str] = []
    if scope == "main":
        paths.append("schedulers/main.scheduler.json")
    if scope == "branch" and branch_id:
        paths.append(f"schedulers/{branch_id}.worker.scheduler.json")
    if not paths:
        checks.append(
            {"name": "scheduler_gaps", "status": "skipped", "reason": "scope has no scheduler ledger requirement"}
        )
        return
    defects = []
    for rel_path in paths:
        target = bundle_dir / rel_path
        if not target.exists():
            defects.append(f"missing scheduler ledger {rel_path}")
            continue
        data = load_json(target)
        if not isinstance(data, dict) or not isinstance(data.get("events"), list) or not data.get("events"):
            defects.append(f"scheduler ledger has no terminal events {rel_path}")
    checks.append({"name": "scheduler_gaps", "status": "pass" if not defects else "failed", "defects": defects})
    if defects:
        command = (
            f"python3 {skills_root() / 'goal-main-orchestrator' / 'scripts' / 'scheduler_tick.py'} --manifest {bundle_dir / 'job.manifest.json'} --scope main --runtime-ref goal-main-orchestrator --init --record-ready"
            if scope == "main"
            else f"python3 {skills_root() / 'goal-branch-orchestrator' / 'scripts' / 'scheduler_tick.py'} --manifest {bundle_dir / 'job.manifest.json'} --scope worker --branch-id {branch_id} --runtime-ref goal-branch-orchestrator --init --record-ready"
        )
        action(actions, kind="scheduler_gap", reason="scheduler ledger is missing or has no events", command=command)


def check_telemetry_summary(
    actions: list[dict], checks: list[dict], bundle_dir: Path, scope: str, branch_id: str | None
) -> None:
    if scope == "branch":
        checks.append(
            {
                "name": "telemetry_summary",
                "status": "pass",
                "reason": f"branch scope {branch_id or '<unknown>'} skips bundle-wide telemetry.summary.json freshness; main runtime owns aggregate refresh",
            }
        )
        return
    telemetry_files = sorted(path for path in bundle_dir.rglob("telemetry.json") if path.is_file())
    summary = bundle_dir / "telemetry.summary.json"
    status = "pass"
    reason = "telemetry summary present or no telemetry exists"
    if telemetry_files and not summary.exists():
        status = "failed"
        reason = "telemetry files exist but telemetry.summary.json is missing"
    elif telemetry_files and summary.exists():
        newest = max(path.stat().st_mtime for path in telemetry_files)
        if summary.stat().st_mtime < newest:
            status = "failed"
            reason = "telemetry.summary.json is older than at least one telemetry.json"
    checks.append(
        {"name": "telemetry_summary", "status": status, "telemetry_files": len(telemetry_files), "reason": reason}
    )
    if status != "pass":
        script = skills_root() / "goal-main-orchestrator" / "scripts" / "summarize_telemetry.py"
        action(
            actions,
            kind="missing_telemetry_summary",
            reason=reason,
            command=f"python3 {script} --bundle-dir {bundle_dir}",
        )


def runtime_launch_gate(manifest: dict, repo_root: Path | None) -> dict:
    repo_status = manifest.get("repo_status") if isinstance(manifest.get("repo_status"), dict) else {}
    if repo_status.get("repo_is_git") is False or repo_status.get("status") == "not_in_repo":
        return {
            "status": "blocked",
            "reason": "repository root is not a git work tree; runtime branch/worktree orchestration is blocked",
            "repo_status": repo_status,
        }
    if repo_status.get("base_ref_status") == "missing":
        return {
            "status": "blocked",
            "reason": f"base_ref does not exist: {repo_status.get('base_ref')}",
            "repo_status": repo_status,
        }
    if repo_root is not None:
        result = run(["git", "-C", repo_root.as_posix(), "rev-parse", "--is-inside-work-tree"])
        if result.returncode != 0 or result.stdout.strip() != "true":
            return {
                "status": "blocked",
                "reason": "--repo-root is not a git work tree; runtime branch/worktree orchestration is blocked",
                "repo_root": repo_root.as_posix(),
                "repo_status": repo_status,
            }
    return {"status": "pass", "reason": "git runtime gate passed or was not required", "repo_status": repo_status}


def status_blockers(status_data: object) -> list[str]:
    if not isinstance(status_data, dict):
        return []
    blockers = (
        [item for item in status_data.get("blockers", []) if isinstance(item, str) and item.strip()]
        if isinstance(status_data.get("blockers"), list)
        else []
    )
    for key in ["worker_statuses", "branch_statuses"]:
        for item in status_data.get(key, []) if isinstance(status_data.get(key), list) else []:
            if isinstance(item, dict) and isinstance(item.get("blockers"), list):
                blockers.extend(value for value in item["blockers"] if isinstance(value, str) and value.strip())
    return blockers


def check_amendments_and_blockers(
    actions: list[dict],
    checks: list[dict],
    bundle_dir: Path,
    repo_root: Path | None,
    status_path: Path | None,
    branch_id: str | None,
    scope: str,
) -> None:
    if scope == "branch":
        checks.append(
            {
                "name": "amendment_and_blocker_repair",
                "status": "skipped",
                "reason": "branch orchestrator does not launch goal-plan-amender or blocker-repair packets",
            }
        )
        return
    if status_path is None or not status_path.exists():
        checks.append({"name": "amendment_and_blocker_repair", "status": "skipped", "reason": "--status not supplied"})
        return
    data = load_json(status_path)
    blockers = status_blockers(data)
    status = data.get("status") if isinstance(data, dict) else None
    status_branch_id = data.get("branch_id") if isinstance(data, dict) else None
    terminal_branch = branch_id or (
        status_branch_id if isinstance(status_branch_id, str) and status_branch_id.strip() else None
    )
    if terminal_branch is None and status_path.name.endswith(".status.json"):
        terminal_branch = status_path.name.removesuffix(".status.json")
    checks.append(
        {
            "name": "amendment_and_blocker_repair",
            "status": "pass" if status == "pass" and not blockers else "actionable",
            "blocker_count": len(blockers),
        }
    )
    if status in {"partial", "blocked", "failed"}:
        amend_script = skills_root() / "goal-plan-amender" / "scripts" / "recommend_amendment_decision.py"
        terminal_arg = f" --terminal-branch {terminal_branch}" if terminal_branch else ""
        action(
            actions,
            kind="amendment_eligibility",
            reason=f"terminal status is {status!r}; deterministic amendment decision should run before an amender model",
            command=f"python3 {amend_script} --manifest {bundle_dir / 'job.manifest.json'} --amendment-id A001{terminal_arg} --write-decision --replace",
        )
    if blockers:
        repair_script = skills_root() / "goal-plan-amender" / "scripts" / "create_blocker_repair_packet.py"
        main_prompt = bundle_dir / "main.prompt.md"
        repo_arg = f" --repo-root {repo_root}" if repo_root else " --repo-root /abs/repo"
        audit_path = bundle_dir / "audit" / "prompt-audit.json"
        audit_arg = f" --prompt-audit {audit_path}" if audit_path.exists() else ""
        terminal_arg = f" --terminal-branch {terminal_branch}" if terminal_branch else ""
        action(
            actions,
            kind="blocker_repair_candidate",
            reason="status blockers are present; deterministic blocker-repair packet should be attempted before semantic amender launch",
            command=(
                f"python3 {repair_script} --manifest {bundle_dir / 'job.manifest.json'}"
                f" --main-prompt {main_prompt}{repo_arg}{audit_arg}"
                f" --amendment-id A001{terminal_arg} --replace"
            ),
        )


def gate(args: argparse.Namespace) -> dict:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    bundle_dir = (
        resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
        if args.bundle_dir
        else manifest_path.parent
    )
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True) if args.repo_root else None
    status_path = resolve_absolute_path(args.status, "--status", must_exist=True) if args.status else None
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise SystemExit("--manifest must be a JSON object")
    checks: list[dict] = []
    actions: list[dict] = []
    check_context_index(actions, checks, repo_root)
    check_manifest_fields(actions, checks, manifest)
    lint_report_path = (
        resolve_absolute_path(args.bundle_lint_report, "--bundle-lint-report", must_exist=True)
        if args.bundle_lint_report
        else None
    )
    check_bundle_lint(actions, checks, bundle_dir, lint_report_path)
    check_scheduler(actions, checks, manifest, bundle_dir, args.scope, args.branch_id)
    check_telemetry_summary(actions, checks, bundle_dir, args.scope, args.branch_id)
    check_amendments_and_blockers(actions, checks, bundle_dir, repo_root, status_path, args.branch_id, args.scope)
    decision = "script_actions_needed" if actions else "pass_no_actions"
    status = "pass" if decision == "pass_no_actions" else "blocked"
    runtime_gate = runtime_launch_gate(manifest, repo_root)
    script_repair_model_launch_allowed = decision == "pass_no_actions"
    runtime_launch_allowed = runtime_gate["status"] == "pass"
    launch_allowed = script_repair_model_launch_allowed and runtime_launch_allowed
    return {
        "schema_version": 1,
        "status": status,
        "skill": current_skill_name(),
        "scope": args.scope,
        "manifest": manifest_path.as_posix(),
        "bundle_dir": bundle_dir.as_posix(),
        "decision": decision,
        "script_repair_model_launch_allowed": script_repair_model_launch_allowed,
        "runtime_launch_allowed": runtime_launch_allowed,
        "launch_allowed": launch_allowed,
        "model_launch_allowed": launch_allowed,
        "launch_blocked_reason": None
        if launch_allowed
        else runtime_gate.get("reason")
        if not runtime_launch_allowed
        else "script repair actions are required",
        "action_count": len(actions),
        "check_count": len(checks),
        "runtime_gate": runtime_gate,
        "checks": checks,
        "actions": actions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--bundle-dir")
    parser.add_argument("--repo-root")
    parser.add_argument("--scope", choices=["preflight", "main", "branch", "amender"], default="main")
    parser.add_argument("--branch-id")
    parser.add_argument("--status")
    parser.add_argument("--bundle-lint-report")
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = gate(args)
    output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json or not output_path:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(output_path)
    return 0 if data["decision"] == "pass_no_actions" else 2


if __name__ == "__main__":
    raise SystemExit(main())
