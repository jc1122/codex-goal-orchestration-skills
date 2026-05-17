#!/usr/bin/env python3
"""Validate a goal branch-orchestrator status artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
from pathlib import Path


STATUSES = {"pass", "partial", "blocked", "failed"}
REVIEW_STATUSES = {"mergeable", "mergeable_after_fixes", "blocked", "reject", "missing"}
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}
BRANCH_LITE_PURPOSES = {"branch-packet-planning", "context-pack", "worker-summary", "blocked-triage"}
MAX_WORKER_PACKETS_PER_BRANCH = 4
PORCELAIN_PREFIX_RE = re.compile(r"^[ MADRCU?!]{2} ")
SAFE_REVIEW_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def resolve_absolute_path(value: str, field: str, *, must_exist: bool) -> Path:
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators: {value!r}")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise SystemExit(f"{field} must be an absolute path: {value!r}")
    if ".." in expanded.parts:
        raise SystemExit(f"{field} must not contain '..' traversal: {value!r}")
    if must_exist and not expanded.exists():
        raise SystemExit(f"{field} does not exist: {expanded}")
    return expanded.resolve(strict=must_exist)


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_artifact(defects: list[str], path: Path, field: str) -> object:
    try:
        return load_json(path)
    except Exception as exc:  # noqa: BLE001
        defect(defects, field, f"must be readable JSON at {path}: {exc}")
        return {}


def defect(defects: list[str], path: str, message: str) -> None:
    defects.append(f"{path}: {message}")


def require_object(defects: list[str], value: object, path: str) -> dict:
    if not isinstance(value, dict):
        defect(defects, path, "must be an object")
        return {}
    return value


def require_string(defects: list[str], value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        defect(defects, path, "must be a non-empty string")
        return ""
    return value


def require_string_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            defect(defects, f"{path}[{index}]", "must be a non-empty string")
        else:
            result.append(item)
    if len(result) < min_items:
        defect(defects, path, f"must contain at least {min_items} item(s)")
    return result


def is_repo_relative_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or value.startswith("/")
        or value.startswith("./")
        or value == "."
        or "/./" in value
        or value.endswith("/.")
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or PORCELAIN_PREFIX_RE.match(value) is not None
    )


def is_absolute_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or not path.is_absolute()
        or ".." in path.parts
    )


def validate_path_list(defects: list[str], value: object, path: str) -> None:
    for index, item in enumerate(require_string_list(defects, value, path)):
        if not is_repo_relative_path(item):
            defect(defects, f"{path}[{index}]", "must be a repo-relative path without git porcelain status")


def validate_lite_source_files(defects: list[str], value: object, path: str) -> list[dict]:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return []
    result = []
    seen = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        source_path = require_string(defects, data.get("path"), f"{item_path}.path")
        sha256 = require_string(defects, data.get("sha256"), f"{item_path}.sha256")
        size_bytes = data.get("size_bytes")
        require_string(defects, data.get("reason"), f"{item_path}.reason")
        if source_path and not is_repo_relative_path(source_path):
            defect(defects, f"{item_path}.path", "must be relative without traversal")
        if source_path in seen:
            defect(defects, f"{item_path}.path", f"duplicates source file {source_path!r}")
        seen.add(source_path)
        if sha256 and not SHA256_RE.fullmatch(sha256):
            defect(defects, f"{item_path}.sha256", "must be sha256:<64 lowercase hex chars>")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            defect(defects, f"{item_path}.size_bytes", "must be a non-negative integer")
        result.append({"path": source_path, "sha256": sha256, "size_bytes": size_bytes, "reason": data.get("reason")})
    return result


def load_lite_validator(defects: list[str]):
    path = Path(__file__).resolve().parent / "validate_lite_advice.py"
    if not path.exists():
        defect(defects, "$.lite_advice", f"missing Lite advice validator: {path}")
        return None
    spec = importlib.util.spec_from_file_location("goal_branch_validate_lite_advice", path)
    if spec is None or spec.loader is None:
        defect(defects, "$.lite_advice", f"could not load Lite advice validator: {path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        defect(defects, "$.lite_advice", f"could not import Lite advice validator {path}: {exc}")
        return None
    return module


def lite_validation_command(advice_path: Path, inputs_path: Path) -> str:
    validator_path = Path(__file__).resolve().parent / "validate_lite_advice.py"
    return shlex.join([
        "python3",
        validator_path.as_posix(),
        "--advice",
        advice_path.as_posix(),
        "--inputs",
        inputs_path.as_posix(),
    ])


def discover_unrecorded_lite_packets(
    defects: list[str],
    path: str,
    *,
    manifest_path: Path,
    reported_ids: set[str],
    branch_id: str | None,
) -> None:
    lite_root = manifest_path.parent / "lite"
    if not lite_root.is_dir():
        return
    branch_prefix = f"{branch_id}-L" if isinstance(branch_id, str) and branch_id.strip() else ""
    for packet_dir in sorted(item for item in lite_root.iterdir() if item.is_dir()):
        inputs_path = packet_dir / "input-files.json"
        advice_path = packet_dir / "advice.json"
        inputs_data: object = {}
        if inputs_path.exists():
            inputs_data = load_json_artifact(defects, inputs_path, f"{path}.{packet_dir.name}.inputs_path")
        elif advice_path.exists() and branch_prefix and packet_dir.name.startswith(branch_prefix):
            defect(defects, path, f"unrecorded malformed branch Lite packet without input-files.json: {packet_dir}")
            continue
        if not isinstance(inputs_data, dict):
            continue
        purpose = inputs_data.get("purpose")
        skill = inputs_data.get("skill")
        input_packet_id = inputs_data.get("packet_id")
        packet_id = input_packet_id if isinstance(input_packet_id, str) and input_packet_id.strip() else packet_dir.name
        branch_scoped = bool(branch_prefix) and (
            packet_dir.name.startswith(branch_prefix)
            or packet_id.startswith(branch_prefix)
        )
        branch_lite = (
            purpose in BRANCH_LITE_PURPOSES
            or skill == "goal-branch-orchestrator"
            or branch_scoped
        )
        if branch_lite and not branch_scoped:
            defect(defects, path, f"branch Lite packet is not scoped to {branch_prefix}: {packet_id} at {packet_dir}")
            continue
        if branch_lite and packet_id not in reported_ids:
            defect(defects, path, f"unrecorded manifest-owned branch Lite packet: {packet_id} at {packet_dir}")


def validate_lite_advice_entries(
    defects: list[str],
    value: object,
    path: str,
    *,
    manifest_path: Path,
    branch_id: str | None,
) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    lite_validator = None
    seen = set()
    reported_ids: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        required = [
            "packet_id",
            "purpose",
            "status",
            "disposition",
            "advice_path",
            "inputs_path",
            "source_files",
            "validation_command",
            "validation_status",
            "validation_defects",
            "reason",
        ]
        for key in required:
            if key not in data:
                defect(defects, item_path, f"missing key: {key}")
        packet_id = require_string(defects, data.get("packet_id"), f"{item_path}.packet_id")
        if packet_id and not SAFE_REVIEW_PACKET_RE.fullmatch(packet_id):
            defect(defects, f"{item_path}.packet_id", "must be a safe packet id")
        if branch_id and packet_id and not packet_id.startswith(f"{branch_id}-L"):
            defect(defects, f"{item_path}.packet_id", f"must start with {branch_id}-L")
        if packet_id in seen:
            defect(defects, f"{item_path}.packet_id", f"duplicates Lite packet {packet_id!r}")
        seen.add(packet_id)
        if packet_id:
            reported_ids.add(packet_id)
        purpose = require_string(defects, data.get("purpose"), f"{item_path}.purpose")
        if purpose and purpose not in BRANCH_LITE_PURPOSES:
            defect(defects, f"{item_path}.purpose", f"must be one of {sorted(BRANCH_LITE_PURPOSES)}")
        status = data.get("status")
        if status not in LITE_STATUSES:
            defect(defects, f"{item_path}.status", f"must be one of {sorted(LITE_STATUSES)}")
        disposition = data.get("disposition")
        if disposition not in LITE_DISPOSITIONS:
            defect(defects, f"{item_path}.disposition", f"must be one of {sorted(LITE_DISPOSITIONS)}")
        if disposition == "used" and status != "ok":
            defect(defects, f"{item_path}.disposition", "may be used only when Lite status is ok")
        advice_path_value = require_string(defects, data.get("advice_path"), f"{item_path}.advice_path")
        inputs_path_value = require_string(defects, data.get("inputs_path"), f"{item_path}.inputs_path")
        if advice_path_value and not is_absolute_path(advice_path_value):
            defect(defects, f"{item_path}.advice_path", "must be an absolute path without traversal")
        if inputs_path_value and not is_absolute_path(inputs_path_value):
            defect(defects, f"{item_path}.inputs_path", "must be an absolute path without traversal")
        validation_status = data.get("validation_status")
        if validation_status not in LITE_VALIDATION_STATUSES:
            defect(defects, f"{item_path}.validation_status", f"must be one of {sorted(LITE_VALIDATION_STATUSES)}")
        validation_defects = require_string_list(defects, data.get("validation_defects"), f"{item_path}.validation_defects")
        if validation_status == "pass" and validation_defects:
            defect(defects, f"{item_path}.validation_defects", "must be empty when validation_status is pass")
        if validation_status == "failed" and not validation_defects:
            defect(defects, f"{item_path}.validation_defects", "must explain failed Lite validation")
        source_files = validate_lite_source_files(defects, data.get("source_files"), f"{item_path}.source_files")
        validation_command = require_string(defects, data.get("validation_command"), f"{item_path}.validation_command")
        require_string(defects, data.get("reason"), f"{item_path}.reason")
        if not (advice_path_value and inputs_path_value and is_absolute_path(advice_path_value) and is_absolute_path(inputs_path_value)):
            continue
        advice_path = Path(advice_path_value).resolve()
        inputs_path = Path(inputs_path_value).resolve()
        if packet_id:
            expected_dir = (manifest_path.parent / "lite" / packet_id).resolve()
            expected_advice = expected_dir / "advice.json"
            expected_inputs = expected_dir / "input-files.json"
            if advice_path != expected_advice:
                defect(defects, f"{item_path}.advice_path", f"must be manifest-owned Lite advice path: {expected_advice}")
            if inputs_path != expected_inputs:
                defect(defects, f"{item_path}.inputs_path", f"must be manifest-owned Lite inputs path: {expected_inputs}")
            expected_command = lite_validation_command(expected_advice, expected_inputs)
            if validation_command and validation_command != expected_command:
                defect(defects, f"{item_path}.validation_command", f"must be exactly: {expected_command}")
        if not advice_path.exists():
            defect(defects, f"{item_path}.advice_path", f"artifact does not exist: {advice_path}")
            continue
        if not inputs_path.exists():
            defect(defects, f"{item_path}.inputs_path", f"artifact does not exist: {inputs_path}")
            continue
        advice_data = load_json_artifact(defects, advice_path, f"{item_path}.advice_path")
        inputs_data = load_json_artifact(defects, inputs_path, f"{item_path}.inputs_path")
        if not isinstance(inputs_data, dict):
            defect(defects, f"{item_path}.inputs_path", "must be a JSON object")
            continue
        expected_sources = inputs_data.get("source_files") if isinstance(inputs_data.get("source_files"), list) else []
        expected_min = [
            {
                "path": source.get("path"),
                "sha256": source.get("sha256"),
                "size_bytes": source.get("size_bytes"),
                "reason": source.get("reason"),
            }
            for source in expected_sources
            if isinstance(source, dict)
        ]
        if source_files != expected_min:
            defect(defects, f"{item_path}.source_files", "must match input-files.json source metadata exactly")
        if lite_validator is None:
            lite_validator = load_lite_validator(defects)
        if lite_validator is not None:
            lite_defects = lite_validator.validate(
                advice_data,
                packet_id=packet_id or None,
                purpose=purpose or None,
                expected_sources=expected_sources,
                inputs=inputs_data,
                inputs_path=inputs_path,
            )
            actual_validation_status = "pass" if not lite_defects else "failed"
            if validation_status in LITE_VALIDATION_STATUSES and validation_status != actual_validation_status:
                defect(defects, f"{item_path}.validation_status", f"must match actual Lite validation status {actual_validation_status!r}")
            if validation_status == "failed" and validation_defects != lite_defects:
                defect(defects, f"{item_path}.validation_defects", "must match actual Lite validation defects exactly")
            if validation_status == "pass" and validation_defects:
                defect(defects, f"{item_path}.validation_defects", "must be empty when actual Lite validation passes")
            if disposition == "used" and lite_defects:
                defect(defects, item_path, "used Lite advice must pass validation")
            for lite_defect in lite_defects:
                if disposition == "used":
                    defect(defects, item_path, f"invalid Lite advice artifact: {lite_defect}")
    discover_unrecorded_lite_packets(
        defects,
        path,
        manifest_path=manifest_path,
        reported_ids=reported_ids,
        branch_id=branch_id,
    )


def validate_command_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> None:
    require_string_list(defects, value, path, min_items=min_items)


def contains_base_range_diff_check(commands: list[str], base_ref: str) -> bool:
    expected_range = f"{base_ref}...HEAD"
    for command in commands:
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        if not tokens or tokens[0] != "git":
            continue
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "-C":
                index += 2
                continue
            if token == "-c":
                index += 2
                continue
            if token.startswith("-c") and token != "-c":
                index += 1
                continue
            break
        if index >= len(tokens) or tokens[index] != "diff":
            continue
        args = tokens[index + 1 :]
        if "--check" in args and expected_range in args:
            return True
    return False


def validate_base_range_diff_check(defects: list[str], commands_value: object, path: str, manifest: object) -> None:
    commands = require_string_list(defects, commands_value, path, min_items=1)
    manifest_root = require_object(defects, manifest, "manifest")
    base_ref = require_string(defects, manifest_root.get("base_ref"), "manifest.base_ref")
    if base_ref and not contains_base_range_diff_check(commands, base_ref):
        defect(defects, path, f"must include base-range whitespace check: git diff --check {base_ref}...HEAD")


def validate_worker_status(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "status",
        "status_path",
        "worktree",
        "changed_files",
        "commands_run",
        "tests",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if status_path and not is_absolute_path(status_path):
        defect(defects, f"{path}.status_path", "must be an absolute path without traversal")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")


def validate_worker_artifact(defects: list[str], value: object, path: str) -> dict:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "role",
        "status",
        "branch",
        "worktree",
        "changed_files",
        "commands_run",
        "tests",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if data.get("role") != "worker":
        defect(defects, f"{path}.role", "must be 'worker'")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    require_string(defects, data.get("branch"), f"{path}.branch")
    worktree = require_string(defects, data.get("worktree"), f"{path}.worktree")
    if worktree and not is_absolute_path(worktree):
        defect(defects, f"{path}.worktree", "must be an absolute path without traversal")
    validate_path_list(defects, data.get("changed_files"), f"{path}.changed_files")
    validate_command_list(defects, data.get("commands_run"), f"{path}.commands_run", min_items=1)
    validate_command_list(defects, data.get("tests"), f"{path}.tests")
    blockers = require_string_list(defects, data.get("blockers"), f"{path}.blockers")
    if data.get("status") == "pass" and blockers:
        defect(defects, f"{path}.blockers", "must be empty when status is pass")
    if data.get("status") in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, f"{path}.blockers", "must explain non-pass status")
    require_string(defects, data.get("handoff"), f"{path}.handoff")
    return data


def validate_worker_artifacts(
    defects: list[str],
    worker_statuses: object,
    branch_status: object,
    *,
    branch: object,
    manifest_path: Path,
) -> None:
    if not isinstance(worker_statuses, list):
        return
    require_existing = branch_status in {"pass", "partial"}
    compared_keys = ["packet_id", "status", "worktree", "changed_files", "commands_run", "tests", "blockers", "handoff"]
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        item_path = f"$.worker_statuses[{index}]"
        status_path_value = item.get("status_path")
        if not isinstance(status_path_value, str) or not status_path_value.strip() or not is_absolute_path(status_path_value):
            continue
        status_artifact = Path(status_path_value).resolve()
        packet_id = item.get("packet_id")
        if isinstance(packet_id, str) and packet_id.strip():
            expected_status_artifact = (manifest_path.parent / "workers" / packet_id / "status.json").resolve()
            if status_artifact != expected_status_artifact:
                defect(
                    defects,
                    f"{item_path}.status_path",
                    f"must be manifest-owned worker status path: {expected_status_artifact}",
                )
        if not status_artifact.exists():
            if require_existing:
                defect(defects, f"{item_path}.status_path", f"artifact does not exist: {status_artifact}")
            continue
        artifact = validate_worker_artifact(
            defects,
            load_json_artifact(defects, status_artifact, f"{item_path}.status_path"),
            f"{item_path}.status_path",
        )
        if isinstance(branch, str) and branch.strip() and artifact.get("branch") != branch:
            defect(defects, f"{item_path}.status_path.branch", "must match branch status branch")
        for key in compared_keys:
            if artifact.get(key) != item.get(key):
                defect(defects, f"{item_path}.{key}", "must match worker status artifact")


def manifest_branch_entry(defects: list[str], manifest: object, branch_id: str) -> dict:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    if not isinstance(branches, list) or not branches:
        defect(defects, "manifest.branches", "must be a non-empty array")
        return {}
    matches = []
    for index, item in enumerate(branches):
        if not isinstance(item, dict):
            defect(defects, f"manifest.branches[{index}]", "must be an object")
            continue
        item_id = item.get("id")
        if item_id == branch_id:
            matches.append((index, item))
    if not matches:
        defect(defects, "$.branch_id", f"must be declared in manifest: {branch_id!r}")
        return {}
    if len(matches) > 1:
        defect(defects, "manifest.branches", f"must not duplicate branch id {branch_id!r}")
    return matches[0][1]


def expected_worker_packet_ids(defects: list[str], branch_entry: dict, branch_id: str) -> list[str]:
    work_items = branch_entry.get("work_items")
    if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "manifest.branch.work_items", "must contain 1 to 4 work item objects")
        return []
    packet_ids = []
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            defect(defects, f"manifest.branch.work_items[{index}]", "must be an object")
            continue
        item_id = require_string(defects, item.get("id"), f"manifest.branch.work_items[{index}].id")
        packet_id = require_string(defects, item.get("packet_id"), f"manifest.branch.work_items[{index}].packet_id")
        if item_id and packet_id:
            expected_packet_id = f"{branch_id}-{item_id}"
            if packet_id != expected_packet_id:
                defect(defects, f"manifest.branch.work_items[{index}].packet_id", f"must be {expected_packet_id!r}")
            packet_ids.append(packet_id)
    if len(packet_ids) != len(set(packet_ids)):
        defect(defects, "manifest.branch.work_items", "packet_id values must be unique")
    return packet_ids


def validate_manifest_branch_contract(
    defects: list[str],
    root: dict,
    manifest: object,
    *,
    manifest_path: Path,
    status_path: Path,
    require_all_workers: bool,
) -> dict:
    branch_id = root.get("branch_id")
    if not isinstance(branch_id, str) or not branch_id.strip():
        return {}
    branch_entry = manifest_branch_entry(defects, manifest, branch_id)
    if not branch_entry:
        return {}

    manifest_branch_name = branch_entry.get("branch_name")
    if isinstance(manifest_branch_name, str) and manifest_branch_name.strip() and root.get("branch") != manifest_branch_name:
        defect(defects, "$.branch", f"must match manifest branch_name {manifest_branch_name!r}")

    manifest_status_path = branch_entry.get("status_path")
    if isinstance(manifest_status_path, str) and manifest_status_path.strip():
        expected_status_path = (manifest_path.parent / manifest_status_path).resolve().as_posix()
        if status_path.as_posix() != expected_status_path:
            defect(defects, "--status", "must match manifest branch status_path")

    worker_parallelism = root.get("worker_parallelism")
    manifest_max_workers = branch_entry.get("max_active_worker_packets")
    if not is_strict_int(manifest_max_workers) or manifest_max_workers < 1 or manifest_max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "manifest.branch.max_active_worker_packets", "must be an integer from 1 to 4")
    if (
        isinstance(worker_parallelism, dict)
        and is_strict_int(manifest_max_workers)
        and worker_parallelism.get("max_active_worker_packets") != manifest_max_workers
    ):
        defect(defects, "$.worker_parallelism.max_active_worker_packets", "must match manifest max_active_worker_packets")

    expected_ids = set(expected_worker_packet_ids(defects, branch_entry, branch_id))
    worker_statuses = root.get("worker_statuses")
    if not isinstance(worker_statuses, list) or not expected_ids:
        return branch_entry
    seen_ids = set()
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        packet_id = item.get("packet_id")
        if not isinstance(packet_id, str) or not packet_id.strip():
            continue
        if packet_id in seen_ids:
            defect(defects, f"$.worker_statuses[{index}].packet_id", f"duplicates worker packet {packet_id!r}")
            continue
        seen_ids.add(packet_id)
        if packet_id not in expected_ids:
            defect(defects, f"$.worker_statuses[{index}].packet_id", "is not declared by manifest work_items")
    if require_all_workers:
        missing = sorted(expected_ids - seen_ids)
        extra = sorted(seen_ids - expected_ids)
        if missing:
            defect(defects, "$.worker_statuses", f"missing manifest worker packet statuses: {', '.join(missing)}")
        if extra:
            defect(defects, "$.worker_statuses", f"contains worker packet statuses not declared in manifest: {', '.join(extra)}")
    return branch_entry


def validate_worker_parallelism(defects: list[str], value: object, path: str, *, worker_count: int) -> None:
    data = require_object(defects, value, path)
    required = [
        "max_worker_packets_per_branch",
        "max_active_worker_packets",
        "max_observed_active_worker_packets",
        "concurrent_launch_default",
        "serialized_workers",
        "serial_reasons",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    if data.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_worker_packets_per_branch", "must be 4")
    max_active = data.get("max_active_worker_packets")
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_active_worker_packets", "must be an integer from 1 to 4")
    observed = data.get("max_observed_active_worker_packets")
    if not is_strict_int(observed) or observed < 0 or observed > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must be an integer from 0 to 4")
    if is_strict_int(max_active) and is_strict_int(observed) and observed > max_active:
        defect(defects, f"{path}.max_observed_active_worker_packets", "must not exceed max_active_worker_packets")
    if data.get("concurrent_launch_default") is not True:
        defect(defects, f"{path}.concurrent_launch_default", "must be true")
    serialized_workers = require_string_list(defects, data.get("serialized_workers"), f"{path}.serialized_workers")
    serial_reasons = require_string_list(defects, data.get("serial_reasons"), f"{path}.serial_reasons")
    if serialized_workers and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify serialized workers")
    if is_strict_int(max_active) and max_active < MAX_WORKER_PACKETS_PER_BRANCH and not serial_reasons:
        defect(defects, f"{path}.serial_reasons", "must justify max_active_worker_packets below 4")
    if (
        worker_count > 1
        and is_strict_int(max_active)
        and is_strict_int(observed)
        and observed < min(max_active, worker_count)
        and not serial_reasons
    ):
        defect(defects, f"{path}.serial_reasons", "must justify observed worker parallelism below available worker concurrency")


def validate_worker_rollup(defects: list[str], worker_statuses: object, branch_status: object) -> None:
    if not isinstance(worker_statuses, list):
        return
    if branch_status != "pass":
        return
    for index, item in enumerate(worker_statuses):
        if not isinstance(item, dict):
            continue
        worker_status = item.get("status")
        if worker_status != "pass":
            defect(defects, f"$.worker_statuses[{index}].status", "must be pass when branch status is pass")


def validate_review_artifact(
    defects: list[str],
    value: object,
    expected_verdict: str,
    path: str,
    *,
    manifest: object,
    branch_id: str | None,
) -> None:
    data = require_object(defects, value, path)
    required = [
        "packet_id",
        "role",
        "verdict",
        "findings",
        "commands_run",
        "verification_gaps",
        "residual_risks",
        "summary",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    packet_id = require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if packet_id and not SAFE_REVIEW_PACKET_RE.fullmatch(packet_id):
        defect(defects, f"{path}.packet_id", "must be a safe packet id")
    if branch_id and packet_id and not packet_id.startswith(f"{branch_id}-R"):
        defect(defects, f"{path}.packet_id", f"must start with {branch_id}-R")
    if data.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    verdict = data.get("verdict")
    if verdict not in REVIEW_STATUSES - {"missing"}:
        defect(defects, f"{path}.verdict", f"must be one of {sorted(REVIEW_STATUSES - {'missing'})}")
    if expected_verdict != "missing" and verdict != expected_verdict:
        defect(defects, f"{path}.verdict", "must match branch review_status")
    require_string_list(defects, data.get("findings"), f"{path}.findings")
    validate_base_range_diff_check(defects, data.get("commands_run"), f"{path}.commands_run", manifest)
    verification_gaps = require_string_list(defects, data.get("verification_gaps"), f"{path}.verification_gaps")
    if verdict == "mergeable" and verification_gaps:
        defect(defects, f"{path}.verification_gaps", "must be empty when verdict is mergeable")
    require_string_list(defects, data.get("residual_risks"), f"{path}.residual_risks")
    require_string(defects, data.get("summary"), f"{path}.summary")


def validate_review_artifact_for_branch(
    defects: list[str],
    branch_entry: dict,
    review_status: object,
    branch_status: object,
    *,
    manifest: object,
    manifest_path: Path,
) -> None:
    review_path = branch_entry.get("review_path")
    if not isinstance(review_path, str) or not review_path.strip():
        return
    review_artifact = (manifest_path.parent / review_path).resolve()
    require_existing = branch_status == "pass" or review_status != "missing"
    if not review_artifact.exists():
        if require_existing:
            defect(defects, "$.review_status", f"review artifact does not exist: {review_artifact}")
        return
    validate_review_artifact(
        defects,
        load_json_artifact(defects, review_artifact, "$.review_status"),
        str(review_status),
        "$.review_status",
        manifest=manifest,
        branch_id=str(branch_entry.get("id", "")) or None,
    )


def validate_branch_status(
    data: object,
    *,
    branch_id: str | None,
    branch: str | None,
    worktree: str | None,
    manifest: object,
    manifest_path: Path,
    status_path: Path,
) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "branch_id",
        "status",
        "branch",
        "worktree",
        "worker_statuses",
        "worker_parallelism",
        "lite_advice",
        "review_status",
        "changed_files",
        "commands_run",
        "tests",
        "dod_checklist",
        "blockers",
        "handoff",
    ]
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    if branch_id and root.get("branch_id") != branch_id:
        defect(defects, "$.branch_id", f"must be {branch_id!r}")
    if branch and root.get("branch") != branch:
        defect(defects, "$.branch", f"must be {branch!r}")
    if worktree and root.get("worktree") != worktree:
        defect(defects, "$.worktree", f"must be {worktree!r}")
    require_string(defects, root.get("branch_id"), "$.branch_id")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    require_string(defects, root.get("branch"), "$.branch")
    root_worktree = require_string(defects, root.get("worktree"), "$.worktree")
    if root_worktree and not is_absolute_path(root_worktree):
        defect(defects, "$.worktree", "must be an absolute path without traversal")
    worker_statuses = root.get("worker_statuses")
    min_workers = 1 if status in {"pass", "partial"} else 0
    if not isinstance(worker_statuses, list) or len(worker_statuses) < min_workers or len(worker_statuses) > MAX_WORKER_PACKETS_PER_BRANCH:
        defect(defects, "$.worker_statuses", f"must contain {min_workers} to 4 worker status objects")
    else:
        for index, item in enumerate(worker_statuses):
            validate_worker_status(defects, item, f"$.worker_statuses[{index}]")
    validate_worker_rollup(defects, worker_statuses, status)
    validate_worker_artifacts(
        defects,
        worker_statuses,
        status,
        branch=root.get("branch"),
        manifest_path=manifest_path,
    )
    worker_count = len(worker_statuses) if isinstance(worker_statuses, list) else 0
    validate_worker_parallelism(defects, root.get("worker_parallelism"), "$.worker_parallelism", worker_count=worker_count)
    root_branch_id = root.get("branch_id") if isinstance(root.get("branch_id"), str) else None
    validate_lite_advice_entries(
        defects,
        root.get("lite_advice"),
        "$.lite_advice",
        manifest_path=manifest_path,
        branch_id=root_branch_id,
    )
    branch_entry = validate_manifest_branch_contract(
        defects,
        root,
        manifest,
        manifest_path=manifest_path,
        status_path=status_path,
        require_all_workers=status in {"pass", "partial"},
    )
    review_status = root.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, "$.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if status == "pass" and review_status != "mergeable":
        defect(defects, "$.review_status", "must be mergeable when branch status is pass")
    if branch_entry:
        validate_review_artifact_for_branch(
            defects,
            branch_entry,
            review_status,
            status,
            manifest=manifest,
            manifest_path=manifest_path,
        )
    validate_path_list(defects, root.get("changed_files"), "$.changed_files")
    if status == "pass":
        validate_base_range_diff_check(defects, root.get("commands_run"), "$.commands_run", manifest)
    else:
        validate_command_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    validate_command_list(defects, root.get("tests"), "$.tests")
    require_string_list(defects, root.get("dod_checklist"), "$.dod_checklist", min_items=1)
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    if status == "pass" and blockers:
        defect(defects, "$.blockers", "must be empty when status is pass")
    if status in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, "$.blockers", "must explain non-pass status")
    require_string(defects, root.get("handoff"), "$.handoff")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--branch-id")
    parser.add_argument("--branch")
    parser.add_argument("--worktree")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True).as_posix() if args.worktree else None
    defects = validate_branch_status(
        load_json(status_path),
        branch_id=args.branch_id,
        branch=args.branch,
        worktree=worktree,
        manifest=load_json(manifest_path),
        manifest_path=manifest_path,
        status_path=status_path,
    )
    result = {"status": "pass" if not defects else "failed", "status_path": status_path.as_posix(), "defects": defects}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status={result['status']}")
        for item in defects:
            print(item)
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
