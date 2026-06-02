#!/usr/bin/env python3
"""Reconstruct a goal bundle's resume and final-state surface."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load required module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_module("goal_shared_orchestration_contract", SCRIPT_DIR / "orchestration_contract.py")
STATUS_VALIDATION = _load_module("goal_shared_status_validation", SCRIPT_DIR / "status_validation.py")
MAIN_VALIDATOR = _load_module(
    "goal_reconcile_validate_main_status",
    SKILLS_ROOT / "goal-main-orchestrator" / "scripts" / "validate_main_status.py",
)
MAIN_ASSEMBLER = _load_module(
    "goal_reconcile_assemble_main_status",
    SKILLS_ROOT / "goal-main-orchestrator" / "scripts" / "assemble_main_status.py",
)
BRANCH_VALIDATOR = _load_module(
    "goal_reconcile_validate_branch_status",
    SKILLS_ROOT / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py",
)


TELEMETRY_ROOTS = ("audit", "workers", "research", "reviewers", "lite", "amendments")
RUNTIME_STATUS_VALUES = set(CONTRACT.STATUSES)
REVIEW_STATUS_VALUES = set(CONTRACT.REVIEW_STATUSES)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object at {path}")
    return data


def read_json_or_none(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return read_json(path), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def rel_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def bundle_path(bundle_dir: Path, value: object, fallback: str) -> Path:
    raw = value if isinstance(value, str) and value.strip() else fallback
    path = Path(str(raw))
    return path if path.is_absolute() else bundle_dir / path


def repo_or_bundle_path(bundle_dir: Path, repo_root: Path | None, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    if repo_root is not None:
        return repo_root / path
    return None


def artifact_ref(bundle_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "path": rel_path(bundle_dir, path),
        "exists": path.exists(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }


def add_issue(target: list[dict[str, Any]], *, code: str, path: Path | str, kind: str, owner: str, message: str) -> None:
    path_text = path.as_posix() if isinstance(path, Path) else path
    item = {
        "code": code,
        "kind": kind,
        "owner": owner,
        "path": path_text,
        "message": message,
    }
    if item not in target:
        target.append(item)


def materialize_main_status_if_missing(manifest_path: Path) -> None:
    main_status_path = manifest_path.parent / "main.status.json"
    if main_status_path.exists():
        return
    MAIN_ASSEMBLER.assemble(
        manifest_path,
        out_path=main_status_path,
        write_decision=True,
        summary_text="Main status materialized by reconcile_goal_run.py from current bundle artifacts.",
    )


def branch_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        return []
    return [branch for branch in branches if isinstance(branch, dict)]


def branch_dependencies(branches: list[dict[str, Any]]) -> dict[str, list[str]]:
    known = {branch.get("id") for branch in branches if isinstance(branch.get("id"), str)}
    result: dict[str, list[str]] = {}
    for branch in branches:
        branch_id = branch.get("id")
        if not isinstance(branch_id, str):
            continue
        deps: list[str] = []
        raw_deps = branch.get("depends_on", [])
        if isinstance(raw_deps, list):
            deps = [item for item in raw_deps if isinstance(item, str) and item in known]
        result[branch_id] = deps
    return result


def worker_dependencies(branch: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    work_items = branch.get("work_items")
    if not isinstance(work_items, list):
        return [], {}
    item_to_packet: dict[str, str] = {}
    packet_ids: list[str] = []
    dependencies: dict[str, list[str]] = {}
    for item in work_items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        packet_id = item.get("packet_id")
        if not isinstance(item_id, str) or not isinstance(packet_id, str):
            continue
        packet_ids.append(packet_id)
        packet_deps: list[str] = []
        raw_deps = item.get("depends_on", [])
        if isinstance(raw_deps, list):
            packet_deps = [item_to_packet[dep] for dep in raw_deps if isinstance(dep, str) and dep in item_to_packet]
        dependencies[packet_id] = packet_deps
        item_to_packet[item_id] = packet_id
    return packet_ids, dependencies


def validate_scheduler(
    *,
    defects: list[str],
    scheduler_path: Path,
    expected_path: str,
    scheduler_kind: str,
    expected_ids: list[str],
    dependencies: dict[str, list[str]],
    capacity: int,
    manifest_path: Path,
    require_all_launched: bool,
) -> dict[str, Any]:
    local_defects: list[str] = []
    rollup = STATUS_VALIDATION.validate_scheduler_artifact(
        local_defects,
        scheduler_path,
        "$.scheduler",
        scheduler_kind=scheduler_kind,
        expected_path=expected_path,
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=capacity,
        manifest_path=manifest_path,
        require_all_launched=require_all_launched,
    )
    defects.extend(local_defects)
    return {
        "path": expected_path,
        "exists": scheduler_path.exists(),
        "validation_status": "pass" if not local_defects else "failed",
        "validation_defects": local_defects,
        "launched": rollup.get("launched", []),
        "finished": rollup.get("finished", []),
        "closed_or_deferred": sorted(set(rollup.get("finished", [])) | set(rollup.get("deferred", []))),
        "active": rollup.get("active", []),
        "blocked": rollup.get("blocked", []),
        "deferred": rollup.get("deferred", []),
        "finished_status": rollup.get("finished_status", {}),
        "max_observed_active": rollup.get("max_observed_active", 0),
    }


def discover_telemetry_files(bundle_dir: Path, *, debug: bool = False) -> list[Path]:
    filename = "telemetry.debug.json" if debug else "telemetry.json"
    files: list[Path] = []
    for root_name in TELEMETRY_ROOTS:
        root = bundle_dir / root_name
        if root.is_dir():
            files.extend(path for path in root.glob(f"**/{filename}") if path.is_file())
    return sorted(files, key=lambda path: rel_path(bundle_dir, path))


def telemetry_summary_state(
    bundle_dir: Path,
    *,
    stale_or_unreconciled: list[dict[str, Any]],
    missing_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    telemetry_files = discover_telemetry_files(bundle_dir)
    debug_files = discover_telemetry_files(bundle_dir, debug=True)
    summary_path = bundle_dir / "telemetry.summary.json"
    debug_summary_path = bundle_dir / "telemetry.debug.summary.json"
    state: dict[str, Any] = {
        "summary_path": "telemetry.summary.json",
        "summary_exists": summary_path.exists(),
        "telemetry_files": [rel_path(bundle_dir, path) for path in telemetry_files],
        "telemetry_count": len(telemetry_files),
        "debug_summary_path": "telemetry.debug.summary.json",
        "debug_summary_exists": debug_summary_path.exists(),
        "debug_telemetry_count": len(debug_files),
    }
    if telemetry_files and not summary_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_telemetry_summary",
            path="telemetry.summary.json",
            kind="telemetry_summary",
            owner="main",
            message="packet telemetry exists but telemetry.summary.json is missing",
        )
    if summary_path.exists():
        summary_mtime = summary_path.stat().st_mtime_ns
        stale = [rel_path(bundle_dir, path) for path in telemetry_files if path.stat().st_mtime_ns > summary_mtime]
        if stale:
            add_issue(
                stale_or_unreconciled,
                code="stale_telemetry_summary",
                path="telemetry.summary.json",
                kind="telemetry_summary",
                owner="main",
                message="telemetry.summary.json is older than packet telemetry: " + ", ".join(stale),
            )
        summary, error = read_json_or_none(summary_path)
        if summary is None:
            add_issue(
                stale_or_unreconciled,
                code="invalid_telemetry_summary",
                path="telemetry.summary.json",
                kind="telemetry_summary",
                owner="main",
                message=error or "telemetry summary is not a JSON object",
            )
        else:
            listed = summary.get("telemetry_files")
            if isinstance(listed, list):
                omitted = sorted(set(state["telemetry_files"]) - {item for item in listed if isinstance(item, str)})
                if omitted:
                    add_issue(
                        stale_or_unreconciled,
                        code="telemetry_summary_omits_files",
                        path="telemetry.summary.json",
                        kind="telemetry_summary",
                        owner="main",
                        message="telemetry.summary.json omits packet telemetry: " + ", ".join(omitted),
                    )
            totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
            state["summary_totals"] = {
                "packet_count": totals.get("packet_count"),
                "attempts_called": totals.get("attempts_called"),
                "attempts_declared": totals.get("attempts_declared"),
            }
    return state


def reviewer_outputs(bundle_dir: Path, branch_id: str) -> list[dict[str, Any]]:
    reviewers_dir = bundle_dir / "reviewers"
    if not reviewers_dir.is_dir():
        return []
    outputs: list[dict[str, Any]] = []
    for path in sorted(reviewers_dir.glob(f"{branch_id}-R*/review.json")):
        data, error = read_json_or_none(path)
        outputs.append(
            {
                "path": rel_path(bundle_dir, path),
                "exists": True,
                "valid_json": data is not None,
                "packet_id": data.get("packet_id") if isinstance(data, dict) else None,
                "role": data.get("role") if isinstance(data, dict) else None,
                "verdict": data.get("verdict") if isinstance(data, dict) else None,
                "sha256": sha256_file(path),
                "error": error,
            }
        )
    return outputs


def packet_artifact_summary(bundle_dir: Path, packet_id: str, role: str) -> dict[str, Any]:
    root_name = "research" if role == "research-worker" else "workers"
    output_name = "research.json" if role == "research-worker" else "status.json"
    packet_dir = bundle_dir / root_name / packet_id
    return {
        "packet_id": packet_id,
        "role": role,
        "packet_dir": rel_path(bundle_dir, packet_dir),
        "packet_dir_exists": packet_dir.is_dir(),
        "output_path": rel_path(bundle_dir, packet_dir / output_name),
        "output_exists": (packet_dir / output_name).exists(),
        "telemetry_path": rel_path(bundle_dir, packet_dir / "telemetry.json"),
        "telemetry_exists": (packet_dir / "telemetry.json").exists(),
        "packet_summary_path": rel_path(bundle_dir, packet_dir / "packet.summary.json"),
        "packet_summary_exists": (packet_dir / "packet.summary.json").exists(),
        "launcher_state_path": rel_path(bundle_dir, packet_dir / "launcher-state.json"),
        "launcher_state_exists": (packet_dir / "launcher-state.json").exists(),
    }


def branch_summary(
    *,
    bundle_dir: Path,
    repo_root: Path | None,
    manifest_path: Path,
    manifest: dict[str, Any],
    branch: dict[str, Any],
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    next_commands: list[str],
) -> dict[str, Any]:
    branch_id = str(branch.get("id", ""))
    status_path = bundle_path(bundle_dir, branch.get("status_path"), f"branches/{branch_id}.status.json")
    review_path = bundle_path(bundle_dir, branch.get("review_path"), f"branches/{branch_id}.review.json")
    prompt_path = bundle_path(bundle_dir, branch.get("prompt"), f"branches/{branch_id}.prompt.md")
    pre_review_gate_path = bundle_path(bundle_dir, branch.get("pre_review_gate_path"), f"branches/{branch_id}.pre_review_gate.json")
    worktree_path = repo_or_bundle_path(bundle_dir, repo_root, branch.get("worktree_path"))
    status_data, status_error = read_json_or_none(status_path) if status_path.exists() else (None, None)
    status_value = status_data.get("status") if isinstance(status_data, dict) else None
    review_status = status_data.get("review_status") if isinstance(status_data, dict) else None
    if not status_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_branch_status",
            path=rel_path(bundle_dir, status_path),
            kind="branch_status",
            owner=branch_id,
            message=f"manifest branch {branch_id} has no status artifact",
        )
    if not prompt_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_branch_prompt",
            path=rel_path(bundle_dir, prompt_path),
            kind="branch_prompt",
            owner=branch_id,
            message=f"manifest branch {branch_id} prompt is missing",
        )
    if worktree_path is not None and not worktree_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_branch_worktree",
            path=worktree_path.as_posix(),
            kind="worktree",
            owner=branch_id,
            message=f"manifest branch {branch_id} worktree is missing",
        )
    outputs = reviewer_outputs(bundle_dir, branch_id)
    if outputs and not review_path.exists():
        add_issue(
            stale_or_unreconciled,
            code="unpromoted_review",
            path=rel_path(bundle_dir, review_path),
            kind="review",
            owner=branch_id,
            message=f"reviewer packet output exists for {branch_id} but branch review path is missing",
        )
    if review_path.exists() and outputs:
        review_sha = sha256_file(review_path)
        if review_sha not in {item.get("sha256") for item in outputs}:
            add_issue(
                stale_or_unreconciled,
                code="review_path_not_matching_reviewer_output",
                path=rel_path(bundle_dir, review_path),
                kind="review",
                owner=branch_id,
                message=f"branch review path for {branch_id} does not match any reviewer packet output",
            )
    if status_value == "pass" and not review_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_pass_review",
            path=rel_path(bundle_dir, review_path),
            kind="review",
            owner=branch_id,
            message=f"passing branch {branch_id} requires a canonical branch review artifact",
        )

    validation_defects: list[str] = []
    if status_data is not None:
        validation_defects = BRANCH_VALIDATOR.validate_branch_status(
            status_data,
            branch_id=branch_id,
            branch=status_data.get("branch") if isinstance(status_data.get("branch"), str) else branch.get("branch_name"),
            worktree=status_data.get("worktree") if isinstance(status_data.get("worktree"), str) else (worktree_path.as_posix() if worktree_path else None),
            manifest=manifest,
            manifest_path=manifest_path,
            status_path=status_path,
        )
        if validation_defects:
            add_issue(
                stale_or_unreconciled,
                code="invalid_branch_status",
                path=rel_path(bundle_dir, status_path),
                kind="branch_status",
                owner=branch_id,
                message=f"branch status validator failed with {len(validation_defects)} defect(s)",
            )
    elif status_error:
        validation_defects = [status_error]

    worker_ids, worker_deps = worker_dependencies(branch)
    scheduler_rel = "schedulers/" + f"{branch_id}.worker.scheduler.json"
    worker_parallelism = branch.get("worker_parallelism") if isinstance(branch.get("worker_parallelism"), dict) else {}
    if isinstance(worker_parallelism.get("scheduler_path"), str):
        scheduler_rel = worker_parallelism["scheduler_path"]
    scheduler_defects: list[str] = []
    scheduler = validate_scheduler(
        defects=scheduler_defects,
        scheduler_path=bundle_dir / scheduler_rel,
        expected_path=scheduler_rel,
        scheduler_kind="branch-worker-pool",
        expected_ids=worker_ids,
        dependencies=worker_deps,
        capacity=int(branch.get("max_active_worker_packets", CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH))
        if isinstance(branch.get("max_active_worker_packets"), int)
        else CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH,
        manifest_path=manifest_path,
        require_all_launched=status_value == "pass",
    )
    if scheduler_defects:
        add_issue(
            stale_or_unreconciled,
            code="unreconciled_worker_scheduler",
            path=scheduler_rel,
            kind="scheduler",
            owner=branch_id,
            message=f"worker scheduler has {len(scheduler_defects)} validation defect(s)",
        )

    if status_path.exists():
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-branch-orchestrator' / 'scripts' / 'validate_branch_status.py'} "
            f"--manifest {manifest_path.as_posix()} --status {status_path.as_posix()}"
        )
    elif branch_id:
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-branch-orchestrator' / 'scripts' / 'assemble_branch_status.py'} "
            f"--manifest {manifest_path.as_posix()} --branch-id {branch_id} --worktree <branch-worktree> --replace"
        )

    workers = []
    if isinstance(branch.get("work_items"), list):
        for item in branch["work_items"]:
            if isinstance(item, dict) and isinstance(item.get("packet_id"), str):
                workers.append(
                    packet_artifact_summary(bundle_dir, str(item["packet_id"]), str(item.get("worker_type", "worker")))
                )

    return {
        "branch_id": branch_id,
        "branch_name": branch.get("branch_name"),
        "status": status_value,
        "schema_status": "pass" if status_data is not None else "missing",
        "runtime_status": status_value if status_value in RUNTIME_STATUS_VALUES else "missing",
        "review_status": review_status if review_status in REVIEW_STATUS_VALUES else "missing",
        "resume_action": "reuse_terminal_status" if status_data is not None and not validation_defects else "repair_or_reassemble",
        "status_path": artifact_ref(bundle_dir, status_path),
        "review_path": artifact_ref(bundle_dir, review_path),
        "prompt_path": artifact_ref(bundle_dir, prompt_path),
        "pre_review_gate_path": artifact_ref(bundle_dir, pre_review_gate_path),
        "worktree_path": worktree_path.as_posix() if worktree_path else None,
        "worktree_exists": worktree_path.exists() if worktree_path else None,
        "reviewer_outputs": outputs,
        "worker_scheduler": scheduler,
        "workers": workers,
        "validation": {
            "status": "pass" if not validation_defects else "failed",
            "defects": validation_defects,
        },
    }


def manifest_path_checks(
    *,
    bundle_dir: Path,
    repo_root: Path | None,
    manifest: dict[str, Any],
    missing_artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    main_prompt = bundle_dir / "main.prompt.md"
    checks.append({"kind": "main_prompt", **artifact_ref(bundle_dir, main_prompt)})
    if not main_prompt.exists():
        add_issue(
            missing_artifacts,
            code="missing_main_prompt",
            path="main.prompt.md",
            kind="main_prompt",
            owner="main",
            message="main.prompt.md is missing",
        )
    for branch in branch_entries(manifest):
        branch_id = str(branch.get("id", ""))
        for key, fallback, kind in [
            ("prompt", f"branches/{branch_id}.prompt.md", "branch_prompt"),
            ("status_path", f"branches/{branch_id}.status.json", "branch_status"),
            ("review_path", f"branches/{branch_id}.review.json", "branch_review"),
            ("pre_review_gate_path", f"branches/{branch_id}.pre_review_gate.json", "pre_review_gate"),
        ]:
            path = bundle_path(bundle_dir, branch.get(key), fallback)
            checks.append({"branch_id": branch_id, "manifest_key": key, "kind": kind, **artifact_ref(bundle_dir, path)})
        worktree = repo_or_bundle_path(bundle_dir, repo_root, branch.get("worktree_path"))
        if worktree is not None:
            checks.append(
                {
                    "branch_id": branch_id,
                    "manifest_key": "worktree_path",
                    "kind": "worktree",
                    "path": worktree.as_posix(),
                    "exists": worktree.exists(),
                    "sha256": None,
                    "size_bytes": None,
                }
            )
    return checks


def build_report(manifest_path: Path, *, repo_root: Path | None) -> dict[str, Any]:
    bundle_dir = manifest_path.parent
    manifest = read_json(manifest_path)
    branches = branch_entries(manifest)
    missing_artifacts: list[dict[str, Any]] = []
    stale_or_unreconciled: list[dict[str, Any]] = []
    next_commands: list[str] = []
    manifest_checks = manifest_path_checks(
        bundle_dir=bundle_dir,
        repo_root=repo_root,
        manifest=manifest,
        missing_artifacts=missing_artifacts,
    )

    telemetry = telemetry_summary_state(
        bundle_dir,
        stale_or_unreconciled=stale_or_unreconciled,
        missing_artifacts=missing_artifacts,
    )
    branch_reports = [
        branch_summary(
            bundle_dir=bundle_dir,
            repo_root=repo_root,
            manifest_path=manifest_path,
            manifest=manifest,
            branch=branch,
            missing_artifacts=missing_artifacts,
            stale_or_unreconciled=stale_or_unreconciled,
            next_commands=next_commands,
        )
        for branch in branches
    ]

    main_status_path = bundle_dir / "main.status.json"
    main_status_data, main_status_error = read_json_or_none(main_status_path) if main_status_path.exists() else (None, None)
    main_validation_defects: list[str] = []
    if main_status_data is None:
        add_issue(
            missing_artifacts,
            code="missing_main_status",
            path="main.status.json",
            kind="main_status",
            owner="main",
            message="main.status.json is missing; finalization has not written a terminal main artifact",
        )
        if main_status_error:
            main_validation_defects.append(main_status_error)
    else:
        main_validation_defects = MAIN_VALIDATOR.validate_main_status(
            main_status_data,
            job_id=manifest.get("job_id") if isinstance(manifest.get("job_id"), str) else None,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        if main_validation_defects:
            add_issue(
                stale_or_unreconciled,
                code="invalid_main_status",
                path="main.status.json",
                kind="main_status",
                owner="main",
                message=f"main status validator failed with {len(main_validation_defects)} defect(s)",
            )

    branch_ids = [str(branch.get("id")) for branch in branches if isinstance(branch.get("id"), str)]
    main_scheduler_rel = CONTRACT.MAIN_SCHEDULER_PATH
    parallelization = manifest.get("parallelization") if isinstance(manifest.get("parallelization"), dict) else {}
    if isinstance(parallelization.get("scheduler_path"), str):
        main_scheduler_rel = parallelization["scheduler_path"]
    main_scheduler_defects: list[str] = []
    main_scheduler = validate_scheduler(
        defects=main_scheduler_defects,
        scheduler_path=bundle_dir / main_scheduler_rel,
        expected_path=main_scheduler_rel,
        scheduler_kind="main-branch-pool",
        expected_ids=branch_ids,
        dependencies=branch_dependencies(branches),
        capacity=int(manifest.get("max_active_branch_agents", CONTRACT.MAX_ACTIVE_BRANCH_AGENTS))
        if isinstance(manifest.get("max_active_branch_agents"), int)
        else CONTRACT.MAX_ACTIVE_BRANCH_AGENTS,
        manifest_path=manifest_path,
        require_all_launched=main_status_data is not None and main_status_data.get("status") == "pass",
    )
    if main_scheduler_defects:
        add_issue(
            stale_or_unreconciled,
            code="unreconciled_main_scheduler",
            path=main_scheduler_rel,
            kind="scheduler",
            owner="main",
            message=f"main scheduler has {len(main_scheduler_defects)} validation defect(s)",
        )

    if not main_status_path.exists():
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'assemble_main_status.py'} "
            f"--manifest {manifest_path.as_posix()} --out {main_status_path.as_posix()} --replace"
        )
    if telemetry["telemetry_count"] and (not telemetry["summary_exists"] or any(item["code"].startswith("stale_telemetry") for item in stale_or_unreconciled)):
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'summarize_telemetry.py'} "
            f"--bundle-dir {bundle_dir.as_posix()}"
        )
    next_commands.append(
        f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'validate_main_status.py'} "
        f"--manifest {manifest_path.as_posix()} --status {main_status_path.as_posix()}"
    )

    validation_defects = [
        *[f"main_status: {item}" for item in main_validation_defects],
        *[f"main_scheduler: {item}" for item in main_scheduler_defects],
    ]
    for branch in branch_reports:
        validation_defects.extend(f"{branch['branch_id']}: {item}" for item in branch["validation"]["defects"])
        validation_defects.extend(f"{branch['branch_id']} scheduler: {item}" for item in branch["worker_scheduler"]["validation_defects"])

    hard_issue_count = len(missing_artifacts) + len(stale_or_unreconciled) + len(validation_defects)
    main_status_value = main_status_data.get("status") if isinstance(main_status_data, dict) else None
    status = "pass" if hard_issue_count == 0 and main_status_value == "pass" else "blocked"
    if hard_issue_count == 0 and main_status_value in {"partial", "blocked", "failed"}:
        status = str(main_status_value)

    branch_reuse = {
        branch["branch_id"]: branch["validation"]["status"] == "pass" and branch["status_path"]["exists"]
        for branch in branch_reports
    }
    final_state_status = "pass" if hard_issue_count == 0 else "failed"
    overall_safe_to_reuse = (
        status in {"pass", "partial", "blocked", "failed"}
        and final_state_status == "pass"
        and not missing_artifacts
        and not stale_or_unreconciled
        and not validation_defects
    )
    report = {
        "schema_version": 1,
        "status": status,
        "generated_at": utc_now(),
        "bundle_dir": bundle_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "manifest": {
            "job_id": manifest.get("job_id"),
            "sha256": sha256_file(manifest_path),
            "branch_ids": branch_ids,
        },
        "main_status": {
            "path": "main.status.json",
            "exists": main_status_path.exists(),
            "status": main_status_value,
            "schema_status": "pass" if main_status_data is not None else "missing",
            "runtime_status": main_status_value if main_status_value in RUNTIME_STATUS_VALUES else "missing",
            "resume_action": "reuse_terminal_status" if main_status_data is not None and not main_validation_defects else "assemble_or_repair",
            "validation": {
                "status": "pass" if not main_validation_defects else "failed",
                "defects": main_validation_defects,
            },
        },
        "main_scheduler": main_scheduler,
        "branches": branch_reports,
        "telemetry": telemetry,
        "manifest_path_checks": manifest_checks,
        "missing_artifacts": missing_artifacts,
        "stale_or_unreconciled": stale_or_unreconciled,
        "safe_to_reuse": {
            "overall": overall_safe_to_reuse,
            "branches": branch_reuse,
        },
        "next_commands": sorted(dict.fromkeys(next_commands)),
        "final_state_validation": {
            "status": final_state_status,
            "defects": validation_defects,
            "missing_artifact_count": len(missing_artifacts),
            "stale_or_unreconciled_count": len(stale_or_unreconciled),
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="Path to job.manifest.json.")
    parser.add_argument("--repo-root", help="Repository root used to resolve relative branch worktree paths.")
    parser.add_argument("--output", help="Optional report output path. Defaults to stdout only unless --write is used.")
    parser.add_argument("--write", action="store_true", help="Write orchestration.state.json and resume.report.json in the bundle root.")
    parser.add_argument("--require-pass", action="store_true", help="Return non-zero unless final_state_validation.status is pass.")
    parser.add_argument("--json", action="store_true", help="Print JSON report instead of compact status lines.")
    args = parser.parse_args()

    manifest_path = STATUS_VALIDATION.resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = STATUS_VALIDATION.resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True) if args.repo_root else None
    if args.write:
        materialize_main_status_if_missing(manifest_path)
    report = build_report(manifest_path, repo_root=repo_root)
    if args.write:
        write_json(manifest_path.parent / "orchestration.state.json", report)
        write_json(manifest_path.parent / "resume.report.json", report)
    if args.output:
        output_path = STATUS_VALIDATION.resolve_absolute_path(args.output, "--output", must_exist=False)
        write_json(output_path, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"status={report['status']}")
        print(f"final_state_validation={report['final_state_validation']['status']}")
        for item in report["missing_artifacts"]:
            print(f"missing: {item['path']} {item['message']}")
        for item in report["stale_or_unreconciled"]:
            print(f"unreconciled: {item['path']} {item['message']}")
    if args.require_pass and report["final_state_validation"]["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
