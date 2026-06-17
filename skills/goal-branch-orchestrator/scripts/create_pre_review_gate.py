#!/usr/bin/env python3
"""Create a deterministic schema v2 pre-review gate artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
from pathlib import Path


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load required module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = SCRIPT_DIR.parents[1]
SHARED_SCRIPTS = SKILLS_ROOT / "_goal_shared" / "scripts"
PREFLIGHT_SCRIPTS = SKILLS_ROOT / "goal-preflight" / "scripts"

CONTRACT = _load_module("goal_shared_orchestration_contract", SHARED_SCRIPTS / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")
STATUS_VALIDATION = _load_module("goal_shared_status_validation", SHARED_SCRIPTS / "status_validation.py")
BRANCH_VALIDATOR = _load_module("goal_branch_validate_branch_status", SCRIPT_DIR / "validate_branch_status.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
is_repo_relative_path = PATH_RULES.is_repo_relative_path
require_safe_label = PATH_RULES.require_safe_packet_label


ATTEMPT_DIR_RE = re.compile(r"attempt-\d{3,}")


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_command(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


_GIT_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def validate_base_ref(base_ref: str) -> str:
    """Reject manifest base_ref values that are not a plausible git ref.

    Closes the manifest-base_ref command-injection vector: only safe ref
    characters are allowed (no shell metacharacters, whitespace, or leading
    dashes that could be parsed as git options).
    """
    candidate = base_ref.strip()
    if not candidate:
        raise SystemExit("manifest base_ref must be a non-empty git ref")
    if candidate.startswith("-"):
        raise SystemExit(f"manifest base_ref must not start with '-': {base_ref!r}")
    if ".." in candidate or candidate.endswith("/") or candidate.endswith(".lock"):
        raise SystemExit(f"manifest base_ref is not a plausible git ref: {base_ref!r}")
    if not _GIT_REF_RE.fullmatch(candidate):
        raise SystemExit(f"manifest base_ref contains characters that are not valid in a git ref: {base_ref!r}")
    return candidate


def run_test_command(command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a declared test command as list-form argv (shell=False).

    The command string is tokenized with shlex and executed without a shell,
    closing the shell-injection vector while preserving simple argv commands.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return subprocess.CompletedProcess(
            args=command, returncode=2, stdout=f"invalid test command {command!r}: {exc}"
        )
    if not argv:
        return subprocess.CompletedProcess(args=command, returncode=2, stdout=f"empty test command: {command!r}")
    return run_command(argv, cwd=cwd)


def status_validation_defects(output: str) -> list[str]:
    try:
        data = json.loads(output)
    except Exception:
        return []
    defects = data.get("defects") if isinstance(data, dict) else None
    if not isinstance(defects, list):
        return []
    return [item for item in defects if isinstance(item, str)]


def expected_pre_review_bootstrap_defect(message: str) -> bool:
    allowed_prefixes = (
        "$.review_status.pre_review_gate",
        "$.review_status.semantic_input_hashes",
        "$.review_status.reuse_policy.source_review_path.semantic_input_hashes",
    )
    return message.startswith(allowed_prefixes)


def allowed_status_bootstrap_defects(output: str) -> list[str]:
    defects = status_validation_defects(output)
    if defects and all(expected_pre_review_bootstrap_defect(item) for item in defects):
        return defects
    return []


def is_retry_attempt_artifact(path: str) -> bool:
    if not isinstance(path, str):
        return False
    parts = Path(path).parts
    for index, part in enumerate(parts[:-2]):
        if part == "attempts" and index + 1 < len(parts) and ATTEMPT_DIR_RE.fullmatch(parts[index + 1]):
            return True
    return False


def pre_review_input_artifact_paths(
    bundle_dir: Path,
    branch: dict,
    branch_id: str,
) -> tuple[list[str], list[str]]:
    rel_paths = BRANCH_VALIDATOR.required_pre_review_input_paths(branch, branch_id, bundle_dir=bundle_dir)
    diagnostic_paths = (
        BRANCH_VALIDATOR.diagnostic_pre_review_input_paths(branch, branch_id, bundle_dir=bundle_dir)
        if hasattr(BRANCH_VALIDATOR, "diagnostic_pre_review_input_paths")
        else []
    )
    current_artifacts: list[str] = []
    diagnostic_artifacts: list[str] = []
    for rel_path in [*rel_paths, *diagnostic_paths]:
        if not isinstance(rel_path, str) or not rel_path.strip():
            continue
        if is_retry_attempt_artifact(rel_path):
            diagnostic_artifacts.append(rel_path)
        elif rel_path not in current_artifacts:
            current_artifacts.append(rel_path)
    return current_artifacts, sorted(dict.fromkeys(diagnostic_artifacts))


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be an array")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest must contain exactly one branch {branch_id!r}")
    return matches[0]


def safe_status_path(bundle_dir: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip() or not is_repo_relative_path(value):
        raise SystemExit(f"{field} must be a safe bundle-relative path")
    return (bundle_dir / value).resolve()


def path_is_owned(path: str, owned_paths: list[str]) -> bool:
    return any(path == owned or path.startswith(f"{owned.rstrip('/')}/") for owned in owned_paths)


def ownership_check(branch: dict, branch_status: dict) -> dict:
    owned_paths = [item for item in branch.get("owned_paths", []) if isinstance(item, str)]
    changed_files = [item for item in branch_status.get("changed_files", []) if isinstance(item, str)]
    defects = []
    for changed in changed_files:
        if not is_repo_relative_path(changed, reject_porcelain=True):
            defects.append(f"changed file is not a safe repo-relative path: {changed!r}")
            continue
        if not path_is_owned(changed, owned_paths):
            defects.append(f"changed file is outside branch owned_paths: {changed}")
    result = {"status": "pass" if not defects else "failed", "changed_files": changed_files, "owned_paths": owned_paths}
    if defects:
        result["defects"] = defects
    return result


def required_input_hashes(
    bundle_dir: Path,
    rel_paths: list[str],
    *,
    strict: bool,
) -> tuple[dict[str, str], list[str], list[str]]:
    hashes: dict[str, str] = {}
    defects: list[str] = []
    missing: list[str] = []
    for rel_path in rel_paths:
        if not is_repo_relative_path(rel_path):
            defects.append(f"unsafe required input path: {rel_path!r}")
            continue
        path = bundle_dir / rel_path
        if not path.exists():
            if strict:
                defects.append(f"required input does not exist: {rel_path}")
            else:
                missing.append(rel_path)
            continue
        hashes[rel_path] = sha256_file(path)
    return hashes, defects, missing


def is_final_base_reuse_source(review_path: Path) -> bool:
    review_path_text = review_path.as_posix()
    return "final-base" in review_path_text or "final_base" in review_path_text


def expected_worker_packet_ids(branch: dict, branch_id: str) -> list[str]:
    result = []
    work_items = branch.get("work_items")
    if not isinstance(work_items, list):
        return result
    for item in work_items:
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if isinstance(packet_id, str) and packet_id.strip():
            result.append(packet_id)
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id.strip():
            result.append(f"{branch_id}-{item_id}")
    return result


def manifest_item_packet_id(branch_id: str, item: dict) -> str:
    packet_id = item.get("packet_id")
    if isinstance(packet_id, str) and packet_id.strip():
        return packet_id
    item_id = item.get("id")
    return f"{branch_id}-{item_id}" if isinstance(item_id, str) and item_id.strip() else ""


def packet_terminal_defects(bundle_dir: Path, branch: dict, branch_id: str, packet_id: str, status: dict) -> list[str]:
    work_items = branch.get("work_items")
    item = None
    if isinstance(work_items, list):
        item = next(
            (
                candidate
                for candidate in work_items
                if isinstance(candidate, dict) and manifest_item_packet_id(branch_id, candidate) == packet_id
            ),
            None,
        )
    packet_root = BRANCH_VALIDATOR.packet_artifact_root(
        item.get("worker_type", "worker") if isinstance(item, dict) else "worker",
        packet_id,
    )
    launcher_path = bundle_dir / packet_root / "launcher-state.json"
    summary_path = bundle_dir / packet_root / "packet.summary.json"
    defects: list[str] = []
    if not launcher_path.exists():
        defects.append(f"worker packet {packet_id} missing launcher-state.json before reviewer launch")
    else:
        launcher = read_json(launcher_path)
        if status.get("status") == "pass" and launcher.get("terminal_state") != "pass":
            defects.append(
                f"worker packet {packet_id} launcher-state terminal_state must be 'pass' before reviewer launch, "
                f"got {launcher.get('terminal_state')!r}"
            )
    if not summary_path.exists():
        defects.append(f"worker packet {packet_id} missing packet.summary.json before reviewer launch")
    else:
        summary = read_json(summary_path)
        if status.get("status") == "pass":
            if summary.get("terminal_state") != "pass":
                defects.append(
                    f"worker packet {packet_id} packet.summary terminal_state must be 'pass' before reviewer launch, "
                    f"got {summary.get('terminal_state')!r}"
                )
            if summary.get("output_status") != "pass":
                defects.append(
                    f"worker packet {packet_id} packet.summary output_status must be 'pass' before reviewer launch, "
                    f"got {summary.get('output_status')!r}"
                )
    return defects


def worker_pass_defects(bundle_dir: Path, branch: dict, branch_status: dict, branch_id: str) -> list[str]:
    defects = []
    expected_ids = expected_worker_packet_ids(branch, branch_id)
    statuses = branch_status.get("worker_statuses")
    if not isinstance(statuses, list):
        return ["branch status worker_statuses must be present before reviewer launch"]
    by_packet = {
        item.get("packet_id"): item
        for item in statuses
        if isinstance(item, dict) and isinstance(item.get("packet_id"), str)
    }
    for packet_id in expected_ids:
        status = by_packet.get(packet_id)
        if status is None:
            defects.append(f"worker packet {packet_id} is missing from branch status before reviewer launch")
            continue
        if status.get("status") != "pass":
            defects.append(
                f"worker packet {packet_id} must be pass before reviewer launch, got {status.get('status')!r}"
            )
        blockers = status.get("blockers")
        if isinstance(blockers, list) and blockers:
            defects.append(f"worker packet {packet_id} still has blockers before reviewer launch")
        defects.extend(packet_terminal_defects(bundle_dir, branch, branch_id, packet_id, status))
    extra = sorted(packet_id for packet_id in by_packet if packet_id not in set(expected_ids))
    if extra:
        defects.append("branch status contains worker packets not declared by manifest: " + ", ".join(extra))
    if [item.get("packet_id") for item in statuses if isinstance(item, dict)] != expected_ids:
        defects.append("branch status worker evidence must preserve manifest work item order before reviewer launch")
    parallelism = branch_status.get("worker_parallelism")
    if not isinstance(parallelism, dict):
        defects.append("branch status worker_parallelism must be present before reviewer launch")
    else:
        for field in ["active_ids", "blocked_ids", "deferred_ids"]:
            values = parallelism.get(field)
            if isinstance(values, list) and values:
                defects.append(
                    f"worker scheduler reports {field} before reviewer launch: "
                    + ", ".join(str(item) for item in values)
                )
        finished_ids = [item for item in parallelism.get("finished_ids", []) if isinstance(item, str)]
        missing_finished = [packet_id for packet_id in expected_ids if packet_id not in finished_ids]
        if missing_finished:
            defects.append(
                "worker scheduler is missing finish evidence before reviewer launch: " + ", ".join(missing_finished)
            )
    if branch_status.get("status") in {"blocked", "failed"}:
        defects.append(
            f"branch status is {branch_status.get('status')!r}; reviewer launch requires integrated worker evidence"
        )
    return defects


def git_lines(command: list[str], *, cwd: Path, defects: list[str], label: str) -> list[str]:
    result = run_command(command, cwd=cwd)
    if result.returncode != 0:
        defects.append(f"{label} failed ({result.returncode}): {result.stdout.strip()}")
        return []
    paths = []
    for line in result.stdout.splitlines():
        path = line.strip()
        if not path:
            continue
        if not is_repo_relative_path(path, reject_porcelain=True):
            defects.append(f"{label} returned unsafe path: {path!r}")
            continue
        if path not in paths:
            paths.append(path)
    return paths


def is_runtime_cache_path(path: str) -> bool:
    parts = [part for part in Path(path).parts if part]
    if any(part in {"__pycache__", ".pytest_cache", ".ruff_cache"} for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if path.endswith((".pyc", ".pyo", ".egg-info")):
        return True
    return path == ".runtime-cache" or path.startswith(".runtime-cache/")


def declared_worker_changed_files(branch_status: dict) -> list[str]:
    changed_files: list[str] = []
    status_changed = branch_status.get("changed_files")
    if isinstance(status_changed, list):
        for rel_path in status_changed:
            if isinstance(rel_path, str) and rel_path.strip() and rel_path not in changed_files:
                changed_files.append(rel_path)
    statuses = branch_status.get("worker_statuses")
    if isinstance(statuses, list):
        for status in statuses:
            if not isinstance(status, dict):
                continue
            worker_changed = status.get("changed_files")
            if not isinstance(worker_changed, list):
                continue
            for rel_path in worker_changed:
                if isinstance(rel_path, str) and rel_path.strip() and rel_path not in changed_files:
                    changed_files.append(rel_path)
    return [
        rel_path
        for rel_path in changed_files
        if is_repo_relative_path(rel_path, reject_porcelain=True) and not is_runtime_cache_path(rel_path)
    ]


def dirty_paths(worktree: Path) -> tuple[dict[str, list[str]], list[str]]:
    defects: list[str] = []
    commands = {
        "unstaged": ["git", "diff", "--name-only", "HEAD"],
        "staged": ["git", "diff", "--cached", "--name-only", "HEAD"],
        "untracked": ["git", "ls-files", "--others", "--exclude-standard"],
    }
    paths: dict[str, list[str]] = {}
    for key, command in commands.items():
        values = git_lines(command, cwd=worktree, defects=defects, label=" ".join(command))
        paths[key] = [
            value
            for value in values
            if is_repo_relative_path(value, reject_porcelain=True) and not is_runtime_cache_path(value)
        ]
    return paths, defects


def worktree_integration_check(worktree: Path, branch_status: dict) -> tuple[dict, list[str]]:
    dirty_by_kind, defects = dirty_paths(worktree)
    declared_changed = declared_worker_changed_files(branch_status)
    dirty_worker_paths = sorted(
        {
            path
            for path in [
                *dirty_by_kind.get("unstaged", []),
                *dirty_by_kind.get("staged", []),
                *dirty_by_kind.get("untracked", []),
            ]
            if path in declared_changed
        }
    )
    check = {
        "status": "pass" if not defects and not dirty_worker_paths else "failed",
        "commands": [
            "git diff --name-only HEAD",
            "git diff --cached --name-only HEAD",
            "git ls-files --others --exclude-standard",
        ],
        "declared_worker_changed_files": declared_changed,
        "unstaged_paths": dirty_by_kind.get("unstaged", []),
        "staged_paths": dirty_by_kind.get("staged", []),
        "untracked_paths": dirty_by_kind.get("untracked", []),
        "dirty_worker_changed_files": dirty_worker_paths,
        "runtime_cache_paths_ignored": True,
    }
    if dirty_worker_paths:
        defects.append(
            "worker changed files are still uncommitted before reviewer launch: "
            + ", ".join(dirty_worker_paths)
            + "; commit or otherwise integrate accepted worker edits into branch history before pre-review"
        )
    if defects:
        check["defects"] = list(defects)
    return check, defects


def untracked_whitespace_defects(worktree: Path) -> list[str]:
    result = run_command(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if result.returncode != 0:
        return [f"untracked file scan failed:\n{result.stdout.strip()}"]
    defects: list[str] = []
    for rel_path in result.stdout.splitlines():
        rel_path = rel_path.strip()
        if (
            not rel_path
            or not is_repo_relative_path(rel_path, reject_porcelain=True)
            or is_runtime_cache_path(rel_path)
        ):
            continue
        target = (worktree / rel_path).resolve()
        try:
            target.relative_to(worktree.resolve())
        except ValueError:
            continue
        if not target.is_file():
            continue
        data = target.read_bytes()
        if b"\0" in data:
            continue
        for line_no, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if line.endswith((" ", "\t")):
                defects.append(f"{rel_path}:{line_no}: trailing whitespace in untracked file")
    return defects


def file_state(worktree: Path, rel_path: str) -> str:
    target = (worktree / rel_path).resolve()
    try:
        target.relative_to(worktree.resolve())
    except ValueError:
        return "outside-worktree"
    if target.is_symlink():
        return "symlink:" + sha256_text(os.readlink(target)).removeprefix("sha256:")
    if not target.exists():
        return "missing"
    if not target.is_file():
        return "non-file"
    return sha256_file(target)


def freshness_paths(paths: list[str]) -> list[str]:
    result: list[str] = []
    for path in paths:
        if not is_repo_relative_path(path, reject_porcelain=True) or is_runtime_cache_path(path):
            continue
        if path not in result:
            result.append(path)
    return result


def worktree_snapshot(worktree: Path, base_ref: str, branch_id: str, branch_status: dict) -> tuple[dict, list[str]]:
    defects = []
    head_result = run_command(["git", "rev-parse", "HEAD"], cwd=worktree)
    merge_base_result = run_command(["git", "merge-base", base_ref, "HEAD"], cwd=worktree)
    name_status_result = run_command(
        ["git", "diff", "--name-status", "--find-renames", f"{base_ref}...HEAD"], cwd=worktree
    )
    if head_result.returncode != 0:
        defects.append("could not capture worktree HEAD:\n" + head_result.stdout.strip())
    if merge_base_result.returncode != 0:
        defects.append("could not capture worktree merge-base:\n" + merge_base_result.stdout.strip())
    if name_status_result.returncode != 0:
        defects.append("could not capture worktree name-status diff:\n" + name_status_result.stdout.strip())
    base_paths = git_lines(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=worktree,
        defects=defects,
        label=f"git diff --name-only {base_ref}...HEAD",
    )
    unstaged_paths = git_lines(
        ["git", "diff", "--name-only", "HEAD"], cwd=worktree, defects=defects, label="git diff --name-only HEAD"
    )
    staged_paths = git_lines(
        ["git", "diff", "--cached", "--name-only", "HEAD"],
        cwd=worktree,
        defects=defects,
        label="git diff --cached --name-only HEAD",
    )
    untracked_paths = git_lines(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        defects=defects,
        label="git ls-files --others --exclude-standard",
    )
    status_paths = (
        [path for path in branch_status.get("changed_files", []) if isinstance(path, str) and path.strip()]
        if isinstance(branch_status.get("changed_files"), list)
        else []
    )
    base_paths = freshness_paths(base_paths)
    current_paths = freshness_paths([*status_paths, *base_paths, *unstaged_paths, *staged_paths, *untracked_paths])
    snapshot = {
        "schema_version": 1,
        "branch_id": branch_id,
        "worktree": worktree.as_posix(),
        "base_ref": base_ref,
        "worktree_head": head_result.stdout.strip().splitlines()[0]
        if head_result.returncode == 0 and head_result.stdout.strip()
        else "",
        "merge_base": merge_base_result.stdout.strip().splitlines()[0]
        if merge_base_result.returncode == 0 and merge_base_result.stdout.strip()
        else "",
        "diff_name_status_sha256": sha256_text(name_status_result.stdout if name_status_result.returncode == 0 else ""),
        "base_range_changed_files": base_paths,
        "current_changed_files": current_paths,
        "current_file_hashes": {path: file_state(worktree, path) for path in current_paths},
        "commands_run": [
            "git rev-parse HEAD",
            f"git merge-base {base_ref} HEAD",
            f"git diff --name-status --find-renames {base_ref}...HEAD",
            f"git diff --name-only {base_ref}...HEAD",
            "git diff --name-only HEAD",
            "git diff --cached --name-only HEAD",
            "git ls-files --others --exclude-standard",
        ],
    }
    return snapshot, defects


def default_reuse_policy() -> dict:
    return {
        "mode": "new",
        "accepted": False,
        "semantic_hashes_match": False,
        "source_review_path": None,
        "source_telemetry_path": None,
    }


def review_route_policy(branch: dict, branch_id: str, manifest: dict) -> dict:
    work_items = []
    for item in branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []:
        if not isinstance(item, dict):
            continue
        worker_type = item.get("worker_type", "worker")
        if worker_type == "research":
            worker_type = "research-worker"
        work_items.append(
            {
                "id": item.get("id"),
                "packet_id": item.get("packet_id"),
                "worker_type": worker_type,
                "route_class": "research-worker" if worker_type == "research-worker" else item.get("route_class"),
                "route_class_reason": item.get("route_class_reason"),
            }
        )
    return {
        "schema_version": 1,
        "branch_id": branch_id,
        "review_model_policy": manifest.get("review_model_policy"),
        "worker_model_policy": manifest.get("worker_model_policy"),
        "branch_review_tier": branch.get("review_tier"),
        "branch_review_tier_reason": branch.get("review_tier_reason"),
        "work_item_route_policy": work_items,
    }


def pre_review_evidence(
    branch_id: str,
    tests: dict,
    dod_items: list[str],
    diff_command: str,
    ownership: dict,
    *,
    declared_dod_items: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "branch_id": branch_id,
        "tests": tests,
        "dod_evidence": {
            "status": "pass" if dod_items else "failed",
            "items": dod_items,
            "declared_items": declared_dod_items or [],
            "declared_items_are_evidence": False,
        },
        "diff_check": {
            "command": diff_command,
        },
        "ownership": ownership,
    }


def display_path(bundle_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(bundle_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def reuse_policy(
    args: argparse.Namespace,
    bundle_dir: Path,
    semantic_hashes: dict[str, str],
    semantic_input_paths: list[str],
    *,
    route_policy_path: str,
) -> tuple[dict, dict, list[str]]:
    base_eligibility = {
        "eligible": False,
        "reason": "",
        "required_hashes": sorted(semantic_hashes),
        "route_policy_path": route_policy_path,
        "source_review_path": None,
        "source_telemetry_path": None,
    }
    if not args.reuse_source_review and not args.reuse_source_telemetry:
        eligibility = dict(base_eligibility)
        eligibility["reason"] = "no source review and telemetry were supplied"
        return default_reuse_policy(), eligibility, []
    if not args.reuse_source_review or not args.reuse_source_telemetry:
        eligibility = dict(base_eligibility)
        eligibility["reason"] = "accepted reuse requires both --reuse-source-review and --reuse-source-telemetry"
        return default_reuse_policy(), eligibility, [eligibility["reason"]]
    review_path = resolve_absolute_path(args.reuse_source_review, "--reuse-source-review", must_exist=True)
    telemetry_path = resolve_absolute_path(args.reuse_source_telemetry, "--reuse-source-telemetry", must_exist=True)
    source_review_display = display_path(bundle_dir, review_path)
    source_telemetry_display = display_path(bundle_dir, telemetry_path)
    defects = []
    review = read_json(review_path)
    source_hashes = {
        key: value
        for key, value in (
            review.get("semantic_input_hashes", {}) if isinstance(review.get("semantic_input_hashes"), dict) else {}
        ).items()
        if isinstance(key, str) and key in semantic_input_paths and isinstance(value, str)
    }
    if source_hashes != semantic_hashes:
        missing_paths = [
            rel_path
            for rel_path in sorted(semantic_input_paths)
            if source_hashes.get(rel_path) != semantic_hashes.get(rel_path)
        ]
        defects.append(
            "source review semantic_input_hashes do not match current canonical pre-review inputs: "
            + ", ".join(missing_paths)
        )
    telemetry_defects: list[str] = []
    STATUS_VALIDATION.validate_telemetry_artifact(
        telemetry_defects,
        telemetry_path,
        "reuse_source_telemetry",
        role="reviewer",
        # Use the validator's reviewer allowlist (bridge-led REVIEW_MODEL_ROUTES + gpt) so the
        # gate does not refuse a legitimately reused ds-pro-max / ds-flash-max reviewer telemetry.
        allowed_aliases=BRANCH_VALIDATOR.REVIEWER_ALLOWED_ALIASES,
        require_called=True,
    )
    defects.extend(telemetry_defects)
    accepted = not defects
    final_base_reuse = is_final_base_reuse_source(review_path)
    no_op_reason = (
        "deterministic final-base no-op reuse path accepted"
        if final_base_reuse
        else "deterministic reviewer reuse/no-op path accepted"
    )
    acceptance_reason = (
        no_op_reason
        if final_base_reuse
        else "source review and telemetry match current canonical pre-review evidence; deterministic reuse accepted"
    )
    if not accepted:
        acceptance_reason = "; ".join(defects)
    policy = {
        "mode": "reuse" if accepted else "new",
        "accepted": accepted,
        "semantic_hashes_match": accepted,
        "no_op_reuse_reason": no_op_reason if accepted else "",
        "source_review_path": source_review_display,
        "source_telemetry_path": source_telemetry_display,
    }
    eligibility = dict(base_eligibility)
    eligibility.update(
        {
            "eligible": accepted,
            "reason": acceptance_reason,
            "source_review_path": source_review_display,
            "source_telemetry_path": source_telemetry_display,
        }
    )
    return policy, eligibility, defects


def manifest_test_commands(branch: dict) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    tests = branch.get("tests")
    if isinstance(tests, list):
        for command in tests:
            if isinstance(command, str) and command.strip() and command.strip() not in seen:
                commands.append(command.strip())
                seen.add(command.strip())
    return commands


SEMANTIC_PROBE_KEYWORDS = (
    "compatible",
    "compatibility",
    "contract",
    "verifier",
    "api",
    "integration",
    "round-trip",
    "round trip",
)
SEMANTIC_TARGET_RE = re.compile(
    r"\b(?:compatible|compatibility|contract|verifier|api|integration)\b"
    r"(?:\s+(?:with|for|against|to)\s+)"
    r"([A-Za-z0-9_.:/+-]+)",
    re.I,
)


def semantic_keyword_present(text: str, keyword: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])", text) is not None


def branch_semantic_probe_requirements(branch: dict) -> list[str]:
    text_parts: list[str] = []
    for key in ("objective", "scope"):
        value = branch.get(key)
        if isinstance(value, str):
            text_parts.append(value)
    dod = branch.get("dod")
    if isinstance(dod, str):
        text_parts.append(dod)
    elif isinstance(dod, list):
        text_parts.extend(item for item in dod if isinstance(item, str))
    combined = "\n".join(text_parts).lower()
    requirements = [keyword for keyword in SEMANTIC_PROBE_KEYWORDS if semantic_keyword_present(combined, keyword)]
    return sorted(dict.fromkeys(requirements))


def branch_semantic_probe_targets(branch: dict) -> list[str]:
    text_parts: list[str] = []
    for key in ("objective", "scope"):
        value = branch.get(key)
        if isinstance(value, str):
            text_parts.append(value)
    dod = branch.get("dod")
    if isinstance(dod, str):
        text_parts.append(dod)
    elif isinstance(dod, list):
        text_parts.extend(item for item in dod if isinstance(item, str))
    targets: list[str] = []
    for text in text_parts:
        for match in SEMANTIC_TARGET_RE.finditer(text):
            target = match.group(1).strip("`'\".,:;()[]{}").lower()
            if target and target not in targets:
                targets.append(target)
    return targets


PYTHON_COMMAND_RE = re.compile(r"^python\d*(?:\.\d+)?$")
_FULL_SUITE_PYTEST_OPTIONS_REQUIRING_VALUE = {
    "-n",
    "--numprocesses",
    "--dist",
    "--maxfail",
    "--confcutdir",
    "--rootdir",
    "--junitxml",
    "--junitprefix",
    "--durations",
    "--color",
    "--randomly-seed",
}
_PYTEST_OPTIONS_NOT_RUNNING_TESTS = {
    "-h",
    "--help",
    "--version",
    "--fixtures",
    "--fixtures-per-test",
    "--markers",
    "--collect-only",
    "--co",
    "--trace-config",
}


def _pytest_full_suite_command(command: str) -> bool:
    if "||" in command or ";" in command:
        return False
    segments = re.split(r"\s*&&\s*", command.strip())
    return any(_pytest_full_suite_segment(segment.strip()) for segment in segments if segment.strip())


def _pytest_full_suite_segment(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    if not tokens:
        return False
    index = 0
    while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
        index += 1
    if index >= len(tokens):
        return False
    if tokens[index] == "env":
        index += 1
        while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
            index += 1
    if index >= len(tokens):
        return False

    if tokens[index] == "pytest" or tokens[index] == "pytest.exe":
        return _segment_tail_has_no_positional_args(tokens[index + 1 :])
    if (
        PYTHON_COMMAND_RE.fullmatch(tokens[index])
        and index + 2 < len(tokens)
        and tokens[index + 1] == "-m"
        and tokens[index + 2] in {"pytest", "pytest.exe"}
    ):
        return _segment_tail_has_no_positional_args(tokens[index + 3 :])
    return False


def _segment_tail_has_no_positional_args(tokens: list[str]) -> bool:
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            return False
        if token in _PYTEST_OPTIONS_NOT_RUNNING_TESTS:
            return False
        if not token.startswith("-"):
            return False
        if token in _FULL_SUITE_PYTEST_OPTIONS_REQUIRING_VALUE:
            skip_next = True
            continue
        if token.startswith("--") and "=" in token:
            continue
    return True


def _semantic_target_matches(command_text: str, target: str) -> bool:
    if target == "all":
        return _pytest_full_suite_command(command_text)
    return target in command_text


def semantic_probe_check(branch: dict, tests: dict) -> tuple[dict, list[str]]:
    requirements = branch_semantic_probe_requirements(branch)
    targets = branch_semantic_probe_targets(branch)
    commands = tests.get("commands") if isinstance(tests.get("commands"), list) else []
    command_values = [item for item in commands if isinstance(item, str) and item.strip()]
    missing_targets: list[str] = []
    if requirements and targets and command_values:
        lowered_commands = [command.lower() for command in command_values]
        missing_targets = [
            target
            for target in targets
            if not any(_semantic_target_matches(command, target) for command in lowered_commands)
        ]
    status = "pass" if not requirements or (command_values and not missing_targets) else "failed"
    check = {
        "status": status,
        "requirements": requirements,
        "targets": targets,
        "commands": command_values,
        "probe_kind": "cross_contract" if requirements else "not_applicable",
        "source": "branch objective/scope/DoD keyword scan",
    }
    defects = []
    if requirements and not command_values:
        defects.append(
            "branch declares checkable compatibility/contract/API/verifier/integration behavior but pre-review has no command-backed semantic probe"
        )
    for target in missing_targets:
        defects.append(f"cross-contract semantic probe must mention declared target: {target}")
    return check, defects


def test_check(args: argparse.Namespace, worktree: Path, branch_status: dict, branch: dict) -> tuple[dict, list[str]]:
    if args.skip_tests:
        if not str(args.test_skip_reason or "").strip():
            return {"status": "failed"}, ["--test-skip-reason is required with --skip-tests"]
        return {"status": "skipped", "skip_allowed": True, "reason": args.test_skip_reason.strip()}, []
    defects = []
    commands = []
    manifest_commands = manifest_test_commands(branch)
    explicit_commands = [command for command in args.test_command if isinstance(command, str) and command.strip()]
    for command in [*explicit_commands, *manifest_commands]:
        result = run_test_command(command, cwd=worktree)
        commands.append(command)
        if result.returncode != 0:
            defects.append(f"test command failed ({result.returncode}): {command}\n{result.stdout.strip()}")
    evidence = list(args.test_evidence)
    declared_status_tests = []
    if not commands and not evidence:
        status_tests = branch_status.get("tests")
        if isinstance(status_tests, list) and all(isinstance(item, str) and item.strip() for item in status_tests):
            declared_status_tests = list(status_tests)
            defects.append(
                "branch status declared tests are informational; pre-review requires executed commands, explicit evidence, or --skip-tests"
            )
    if not commands and not evidence:
        defects.append("test evidence is required; pass --test-command, --test-evidence, or --skip-tests")
    check = {"status": "pass" if not defects else "failed"}
    if commands:
        check["commands"] = commands
    if manifest_commands:
        check["manifest_commands"] = manifest_commands
    if evidence:
        check["evidence"] = evidence
    if declared_status_tests:
        check["declared_status_tests"] = declared_status_tests
        check["declared_status_tests_are_evidence"] = False
    return check, defects


def create_gate(args: argparse.Namespace) -> tuple[Path, dict, list[str]]:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    bundle_dir = manifest_path.parent
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    branch_id = require_safe_label(args.branch_id, "--branch-id")
    review_packet_id = require_safe_label(args.review_packet_id or f"{branch_id}-R01", "--review-packet-id")
    manifest = read_json(manifest_path)
    branch = branch_entry(manifest, branch_id)
    branch_name = branch.get("branch_name")
    if not isinstance(branch_name, str) or not branch_name.strip():
        raise SystemExit(f"manifest branch {branch_id} is missing branch_name")
    status_path = (
        resolve_absolute_path(args.branch_status, "--branch-status", must_exist=True)
        if args.branch_status
        else safe_status_path(bundle_dir, branch.get("status_path"), "manifest branch status_path")
    )
    output_path = (
        resolve_absolute_path(args.output, "--output", must_exist=False)
        if args.output
        else safe_status_path(bundle_dir, branch.get("pre_review_gate_path"), "manifest branch pre_review_gate_path")
    )
    if output_path.exists() and not args.replace:
        raise SystemExit(f"pre-review gate already exists; pass --replace to recreate: {output_path}")

    branch_status = read_json(status_path)
    status_command = [
        "python3",
        (SCRIPT_DIR / "validate_branch_status.py").as_posix(),
        "--manifest",
        manifest_path.as_posix(),
        "--status",
        status_path.as_posix(),
        "--branch-id",
        branch_id,
        "--branch",
        branch_name,
        "--worktree",
        worktree.as_posix(),
        "--json",
    ]
    status_result = run_command(status_command)
    bootstrap_allowed_status_defects = (
        allowed_status_bootstrap_defects(status_result.stdout) if status_result.returncode != 0 else []
    )
    status_validation_passed = status_result.returncode == 0 or bool(bootstrap_allowed_status_defects)
    manifest_command = [
        "python3",
        (PREFLIGHT_SCRIPTS / "lint_goal_bundle.py").as_posix(),
        "--bundle-dir",
        bundle_dir.as_posix(),
        "--no-write",
    ]
    manifest_result = run_command(manifest_command)
    base_ref = validate_base_ref(str(manifest.get("base_ref", "main")))
    diff_command = f"git diff --check {base_ref}...HEAD"
    diff_result = run_command(["git", "diff", "--check", f"{base_ref}...HEAD"], cwd=worktree)
    unstaged_diff_command = "git diff --check HEAD"
    unstaged_diff_result = run_command(["git", "diff", "--check", "HEAD"], cwd=worktree)
    staged_diff_command = "git diff --cached --check HEAD"
    staged_diff_result = run_command(["git", "diff", "--cached", "--check", "HEAD"], cwd=worktree)
    untracked_check_command = "git ls-files --others --exclude-standard + internal untracked trailing-whitespace scan"
    untracked_defects = untracked_whitespace_defects(worktree)
    tests, test_defects = test_check(args, worktree, branch_status, branch)
    freshness_rel_path = BRANCH_VALIDATOR.worktree_freshness_path(branch_id)
    freshness_snapshot, freshness_defects = worktree_snapshot(worktree, str(base_ref), branch_id, branch_status)
    write_json(bundle_dir / freshness_rel_path, freshness_snapshot)
    route_policy_rel_path = BRANCH_VALIDATOR.review_route_policy_path(branch_id)
    write_json(bundle_dir / route_policy_rel_path, review_route_policy(branch, branch_id, manifest))
    worker_defects = worker_pass_defects(bundle_dir, branch, branch_status, branch_id)
    integration, integration_defects = worktree_integration_check(worktree, branch_status)
    ownership = ownership_check(branch, branch_status)
    semantic_probes, semantic_probe_defects = semantic_probe_check(branch, tests)
    dod_items = [item for item in args.dod_item if item.strip()]
    dod_value = branch_status.get("dod_checklist")
    declared_dod_items = (
        [item for item in dod_value if isinstance(item, str) and item.strip()] if isinstance(dod_value, list) else []
    )
    dod_defects = (
        []
        if dod_items
        else ["DoD evidence is required; pass --dod-item. Branch status dod_checklist is informational only."]
    )
    evidence_rel_path = BRANCH_VALIDATOR.review_evidence_path(branch_id)
    write_json(
        bundle_dir / evidence_rel_path,
        pre_review_evidence(
            branch_id,
            tests,
            dod_items,
            diff_command,
            ownership,
            declared_dod_items=declared_dod_items,
        ),
    )
    current_artifact_paths, diagnostic_artifact_paths = pre_review_input_artifact_paths(
        bundle_dir,
        branch,
        branch_id,
    )
    semantic_hashes, hash_defects, missing_current_artifact_paths = required_input_hashes(
        bundle_dir,
        current_artifact_paths,
        strict=True,
    )
    diagnostic_hashes, diagnostic_hash_defects, missing_diagnostic_hashes = required_input_hashes(
        bundle_dir,
        diagnostic_artifact_paths,
        strict=False,
    )
    reuse, reuse_eligibility, reuse_defects = reuse_policy(
        args,
        bundle_dir,
        semantic_hashes,
        current_artifact_paths,
        route_policy_path=route_policy_rel_path,
    )

    checks = {
        "manifest_validation": {
            "status": "pass" if manifest_result.returncode == 0 else "failed",
            "command": shlex.join(manifest_command),
        },
        "status_validation": {
            "status": "pass" if status_validation_passed else "failed",
            "command": shlex.join(status_command),
        },
        "tests": tests,
        "diff_check": {
            "status": "pass"
            if diff_result.returncode == 0
            and unstaged_diff_result.returncode == 0
            and staged_diff_result.returncode == 0
            and not untracked_defects
            else "failed",
            "commands": [diff_command, unstaged_diff_command, staged_diff_command, untracked_check_command],
        },
        "artifacts_fresh": {
            "status": "pass"
            if not hash_defects and not freshness_defects and not diagnostic_hash_defects
            else "failed",
            "artifacts": sorted(semantic_hashes),
            "current_artifacts": dict(sorted(semantic_hashes.items())),
            "diagnostic_artifacts": dict(sorted(diagnostic_hashes.items())),
            "worktree_freshness_path": freshness_rel_path,
            "missing_diagnostic_artifacts": sorted(missing_diagnostic_hashes),
        },
        "worker_evidence": {
            "status": "pass" if not worker_defects else "failed",
            "expected_packet_ids": expected_worker_packet_ids(branch, branch_id),
        },
        "worktree_integration": integration,
        "ownership": ownership,
        "semantic_probes": semantic_probes,
        "dod_evidence": {
            "status": "pass" if not dod_defects else "failed",
            "items": dod_items,
            "declared_items": declared_dod_items,
            "declared_items_are_evidence": False,
        },
    }
    defects = []
    if manifest_result.returncode != 0:
        defects.append("manifest validation failed:\n" + manifest_result.stdout.strip())
    if bootstrap_allowed_status_defects:
        checks["status_validation"]["bootstrap_allowed_defects"] = bootstrap_allowed_status_defects
        checks["status_validation"]["bootstrap_reason"] = (
            "allowed stale or missing pre-review artifacts while creating replacement gate"
        )
    elif status_result.returncode != 0:
        defects.append("branch status validation failed:\n" + status_result.stdout.strip())
    if diff_result.returncode != 0:
        defects.append(f"diff check failed:\n{diff_result.stdout.strip()}")
    if unstaged_diff_result.returncode != 0:
        defects.append(f"unstaged diff check failed:\n{unstaged_diff_result.stdout.strip()}")
    if staged_diff_result.returncode != 0:
        defects.append(f"staged diff check failed:\n{staged_diff_result.stdout.strip()}")
    defects.extend(untracked_defects)
    defects.extend(test_defects)
    defects.extend(hash_defects)
    defects.extend(diagnostic_hash_defects)
    defects.extend(freshness_defects)
    defects.extend(worker_defects)
    defects.extend(integration_defects)
    defects.extend(ownership.get("defects", []))
    defects.extend(semantic_probe_defects)
    defects.extend(reuse_defects)
    defects.extend(dod_defects)

    gate = {
        "schema_version": 2,
        "branch_id": branch_id,
        "status": "pass" if not defects else "failed",
        "review_packet_id": review_packet_id,
        "commands_run": [
            shlex.join(status_command),
            shlex.join(manifest_command),
            *tests.get("commands", []),
            diff_command,
            unstaged_diff_command,
            staged_diff_command,
            untracked_check_command,
        ],
        "checks": checks,
        "semantic_input_hashes": semantic_hashes,
        "volatile_input_hashes": {
            "diagnostic_artifacts": diagnostic_hashes,
            "missing_diagnostic_artifacts": sorted(missing_diagnostic_hashes),
            "missing_current_artifacts": sorted(missing_current_artifact_paths),
            "non_canonical_inputs": dict(sorted(diagnostic_hashes.items())),
        },
        "reuse_eligibility": reuse_eligibility,
        "reuse_policy": reuse,
    }
    if defects:
        gate["defects"] = defects
    write_json(output_path, gate)

    if not defects:
        validation_defects: list[str] = []
        STATUS_VALIDATION.validate_pre_review_gate_artifact(
            validation_defects,
            output_path,
            "pre_review_gate",
            manifest_path=manifest_path,
            branch_id=branch_id,
            review_packet_id=review_packet_id,
            required_input_paths=current_artifact_paths,
        )
        if validation_defects:
            gate["status"] = "failed"
            gate["defects"] = validation_defects
            write_json(output_path, gate)
            defects.extend(validation_defects)
    return output_path, gate, defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--branch-status")
    parser.add_argument("--review-packet-id")
    parser.add_argument("--output")
    parser.add_argument("--test-command", action="append", default=[])
    parser.add_argument("--test-evidence", action="append", default=[])
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--test-skip-reason")
    parser.add_argument("--dod-item", action="append", default=[])
    parser.add_argument("--reuse-source-review")
    parser.add_argument("--reuse-source-telemetry")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    output_path, gate, defects = create_gate(args)
    result = {
        "status": "pass" if not defects else "failed",
        "gate_path": output_path.as_posix(),
        "review_packet_id": gate.get("review_packet_id"),
        "defects": defects,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(output_path)
        for defect in defects:
            print(defect)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
