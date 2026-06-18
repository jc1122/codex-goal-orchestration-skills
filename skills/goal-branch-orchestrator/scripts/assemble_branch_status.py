#!/usr/bin/env python3
"""Assemble a conservative branch status artifact from manifest-owned runtime artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shlex
import shutil
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

CONTRACT = _load_module("goal_shared_orchestration_contract", SHARED_SCRIPTS / "orchestration_contract.py")
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")
STATUS_VALIDATION = _load_module("goal_shared_status_validation", SHARED_SCRIPTS / "status_validation.py")
BRANCH_VALIDATOR = _load_module("goal_branch_validate_branch_status", SCRIPT_DIR / "validate_branch_status.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
is_repo_relative_path = PATH_RULES.is_repo_relative_path
require_safe_label = PATH_RULES.require_safe_packet_label


LOCAL_VALIDATION_FAILURE_TOKENS = ("failed", "error", "mismatch=false")
TEST_COMMAND_TOKENS = (" pytest ", "python3 -m pytest", "python -m pytest", "make test", "npm test")


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def read_object_or_blocker(path: Path, blockers: list[str], label: str) -> dict | None:
    """Read a semi-trusted runtime artifact; on a malformed/non-object file record a
    conservative blocker and return None rather than aborting the whole assembler."""
    try:
        return read_json(path)
    except SystemExit as exc:
        blockers.append(f"{label} is not a readable JSON object: {exc}")
        return None


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def annotate_status_lanes(branch_status: dict, validation_defects: list[str]) -> None:
    status = branch_status.get("status")
    review_status = branch_status.get("review_status")
    blockers = branch_status.get("blockers")
    blocker_count = len(blockers) if isinstance(blockers, list) else 0
    branch_status["schema_status"] = "pass" if not validation_defects else "failed"
    branch_status["runtime_status"] = status if status in CONTRACT.STATUSES else "failed"
    branch_status["dod_status"] = (
        "pass"
        if status == "pass" and review_status == "mergeable" and blocker_count == 0 and not validation_defects
        else "incomplete"
    )
    branch_status["artifact_valid"] = not validation_defects
    branch_status["runtime_success"] = status == "pass"
    branch_status["dod_complete"] = branch_status["dod_status"] == "pass"
    branch_status["review_complete"] = review_status == "mergeable"
    branch_status["resume_action"] = "reuse_terminal_status" if not validation_defects else "repair_or_reassemble"


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


def run_git(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        shell=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        raise SystemExit("manifest branches must be an array")
    matches = [branch for branch in branches if isinstance(branch, dict) and branch.get("id") == branch_id]
    if len(matches) != 1:
        raise SystemExit(f"manifest must contain exactly one branch {branch_id!r}")
    return matches[0]


def safe_bundle_path(bundle_dir: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip() or not is_repo_relative_path(value):
        raise SystemExit(f"{field} must be a safe bundle-relative path")
    return (bundle_dir / value).resolve()


def worker_role(item: dict) -> str:
    role = item.get("worker_type", "worker")
    if role == "research":
        role = "research-worker"
    return role if role in CONTRACT.WORK_ITEM_ROLES else "worker"


def command_is_test(command: str) -> bool:
    normalized = f" {command.lower()} "
    return any(token in normalized for token in TEST_COMMAND_TOKENS)


def local_validation_failed(result: str) -> bool:
    lowered = result.lower()
    return any(token in lowered for token in LOCAL_VALIDATION_FAILURE_TOKENS)


def issue059_repair_evidence_path(bundle_dir: Path, branch_id: str, packet_id: str) -> Path:
    return bundle_dir / "branches" / f"{branch_id}.{packet_id}.repair-evidence.json"


def synthesize_issue059_repair_evidence(
    bundle_dir: Path,
    branch: dict,
    branch_id: str,
    packet_id: str,
    worktree: Path,
) -> tuple[Path | None, list[str]]:
    partial_path = bundle_dir / "branches" / f"{branch_id}.partial.evidence.json"
    if not partial_path.exists():
        return None, [f"no repair-evidence candidate for {packet_id}"]
    try:
        partial = read_json(partial_path)
    except SystemExit as exc:
        return None, [f"partial repair evidence for {packet_id} is not a readable JSON object: {exc}"]
    if partial.get("branch_id") != branch_id:
        return None, [f"partial repair evidence branch_id mismatch for {packet_id}: {partial.get('branch_id')!r}"]
    if partial.get("status") != "partial":
        return None, [f"partial repair evidence status is not partial for {packet_id}: {partial.get('status')!r}"]
    if partial.get("code_integrated") is not True:
        return None, [f"partial repair evidence did not set code_integrated=true for {packet_id}"]
    worker_blocker = partial.get("worker_blocker")
    if not isinstance(worker_blocker, dict):
        return None, [f"partial repair evidence is missing worker_blocker metadata for {packet_id}"]
    if worker_blocker.get("packet_id") != packet_id:
        return None, [
            f"partial repair evidence maps to {worker_blocker.get('packet_id')!r}, not {packet_id!r} for repair promotion",
        ]
    integrated_commit = partial.get("integrated_commit")
    if not isinstance(integrated_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", integrated_commit):
        return None, [f"partial repair evidence missing valid 40-char integrated_commit for {packet_id}"]
    local_validation = partial.get("local_validation")
    if not isinstance(local_validation, list) or not local_validation:
        return None, [f"partial repair evidence missing local_validation commands for {packet_id}"]
    commands: list[str] = []
    tests: list[str] = []
    for item in local_validation:
        if not isinstance(item, dict):
            return None, ["partial repair evidence local_validation entries must be objects"]
        command = item.get("command")
        result = item.get("result")
        if not isinstance(command, str) or not command.strip():
            continue
        command = command.strip()
        if command not in commands:
            commands.append(command)
        if command_is_test(command) and command not in tests:
            tests.append(command)
        if isinstance(result, str) and local_validation_failed(result):
            return None, [f"partial repair evidence command failed: {command!r} -> {result!r}"]
    if not commands:
        return None, [f"partial repair evidence for {packet_id} has no commands"]
    if not tests:
        return None, [f"partial repair evidence for {packet_id} has no test commands"]
    if not any("git diff --check" in command for command in commands):
        return None, [f"partial repair evidence for {packet_id} lacks git diff --check command"]

    work_item_id = ""
    work_items = branch.get("work_items")
    if isinstance(work_items, list):
        for item in work_items:
            if isinstance(item, dict) and item.get("packet_id") == packet_id and isinstance(item.get("id"), str):
                work_item_id = item.get("id")
                break
    evidence = {
        "schema_version": 1,
        "kind": "worker-repair-promotion",
        "branch_id": branch_id,
        "packet_id": packet_id,
        "work_item_id": work_item_id,
        "code_integrated": True,
        "integrated_commit": integrated_commit,
        "worktree": worktree.as_posix(),
        "commands_run": commands,
        "tests": tests,
        "local_validation": local_validation,
        "evidence_summary": f"Recovered deterministic repair evidence from {branch_id}.partial.evidence.json.",
        "handoff": f"Recovered repair evidence for {packet_id} after worker route failures.",
    }
    repair_path = issue059_repair_evidence_path(bundle_dir, branch_id, packet_id)
    write_json(repair_path, evidence)
    return repair_path, []


def promote_worker_with_repair_evidence(
    manifest_path: Path,
    bundle_dir: Path,
    branch: dict,
    branch_id: str,
    packet_id: str,
    status: dict,
    worktree: Path,
) -> list[str]:
    blockers: list[str] = []
    if status.get("status") == "pass":
        return blockers
    repair_path = issue059_repair_evidence_path(bundle_dir, branch_id, packet_id)
    if not repair_path.exists():
        partial_path = bundle_dir / "branches" / f"{branch_id}.partial.evidence.json"
        if not partial_path.exists():
            return blockers
        synth_path, synth_errors = synthesize_issue059_repair_evidence(
            bundle_dir=bundle_dir,
            branch=branch,
            branch_id=branch_id,
            packet_id=packet_id,
            worktree=worktree,
        )
        if synth_path is None:
            blockers.extend(
                f"partial repair evidence synthesis unavailable for {packet_id}: {error}" for error in synth_errors
            )
            return blockers
        repair_path = synth_path
    result = subprocess.run(
        [
            "python3",
            (SCRIPT_DIR / "promote_worker_repair_evidence.py").as_posix(),
            "--manifest",
            manifest_path.as_posix(),
            "--branch-id",
            branch_id,
            "--packet-id",
            packet_id,
            "--worktree",
            worktree.as_posix(),
            "--evidence",
            repair_path.as_posix(),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        blockers.append(f"could not promote repair evidence for {packet_id}: {result.stdout.strip()}")
    return blockers


def collect_worker_statuses(
    bundle_dir: Path,
    manifest_path: Path,
    manifest: dict,
    branch: dict,
    branch_id: str,
    worktree: Path,
) -> tuple[list[dict], list[str]]:
    statuses: list[dict] = []
    blockers: list[str] = []
    work_items = branch.get("work_items")
    if not isinstance(work_items, list):
        return statuses, [f"manifest branch {branch_id} does not declare work_items"]
    for item in work_items:
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            item_id = item.get("id")
            packet_id = f"{branch_id}-{item_id}" if isinstance(item_id, str) else ""
        if not packet_id:
            blockers.append(f"manifest branch {branch_id} has work item without packet_id")
            continue
        role = worker_role(item)
        artifact_path = (
            bundle_dir / "research" / packet_id / "research.json"
            if role == "research-worker"
            else bundle_dir / "workers" / packet_id / "status.json"
        )
        if not artifact_path.exists():
            blockers.append(f"missing {role} artifact for {packet_id}: {artifact_path}")
            continue
        status = read_object_or_blocker(artifact_path, blockers, f"{role} artifact for {packet_id}")
        if status is None:
            continue
        if role == "worker":
            repair_blockers = promote_worker_with_repair_evidence(
                manifest_path=manifest_path,
                bundle_dir=bundle_dir,
                branch=branch,
                branch_id=branch_id,
                packet_id=packet_id,
                status=status,
                worktree=worktree,
            )
            if repair_blockers:
                blockers.extend(repair_blockers)
            reread = read_object_or_blocker(artifact_path, blockers, f"{role} artifact for {packet_id} after repair")
            if reread is None:
                continue
            status = reread
        if role == "worker":
            status.setdefault("role", "worker")
        status["status_path"] = artifact_path.resolve().as_posix()
        statuses.append(status)
        if status.get("status") != "pass":
            blockers.append(f"{packet_id} finished {status.get('status')!r}, not pass")
    return statuses, blockers


def scheduler_rollup(manifest_path: Path, branch: dict, branch_id: str) -> tuple[dict, list[str]]:
    bundle_dir = manifest_path.parent
    expected_path = CONTRACT.worker_scheduler_path(branch_id)
    expected_ids = BRANCH_VALIDATOR.expected_worker_packet_ids([], branch, branch_id)
    dependencies = BRANCH_VALIDATOR.expected_worker_dependencies(branch, branch_id)
    max_active = branch.get("max_active_worker_packets")
    capacity = (
        max_active
        if isinstance(max_active, int) and not isinstance(max_active, bool)
        else CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
    )
    defects: list[str] = []
    scheduler_file = bundle_dir / expected_path
    summary = STATUS_VALIDATION.validate_scheduler_artifact(
        defects,
        scheduler_file,
        "$.worker_parallelism.scheduler_path",
        scheduler_kind="branch-worker-pool",
        expected_path=expected_path,
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=capacity,
        manifest_path=manifest_path,
        require_all_launched=False,
    )
    refill_events = []
    scheduler_serial_reasons: list[str] = []
    if scheduler_file.exists():
        try:
            scheduler_data = read_json(scheduler_file)
            for event in scheduler_data.get("events", []):
                if isinstance(event, dict) and event.get("event") == "refill":
                    seq = event.get("seq")
                    eligible = event.get("eligible_ids", [])
                    suffix = (
                        ",".join(item for item in eligible if isinstance(item, str))
                        if isinstance(eligible, list)
                        else ""
                    )
                    refill_events.append(f"seq:{seq}:{suffix}" if isinstance(seq, int) else suffix)
                if isinstance(event, dict) and event.get("event") in {"defer", "under_capacity", "blocked"}:
                    ids: list[str] = []
                    event_id = event.get("id")
                    if isinstance(event_id, str):
                        ids = [event_id]
                    eligible_ids = event.get("eligible_ids")
                    if isinstance(eligible_ids, list):
                        ids = [item for item in eligible_ids if isinstance(item, str)]
                    reason = event.get("reason")
                    reason_code = event.get("reason_code")
                    detail = reason if isinstance(reason, str) and reason.strip() else reason_code
                    if ids and isinstance(detail, str) and detail.strip():
                        scheduler_serial_reasons.append(
                            f"scheduler {event.get('event')} for {','.join(ids)}: {detail.strip()}"
                        )
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - read_json raises SystemExit on non-dict
            defects.append(f"could not read scheduler refill events: {exc}")
    manifest_serial_reasons = (
        branch.get("worker_parallelism", {}).get("serial_reasons", [])
        if isinstance(branch.get("worker_parallelism"), dict)
        else branch.get("worker_serial_reasons", [])
    )
    serial_reasons: list[str] = []
    for reason in [*manifest_serial_reasons, *scheduler_serial_reasons]:
        if isinstance(reason, str) and reason.strip() and reason not in serial_reasons:
            serial_reasons.append(reason)
    worker_parallelism = {
        "scheduler_path": expected_path,
        "max_worker_packets_per_branch": CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH,
        "max_active_worker_packets": capacity,
        "max_observed_active_worker_packets": summary.get("max_observed_active", 0),
        "max_observed_active": summary.get("max_observed_active", 0),
        "concurrent_launch_default": True,
        "rolling_refill_default": True,
        "scheduling_mode": "rolling",
        "launched_ids": summary.get("launched", []),
        "finished_ids": summary.get("finished", []),
        "active_ids": summary.get("active", []),
        "blocked_ids": summary.get("blocked", []),
        "deferred_ids": summary.get("deferred", []),
        "serialized_workers": [],
        "deferred_workers": summary.get("deferred", []),
        "serial_reasons": serial_reasons,
        "refill_events": refill_events,
    }
    return worker_parallelism, defects


def collect_lite_advice(bundle_dir: Path, branch_id: str) -> list[dict]:
    lite_dir = bundle_dir / "lite"
    if not lite_dir.is_dir():
        return []
    records = []
    for packet_dir in sorted(
        path for path in lite_dir.iterdir() if path.is_dir() and path.name.startswith(f"{branch_id}-L")
    ):
        advice_path = packet_dir / "advice.json"
        inputs_path = packet_dir / "input-files.json"
        if not advice_path.exists() or not inputs_path.exists():
            continue
        try:
            inputs = read_json(inputs_path)
            advice = read_json(advice_path)
        except SystemExit:
            continue  # advisory Lite packet with a malformed artifact is ignored, not fatal
        command = STATUS_VALIDATION.lite_validation_command(SCRIPT_DIR, advice_path.resolve(), inputs_path.resolve())
        result = subprocess.run(
            shlex.split(command),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        defects = [line.removeprefix("- ").strip() for line in result.stdout.splitlines() if line.startswith("- ")]
        source_files = (
            [
                {
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                    "size_bytes": item.get("size_bytes"),
                    "reason": item.get("reason"),
                }
                for item in inputs.get("source_files", [])
                if isinstance(item, dict)
            ]
            if isinstance(inputs.get("source_files"), list)
            else []
        )
        records.append(
            {
                "packet_id": packet_dir.name,
                "purpose": advice.get("purpose", inputs.get("purpose")),
                "avoids_action": advice.get("avoids_action", inputs.get("avoids_action")),
                "expected_savings_reason": advice.get("expected_savings_reason", inputs.get("expected_savings_reason")),
                "status": advice.get("status", "blocked"),
                "disposition": "ignored",
                "advice_path": advice_path.resolve().as_posix(),
                "inputs_path": inputs_path.resolve().as_posix(),
                "source_files": source_files,
                "validation_command": command,
                "validation_status": "pass" if result.returncode == 0 else "failed",
                "validation_defects": defects,
                "reason": "Assembled branch status records Lite advice as advisory context only.",
            }
        )
    return records


def changed_files_from_git(worktree: Path, base_ref: str, blockers: list[str] | None = None) -> list[str]:
    result = run_git(["diff", "--name-only", f"{base_ref}...HEAD"], cwd=worktree)
    if result.returncode != 0:
        if blockers is not None:
            blockers.append(f"git diff --name-only {base_ref}...HEAD failed: {result.stdout.strip()}")
        return []
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and is_repo_relative_path(line.strip(), reject_porcelain=True)
    ]


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
    result = run_git(["ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if result.returncode != 0:
        return [f"untracked file scan failed: {result.stdout.strip()}"]
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


def append_unique(target: list[str], value: object) -> None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped not in target:
            target.append(stripped)


def collect_worker_tests(worker_statuses: list[dict]) -> list[str]:
    tests: list[str] = []
    for status in worker_statuses:
        values = status.get("tests")
        if isinstance(values, list):
            for value in values:
                append_unique(tests, value)
    return tests


def collect_manifest_dod(branch: dict) -> list[str]:
    items: list[str] = []
    dod = branch.get("dod")
    if isinstance(dod, list):
        for value in dod:
            append_unique(items, value)
    else:
        append_unique(items, dod)
    work_items = branch.get("work_items")
    if isinstance(work_items, list):
        for work_item in work_items:
            if not isinstance(work_item, dict):
                continue
            dod_values = work_item.get("dod")
            if isinstance(dod_values, list):
                for value in dod_values:
                    append_unique(items, value)
    return items


def current_pre_review_gate(
    bundle_dir: Path, branch_id: str
) -> tuple[dict | None, dict[str, str], str | None, list[str]]:
    gate_path = bundle_dir / CONTRACT.pre_review_gate_path(branch_id)
    if not gate_path.exists():
        return None, {}, None, [f"current pre-review gate is missing: {gate_path}"]
    try:
        gate = read_json(gate_path)
    except SystemExit as exc:
        return None, {}, None, [f"current pre-review gate is not a readable JSON object: {exc}"]
    if gate.get("status") != "pass":
        return gate, {}, None, [f"current pre-review gate is not pass: {gate_path}"]
    gate_hashes = gate.get("semantic_input_hashes")
    if not isinstance(gate_hashes, dict):
        return gate, {}, None, [f"current pre-review gate lacks semantic_input_hashes: {gate_path}"]
    expected_hashes = {
        key: value for key, value in gate_hashes.items() if isinstance(key, str) and isinstance(value, str)
    }
    expected_packet_id = gate.get("review_packet_id") if isinstance(gate.get("review_packet_id"), str) else None
    return gate, expected_hashes, expected_packet_id, []


def review_matches_gate(
    data: dict, branch_id: str, expected_hashes: dict[str, str], expected_packet_id: str | None
) -> bool:
    packet_id = data.get("packet_id")
    if not isinstance(packet_id, str) or not packet_id.startswith(f"{branch_id}-R"):
        return False
    if expected_packet_id is not None and packet_id != expected_packet_id:
        return False
    if data.get("role") != "reviewer":
        return False
    candidate_hashes = data.get("semantic_input_hashes")
    current_hashes = (
        {key: value for key, value in candidate_hashes.items() if isinstance(key, str) and isinstance(value, str)}
        if isinstance(candidate_hashes, dict)
        else {}
    )
    return current_hashes == expected_hashes


def current_reviewer_candidates(
    bundle_dir: Path,
    branch_id: str,
    expected_hashes: dict[str, str],
    expected_packet_id: str | None,
) -> tuple[list[Path], list[str]]:
    reviewers_dir = bundle_dir / "reviewers"
    if not reviewers_dir.is_dir():
        return [], ["reviewers directory is missing"]
    candidates: list[Path] = []
    defects: list[str] = []
    for candidate in sorted(reviewers_dir.glob(f"{branch_id}-R*/review.json")):
        try:
            data = read_json(candidate)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - read_json raises SystemExit on non-dict
            defects.append(f"candidate reviewer artifact is invalid JSON: {candidate}: {exc}")
            continue
        packet_id = data.get("packet_id")
        if isinstance(packet_id, str) and packet_id.startswith(f"{branch_id}-R") and data.get("role") == "reviewer":
            if not review_matches_gate(data, branch_id, expected_hashes, expected_packet_id):
                defects.append(f"candidate reviewer artifact is stale for current pre-review gate: {candidate}")
                continue
            candidates.append(candidate)
    return candidates, defects


def promote_reviewer_output(bundle_dir: Path, review_path: Path, branch_id: str) -> list[str]:
    _gate, expected_hashes, expected_packet_id, gate_defects = current_pre_review_gate(bundle_dir, branch_id)
    if gate_defects:
        return [f"review artifact is not current and {defect}" for defect in gate_defects]
    candidates, defects = current_reviewer_candidates(bundle_dir, branch_id, expected_hashes, expected_packet_id)
    if len(candidates) != 1:
        if candidates:
            defects.append(
                "review artifact is missing and reviewer promotion is ambiguous: "
                + ", ".join(path.as_posix() for path in candidates)
            )
        else:
            defects.append(f"review artifact is missing: {review_path}")
        return defects
    review_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidates[0], review_path)
    return []


def promote_current_reviewer_if_newer(bundle_dir: Path, review_path: Path, branch_id: str) -> list[str]:
    _gate, expected_hashes, expected_packet_id, gate_defects = current_pre_review_gate(bundle_dir, branch_id)
    if gate_defects:
        return [f"review artifact is not current and {defect}" for defect in gate_defects]
    candidates, defects = current_reviewer_candidates(bundle_dir, branch_id, expected_hashes, expected_packet_id)
    if len(candidates) != 1:
        return defects if candidates else []
    candidate = candidates[0]
    if review_path.exists() and sha256_file(review_path) == sha256_file(candidate):
        return []
    review_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, review_path)
    return []


def review_status(bundle_dir: Path, branch: dict, branch_id: str) -> tuple[str, list[str]]:
    review_path = safe_bundle_path(bundle_dir, branch.get("review_path"), "manifest branch review_path")
    gate_path = bundle_dir / CONTRACT.pre_review_gate_path(branch_id)
    if not review_path.exists() and not gate_path.exists():
        return "missing", []
    if not review_path.exists():
        promotion_defects = promote_reviewer_output(bundle_dir, review_path, branch_id)
        if promotion_defects:
            return "missing", promotion_defects
    _gate, expected_hashes, expected_packet_id, gate_defects = current_pre_review_gate(bundle_dir, branch_id)
    if gate_defects:
        return "missing", gate_defects
    promotion_defects = promote_current_reviewer_if_newer(bundle_dir, review_path, branch_id)
    if promotion_defects and not review_path.exists():
        return "missing", promotion_defects
    try:
        review = read_json(review_path)
    except SystemExit as exc:
        return "missing", [f"review artifact is not a readable JSON object: {exc}"]
    if not review_matches_gate(review, branch_id, expected_hashes, expected_packet_id):
        promotion_defects = promote_reviewer_output(bundle_dir, review_path, branch_id)
        if promotion_defects:
            return "missing", promotion_defects
        try:
            review = read_json(review_path)
        except SystemExit as exc:
            return "missing", [f"review artifact is not a readable JSON object: {exc}"]
    verdict = review.get("verdict")
    verification_gaps = review.get("verification_gaps")
    if verdict not in CONTRACT.REVIEW_STATUSES:
        return "missing", [f"review artifact has invalid verdict: {verdict!r}"]
    if verdict == "mergeable" and verification_gaps:
        return "missing", ["review artifact has non-empty verification_gaps with mergeable verdict"]
    if verdict != "mergeable":
        return str(verdict), [f"review verdict is {verdict!r}, not mergeable"]
    return "mergeable", []


def review_waiver_rel_path(branch_id: str) -> str:
    return f"branches/{branch_id}.review-waiver.json"


def write_review_waiver(
    bundle_dir: Path,
    branch: dict,
    branch_id: str,
    branch_status: dict,
) -> None:
    rel_path = branch_status.get("review_waiver_path")
    if not isinstance(rel_path, str) or not rel_path.strip():
        return
    review_path = branch.get("review_path")
    review_artifact_exists = False
    if isinstance(review_path, str) and review_path.strip():
        try:
            review_artifact_exists = safe_bundle_path(bundle_dir, review_path, "manifest branch review_path").exists()
        except (ValueError, SystemExit):  # safe_bundle_path raises SystemExit on an unsafe path
            review_artifact_exists = False
    waiver = {
        "schema_version": 1,
        "kind": "review-waiver",
        "branch_id": branch_id,
        "branch_status": branch_status.get("status"),
        "review_status": branch_status.get("review_status"),
        "review_path": review_path if isinstance(review_path, str) else "",
        "reviewer_launch_skipped": not review_artifact_exists,
        "review_artifact_rejected": review_artifact_exists,
        "reason_code": "review_artifact_not_accepted" if review_artifact_exists else "branch_non_pass_terminal_blocker",
        "reason": (
            "Reviewer artifact exists but was not accepted as mergeable evidence; repair or rerun review after the blockers are cleared."
            if review_artifact_exists
            else "Branch is non-pass with terminal blocker evidence; launch a reviewer only after repair produces pass-ready evidence."
        ),
        "validated_by": "assemble_branch_status.py",
        "blockers": branch_status.get("blockers", []),
        "branch_status_path": branch.get("status_path"),
    }
    write_json(bundle_dir / rel_path, waiver)


def assemble(args: argparse.Namespace) -> tuple[Path, dict, list[str]]:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    bundle_dir = manifest_path.parent
    manifest = read_json(manifest_path)
    branch_id = require_safe_label(args.branch_id, "--branch-id")
    branch = branch_entry(manifest, branch_id)
    branch_name = branch.get("branch_name")
    if not isinstance(branch_name, str) or not branch_name.strip():
        raise SystemExit(f"manifest branch {branch_id} is missing branch_name")
    output_path = (
        resolve_absolute_path(args.output, "--output", must_exist=False)
        if args.output
        else safe_bundle_path(bundle_dir, branch.get("status_path"), "manifest branch status_path")
    )
    if output_path.exists() and not args.replace:
        raise SystemExit(f"branch status already exists; pass --replace to recreate: {output_path}")

    worker_statuses, worker_blockers = collect_worker_statuses(
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        branch=branch,
        branch_id=branch_id,
        worktree=worktree,
    )
    worker_parallelism, scheduler_defects = scheduler_rollup(manifest_path, branch, branch_id)
    inferred_review_status, review_blockers = review_status(bundle_dir, branch, branch_id)
    base_ref = validate_base_ref(str(manifest.get("base_ref", "main")))
    diff_commands = [
        (f"git diff --check {base_ref}...HEAD", ["diff", "--check", f"{base_ref}...HEAD"]),
        ("git diff --check HEAD", ["diff", "--check", "HEAD"]),
        ("git diff --cached --check HEAD", ["diff", "--cached", "--check", "HEAD"]),
    ]
    diff_results = [(label, run_git(argv, cwd=worktree)) for label, argv in diff_commands]
    untracked_check_command = "git ls-files --others --exclude-standard + internal untracked trailing-whitespace scan"
    untracked_defects = untracked_whitespace_defects(worktree)
    blockers = list(args.blocker)
    blockers.extend(worker_blockers)
    blockers.extend(scheduler_defects)
    blockers.extend(review_blockers)
    changed_files = (
        list(args.changed_file) if args.changed_file else changed_files_from_git(worktree, base_ref, blockers)
    )
    dod_items = [item for item in args.dod_item if item.strip()]
    if not dod_items:
        dod_items = collect_manifest_dod(branch)
    for command, diff_result in diff_results:
        if diff_result.returncode != 0:
            blockers.append(f"{command} failed: {diff_result.stdout.strip()}")
    blockers.extend(untracked_defects)
    if not dod_items:
        blockers.append("DoD checklist is missing")

    all_workers_pass = bool(worker_statuses) and not worker_blockers
    diff_checks_pass = all(result.returncode == 0 for _command, result in diff_results) and not untracked_defects
    can_pass = (
        args.allow_pass
        and all_workers_pass
        and inferred_review_status == "mergeable"
        and diff_checks_pass
        and not blockers
    )
    if args.status:
        status = args.status
    elif can_pass:
        status = "pass"
    elif worker_statuses:
        status = "partial"
    else:
        status = "blocked"
    if status == "pass" and inferred_review_status == "missing":
        status = "partial"
    if status != "pass" and not blockers:
        blockers.append("Branch status assembled conservatively without explicit pass evidence.")
    tests = list(args.test_evidence) if args.test_evidence else collect_worker_tests(worker_statuses)
    commands_run: list[str] = []
    for value in args.command_run:
        append_unique(commands_run, value)
    for value in tests:
        append_unique(commands_run, value)
    for command, _result in diff_results:
        append_unique(commands_run, command)
    append_unique(commands_run, untracked_check_command)
    branch_status = {
        "branch_id": branch_id,
        "status": status,
        "branch": branch_name,
        "worktree": worktree.as_posix(),
        "worker_statuses": worker_statuses,
        "worker_parallelism": worker_parallelism,
        "lite_advice": collect_lite_advice(bundle_dir, branch_id),
        "review_status": inferred_review_status,
        "changed_files": changed_files,
        "commands_run": commands_run,
        "tests": tests,
        "dod_checklist": dod_items,
        "blockers": blockers if status != "pass" else [],
        "handoff": args.handoff or "Branch status assembled from manifest-owned runtime artifacts.",
    }
    if status != "pass" and inferred_review_status == "missing":
        branch_status["review_waiver_path"] = review_waiver_rel_path(branch_id)
        write_review_waiver(bundle_dir, branch, branch_id, branch_status)
    annotate_status_lanes(branch_status, [])
    write_json(output_path, branch_status)

    validation_defects = BRANCH_VALIDATOR.validate_branch_status(
        branch_status,
        branch_id=branch_id,
        branch=branch_name,
        worktree=worktree.as_posix(),
        manifest=manifest,
        manifest_path=manifest_path,
        status_path=output_path,
    )
    if validation_defects and branch_status.get("status") == "pass":
        downgraded_blockers: list[str] = []
        for value in validation_defects:
            append_unique(downgraded_blockers, value)
        branch_status["status"] = "blocked"
        branch_status["blockers"] = downgraded_blockers
        branch_status["handoff"] = (
            "Branch status validation failed; pass artifact was downgraded to blocked with validator defects preserved."
        )
        if branch_status.get("review_status") == "missing":
            branch_status["review_waiver_path"] = review_waiver_rel_path(branch_id)
            write_review_waiver(bundle_dir, branch, branch_id, branch_status)
        write_json(output_path, branch_status)
        validation_defects = BRANCH_VALIDATOR.validate_branch_status(
            branch_status,
            branch_id=branch_id,
            branch=branch_name,
            worktree=worktree.as_posix(),
            manifest=manifest,
            manifest_path=manifest_path,
            status_path=output_path,
        )
    annotate_status_lanes(branch_status, validation_defects)
    write_json(output_path, branch_status)
    return output_path, branch_status, validation_defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--output")
    parser.add_argument("--status", choices=list(CONTRACT.STATUSES))
    parser.add_argument("--allow-pass", action="store_true")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--command-run", action="append", default=[])
    parser.add_argument("--test-evidence", action="append", default=[])
    parser.add_argument("--dod-item", action="append", default=[])
    parser.add_argument("--blocker", action="append", default=[])
    parser.add_argument("--handoff")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    output_path, status, defects = assemble(args)
    result = {
        "status": "pass" if not defects else "failed",
        "status_path": output_path.as_posix(),
        "branch_status": status.get("status"),
        "review_status": status.get("review_status"),
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
