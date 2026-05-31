#!/usr/bin/env python3
"""Create a /goal orchestration bundle from a structured preflight brief."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

from render_goal_bootloader import render_bootloader


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "goal-job"


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
require_safe_id = PATH_RULES.require_safe_id
require_safe_label = PATH_RULES.require_safe_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
require_branch_name = PATH_RULES.require_branch_name
require_relative_path = PATH_RULES.require_relative_path
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
MAX_WAVES = CONTRACT.MAX_WAVES
DEFAULT_TOTAL_BRANCH_CAP = CONTRACT.DEFAULT_TOTAL_BRANCH_CAP
DEFAULT_WORKER_LADDER = CONTRACT.DEFAULT_WORKER_LADDER
WORKER_MODEL_POLICY = CONTRACT.WORKER_MODEL_POLICY
AMENDER_MODEL_POLICY = CONTRACT.AMENDER_MODEL_POLICY
LITE_MODEL_POLICY = CONTRACT.LITE_MODEL_POLICY
LITE_ADVISOR_POLICY = CONTRACT.LITE_ADVISOR_POLICY
RESEARCH_WORKER_POLICY = CONTRACT.RESEARCH_WORKER_POLICY
REVIEW_MODEL_POLICY = CONTRACT.REVIEW_MODEL_POLICY
ORCHESTRATION_WATCHDOG = CONTRACT.ORCHESTRATION_WATCHDOG


def example_brief() -> dict:
    return {
        "job_id": "toy-performance-optimization",
        "title": "Toy performance optimization",
        "base_ref": "main",
        "goal": "Reduce latency in the deterministic toy workflow without changing public behavior.",
        "source_summary": "The repository has two independent slow paths with existing tests that cover output compatibility.",
        "max_active_branch_agents": MAX_ACTIVE_BRANCH_AGENTS,
        "parallelization_rationale": "The branches own separate files and can run concurrently as a saturated pool.",
        "required_evidence": [
            "Each branch records exact verification commands and passing test evidence.",
            "Final status preserves any blocked, partial, or failed branch evidence.",
        ],
        "final_dod": [
            "Existing behavior remains covered by targeted tests.",
            "No new runtime dependencies are added.",
        ],
        "branches": [
            {
                "id": "B01",
                "branch_name": "optimize-kernel",
                "objective": "Optimize the pure computation path while preserving exact outputs and function signatures.",
                "worktree_path": ".worktrees/optimize-kernel",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Replace avoidable repeated computation in src/kernel.py with a deterministic cache.",
                        "owned_paths": ["src/kernel.py"],
                        "context_files": ["tests/test_kernel.py"],
                        "depends_on": [],
                        "verification": ["pytest tests/test_kernel.py"],
                        "dod": [
                            "Kernel tests pass without output changes.",
                            "Public function signatures in src/kernel.py are unchanged.",
                        ],
                    },
                    {
                        "id": "W02",
                        "objective": "Add or tighten focused regression coverage for the optimized kernel behavior.",
                        "owned_paths": ["tests/test_kernel.py"],
                        "context_files": ["src/kernel.py"],
                        "depends_on": [],
                        "verification": ["pytest tests/test_kernel.py"],
                        "dod": [
                            "Tests fail against an obvious legacy output regression.",
                            "Coverage stays focused on observable behavior.",
                        ],
                    },
                ],
            },
            {
                "id": "B02",
                "branch_name": "optimize-cli",
                "objective": "Reduce avoidable CLI overhead while preserving exact command output and exit codes.",
                "worktree_path": ".worktrees/optimize-cli",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Simplify src/cli.py work performed per invocation without changing output format.",
                        "owned_paths": ["src/cli.py"],
                        "context_files": ["tests/test_cli.py"],
                        "depends_on": [],
                        "verification": ["pytest tests/test_cli.py"],
                        "dod": [
                            "CLI tests pass for text and JSON output modes.",
                            "Exit code behavior remains unchanged.",
                        ],
                    },
                    {
                        "id": "W02",
                        "objective": "Add focused CLI regression coverage for output and error behavior.",
                        "owned_paths": ["tests/test_cli.py"],
                        "context_files": ["src/cli.py"],
                        "depends_on": [],
                        "verification": ["pytest tests/test_cli.py"],
                        "dod": [
                            "CLI regression tests cover the optimized code path.",
                            "No tests depend on wall-clock timing.",
                        ],
                    },
                ],
            },
        ],
    }


def brief_schema_summary() -> dict:
    return {
        "schema_version": 1,
        "purpose": "Structured input for create_goal_bundle.py; use this instead of reading script source.",
        "top_level_required": {
            "job_id": "stable slug-like job id",
            "goal": "concrete falsifiable objective",
            "source_summary": "short summary of source report or repo diagnosis",
            "required_evidence": ["falsifiable evidence item"],
            "final_dod": ["final definition-of-done item"],
            "branches": ["one to twenty branch objects; prefer independent ready branches"],
        },
        "top_level_optional": {
            "title": "display title",
            "base_ref": "git base ref; defaults to main",
            "max_active_branch_agents": f"integer 1-{MAX_ACTIVE_BRANCH_AGENTS}; default {MAX_ACTIVE_BRANCH_AGENTS}",
            "serial_reasons": "optional; deterministic defaults are supplied for underfilled branch capacity",
            "parallelization_rationale": "why branches can run as a rolling saturated pool",
            "merge_policy": "operator-controlled merge instructions",
            "artifact_policy": "artifact retention policy; deterministic default is supplied",
            "cleanup_policy": "cleanup policy; deterministic default preserves non-pass evidence",
            "preflight_lite_advice": "array; use [] when no Lite packet was used",
        },
        "branch_required": {
            "objective": "branch-level objective",
            "work_items": [f"one to {MAX_WORKER_PACKETS_PER_BRANCH} worker-sized objects"],
        },
        "branch_optional": {
            "id": "B01-style id; defaults by order",
            "branch_name": "safe git branch name; defaults from job id and branch id",
            "worktree_path": "repo-relative worktree path",
            "depends_on": "prior branch ids only; omit or [] for parallel branches",
            "max_active_worker_packets": f"integer 1-{MAX_WORKER_PACKETS_PER_BRANCH}; default {MAX_WORKER_PACKETS_PER_BRANCH}",
            "worker_serial_reasons": "optional; deterministic defaults are supplied for underfilled worker capacity",
            "scope": "branch-specific boundary text",
            "dod": "branch-level DoD; worker DoD is still required",
            "stop_conditions": "branch stop conditions",
        },
        "work_item_required": {
            "objective": "worker-sized objective",
            "owned_paths": ["repo-relative paths the worker may edit"],
            "verification": ["exact command strings the worker should run"],
            "dod": ["falsifiable worker DoD item"],
        },
        "work_item_optional": {
            "id": "W01-style id; defaults by order",
            "worker_type": "worker or research-worker; default worker",
            "context_files": ["repo-relative read-first files"],
            "depends_on": "prior work item ids only; omit or [] for parallel workers",
        },
        "commands": {
            "print_example": "python3 create_goal_bundle.py --example-brief",
            "print_schema": "python3 create_goal_bundle.py --brief-schema-json",
            "lint": "python3 lint_preflight_brief.py --brief /abs/brief.json --repo-root /abs/repo --json",
            "create_bundle": "python3 create_goal_bundle.py --brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle",
        },
    }


def require_agent_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 4")
    limit = value
    if limit < 1 or limit > MAX_ACTIVE_BRANCH_AGENTS:
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 4")
    return limit


def require_worker_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4")
    limit = value
    if limit < 1 or limit > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4")
    return limit


def nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_reason_list(value: object, fallback: str = "") -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if fallback:
        return [fallback]
    return []


def append_reason_once(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def require_string_list(value: object, field: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit(f"{field} must be a list")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SystemExit(f"{field}[{index}] must be a non-empty string")
        result.append(item.strip())
    if len(result) < min_items:
        raise SystemExit(f"{field} must contain at least {min_items} item(s)")
    return result


def normalize_worker_type(value: object, field: str) -> str:
    if value is None:
        return "worker"
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be 'worker' or 'research-worker'")
    normalized = value.strip()
    if normalized == "research":
        normalized = "research-worker"
    if normalized not in {"worker", "research-worker"}:
        raise SystemExit(f"{field} must be 'worker' or 'research-worker'")
    return normalized


def branch_id(index: int) -> str:
    return f"B{index:02d}"


def wave_id(index: int) -> str:
    return f"wave-{index:02d}"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bullets(values: list[str], fallback: str = "- none") -> str:
    if not values:
        return fallback
    return "\n".join(f"- {value}" for value in values)


def format_work_items(branch_id_value: str, items: list[dict]) -> str:
    if not items:
        return "- No work items supplied; preflight should ask for or synthesize worker-sized items."
    chunks = []
    for idx, item in enumerate(items, start=1):
        item_id = item.get("id") or f"W{idx:02d}"
        packet_id = item.get("packet_id") or f"{branch_id_value}-{item_id}"
        chunks.append(
            "\n".join(
                [
                    f"### {item_id}: {item.get('title') or 'Work item'}",
                    f"Worker packet id: {packet_id}",
                    f"Worker type: {item.get('worker_type', 'worker')}",
                    f"Objective: {item.get('objective', 'Objective not supplied.')}",
                    "Owned paths:",
                    bullets(item.get("owned_paths", [])),
                    "Context files:",
                    bullets(item.get("context_files", [])),
                    "Depends on:",
                    bullets(item.get("depends_on", [])),
                    "Verification commands:",
                    bullets(item.get("verification", [])),
                    "Definition of Done:",
                    bullets(item.get("dod", [])),
                ]
            )
        )
    return "\n\n".join(chunks)


def normalize_work_items(items: object, branch_id_value: str) -> list[dict]:
    if not isinstance(items, list):
        raise SystemExit(f"branch {branch_id_value} work_items must be a list")
    if len(items) < 1 or len(items) > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(f"branch {branch_id_value} must have 1 to {MAX_WORKER_PACKETS_PER_BRANCH} worker packets; split or synthesize work items")
    normalized = []
    seen_ids = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"branch {branch_id_value} work_items entries must be objects")
        item_id = require_safe_label(str(item.get("id") or f"W{index:02d}"), f"branch {branch_id_value} work item id")
        if item_id in seen_ids:
            raise SystemExit(f"branch {branch_id_value} duplicate work item id: {item_id}")
        seen_ids.add(item_id)
        packet_id = require_safe_label(f"{branch_id_value}-{item_id}", f"branch {branch_id_value} work item {item_id} packet_id")
        objective = nonempty_text(item.get("objective"))
        if not objective:
            raise SystemExit(f"branch {branch_id_value} work item {item_id} requires objective")
        normalized_item = {
            **item,
            "id": item_id,
            "packet_id": packet_id,
            "worker_type": normalize_worker_type(item.get("worker_type"), f"branch {branch_id_value} work item {item_id} worker_type"),
            "objective": objective,
            "owned_paths": [require_relative_path(value, f"branch {branch_id_value} work item {item_id} owned_paths") for value in require_string_list(item.get("owned_paths"), f"branch {branch_id_value} work item {item_id} owned_paths", min_items=1)],
            "context_files": [require_relative_path(value, f"branch {branch_id_value} work item {item_id} context_files") for value in require_string_list(item.get("context_files", []), f"branch {branch_id_value} work item {item_id} context_files")],
            "depends_on": require_string_list(item.get("depends_on", []), f"branch {branch_id_value} work item {item_id} depends_on"),
            "verification": require_string_list(item.get("verification"), f"branch {branch_id_value} work item {item_id} verification", min_items=1),
            "dod": require_string_list(item.get("dod"), f"branch {branch_id_value} work item {item_id} dod", min_items=1),
        }
        normalized.append(normalized_item)
    known_ids = {item["id"] for item in normalized}
    order = {item["id"]: index for index, item in enumerate(normalized)}
    for index, item in enumerate(normalized):
        for dep in item["depends_on"]:
            if dep not in known_ids:
                raise SystemExit(f"branch {branch_id_value} work item {item['id']} depends on unknown work item: {dep}")
            if order[dep] >= index:
                raise SystemExit(f"branch {branch_id_value} work item {item['id']} depends_on must reference only prior work item ids: {dep}")
    return normalized


def derived_owned_paths(work_items: list[dict]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for item in work_items:
        for value in item.get("owned_paths", []):
            if isinstance(value, str) and value not in seen:
                seen.add(value)
                paths.append(value)
    return paths


def ready_width(items: list[dict]) -> int:
    return len([item for item in items if not item.get("depends_on")])


def longest_work_item_chain(items: list[dict]) -> int:
    lengths: dict[str, int] = {}
    for item in items:
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        deps = item.get("depends_on", [])
        dep_lengths = [lengths.get(dep, 1) for dep in deps if isinstance(dep, str)]
        lengths[item_id] = 1 + (max(dep_lengths) if dep_lengths else 0)
    return max(lengths.values(), default=0)


def normalize_branch_dependencies(branches: list[dict]) -> None:
    order = {branch["id"]: index for index, branch in enumerate(branches)}
    for index, branch in enumerate(branches):
        raw_deps = branch.get("depends_on", [])
        if not isinstance(raw_deps, list):
            raise SystemExit(f"branch {branch['id']} depends_on must be a list")
        deps = []
        seen = set()
        for dep_index, dep in enumerate(raw_deps):
            dep_id = require_safe_id(str(dep).upper(), f"branch {branch['id']} depends_on[{dep_index}]")
            if dep_id in seen:
                raise SystemExit(f"branch {branch['id']} depends_on repeats branch {dep_id}")
            if dep_id not in order:
                raise SystemExit(f"branch {branch['id']} depends on unknown branch: {dep_id}")
            if order[dep_id] >= index:
                raise SystemExit(f"branch {branch['id']} depends_on must reference only prior branch ids; invalid dependency: {dep_id}")
            seen.add(dep_id)
            deps.append(dep_id)
        branch["depends_on"] = deps


def chunk_waves(branches: list[dict], wave_size: int) -> list[dict]:
    waves = []
    for offset in range(0, len(branches), wave_size):
        wave_branches = branches[offset : offset + wave_size]
        waves.append(
            {
                "id": wave_id(len(waves) + 1),
                "branches": [branch["id"] for branch in wave_branches],
            }
        )
    return waves


def ensure_unique_branch_values(branches: list[dict]) -> None:
    for field in ["id", "branch_name", "worktree_path"]:
        seen: dict[str, str] = {}
        for branch in branches:
            value = branch[field]
            owner = seen.get(value)
            if owner is not None:
                raise SystemExit(f"branch {branch['id']} {field} duplicates branch {owner}: {value}")
            seen[value] = branch["id"]

    reserved_bundle_paths = {
        "job.manifest.json",
        "main.prompt.md",
        "goal-bootloader.md",
        "PREFLIGHT_REPORT.md",
        "preflight.lint.json",
    }
    seen_paths: dict[str, str] = {}
    for branch in branches:
        for field in ["prompt", "status_path", "review_path", "pre_review_gate_path"]:
            value = branch[field]
            label = f"branch {branch['id']} {field}"
            if value in reserved_bundle_paths:
                raise SystemExit(f"{label} must not collide with reserved bundle file: {value}")
            owner = seen_paths.get(value)
            if owner is not None:
                raise SystemExit(f"{label} duplicates {owner}: {value}")
            seen_paths[value] = label


def normalize_brief(brief: dict) -> dict:
    if "job_id" not in brief:
        raise SystemExit("brief must include job_id")
    if not brief.get("branches"):
        raise SystemExit("brief must include synthesized branches before bundle generation")

    job_id = slug(brief["job_id"])
    base_ref = require_branch_name(str(brief.get("base_ref", "main")), "base_ref")
    max_active = require_agent_limit(brief.get("max_active_branch_agents", MAX_ACTIVE_BRANCH_AGENTS))
    serial_reason = nonempty_text(brief.get("serial_reason"))
    serial_reasons = normalize_reason_list(brief.get("serial_reasons"), serial_reason)
    parallelization_rationale = nonempty_text(brief.get("parallelization_rationale"))
    branches = []
    for idx, original in enumerate(brief["branches"], start=1):
        bid = original.get("id") or branch_id(idx)
        bid = require_safe_id(str(bid).upper(), "branch id")
        branch_name = require_branch_name(original.get("branch_name") or f"{job_id}-{bid.lower()}")
        worktree_path = require_relative_path(original.get("worktree_path") or f".worktrees/{branch_name}", "worktree_path")
        max_workers = require_worker_limit(original.get("max_active_worker_packets", MAX_WORKER_PACKETS_PER_BRANCH))
        original_worker_parallelism = original.get("worker_parallelism") if isinstance(original.get("worker_parallelism"), dict) else {}
        worker_serial_reason = nonempty_text(original.get("worker_serial_reason"))
        worker_serial_reasons = normalize_reason_list(
            original.get("worker_serial_reasons", original_worker_parallelism.get("serial_reasons")),
            worker_serial_reason,
        )
        worker_parallelization_rationale = (
            nonempty_text(original.get("worker_parallelization_rationale"))
            or nonempty_text(original_worker_parallelism.get("parallelization_rationale"))
        )
        work_items = normalize_work_items(original.get("work_items", []), bid)
        owned_paths = derived_owned_paths(work_items)
        if max_workers < MAX_WORKER_PACKETS_PER_BRANCH:
            append_reason_once(
                worker_serial_reasons,
                f"Branch {bid} caps worker concurrency at {max_workers}, below the package maximum of {MAX_WORKER_PACKETS_PER_BRANCH}.",
            )
        if len(work_items) == 1 and max_workers > 1:
            append_reason_once(worker_serial_reasons, f"Branch {bid} declares one worker item, so no additional independent worker packet exists to fill capacity.")
        if ready_width(work_items) < min(max_workers, len(work_items)):
            append_reason_once(worker_serial_reasons, f"Branch {bid} worker depends_on topology leaves fewer initially ready workers than capacity.")
        if len(work_items) > 2 and longest_work_item_chain(work_items) >= len(work_items) - 1:
            append_reason_once(worker_serial_reasons, f"Branch {bid} worker dependency chain serializes most work by explicit depends_on topology.")
        branch = {
            **original,
            "work_items": work_items,
            "id": bid,
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "owned_paths": owned_paths,
            "prompt": require_relative_path(original.get("prompt") or f"branches/{bid}.prompt.md", "prompt"),
            "status_path": require_relative_path(original.get("status_path") or f"branches/{bid}.status.json", "status_path"),
            "review_path": require_relative_path(original.get("review_path") or f"branches/{bid}.review.json", "review_path"),
            "pre_review_gate_path": require_relative_path(original.get("pre_review_gate_path") or CONTRACT.pre_review_gate_path(bid), "pre_review_gate_path"),
            "max_active_worker_packets": max_workers,
            "worker_parallelism": {
                "parallelism_default": True,
                "scheduling_mode": "rolling",
                "scheduler_path": CONTRACT.worker_scheduler_path(bid),
                "max_active_worker_packets": max_workers,
                "max_worker_packets_per_branch": MAX_WORKER_PACKETS_PER_BRANCH,
                "serial_reasons": worker_serial_reasons,
                "parallelization_rationale": worker_parallelization_rationale
                or f"Launch independent worker packets as a rolling saturated pool up to {max_workers} active worker packets.",
                "wave_execution": "Use work items as an ordered ready queue. Keep worker slots saturated up to max_active_worker_packets; when a worker finishes and capacity is freed, launch the next eligible worker whose depends_on work item ids are complete.",
                "dependency_policy": "Work item depends_on entries are explicit prior-worker dependencies; workers without unresolved depends_on entries are eligible whenever capacity is available.",
                "slot_refill": "After a worker launcher exits, collect and integrate its status/diff, remove it from the active set, then launch the next eligible worker immediately if capacity is available.",
            },
        }
        branches.append(branch)

    ensure_unique_branch_values(branches)

    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        raise SystemExit(f"brief has more than {DEFAULT_TOTAL_BRANCH_CAP} branches; max is {MAX_WAVES} waves of {MAX_ACTIVE_BRANCH_AGENTS}")
    if len(branches) == 1:
        append_reason_once(serial_reasons, "Only one branch is declared, so branch orchestration cannot fill multiple branch slots.")
    if max_active < MAX_ACTIVE_BRANCH_AGENTS:
        append_reason_once(
            serial_reasons,
            f"max_active_branch_agents is {max_active}, below the package maximum of {MAX_ACTIVE_BRANCH_AGENTS}.",
        )
    preflight_lite_advice = brief.get("preflight_lite_advice", [])
    if not isinstance(preflight_lite_advice, list):
        raise SystemExit("preflight_lite_advice must be an array when supplied")

    waves = brief.get("waves") or chunk_waves(branches, max_active)
    if len(waves) > MAX_WAVES:
        raise SystemExit(f"waves must not exceed {MAX_WAVES}")
    branch_ids = {branch["id"] for branch in branches}
    seen_wave_ids = set()
    seen_wave_branches = []
    wave_by_branch = {}
    for idx, wave in enumerate(waves):
        wid = require_safe_label(str(wave["id"]), "wave id")
        if wid in seen_wave_ids:
            raise SystemExit(f"duplicate wave id: {wid}")
        seen_wave_ids.add(wid)
        wave["id"] = wid
        wave_branches = wave.get("branches")
        if not isinstance(wave_branches, list) or not wave_branches:
            raise SystemExit(f"wave {wid} must list at least one branch")
        if len(wave_branches) > max_active:
            raise SystemExit(f"wave {wid} has more than max_active_branch_agents={max_active} branches")
        for bid in wave_branches:
            if bid not in branch_ids:
                raise SystemExit(f"wave {wid} references unknown branch id: {bid}")
            if bid in wave_by_branch:
                raise SystemExit(f"branch {bid} appears in more than one wave")
            wave_by_branch[bid] = wave["id"]
            seen_wave_branches.append(bid)
    if set(seen_wave_branches) != branch_ids:
        raise SystemExit("waves must cover every branch exactly once")
    for branch in branches:
        branch["wave"] = wave_by_branch[branch["id"]]
    normalize_branch_dependencies(branches)

    initial_ready = len([branch for branch in branches if not branch.get("depends_on")])
    if initial_ready < min(max_active, len(branches)):
        append_reason_once(serial_reasons, "Initial ready branch count underfills max_active_branch_agents because of explicit branch depends_on topology.")
    branch_chain_lengths: dict[str, int] = {}
    for branch in branches:
        dep_lengths = [branch_chain_lengths.get(dep, 1) for dep in branch.get("depends_on", [])]
        branch_chain_lengths[branch["id"]] = 1 + (max(dep_lengths) if dep_lengths else 0)
    if len(branches) > 2 and max(branch_chain_lengths.values(), default=0) >= len(branches) - 1:
        append_reason_once(serial_reasons, "Branch dependency chain serializes most work by explicit branch depends_on topology.")

    return {
        **brief,
        "job_id": job_id,
        "base_ref": base_ref,
        "artifact_policy": nonempty_text(brief.get("artifact_policy"))
        or "Preserve the full orchestration bundle under plans/orchestration/<job-id>; commit generated preflight prompts only when the user explicitly asks, and commit runtime status/review/audit artifacts only when the main prompt or user explicitly requires them.",
        "cleanup_policy": nonempty_text(brief.get("cleanup_policy"))
        or "On pass, report mergeability and leave branch/worktree removal to explicit user authorization. On partial, blocked, or failed runs, preserve branch worktrees, branches, packets, and logs for inspection unless the user explicitly authorizes cleanup.",
        "max_active_branch_agents": max_active,
        "parallelization": {
            "parallelism_default": True,
            "max_active_branch_agents": max_active,
            "max_branches_per_wave": MAX_ACTIVE_BRANCH_AGENTS,
            "max_waves": MAX_WAVES,
            "scheduling_mode": "rolling",
            "scheduler_path": CONTRACT.MAIN_SCHEDULER_PATH,
            "serial_reasons": serial_reasons,
            "parallelization_rationale": parallelization_rationale
            or f"Keep up to {max_active} branch orchestrators active; defer only branches whose depends_on branch ids are not complete.",
            "wave_execution": "Use waves as scheduling/order groups only. Keep branch orchestrator slots saturated up to max_active_branch_agents; when a branch finishes and capacity is freed, launch the next eligible branch whose depends_on branch ids are complete.",
            "dependency_policy": "Branch depends_on entries are explicit prior-branch dependencies; branches without unresolved depends_on entries are eligible whenever capacity is available.",
        },
        "branches": branches,
        "waves": waves,
        "preflight_lite_advice": preflight_lite_advice,
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_bundle_dirs(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ["branches", "workers", "research", "reviewers", "audit", "lite", "schedulers", "amendments"]:
        (bundle_dir / dirname).mkdir(exist_ok=True)


def render_branch_waves(waves: list[dict]) -> str:
    lines = []
    for wave in waves:
        lines.append(f"- {wave['id']}: {', '.join(wave['branches'])}")
    return "\n".join(lines)


def render_branch_dependencies(branches: list[dict]) -> str:
    lines = []
    for branch in branches:
        deps = branch.get("depends_on", [])
        lines.append(f"- {branch['id']}: {', '.join(deps) if deps else 'none'}")
    return "\n".join(lines)


def manifest_from_normalized_brief(brief: dict) -> dict:
    return {
        "job_id": brief["job_id"],
        "main_prompt": "main.prompt.md",
        "base_ref": brief["base_ref"],
        "artifact_policy": brief["artifact_policy"],
        "cleanup_policy": brief["cleanup_policy"],
        "max_active_branch_agents": brief["max_active_branch_agents"],
        "parallelization": brief["parallelization"],
        "adaptation_policy": CONTRACT.ADAPTATION_POLICY,
        "worker_model_policy": WORKER_MODEL_POLICY,
        "amender_model_policy": AMENDER_MODEL_POLICY,
        "lite_model_policy": LITE_MODEL_POLICY,
        "lite_advisor_policy": LITE_ADVISOR_POLICY,
        "research_worker_policy": RESEARCH_WORKER_POLICY,
        "review_model_policy": REVIEW_MODEL_POLICY,
        "orchestration_watchdog": ORCHESTRATION_WATCHDOG,
        "preflight_lite_advice": brief["preflight_lite_advice"],
        "branches": [
            {
                "id": branch["id"],
                "wave": branch["wave"],
                "prompt": branch["prompt"],
                "branch_name": branch["branch_name"],
                "worktree_path": branch["worktree_path"],
                "status_path": branch["status_path"],
                "review_path": branch["review_path"],
                "pre_review_gate_path": branch["pre_review_gate_path"],
                "depends_on": branch["depends_on"],
                "owned_paths": branch["owned_paths"],
                "work_items": branch["work_items"],
                "max_active_worker_packets": branch["max_active_worker_packets"],
                "worker_parallelism": branch["worker_parallelism"],
                **(
                    {"recovers_from": branch["recovers_from"]}
                    if isinstance(branch.get("recovers_from"), list)
                    else {}
                ),
                **(
                    {"contention_reason": branch["contention_reason"]}
                    if isinstance(branch.get("contention_reason"), str) and branch["contention_reason"].strip()
                    else {}
                ),
                **(
                    {"worker_contention_reason": branch["worker_contention_reason"]}
                    if isinstance(branch.get("worker_contention_reason"), str) and branch["worker_contention_reason"].strip()
                    else {}
                ),
            }
            for branch in brief["branches"]
        ],
        "waves": brief["waves"],
    }


def render_main_prompt_text(brief: dict) -> str:
    main_prompt = (Path(__file__).resolve().parents[1] / "assets" / "main.prompt.template.md").read_text(encoding="utf-8")
    return main_prompt.format(
        title=brief.get("title", brief["job_id"]),
        job_id=brief["job_id"],
        base_ref=brief["base_ref"],
        goal=brief.get("goal", "Goal not supplied."),
        source_summary=brief.get("source_summary", "Source summary not supplied."),
        branch_waves=render_branch_waves(brief["waves"]),
        branch_dependencies=render_branch_dependencies(brief["branches"]),
        max_active_branch_agents=brief["max_active_branch_agents"],
        main_scheduler_path=CONTRACT.MAIN_SCHEDULER_PATH,
        parallelization_rationale=brief["parallelization"]["parallelization_rationale"],
        merge_policy=brief.get("merge_policy", "Report mergeability only unless explicitly authorized to merge."),
        cleanup_policy=brief["cleanup_policy"],
        artifact_policy=brief["artifact_policy"],
        required_evidence=bullets(brief.get("required_evidence", [])),
        final_dod=bullets(brief.get("final_dod", [])),
    )


def render_branch_prompt_text(brief: dict, branch: dict) -> str:
    branch_template = (Path(__file__).resolve().parents[1] / "assets" / "branch.prompt.template.md").read_text(encoding="utf-8")
    scope = branch.get("scope") or (
        f"Bounded to the owned paths, work items, verification commands, and stop conditions listed for {branch['id']}."
    )
    return branch_template.format(
        branch_id=branch["id"],
        title=branch.get("title", branch.get("objective", branch["id"])),
        base_ref=brief["base_ref"],
        branch_name=branch["branch_name"],
        worktree_path=branch["worktree_path"],
        wave=branch["wave"],
        depends_on=bullets(branch.get("depends_on", [])),
        max_active_worker_packets=branch["max_active_worker_packets"],
        worker_scheduler_path=CONTRACT.worker_scheduler_path(branch["id"]),
        pre_review_gate_path=branch["pre_review_gate_path"],
        worker_parallelization_rationale=branch["worker_parallelism"]["parallelization_rationale"],
        default_worker_ladder=CONTRACT.format_worker_ladder(DEFAULT_WORKER_LADDER),
        allowed_worker_routes=", ".join(DEFAULT_WORKER_LADDER),
        objective=branch.get("objective", "Objective not supplied."),
        scope=scope,
        owned_paths=bullets(branch.get("owned_paths", [])),
        work_items=format_work_items(branch["id"], branch.get("work_items", [])),
        tests=bullets(branch.get("tests", [])),
        stop_conditions=bullets(branch.get("stop_conditions", [])),
        dod=bullets(branch.get("dod", [])),
    )


def write_bundle_prompts(
    brief: dict,
    bundle_dir: Path,
    *,
    branch_ids: set[str] | None = None,
    write_main: bool = True,
) -> None:
    if write_main:
        write(bundle_dir / "main.prompt.md", render_main_prompt_text(brief))
    for branch in brief["branches"]:
        if branch_ids is not None and branch["id"] not in branch_ids:
            continue
        write(bundle_dir / branch["prompt"], render_branch_prompt_text(brief, branch))


def lint_bundle(bundle_dir: Path, *, write_output: bool = True) -> dict:
    path = Path(__file__).resolve().parent / "lint_goal_bundle.py"
    spec = importlib.util.spec_from_file_location("goal_preflight_lint_goal_bundle", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load bundle linter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = module.lint(bundle_dir)
    if write_output:
        write(bundle_dir / "preflight.lint.json", json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


def create_bundle(brief: dict, repo_root: Path, out_dir: Path | None) -> Path:
    brief = normalize_brief(brief)

    bundle_dir = out_dir or repo_root / "plans" / "orchestration" / brief["job_id"]
    ensure_bundle_dirs(bundle_dir)

    manifest = manifest_from_normalized_brief(brief)
    write(bundle_dir / "job.manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    write_bundle_prompts(brief, bundle_dir)

    bootloader = render_bootloader(bundle_dir.resolve(), repo_root.resolve())
    write(bundle_dir / "goal-bootloader.md", bootloader)
    report = "\n".join(
        [
            f"# Preflight Report: {brief['job_id']}",
            "",
            f"Bundle: {bundle_dir.resolve()}",
            f"Branches: {len(brief['branches'])}",
            f"Waves: {len(brief['waves'])}",
            f"Max active branch agents: {brief['max_active_branch_agents']}",
            f"Parallelization: {brief['parallelization']['parallelization_rationale']}",
            f"Scheduling: rolling; runtime branch scheduler ledger path is {CONTRACT.MAIN_SCHEDULER_PATH}; saturate active branch orchestrators up to max_active_branch_agents and defer only branches with incomplete depends_on branch ids.",
            f"Worker model policy: {CONTRACT.format_worker_ladder(DEFAULT_WORKER_LADDER)}; branches may choose an ordered subsequence with a recorded reason.",
            "Research worker policy: use research-worker packets for outside information gathering; launcher uses Codex native web search with user config loaded and read-only sandboxing, allowing configured read-only CLI/MCP/connector/browser/search tools plus shell/network inspection commands while prohibiting file edits and state-changing actions.",
            f"Artifact policy: {brief['artifact_policy']}",
            f"Cleanup policy: {brief['cleanup_policy']}",
            "",
            "Bootstrap: generated bootloaders require runtime skill availability checks before prompt audit.",
            "Lite: optional advisory packets may route context but never satisfy audit, review, mergeability, or DoD evidence; preflight Lite provenance lives in job.manifest.json preflight_lite_advice.",
            "Run `lint_goal_bundle.py` before launching `/goal`.",
            "",
        ]
    )
    write(bundle_dir / "PREFLIGHT_REPORT.md", report)
    return bundle_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a compact /goal orchestration bundle from a structured brief.",
        epilog=(
            "For agent-readable brief shape, run --brief-schema-json. "
            "For a valid compact starter brief, run --example-brief."
        ),
    )
    parser.add_argument("--brief")
    parser.add_argument("--repo-root")
    parser.add_argument("--out-dir")
    parser.add_argument(
        "--example-brief",
        "--brief-template-json",
        dest="example_brief",
        action="store_true",
        help="Print a valid compact brief JSON template and exit.",
    )
    parser.add_argument(
        "--brief-schema-json",
        action="store_true",
        help="Print an agent-readable brief field guide and exit.",
    )
    args = parser.parse_args()

    if args.example_brief:
        print(json.dumps(example_brief(), indent=2, sort_keys=True))
        return 0
    if args.brief_schema_json:
        print(json.dumps(brief_schema_summary(), indent=2, sort_keys=True))
        return 0
    if not args.brief or not args.repo_root:
        parser.print_usage(sys.stderr)
        raise SystemExit("--brief and --repo-root are required unless printing --example-brief or --brief-schema-json")

    brief_path = resolve_absolute_path(args.brief, "--brief", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False) if args.out_dir else None
    bundle_dir = create_bundle(load_json(brief_path), repo_root, out_dir)
    print(bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
