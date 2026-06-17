#!/usr/bin/env python3
"""Promote command-verified branch repair evidence into canonical worker status."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple


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

CONTRACT = _load_module("goal_shared_orchestration_contract", SHARED_SCRIPTS / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")

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


def run_git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=worktree,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {worktree}:\n{result.stdout}")
    return result.stdout.strip()


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be an array")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest must contain exactly one branch {branch_id!r}")
    return matches[0]


def work_item(branch: dict, packet_id: str) -> dict:
    items = branch.get("work_items")
    if not isinstance(items, list):
        raise SystemExit("manifest branch must declare work_items")
    matches = [item for item in items if isinstance(item, dict) and item.get("packet_id") == packet_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest branch must contain exactly one work item for {packet_id!r}")
    return matches[0]


def safe_bundle_rel_path(bundle_dir: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(bundle_dir.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"path must be inside bundle: {path}") from exc
    if not is_repo_relative_path(rel):
        raise SystemExit(f"path is not safe bundle-relative: {rel}")
    return rel


def changed_files_from_git(worktree: Path, base_ref: str) -> list[str]:
    output = run_git(worktree, "diff", "--name-only", f"{base_ref}...HEAD")
    return [
        line.strip()
        for line in output.splitlines()
        if line.strip() and is_repo_relative_path(line.strip(), reject_porcelain=True)
    ]


def path_is_owned(path: str, owned_paths: list[str]) -> bool:
    return any(path == owned or path.startswith(f"{owned.rstrip('/')}/") for owned in owned_paths)


def evidence_commands(evidence: dict) -> tuple[list[str], list[str]]:
    commands: list[str] = []
    tests: list[str] = []
    for item in evidence.get("local_validation", []):
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        result = item.get("result")
        if isinstance(command, str) and command.strip():
            commands.append(command.strip())
            lowered = command.lower()
            if any(token in lowered for token in ("pytest", " test", "tests/", "unittest")):
                tests.append(command.strip())
        if isinstance(result, str) and any(token in result.lower() for token in ("failed", "error", "mismatch=false")):
            raise SystemExit(f"repair evidence records non-passing validation result for {command!r}: {result}")
    for value in evidence.get("commands_run", []):
        if isinstance(value, str) and value.strip() and value.strip() not in commands:
            commands.append(value.strip())
    for value in evidence.get("tests", []):
        if isinstance(value, str) and value.strip() and value.strip() not in tests:
            tests.append(value.strip())
    if not any("git diff --check" in command for command in commands):
        raise SystemExit("repair evidence must include a git diff --check command")
    if not tests:
        raise SystemExit("repair evidence must include at least one test command")
    return commands, tests


def run_scheduler_promotion(manifest_path: Path, branch_id: str, packet_id: str) -> None:
    command = [
        "python3",
        (SCRIPT_DIR / "scheduler_tick.py").as_posix(),
        "--manifest",
        manifest_path.as_posix(),
        "--scope",
        "worker",
        "--branch-id",
        branch_id,
        "--runtime-ref",
        "goal-branch-orchestrator-repair-promotion",
        "--launch",
        packet_id,
        "--finish",
        packet_id,
        "--status",
        "pass",
        "--close",
        packet_id,
        "--validate-final",
    ]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise SystemExit("scheduler repair promotion failed:\n" + result.stdout)


class PromoteInputs(NamedTuple):
    manifest_path: Path
    worktree: Path
    evidence_path: Path
    branch_id: str
    packet_id: str
    bundle_dir: Path


class ManifestTargets(NamedTuple):
    manifest: dict
    branch: dict
    item: dict
    branch_name: str
    item_id: str


class PromotionArchive(NamedTuple):
    source_status: Path
    previous_status: dict
    packet_dir: Path
    telemetry_path: Path
    archived_status: Path
    archived_telemetry: Path


def resolve_promote_inputs(args: argparse.Namespace) -> PromoteInputs:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    evidence_path = resolve_absolute_path(args.evidence, "--evidence", must_exist=True)
    branch_id = require_safe_label(args.branch_id, "--branch-id")
    packet_id = require_safe_label(args.packet_id, "--packet-id")
    bundle_dir = manifest_path.parent
    try:
        evidence_path.resolve().relative_to((bundle_dir / "branches").resolve())
    except ValueError as exc:
        raise SystemExit("--evidence must be inside the bundle branches directory") from exc
    return PromoteInputs(manifest_path, worktree, evidence_path, branch_id, packet_id, bundle_dir)


def load_manifest_targets(manifest_path: Path, branch_id: str, packet_id: str) -> ManifestTargets:
    manifest = read_json(manifest_path)
    branch = branch_entry(manifest, branch_id)
    item = work_item(branch, packet_id)
    branch_name = branch.get("branch_name")
    if not isinstance(branch_name, str) or not branch_name.strip():
        raise SystemExit(f"manifest branch {branch_id} is missing branch_name")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise SystemExit(f"manifest work item for {packet_id} is missing id")
    return ManifestTargets(manifest, branch, item, branch_name, item_id)


def validate_evidence_identity(evidence: dict, branch_id: str, packet_id: str) -> None:
    if evidence.get("schema_version") != 1:
        raise SystemExit("repair evidence schema_version must be 1")
    if evidence.get("kind") != "worker-repair-promotion":
        raise SystemExit("repair evidence kind must be worker-repair-promotion")
    if evidence.get("branch_id") != branch_id or evidence.get("packet_id") != packet_id:
        raise SystemExit("repair evidence branch_id and packet_id must match arguments")
    if evidence.get("code_integrated") is not True:
        raise SystemExit("repair evidence must record code_integrated=true")


def verify_worktree_integration(worktree: Path, evidence: dict) -> str:
    head = run_git(worktree, "rev-parse", "HEAD")
    if evidence.get("integrated_commit") != head:
        raise SystemExit("repair evidence integrated_commit must match current worktree HEAD")
    status_output = run_git(worktree, "status", "--short", "--untracked-files=all")
    if status_output.strip():
        raise SystemExit("repair promotion requires a clean branch worktree:\n" + status_output)
    return head


def resolve_promoted_changes(manifest: dict, branch: dict, item: dict, worktree: Path) -> list[str]:
    base_ref = str(manifest.get("base_ref", "main"))
    changed_files = changed_files_from_git(worktree, base_ref)
    branch_owned = [
        owned
        for work in branch.get("work_items", [])
        if isinstance(work, dict)
        for owned in work.get("owned_paths", [])
        if isinstance(owned, str) and owned.strip()
    ]
    unowned = [path for path in changed_files if branch_owned and not path_is_owned(path, branch_owned)]
    if unowned:
        raise SystemExit(
            "repair evidence cannot promote branch changes outside declared owned paths: " + ", ".join(unowned)
        )

    item_owned = [owned for owned in item.get("owned_paths", []) if isinstance(owned, str) and owned.strip()]
    promoted_changed = [path for path in changed_files if not item_owned or path_is_owned(path, item_owned)]
    if not promoted_changed:
        raise SystemExit("repair promotion found no current changed files inside the target work item owned paths")
    return promoted_changed


def archive_source_status(bundle_dir: Path, packet_id: str, head: str, replace: bool) -> PromotionArchive:
    source_status = bundle_dir / "workers" / packet_id / "status.json"
    if not source_status.exists():
        raise SystemExit(f"canonical worker status does not exist: {source_status}")
    previous_status = read_json(source_status)
    if previous_status.get("status") == "pass" and not replace:
        raise SystemExit("canonical worker status is already pass; pass --replace to rewrite")

    packet_dir = source_status.parent
    promotion_dir = packet_dir / "repair-promotions"
    promotion_id = f"promotion-{head[:12]}"
    archived_status = promotion_dir / f"{promotion_id}.source-status.json"
    archived_telemetry = promotion_dir / f"{promotion_id}.source-telemetry.json"
    if archived_status.exists() and not replace:
        raise SystemExit(f"repair promotion already exists: {archived_status}")
    promotion_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_status, archived_status)
    telemetry_path = packet_dir / "telemetry.json"
    if telemetry_path.exists():
        shutil.copyfile(telemetry_path, archived_telemetry)
    return PromotionArchive(
        source_status, previous_status, packet_dir, telemetry_path, archived_status, archived_telemetry
    )


def resolve_route_selection(previous_status: dict, item: dict) -> tuple[list[str], str]:
    source_ladder = previous_status.get("selected_ladder")
    selected_ladder = (
        [value for value in source_ladder if isinstance(value, str)] if isinstance(source_ladder, list) else []
    )
    if not selected_ladder:
        selected_ladder = ["deterministic-repair"]
    route_class = (
        previous_status.get("route_class")
        if isinstance(previous_status.get("route_class"), str)
        else item.get("route_class", "normal-code")
    )
    return selected_ladder, route_class


def build_worker_status(
    inputs: PromoteInputs,
    targets: ManifestTargets,
    archive: PromotionArchive,
    evidence: dict,
    head: str,
    promoted_changed: list[str],
    commands_run: list[str],
    tests: list[str],
    selected_ladder: list[str],
    route_class: str,
) -> dict:
    bundle_dir = inputs.bundle_dir
    return {
        "packet_id": inputs.packet_id,
        "role": "worker",
        "status": "pass",
        "branch_id": inputs.branch_id,
        "work_item_id": targets.item_id,
        "manifest_hash": sha256_file(inputs.manifest_path),
        "manifest_epoch": "current",
        "worktree_path": inputs.worktree.as_posix(),
        "route_id": f"{inputs.packet_id}:{route_class}:{','.join(selected_ladder)}",
        "evidence_summary": evidence.get("evidence_summary")
        if isinstance(evidence.get("evidence_summary"), str) and evidence.get("evidence_summary").strip()
        else "Command-verified branch repair evidence promoted this worker after route failures.",
        "branch": targets.branch_name,
        "worktree": inputs.worktree.as_posix(),
        "route_class": route_class,
        "selected_ladder": selected_ladder,
        "selection_reason": "Deterministic repair promotion after route failures; see repair_evidence_path.",
        "changed_files": promoted_changed,
        "commands_run": commands_run,
        "tests": tests,
        "blockers": [],
        "handoff": evidence.get("handoff")
        if isinstance(evidence.get("handoff"), str) and evidence.get("handoff").strip()
        else "Worker promoted by validated repair evidence.",
        "repair_evidence_path": safe_bundle_rel_path(bundle_dir, inputs.evidence_path),
        "repair_promotion": {
            "kind": "worker-repair-promotion",
            "source_status_path": safe_bundle_rel_path(bundle_dir, archive.archived_status),
            "source_telemetry_path": safe_bundle_rel_path(bundle_dir, archive.archived_telemetry)
            if archive.archived_telemetry.exists()
            else None,
            "integrated_commit": head,
            "validated_by": "promote_worker_repair_evidence.py",
        },
    }


def write_route_artifact(
    packet_dir: Path, packet_id: str, route_class: str, selected_ladder: list[str], selection_reason: str
) -> None:
    route_path = packet_dir / "route.json"
    route = read_json(route_path) if route_path.exists() else {}
    route.update(
        {
            "schema_version": 1,
            "packet_id": packet_id,
            "role": "worker",
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "policy_router": route.get("policy_router", "deterministic-repair-promotion"),
            "policy_version": route.get("policy_version", "goal-worker-route-policy-v1"),
            "route_policy_version": route.get("route_policy_version", "goal-route-policy-v2"),
            "allowed_aliases": route.get("allowed_aliases", selected_ladder),
            "default_ladder": route.get("default_ladder", selected_ladder),
        }
    )
    write_json(route_path, route)


def write_packet_artifacts(
    inputs: PromoteInputs,
    archive: PromotionArchive,
    packet_id: str,
    route_class: str,
    selected_ladder: list[str],
    selection_reason: str,
) -> None:
    bundle_dir = inputs.bundle_dir
    packet_dir = archive.packet_dir
    telemetry_path = archive.telemetry_path
    write_json(
        packet_dir / "launcher-state.json",
        {
            "schema_version": 1,
            "packet_id": packet_id,
            "role": "worker",
            "terminal_state": "pass",
            "events": [
                {
                    "seq": 1,
                    "state": "pass",
                    "alias": "deterministic-repair",
                    "dirty": False,
                    "returncode": 0,
                    "output_nonempty": True,
                    "repair_evidence_path": safe_bundle_rel_path(bundle_dir, inputs.evidence_path),
                }
            ],
        },
    )
    write_json(
        packet_dir / "packet.summary.json",
        {
            "schema_version": 1,
            "packet_id": packet_id,
            "role": "worker",
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "output_path": "status.json",
            "output_exists": True,
            "output_status": "pass",
            "telemetry_path": "telemetry.json",
            "telemetry_exists": telemetry_path.exists(),
            "launcher_state_path": "launcher-state.json",
            "launcher_state_exists": True,
            "terminal_state": "pass",
            "next_action": "validate_and_collect",
            "attempts": [
                {
                    "attempt_index": 0,
                    "alias": "deterministic-repair",
                    "state": "pass",
                    "failure_class": "none",
                    "repair_evidence_path": safe_bundle_rel_path(bundle_dir, inputs.evidence_path),
                }
            ],
            "repair_evidence_path": safe_bundle_rel_path(bundle_dir, inputs.evidence_path),
        },
    )


def promote(args: argparse.Namespace) -> dict:
    inputs = resolve_promote_inputs(args)
    targets = load_manifest_targets(inputs.manifest_path, inputs.branch_id, inputs.packet_id)

    evidence = read_json(inputs.evidence_path)
    validate_evidence_identity(evidence, inputs.branch_id, inputs.packet_id)

    head = verify_worktree_integration(inputs.worktree, evidence)

    promoted_changed = resolve_promoted_changes(targets.manifest, targets.branch, targets.item, inputs.worktree)

    commands_run, tests = evidence_commands(evidence)
    archive = archive_source_status(inputs.bundle_dir, inputs.packet_id, head, args.replace)

    selected_ladder, route_class = resolve_route_selection(archive.previous_status, targets.item)
    worker_status = build_worker_status(
        inputs,
        targets,
        archive,
        evidence,
        head,
        promoted_changed,
        commands_run,
        tests,
        selected_ladder,
        route_class,
    )
    write_json(archive.source_status, worker_status)
    write_route_artifact(
        archive.packet_dir, inputs.packet_id, route_class, selected_ladder, worker_status["selection_reason"]
    )
    write_packet_artifacts(
        inputs, archive, inputs.packet_id, route_class, selected_ladder, worker_status["selection_reason"]
    )
    run_scheduler_promotion(inputs.manifest_path, inputs.branch_id, inputs.packet_id)
    return {
        "status": "pass",
        "worker_status_path": archive.source_status.as_posix(),
        "repair_evidence_path": inputs.evidence_path.as_posix(),
        "packet_id": inputs.packet_id,
        "branch_id": inputs.branch_id,
        "integrated_commit": head,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = promote(args)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(result["worker_status_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
