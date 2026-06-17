#!/usr/bin/env python3
"""Create a /goal orchestration bundle from a structured preflight brief."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

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
DEFAULT_WORKER_ROUTE_CLASS = CONTRACT.DEFAULT_WORKER_ROUTE_CLASS
MANIFEST_WORKER_ROUTE_CLASSES = CONTRACT.MANIFEST_WORKER_ROUTE_CLASSES
RESEARCH_WORKER_TYPE = CONTRACT.RESEARCH_WORKER_TYPE
WORKER_MODEL_POLICY = CONTRACT.WORKER_MODEL_POLICY
AMENDER_MODEL_POLICY = CONTRACT.AMENDER_MODEL_POLICY
LITE_MODEL_POLICY = CONTRACT.LITE_MODEL_POLICY
LITE_ADVISOR_POLICY = CONTRACT.LITE_ADVISOR_POLICY
RESEARCH_WORKER_POLICY = CONTRACT.RESEARCH_WORKER_POLICY
REVIEW_MODEL_POLICY = CONTRACT.REVIEW_MODEL_POLICY
ORCHESTRATION_WATCHDOG = CONTRACT.ORCHESTRATION_WATCHDOG
TELEMETRY_POLICY_DEFAULT = CONTRACT.TELEMETRY_POLICY_DEFAULT
TELEMETRY_POLICY_SCHEMA_VERSION = CONTRACT.TELEMETRY_POLICY_SCHEMA_VERSION
TELEMETRY_POLICY_MODES = CONTRACT.TELEMETRY_POLICY_MODES
TELEMETRY_COLLECT_ITEMS = CONTRACT.TELEMETRY_COLLECT_ITEMS
RUNTIME_RULES_PATH = "runtime-rules.md"
PROMOTED_CONTEXT_ATTACHMENT_MIN_BYTES = 8192
PROMOTED_CONTEXT_ATTACHMENT_MIN_USES = 2
INLINE_SOURCE_CONTRACT_MAX_CHARS = 12000
EXACT_SOURCE_RE = re.compile(
    r"\b("
    r"exact\s+(?:operation\s+)?(?:list|instance|source|payload|dataset|benchmark|data)|"
    r"provided\s+in\s+this\s+brief|"
    r"matching\s+the\s+exact|"
    r"matches\s+the\s+exact|"
    r"source[-\s]+of[-\s]+truth|"
    r"exact\s+FT\d+"
    r")\b",
    re.IGNORECASE,
)
INLINE_SOURCE_TUPLE_RE = re.compile(r"\(\s*\d+\s*,\s*\d+\s*\)")

DOC_PATH_RE = re.compile(
    r"(^|/)(readme|changelog|license|notice|contributing|docs?|documentation)(\.|/|$)|"
    r"\.(md|markdown|rst|txt|adoc)$",
    re.IGNORECASE,
)
TEST_PATH_RE = re.compile(r"(^|/)(tests?|specs?)(/|$)|(^|/)(test_|.*_test\.)|(\.spec\.|\_spec\.)", re.IGNORECASE)
CODE_PATH_RE = re.compile(
    r"\.(py|js|jsx|ts|tsx|go|rs|java|kt|c|cc|cpp|h|hpp|cs|rb|php|swift|scala|sh|bash|zsh)$", re.IGNORECASE
)
COMPLEX_TERMS = (
    "scheduler",
    "validator",
    "validation",
    "security",
    "auth",
    "credential",
    "migration",
    "schema",
    "concurrency",
    "data-loss",
    "data loss",
    "cross-module",
    "cross module",
    "architecture",
    "public api",
    "prompt-audit",
    "telemetry",
    "runtime",
    "orchestration",
    "reviewer",
    "fallback",
    "timeout",
    "state machine",
)
MECHANICAL_TERMS = (
    "format",
    "formatting",
    "lint",
    "typo",
    "spelling",
    "rename",
    "version bump",
    "metadata",
    "generated",
    "stale context",
    "path fix",
    "path-only",
    "regenerate",
)


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
                        "route_class": "normal-code",
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
                        "route_class": "small-edit",
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
                        "route_class": "normal-code",
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
                        "route_class": "small-edit",
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
            "base_ref": "git base ref; defaults to the current git branch, falling back to main",
            "max_active_branch_agents": f"integer 1-{MAX_ACTIVE_BRANCH_AGENTS}; default {MAX_ACTIVE_BRANCH_AGENTS}",
            "serial_reasons": "optional; deterministic defaults are supplied for underfilled branch capacity",
            "parallelization_rationale": "why branches can run as a rolling saturated pool",
            "merge_policy": "operator-controlled merge instructions",
            "artifact_policy": "artifact retention policy; deterministic default is supplied",
            "cleanup_policy": "cleanup policy; deterministic default preserves non-pass evidence",
            "preflight_lite_advice": "array; use [] when no Lite packet was used",
            "telemetry_policy": "manifest-owned opt-in telemetry policy object; supported modes are standard and debug, raw_text must be false, collect names debug metric groups",
            "telemetry_mode": "lean shorthand; set to debug to expand the full safe debug telemetry policy",
            "debug_telemetry": "boolean shorthand; true means telemetry_mode=debug, false means standard unless telemetry_policy says otherwise",
            "route_policy_degraded_telemetry_waiver": "optional object {accepted: true, reason: non-empty string}; allows runtime launch when verified routes lack token telemetry but the operator accepts character/timing telemetry fallback",
            "source_attachments": "array of repo-relative file paths or {path,label,kind}; use for large benchmark/spec files instead of pasting them into goal; required when the brief references an exact instance/list/source that is not fully inline",
            "runtime_cap": "string or object declaring any concrete runtime/time cap mentioned in success criteria, including CLI flag when applicable",
            "goal_config": "optional copied model/provider/harness configuration supplied through --goal-config; manifest stores only compact summaries and hashes; do not hand-author in the brief",
            "goal_config_check": "optional passing goal-config check report supplied through --goal-config-check; required when --goal-config is used",
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
            "dependency_context_reason": "required when a branch depends on prior branches but no work item context_files are declared; bundle creation supplies a deterministic default",
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
            "route_class": f"one of {', '.join(MANIFEST_WORKER_ROUTE_CLASSES)} for worker items; inferred deterministically when omitted; omit for research-worker",
            "route_class_reason": "optional explicit reason; bundle creation always writes a non-empty reason to job.manifest.json and branch prompts",
            "context_files": [
                "repo-relative read-first files that must already exist under repo root; use owned_paths for new writable outputs; repeated large files are promoted to source_attachments and referenced through source_attachment_refs"
            ],
            "source_attachment_refs": "labels from source_attachments; generated automatically when repeated large context_files are promoted",
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


def current_git_branch(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "branch", "--show-current"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value if value else "main"


def _is_git_repo(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "rev-parse", "--is-inside-work-tree"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_ref_exists(repo_root: Path, ref: str) -> bool:
    for candidate in [f"refs/heads/{ref}", f"refs/remotes/{ref}"]:
        result = subprocess.run(
            ["git", "-C", repo_root.as_posix(), "show-ref", "--verify", "--quiet", candidate],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode == 0:
            return True
    return False


def _parse_git_status_lines(raw: str) -> tuple[list[str], list[str]]:
    tracked: list[str] = []
    untracked: list[str] = []
    for line in raw.splitlines():
        status_line = line.rstrip()
        if len(status_line) < 3:
            continue
        status = status_line[:2]
        if status == "##":
            continue
        if status == "!!":
            continue
        path = status_line[3:].strip()
        if not path:
            continue
        if "->" in path:
            path = path.split("->", 1)[0].strip()
        if status == "??":
            untracked.append(path)
        else:
            tracked.append(path)
    return tracked, untracked


def _is_root_required_for_runtime(branch_paths: list[str] | None) -> bool:
    if not branch_paths:
        return True
    for branch_path in branch_paths:
        normalized = branch_path.strip()
        normalized = normalized.strip("/")
        if not normalized or normalized in {".", "./"}:
            return True
        normalized = normalized.replace("\\", "/")
        if normalized.startswith(".worktrees/"):
            continue
        return True
    return False


def git_repo_status(repo_root: Path, *, base_ref: str | None = None, branch_paths: list[str] | None = None) -> dict:
    if not _is_git_repo(repo_root):
        return {
            "repo_root": repo_root.resolve().as_posix(),
            "repo_is_git": False,
            "base_ref": base_ref,
            "base_ref_status": "not_checked",
            "reason": "repo root is not a git work tree",
        }
    status_result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "status", "--short", "--branch"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    tracked_changes, untracked_changes = _parse_git_status_lines(status_result.stdout)
    used_for_runtime = _is_root_required_for_runtime(branch_paths or [])
    dirty = bool(tracked_changes or untracked_changes)
    if dirty:
        decision = "warning" if used_for_runtime else "ignored_because_isolated_worktrees"
    else:
        decision = "clean"
    status = {
        "repo_root": repo_root.resolve().as_posix(),
        "repo_is_git": True,
        "current_branch": current_git_branch(repo_root),
        "base_ref": base_ref,
        "base_ref_status": "not_requested",
        "root_worktree_state": {
            "dirty": dirty,
            "tracked_changes": tracked_changes,
            "untracked_changes": untracked_changes,
            "used_for_runtime": used_for_runtime,
            "decision": decision,
        },
    }
    if base_ref:
        exists = _git_ref_exists(repo_root, base_ref)
        status["base_ref_status"] = "exists" if exists else "missing"
        if not exists:
            status["reason"] = f"base_ref does not exist in repository refs: {base_ref!r}"
    return status


def require_worker_limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4")
    limit = value
    if limit < 1 or limit > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4")
    return limit


def nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_source_attachments(value: object, *, repo_root: Path | None = None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SystemExit("source_attachments must be a list")
    attachments: list[dict] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            raw_path = item
            label = Path(item).name
            kind = "context"
        elif isinstance(item, dict):
            raw_path = item.get("path")
            label = nonempty_text(item.get("label")) or (
                Path(str(raw_path)).name if raw_path else f"attachment-{index + 1}"
            )
            kind = nonempty_text(item.get("kind")) or "context"
        else:
            raise SystemExit(f"source_attachments[{index}] must be a path string or object")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise SystemExit(f"source_attachments[{index}].path must be non-empty")
        rel_path = require_relative_path(raw_path, f"source_attachments[{index}].path")
        attachment = {"path": rel_path, "label": label, "kind": kind}
        if repo_root is not None:
            target = repo_root / rel_path
            if not target.exists():
                raise SystemExit(f"source attachment does not exist: {rel_path}")
            if target.is_file():
                attachment["sha256"] = sha256_file(target)
                attachment["bytes"] = target.stat().st_size
        attachments.append(attachment)
    return attachments


def attachment_label_for_path(path: str, used_labels: set[str]) -> str:
    base = slug(Path(path).stem or Path(path).name or "source")
    label = f"source-{base}"
    suffix = 2
    while label in used_labels:
        label = f"source-{base}-{suffix}"
        suffix += 1
    used_labels.add(label)
    return label


def source_attachment_by_path(attachments: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in attachments:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        label = item.get("label")
        if isinstance(path, str) and isinstance(label, str) and path and label:
            result[path] = label
    return result


def _context_file_use_counts(brief: dict) -> dict[str, int]:
    context_counts: dict[str, int] = {}
    for branch in brief.get("branches", []):
        if not isinstance(branch, dict):
            continue
        for item in branch.get("work_items", []):
            if not isinstance(item, dict):
                continue
            for path in item.get("context_files", []):
                if isinstance(path, str) and path:
                    context_counts[path] = context_counts.get(path, 0) + 1
    return context_counts


def _rewrite_work_items_for_promotions(brief: dict, promoted: dict[str, str]) -> None:
    for branch in brief.get("branches", []):
        if not isinstance(branch, dict):
            continue
        for item in branch.get("work_items", []):
            if not isinstance(item, dict):
                continue
            kept_context: list[str] = []
            refs = (
                [ref for ref in item.get("source_attachment_refs", []) if isinstance(ref, str) and ref.strip()]
                if isinstance(item.get("source_attachment_refs"), list)
                else []
            )
            seen_refs = set(refs)
            for path in item.get("context_files", []):
                if not isinstance(path, str) or not path:
                    continue
                label = promoted.get(path)
                if label is None:
                    kept_context.append(path)
                    continue
                if label not in seen_refs:
                    refs.append(label)
                    seen_refs.add(label)
            item["context_files"] = kept_context
            item["source_attachment_refs"] = refs


def promote_repeated_context_attachments(brief: dict, *, repo_root: Path | None) -> dict:
    attachments = list(brief.get("source_attachments", [])) if isinstance(brief.get("source_attachments"), list) else []
    used_labels = {str(item.get("label")) for item in attachments if isinstance(item, dict) and item.get("label")}
    attachment_paths = source_attachment_by_path(attachments)
    context_counts = _context_file_use_counts(brief)

    promoted: dict[str, str] = {}
    promotions: list[dict] = []
    if repo_root is not None:
        for path, count in sorted(context_counts.items()):
            if count < PROMOTED_CONTEXT_ATTACHMENT_MIN_USES:
                continue
            target = repo_root / path
            if not target.is_file():
                continue
            size = target.stat().st_size
            if size < PROMOTED_CONTEXT_ATTACHMENT_MIN_BYTES:
                continue
            label = attachment_paths.get(path) or attachment_label_for_path(path, used_labels)
            promoted[path] = label
            promotions.append({"path": path, "label": label, "bytes": size, "work_item_refs": count})
            if path not in attachment_paths:
                attachments.append(
                    {
                        "path": path,
                        "label": label,
                        "kind": "context-source",
                        "sha256": sha256_file(target),
                        "bytes": size,
                        "promoted_from_context_files": True,
                    }
                )
                attachment_paths[path] = label

    if not promoted:
        brief["source_attachments"] = attachments
        return brief

    _rewrite_work_items_for_promotions(brief, promoted)

    brief["source_attachments"] = attachments
    brief["source_attachment_promotions"] = promotions
    return brief


def normalize_runtime_cap(value: object) -> object | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    if isinstance(value, dict):
        if not value:
            return None
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise SystemExit("runtime_cap keys must be non-empty strings")
            if item is None:
                continue
            if isinstance(item, str):
                item = item.strip()
                if not item:
                    continue
            elif not isinstance(item, (bool, int, float, list, dict)):
                raise SystemExit("runtime_cap values must be strings, numbers, booleans, arrays, or objects")
            normalized[key.strip()] = item
        return normalized or None
    raise SystemExit("runtime_cap must be a string or object when supplied")


def normalize_telemetry_policy(value: object) -> dict:
    if value is None:
        return dict(TELEMETRY_POLICY_DEFAULT)

    if not isinstance(value, dict):
        raise SystemExit("telemetry_policy must be an object")

    schema_version = value.get("schema_version", TELEMETRY_POLICY_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version != TELEMETRY_POLICY_SCHEMA_VERSION:
        raise SystemExit(f"telemetry_policy.schema_version must be {TELEMETRY_POLICY_SCHEMA_VERSION}")

    mode = value.get("mode", "standard")
    if not isinstance(mode, str) or mode.strip() not in TELEMETRY_POLICY_MODES:
        raise SystemExit(f"telemetry_policy.mode must be one of {', '.join(TELEMETRY_POLICY_MODES)}")

    normalized = {
        "schema_version": schema_version,
        "mode": mode.strip(),
    }

    raw_text = value.get("raw_text", False)
    if raw_text is not False:
        raise SystemExit("telemetry_policy.raw_text must be false")

    for key in value:
        lowered = str(key).lower()
        if "usd" in lowered or "pricing" in lowered:
            raise SystemExit("telemetry_policy must not contain usd/pricing keys")

    collect = value.get("collect", [])
    if collect is None:
        collect = []
    elif isinstance(collect, str):
        collect = [collect]
    elif not isinstance(collect, list):
        raise SystemExit("telemetry_policy.collect must be a list of collection names")

    normalized["raw_text"] = False
    if collect:
        normalized_collect: list[str] = []
        for index, item in enumerate(collect):
            if not isinstance(item, str) or not item.strip():
                raise SystemExit(f"telemetry_policy.collect[{index}] must be a non-empty string")
            normalized_item = item.strip()
            if normalized_item not in TELEMETRY_COLLECT_ITEMS:
                raise SystemExit("telemetry_policy.collect must be one of " + ", ".join(TELEMETRY_COLLECT_ITEMS))
            if normalized_item not in normalized_collect:
                normalized_collect.append(normalized_item)
        normalized["collect"] = normalized_collect
    elif mode.strip() == "debug":
        normalized["collect"] = list(TELEMETRY_COLLECT_ITEMS)
    else:
        normalized["collect"] = []

    unknown_keys = set(value.keys()) - {"schema_version", "mode", "raw_text", "collect"}
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise SystemExit(f"telemetry_policy contains unsupported keys: {unknown}")

    return normalized


def telemetry_policy_for_mode(mode: str) -> dict:
    policy = dict(TELEMETRY_POLICY_DEFAULT)
    policy["mode"] = mode
    policy["collect"] = list(TELEMETRY_COLLECT_ITEMS) if mode == "debug" else []
    return policy


def normalize_telemetry_mode(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be one of {', '.join(TELEMETRY_POLICY_MODES)}")
    mode = value.strip().lower()
    if mode not in TELEMETRY_POLICY_MODES:
        raise SystemExit(f"{field} must be one of {', '.join(TELEMETRY_POLICY_MODES)}")
    return mode


def normalize_debug_telemetry(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SystemExit("debug_telemetry must be a boolean")
    return "debug" if value else "standard"


def normalize_brief_telemetry_policy(brief: dict) -> dict:
    telemetry_mode = normalize_telemetry_mode(brief.get("telemetry_mode"), field="telemetry_mode")
    debug_mode = normalize_debug_telemetry(brief.get("debug_telemetry"))
    if telemetry_mode is not None and debug_mode is not None and telemetry_mode != debug_mode:
        raise SystemExit("telemetry_mode and debug_telemetry request conflicting modes")

    requested_mode = telemetry_mode or debug_mode
    raw_policy = brief.get("telemetry_policy")
    if raw_policy is None and requested_mode is not None:
        raw_policy = telemetry_policy_for_mode(requested_mode)

    policy = normalize_telemetry_policy(raw_policy)
    if requested_mode is not None and policy["mode"] != requested_mode:
        raise SystemExit("telemetry_mode/debug_telemetry conflicts with telemetry_policy.mode")
    return policy


def normalize_route_policy_degraded_telemetry_waiver(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SystemExit("route_policy_degraded_telemetry_waiver must be an object")
    unknown_keys = set(value) - {"accepted", "reason"}
    if unknown_keys:
        unknown = ", ".join(sorted(str(key) for key in unknown_keys))
        raise SystemExit(f"route_policy_degraded_telemetry_waiver contains unsupported keys: {unknown}")
    accepted = value.get("accepted")
    if not isinstance(accepted, bool):
        raise SystemExit("route_policy_degraded_telemetry_waiver.accepted must be a boolean")
    reason = value.get("reason")
    if accepted and (not isinstance(reason, str) or not reason.strip()):
        raise SystemExit("route_policy_degraded_telemetry_waiver.reason must be a non-empty string when accepted=true")
    normalized = {"accepted": accepted}
    if isinstance(reason, str) and reason.strip():
        normalized["reason"] = reason.strip()
    return normalized


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


def normalize_route_class(value: object, worker_type: str, field: str) -> str | None:
    if worker_type == "research-worker":
        if value is not None:
            raise SystemExit(f"{field} is only valid for worker items, not research-worker items")
        return None
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be one of {', '.join(MANIFEST_WORKER_ROUTE_CLASSES)}")
    normalized = value.strip()
    if normalized not in MANIFEST_WORKER_ROUTE_CLASSES:
        raise SystemExit(f"{field} must be one of {', '.join(MANIFEST_WORKER_ROUTE_CLASSES)}")
    return normalized


def route_class_reason(value: object, fallback: str, field: str) -> str:
    if value is None:
        return fallback
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"{field} must be a non-empty string when supplied")
    return value.strip()


def path_bucket(path: str) -> str:
    if DOC_PATH_RE.search(path):
        return "docs"
    if TEST_PATH_RE.search(path):
        return "test"
    if CODE_PATH_RE.search(path):
        return "code"
    return "other"


def top_level(path: str) -> str:
    return path.split("/", 1)[0]


def has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _route_class_inference_signals(item: dict, branch_context: dict) -> dict:
    owned_paths = item.get("owned_paths", [])
    item_text = " ".join(
        str(value)
        for value in [
            item.get("objective", ""),
            " ".join(item.get("verification", [])),
            " ".join(item.get("dod", [])),
        ]
    ).lower()
    all_text = " ".join(
        str(value)
        for value in [
            branch_context.get("objective", ""),
            branch_context.get("scope", ""),
            branch_context.get("branch_risk", ""),
            item_text,
        ]
    ).lower()
    return {
        "owned_buckets": [path_bucket(path) for path in owned_paths if isinstance(path, str)],
        "has_complex": has_any_term(all_text, COMPLEX_TERMS),
        "has_mechanical": has_any_term(item_text, MECHANICAL_TERMS),
        "changed_surface": {top_level(path) for path in owned_paths if isinstance(path, str) and path.strip()},
        "has_dependencies": bool(item.get("depends_on")) or bool(branch_context.get("depends_on")),
        "path_count": len(owned_paths),
    }


def infer_route_class(
    item: dict,
    *,
    branch_context: dict,
    explicit_route_class: str | None,
) -> tuple[str | None, str]:
    worker_type = item["worker_type"]
    item_id = item["id"]
    if worker_type == "research-worker":
        return None, route_class_reason(
            item.get("route_class_reason"),
            "research_worker_read_only_info",
            f"branch {branch_context['id']} work item {item_id} route_class_reason",
        )
    if explicit_route_class is not None:
        return explicit_route_class, route_class_reason(
            item.get("route_class_reason"),
            "explicit_brief",
            f"branch {branch_context['id']} work item {item_id} route_class_reason",
        )

    signals = _route_class_inference_signals(item, branch_context)
    owned_buckets = signals["owned_buckets"]
    has_complex = signals["has_complex"]
    has_mechanical = signals["has_mechanical"]
    changed_surface = signals["changed_surface"]
    has_dependencies = signals["has_dependencies"]
    path_count = signals["path_count"]

    if owned_buckets and all(bucket == "docs" for bucket in owned_buckets) and not has_complex:
        return "docs", "inferred_docs_only"
    if has_mechanical and not has_complex and path_count <= 3 and not has_dependencies:
        return "mechanical", "inferred_mechanical_small_surface"
    if (
        has_complex
        or path_count >= 4
        or len(changed_surface) >= 3
        or (has_dependencies and path_count >= 2)
        or branch_context.get("contention_risk") is True
    ):
        return "complex-code", "inferred_complex_or_cross_module"
    if path_count <= 2 and not has_dependencies:
        if owned_buckets and all(bucket == "test" for bucket in owned_buckets):
            return "small-edit", "inferred_small_test_only"
        if path_count == 1 or len(changed_surface) <= 1:
            return "small-edit", "inferred_small_edit"
    return DEFAULT_WORKER_ROUTE_CLASS, "inferred_normal_code_default"


def branch_id(index: int) -> str:
    return f"B{index:02d}"


def wave_id(index: int) -> str:
    return f"wave-{index:02d}"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def usable_python_command() -> str:
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"
    return "python3"


def normalize_verification_command(command: str) -> str:
    if re.match(r"^\s*python(?=\s|$)", command) is None:
        return command
    python_command = usable_python_command()
    if python_command == "python":
        return command
    return re.sub(
        r"^(\s*)python(?=\s|$)",
        lambda match: f"{match.group(1)}{python_command}",
        command,
        count=1,
    )


def normalize_verification_commands(commands: list[str]) -> list[str]:
    return [normalize_verification_command(command) for command in commands]


def package_skeletons_for_path(path: str) -> list[str]:
    parts = Path(path).parts
    if len(parts) < 2 or not path.endswith(".py"):
        return []
    if parts[-1] == "__init__.py" or parts[0] in {"tests", "test", "docs", "scripts"}:
        return []
    skeletons: list[str] = []
    for index in range(1, len(parts)):
        package_parts = parts[:index]
        if package_parts[-1] in {"src", "lib"}:
            continue
        if not all(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in package_parts if part not in {"src", "lib"}
        ):
            continue
        skeleton = "/".join([*package_parts, "__init__.py"])
        if skeleton not in skeletons:
            skeletons.append(skeleton)
    return skeletons


def assign_package_skeleton_context_files(work_items: list[dict], repo_root: Path | None = None) -> None:
    for item in work_items:
        owned_paths = item.get("owned_paths")
        context_files = item.get("context_files")
        if not isinstance(owned_paths, list) or not isinstance(context_files, list):
            continue
        additions: list[str] = []
        for path in list(owned_paths):
            if not isinstance(path, str):
                continue
            for skeleton in package_skeletons_for_path(path):
                if skeleton in context_files:
                    continue
                if repo_root is None:
                    if not Path(skeleton).exists():
                        continue
                elif not (repo_root / skeleton).exists():
                    continue
                if skeleton not in context_files and skeleton not in additions:
                    additions.append(skeleton)
        if additions:
            context_files.extend(additions)


def bullets(values: object, fallback: str = "- none") -> str:
    if isinstance(values, str):
        values = [values.strip()] if values.strip() else []
    if isinstance(values, list):
        values = [item.strip() for item in values if isinstance(item, str) and item.strip()]
    else:
        values = []
    if not values:
        return fallback
    return "\n".join(f"- {value}" for value in values)


def branch_scope_text(branch: dict) -> str:
    return branch.get("scope") or (
        f"Bounded to the owned paths, work items, verification commands, and stop conditions listed for {branch['id']}."
    )


def branch_additional_validators(branch: dict) -> str:
    tests = (
        [item.strip() for item in branch.get("tests", []) if isinstance(item, str) and item.strip()]
        if isinstance(branch.get("tests"), list)
        else []
    )
    if tests:
        return bullets(tests)
    validators: list[str] = []
    seen: set[str] = set()
    for item in branch.get("work_items", []):
        if not isinstance(item, dict):
            continue
        for command in item.get("verification", []):
            if isinstance(command, str) and command.strip() and command not in seen:
                validators.append(command.strip())
                seen.add(command)
    return bullets(validators, fallback="- No branch-level validators beyond work-item verification commands.")


PREMIUM_ROUTE_MARKERS = ("demanding", "heavy", "premium", "pro", "gpt-5.5", "gpt-5.4")


def cheaper_worker_ladder(default_ladder: list[str]) -> list[str]:
    cheap = [
        alias for alias in default_ladder if not any(marker in str(alias).lower() for marker in PREMIUM_ROUTE_MARKERS)
    ]
    if cheap:
        return cheap[-2:]
    return default_ladder[-1:]


def deterministic_route_class_ladders(worker_policy: dict) -> dict[str, list[str]]:
    default_ladder = worker_policy.get("default_ladder")
    if not isinstance(default_ladder, list) or not default_ladder:
        default_ladder = list(DEFAULT_WORKER_LADDER)
    default_ladder = [str(alias) for alias in default_ladder if isinstance(alias, str) and alias]
    if not default_ladder:
        default_ladder = list(DEFAULT_WORKER_LADDER)
    cheap_ladder = cheaper_worker_ladder(default_ladder)
    cheapest = [cheap_ladder[-1]] if cheap_ladder else [default_ladder[-1]]
    return {
        "mechanical": cheapest,
        "docs": cheapest,
        "small-edit": cheap_ladder,
        "normal-code": cheap_ladder,
        "complex-code": default_ladder,
        "custom": default_ladder,
    }


def _valid_route_class_ladder(ladder: object, *, default_aliases: list[str], allowed_set: set[str]) -> list[str] | None:
    if not isinstance(ladder, list) or not ladder:
        return None
    aliases = [alias for alias in ladder if isinstance(alias, str) and alias]
    if len(aliases) != len(ladder):
        return None
    if any(alias not in allowed_set for alias in aliases):
        return None
    if [alias for alias in default_aliases if alias in aliases] != aliases:
        return None
    return aliases


def valid_route_class_ladders(worker_policy: dict, route_classes: object) -> dict[str, list[str]] | None:
    default_ladder = worker_policy.get("default_ladder")
    allowed_routes = worker_policy.get("allowed_routes")
    if not isinstance(default_ladder, list) or not default_ladder:
        return None
    if not isinstance(allowed_routes, list) or not allowed_routes:
        allowed_routes = default_ladder
    default_aliases = [alias for alias in default_ladder if isinstance(alias, str) and alias]
    allowed_aliases = [alias for alias in allowed_routes if isinstance(alias, str) and alias]
    if not default_aliases or not allowed_aliases or not isinstance(route_classes, dict):
        return None
    allowed_set = set(allowed_aliases)
    normalized: dict[str, list[str]] = {}
    for route_class in MANIFEST_WORKER_ROUTE_CLASSES:
        aliases = _valid_route_class_ladder(
            route_classes.get(route_class), default_aliases=default_aliases, allowed_set=allowed_set
        )
        if aliases is None:
            return None
        normalized[route_class] = aliases
    return normalized


def normalize_worker_model_policy(policy: dict) -> dict:
    if not isinstance(policy, dict):
        policy = WORKER_MODEL_POLICY
    normalized = dict(policy)
    route_classes = valid_route_class_ladders(normalized, normalized.get("route_classes"))
    if route_classes is not None:
        normalized["route_classes"] = route_classes
        normalized["route_class_ladder_source"] = normalized.get(
            "route_class_ladder_source",
            "goal_config.model_policies.worker_model_policy.route_classes",
        )
    else:
        normalized["route_classes"] = deterministic_route_class_ladders(normalized)
        normalized["route_class_ladder_source"] = "preflight_deterministic_cheap_subsequences"
    return normalized


def route_availability_verified_from_check(goal_config_check: dict | None) -> bool | None:
    if not isinstance(goal_config_check, dict):
        return None
    summary = goal_config_check.get("summary") if isinstance(goal_config_check.get("summary"), dict) else {}
    if summary.get("route_verification_status") == "routes_verified":
        return True
    accepted = summary.get("accepted_route_count")
    return isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0


def route_recommendations_enabled(goal_config_check: dict | None) -> bool:
    verified = route_availability_verified_from_check(goal_config_check)
    return verified is True


def accepted_route_roles(goal_config_check: dict | None) -> set[str]:
    if not isinstance(goal_config_check, dict):
        return set()
    accepted: set[str] = set()
    routes = goal_config_check.get("accepted_routes")
    if not isinstance(routes, list):
        return accepted
    for route in routes:
        if not isinstance(route, dict):
            continue
        for key in ("role", "alias"):
            value = route.get(key)
            if isinstance(value, str) and value.strip():
                accepted.add(value.strip())
    return accepted


def ordered_worker_policy_roles(worker_policy: dict) -> list[str]:
    normalized = normalize_worker_model_policy(worker_policy)
    roles: list[str] = []

    def add(values: object) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, str) and value.strip() and value not in roles:
                roles.append(value)

    add(normalized.get("default_ladder"))
    add(normalized.get("allowed_routes"))
    route_classes = normalized.get("route_classes")
    if isinstance(route_classes, dict):
        for route_class in MANIFEST_WORKER_ROUTE_CLASSES:
            add(route_classes.get(route_class))
    return roles


def route_coverage_summary(goal_config_check: dict | None, worker_policy: dict) -> dict:
    required = ordered_worker_policy_roles(worker_policy)
    accepted = sorted(accepted_route_roles(goal_config_check))
    accepted_set = set(accepted)
    missing = [role for role in required if role not in accepted_set]
    return {
        "required_worker_roles": required,
        "accepted_route_roles": accepted,
        "missing_worker_roles": missing,
        "worker_route_coverage": "pass" if required and not missing else "missing" if missing else "not_applicable",
    }


def prune_worker_policy_to_accepted_routes(worker_policy: dict, goal_config_check: dict | None) -> tuple[dict, dict]:
    normalized = normalize_worker_model_policy(worker_policy)
    accepted = accepted_route_roles(goal_config_check)
    if not accepted:
        return normalized, {
            "status": "not_applied",
            "reason": "goal-config check did not include accepted route role evidence",
            "accepted_route_roles": [],
            "removed_worker_roles": [],
        }

    original_roles = ordered_worker_policy_roles(normalized)
    removed = [role for role in original_roles if role not in accepted]
    if not removed:
        return normalized, {
            "status": "not_needed",
            "accepted_route_roles": sorted(accepted),
            "removed_worker_roles": [],
        }

    pruned = dict(normalized)
    default_ladder = [
        role for role in normalized.get("default_ladder", []) if isinstance(role, str) and role in accepted
    ]
    allowed_routes = [
        role for role in normalized.get("allowed_routes", []) if isinstance(role, str) and role in accepted
    ]
    if not default_ladder:
        return normalized, {
            "status": "blocked",
            "reason": "accepted route evidence removes every configured worker default route",
            "accepted_route_roles": sorted(accepted),
            "removed_worker_roles": removed,
        }
    if not allowed_routes:
        allowed_routes = list(default_ladder)
    pruned["default_ladder"] = default_ladder
    pruned["allowed_routes"] = allowed_routes
    pruned_classes: dict[str, list[str]] = {}
    route_classes = normalized.get("route_classes") if isinstance(normalized.get("route_classes"), dict) else {}
    for route_class in MANIFEST_WORKER_ROUTE_CLASSES:
        ladder = route_classes.get(route_class)
        filtered = (
            [role for role in ladder if isinstance(role, str) and role in accepted] if isinstance(ladder, list) else []
        )
        pruned_classes[route_class] = filtered or list(default_ladder)
    pruned["route_classes"] = pruned_classes
    pruned["route_class_ladder_source"] = "goal_config_check.accepted_routes_pruned_unverified_worker_aliases"
    pruned["route_policy_pruning"] = {
        "status": "pruned",
        "accepted_route_roles": sorted(accepted),
        "removed_worker_roles": removed,
        "reason": "worker routes not present in accepted goal-config smoke/discovery evidence were removed before runtime",
    }
    return pruned, dict(pruned["route_policy_pruning"])


def sanitize_and_prune_runtime_goal_config(config: dict, goal_config_check: dict | None) -> tuple[dict, dict]:
    sanitized = sanitized_runtime_goal_config(config)
    policies = sanitized.setdefault("model_policies", {})
    worker_policy = (
        policies.get("worker_model_policy", WORKER_MODEL_POLICY) if isinstance(policies, dict) else WORKER_MODEL_POLICY
    )
    pruned_worker_policy, pruning = prune_worker_policy_to_accepted_routes(worker_policy, goal_config_check)
    if isinstance(policies, dict):
        policies["worker_model_policy"] = pruned_worker_policy
    sanitized["route_policy_pruning"] = pruning
    return sanitized, pruning


def route_deferred_text(goal_config_check: dict | None) -> str:
    verified = route_availability_verified_from_check(goal_config_check)
    if verified is None:
        return "route availability is unverified because no current goal-config route check was supplied; capture a fresh model catalog or smoke-check accepted routes before selecting runtime aliases"
    if verified is False:
        return "route availability not verified by the supplied goal-config check; capture a fresh model catalog or smoke-check accepted routes before selecting runtime aliases"
    return ""


def route_class_ladder_guidance(
    worker_policy: dict, *, recommendations_enabled: bool = True, deferred_text: str = ""
) -> str:
    if not recommendations_enabled:
        return (
            f"- deferred: {deferred_text or 'route availability must be verified before recommending worker aliases'}"
        )
    route_classes = worker_policy.get("route_classes") if isinstance(worker_policy.get("route_classes"), dict) else {}
    lines = []
    for route_class in MANIFEST_WORKER_ROUTE_CLASSES:
        ladder = route_classes.get(route_class)
        if not isinstance(ladder, list) or not ladder:
            continue
        lines.append(f"- {route_class}: {CONTRACT.format_worker_ladder(ladder)}")
    return "\n".join(lines) if lines else "- no route-class ladder guidance recorded"


def work_item_route_ladder(item: dict, worker_policy: dict) -> list[str] | None:
    if item.get("worker_type") == RESEARCH_WORKER_TYPE:
        return None
    route_class = item.get("route_class")
    route_classes = worker_policy.get("route_classes") if isinstance(worker_policy.get("route_classes"), dict) else {}
    ladder = route_classes.get(route_class)
    if isinstance(ladder, list) and ladder and all(isinstance(alias, str) and alias for alias in ladder):
        return list(ladder)
    return None


def add_route_ladder_recommendations(
    items: list[dict], worker_policy: dict, *, recommendations_enabled: bool = True
) -> list[dict]:
    updated = []
    for item in items:
        normalized = dict(item)
        ladder = work_item_route_ladder(normalized, worker_policy) if recommendations_enabled else None
        if ladder:
            normalized["route_class_recommended_ladder"] = ladder
            normalized["route_class_ladder_source"] = worker_policy.get(
                "route_class_ladder_source", "worker_model_policy.route_classes"
            )
        updated.append(normalized)
    return updated


def format_work_items(branch_id_value: str, items: list[dict]) -> str:
    if not items:
        return "- No work items supplied; preflight should ask for or synthesize worker-sized items."
    chunks = []
    for idx, item in enumerate(items, start=1):
        item_id = item.get("id") or f"W{idx:02d}"
        packet_id = item.get("packet_id") or f"{branch_id_value}-{item_id}"
        recommended_ladder = item.get("route_class_recommended_ladder")
        recommended_ladder_text = (
            CONTRACT.format_worker_ladder(recommended_ladder)
            if isinstance(recommended_ladder, list) and recommended_ladder
            else "n/a"
        )
        chunks.append(
            "\n".join(
                [
                    f"### {item_id}: {item.get('title') or 'Work item'}",
                    f"Worker packet id: {packet_id}",
                    f"Worker type: {item.get('worker_type', 'worker')}",
                    f"Route class: {item.get('route_class', 'n/a')}",
                    f"Route class reason code: {item.get('route_class_reason', 'n/a')}",
                    f"Recommended ladder: {recommended_ladder_text}",
                    f"Objective: {item.get('objective', 'Objective not supplied.')}",
                    "Owned paths:",
                    bullets(item.get("owned_paths", [])),
                    "Context files:",
                    bullets(item.get("context_files", [])),
                    "Source attachment refs:",
                    bullets(item.get("source_attachment_refs", [])),
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


def normalize_work_items(
    items: object, branch_id_value: str, *, branch_context: dict, repo_root: Path | None = None
) -> list[dict]:
    if not isinstance(items, list):
        raise SystemExit(f"branch {branch_id_value} work_items must be a list")
    if len(items) < 1 or len(items) > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit(
            f"branch {branch_id_value} must have 1 to {MAX_WORKER_PACKETS_PER_BRANCH} worker packets; split or synthesize work items"
        )
    normalized = []
    seen_ids = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"branch {branch_id_value} work_items entries must be objects")
        item_id = require_safe_label(str(item.get("id") or f"W{index:02d}"), f"branch {branch_id_value} work item id")
        if item_id in seen_ids:
            raise SystemExit(f"branch {branch_id_value} duplicate work item id: {item_id}")
        seen_ids.add(item_id)
        packet_id = require_safe_label(
            f"{branch_id_value}-{item_id}", f"branch {branch_id_value} work item {item_id} packet_id"
        )
        objective = nonempty_text(item.get("objective"))
        if not objective:
            raise SystemExit(f"branch {branch_id_value} work item {item_id} requires objective")
        normalized_item = {
            **item,
            "id": item_id,
            "packet_id": packet_id,
            "objective": objective,
            "owned_paths": [
                require_relative_path(value, f"branch {branch_id_value} work item {item_id} owned_paths")
                for value in require_string_list(
                    item.get("owned_paths"), f"branch {branch_id_value} work item {item_id} owned_paths", min_items=1
                )
            ],
            "context_files": [
                require_relative_path(value, f"branch {branch_id_value} work item {item_id} context_files")
                for value in require_string_list(
                    item.get("context_files", []), f"branch {branch_id_value} work item {item_id} context_files"
                )
            ],
            "source_attachment_refs": require_string_list(
                item.get("source_attachment_refs", []),
                f"branch {branch_id_value} work item {item_id} source_attachment_refs",
            ),
            "depends_on": require_string_list(
                item.get("depends_on", []), f"branch {branch_id_value} work item {item_id} depends_on"
            ),
            "verification": normalize_verification_commands(
                require_string_list(
                    item.get("verification"), f"branch {branch_id_value} work item {item_id} verification", min_items=1
                )
            ),
            "dod": require_string_list(
                item.get("dod"), f"branch {branch_id_value} work item {item_id} dod", min_items=1
            ),
        }
        worker_type = normalize_worker_type(
            item.get("worker_type"), f"branch {branch_id_value} work item {item_id} worker_type"
        )
        explicit_route_class = normalize_route_class(
            item.get("route_class"), worker_type, f"branch {branch_id_value} work item {item_id} route_class"
        )
        normalized_item["worker_type"] = worker_type
        route_class, reason = infer_route_class(
            normalized_item,
            branch_context=branch_context,
            explicit_route_class=explicit_route_class,
        )
        if route_class is not None:
            normalized_item["route_class"] = route_class
        normalized_item["route_class_reason"] = reason
        normalized.append(normalized_item)
    known_ids = {item["id"] for item in normalized}
    order = {item["id"]: index for index, item in enumerate(normalized)}
    for index, item in enumerate(normalized):
        for dep in item["depends_on"]:
            if dep not in known_ids:
                raise SystemExit(f"branch {branch_id_value} work item {item['id']} depends on unknown work item: {dep}")
            if order[dep] >= index:
                raise SystemExit(
                    f"branch {branch_id_value} work item {item['id']} depends_on must reference only prior work item ids: {dep}"
                )
    assign_package_skeleton_context_files(normalized, repo_root=repo_root)
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


def max_ready_width(items: list[dict], *, id_field: str = "id", depends_on_field: str = "depends_on") -> int:
    remaining: dict[str, dict] = {
        str(item.get(id_field)): item for item in items if isinstance(item, dict) and str(item.get(id_field))
    }
    levels: dict[str, int] = {}
    while remaining:
        progressed = False
        for item_id, item in list(remaining.items()):
            deps = [str(dep) for dep in item.get(depends_on_field, []) if isinstance(dep, str)]
            if any(dep in remaining for dep in deps):
                continue
            dep_levels = [levels.get(dep, 0) for dep in deps]
            levels[item_id] = 1 + (max(dep_levels) if dep_levels else 0)
            remaining.pop(item_id)
            progressed = True
        if not progressed:
            for item_id in list(remaining):
                levels[item_id] = 1
                remaining.pop(item_id)
    widths: dict[int, int] = {}
    for level in levels.values():
        widths[level] = widths.get(level, 0) + 1
    return max(widths.values(), default=0)


def ready_width_metadata(
    item_count: int, current_ready_width: int, capacity: int, *, scope: str, identifier: str | None = None
) -> dict[str, object]:
    usable_capacity = min(max(capacity, 1), item_count) if item_count else 0
    if identifier:
        scope_name = f"{scope} {identifier}"
    else:
        scope_name = scope
    reason = None
    if usable_capacity > 1 and current_ready_width < usable_capacity:
        reason = (
            f"{scope_name} exposes {current_ready_width} ready item(s) against usable capacity {usable_capacity};"
            " this limits parallel fill under current dependencies"
        )
    return {
        "ready_width": current_ready_width,
        "usable_capacity": usable_capacity,
        "underutilization_reason": reason,
    }


def _path_value_set(values: object) -> set[str]:
    return {str(value) for value in values if isinstance(value, str) and value.strip()}


def _path_sets_overlap(a: set[str], b: set[str]) -> bool:
    for left in a:
        for right in b:
            if path_is_covered(left, right) or path_is_covered(right, left):
                return True
    return False


def item_dependency_profile(item: dict, repo_root: Path | None) -> dict[str, set[str]]:
    owned = _path_value_set(item.get("owned_paths", []))
    context = _path_value_set(item.get("context_files", []))
    required: set[str] = set()
    for command in item.get("verification", []):
        if not isinstance(command, str) or not command.strip():
            continue
        command_paths, _modules = command_required_paths(command, repo_root)
        required.update(command_paths)
    return {
        "owned_paths": owned,
        "context_files": context,
        "required_paths": required,
    }


def _has_concrete_path_dependency(dep_profile: dict[str, set[str]], item_profile: dict[str, set[str]]) -> bool:
    return (
        _path_sets_overlap(dep_profile["owned_paths"], item_profile["owned_paths"])
        or _path_sets_overlap(dep_profile["owned_paths"], item_profile["context_files"])
        or _path_sets_overlap(dep_profile["owned_paths"], item_profile["required_paths"])
        or _path_sets_overlap(dep_profile["required_paths"], item_profile["owned_paths"])
    )


def optimize_worker_dependencies(branch: dict, *, repo_root: Path | None, branch_worker_reasons: list[str]) -> None:
    work_items = branch.get("work_items")
    if not isinstance(work_items, list) or len(work_items) <= 1:
        return

    profiles = {item["id"]: item_dependency_profile(item, repo_root) for item in work_items if isinstance(item, dict)}
    removed_edges: list[tuple[str, str]] = []

    for item in work_items:
        if not isinstance(item, dict):
            continue
        depends_on = list(item.get("depends_on", []))
        if not depends_on:
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        kept: list[str] = []
        for dependency in depends_on:
            if not isinstance(dependency, str):
                continue
            dep_profile = profiles.get(dependency)
            if dep_profile is None:
                kept.append(dependency)
                continue
            if _has_concrete_path_dependency(dep_profile, profiles[item_id]):
                kept.append(dependency)
                continue
            removed_edges.append((dependency, item_id))
        if len(kept) != len(depends_on):
            item["depends_on"] = kept

    for dependency, dependent in removed_edges:
        append_reason_once(
            branch_worker_reasons,
            f"Removed worker dependency {dependency}->{dependent} in branch {branch.get('id')} after path/verification dependency check found no direct data coupling.",
        )


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
                raise SystemExit(
                    f"branch {branch['id']} depends_on must reference only prior branch ids; invalid dependency: {dep_id}"
                )
            seen.add(dep_id)
            deps.append(dep_id)
        branch["depends_on"] = deps


def dependency_waves(branches: list[dict], wave_size: int) -> list[dict]:
    levels: dict[str, int] = {}
    grouped: dict[int, list[dict]] = {}
    for branch in branches:
        deps = branch.get("depends_on") if isinstance(branch.get("depends_on"), list) else []
        dep_levels = [levels.get(dep, 1) for dep in deps if isinstance(dep, str)]
        level = 1 + (max(dep_levels) if dep_levels else 0)
        levels[branch["id"]] = level
        grouped.setdefault(level, []).append(branch)

    waves: list[dict] = []
    for level in sorted(grouped):
        level_branches = grouped[level]
        for offset in range(0, len(level_branches), wave_size):
            wave_branches = level_branches[offset : offset + wave_size]
            waves.append(
                {
                    "id": wave_id(len(waves) + 1),
                    "branches": [branch["id"] for branch in wave_branches],
                    "dependency_level": level,
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
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "readiness.json",
        "goal-config-selection.json",
        "preflight.pipeline.json",
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


def _annotate_worker_parallelism(
    branch: dict, branch_dependency_reasons: dict[str, list[str]], serial_reasons: list[str]
) -> None:
    worker_items = branch.get("work_items") if isinstance(branch.get("work_items"), list) else []
    worker_max_ready_width = max_ready_width(worker_items)
    worker_parallelism = branch["worker_parallelism"]
    worker_serial_reasons = worker_parallelism["serial_reasons"]
    worker_capacity = branch.get("max_active_worker_packets", MAX_WORKER_PACKETS_PER_BRANCH)
    if len(worker_items) == 1 and worker_capacity > 1:
        append_reason_once(
            worker_serial_reasons,
            f"Branch {branch['id']} declares one worker item, so no additional independent worker packet exists to fill capacity.",
        )
    elif worker_max_ready_width < min(worker_capacity, len(worker_items)):
        append_reason_once(
            worker_serial_reasons,
            f"Branch {branch['id']} worker DAG limits initial and maximal ready parallelism below worker capacity.",
        )
    if len(worker_items) > 2 and longest_work_item_chain(worker_items) >= len(worker_items) - 1:
        append_reason_once(
            worker_serial_reasons,
            f"Branch {branch['id']} worker dependency chain serializes most work by explicit depends_on topology.",
        )
    ready_meta = ready_width_metadata(
        len(worker_items),
        worker_max_ready_width,
        worker_capacity,
        scope="worker",
        identifier=branch["id"],
    )
    worker_parallelism["ready_width_metadata"] = ready_meta
    underutilization_reason = ready_meta["underutilization_reason"]
    if isinstance(underutilization_reason, str):
        append_reason_once(worker_serial_reasons, underutilization_reason)
    if branch_dependency_reasons.get(branch["id"]):
        for reason in branch_dependency_reasons[branch["id"]]:
            append_reason_once(serial_reasons, reason)


def _resolve_waves(brief: dict, branches: list[dict], max_active: int) -> tuple[list[dict], dict[str, str]]:
    waves = brief.get("waves") or dependency_waves(branches, max_active)
    if len(waves) > MAX_WAVES:
        raise SystemExit(f"waves must not exceed {MAX_WAVES}")
    branch_ids = {branch["id"] for branch in branches}
    seen_wave_ids = set()
    seen_wave_branches = []
    wave_by_branch: dict[str, str] = {}
    for wave in waves:
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
    return waves, wave_by_branch


def _annotate_branch_parallelism(
    branches: list[dict], max_active: int, serial_reasons: list[str]
) -> tuple[int, dict[str, object]]:
    initial_ready = len([branch for branch in branches if not branch.get("depends_on")])
    branch_max_ready_width = max_ready_width(branches)
    parallelization_meta = ready_width_metadata(len(branches), branch_max_ready_width, max_active, scope="branch")
    if initial_ready < min(max_active, len(branches)):
        append_reason_once(
            serial_reasons,
            "Initial ready branch count underfills max_active_branch_agents because of explicit branch depends_on topology.",
        )
    if branch_max_ready_width < min(max_active, len(branches)) and initial_ready <= 1:
        append_reason_once(
            serial_reasons,
            f"Branch DAG max ready width remains {branch_max_ready_width} against usable branch capacity {min(max_active, len(branches))}; this limits parallel fill by design.",
        )
    underutilization_reason = parallelization_meta["underutilization_reason"]
    if isinstance(underutilization_reason, str):
        append_reason_once(serial_reasons, underutilization_reason)
    branch_chain_lengths: dict[str, int] = {}
    for branch in branches:
        dep_lengths = [branch_chain_lengths.get(dep, 1) for dep in branch.get("depends_on", [])]
        branch_chain_lengths[branch["id"]] = 1 + (max(dep_lengths) if dep_lengths else 0)
    if len(branches) > 2 and max(branch_chain_lengths.values(), default=0) >= len(branches) - 1:
        append_reason_once(
            serial_reasons, "Branch dependency chain serializes most work by explicit branch depends_on topology."
        )
    return branch_max_ready_width, parallelization_meta


def _build_branch_record(original: dict, idx: int, *, job_id: str, repo_root: Path | None) -> dict:
    bid = original.get("id") or branch_id(idx)
    bid = require_safe_id(str(bid).upper(), "branch id")
    branch_name = require_branch_name(original.get("branch_name") or f"{job_id}-{bid.lower()}")
    worktree_path = require_relative_path(original.get("worktree_path") or f".worktrees/{branch_name}", "worktree_path")
    max_workers = require_worker_limit(original.get("max_active_worker_packets", MAX_WORKER_PACKETS_PER_BRANCH))
    original_worker_parallelism = (
        original.get("worker_parallelism") if isinstance(original.get("worker_parallelism"), dict) else {}
    )
    worker_serial_reason = nonempty_text(original.get("worker_serial_reason"))
    worker_serial_reasons = normalize_reason_list(
        original.get("worker_serial_reasons", original_worker_parallelism.get("serial_reasons")),
        worker_serial_reason,
    )
    worker_parallelization_rationale = nonempty_text(original.get("worker_parallelization_rationale")) or nonempty_text(
        original_worker_parallelism.get("parallelization_rationale")
    )
    branch_context = {
        "id": bid,
        "objective": nonempty_text(original.get("objective")),
        "scope": nonempty_text(original.get("scope")),
        "branch_risk": nonempty_text(original.get("branch_risk")),
        "depends_on": original.get("depends_on", []) if isinstance(original.get("depends_on"), list) else [],
        "contention_risk": any(
            isinstance(original.get(key), str) and original.get(key, "").strip()
            for key in ["contention_reason", "worker_contention_reason"]
        ),
    }
    work_items = normalize_work_items(
        original.get("work_items", []),
        bid,
        branch_context=branch_context,
        repo_root=repo_root,
    )
    owned_paths = derived_owned_paths(work_items)
    if max_workers < MAX_WORKER_PACKETS_PER_BRANCH:
        append_reason_once(
            worker_serial_reasons,
            f"Branch {bid} caps worker concurrency at {max_workers}, below the package maximum of {MAX_WORKER_PACKETS_PER_BRANCH}.",
        )
    return {
        **original,
        "work_items": work_items,
        "id": bid,
        "branch_name": branch_name,
        "worktree_path": worktree_path,
        "owned_paths": owned_paths,
        "prompt": require_relative_path(original.get("prompt") or f"branches/{bid}.prompt.md", "prompt"),
        "status_path": require_relative_path(
            original.get("status_path") or f"branches/{bid}.status.json", "status_path"
        ),
        "review_path": require_relative_path(
            original.get("review_path") or f"branches/{bid}.review.json", "review_path"
        ),
        "pre_review_gate_path": require_relative_path(
            original.get("pre_review_gate_path") or CONTRACT.pre_review_gate_path(bid), "pre_review_gate_path"
        ),
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


def normalize_brief(
    brief: dict, *, default_base_ref: str = "main", validate_base_ref: bool = True, repo_root: Path | None = None
) -> dict:
    if "job_id" not in brief:
        raise SystemExit("brief must include job_id")
    if not brief.get("branches"):
        raise SystemExit("brief must include synthesized branches before bundle generation")

    job_id = slug(brief["job_id"])
    base_ref = require_branch_name(str(brief.get("base_ref") or default_base_ref), "base_ref")
    if (
        validate_base_ref
        and repo_root is not None
        and _is_git_repo(repo_root)
        and not _git_ref_exists(repo_root, base_ref)
    ):
        raise SystemExit(f"base_ref does not exist in repository refs: {base_ref!r}")
    max_active = require_agent_limit(brief.get("max_active_branch_agents", MAX_ACTIVE_BRANCH_AGENTS))
    serial_reason = nonempty_text(brief.get("serial_reason"))
    serial_reasons = normalize_reason_list(brief.get("serial_reasons"), serial_reason)
    parallelization_rationale = nonempty_text(brief.get("parallelization_rationale"))
    branches = []
    branch_dependency_reasons: dict[str, list[str]] = {}
    for idx, original in enumerate(brief["branches"], start=1):
        branch = _build_branch_record(original, idx, job_id=job_id, repo_root=repo_root)
        branch_dependency_reasons[branch["id"]] = []
        branches.append(branch)

    ensure_unique_branch_values(branches)
    for branch in branches:
        optimize_worker_dependencies(
            branch,
            repo_root=repo_root,
            branch_worker_reasons=branch["worker_parallelism"]["serial_reasons"],
        )
    for branch in branches:
        _annotate_worker_parallelism(branch, branch_dependency_reasons, serial_reasons)

    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        raise SystemExit(
            f"brief has more than {DEFAULT_TOTAL_BRANCH_CAP} branches; max is {MAX_WAVES} waves of {MAX_ACTIVE_BRANCH_AGENTS}"
        )
    if len(branches) == 1:
        append_reason_once(
            serial_reasons, "Only one branch is declared, so branch orchestration cannot fill multiple branch slots."
        )
    if max_active < MAX_ACTIVE_BRANCH_AGENTS:
        append_reason_once(
            serial_reasons,
            f"max_active_branch_agents is {max_active}, below the package maximum of {MAX_ACTIVE_BRANCH_AGENTS}.",
        )
    preflight_lite_advice = brief.get("preflight_lite_advice", [])
    if not isinstance(preflight_lite_advice, list):
        raise SystemExit("preflight_lite_advice must be an array when supplied")
    telemetry_policy = normalize_brief_telemetry_policy(brief)
    degraded_telemetry_waiver = normalize_route_policy_degraded_telemetry_waiver(
        brief.get("route_policy_degraded_telemetry_waiver")
    )

    waves, wave_by_branch = _resolve_waves(brief, branches, max_active)
    for branch in branches:
        branch["wave"] = wave_by_branch[branch["id"]]
    normalize_branch_dependencies(branches)
    for branch in branches:
        work_items = branch.get("work_items") if isinstance(branch.get("work_items"), list) else []
        has_context = any(
            isinstance(item, dict) and isinstance(item.get("context_files"), list) and bool(item.get("context_files"))
            for item in work_items
        )
        if branch.get("depends_on") and not has_context:
            branch["dependency_context_reason"] = (
                nonempty_text(branch.get("dependency_context_reason"))
                or "No direct context_files declared; runtime must inspect completed dependency branch status/review artifacts before launching this branch."
            )

    branch_max_ready_width, parallelization_meta = _annotate_branch_parallelism(branches, max_active, serial_reasons)

    return {
        **brief,
        "job_id": job_id,
        "base_ref": base_ref,
        "artifact_policy": nonempty_text(brief.get("artifact_policy"))
        or "Preserve the full orchestration bundle under the selected bundle directory; commit generated preflight prompts only when the user explicitly asks, and commit runtime status/review/audit artifacts only when the main prompt or user explicitly requires them.",
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
            "ready_width": branch_max_ready_width,
            "ready_width_metadata": parallelization_meta,
            "serial_reasons": serial_reasons,
            "parallelization_rationale": parallelization_rationale
            or f"Keep up to {max_active} branch orchestrators active; defer only branches whose depends_on branch ids are not complete.",
            "wave_execution": "Use waves as scheduling/order groups only. Keep branch orchestrator slots saturated up to max_active_branch_agents; when a branch finishes and capacity is freed, launch the next eligible branch whose depends_on branch ids are complete.",
            "dependency_policy": "Branch depends_on entries are explicit prior-branch dependencies; branches without unresolved depends_on entries are eligible whenever capacity is available.",
        },
        "branches": branches,
        "waves": waves,
        "preflight_lite_advice": preflight_lite_advice,
        "telemetry_policy": telemetry_policy,
        "route_policy_degraded_telemetry_waiver": degraded_telemetry_waiver,
        "source_attachments": normalize_source_attachments(brief.get("source_attachments"), repo_root=repo_root),
        "runtime_cap": normalize_runtime_cap(brief.get("runtime_cap")),
    }


BILLING_FORBIDDEN_RE = re.compile(r"usd|dollar|pricing|price", re.IGNORECASE)


def validate_goal_config(config: dict) -> None:
    if config.get("schema_version") != 1:
        raise SystemExit("goal_config.schema_version must be 1")
    if BILLING_FORBIDDEN_RE.search(json.dumps(config, sort_keys=True)):
        raise SystemExit("goal_config must not contain billing, price, pricing, dollar, or USD fields")
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        raise SystemExit("goal_config.models must be a non-empty object")
    harnesses = config.get("harnesses")
    if not isinstance(harnesses, dict) or not harnesses:
        raise SystemExit("goal_config.harnesses must be a non-empty object")
    ladders = config.get("model_ladders")
    if not isinstance(ladders, dict) or not isinstance(ladders.get("worker"), list) or not ladders["worker"]:
        raise SystemExit("goal_config.model_ladders.worker must be a non-empty array")
    for ladder_name, ladder in ladders.items():
        if not isinstance(ladder, list) or not ladder:
            raise SystemExit(f"goal_config.model_ladders.{ladder_name} must be a non-empty array")
        for role in ladder:
            if role not in models:
                raise SystemExit(f"goal_config.model_ladders.{ladder_name} references unknown role: {role}")
    policies = config.get("model_policies")
    if not isinstance(policies, dict):
        raise SystemExit("goal_config.model_policies must be present; regenerate with goal-config")
    for key in ("worker_model_policy", "review_model_policy", "amender_model_policy", "lite_model_policy"):
        if not isinstance(policies.get(key), dict):
            raise SystemExit(f"goal_config.model_policies.{key} must be an object")


def sanitized_runtime_goal_config(config: dict) -> dict:
    sanitized = copy.deepcopy(config)
    telemetry = sanitized.get("telemetry")
    if not isinstance(telemetry, dict):
        telemetry = {}
    telemetry["raw_text"] = False
    sanitized["telemetry"] = telemetry
    return sanitized


def _is_int_in_range(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _compatibility_cap_defects(branch_cap: object, worker_cap: object) -> list[str]:
    defects: list[str] = []
    if not _is_int_in_range(branch_cap, 1, MAX_ACTIVE_BRANCH_AGENTS):
        defects.append(
            f"aggressiveness.max_active_branch_agents must be an integer from 1 to {MAX_ACTIVE_BRANCH_AGENTS}; got {branch_cap!r}"
        )
    if not _is_int_in_range(worker_cap, 1, MAX_WORKER_PACKETS_PER_BRANCH):
        defects.append(
            f"aggressiveness.max_active_worker_packets must be an integer from 1 to {MAX_WORKER_PACKETS_PER_BRANCH}; got {worker_cap!r}"
        )
    return defects


def _compatibility_check_defects(
    config_validation_mode: object, check_mode: object, check_status: object, check: dict | None
) -> list[str]:
    defects: list[str] = []
    if check is None:
        defects.append("goal_config_check is required when goal_config is supplied")
    elif check_status != "pass":
        defects.append(f"goal_config_check.status must be pass; got {check_status!r}")
    if config_validation_mode in {"smoke", "debug"} and check_mode not in {"smoke", "discover"}:
        defects.append(
            f"config validation mode {config_validation_mode!r} requires a smoke/discovery check report; got check mode {check_mode!r}"
        )
    return defects


def preflight_compatibility_summary(config: dict | None, check: dict | None) -> dict:
    if config is None:
        return {"status": "not_configured", "defects": []}
    aggressiveness = config.get("aggressiveness") if isinstance(config.get("aggressiveness"), dict) else {}
    branch_cap = aggressiveness.get("max_active_branch_agents")
    worker_cap = aggressiveness.get("max_active_worker_packets")
    defects = _compatibility_cap_defects(branch_cap, worker_cap)

    validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
    config_validation_mode = validation.get("mode")
    check_mode = check.get("mode") if isinstance(check, dict) else None
    check_status = check.get("status") if isinstance(check, dict) else None
    defects.extend(_compatibility_check_defects(config_validation_mode, check_mode, check_status, check))

    policies = config.get("model_policies") if isinstance(config.get("model_policies"), dict) else {}
    worker_policy = normalize_worker_model_policy(policies.get("worker_model_policy", WORKER_MODEL_POLICY))
    route_coverage = route_coverage_summary(check, worker_policy)
    telemetry = config.get("telemetry") if isinstance(config.get("telemetry"), dict) else {}
    token_summary = {}
    if isinstance(check, dict):
        summary = check.get("summary") if isinstance(check.get("summary"), dict) else {}
        token_summary = summary.get("token_telemetry") if isinstance(summary.get("token_telemetry"), dict) else {}
    raw_text = telemetry.get("raw_text")
    return {
        "status": "pass" if not defects else "failed",
        "defects": defects,
        "caps": {
            "max_active_branch_agents": branch_cap,
            "max_active_worker_packets": worker_cap,
            "preflight_max_active_branch_agents": MAX_ACTIVE_BRANCH_AGENTS,
            "preflight_max_active_worker_packets": MAX_WORKER_PACKETS_PER_BRANCH,
        },
        "config_validation_mode": config_validation_mode,
        "check_mode": check_mode,
        "check_status": check_status,
        "telemetry": {
            "config_mode": telemetry.get("mode"),
            "config_raw_text": raw_text,
            "manifest_raw_text": False,
            "policy_transformation": "raw_text_true_is_sanitized_to_manifest_raw_text_false"
            if raw_text is True
            else "none",
            "token_telemetry": token_summary,
        },
        "effort": config.get("effort") if isinstance(config.get("effort"), dict) else {},
        "route_coverage": route_coverage,
        "route_policy_pruning": config.get("route_policy_pruning")
        if isinstance(config.get("route_policy_pruning"), dict)
        else {},
    }


def load_goal_config(path: Path | None) -> dict | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"goal config must be a JSON object: {path}")
    validate_goal_config(data)
    return data


def load_goal_config_check(path: Path | None) -> dict | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"goal config check must be a JSON object: {path}")
    if data.get("status") != "pass":
        raise SystemExit(f"goal config check must have status=pass: {path}")
    failures = data.get("failures")
    if failures not in ([], None):
        raise SystemExit(f"goal config check must not contain failures: {path}")
    return data


def apply_goal_config_to_brief(brief: dict, config: dict | None) -> dict:
    if config is None:
        return brief
    updated = dict(brief)
    aggressiveness = config.get("aggressiveness") if isinstance(config.get("aggressiveness"), dict) else {}
    max_active = aggressiveness.get("max_active_branch_agents")
    if "max_active_branch_agents" not in updated and isinstance(max_active, int) and not isinstance(max_active, bool):
        updated["max_active_branch_agents"] = max_active
    max_workers = aggressiveness.get("max_active_worker_packets")
    if isinstance(max_workers, int) and not isinstance(max_workers, bool):
        branches = []
        for branch in updated.get("branches", []):
            if isinstance(branch, dict):
                branch = dict(branch)
                if "max_active_worker_packets" not in branch:
                    branch["max_active_worker_packets"] = max_workers
            branches.append(branch)
        updated["branches"] = branches
    preflight_intent = config.get("preflight_intent") if isinstance(config.get("preflight_intent"), dict) else {}
    telemetry_mode = preflight_intent.get("telemetry_mode")
    if (
        isinstance(telemetry_mode, str)
        and "telemetry_mode" not in updated
        and "debug_telemetry" not in updated
        and "telemetry_policy" not in updated
    ):
        updated["telemetry_mode"] = telemetry_mode
    updated["goal_config"] = config
    return updated


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


COMMAND_PATH_EXTENSIONS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".csv",
    ".sh",
)
PYTHON_BIN_NAMES = {"python", "python3", "py"}
RUNTIME_INFRA_PATH_PREFIXES = (
    "skills/_goal_shared/",
    "skills/goal-branch-orchestrator/",
    "skills/goal-main-orchestrator/",
    "skills/goal-plan-amender/",
    "skills/goal-preflight/",
)


def path_is_covered(path: str, owner: str) -> bool:
    path_norm = path.rstrip("/")
    owner_norm = owner.rstrip("/")
    return path_norm == owner_norm or path_norm.startswith(owner_norm + "/") or owner_norm.startswith(path_norm + "/")


def clean_command_token(token: str) -> str:
    return token.strip().strip("'\"`").rstrip(".,;:)")


def is_runtime_infra_path(path: str) -> bool:
    return path.startswith(RUNTIME_INFRA_PATH_PREFIXES)


def command_path_tokens(command: str) -> list[str]:
    paths = []
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        cleaned = clean_command_token(token)
        if not cleaned or cleaned.startswith("-") or "://" in cleaned or cleaned.startswith("$"):
            continue
        if "<" in cleaned or ">" in cleaned:
            continue
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if cleaned.startswith("/") or cleaned.startswith("../"):
            continue
        if is_runtime_infra_path(cleaned):
            continue
        if any(cleaned.endswith(ext) for ext in COMMAND_PATH_EXTENSIONS) or "/" in cleaned:
            try:
                paths.append(require_relative_path(cleaned, "command_path"))
            except SystemExit:
                continue
    return sorted(set(paths))


def python_modules_from_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    modules = []
    for index, token in enumerate(tokens):
        if token == "-m" and index > 0 and tokens[index - 1] in PYTHON_BIN_NAMES and index + 1 < len(tokens):
            module = tokens[index + 1].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module):
                modules.append(module)
    return modules


def module_candidate_paths(module: str, repo_root: Path | None) -> list[str]:
    module_path = module.replace(".", "/")
    candidates = [
        f"{module_path}.py",
        f"{module_path}/__init__.py",
        f"{module_path}/__main__.py",
        f"src/{module_path}.py",
        f"src/{module_path}/__init__.py",
        f"src/{module_path}/__main__.py",
    ]
    if repo_root is None:
        return candidates
    existing = [path for path in candidates if (repo_root / path).exists()]
    return existing or candidates


IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][A-Za-z0-9_\.]*)\s+import|import\s+([A-Za-z_][A-Za-z0-9_\.]*))", re.MULTILINE
)


def import_dependency_paths(path: str, repo_root: Path | None) -> list[str]:
    if repo_root is None or not path.endswith(".py"):
        return []
    target = repo_root / path
    if not target.is_file() or target.stat().st_size > 256_000:
        return []
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    deps: list[str] = []
    for match in IMPORT_RE.finditer(text):
        module = match.group(1) or match.group(2) or ""
        root = module.split(".", 1)[0]
        if root in {
            "argparse",
            "collections",
            "dataclasses",
            "json",
            "os",
            "pathlib",
            "pytest",
            "sys",
            "typing",
            "unittest",
        }:
            continue
        deps.extend(
            candidate for candidate in module_candidate_paths(module, repo_root) if (repo_root / candidate).exists()
        )
        if "." in module:
            deps.extend(
                candidate for candidate in module_candidate_paths(root, repo_root) if (repo_root / candidate).exists()
            )
    return sorted(set(deps))


def command_required_paths(command: str, repo_root: Path | None) -> tuple[list[str], list[str]]:
    paths = command_path_tokens(command)
    modules = python_modules_from_command(command)
    inferred = []
    for module in modules:
        inferred.extend(module_candidate_paths(module, repo_root))
    for path in list(paths):
        inferred.extend(import_dependency_paths(path, repo_root))
    return sorted(set(paths + inferred)), sorted(set(modules))


def infer_execution_strategy(repo_root: Path, branches: list[dict]) -> dict:
    has_src = (repo_root / "src").is_dir()
    has_project_config = (
        (repo_root / "pyproject.toml").is_file()
        or (repo_root / "setup.py").is_file()
        or (repo_root / "setup.cfg").is_file()
    )
    module_commands = []
    src_module_paths = []
    for branch in branches:
        for item in branch.get("work_items", []):
            if not isinstance(item, dict):
                continue
            for command in item.get("verification", []):
                if not isinstance(command, str):
                    continue
                modules = python_modules_from_command(command)
                if modules:
                    module_commands.append(command)
                for module in modules:
                    src_module_paths.extend(
                        path for path in module_candidate_paths(module, repo_root) if path.startswith("src/")
                    )
    if has_src and has_project_config and module_commands:
        strategy = "editable_install_src_package"
        setup_commands = ["python3 -m pip install -e ."]
        validation_env = {"PYTHONPATH": "src"}
        reason = "src layout with project metadata and python -m validation detected; use one editable-install strategy before CLI/import acceptance commands"
    elif has_src:
        strategy = "pythonpath_src"
        setup_commands = []
        validation_env = {"PYTHONPATH": "src"}
        reason = "src layout detected without a package-install validation signal; run validation with PYTHONPATH=src"
    else:
        strategy = "repo_root_direct"
        setup_commands = []
        validation_env = {}
        reason = "no src package layout detected; run validation from repository root"
    return {
        "schema_version": 1,
        "id": strategy,
        "strategy": strategy,
        "setup_commands": setup_commands,
        "validation_env": validation_env,
        "module_entrypoint_commands": sorted(set(module_commands)),
        "module_entrypoint_paths": sorted(set(src_module_paths)),
        "reason": reason,
    }


class _BranchOwnership(NamedTuple):
    branch_id: str
    branch_paths: list[str]
    dependency_paths: list[str]
    other_branch_paths: dict[str, list[str]]


def _owning_branch_candidates(
    uncovered_paths: list[str],
    ownership: _BranchOwnership,
    work_item_id: object,
    dependency_recommendations: list[dict],
) -> list[dict]:
    owning_branch_candidates: list[dict] = []
    for path in uncovered_paths:
        owners = [
            other_id
            for other_id, paths in ownership.other_branch_paths.items()
            if any(path_is_covered(path, owner) for owner in paths)
        ]
        if owners:
            owning_branch_candidates.append({"path": path, "owners": owners})
            dependency_recommendations.append(
                {
                    "branch_id": ownership.branch_id,
                    "work_item_id": work_item_id,
                    "path": path,
                    "recommended_depends_on": owners,
                    "reason": "verification command requires a path owned by a sibling branch",
                }
            )
    return owning_branch_candidates


def _ownership_command_record(
    command: str,
    *,
    work_item_id: object,
    ownership: _BranchOwnership,
    repo_root: Path | None,
    dependency_recommendations: list[dict],
) -> dict:
    required_paths, modules = command_required_paths(command, repo_root)
    outside_paths = [
        path for path in required_paths if not any(path_is_covered(path, owner) for owner in ownership.branch_paths)
    ]
    dependency_covered = [
        path for path in outside_paths if any(path_is_covered(path, owner) for owner in ownership.dependency_paths)
    ]
    uncovered_paths = [path for path in outside_paths if path not in dependency_covered]
    owning_branch_candidates = _owning_branch_candidates(
        uncovered_paths, ownership, work_item_id, dependency_recommendations
    )
    missing_owned_paths = [
        path for path in uncovered_paths if not any(path == item["path"] for item in owning_branch_candidates)
    ]
    status = "pass" if not uncovered_paths else "needs_review"
    return {
        "branch_id": ownership.branch_id,
        "work_item_id": work_item_id,
        "command": command,
        "status": status,
        "module_entrypoints": modules,
        "required_paths": required_paths,
        "branch_owned_paths": ownership.branch_paths,
        "dependency_covered_paths": dependency_covered,
        "uncovered_paths": uncovered_paths,
        "owning_branch_candidates": owning_branch_candidates,
        "missing_owned_paths": missing_owned_paths,
        "recommended_action": (
            "none"
            if status == "pass"
            else "co-locate required paths in this branch or add explicit depends_on and merge sequencing before dispatch"
        ),
    }


def build_ownership_feasibility(branches: list[dict], repo_root: Path | None) -> dict:
    branch_owned = {
        branch["id"]: [path for path in branch.get("owned_paths", []) if isinstance(path, str)]
        for branch in branches
        if isinstance(branch.get("id"), str)
    }
    records = []
    dependency_recommendations = []
    for branch in branches:
        branch_id = branch["id"]
        ownership = _BranchOwnership(
            branch_id=branch_id,
            branch_paths=branch_owned.get(branch_id, []),
            dependency_paths=[path for dep_id in branch.get("depends_on", []) for path in branch_owned.get(dep_id, [])],
            other_branch_paths={
                other_id: paths
                for other_id, paths in branch_owned.items()
                if other_id != branch_id and other_id not in set(branch.get("depends_on", []))
            },
        )
        for item in branch.get("work_items", []):
            if not isinstance(item, dict):
                continue
            work_item_id = item.get("id", "unknown")
            for command in item.get("verification", []):
                if not isinstance(command, str) or not command.strip():
                    continue
                records.append(
                    _ownership_command_record(
                        command,
                        work_item_id=work_item_id,
                        ownership=ownership,
                        repo_root=repo_root,
                        dependency_recommendations=dependency_recommendations,
                    )
                )
    needs_review = [record for record in records if record.get("status") != "pass"]
    return {
        "schema_version": 1,
        "status": "needs_review" if needs_review else "pass",
        "command_count": len(records),
        "needs_review_count": len(needs_review),
        "dependency_recommendations": dependency_recommendations,
        "commands": records,
    }


def build_route_contract(
    goal_config_check: dict | None, *, goal_config_check_sha256: str | None, worker_policy: dict
) -> dict:
    summary = compact_goal_config_check_summary(goal_config_check or {}) if isinstance(goal_config_check, dict) else {}
    accepted_route_count = summary.get("accepted_route_count")
    coverage = route_coverage_summary(goal_config_check, worker_policy)
    missing_worker_roles = coverage.get("missing_worker_roles", [])
    verified = route_availability_verified_from_check(goal_config_check)
    if missing_worker_roles:
        verified = False
    return {
        "schema_version": 1,
        "policy_router": worker_policy.get("policy_router")
        or ("goal-config-v1" if goal_config_check_sha256 else "deterministic-v1"),
        "policy_version": worker_policy.get("policy_version") or "preflight-route-contract-v1",
        "goal_config_check_path": "goal-config.check.json" if goal_config_check_sha256 else None,
        "goal_config_check_sha256": goal_config_check_sha256,
        "goal_config_check_status": summary.get("status", "not_configured"),
        "route_model_availability_verified": verified is True,
        "route_recommendations_enabled": verified is True,
        "accepted_route_count": accepted_route_count
        if isinstance(accepted_route_count, int) and not isinstance(accepted_route_count, bool)
        else 0,
        "accepted_route_roles": coverage.get("accepted_route_roles", []),
        "required_worker_roles": coverage.get("required_worker_roles", []),
        "missing_worker_roles": missing_worker_roles,
        "catalog_refresh_required": verified is not True,
        "route_catalog_source": "goal_config_check" if goal_config_check_sha256 else "branch_runtime_model_catalog",
        "diagnostic_note": route_deferred_text(goal_config_check)
        if verified is not True
        else "route availability verified by goal-config check hash",
    }


def build_runtime_index(manifest: dict) -> dict:
    branches = []
    worker_packet_count = 0
    for branch in manifest.get("branches", []):
        if not isinstance(branch, dict):
            continue
        workers = []
        for item in branch.get("work_items", []):
            if not isinstance(item, dict):
                continue
            packet_id = item.get("packet_id") or f"{branch.get('id')}-{item.get('id')}"
            worker_type = item.get("worker_type", "worker")
            if worker_type in {"research", "research-worker"}:
                artifact_root = "research"
                status_name = "research.json"
                role = "research-worker"
            else:
                artifact_root = "workers"
                status_name = "status.json"
                role = "worker"
            workers.append(
                {
                    "packet_id": packet_id,
                    "work_item_id": item.get("id"),
                    "worker_type": role,
                    "status_path": f"{artifact_root}/{packet_id}/{status_name}",
                    "summary_path": f"{artifact_root}/{packet_id}/packet.summary.json",
                    "telemetry_path": f"{artifact_root}/{packet_id}/telemetry.json",
                    "load_policy": "targeted_runtime_only",
                }
            )
        worker_packet_count += len(workers)
        branches.append(
            {
                "id": branch.get("id"),
                "prompt": branch.get("prompt"),
                "status_path": branch.get("status_path"),
                "review_path": branch.get("review_path"),
                "pre_review_gate_path": branch.get("pre_review_gate_path"),
                "worker_scheduler_path": branch.get("worker_parallelism", {}).get("scheduler_path")
                if isinstance(branch.get("worker_parallelism"), dict)
                else None,
                "workers": workers,
            }
        )
    return {
        "schema_version": 1,
        "kind": "goal-runtime-index",
        "job_id": manifest.get("job_id"),
        "manifest_path": "job.manifest.json",
        "main_prompt": manifest.get("main_prompt"),
        "runtime_rules": {
            "path": manifest.get("runtime_rules_path"),
            "sha256": manifest.get("runtime_rules_sha256"),
            "load_policy": "read_before_dispatch",
        },
        "route_contract": {
            "sha256": manifest.get("route_contract_sha256"),
            "catalog_refresh_required": manifest.get("route_contract", {}).get("catalog_refresh_required")
            if isinstance(manifest.get("route_contract"), dict)
            else None,
        },
        "execution_strategy": {
            "id": manifest.get("execution_strategy", {}).get("id")
            if isinstance(manifest.get("execution_strategy"), dict)
            else None,
            "setup_commands": manifest.get("execution_strategy", {}).get("setup_commands", [])
            if isinstance(manifest.get("execution_strategy"), dict)
            else [],
            "validation_env": manifest.get("execution_strategy", {}).get("validation_env", {})
            if isinstance(manifest.get("execution_strategy"), dict)
            else {},
        },
        "ownership_feasibility": {
            "status": manifest.get("ownership_feasibility", {}).get("status")
            if isinstance(manifest.get("ownership_feasibility"), dict)
            else None,
            "needs_review_count": manifest.get("ownership_feasibility", {}).get("needs_review_count")
            if isinstance(manifest.get("ownership_feasibility"), dict)
            else None,
        },
        "branches": branches,
        "machine_artifacts": [
            {"path": "preflight.lint.json", "role": "preflight-lint", "load_policy": "summary_only"},
            {"path": "PREFLIGHT_REPORT.md", "role": "human-report", "load_policy": "initial_epoch_summary"},
            {"path": "readiness.json", "role": "readiness", "load_policy": "generated_by_render_goal_bootloader"},
            {"path": "run.trace.jsonl", "role": "runtime-trace", "load_policy": "query_with_targeted_scripts"},
            {"path": "telemetry.summary.json", "role": "telemetry", "load_policy": "summary_only"},
        ],
        "large_artifact_policy": "Use this index and role-specific paths first; load large machine artifacts only through targeted scripts or compact summaries.",
        "counts": {
            "branch_count": len(branches),
            "worker_packet_count": worker_packet_count,
            "machine_artifact_count": 5,
        },
    }


def provenance_path(path: Path, bundle_dir: Path) -> dict:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(bundle_dir.resolve())
        source_path = relative.as_posix()
        source_path_type = "bundle_relative"
    except ValueError:
        source_path = resolved.as_posix()
        source_path_type = "absolute"
    return {
        "source_path": source_path,
        "source_path_type": source_path_type,
        "source_basename": path.name,
        "source_sha256": sha256_file(path) if path.exists() else None,
    }


def ensure_bundle_dirs(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ["branches"]:
        (bundle_dir / dirname).mkdir(exist_ok=True)


def render_branch_waves(waves: list[dict]) -> str:
    lines = []
    for wave in waves:
        level = wave.get("dependency_level")
        level_label = f" dependency_level={level}" if isinstance(level, int) else ""
        lines.append(f"- {wave['id']}{level_label}: {', '.join(wave['branches'])}")
    return "\n".join(lines)


def render_branch_dependencies(branches: list[dict]) -> str:
    lines = []
    for branch in branches:
        deps = branch.get("depends_on", [])
        lines.append(f"- {branch['id']}: {', '.join(deps) if deps else 'none'}")
    return "\n".join(lines)


def repo_runtime_gate_summary(repo_status: dict) -> str:
    if repo_status.get("repo_is_git") is False:
        return "blocked - repository root is not a git work tree; runtime branch/worktree orchestration requires entering or initializing a git work tree, or an explicit supported no-git runtime mode"
    if repo_status.get("base_ref_status") == "missing":
        return f"blocked - base_ref does not exist: {repo_status.get('base_ref')}"
    return "pass - git runtime gate is satisfied"


def bundle_git_ignore_warning_record(repo_root: Path, bundle_dir: Path) -> dict:
    if not _is_git_repo(repo_root):
        return {}
    try:
        relative = bundle_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return {}
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "check-ignore", "-q", relative.as_posix()],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode == 0:
        return {}
    return {
        "code": "bundle_inside_git_worktree_not_ignored",
        "severity": "warning",
        "path": relative.as_posix(),
        "message": f"Bundle git ignore warning: {relative.as_posix()} is inside the git work tree and is not ignored; generated bundle files may dirty the repository.",
    }


def goal_config_report_lines(goal_config: dict | None, goal_config_check: dict | None, manifest: dict) -> list[str]:
    if not goal_config:
        return []
    check_summary = compact_goal_config_check_summary(goal_config_check or {})
    accepted = check_summary.get("accepted_route_count")
    route_label = "not verified" if accepted in (None, 0) else f"{accepted} accepted route(s)"
    route_status = check_summary.get("route_verification_status")
    provenance = manifest.get("goal_config_provenance", {})
    config_provenance = provenance.get("config", {}) if isinstance(provenance, dict) else {}
    source = config_provenance.get("source_path") if isinstance(config_provenance, dict) else None
    return [
        f"Goal config: {goal_config.get('profile', 'custom')} copied to goal.config.json from {source or 'supplied config'}; preflight config check status={check_summary.get('status')}.",
        f"Goal config route availability: {route_label}; route_verification_status={route_status}; this is distinct from config-schema/preflight compatibility status.",
    ]


def render_source_attachments(attachments: list[dict]) -> str:
    if not attachments:
        return "- none"
    lines = []
    for item in attachments:
        detail = f"{item.get('path')} ({item.get('kind', 'context')})"
        if item.get("sha256"):
            detail += f" sha256={item['sha256']}"
        if item.get("bytes") is not None:
            detail += f" bytes={item['bytes']}"
        lines.append(f"- {item.get('label', item.get('path'))}: {detail}")
    return "\n".join(lines)


def has_inline_source_payload(text: str) -> bool:
    if len(INLINE_SOURCE_TUPLE_RE.findall(text)) >= 10:
        return True
    if len(text) < 400:
        return False
    lowered = text.lower()
    return "ft10 = [" in lowered or ("operation list" in lowered and text.count("[") >= 4 and text.count("]") >= 4)


def render_branch_source_contract(brief: dict, bundle_root: str) -> str:
    goal = str(brief.get("goal", "")).strip()
    source_summary = str(brief.get("source_summary", "")).strip()
    collected = "\n".join(
        text
        for text in [
            goal,
            source_summary,
            "\n".join(str(item) for item in brief.get("required_evidence", []) if isinstance(item, str)),
            "\n".join(str(item) for item in brief.get("final_dod", []) if isinstance(item, str)),
        ]
        if text
    )
    exact_source_referenced = bool(EXACT_SOURCE_RE.search(collected))
    lines = [
        f"- Main prompt source: {bundle_root}/main.prompt.md",
        f"- Source summary: {source_summary or 'not supplied'}",
    ]
    attachments = render_source_attachments(brief.get("source_attachments", []))
    if attachments != "- none":
        lines.extend(["", "Source attachments:", attachments])
    if exact_source_referenced and has_inline_source_payload(goal):
        if len(goal) <= INLINE_SOURCE_CONTRACT_MAX_CHARS:
            lines.extend(["", "Inline exact-source goal payload:", "```text", goal, "```"])
        else:
            lines.append(
                f"- Inline exact-source payload is in main.prompt.md and is {len(goal)} chars; use that file as the source of truth before pass."
            )
    elif exact_source_referenced:
        lines.append(
            "- Exact source data is referenced; compare against main.prompt.md and any listed source attachments before pass."
        )
    return "\n".join(lines)


def render_runtime_cap(value: object) -> str:
    if value is None:
        return "- none declared"
    if isinstance(value, str):
        return f"- {value}"
    if isinstance(value, dict):
        lines = []
        for key in sorted(value):
            lines.append(f"- {key}: {value[key]}")
        return "\n".join(lines) if lines else "- none declared"
    return f"- {value}"


def render_runtime_rules_text(brief: dict) -> str:
    template = (Path(__file__).resolve().parents[1] / "assets" / "runtime-rules.template.md").read_text(
        encoding="utf-8"
    )
    return template.format(
        job_id=brief["job_id"],
        base_ref=brief["base_ref"],
        telemetry_policy_mode=brief["telemetry_policy"]["mode"],
        main_scheduler_path=CONTRACT.MAIN_SCHEDULER_PATH,
    )


def compact_goal_config_summary(config: dict, *, manifest_telemetry_policy: dict | None = None) -> dict:
    validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
    telemetry = config.get("telemetry") if isinstance(config.get("telemetry"), dict) else {}
    manifest_policy = manifest_telemetry_policy if isinstance(manifest_telemetry_policy, dict) else {}
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    discovery_summary = config.get("discovery_summary") if isinstance(config.get("discovery_summary"), dict) else {}
    validation_summary = config.get("summary") if isinstance(config.get("summary"), dict) else {}
    compact_models = {}
    for role, model in models.items():
        if not isinstance(model, dict):
            continue
        compact_models[role] = {
            "alias": model.get("alias"),
            "harness": model.get("harness"),
            "provider": model.get("provider"),
            "model": model.get("model"),
        }
    return {
        "profile": config.get("profile"),
        "effort_profile": config.get("effort_profile"),
        "base_effort_profile": config.get("base_effort_profile"),
        "aggressiveness": config.get("aggressiveness", {}),
        "validation_mode": validation.get("mode"),
        "telemetry_mode": telemetry.get("mode"),
        "telemetry_raw_text": telemetry.get("raw_text"),
        "source_config_telemetry": {
            "mode": telemetry.get("mode"),
            "raw_text": telemetry.get("raw_text"),
        },
        "manifest_telemetry_policy": {
            "mode": manifest_policy.get("mode"),
            "raw_text": manifest_policy.get("raw_text"),
        },
        "telemetry_interpretation": "source config telemetry is provenance only; job.manifest.json telemetry_policy is authoritative at runtime",
        "model_ladders": config.get("model_ladders", {}),
        "models": compact_models,
        "effort": config.get("effort") if isinstance(config.get("effort"), dict) else {},
        "source_route_provenance": {
            "accepted_route_count": discovery_summary.get(
                "accepted_route_count", validation_summary.get("accepted_route_count")
            ),
            "route_model_availability_verified": discovery_summary.get(
                "accepted_route_count", validation_summary.get("accepted_route_count")
            )
            not in (None, 0),
            "note": "copied source config metadata is provenance only; goal_config_check_summary is the current preflight check result",
        },
        "route_policy_pruning": config.get("route_policy_pruning", {}),
        "compatibility": config.get("compatibility", {}),
    }


def compact_goal_config_check_summary(check: dict) -> dict:
    summary = check.get("summary") if isinstance(check.get("summary"), dict) else {}
    accepted = summary.get("accepted_route_count")
    route_verified = isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0
    route_status = check.get("route_verification_status") or summary.get("route_verification_status")
    if not route_status:
        route_status = (
            "routes_verified"
            if route_verified
            else "schema_pass_routes_not_checked"
            if check.get("status") == "pass"
            else check.get("status")
        )
    return {
        "status": check.get("status"),
        "schema_status": check.get("status"),
        "route_verification_status": route_status,
        "route_model_availability_verified": route_verified,
        "mode": check.get("mode"),
        "check_mode": check.get("check_mode"),
        "config_validation_mode": check.get("config_validation_mode"),
        "accepted_route_count": accepted,
        "accepted_route_roles": sorted(accepted_route_roles(check)),
        "rejected_route_count": summary.get("rejected_route_count"),
        "skipped_route_count": summary.get("skipped_route_count"),
        "unvisited_route_count": summary.get("unvisited_route_count"),
        "checked_role_count": summary.get("checked_role_count"),
        "harness_count": summary.get("harness_count"),
        "failure_count": summary.get("failure_count"),
        "token_telemetry": summary.get("token_telemetry", {}),
    }


def preflight_input_precedence(original_brief: dict, normalized_brief: dict, config: dict | None) -> dict:
    aggressiveness = (
        config.get("aggressiveness")
        if isinstance(config, dict) and isinstance(config.get("aggressiveness"), dict)
        else {}
    )
    config_preflight_intent = (
        config.get("preflight_intent")
        if isinstance(config, dict) and isinstance(config.get("preflight_intent"), dict)
        else {}
    )
    original_branches = original_brief.get("branches") if isinstance(original_brief.get("branches"), list) else []
    original_branch_by_id = {
        str(branch.get("id") or branch_id(index)): branch
        for index, branch in enumerate(original_branches, start=1)
        if isinstance(branch, dict)
    }
    branch_caps = {}
    for branch in normalized_brief.get("branches", []):
        if not isinstance(branch, dict):
            continue
        bid = str(branch.get("id"))
        original = original_branch_by_id.get(bid, {})
        branch_has_explicit_cap = isinstance(original, dict) and "max_active_worker_packets" in original
        config_worker_cap_applied = (
            isinstance(aggressiveness.get("max_active_worker_packets"), int)
            and not isinstance(aggressiveness.get("max_active_worker_packets"), bool)
            and not branch_has_explicit_cap
        )
        branch_caps[bid] = {
            "brief_value": original.get("max_active_worker_packets") if isinstance(original, dict) else None,
            "applied_value": branch.get("max_active_worker_packets"),
            "source": "brief"
            if branch_has_explicit_cap
            else ("goal_config.aggressiveness.max_active_worker_packets" if config_worker_cap_applied else "default"),
        }
    branch_has_explicit_cap = "max_active_branch_agents" in original_brief
    config_branch_cap_applied = (
        isinstance(aggressiveness.get("max_active_branch_agents"), int)
        and not isinstance(aggressiveness.get("max_active_branch_agents"), bool)
        and not branch_has_explicit_cap
    )
    return {
        "goal_config_applied": config is not None,
        "max_active_branch_agents": {
            "brief_value": original_brief.get("max_active_branch_agents"),
            "applied_value": normalized_brief.get("max_active_branch_agents"),
            "source": "brief"
            if branch_has_explicit_cap
            else ("goal_config.aggressiveness.max_active_branch_agents" if config_branch_cap_applied else "default"),
        },
        "max_active_worker_packets_by_branch": branch_caps,
        "telemetry_policy": {
            "brief_value": original_brief.get("telemetry_policy")
            or original_brief.get("telemetry_mode")
            or original_brief.get("debug_telemetry"),
            "config_preflight_intent": config_preflight_intent.get("telemetry_mode"),
            "applied_mode": normalized_brief.get("telemetry_policy", {}).get("mode"),
            "source": "brief"
            if any(key in original_brief for key in ("telemetry_policy", "telemetry_mode", "debug_telemetry"))
            else (
                "goal_config.preflight_intent.telemetry_mode"
                if config_preflight_intent.get("telemetry_mode")
                else "default"
            ),
        },
        "note": "Explicit brief caps override goal_config aggressiveness defaults; goal_config caps apply only when the brief omits the corresponding cap. Explicit brief telemetry fields override goal_config preflight_intent.",
    }


def manifest_from_normalized_brief(brief: dict, bundle_dir: Path | None = None) -> dict:
    goal_config = brief.get("goal_config") if isinstance(brief.get("goal_config"), dict) else None
    goal_config_check = brief.get("goal_config_check") if isinstance(brief.get("goal_config_check"), dict) else None
    model_policies = goal_config.get("model_policies", {}) if goal_config else {}
    worker_model_policy = normalize_worker_model_policy(model_policies.get("worker_model_policy", WORKER_MODEL_POLICY))
    worker_route_recommendations_enabled = route_recommendations_enabled(goal_config_check)
    repo_root = Path(brief["repo_root"]) if isinstance(brief.get("repo_root"), str) and brief["repo_root"] else None
    goal_config_check_sha256 = (
        sha256_file(bundle_dir / "goal-config.check.json")
        if bundle_dir is not None and (bundle_dir / "goal-config.check.json").is_file()
        else None
    )
    route_contract = build_route_contract(
        goal_config_check, goal_config_check_sha256=goal_config_check_sha256, worker_policy=worker_model_policy
    )
    execution_strategy = (
        brief.get("execution_strategy")
        if isinstance(brief.get("execution_strategy"), dict)
        else infer_execution_strategy(repo_root or Path.cwd(), brief["branches"])
    )
    ownership_feasibility = build_ownership_feasibility(brief["branches"], repo_root)
    return {
        "job_id": brief["job_id"],
        "title": brief.get("title") or brief["job_id"],
        "goal": brief.get("goal", ""),
        "source_summary": brief.get("source_summary", ""),
        "required_evidence": brief.get("required_evidence", []),
        "final_dod": brief.get("final_dod", []),
        "main_prompt": "main.prompt.md",
        "runtime_rules_path": brief.get("runtime_rules_path", RUNTIME_RULES_PATH),
        "runtime_rules_sha256": brief.get("runtime_rules_sha256"),
        "base_ref": brief["base_ref"],
        "artifact_policy": brief["artifact_policy"],
        "cleanup_policy": brief["cleanup_policy"],
        "max_active_branch_agents": brief["max_active_branch_agents"],
        "parallelization": brief["parallelization"],
        "adaptation_policy": CONTRACT.ADAPTATION_POLICY,
        "worker_model_policy": worker_model_policy,
        "amender_model_policy": model_policies.get("amender_model_policy", AMENDER_MODEL_POLICY),
        "lite_model_policy": model_policies.get("lite_model_policy", LITE_MODEL_POLICY),
        "lite_advisor_policy": LITE_ADVISOR_POLICY,
        "research_worker_policy": RESEARCH_WORKER_POLICY,
        "review_model_policy": model_policies.get("review_model_policy", REVIEW_MODEL_POLICY),
        "route_contract": route_contract,
        "route_contract_sha256": sha256_json(route_contract),
        "execution_strategy": execution_strategy,
        "ownership_feasibility": ownership_feasibility,
        "orchestration_watchdog": ORCHESTRATION_WATCHDOG,
        "preflight_lite_advice": brief["preflight_lite_advice"],
        "telemetry_policy": brief["telemetry_policy"],
        "route_policy_degraded_telemetry_waiver": brief.get("route_policy_degraded_telemetry_waiver"),
        "source_attachments": brief.get("source_attachments", []),
        **(
            {"source_attachment_promotions": brief["source_attachment_promotions"]}
            if isinstance(brief.get("source_attachment_promotions"), list) and brief["source_attachment_promotions"]
            else {}
        ),
        **({"runtime_cap": brief["runtime_cap"]} if brief.get("runtime_cap") is not None else {}),
        "repo_status": brief.get("repo_status", {}),
        "preflight_compatibility": brief.get("preflight_compatibility", {"status": "not_configured", "defects": []}),
        "preflight_input_precedence": brief.get("preflight_input_precedence", {}),
        "preflight_warnings": brief.get("preflight_warnings", []),
        **(
            {
                "goal_config_path": "goal.config.json",
                "goal_config_summary": compact_goal_config_summary(
                    goal_config, manifest_telemetry_policy=brief["telemetry_policy"]
                ),
                "goal_config_check_path": "goal-config.check.json",
                "goal_config_check_summary": compact_goal_config_check_summary(goal_config_check or {}),
                **(
                    {
                        "goal_config_sha256": sha256_file(bundle_dir / "goal.config.json"),
                        "goal_config_check_sha256": sha256_file(bundle_dir / "goal-config.check.json"),
                    }
                    if bundle_dir is not None
                    else {}
                ),
            }
            if goal_config
            else {}
        ),
        "branches": [
            _manifest_branch_entry(
                branch,
                worker_model_policy=worker_model_policy,
                worker_route_recommendations_enabled=worker_route_recommendations_enabled,
                execution_strategy=execution_strategy,
                route_contract=route_contract,
            )
            for branch in brief["branches"]
        ],
        "waves": brief["waves"],
    }


def _manifest_branch_entry(
    branch: dict,
    *,
    worker_model_policy: dict,
    worker_route_recommendations_enabled: bool,
    execution_strategy: dict,
    route_contract: dict,
) -> dict:
    return {
        "id": branch["id"],
        "objective": branch.get("objective", ""),
        "scope": branch_scope_text(branch),
        "wave": branch["wave"],
        "prompt": branch["prompt"],
        "branch_name": branch["branch_name"],
        "worktree_path": branch["worktree_path"],
        "status_path": branch["status_path"],
        "review_path": branch["review_path"],
        "pre_review_gate_path": branch["pre_review_gate_path"],
        "depends_on": branch["depends_on"],
        "owned_paths": branch["owned_paths"],
        "work_items": add_route_ladder_recommendations(
            branch["work_items"],
            worker_model_policy,
            recommendations_enabled=worker_route_recommendations_enabled,
        ),
        "execution_strategy_ref": execution_strategy.get("id"),
        "route_contract_sha256": sha256_json(route_contract),
        "max_active_worker_packets": branch["max_active_worker_packets"],
        "worker_parallelism": branch["worker_parallelism"],
        **(
            {"dependency_context_reason": branch["dependency_context_reason"]}
            if isinstance(branch.get("dependency_context_reason"), str) and branch["dependency_context_reason"].strip()
            else {}
        ),
        **({"recovers_from": branch["recovers_from"]} if isinstance(branch.get("recovers_from"), list) else {}),
        **({"supersedes": branch["supersedes"]} if isinstance(branch.get("supersedes"), list) else {}),
        **(
            {"recovery_mode": branch["recovery_mode"]}
            if isinstance(branch.get("recovery_mode"), str) and branch["recovery_mode"].strip()
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


def render_main_prompt_text(brief: dict) -> str:
    main_prompt = (Path(__file__).resolve().parents[1] / "assets" / "main.prompt.template.md").read_text(
        encoding="utf-8"
    )
    bundle_root = str(brief.get("bundle_root") or "/absolute/path/to/bundle")
    repo_root = str(brief.get("repo_root") or "/absolute/path/to/repo")
    manifest_path = f"{bundle_root}/job.manifest.json"
    audit_path = f"{bundle_root}/audit/prompt-audit.json"
    branch_status_glob = f"{bundle_root}/branches/Bxx.status.json"
    main_status_path = f"{bundle_root}/main.status.json"
    return main_prompt.format(
        title=brief.get("title", brief["job_id"]),
        job_id=brief["job_id"],
        base_ref=brief["base_ref"],
        bundle_root_shell=shlex.quote(bundle_root),
        repo_root_shell=shlex.quote(repo_root),
        manifest_path_shell=shlex.quote(manifest_path),
        audit_path_shell=shlex.quote(audit_path),
        branch_status_glob_shell=shlex.quote(branch_status_glob),
        main_status_path_shell=shlex.quote(main_status_path),
        goal=brief.get("goal", "Goal not supplied."),
        source_summary=brief.get("source_summary", "Source summary not supplied."),
        source_attachments=render_source_attachments(brief.get("source_attachments", [])),
        runtime_cap=render_runtime_cap(brief.get("runtime_cap")),
        runtime_readiness_gate=repo_runtime_gate_summary(brief.get("repo_status", {})),
        branch_waves=render_branch_waves(brief["waves"]),
        branch_dependencies=render_branch_dependencies(brief["branches"]),
        max_active_branch_agents=brief["max_active_branch_agents"],
        main_scheduler_path=CONTRACT.MAIN_SCHEDULER_PATH,
        parallelization_rationale=brief["parallelization"]["parallelization_rationale"],
        merge_policy=brief.get("merge_policy", "Report mergeability only unless explicitly authorized to merge."),
        cleanup_policy=brief["cleanup_policy"],
        artifact_policy=brief["artifact_policy"],
        required_evidence=bullets(brief.get("required_evidence", [])),
        telemetry_policy_mode=brief["telemetry_policy"]["mode"],
        final_dod=bullets(brief.get("final_dod", [])),
    )


def render_branch_prompt_text(brief: dict, branch: dict) -> str:
    branch_template = (Path(__file__).resolve().parents[1] / "assets" / "branch.prompt.template.md").read_text(
        encoding="utf-8"
    )
    scope = branch_scope_text(branch)
    bundle_root = str(brief.get("bundle_root") or "/absolute/path/to/bundle")
    manifest_path = f"{bundle_root}/job.manifest.json"
    branch_status_path = f"{bundle_root}/{branch['status_path']}"
    raw_runtime_rules_path = brief.get("runtime_rules_path")
    if not isinstance(raw_runtime_rules_path, str):
        raw_runtime_rules_path = RUNTIME_RULES_PATH
    runtime_rules_path = (
        raw_runtime_rules_path
        if Path(raw_runtime_rules_path).is_absolute()
        else f"{bundle_root}/{raw_runtime_rules_path}"
    )
    goal_config = brief.get("goal_config") if isinstance(brief.get("goal_config"), dict) else None
    worker_policy = (
        goal_config.get("model_policies", {}).get("worker_model_policy", WORKER_MODEL_POLICY)
        if goal_config
        else WORKER_MODEL_POLICY
    )
    worker_policy = normalize_worker_model_policy(worker_policy)
    default_worker_ladder = worker_policy.get("default_ladder", list(DEFAULT_WORKER_LADDER))
    allowed_worker_routes = worker_policy.get("allowed_routes", list(DEFAULT_WORKER_LADDER))
    goal_config_check = brief.get("goal_config_check") if isinstance(brief.get("goal_config_check"), dict) else None
    route_enabled = route_recommendations_enabled(goal_config_check)
    route_deferred = route_deferred_text(goal_config_check)
    execution_strategy = brief.get("execution_strategy") if isinstance(brief.get("execution_strategy"), dict) else {}
    ownership_feasibility = (
        brief.get("ownership_feasibility") if isinstance(brief.get("ownership_feasibility"), dict) else {}
    )
    route_contract = brief.get("route_contract") if isinstance(brief.get("route_contract"), dict) else {}
    default_worker_ladder_text = (
        CONTRACT.format_worker_ladder(default_worker_ladder) if route_enabled else f"deferred - {route_deferred}"
    )
    allowed_worker_routes_text = ", ".join(allowed_worker_routes) if route_enabled else f"deferred - {route_deferred}"
    work_items = add_route_ladder_recommendations(
        branch.get("work_items", []), worker_policy, recommendations_enabled=route_enabled
    )
    worker_packet_count = len(branch.get("work_items", [])) if isinstance(branch.get("work_items"), list) else 0
    effective_worker_cap = (
        min(int(branch["max_active_worker_packets"]), worker_packet_count) if worker_packet_count else 0
    )
    return branch_template.format(
        branch_id=branch["id"],
        title=branch.get("title", branch.get("objective", branch["id"])),
        base_ref=brief["base_ref"],
        manifest_path_shell=shlex.quote(manifest_path),
        branch_status_path_shell=shlex.quote(branch_status_path),
        branch_name=branch["branch_name"],
        worktree_path=branch["worktree_path"],
        wave=branch["wave"],
        depends_on=bullets(branch.get("depends_on", [])),
        dependency_context=branch.get("dependency_context_reason") or "none",
        runtime_rules_path=runtime_rules_path,
        runtime_rules_sha256=brief.get("runtime_rules_sha256", ""),
        runtime_index_path=brief.get("runtime_index_path", "runtime.index.json"),
        runtime_index_sha256=brief.get("runtime_index_sha256", ""),
        execution_strategy_id=execution_strategy.get("id", "not_recorded"),
        execution_strategy_setup=bullets(
            execution_strategy.get("setup_commands", []), fallback="- No setup command required."
        ),
        execution_strategy_env=", ".join(
            f"{key}={value}" for key, value in sorted(execution_strategy.get("validation_env", {}).items())
        )
        if isinstance(execution_strategy.get("validation_env"), dict) and execution_strategy.get("validation_env")
        else "none",
        route_contract_sha256=brief.get("route_contract_sha256", ""),
        route_contract_catalog_refresh_required=str(route_contract.get("catalog_refresh_required", "unknown")).lower(),
        ownership_feasibility_status=ownership_feasibility.get("status", "not_recorded"),
        ownership_feasibility_needs_review=ownership_feasibility.get("needs_review_count", "unknown"),
        max_active_worker_packets=branch["max_active_worker_packets"],
        effective_worker_cap=effective_worker_cap,
        worker_packet_count=worker_packet_count,
        max_worker_packets_per_branch=MAX_WORKER_PACKETS_PER_BRANCH,
        worker_scheduler_path=CONTRACT.worker_scheduler_path(branch["id"]),
        pre_review_gate_path=branch["pre_review_gate_path"],
        worker_parallelization_rationale=branch["worker_parallelism"]["parallelization_rationale"],
        branch_serial_reasons=bullets(
            brief["parallelization"].get("serial_reasons", []),
            fallback="- No branch-level serial or under-capacity reasons recorded.",
        ),
        worker_serial_reasons=bullets(
            branch["worker_parallelism"].get("serial_reasons", []),
            fallback="- No worker-level serial or under-capacity reasons recorded.",
        ),
        default_worker_ladder=default_worker_ladder_text,
        allowed_worker_routes=allowed_worker_routes_text,
        route_class_ladders=route_class_ladder_guidance(
            worker_policy, recommendations_enabled=route_enabled, deferred_text=route_deferred
        ),
        objective=branch.get("objective", "Objective not supplied."),
        scope=scope,
        source_contract=render_branch_source_contract(brief, bundle_root),
        owned_paths=bullets(branch.get("owned_paths", [])),
        work_items=format_work_items(branch["id"], work_items),
        tests=branch_additional_validators(branch),
        stop_conditions=bullets(
            branch.get("stop_conditions", []),
            fallback="- No branch-specific stop conditions beyond runtime blockers and validator failures.",
        ),
        telemetry_policy_mode=brief["telemetry_policy"]["mode"],
        dod=bullets(branch.get("dod", []), fallback=""),
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


def write_runtime_rules(brief: dict, bundle_dir: Path) -> str:
    path = bundle_dir / RUNTIME_RULES_PATH
    write(path, render_runtime_rules_text(brief))
    return sha256_file(path)


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


class GoalConfigInputs(NamedTuple):
    config: dict | None = None
    check: dict | None = None
    config_source: Path | None = None
    check_source: Path | None = None


def _goal_config_provenance(config_source: Path | None, check_source: Path | None, bundle_dir: Path) -> dict:
    provenance: dict[str, dict] = {}
    if config_source is not None:
        provenance["config"] = {
            **provenance_path(config_source, bundle_dir),
            "copied_path": "goal.config.json",
            "sanitized_runtime_copy": True,
            "sanitized_fields": ["telemetry.raw_text"],
        }
    else:
        provenance["config"] = {
            "source_path": "inline",
            "source_path_type": "inline",
            "copied_path": "goal.config.json",
            "sanitized_runtime_copy": True,
            "sanitized_fields": ["telemetry.raw_text"],
        }
    if check_source is not None:
        provenance["check"] = {
            **provenance_path(check_source, bundle_dir),
            "copied_path": "goal-config.check.json",
        }
    else:
        provenance["check"] = {
            "source_path": "inline",
            "source_path_type": "inline",
            "copied_path": "goal-config.check.json",
        }
    return provenance


def _render_preflight_report(
    brief: dict,
    manifest: dict,
    bundle_dir: Path,
    goal_config_inputs: GoalConfigInputs,
    git_ignore_warning: str,
) -> str:
    goal_config = goal_config_inputs.config
    goal_config_check = goal_config_inputs.check
    return "\n".join(
        [
            f"# Preflight Report: {brief['job_id']}",
            "",
            f"Bundle: {bundle_dir.resolve()}",
            f"Branches: {len(brief['branches'])}",
            f"Waves: {len(brief['waves'])}",
            f"Runtime rules appendix: {RUNTIME_RULES_PATH} sha256={brief.get('runtime_rules_sha256')}",
            f"Runtime index: runtime.index.json sha256={manifest.get('runtime_index_sha256')}",
            f"Execution strategy: {manifest['execution_strategy']['id']} ({manifest['execution_strategy']['reason']})",
            f"Route contract: sha256={manifest['route_contract_sha256']} catalog_refresh_required={manifest['route_contract']['catalog_refresh_required']}",
            f"Ownership feasibility: {manifest['ownership_feasibility']['status']} ({manifest['ownership_feasibility']['needs_review_count']} command(s) need review)",
            f"Max active branch agents: {brief['max_active_branch_agents']}",
            f"Runtime readiness gate: {repo_runtime_gate_summary(brief['repo_status'])}",
            f"Config precedence: {brief['preflight_input_precedence']['note']}",
            f"Parallelization: {brief['parallelization']['parallelization_rationale']}",
            f"Scheduling: rolling; runtime branch scheduler ledger path is {CONTRACT.MAIN_SCHEDULER_PATH}; saturate active branch orchestrators up to max_active_branch_agents and defer only branches with incomplete depends_on branch ids.",
            "Waves are dependency-aware scheduling/order groups; dependencies are explicit via depends_on and runtime readiness still gates launch eligibility.",
            f"Worker model policy: {CONTRACT.format_worker_ladder(manifest['worker_model_policy']['default_ladder'])}; branches may choose an ordered subsequence with a recorded reason.",
            *goal_config_report_lines(goal_config, goal_config_check, manifest),
            *([git_ignore_warning] if git_ignore_warning else []),
            "Research worker policy: use research-worker packets for outside information gathering; launcher uses Codex native web search with user config loaded and read-only sandboxing, allowing configured read-only CLI/MCP/connector/browser/search tools plus shell/network inspection commands while prohibiting file edits and state-changing actions.",
            f"Artifact policy: {brief['artifact_policy']}",
            f"Cleanup policy: {brief['cleanup_policy']}",
            "",
            "Bootstrap: generated bootloaders require runtime skill availability checks before prompt audit.",
            "Lite: optional advisory packets may route context but never satisfy audit, review, mergeability, or DoD evidence; preflight Lite provenance lives in job.manifest.json preflight_lite_advice.",
            "Launch gate: run bundle lint and readiness; launch only when readiness reports `launch_allowed=true`.",
            "",
        ]
    )


def create_bundle(
    brief: dict,
    repo_root: Path,
    out_dir: Path | None,
    *,
    goal_config_inputs: GoalConfigInputs | None = None,
) -> Path:
    goal_config_inputs = goal_config_inputs or GoalConfigInputs()
    goal_config = goal_config_inputs.config
    goal_config_check = goal_config_inputs.check
    goal_config_source = goal_config_inputs.config_source
    goal_config_check_source = goal_config_inputs.check_source
    if goal_config is not None and goal_config_check is None:
        raise SystemExit("--goal-config requires --goal-config-check with status=pass")
    original_brief = dict(brief)
    runtime_goal_config = None
    route_policy_pruning = {}
    if goal_config is not None:
        runtime_goal_config, route_policy_pruning = sanitize_and_prune_runtime_goal_config(
            goal_config, goal_config_check
        )
    compatibility = preflight_compatibility_summary(runtime_goal_config or goal_config, goal_config_check)
    if compatibility.get("status") == "failed":
        defects = "; ".join(str(item) for item in compatibility.get("defects", []))
        raise SystemExit(f"goal_config is not preflight-compatible: {defects}")
    brief = apply_goal_config_to_brief(brief, runtime_goal_config)
    brief = normalize_brief(
        brief,
        default_base_ref=current_git_branch(repo_root),
        validate_base_ref=True,
        repo_root=repo_root,
    )
    brief = promote_repeated_context_attachments(brief, repo_root=repo_root)
    branch_paths = []
    for branch in brief.get("branches", []):
        if not isinstance(branch, dict):
            continue
        path = branch.get("worktree_path")
        if isinstance(path, str):
            branch_paths.append(path)
    brief["repo_status"] = git_repo_status(repo_root, base_ref=brief["base_ref"], branch_paths=branch_paths)
    brief["preflight_compatibility"] = compatibility
    brief["preflight_input_precedence"] = preflight_input_precedence(original_brief, brief, goal_config)
    if runtime_goal_config is not None:
        brief["goal_config"] = runtime_goal_config
        brief["goal_config_check"] = goal_config_check or {}
        brief["route_policy_pruning"] = route_policy_pruning

    bundle_dir = out_dir or repo_root / "plans" / "orchestration" / brief["job_id"]
    ensure_bundle_dirs(bundle_dir)
    brief["bundle_root"] = bundle_dir.resolve().as_posix()
    brief["repo_root"] = repo_root.resolve().as_posix()
    brief["execution_strategy"] = infer_execution_strategy(repo_root, brief["branches"])
    preflight_warnings = []
    git_ignore_warning_record = bundle_git_ignore_warning_record(repo_root, bundle_dir)
    if git_ignore_warning_record:
        preflight_warnings.append(git_ignore_warning_record)
    brief["preflight_warnings"] = preflight_warnings

    if runtime_goal_config is not None:
        write(bundle_dir / "goal.config.json", json.dumps(runtime_goal_config, indent=2, sort_keys=True) + "\n")
    if goal_config_check_source is not None:
        shutil.copyfile(goal_config_check_source, bundle_dir / "goal-config.check.json")
    elif goal_config_check is not None:
        write(bundle_dir / "goal-config.check.json", json.dumps(goal_config_check, indent=2, sort_keys=True) + "\n")

    brief["runtime_rules_path"] = RUNTIME_RULES_PATH
    brief["runtime_rules_sha256"] = write_runtime_rules(brief, bundle_dir)

    manifest = manifest_from_normalized_brief(brief, bundle_dir)
    if runtime_goal_config is not None:
        manifest["goal_config_provenance"] = _goal_config_provenance(
            goal_config_source, goal_config_check_source, bundle_dir
        )
    manifest["runtime_index_path"] = "runtime.index.json"
    runtime_index = build_runtime_index(manifest)
    write(bundle_dir / "runtime.index.json", canonical_json_text(runtime_index))
    manifest["runtime_index_sha256"] = sha256_file(bundle_dir / "runtime.index.json")
    brief["runtime_index_path"] = manifest["runtime_index_path"]
    brief["runtime_index_sha256"] = manifest["runtime_index_sha256"]
    brief["route_contract"] = manifest["route_contract"]
    brief["route_contract_sha256"] = manifest["route_contract_sha256"]
    brief["ownership_feasibility"] = manifest["ownership_feasibility"]
    write(bundle_dir / "job.manifest.json", canonical_json_text(manifest))

    write_bundle_prompts(brief, bundle_dir)

    bootloader = render_bootloader(bundle_dir.resolve(), repo_root.resolve())
    write(bundle_dir / "goal-bootloader.md", bootloader)
    git_ignore_warning = git_ignore_warning_record.get("message", "") if git_ignore_warning_record else ""
    report = _render_preflight_report(brief, manifest, bundle_dir, goal_config_inputs, git_ignore_warning)
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
    parser.add_argument("--json", action="store_true", help="Print a machine-readable command result JSON.")
    parser.add_argument("--output", type=Path, help="Write the command result JSON to this path.")
    parser.add_argument(
        "--goal-config", help="Absolute path to checked goal.config.json to embed and consume in the bundle."
    )
    parser.add_argument(
        "--goal-config-check", help="Absolute path to a passing check_goal_config.py report for --goal-config."
    )
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
    goal_config_path = (
        resolve_absolute_path(args.goal_config, "--goal-config", must_exist=True) if args.goal_config else None
    )
    goal_config_check_path = (
        resolve_absolute_path(args.goal_config_check, "--goal-config-check", must_exist=True)
        if args.goal_config_check
        else None
    )
    if goal_config_check_path is not None and goal_config_path is None:
        raise SystemExit("--goal-config-check requires --goal-config")
    goal_config = load_goal_config(goal_config_path)
    goal_config_check = load_goal_config_check(goal_config_check_path)
    bundle_dir = create_bundle(
        load_json(brief_path),
        repo_root,
        out_dir,
        goal_config_inputs=GoalConfigInputs(
            config=goal_config,
            check=goal_config_check,
            config_source=goal_config_path,
            check_source=goal_config_check_path,
        ),
    )
    result = {
        "status": "pass",
        "bundle_dir": bundle_dir.resolve().as_posix(),
        "manifest_path": (bundle_dir / "job.manifest.json").resolve().as_posix(),
        "main_prompt_path": (bundle_dir / "main.prompt.md").resolve().as_posix(),
        "bootloader_path": (bundle_dir / "goal-bootloader.md").resolve().as_posix(),
        "repo_root": repo_root.resolve().as_posix(),
        "goal_config_path": goal_config_path.resolve().as_posix() if goal_config_path is not None else None,
        "goal_config_check_path": goal_config_check_path.resolve().as_posix()
        if goal_config_check_path is not None
        else None,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
