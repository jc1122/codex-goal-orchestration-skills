#!/usr/bin/env python3
"""Shared helpers for goal-plan-amender scripts."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, path.parent.as_posix())
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(path.parent.as_posix())
        except ValueError:
            pass
    return module


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]
SHARED_SCRIPTS = SKILLS_ROOT / "_goal_shared" / "scripts"
PREFLIGHT_SCRIPTS = SKILLS_ROOT / "goal-preflight" / "scripts"

CONTRACT = _load_module("goal_shared_orchestration_contract", SHARED_SCRIPTS / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")
PREFLIGHT = _load_module("goal_preflight_create_goal_bundle", PREFLIGHT_SCRIPTS / "create_goal_bundle.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
require_safe_id = PATH_RULES.require_safe_id
require_safe_label = PATH_RULES.require_safe_label
require_branch_name = PATH_RULES.require_branch_name
relative_path_defect = PATH_RULES.relative_path_defect
safe_branch_name = PATH_RULES.safe_branch_name

PROTECTED_BRANCH_KEYS = (
    "id",
    "wave",
    "prompt",
    "branch_name",
    "worktree_path",
    "status_path",
    "review_path",
    "pre_review_gate_path",
    "depends_on",
    "owned_paths",
    "work_items",
    "max_active_worker_packets",
    "worker_parallelism",
)
NONPASS_TERMINAL_STATUSES = {"partial", "blocked", "failed"}


def json_text(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def load_json_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(data), encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_sha256(data: object) -> str:
    return sha256_text(json_text(data))


def branch_map(manifest: dict) -> dict[str, dict]:
    branches = manifest.get("branches", [])
    if not isinstance(branches, list):
        return {}
    result: dict[str, dict] = {}
    for branch in branches:
        if isinstance(branch, dict) and isinstance(branch.get("id"), str):
            result[branch["id"]] = branch
    return result


def branch_index(branches: list[dict], branch_id: str) -> int | None:
    for index, branch in enumerate(branches):
        if isinstance(branch, dict) and branch.get("id") == branch_id:
            return index
    return None


def branch_brief_from_manifest(branch: dict) -> dict:
    result = copy.deepcopy(branch)
    worker_parallelism = result.get("worker_parallelism")
    if isinstance(worker_parallelism, dict):
        result.setdefault("worker_serial_reasons", worker_parallelism.get("serial_reasons", []))
        result.setdefault("worker_parallelization_rationale", worker_parallelism.get("parallelization_rationale", ""))
    return result


def manifest_to_brief(manifest: dict) -> dict:
    parallelization = manifest.get("parallelization", {})
    if not isinstance(parallelization, dict):
        parallelization = {}
    return {
        "job_id": manifest.get("job_id"),
        "base_ref": manifest.get("base_ref", "main"),
        "artifact_policy": manifest.get("artifact_policy", ""),
        "cleanup_policy": manifest.get("cleanup_policy", ""),
        "max_active_branch_agents": manifest.get("max_active_branch_agents", CONTRACT.MAX_ACTIVE_BRANCH_AGENTS),
        "serial_reasons": parallelization.get("serial_reasons", []),
        "parallelization_rationale": parallelization.get("parallelization_rationale", ""),
        "preflight_lite_advice": manifest.get("preflight_lite_advice", []),
        "branches": [branch_brief_from_manifest(branch) for branch in manifest.get("branches", []) if isinstance(branch, dict)],
    }


def normalize_candidate_manifest(manifest: dict) -> tuple[dict, dict]:
    brief = PREFLIGHT.normalize_brief(manifest_to_brief(manifest))
    normalized = PREFLIGHT.manifest_from_normalized_brief(brief)
    for key in ["amendment_history", "obsolete_branches"]:
        if key in manifest:
            normalized[key] = copy.deepcopy(manifest[key])
    return normalized, brief


def scheduler_state(manifest_path: Path, manifest: dict) -> tuple[set[str], dict[str, str]]:
    parallelization = manifest.get("parallelization", {})
    scheduler_path = parallelization.get("scheduler_path") if isinstance(parallelization, dict) else CONTRACT.MAIN_SCHEDULER_PATH
    if not isinstance(scheduler_path, str) or relative_path_defect(scheduler_path, "scheduler_path"):
        raise ValueError("manifest scheduler_path is unsafe; refusing to infer protected branch state")
    ledger_path = manifest_path.parent / scheduler_path
    if not ledger_path.exists():
        return set(), {}
    try:
        ledger = load_json_object(ledger_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"could not read scheduler ledger for protected branch inference: {ledger_path}: {exc}") from exc
    active: set[str] = set()
    finished_status: dict[str, str] = {}
    terminal: dict[str, str] = {}
    events = ledger.get("events", [])
    if not isinstance(events, list):
        raise ValueError(f"scheduler ledger events must be an array for protected branch inference: {ledger_path}")
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str):
            continue
        item_id = event["id"]
        name = event.get("event")
        if name == "launch":
            active.add(item_id)
            terminal.pop(item_id, None)
        elif name == "finish" and event.get("status") in CONTRACT.SCHEDULER_TERMINAL_STATUSES:
            finished_status[item_id] = str(event["status"])
        elif name == "close":
            active.discard(item_id)
            if item_id in finished_status:
                terminal[item_id] = finished_status[item_id]
    return active, terminal


def status_file_terminal_state(manifest_path: Path, manifest: dict) -> dict[str, str]:
    statuses: dict[str, str] = {}
    bundle_dir = manifest_path.parent
    for branch_id, branch in branch_map(manifest).items():
        status_path = branch.get("status_path")
        if not isinstance(status_path, str) or relative_path_defect(status_path, f"branch {branch_id}.status_path"):
            continue
        path = bundle_dir / status_path
        if not path.exists():
            continue
        try:
            data = load_json_object(path)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"could not read terminal branch status artifact for protected branch inference: {path}: {exc}") from exc
        status = data.get("status")
        if status in CONTRACT.STATUSES:
            statuses[branch_id] = str(status)
    return statuses


def protected_ids(
    manifest_path: Path,
    manifest: dict,
    *,
    active_ids: list[str] | None = None,
    terminal_ids: list[str] | None = None,
    infer_scheduler: bool = True,
) -> tuple[set[str], set[str], dict[str, str]]:
    if not infer_scheduler:
        raise ValueError("scheduler/status inference is mandatory for amendment protected branch ids")
    active = set(active_ids or [])
    terminal_status: dict[str, str] = {branch_id: "terminal" for branch_id in (terminal_ids or [])}
    inferred_active, inferred_terminal = scheduler_state(manifest_path, manifest)
    active |= inferred_active
    terminal_status.update(inferred_terminal)
    status_terminal = status_file_terminal_state(manifest_path, manifest)
    stale_active = sorted(active & set(status_terminal))
    if stale_active:
        raise ValueError(
            "branch status artifacts mark scheduler-active branch ids as terminal; "
            f"refusing stale status overlap: {', '.join(stale_active)}"
        )
    terminal_status.update(status_terminal)
    overlap = sorted(active & set(terminal_status))
    if overlap:
        raise ValueError(f"branch ids cannot be both active and terminal for amendment protection: {', '.join(overlap)}")
    return active, set(terminal_status), terminal_status


def ensure_amendment_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("amendment_id must be a non-empty string")
    return require_safe_id(value.strip().upper(), "amendment_id")


def require_list(value: object, field: str, defects: list[str], *, min_items: int = 0) -> list:
    if not isinstance(value, list):
        defects.append(f"{field} must be an array")
        return []
    if len(value) < min_items:
        defects.append(f"{field} must contain at least {min_items} item(s)")
    return value


def operation_branch_ids(operation: dict) -> set[str]:
    ids: set[str] = set()
    branch_id = operation.get("branch_id")
    if isinstance(branch_id, str):
        ids.add(branch_id)
    branch = operation.get("branch")
    if isinstance(branch, dict) and isinstance(branch.get("id"), str):
        ids.add(branch["id"])
    branches = operation.get("branches")
    if isinstance(branches, list):
        for item in branches:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ids.add(item["id"])
    return ids


def replace_dependency(branches: list[dict], old_id: str, replacement_ids: list[str]) -> None:
    for branch in branches:
        deps = branch.get("depends_on")
        if not isinstance(deps, list) or old_id not in deps:
            continue
        new_deps = []
        for dep in deps:
            if dep == old_id:
                for replacement in replacement_ids:
                    if replacement not in new_deps:
                        new_deps.append(replacement)
            elif dep not in new_deps:
                new_deps.append(dep)
        branch["depends_on"] = new_deps


def append_dependency(branch: dict, dependency: str) -> None:
    deps = branch.get("depends_on")
    if not isinstance(deps, list):
        deps = []
    if dependency not in deps:
        deps.append(dependency)
    branch["depends_on"] = deps


def apply_operations_to_manifest(
    manifest: dict,
    proposal: dict,
    *,
    protected_branch_ids: set[str],
    terminal_branch_statuses: dict[str, str] | None = None,
) -> tuple[dict | None, list[str], list[str]]:
    defects: list[str] = []
    candidate = copy.deepcopy(manifest)
    branches = candidate.get("branches")
    if not isinstance(branches, list):
        return None, [], ["manifest.branches must be an array"]
    changed_branch_ids: set[str] = set()
    obsolete_entries = candidate.setdefault("obsolete_branches", [])
    if not isinstance(obsolete_entries, list):
        defects.append("manifest.obsolete_branches must be an array when present")
        obsolete_entries = []
        candidate["obsolete_branches"] = obsolete_entries

    operations = require_list(proposal.get("operations"), "operations", defects, min_items=1)
    for index, raw_operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(raw_operation, dict):
            defects.append(f"{path} must be an object")
            continue
        operation = copy.deepcopy(raw_operation)
        op = operation.get("op")
        if op not in CONTRACT.ADAPTATION_ALLOWED_OPERATIONS:
            defects.append(f"{path}.op must be one of {list(CONTRACT.ADAPTATION_ALLOWED_OPERATIONS)}")
            continue
        touched_protected = sorted(operation_branch_ids(operation) & protected_branch_ids)
        if touched_protected:
            defects.append(f"{path} attempts to modify protected branch ids: {', '.join(touched_protected)}")
            continue

        if op == "add_branch":
            branch = operation.get("branch")
            if not isinstance(branch, dict):
                defects.append(f"{path}.branch must be an object")
                continue
            branch_id = branch.get("id")
            if not isinstance(branch_id, str):
                defects.append(f"{path}.branch.id must be a string")
                continue
            if branch_index(branches, branch_id) is not None:
                defects.append(f"{path}.branch.id duplicates existing branch {branch_id}")
                continue
            branches.append(branch)
            changed_branch_ids.add(branch_id)
            continue

        branch_id = operation.get("branch_id")
        if not isinstance(branch_id, str):
            defects.append(f"{path}.branch_id must be a string")
            continue
        target_index = branch_index(branches, branch_id)
        if target_index is None:
            defects.append(f"{path}.branch_id does not exist in the manifest: {branch_id}")
            continue

        if op == "replace_unstarted_branch":
            branch = operation.get("branch")
            if not isinstance(branch, dict):
                defects.append(f"{path}.branch must be an object")
                continue
            replacement_id = branch.get("id")
            if not isinstance(replacement_id, str):
                defects.append(f"{path}.branch.id must be a string")
                continue
            existing_index = branch_index(branches, replacement_id)
            if existing_index is not None and existing_index != target_index:
                defects.append(f"{path}.branch.id duplicates existing branch {replacement_id}")
                continue
            branches[target_index] = branch
            replace_dependency(branches[target_index + 1 :], branch_id, [replacement_id])
            changed_branch_ids.add(replacement_id)
            continue

        if op == "split_unstarted_branch":
            new_branches = require_list(operation.get("branches"), f"{path}.branches", defects, min_items=2)
            if len(new_branches) > CONTRACT.MAX_ACTIVE_BRANCH_AGENTS:
                defects.append(f"{path}.branches must contain at most {CONTRACT.MAX_ACTIVE_BRANCH_AGENTS} branches")
                continue
            if any(not isinstance(branch, dict) for branch in new_branches):
                defects.append(f"{path}.branches entries must be objects")
                continue
            replacement_ids = [branch.get("id") for branch in new_branches]
            if any(not isinstance(value, str) for value in replacement_ids):
                defects.append(f"{path}.branches[].id values must be strings")
                continue
            duplicate_ids = {value for value in replacement_ids if replacement_ids.count(value) > 1}
            if duplicate_ids:
                defects.append(f"{path}.branches contain duplicate ids: {', '.join(sorted(duplicate_ids))}")
                continue
            for replacement_id in replacement_ids:
                existing_index = branch_index(branches, str(replacement_id))
                if existing_index is not None and existing_index != target_index:
                    defects.append(f"{path}.branches id duplicates existing branch {replacement_id}")
            if defects and defects[-1].startswith(path):
                continue
            branches[target_index : target_index + 1] = new_branches
            replace_dependency(branches[target_index + len(new_branches) :], branch_id, [str(value) for value in replacement_ids])
            changed_branch_ids.update(str(value) for value in replacement_ids)
            continue

        target = branches[target_index]
        if op == "add_dependency_to_unstarted_branch":
            dependencies = require_list(operation.get("depends_on"), f"{path}.depends_on", defects, min_items=1)
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    defects.append(f"{path}.depends_on entries must be strings")
                    continue
                append_dependency(target, dependency)
            changed_branch_ids.add(branch_id)
            continue

        if op == "add_work_item_to_unstarted_branch":
            work_item = operation.get("work_item")
            if not isinstance(work_item, dict):
                defects.append(f"{path}.work_item must be an object")
                continue
            work_items = target.get("work_items")
            if not isinstance(work_items, list):
                defects.append(f"{path}.branch work_items must be an array")
                continue
            work_items.append(work_item)
            target["work_items"] = work_items
            changed_branch_ids.add(branch_id)
            continue

        if op == "mark_unstarted_branch_obsolete":
            downstream = [
                branch.get("id")
                for branch in branches
                if isinstance(branch, dict) and branch_id in (branch.get("depends_on") if isinstance(branch.get("depends_on"), list) else [])
            ]
            if downstream:
                defects.append(f"{path} cannot mark {branch_id} obsolete while downstream branches depend on it: {', '.join(str(item) for item in downstream)}")
                continue
            removed = branches.pop(target_index)
            obsolete_entries.append(
                {
                    "branch_id": branch_id,
                    "reason": operation.get("reason", "Marked obsolete by accepted amendment."),
                    "archived_branch": removed,
                }
            )
            continue

    defects.extend(
        changed_branch_nonpass_dependency_defects(
            candidate,
            changed_branch_ids,
            terminal_branch_statuses or {},
        )
    )
    return (candidate if not defects else None), sorted(changed_branch_ids), defects


def changed_branch_nonpass_dependency_defects(
    candidate: dict,
    changed_branch_ids: set[str],
    terminal_branch_statuses: dict[str, str],
) -> list[str]:
    nonpass = {
        branch_id: status
        for branch_id, status in terminal_branch_statuses.items()
        if status in NONPASS_TERMINAL_STATUSES
    }
    if not nonpass:
        return []
    defects: list[str] = []
    branches = branch_map(candidate)
    for branch_id in sorted(changed_branch_ids):
        branch = branches.get(branch_id)
        if not isinstance(branch, dict):
            continue
        depends_on = branch.get("depends_on", [])
        if not isinstance(depends_on, list):
            continue
        blocked = [dep for dep in depends_on if isinstance(dep, str) and dep in nonpass]
        if blocked:
            details = ", ".join(f"{dep} ({nonpass[dep]})" for dep in blocked)
            defects.append(
                f"changed branch {branch_id} depends_on non-pass terminal branch ids: {details}; "
                "use recovers_from for recovery evidence instead of depends_on"
            )
    return defects


def protected_entries_unchanged(before: dict, after: dict, protected_branch_ids: set[str]) -> list[str]:
    defects: list[str] = []
    before_map = branch_map(before)
    after_map = branch_map(after)
    for branch_id in sorted(protected_branch_ids):
        before_branch = before_map.get(branch_id)
        after_branch = after_map.get(branch_id)
        if before_branch is None:
            continue
        if after_branch is None:
            defects.append(f"protected branch {branch_id} is missing from candidate manifest")
            continue
        for key in PROTECTED_BRANCH_KEYS:
            if before_branch.get(key) != after_branch.get(key):
                defects.append(f"protected branch {branch_id} changed immutable field {key}")
    return defects


def validate_candidate_with_lint(
    manifest_path: Path,
    candidate: dict,
    normalized_brief: dict,
    *,
    changed_branch_ids: list[str],
) -> list[str]:
    bundle_dir = manifest_path.parent
    with tempfile.TemporaryDirectory(prefix="goal-amender-lint-") as tmp:
        tmp_bundle = Path(tmp) / "bundle"
        shutil.copytree(bundle_dir, tmp_bundle)
        write_json(tmp_bundle / "job.manifest.json", candidate)
        PREFLIGHT.write_bundle_prompts(
            normalized_brief,
            tmp_bundle,
            branch_ids=set(changed_branch_ids),
            write_main=False,
        )
        lint = PREFLIGHT.lint_bundle(tmp_bundle, write_output=False)
    defects = []
    for item in lint.get("defects", []) if isinstance(lint, dict) else []:
        if not isinstance(item, dict):
            continue
        severity = item.get("severity")
        if severity in {"critical", "major"}:
            defects.append(f"candidate lint {severity}: {item.get('file')}: {item.get('message')}")
    return defects


def validate_proposal(
    *,
    manifest_path: Path,
    proposal_path: Path,
    active_branch_ids: list[str] | None = None,
    terminal_branch_ids: list[str] | None = None,
    infer_scheduler: bool = True,
    run_lint: bool = True,
) -> tuple[dict, dict | None, dict | None]:
    manifest = load_json_object(manifest_path)
    proposal = load_json_object(proposal_path)
    defects: list[str] = []
    try:
        active, terminal, terminal_status = protected_ids(
            manifest_path,
            manifest,
            active_ids=active_branch_ids,
            terminal_ids=terminal_branch_ids,
            infer_scheduler=infer_scheduler,
        )
    except ValueError as exc:
        active = set(active_branch_ids or [])
        terminal_status = {branch_id: "terminal" for branch_id in (terminal_branch_ids or [])}
        terminal = set(terminal_status)
        defects.append(str(exc))
    protected = active | terminal
    if active & terminal:
        defects.append("active_branch_ids and terminal_branch_ids must not overlap: " + ", ".join(sorted(active & terminal)))
    if not terminal:
        defects.append("at least one terminal branch id is required to validate a manifest amendment")

    try:
        amendment_id = ensure_amendment_id(proposal.get("amendment_id"))
    except Exception as exc:  # noqa: BLE001
        amendment_id = ""
        defects.append(str(exc))
    if proposal.get("schema_version") != 1:
        defects.append("schema_version must be 1")
    if proposal.get("job_id") != manifest.get("job_id"):
        defects.append("job_id must match manifest job_id")
    if not isinstance(proposal.get("rationale"), str) or not proposal.get("rationale", "").strip():
        defects.append("rationale must be a non-empty string")
    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        defects.append("manifest adaptation_policy does not match the shared amendment proposal policy")
    if manifest.get("amender_model_policy") != CONTRACT.AMENDER_MODEL_POLICY:
        defects.append("manifest amender_model_policy does not match the shared deterministic plan-amender router policy")

    candidate = None
    normalized_brief = None
    changed_branch_ids: list[str] = []
    if not defects:
        candidate, changed_branch_ids, op_defects = apply_operations_to_manifest(
            manifest,
            proposal,
            protected_branch_ids=protected,
            terminal_branch_statuses=terminal_status,
        )
        defects.extend(op_defects)

    if candidate is not None and not defects:
        try:
            candidate, normalized_brief = normalize_candidate_manifest(candidate)
        except SystemExit as exc:
            defects.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            defects.append(f"candidate normalization failed: {exc}")

    if candidate is not None and not defects:
        defects.extend(protected_entries_unchanged(manifest, candidate, protected))

    if candidate is not None and normalized_brief is not None and not defects and run_lint:
        defects.extend(
            validate_candidate_with_lint(
                manifest_path,
                candidate,
                normalized_brief,
                changed_branch_ids=changed_branch_ids,
            )
        )

    status = "pass" if not defects else "failed"
    result = {
        "schema_version": 1,
        "amendment_id": amendment_id or proposal.get("amendment_id"),
        "status": status,
        "manifest": manifest_path.as_posix(),
        "proposal": proposal_path.as_posix(),
        "manifest_sha256_before": sha256_file(manifest_path),
        "proposal_sha256": sha256_file(proposal_path),
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "protected_branch_ids": sorted(protected),
        "changed_branch_ids": changed_branch_ids,
        "candidate_branch_ids": [branch["id"] for branch in candidate.get("branches", [])] if isinstance(candidate, dict) and isinstance(candidate.get("branches"), list) else [],
        "candidate_manifest_sha256": canonical_sha256(candidate) if candidate is not None and not defects else None,
        "defects": defects,
    }
    return result, candidate if status == "pass" else None, normalized_brief if status == "pass" else None
