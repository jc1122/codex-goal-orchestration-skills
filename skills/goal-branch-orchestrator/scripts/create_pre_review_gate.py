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


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
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


def run_shell_command(command: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


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


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be an array")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest must contain exactly one branch {branch_id!r}")
    return matches[0]


def bundle_relative(bundle_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(bundle_dir.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"path must be inside bundle directory: {path}") from exc


def safe_status_path(bundle_dir: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip() or not is_repo_relative_path(value):
        raise SystemExit(f"{field} must be a safe bundle-relative path")
    return (bundle_dir / value).resolve()


def path_is_owned(path: str, owned_paths: list[str]) -> bool:
    for owned in owned_paths:
        if path == owned or path.startswith(f"{owned.rstrip('/')}/"):
            return True
    return False


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


def required_input_hashes(bundle_dir: Path, branch: dict, branch_id: str) -> tuple[dict[str, str], list[str]]:
    rel_paths = BRANCH_VALIDATOR.required_pre_review_input_paths(branch, branch_id, bundle_dir=bundle_dir)
    hashes: dict[str, str] = {}
    defects = []
    for rel_path in rel_paths:
        if not is_repo_relative_path(rel_path):
            defects.append(f"unsafe required input path: {rel_path!r}")
            continue
        path = bundle_dir / rel_path
        if not path.exists():
            defects.append(f"required input does not exist: {rel_path}")
            continue
        hashes[rel_path] = sha256_file(path)
    return hashes, defects


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
            defects.append(f"worker packet {packet_id} must be pass before reviewer launch, got {status.get('status')!r}")
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
                defects.append(f"worker scheduler reports {field} before reviewer launch: " + ", ".join(str(item) for item in values))
        finished_ids = [item for item in parallelism.get("finished_ids", []) if isinstance(item, str)]
        missing_finished = [packet_id for packet_id in expected_ids if packet_id not in finished_ids]
        if missing_finished:
            defects.append("worker scheduler is missing finish evidence before reviewer launch: " + ", ".join(missing_finished))
    if branch_status.get("status") in {"blocked", "failed"}:
        defects.append(f"branch status is {branch_status.get('status')!r}; reviewer launch requires integrated worker evidence")
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


def untracked_whitespace_defects(worktree: Path) -> list[str]:
    result = run_command(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if result.returncode != 0:
        return [f"untracked file scan failed:\n{result.stdout.strip()}"]
    defects: list[str] = []
    for rel_path in result.stdout.splitlines():
        rel_path = rel_path.strip()
        if not rel_path or not is_repo_relative_path(rel_path, reject_porcelain=True) or is_runtime_cache_path(rel_path):
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


def worktree_snapshot(worktree: Path, base_ref: str, branch_id: str, branch_status: dict) -> tuple[dict, list[str]]:
    defects = []
    head_result = run_command(["git", "rev-parse", "HEAD"], cwd=worktree)
    merge_base_result = run_command(["git", "merge-base", base_ref, "HEAD"], cwd=worktree)
    name_status_result = run_command(["git", "diff", "--name-status", "--find-renames", f"{base_ref}...HEAD"], cwd=worktree)
    if head_result.returncode != 0:
        defects.append("could not capture worktree HEAD:\n" + head_result.stdout.strip())
    if merge_base_result.returncode != 0:
        defects.append("could not capture worktree merge-base:\n" + merge_base_result.stdout.strip())
    if name_status_result.returncode != 0:
        defects.append("could not capture worktree name-status diff:\n" + name_status_result.stdout.strip())
    base_paths = git_lines(["git", "diff", "--name-only", f"{base_ref}...HEAD"], cwd=worktree, defects=defects, label=f"git diff --name-only {base_ref}...HEAD")
    unstaged_paths = git_lines(["git", "diff", "--name-only", "HEAD"], cwd=worktree, defects=defects, label="git diff --name-only HEAD")
    staged_paths = git_lines(["git", "diff", "--cached", "--name-only", "HEAD"], cwd=worktree, defects=defects, label="git diff --cached --name-only HEAD")
    untracked_paths = git_lines(["git", "ls-files", "--others", "--exclude-standard"], cwd=worktree, defects=defects, label="git ls-files --others --exclude-standard")
    status_paths = [
        path
        for path in branch_status.get("changed_files", [])
        if isinstance(path, str) and path.strip() and is_repo_relative_path(path, reject_porcelain=True)
    ] if isinstance(branch_status.get("changed_files"), list) else []
    current_paths = []
    for path in [*status_paths, *base_paths, *unstaged_paths, *staged_paths, *untracked_paths]:
        if path not in current_paths:
            current_paths.append(path)
    snapshot = {
        "schema_version": 1,
        "branch_id": branch_id,
        "worktree": worktree.as_posix(),
        "base_ref": base_ref,
        "worktree_head": head_result.stdout.strip().splitlines()[0] if head_result.returncode == 0 and head_result.stdout.strip() else "",
        "merge_base": merge_base_result.stdout.strip().splitlines()[0] if merge_base_result.returncode == 0 and merge_base_result.stdout.strip() else "",
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
        for key, value in review.get("semantic_input_hashes", {}).items()
        if isinstance(key, str) and isinstance(value, str)
    } if isinstance(review.get("semantic_input_hashes"), dict) else {}
    if source_hashes != semantic_hashes:
        defects.append("source review semantic_input_hashes do not match current pre-review inputs")
    telemetry_defects: list[str] = []
    STATUS_VALIDATION.validate_telemetry_artifact(
        telemetry_defects,
        telemetry_path,
        "reuse_source_telemetry",
        role="reviewer",
        allowed_aliases=("gpt-5.4-mini", "gpt-5.4", "gpt-5.5"),
        require_called=True,
    )
    defects.extend(telemetry_defects)
    accepted = not defects
    policy = {
        "mode": "reuse" if accepted else "new",
        "accepted": accepted,
        "semantic_hashes_match": accepted,
        "source_review_path": source_review_display,
        "source_telemetry_path": source_telemetry_display,
    }
    eligibility = dict(base_eligibility)
    eligibility.update(
        {
            "eligible": accepted,
            "reason": "semantic hashes, route policy, worktree freshness, test evidence, and source telemetry match"
            if accepted
            else "; ".join(defects),
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
    requirements = [
        keyword
        for keyword in SEMANTIC_PROBE_KEYWORDS
        if semantic_keyword_present(combined, keyword)
    ]
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


def semantic_probe_check(branch: dict, tests: dict) -> tuple[dict, list[str]]:
    requirements = branch_semantic_probe_requirements(branch)
    targets = branch_semantic_probe_targets(branch)
    commands = tests.get("commands") if isinstance(tests.get("commands"), list) else []
    command_values = [item for item in commands if isinstance(item, str) and item.strip()]
    missing_targets: list[str] = []
    if requirements and targets and command_values:
        command_text = "\n".join(command_values).lower()
        missing_targets = [target for target in targets if target not in command_text]
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
        result = run_shell_command(command, cwd=worktree)
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
        allowed_status_bootstrap_defects(status_result.stdout)
        if status_result.returncode != 0
        else []
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
    base_ref = manifest.get("base_ref", "main")
    diff_command = f"git diff --check {base_ref}...HEAD"
    diff_result = run_shell_command(diff_command, cwd=worktree)
    unstaged_diff_command = "git diff --check HEAD"
    unstaged_diff_result = run_shell_command(unstaged_diff_command, cwd=worktree)
    staged_diff_command = "git diff --cached --check HEAD"
    staged_diff_result = run_shell_command(staged_diff_command, cwd=worktree)
    untracked_check_command = "git ls-files --others --exclude-standard + internal untracked trailing-whitespace scan"
    untracked_defects = untracked_whitespace_defects(worktree)
    tests, test_defects = test_check(args, worktree, branch_status, branch)
    freshness_rel_path = BRANCH_VALIDATOR.worktree_freshness_path(branch_id)
    freshness_snapshot, freshness_defects = worktree_snapshot(worktree, str(base_ref), branch_id, branch_status)
    write_json(bundle_dir / freshness_rel_path, freshness_snapshot)
    route_policy_rel_path = BRANCH_VALIDATOR.review_route_policy_path(branch_id)
    write_json(bundle_dir / route_policy_rel_path, review_route_policy(branch, branch_id, manifest))
    worker_defects = worker_pass_defects(bundle_dir, branch, branch_status, branch_id)
    ownership = ownership_check(branch, branch_status)
    semantic_probes, semantic_probe_defects = semantic_probe_check(branch, tests)
    dod_items = [item for item in args.dod_item if item.strip()]
    dod_value = branch_status.get("dod_checklist")
    declared_dod_items = [item for item in dod_value if isinstance(item, str) and item.strip()] if isinstance(dod_value, list) else []
    dod_defects = [] if dod_items else ["DoD evidence is required; pass --dod-item. Branch status dod_checklist is informational only."]
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
    semantic_hashes, hash_defects = required_input_hashes(bundle_dir, branch, branch_id)
    reuse, reuse_eligibility, reuse_defects = reuse_policy(
        args,
        bundle_dir,
        semantic_hashes,
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
            "status": "pass" if diff_result.returncode == 0 and unstaged_diff_result.returncode == 0 and staged_diff_result.returncode == 0 and not untracked_defects else "failed",
            "commands": [diff_command, unstaged_diff_command, staged_diff_command, untracked_check_command],
        },
        "artifacts_fresh": {
            "status": "pass" if not hash_defects and not freshness_defects else "failed",
            "artifacts": sorted(semantic_hashes),
            "worktree_freshness_path": freshness_rel_path,
        },
        "worker_evidence": {
            "status": "pass" if not worker_defects else "failed",
            "expected_packet_ids": expected_worker_packet_ids(branch, branch_id),
        },
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
        checks["status_validation"]["bootstrap_reason"] = "allowed stale or missing pre-review artifacts while creating replacement gate"
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
    defects.extend(freshness_defects)
    defects.extend(worker_defects)
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
        "volatile_input_hashes": {},
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
            required_input_paths=BRANCH_VALIDATOR.required_pre_review_input_paths(
                branch,
                branch_id,
                bundle_dir=bundle_dir,
            ),
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
