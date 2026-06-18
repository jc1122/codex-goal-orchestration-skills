#!/usr/bin/env python3
"""Reconstruct a goal bundle's resume and final-state surface."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple


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
STALE_ARTIFACT_ROOTS = ("branches/stale", "branches.stale", "reviewers/stale", "reviewers.stale")
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


def read_manifest(path: Path) -> dict[str, Any]:
    """Fail-closed top-level manifest read for the standalone entrypoints.

    ``read_json`` is left bare on purpose (``read_json_or_none`` wraps it with
    ``except Exception``; hardening it to ``SystemExit`` would break that wrapper).
    The direct manifest reads in ``build_report``/``main`` must instead surface a
    clean SystemExit, never a raw JSONDecodeError/ValueError traceback.
    """
    try:
        return read_json(path)
    except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError subclass; OSError = dir/permission
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc


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


def stale_artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".txt", ".log", ".jsonl"}:
        return suffix.removeprefix(".")
    return "artifact"


def infer_stale_reason(root_name: str) -> str:
    if root_name.startswith("branches"):
        return "branch_artifact_superseded_or_archived"
    if root_name.startswith("reviewers"):
        return "reviewer_artifact_superseded_or_archived"
    return "stale_runtime_archive"


def _drop_old_suffix(name: str) -> str:
    for marker in (".old.", ".stale."):
        if marker in name:
            return name.replace(marker, ".", 1)
    return name


def _superseding_parts_from_stale_path(root: Path, path: Path) -> Path | None:
    tail = path.relative_to(root)
    parts = list(tail.parts)
    if not parts:
        return None
    if root.name == "stale" and len(parts) > 1:
        parts = parts[1:]
    parts[-1] = _drop_old_suffix(parts[-1])
    return Path(*parts)


def infer_superseding_artifact(bundle_dir: Path, root_name: str, path: Path) -> str | None:
    root = bundle_dir / root_name
    if root_name.startswith("branches"):
        base_name = "branches"
    elif root_name.startswith("reviewers"):
        base_name = "reviewers"
    else:
        return None
    try:
        relative_parts = _superseding_parts_from_stale_path(root, path)
    except ValueError:
        return None
    if relative_parts is None:
        return None
    candidate = bundle_dir / base_name / relative_parts
    if candidate.is_file():
        return rel_path(bundle_dir, candidate)
    return None


def build_stale_artifact_index(bundle_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    retention_epoch = str(manifest.get("manifest_epoch") or manifest.get("epoch") or "current")
    entries: list[dict[str, Any]] = []
    for root_name in STALE_ARTIFACT_ROOTS:
        root = bundle_dir / root_name
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.name == "stale-artifacts.index.json":
                continue
            superseding_artifact = infer_superseding_artifact(bundle_dir, root_name, path)
            entries.append(
                {
                    "artifact_path": rel_path(bundle_dir, path),
                    "artifact_kind": stale_artifact_kind(path),
                    "original_hash": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                    "stale_reason": infer_stale_reason(root_name),
                    "superseding_artifact": superseding_artifact,
                    "terminal_reason": None
                    if superseding_artifact
                    else f"{infer_stale_reason(root_name)}: no replacement artifact path resolved",
                    "excluded_from_current_evidence": True,
                    "retention_epoch": retention_epoch,
                }
            )
    return {
        "schema_version": 1,
        "kind": "stale-artifacts-index",
        "generated_at": utc_now(),
        "bundle_dir": bundle_dir.as_posix(),
        "retention_epoch": retention_epoch,
        "scan_roots": list(STALE_ARTIFACT_ROOTS),
        "entries": entries,
        "entry_count": len(entries),
    }


def stale_artifact_index_state(
    bundle_dir: Path,
    manifest: dict[str, Any],
    stale_or_unreconciled: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = build_stale_artifact_index(bundle_dir, manifest)
    index_path = bundle_dir / "stale-artifacts.index.json"
    state: dict[str, Any] = {
        "path": "stale-artifacts.index.json",
        "exists": index_path.exists(),
        "entry_count": expected["entry_count"],
        "scan_roots": expected["scan_roots"],
    }
    if expected["entry_count"] == 0:
        return state
    if not index_path.exists():
        add_issue(
            stale_or_unreconciled,
            code="missing_stale_artifact_index",
            path="stale-artifacts.index.json",
            kind="stale_artifact_index",
            owner="main",
            message="stale archive artifacts exist but no bundle-level stale-artifacts.index.json records their exclusion",
        )
        return state
    data, error = read_json_or_none(index_path)
    if data is None:
        add_issue(
            stale_or_unreconciled,
            code="invalid_stale_artifact_index",
            path="stale-artifacts.index.json",
            kind="stale_artifact_index",
            owner="main",
            message=error or "stale artifact index is not a JSON object",
        )
        return state
    expected_entries = expected.get("entries")
    actual_entries = data.get("entries")
    state["indexed_entry_count"] = len(actual_entries) if isinstance(actual_entries, list) else None
    if actual_entries != expected_entries:
        add_issue(
            stale_or_unreconciled,
            code="stale_artifact_index_out_of_date",
            path="stale-artifacts.index.json",
            kind="stale_artifact_index",
            owner="main",
            message="stale-artifacts.index.json does not match current stale archive contents",
        )
    return state


def add_issue(
    target: list[dict[str, Any]], *, code: str, path: Path | str, kind: str, owner: str, message: str
) -> None:
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


def scheduler_dependency_failed_events(scheduler_path: Path) -> dict[str, dict[str, Any]]:
    data, _error = read_json_or_none(scheduler_path)
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for event in data["events"]:
        if not isinstance(event, dict):
            continue
        branch_id = event.get("id")
        if (
            event.get("event") == "blocked"
            and event.get("reason_code") == "dependency_failed"
            and isinstance(branch_id, str)
            and branch_id.strip()
        ):
            result[branch_id] = dict(event)
    return result


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


class _BranchPaths(NamedTuple):
    status_path: Path
    review_path: Path
    prompt_path: Path
    pre_review_gate_path: Path
    worktree_path: Path | None


class _BranchStatusState(NamedTuple):
    status_data: dict[str, Any] | None
    status_error: str | None
    status_value: object
    review_status: object


def _branch_paths(bundle_dir: Path, repo_root: Path | None, branch_id: str, branch: dict[str, Any]) -> _BranchPaths:
    return _BranchPaths(
        status_path=bundle_path(bundle_dir, branch.get("status_path"), f"branches/{branch_id}.status.json"),
        review_path=bundle_path(bundle_dir, branch.get("review_path"), f"branches/{branch_id}.review.json"),
        prompt_path=bundle_path(bundle_dir, branch.get("prompt"), f"branches/{branch_id}.prompt.md"),
        pre_review_gate_path=bundle_path(
            bundle_dir, branch.get("pre_review_gate_path"), f"branches/{branch_id}.pre_review_gate.json"
        ),
        worktree_path=repo_or_bundle_path(bundle_dir, repo_root, branch.get("worktree_path")),
    )


def _branch_status_state(status_path: Path, dependency_failed_terminal: bool) -> _BranchStatusState:
    status_data, status_error = read_json_or_none(status_path) if status_path.exists() else (None, None)
    status_value = status_data.get("status") if isinstance(status_data, dict) else None
    review_status = status_data.get("review_status") if isinstance(status_data, dict) else None
    if status_value is None and dependency_failed_terminal:
        status_value = "blocked"
        review_status = "missing"
    return _BranchStatusState(status_data, status_error, status_value, review_status)


def _record_branch_missing_artifacts(
    *,
    bundle_dir: Path,
    branch_id: str,
    paths: _BranchPaths,
    dependency_failed_terminal: bool,
    missing_artifacts: list[dict[str, Any]],
) -> None:
    if not paths.status_path.exists() and not dependency_failed_terminal:
        add_issue(
            missing_artifacts,
            code="missing_branch_status",
            path=rel_path(bundle_dir, paths.status_path),
            kind="branch_status",
            owner=branch_id,
            message=f"manifest branch {branch_id} has no status artifact",
        )
    if not paths.prompt_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_branch_prompt",
            path=rel_path(bundle_dir, paths.prompt_path),
            kind="branch_prompt",
            owner=branch_id,
            message=f"manifest branch {branch_id} prompt is missing",
        )
    if paths.worktree_path is not None and not paths.worktree_path.exists() and not dependency_failed_terminal:
        add_issue(
            missing_artifacts,
            code="missing_branch_worktree",
            path=paths.worktree_path.as_posix(),
            kind="worktree",
            owner=branch_id,
            message=f"manifest branch {branch_id} worktree is missing",
        )


def _reconcile_branch_review(
    *,
    bundle_dir: Path,
    branch_id: str,
    review_path: Path,
    outputs: list[dict[str, Any]],
    status_value: object,
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
) -> bool:
    branch_needs_reassemble = False
    if outputs and not review_path.exists():
        add_issue(
            stale_or_unreconciled,
            code="unpromoted_review",
            path=rel_path(bundle_dir, review_path),
            kind="review",
            owner=branch_id,
            message=f"reviewer packet output exists for {branch_id} but branch review path is missing",
        )
        branch_needs_reassemble = True
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
            branch_needs_reassemble = True
    if status_value == "pass" and not review_path.exists():
        add_issue(
            missing_artifacts,
            code="missing_pass_review",
            path=rel_path(bundle_dir, review_path),
            kind="review",
            owner=branch_id,
            message=f"passing branch {branch_id} requires a canonical branch review artifact",
        )
    return branch_needs_reassemble


def _branch_validation_defects(
    *,
    bundle_dir: Path,
    branch_id: str,
    branch: dict[str, Any],
    manifest: dict[str, Any],
    manifest_path: Path,
    status_path: Path,
    state: _BranchStatusState,
    worktree_path: Path | None,
    stale_or_unreconciled: list[dict[str, Any]],
) -> list[str]:
    validation_defects: list[str] = []
    if state.status_data is not None:
        validation_defects = BRANCH_VALIDATOR.validate_branch_status(
            state.status_data,
            branch_id=branch_id,
            branch=state.status_data.get("branch")
            if isinstance(state.status_data.get("branch"), str)
            else branch.get("branch_name"),
            worktree=state.status_data.get("worktree")
            if isinstance(state.status_data.get("worktree"), str)
            else (worktree_path.as_posix() if worktree_path else None),
            manifest=manifest,
            manifest_path=manifest_path,
            status_path=status_path,
            allow_archived_manifest_hashes=True,
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
    elif state.status_error:
        validation_defects = [state.status_error]
    return validation_defects


def _branch_worker_scheduler(
    *,
    bundle_dir: Path,
    branch_id: str,
    branch: dict[str, Any],
    manifest_path: Path,
    status_value: object,
    dependency_failed_terminal: bool,
    dependency_failed_event: dict[str, Any] | None,
    stale_or_unreconciled: list[dict[str, Any]],
) -> dict[str, Any]:
    worker_ids, worker_deps = worker_dependencies(branch)
    scheduler_rel = "schedulers/" + f"{branch_id}.worker.scheduler.json"
    worker_parallelism = branch.get("worker_parallelism") if isinstance(branch.get("worker_parallelism"), dict) else {}
    if isinstance(worker_parallelism.get("scheduler_path"), str):
        scheduler_rel = worker_parallelism["scheduler_path"]
    scheduler_defects: list[str] = []
    if dependency_failed_terminal and not (bundle_dir / scheduler_rel).exists():
        scheduler = {
            "path": scheduler_rel,
            "exists": False,
            "validation_status": "pass",
            "validation_defects": [],
            "launched": [],
            "finished": [],
            "closed_or_deferred": [branch_id],
            "active": [],
            "blocked": [branch_id],
            "deferred": [],
            "finished_status": {branch_id: "blocked"},
            "max_observed_active": 0,
            "terminal_reason_code": "dependency_failed",
            "terminal_reason": dependency_failed_event.get("reason")
            if isinstance(dependency_failed_event, dict)
            else None,
        }
    else:
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
    return scheduler


def _append_branch_next_commands(
    *,
    bundle_dir: Path,
    repo_root: Path | None,
    branch_id: str,
    manifest_path: Path,
    status_path: Path,
    worktree_path: Path | None,
    branch_needs_reassemble: bool,
    dependency_failed_terminal: bool,
    next_commands: list[str],
) -> None:
    if branch_needs_reassemble and status_path.exists() and worktree_path is not None:
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-branch-orchestrator' / 'scripts' / 'assemble_branch_status.py'} "
            f"--manifest {manifest_path.as_posix()} --branch-id {branch_id} --worktree {worktree_path.as_posix()} --allow-pass --replace --json"
        )
    if status_path.exists():
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-branch-orchestrator' / 'scripts' / 'validate_branch_status.py'} "
            f"--manifest {manifest_path.as_posix()} --status {status_path.as_posix()} --allow-archived-manifest-hashes"
        )
    elif branch_id and not dependency_failed_terminal:
        canonical_audit_path = bundle_dir / "audit" / "prompt-audit.json"
        if canonical_audit_path.exists():
            repo_root_arg = repo_root.as_posix() if repo_root is not None else "<repo-root>"
            next_commands.append(
                f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'render_branch_worktree_commands.py'} "
                f"--manifest {manifest_path.as_posix()} --repo-root {repo_root_arg} --audit {canonical_audit_path.as_posix()} --branch {branch_id}"
            )
        else:
            next_commands.append(
                f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'run_prompt_audit_phase.py'} "
                f"--manifest {manifest_path.as_posix()} --repo-root "
                f"{repo_root.as_posix() if repo_root is not None else '<repo-root>'} "
                f"--audit-dir {(bundle_dir / 'audit').as_posix()} --deterministic --require-pass"
            )


def _branch_worker_summaries(bundle_dir: Path, branch: dict[str, Any]) -> list[dict[str, Any]]:
    workers = []
    if isinstance(branch.get("work_items"), list):
        for item in branch["work_items"]:
            if isinstance(item, dict) and isinstance(item.get("packet_id"), str):
                workers.append(
                    packet_artifact_summary(bundle_dir, str(item["packet_id"]), str(item.get("worker_type", "worker")))
                )
    return workers


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
    dependency_failed_events: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    branch_id = str(branch.get("id", ""))
    dependency_failed_event = dependency_failed_events.get(branch_id)
    dependency_failed_terminal = dependency_failed_event is not None
    paths = _branch_paths(bundle_dir, repo_root, branch_id, branch)
    state = _branch_status_state(paths.status_path, dependency_failed_terminal)
    status_value = state.status_value
    review_status = state.review_status
    _record_branch_missing_artifacts(
        bundle_dir=bundle_dir,
        branch_id=branch_id,
        paths=paths,
        dependency_failed_terminal=dependency_failed_terminal,
        missing_artifacts=missing_artifacts,
    )
    outputs = reviewer_outputs(bundle_dir, branch_id)
    branch_needs_reassemble = _reconcile_branch_review(
        bundle_dir=bundle_dir,
        branch_id=branch_id,
        review_path=paths.review_path,
        outputs=outputs,
        status_value=status_value,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
    )

    validation_defects = _branch_validation_defects(
        bundle_dir=bundle_dir,
        branch_id=branch_id,
        branch=branch,
        manifest=manifest,
        manifest_path=manifest_path,
        status_path=paths.status_path,
        state=state,
        worktree_path=paths.worktree_path,
        stale_or_unreconciled=stale_or_unreconciled,
    )

    scheduler = _branch_worker_scheduler(
        bundle_dir=bundle_dir,
        branch_id=branch_id,
        branch=branch,
        manifest_path=manifest_path,
        status_value=status_value,
        dependency_failed_terminal=dependency_failed_terminal,
        dependency_failed_event=dependency_failed_event,
        stale_or_unreconciled=stale_or_unreconciled,
    )

    _append_branch_next_commands(
        bundle_dir=bundle_dir,
        repo_root=repo_root,
        branch_id=branch_id,
        manifest_path=manifest_path,
        status_path=paths.status_path,
        worktree_path=paths.worktree_path,
        branch_needs_reassemble=branch_needs_reassemble,
        dependency_failed_terminal=dependency_failed_terminal,
        next_commands=next_commands,
    )

    workers = _branch_worker_summaries(bundle_dir, branch)

    return {
        "branch_id": branch_id,
        "branch_name": branch.get("branch_name"),
        "status": status_value,
        "schema_status": "pass" if state.status_data is not None else "missing",
        "runtime_status": status_value if status_value in RUNTIME_STATUS_VALUES else "missing",
        "review_status": review_status if review_status in REVIEW_STATUS_VALUES else "missing",
        "resume_action": "reuse_terminal_status"
        if state.status_data is not None and not validation_defects
        else "repair_or_reassemble",
        "status_path": artifact_ref(bundle_dir, paths.status_path),
        "review_path": artifact_ref(bundle_dir, paths.review_path),
        "prompt_path": artifact_ref(bundle_dir, paths.prompt_path),
        "pre_review_gate_path": artifact_ref(bundle_dir, paths.pre_review_gate_path),
        "worktree_path": paths.worktree_path.as_posix() if paths.worktree_path else None,
        "worktree_exists": paths.worktree_path.exists() if paths.worktree_path else None,
        "reviewer_outputs": outputs,
        "dependency_failed_terminal": dependency_failed_terminal,
        "dependency_failed_event": dependency_failed_event,
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


def branch_state_counts(branch_reports: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in sorted(RUNTIME_STATUS_VALUES)}
    counts["missing"] = 0
    counts["invalid"] = 0
    for branch in branch_reports:
        runtime_status = branch.get("runtime_status")
        validation = branch.get("validation") if isinstance(branch.get("validation"), dict) else {}
        if runtime_status in RUNTIME_STATUS_VALUES:
            counts[str(runtime_status)] += 1
        else:
            counts["missing"] += 1
        if validation.get("status") != "pass":
            counts["invalid"] += 1
    return counts


def string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def manifest_branches_by_id(branches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        branch_id: branch
        for branch in branches
        if isinstance((branch_id := branch.get("id")), str) and branch_id.strip()
    }


def branch_report_successful(branch: dict[str, Any]) -> bool:
    validation = branch.get("validation") if isinstance(branch.get("validation"), dict) else {}
    return (
        branch.get("runtime_status") == "pass"
        and branch.get("review_status") == "mergeable"
        and validation.get("status") == "pass"
    )


def manifest_branch_declares_recovery(manifest_branch: dict[str, Any] | None, target_branch_id: str) -> bool:
    if not isinstance(manifest_branch, dict):
        return False
    return any(target_branch_id in string_list(manifest_branch.get(field)) for field in ["recovers_from", "supersedes"])


def recovered_branch_ids(branches: list[dict[str, Any]], branch_reports: list[dict[str, Any]]) -> set[str]:
    manifest_by_id = manifest_branches_by_id(branches)
    successful_branch_ids = {
        str(branch.get("branch_id"))
        for branch in branch_reports
        if isinstance(branch.get("branch_id"), str) and branch_report_successful(branch)
    }
    recovered: set[str] = set()
    for branch in branch_reports:
        target_branch_id = branch.get("branch_id")
        if not isinstance(target_branch_id, str) or branch.get("runtime_status") not in {
            "partial",
            "blocked",
            "failed",
        }:
            continue
        if any(
            manifest_branch_declares_recovery(manifest_by_id.get(recovery_id), target_branch_id)
            for recovery_id in successful_branch_ids
        ):
            recovered.add(target_branch_id)
    return recovered


def choose_resume_action(
    *,
    overall_safe_to_reuse: bool,
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    validation_defects: list[str],
) -> str:
    if overall_safe_to_reuse:
        return "reuse_terminal_status"
    missing_codes = {str(item.get("code")) for item in missing_artifacts}
    if {"missing_branch_status", "missing_branch_worktree"} & missing_codes:
        return "launch_or_resume_branches"
    if stale_or_unreconciled:
        return "repair_or_reassemble"
    if validation_defects:
        return "repair_or_revalidate"
    return "assemble_or_validate_final"


def audit_allows_branch_launch(bundle_dir: Path) -> bool:
    for rel_path in ["audit/prompt-audit-phase.json", "audit/prompt-audit.json"]:
        data, _error = read_json_or_none(bundle_dir / rel_path)
        if not isinstance(data, dict):
            continue
        status = data.get("status")
        can_start = data.get("can_start")
        if status == "pass" and can_start is not False:
            return True
    return False


def pre_branch_dispatch_phase(bundle_dir: Path, branch_reports: list[dict[str, Any]]) -> bool:
    if not audit_allows_branch_launch(bundle_dir):
        return False
    if any(branch.get("status_path", {}).get("exists") is True for branch in branch_reports):
        return False
    if any(branch.get("worktree_exists") is True for branch in branch_reports):
        return False
    for branch in branch_reports:
        workers = branch.get("workers")
        if not isinstance(workers, list):
            continue
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            if worker.get("packet_dir_exists") or worker.get("output_exists") or worker.get("launcher_state_exists"):
                return False
    return True


def scheduler_has_events(scheduler: dict[str, Any]) -> bool:
    events = scheduler.get("events")
    if isinstance(events, list) and bool(events):
        return True
    for key in ("active", "launched", "finished", "closed_or_deferred", "blocked", "deferred"):
        values = scheduler.get(key)
        if isinstance(values, list) and values:
            return True
    return False


def suppress_pre_dispatch_issue(item: dict[str, Any]) -> bool:
    return str(item.get("code")) in {
        "missing_main_status",
        "missing_branch_status",
        "missing_branch_worktree",
        "unreconciled_worker_scheduler",
        "unreconciled_main_scheduler",
        "invalid_main_status",
    }


def compact_issue_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item.get("code"),
        "owner": item.get("owner"),
        "kind": item.get("kind"),
        "path": item.get("path"),
        "message": item.get("message"),
    }


def scheduler_recovery_closeout_commands(
    manifest_path: Path, branch_id: str, *, reason_code: str, reason: str
) -> list[str]:
    scheduler_tick = SKILLS_ROOT / "goal-main-orchestrator" / "scripts" / "scheduler_tick.py"
    base = (
        f"python3 {scheduler_tick} --manifest {manifest_path.as_posix()} --scope main "
        "--runtime-ref goal-main-orchestrator"
    )
    quoted_reason = CONTRACT.shell_quote(reason)
    return [
        f"{base} --blocked {branch_id} --reason-code {reason_code} --reason {quoted_reason}",
        f"{base} --finish {branch_id} --status blocked",
        f"{base} --close {branch_id}",
    ]


def stale_active_branch_ids(main_scheduler: dict[str, Any], branch_reports: list[dict[str, Any]]) -> list[str]:
    branch_by_id = {
        str(branch.get("branch_id")): branch for branch in branch_reports if isinstance(branch.get("branch_id"), str)
    }
    result: list[str] = []
    raw_active = main_scheduler.get("active")
    for branch_id in raw_active if isinstance(raw_active, list) else []:
        if not isinstance(branch_id, str):
            continue
        branch = branch_by_id.get(branch_id)
        if not isinstance(branch, dict):
            continue
        status_path = branch.get("status_path") if isinstance(branch.get("status_path"), dict) else {}
        if status_path.get("exists") is True:
            continue
        result.append(branch_id)
    return result


class _BundleScan(NamedTuple):
    manifest_checks: list[dict[str, Any]]
    telemetry: dict[str, Any]
    stale_index: dict[str, Any]
    branch_ids: list[str]
    main_scheduler_rel: str
    branch_reports: list[dict[str, Any]]
    pre_dispatch: bool


class _MainStatusState(NamedTuple):
    path: Path
    data: dict[str, Any] | None
    error: str | None
    validation_defects: list[str]


class _SchedulerState(NamedTuple):
    main_scheduler: dict[str, Any]
    main_scheduler_defects: list[str]
    pre_dispatch: bool


class _ResumeState(NamedTuple):
    status: str
    main_status_value: object
    final_state_status: str
    overall_safe_to_reuse: bool
    resume_action: str
    hard_issue_count: int
    branch_reuse: dict[str, bool]
    effective_missing_artifacts: list[dict[str, Any]]
    effective_stale_or_unreconciled: list[dict[str, Any]]


def _scan_bundle(
    *,
    manifest_path: Path,
    bundle_dir: Path,
    repo_root: Path | None,
    manifest: dict[str, Any],
    branches: list[dict[str, Any]],
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    next_commands: list[str],
) -> _BundleScan:
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
    stale_index = stale_artifact_index_state(
        bundle_dir,
        manifest,
        stale_or_unreconciled,
    )
    branch_ids = [str(branch.get("id")) for branch in branches if isinstance(branch.get("id"), str)]
    main_scheduler_rel = CONTRACT.MAIN_SCHEDULER_PATH
    parallelization = manifest.get("parallelization") if isinstance(manifest.get("parallelization"), dict) else {}
    if isinstance(parallelization.get("scheduler_path"), str):
        main_scheduler_rel = parallelization["scheduler_path"]
    dependency_failed_events = scheduler_dependency_failed_events(bundle_dir / main_scheduler_rel)
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
            dependency_failed_events=dependency_failed_events,
        )
        for branch in branches
    ]
    pre_dispatch = pre_branch_dispatch_phase(bundle_dir, branch_reports)
    return _BundleScan(
        manifest_checks=manifest_checks,
        telemetry=telemetry,
        stale_index=stale_index,
        branch_ids=branch_ids,
        main_scheduler_rel=main_scheduler_rel,
        branch_reports=branch_reports,
        pre_dispatch=pre_dispatch,
    )


def _evaluate_main_status(
    *,
    bundle_dir: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    pre_dispatch: bool,
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
) -> _MainStatusState:
    main_status_path = bundle_dir / "main.status.json"
    main_status_data, main_status_error = (
        read_json_or_none(main_status_path) if main_status_path.exists() else (None, None)
    )
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
        if main_validation_defects and not pre_dispatch:
            add_issue(
                stale_or_unreconciled,
                code="invalid_main_status",
                path="main.status.json",
                kind="main_status",
                owner="main",
                message=f"main status validator failed with {len(main_validation_defects)} defect(s)",
            )
    return _MainStatusState(main_status_path, main_status_data, main_status_error, main_validation_defects)


def _evaluate_main_scheduler(
    *,
    bundle_dir: Path,
    manifest: dict[str, Any],
    manifest_path: Path,
    branches: list[dict[str, Any]],
    branch_ids: list[str],
    main_scheduler_rel: str,
    main_status_data: dict[str, Any] | None,
    pre_dispatch: bool,
    stale_or_unreconciled: list[dict[str, Any]],
) -> _SchedulerState:
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
    if pre_dispatch and scheduler_has_events(main_scheduler):
        pre_dispatch = False
    if main_scheduler_defects and not pre_dispatch:
        add_issue(
            stale_or_unreconciled,
            code="unreconciled_main_scheduler",
            path=main_scheduler_rel,
            kind="scheduler",
            owner="main",
            message=f"main scheduler has {len(main_scheduler_defects)} validation defect(s)",
        )
    return _SchedulerState(main_scheduler, main_scheduler_defects, pre_dispatch)


def _branch_recovery_commands(
    *,
    manifest_path: Path,
    main_scheduler: dict[str, Any],
    main_scheduler_rel: str,
    branch_reports: list[dict[str, Any]],
    pre_dispatch: bool,
    stale_or_unreconciled: list[dict[str, Any]],
) -> list[str]:
    recovery_commands: list[str] = []
    if not pre_dispatch:
        for branch_id in stale_active_branch_ids(main_scheduler, branch_reports):
            add_issue(
                stale_or_unreconciled,
                code="stale_active_branch_launch",
                path=main_scheduler_rel,
                kind="scheduler",
                owner=branch_id,
                message=(
                    f"branch {branch_id} is scheduler-active but no terminal branch status exists; "
                    "close stale launch evidence before relaunching"
                ),
            )
            recovery_commands.extend(
                scheduler_recovery_closeout_commands(
                    manifest_path,
                    branch_id,
                    reason_code="stale_active",
                    reason=(
                        f"{branch_id} branch launch was still scheduler-active after resume, "
                        "but no terminal branch artifact or live branch process was available."
                    ),
                )
            )
    return recovery_commands


def _append_main_next_commands(
    *,
    bundle_dir: Path,
    manifest_path: Path,
    main_status_path: Path,
    telemetry: dict[str, Any],
    stale_or_unreconciled: list[dict[str, Any]],
    next_commands: list[str],
) -> None:
    if not main_status_path.exists():
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'assemble_main_status.py'} "
            f"--manifest {manifest_path.as_posix()} --out {main_status_path.as_posix()} --replace"
        )
    if telemetry["telemetry_count"] and (
        not telemetry["summary_exists"]
        or any(item["code"].startswith("stale_telemetry") for item in stale_or_unreconciled)
    ):
        next_commands.append(
            f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'summarize_telemetry.py'} "
            f"--bundle-dir {bundle_dir.as_posix()}"
        )
    next_commands.append(
        f"python3 {SKILLS_ROOT / 'goal-main-orchestrator' / 'scripts' / 'validate_main_status.py'} "
        f"--manifest {manifest_path.as_posix()} --status {main_status_path.as_posix()}"
    )


def _aggregate_validation_defects(
    *,
    main_validation_defects: list[str],
    main_scheduler_defects: list[str],
    branch_reports: list[dict[str, Any]],
) -> list[str]:
    validation_defects = [
        *[f"main_status: {item}" for item in main_validation_defects],
        *[f"main_scheduler: {item}" for item in main_scheduler_defects],
    ]
    for branch in branch_reports:
        validation_defects.extend(f"{branch['branch_id']}: {item}" for item in branch["validation"]["defects"])
        validation_defects.extend(
            f"{branch['branch_id']} scheduler: {item}" for item in branch["worker_scheduler"]["validation_defects"]
        )
    return validation_defects


def _resume_status(pre_dispatch: bool, hard_issue_count: int, main_status_value: object) -> str:
    status = (
        "incomplete" if pre_dispatch else "pass" if hard_issue_count == 0 and main_status_value == "pass" else "blocked"
    )
    if not pre_dispatch and hard_issue_count == 0 and main_status_value in {"partial", "blocked", "failed"}:
        status = str(main_status_value)
    return status


def _branch_reuse_map(
    branch_reports: list[dict[str, Any]],
    effective_stale_or_unreconciled: list[dict[str, Any]],
) -> dict[str, bool]:
    branch_reuse = {
        branch["branch_id"]: branch["validation"]["status"] == "pass" and branch["status_path"]["exists"]
        for branch in branch_reports
    }
    branch_ids = set(branch_reuse)
    stale_branch_owners = {
        str(item.get("owner"))
        for item in effective_stale_or_unreconciled
        if isinstance(item.get("owner"), str) and item.get("owner") in branch_ids
    }
    return {
        branch_id: reusable and branch_id not in stale_branch_owners for branch_id, reusable in branch_reuse.items()
    }


def _compute_resume_state(
    *,
    pre_dispatch: bool,
    main_status_data: dict[str, Any] | None,
    branch_reports: list[dict[str, Any]],
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    validation_defects: list[str],
) -> _ResumeState:
    effective_missing_artifacts = [
        item for item in missing_artifacts if not (pre_dispatch and suppress_pre_dispatch_issue(item))
    ]
    effective_stale_or_unreconciled = [
        item for item in stale_or_unreconciled if not (pre_dispatch and suppress_pre_dispatch_issue(item))
    ]
    effective_validation_defects = [] if pre_dispatch else validation_defects
    hard_issue_count = (
        len(effective_missing_artifacts) + len(effective_stale_or_unreconciled) + len(effective_validation_defects)
    )
    main_status_value = main_status_data.get("status") if isinstance(main_status_data, dict) else None
    status = _resume_status(pre_dispatch, hard_issue_count, main_status_value)
    branch_reuse = _branch_reuse_map(branch_reports, effective_stale_or_unreconciled)
    final_state_status = "incomplete" if pre_dispatch else "pass" if hard_issue_count == 0 else "failed"
    overall_safe_to_reuse = (
        status in {"pass", "partial", "blocked", "failed"}
        and final_state_status == "pass"
        and not effective_missing_artifacts
        and not effective_stale_or_unreconciled
        and not effective_validation_defects
    )
    resume_action = choose_resume_action(
        overall_safe_to_reuse=overall_safe_to_reuse,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
        validation_defects=validation_defects,
    )
    return _ResumeState(
        status=status,
        main_status_value=main_status_value,
        final_state_status=final_state_status,
        overall_safe_to_reuse=overall_safe_to_reuse,
        resume_action=resume_action,
        hard_issue_count=hard_issue_count,
        branch_reuse=branch_reuse,
        effective_missing_artifacts=effective_missing_artifacts,
        effective_stale_or_unreconciled=effective_stale_or_unreconciled,
    )


def _blocked_work_remaining(
    *,
    branch_reports: list[dict[str, Any]],
    blocked_work: list[dict[str, Any]],
    blocked_branches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        *blocked_work,
        *[
            {
                "code": "terminal_branch_nonpass",
                "owner": item["branch_id"],
                "kind": "branch_status",
                "path": next(
                    (
                        branch.get("status_path", {}).get("path")
                        for branch in branch_reports
                        if branch.get("branch_id") == item["branch_id"]
                    ),
                    None,
                ),
                "message": f"branch {item['branch_id']} runtime_status is {item['runtime_status']}",
            }
            for item in blocked_branches
        ],
    ]


class _CompletionState(NamedTuple):
    terminal_branch_ids: list[str]
    recovered_ids: set[str]
    blocked_branches: list[dict[str, Any]]
    missing_branch_ids: list[str]
    safe_branch_ids: list[str]
    blocked_work: list[dict[str, Any]]
    pending_work: list[dict[str, Any]]
    blocked_work_remaining: list[dict[str, Any]]
    goal_complete: bool


def _derive_completion_state(
    *,
    status: str,
    final_state_status: str,
    pre_dispatch: bool,
    branches: list[dict[str, Any]],
    branch_reports: list[dict[str, Any]],
    branch_reuse: dict[str, bool],
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    effective_missing_artifacts: list[dict[str, Any]],
    effective_stale_or_unreconciled: list[dict[str, Any]],
) -> _CompletionState:
    terminal_branch_ids = [
        branch["branch_id"]
        for branch in branch_reports
        if branch.get("runtime_status") in RUNTIME_STATUS_VALUES and branch.get("status_path", {}).get("exists") is True
    ]
    recovered_ids = recovered_branch_ids(branches, branch_reports)
    blocked_branches = [
        {
            "branch_id": branch["branch_id"],
            "runtime_status": branch.get("runtime_status"),
            "review_status": branch.get("review_status"),
            "validation_status": branch.get("validation", {}).get("status"),
        }
        for branch in branch_reports
        if branch.get("runtime_status") in {"partial", "blocked", "failed"}
        and branch.get("branch_id") not in recovered_ids
    ]
    missing_branch_ids = [
        branch["branch_id"]
        for branch in branch_reports
        if branch.get("runtime_status") == "missing" or branch.get("status_path", {}).get("exists") is not True
    ]
    safe_branch_ids = [branch_id for branch_id, reusable in branch_reuse.items() if reusable]
    blocked_work = [
        compact_issue_ref(item) for item in [*effective_missing_artifacts, *effective_stale_or_unreconciled]
    ]
    pending_work = (
        [
            compact_issue_ref(item)
            for item in [*missing_artifacts, *stale_or_unreconciled]
            if suppress_pre_dispatch_issue(item)
        ]
        if pre_dispatch
        else []
    )
    blocked_work_remaining = _blocked_work_remaining(
        branch_reports=branch_reports,
        blocked_work=blocked_work,
        blocked_branches=blocked_branches,
    )
    goal_complete = status == "pass" and final_state_status == "pass" and not blocked_work_remaining
    return _CompletionState(
        terminal_branch_ids=terminal_branch_ids,
        recovered_ids=recovered_ids,
        blocked_branches=blocked_branches,
        missing_branch_ids=missing_branch_ids,
        safe_branch_ids=safe_branch_ids,
        blocked_work=blocked_work,
        pending_work=pending_work,
        blocked_work_remaining=blocked_work_remaining,
        goal_complete=goal_complete,
    )


def _assemble_report(
    *,
    bundle_dir: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    scan: _BundleScan,
    main_status_path: Path,
    main_status_data: dict[str, Any] | None,
    main_status_value: object,
    main_validation_defects: list[str],
    main_scheduler: dict[str, Any],
    branch_reports: list[dict[str, Any]],
    resume: _ResumeState,
    completion: _CompletionState,
    pre_dispatch: bool,
    validation_defects: list[str],
    missing_artifacts: list[dict[str, Any]],
    stale_or_unreconciled: list[dict[str, Any]],
    recovery_commands: list[str],
    next_commands: list[str],
) -> dict[str, Any]:
    status = resume.status
    final_state_status = resume.final_state_status
    overall_safe_to_reuse = resume.overall_safe_to_reuse
    return {
        "schema_version": 1,
        "status": status,
        "schema_status": final_state_status,
        "runtime_status": status,
        "dod_status": "pass" if status == "pass" and final_state_status == "pass" else "incomplete",
        "review_status": (
            main_status_data.get("review_status")
            if isinstance(main_status_data, dict) and isinstance(main_status_data.get("review_status"), str)
            else "missing"
        ),
        "resume_action": resume.resume_action,
        "execution_phase": "pre_branch_dispatch" if pre_dispatch else "runtime_or_finalization",
        "artifact_reuse_safe": overall_safe_to_reuse,
        "goal_complete": completion.goal_complete,
        "blocked_branches": completion.blocked_branches,
        "recovered_branches": sorted(completion.recovered_ids),
        "blocked_work_remaining": completion.blocked_work_remaining,
        "pending_work": completion.pending_work,
        "next_required_action": resume.resume_action,
        "generated_at": utc_now(),
        "bundle_dir": bundle_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "manifest": {
            "job_id": manifest.get("job_id"),
            "sha256": sha256_file(manifest_path),
            "branch_ids": scan.branch_ids,
        },
        "main_status": {
            "path": "main.status.json",
            "exists": main_status_path.exists(),
            "status": main_status_value,
            "schema_status": "pass" if main_status_data is not None else "missing",
            "runtime_status": main_status_value if main_status_value in RUNTIME_STATUS_VALUES else "missing",
            "resume_action": "reuse_terminal_status"
            if main_status_data is not None and not main_validation_defects
            else "assemble_or_repair",
            "validation": {
                "status": "pass" if not main_validation_defects else "failed",
                "defects": main_validation_defects,
            },
        },
        "main_scheduler": main_scheduler,
        "branches": branch_reports,
        "current_state": {
            "branch_counts": branch_state_counts(branch_reports),
            "terminal_branch_ids": completion.terminal_branch_ids,
            "missing_branch_ids": completion.missing_branch_ids,
            "safe_to_reuse_branch_ids": completion.safe_branch_ids,
            "blocked_work": completion.blocked_work,
            "pending_work": completion.pending_work,
            "main_status_exists": main_status_path.exists(),
            "main_scheduler_status": main_scheduler.get("validation_status"),
            "telemetry_summary_exists": scan.telemetry.get("summary_exists"),
        },
        "telemetry": scan.telemetry,
        "stale_artifact_index": scan.stale_index,
        "manifest_path_checks": scan.manifest_checks,
        "missing_artifacts": missing_artifacts,
        "stale_or_unreconciled": stale_or_unreconciled,
        "safe_to_reuse": {
            "overall": overall_safe_to_reuse,
            "branches": resume.branch_reuse,
            "interpretation": "artifact reuse safety only; inspect goal_complete and blocked_work_remaining for completion state",
        },
        "next_commands": list(dict.fromkeys([*recovery_commands, *next_commands])),
        "final_state_validation": {
            "status": final_state_status,
            "defects": [] if pre_dispatch else validation_defects,
            "deferred_defects": validation_defects if pre_dispatch else [],
            "missing_artifact_count": len(missing_artifacts),
            "stale_or_unreconciled_count": len(stale_or_unreconciled),
        },
    }


def build_report(manifest_path: Path, *, repo_root: Path | None) -> dict[str, Any]:
    bundle_dir = manifest_path.parent
    manifest = read_manifest(manifest_path)
    branches = branch_entries(manifest)
    missing_artifacts: list[dict[str, Any]] = []
    stale_or_unreconciled: list[dict[str, Any]] = []
    next_commands: list[str] = []
    scan = _scan_bundle(
        manifest_path=manifest_path,
        bundle_dir=bundle_dir,
        repo_root=repo_root,
        manifest=manifest,
        branches=branches,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
        next_commands=next_commands,
    )
    branch_reports = scan.branch_reports
    pre_dispatch = scan.pre_dispatch

    main_status = _evaluate_main_status(
        bundle_dir=bundle_dir,
        manifest=manifest,
        manifest_path=manifest_path,
        pre_dispatch=pre_dispatch,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
    )
    main_status_path = main_status.path
    main_status_data = main_status.data
    main_validation_defects = main_status.validation_defects

    scheduler_state = _evaluate_main_scheduler(
        bundle_dir=bundle_dir,
        manifest=manifest,
        manifest_path=manifest_path,
        branches=branches,
        branch_ids=scan.branch_ids,
        main_scheduler_rel=scan.main_scheduler_rel,
        main_status_data=main_status_data,
        pre_dispatch=pre_dispatch,
        stale_or_unreconciled=stale_or_unreconciled,
    )
    main_scheduler = scheduler_state.main_scheduler
    main_scheduler_defects = scheduler_state.main_scheduler_defects
    pre_dispatch = scheduler_state.pre_dispatch

    recovery_commands = _branch_recovery_commands(
        manifest_path=manifest_path,
        main_scheduler=main_scheduler,
        main_scheduler_rel=scan.main_scheduler_rel,
        branch_reports=branch_reports,
        pre_dispatch=pre_dispatch,
        stale_or_unreconciled=stale_or_unreconciled,
    )

    _append_main_next_commands(
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        main_status_path=main_status_path,
        telemetry=scan.telemetry,
        stale_or_unreconciled=stale_or_unreconciled,
        next_commands=next_commands,
    )

    validation_defects = _aggregate_validation_defects(
        main_validation_defects=main_validation_defects,
        main_scheduler_defects=main_scheduler_defects,
        branch_reports=branch_reports,
    )

    resume = _compute_resume_state(
        pre_dispatch=pre_dispatch,
        main_status_data=main_status_data,
        branch_reports=branch_reports,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
        validation_defects=validation_defects,
    )
    completion = _derive_completion_state(
        status=resume.status,
        final_state_status=resume.final_state_status,
        pre_dispatch=pre_dispatch,
        branches=branches,
        branch_reports=branch_reports,
        branch_reuse=resume.branch_reuse,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
        effective_missing_artifacts=resume.effective_missing_artifacts,
        effective_stale_or_unreconciled=resume.effective_stale_or_unreconciled,
    )
    return _assemble_report(
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        scan=scan,
        main_status_path=main_status_path,
        main_status_data=main_status_data,
        main_status_value=resume.main_status_value,
        main_validation_defects=main_validation_defects,
        main_scheduler=main_scheduler,
        branch_reports=branch_reports,
        resume=resume,
        completion=completion,
        pre_dispatch=pre_dispatch,
        validation_defects=validation_defects,
        missing_artifacts=missing_artifacts,
        stale_or_unreconciled=stale_or_unreconciled,
        recovery_commands=recovery_commands,
        next_commands=next_commands,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, help="Path to job.manifest.json.")
    parser.add_argument("--repo-root", help="Repository root used to resolve relative branch worktree paths.")
    parser.add_argument("--output", help="Optional report output path. Defaults to stdout only unless --write is used.")
    parser.add_argument(
        "--write", action="store_true", help="Write orchestration.state.json and resume.report.json in the bundle root."
    )
    parser.add_argument(
        "--require-pass", action="store_true", help="Return non-zero unless final_state_validation.status is pass."
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report instead of compact status lines.")
    args = parser.parse_args()

    manifest_path = STATUS_VALIDATION.resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = (
        STATUS_VALIDATION.resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
        if args.repo_root
        else None
    )
    if args.write:
        manifest = read_manifest(manifest_path)
        write_json(
            manifest_path.parent / "stale-artifacts.index.json",
            build_stale_artifact_index(manifest_path.parent, manifest),
        )
    report = build_report(manifest_path, repo_root=repo_root)
    if args.write and report.get("execution_phase") != "pre_branch_dispatch":
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
