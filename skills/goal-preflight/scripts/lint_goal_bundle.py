#!/usr/bin/env python3
"""Deterministically lint a goal preflight bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
from pathlib import Path, PurePosixPath


MAX_ACTIVE_BRANCH_AGENTS = 4
MAX_WORKER_PACKETS_PER_BRANCH = 4
MAX_WAVES = 5
DEFAULT_TOTAL_BRANCH_CAP = MAX_ACTIVE_BRANCH_AGENTS * MAX_WAVES
SAFE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")
PREFLIGHT_LITE_PURPOSES = {"preflight-decomposition", "lint-repair"}
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}


def is_strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def relative_path_defect(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{field} must be a non-empty relative path"
    if "\\" in value:
        return f"{field} must use POSIX '/' separators, not backslashes"
    if "//" in value:
        return f"{field} must not contain empty path segments"
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        return f"{field} must not contain '.' path segments"
    path = PurePosixPath(value)
    if path.is_absolute():
        return f"{field} must be relative, not absolute"
    if any(part in {"", ".", ".."} for part in path.parts):
        return f"{field} must not contain empty, '.', or '..' segments"
    return None


def safe_branch_name(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return not (
        any(char in INVALID_BRANCH_CHARS for char in value)
        or any(char.isspace() for char in value)
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
    )


def has_dod(text: str) -> bool:
    lowered = text.lower()
    if "definition of done" not in lowered:
        return False
    after = lowered.split("definition of done", 1)[1]
    return "- " in after


def load_lite_validator() -> object | None:
    path = Path(__file__).resolve().parent / "validate_lite_advice.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("goal_preflight_validate_lite_advice", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json_artifact(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def validate_preflight_lite_records(defect, bundle_dir: Path, manifest: dict) -> None:
    records = manifest.get("preflight_lite_advice")
    if not isinstance(records, list):
        defect("job.manifest.json", "critical", "preflight_lite_advice must be present as an array")
        return
    validator = load_lite_validator()
    if validator is None:
        defect("job.manifest.json", "critical", "could not load validate_lite_advice.py for preflight Lite provenance")
        return
    reported_ids: set[str] = set()
    for index, record in enumerate(records):
        path = f"preflight_lite_advice[{index}]"
        if not isinstance(record, dict):
            defect("job.manifest.json", "critical", f"{path} must be an object")
            continue
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
            if key not in record:
                defect("job.manifest.json", "critical", f"{path} missing key: {key}")
        packet_id = record.get("packet_id")
        if not isinstance(packet_id, str) or not SAFE_LABEL_RE.fullmatch(packet_id):
            defect("job.manifest.json", "critical", f"{path}.packet_id must match {SAFE_LABEL_RE.pattern}")
            continue
        if packet_id in reported_ids:
            defect("job.manifest.json", "critical", f"{path}.packet_id duplicates {packet_id}")
        reported_ids.add(packet_id)
        purpose = record.get("purpose")
        if purpose not in PREFLIGHT_LITE_PURPOSES:
            defect("job.manifest.json", "critical", f"{path}.purpose must be one of {sorted(PREFLIGHT_LITE_PURPOSES)}")
        if record.get("status") not in LITE_STATUSES:
            defect("job.manifest.json", "critical", f"{path}.status must be one of {sorted(LITE_STATUSES)}")
        if record.get("disposition") not in LITE_DISPOSITIONS:
            defect("job.manifest.json", "critical", f"{path}.disposition must be one of {sorted(LITE_DISPOSITIONS)}")
        if record.get("disposition") == "used" and record.get("status") != "ok":
            defect("job.manifest.json", "critical", f"{path}.disposition may be used only when Lite status is ok")
        expected_advice = f"lite/{packet_id}/advice.json"
        expected_inputs = f"lite/{packet_id}/input-files.json"
        if record.get("advice_path") != expected_advice:
            defect("job.manifest.json", "critical", f"{path}.advice_path must be {expected_advice!r}")
        if record.get("inputs_path") != expected_inputs:
            defect("job.manifest.json", "critical", f"{path}.inputs_path must be {expected_inputs!r}")
        validation_status = record.get("validation_status")
        validation_defects = record.get("validation_defects")
        if validation_status not in LITE_VALIDATION_STATUSES:
            defect("job.manifest.json", "critical", f"{path}.validation_status must be one of {sorted(LITE_VALIDATION_STATUSES)}")
        if not isinstance(validation_defects, list) or any(not isinstance(item, str) or not item.strip() for item in validation_defects):
            defect("job.manifest.json", "critical", f"{path}.validation_defects must be an array of non-empty strings")
            validation_defects = []
        if validation_status == "pass" and validation_defects:
            defect("job.manifest.json", "critical", f"{path}.validation_defects must be empty when validation_status is pass")
        if validation_status == "failed" and not validation_defects:
            defect("job.manifest.json", "critical", f"{path}.validation_defects must explain failed Lite validation")
        validation_command = record.get("validation_command")
        if not isinstance(record.get("reason"), str) or not record.get("reason", "").strip():
            defect("job.manifest.json", "critical", f"{path}.reason must be a non-empty string")
        advice_path = bundle_dir / expected_advice
        inputs_path = bundle_dir / expected_inputs
        expected_command = lite_validation_command(advice_path, inputs_path)
        if not isinstance(validation_command, str) or validation_command != expected_command:
            defect("job.manifest.json", "critical", f"{path}.validation_command must be exactly: {expected_command}")
        if not advice_path.exists():
            defect("job.manifest.json", "critical", f"{path}.advice_path artifact does not exist: {advice_path}")
            continue
        if not inputs_path.exists():
            defect("job.manifest.json", "critical", f"{path}.inputs_path artifact does not exist: {inputs_path}")
            continue
        try:
            advice_data = load_json_artifact(advice_path)
            inputs_data = load_json_artifact(inputs_path)
        except Exception as exc:  # noqa: BLE001
            defect("job.manifest.json", "critical", f"{path} Lite artifacts must be readable JSON: {exc}")
            continue
        expected_sources = inputs_data.get("source_files") if isinstance(inputs_data, dict) and isinstance(inputs_data.get("source_files"), list) else []
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
        if record.get("source_files") != expected_min:
            defect("job.manifest.json", "critical", f"{path}.source_files must match input-files.json source metadata exactly")
        lite_defects = validator.validate(
            advice_data,
            packet_id=packet_id,
            purpose=str(purpose) if isinstance(purpose, str) else None,
            expected_sources=expected_sources,
            inputs=inputs_data if isinstance(inputs_data, dict) else None,
            inputs_path=inputs_path,
        )
        actual_validation_status = "pass" if not lite_defects else "failed"
        if validation_status in LITE_VALIDATION_STATUSES and validation_status != actual_validation_status:
            defect("job.manifest.json", "critical", f"{path}.validation_status must match actual Lite validation status {actual_validation_status!r}")
        if validation_status == "failed" and validation_defects != lite_defects:
            defect("job.manifest.json", "critical", f"{path}.validation_defects must match actual Lite validation defects exactly")
        if record.get("disposition") == "used" and lite_defects:
            defect("job.manifest.json", "critical", f"{path} used Lite advice must pass validation")

    lite_root = bundle_dir / "lite"
    if not lite_root.is_dir():
        return
    for packet_dir in sorted(item for item in lite_root.iterdir() if item.is_dir()):
        inputs_path = packet_dir / "input-files.json"
        advice_path = packet_dir / "advice.json"
        inputs_data: object = {}
        if inputs_path.exists():
            try:
                inputs_data = load_json_artifact(inputs_path)
            except Exception as exc:  # noqa: BLE001
                defect("job.manifest.json", "critical", f"lite/{packet_dir.name}/input-files.json must be readable JSON: {exc}")
                continue
        elif advice_path.exists() and packet_dir.name.startswith("P"):
            defect("job.manifest.json", "critical", f"unrecorded malformed preflight Lite packet without input-files.json: {packet_dir}")
            continue
        if not isinstance(inputs_data, dict):
            continue
        purpose = inputs_data.get("purpose")
        skill = inputs_data.get("skill")
        input_packet_id = inputs_data.get("packet_id")
        packet_id = input_packet_id if isinstance(input_packet_id, str) and input_packet_id.strip() else packet_dir.name
        relevant = purpose in PREFLIGHT_LITE_PURPOSES or skill == "goal-preflight" or packet_dir.name.startswith("P")
        if relevant and packet_id not in reported_ids:
            defect("job.manifest.json", "critical", f"unrecorded manifest-owned preflight Lite packet: {packet_id} at {packet_dir}")


def lint(bundle_dir: Path) -> dict:
    defects: list[dict] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append({"file": file, "severity": severity, "message": message})

    manifest_path = bundle_dir / "job.manifest.json"
    if not manifest_path.exists():
        defect("job.manifest.json", "critical", "manifest is missing")
        return result(defects)

    try:
        manifest = load_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        defect("job.manifest.json", "critical", f"manifest is not valid JSON: {exc}")
        return result(defects)

    for dirname in ["branches", "workers", "reviewers", "audit", "lite"]:
        if not (bundle_dir / dirname).is_dir():
            defect(dirname + "/", "critical", f"required bundle directory is missing: {dirname}/")

    for key in [
        "job_id",
        "main_prompt",
        "base_ref",
        "artifact_policy",
        "cleanup_policy",
        "branches",
        "waves",
        "max_active_branch_agents",
        "parallelization",
        "preflight_lite_advice",
    ]:
        if key not in manifest:
            defect("job.manifest.json", "critical", f"missing key: {key}")
    validate_preflight_lite_records(defect, bundle_dir, manifest)
    if not safe_branch_name(manifest.get("base_ref")):
        defect("job.manifest.json", "critical", f"base_ref is not safe: {manifest.get('base_ref')!r}")
    for key in ["artifact_policy", "cleanup_policy"]:
        if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip():
            defect("job.manifest.json", "critical", f"{key} must be non-empty")

    max_active = manifest.get("max_active_branch_agents")
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "max_active_branch_agents must be an integer from 1 to 4")

    parallelization = manifest.get("parallelization", {})
    if not isinstance(parallelization, dict):
        defect("job.manifest.json", "critical", "parallelization must be an object")
        parallelization = {}
    if parallelization.get("parallelism_default") is not True:
        defect("job.manifest.json", "critical", "parallelization.parallelism_default must be true")
    if parallelization.get("max_branches_per_wave") != MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "parallelization.max_branches_per_wave must be 4")
    if parallelization.get("max_waves") != MAX_WAVES:
        defect("job.manifest.json", "critical", "parallelization.max_waves must be 5")
    serial_reason = parallelization.get("serial_reason", "")
    parallelization_rationale = parallelization.get("parallelization_rationale", "")
    has_parallelization_reason = (
        isinstance(serial_reason, str)
        and bool(serial_reason.strip())
    ) or (
        isinstance(parallelization_rationale, str)
        and bool(parallelization_rationale.strip())
    )

    branches = manifest.get("branches", [])
    if not branches:
        defect("job.manifest.json", "critical", "branches must be non-empty")
    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        defect("job.manifest.json", "critical", "more than 20 branches exceeds 5 waves of 4")
    if len(branches) == 1 and not (isinstance(serial_reason, str) and serial_reason.strip()):
        defect("job.manifest.json", "critical", "single-branch bundles require parallelization.serial_reason")
    if is_strict_int(max_active) and max_active < MAX_ACTIVE_BRANCH_AGENTS and not has_parallelization_reason:
        defect("job.manifest.json", "critical", "max_active_branch_agents below 4 requires serial_reason or parallelization_rationale")

    ids = [branch.get("id") for branch in branches]
    names = [branch.get("branch_name") for branch in branches]
    worktree_paths = [branch.get("worktree_path") for branch in branches]
    for bid in ids:
        if not isinstance(bid, str) or not SAFE_ID_RE.fullmatch(bid):
            defect("job.manifest.json", "critical", f"branch id is not safe: {bid!r}")
    if len(ids) != len(set(ids)):
        defect("job.manifest.json", "critical", "branch ids must be unique")
    for name in names:
        if not safe_branch_name(name):
            defect("job.manifest.json", "critical", f"branch name is not safe: {name!r}")
    if len(names) != len(set(names)):
        defect("job.manifest.json", "critical", "branch names must be unique")
    if len(worktree_paths) != len(set(worktree_paths)):
        defect("job.manifest.json", "critical", "branch worktree_path values must be unique")

    reserved_bundle_paths = {
        "job.manifest.json",
        "main.prompt.md",
        "goal-bootloader.md",
        "PREFLIGHT_REPORT.md",
        "preflight.lint.json",
    }
    branch_bundle_paths: dict[str, str] = {}
    for branch in branches:
        for key in ["prompt", "status_path", "review_path"]:
            value = branch.get(key)
            if not isinstance(value, str):
                continue
            label = f"branch {branch.get('id')} {key}"
            if value in reserved_bundle_paths:
                defect("job.manifest.json", "critical", f"{label} collides with reserved bundle file: {value}")
            owner = branch_bundle_paths.get(value)
            if owner is not None:
                defect("job.manifest.json", "critical", f"{label} duplicates {owner}: {value}")
            else:
                branch_bundle_paths[value] = label

    waves = manifest.get("waves", [])
    wave_branch_ids = []
    wave_ids = []
    if len(waves) > MAX_WAVES:
        defect("job.manifest.json", "critical", "more than 5 waves is not allowed")
    for idx, wave in enumerate(waves):
        wid = wave.get("id")
        wave_ids.append(wid)
        if not isinstance(wid, str) or not SAFE_LABEL_RE.fullmatch(wid):
            defect("job.manifest.json", "critical", f"wave id is not safe: {wid!r}")
        branch_ids = wave.get("branches", [])
        if not isinstance(branch_ids, list) or not branch_ids:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} must list at least one branch")
            branch_ids = []
        if len(branch_ids) > MAX_ACTIVE_BRANCH_AGENTS:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} has more than 4 branches")
        if is_strict_int(max_active) and len(branch_ids) > max_active:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} exceeds max_active_branch_agents")
        if idx < len(waves) - 1 and is_strict_int(max_active) and len(branch_ids) < max_active and not has_parallelization_reason:
            defect("job.manifest.json", "critical", f"underfilled non-final wave {wave.get('id')} requires serial_reason or parallelization_rationale")
        wave_branch_ids.extend(branch_ids)
    if len(wave_ids) != len(set(wave_ids)):
        defect("job.manifest.json", "critical", "wave ids must be unique")
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        defect("job.manifest.json", "critical", "branch ids must not appear in more than one wave")
    if set(wave_branch_ids) != set(ids):
        defect("job.manifest.json", "critical", "waves must cover exactly the manifest branch ids")

    main_prompt_value = manifest.get("main_prompt", "main.prompt.md")
    main_path_error = relative_path_defect(main_prompt_value, "main_prompt")
    if main_path_error:
        defect("job.manifest.json", "critical", main_path_error)
        main_path = None
    else:
        main_path = resolve(bundle_dir, main_prompt_value)
    if main_path is not None and not main_path.exists():
        defect(str(main_path), "critical", "main prompt is missing")
    elif main_path is not None:
        main_text = main_path.read_text(encoding="utf-8")
        for phrase in [
            "manifest paths",
            "repository root",
            "skill availability bootstrap",
            "prompt audit",
            "prompt-audit.json",
            "pins this manifest",
            "max_active_branch_agents",
            "Parallelism is the default",
            "never exceed 4",
            "Launch all branches in each wave concurrently",
            "do not poll active branch",
            "git diff --check",
            "Cleanup Policy",
            "Artifact Policy",
            "close finished branch orchestrator agents",
            "validate_branch_status.py --manifest",
            "validate_main_status.py --manifest",
            "Optional Lite advisors",
        ]:
            if phrase.lower() not in main_text.lower():
                defect(str(main_path), "critical", f"main prompt missing required phrase: {phrase}")
        if not has_dod(main_text):
            defect(str(main_path), "critical", "main prompt lacks a falsifiable Definition of Done")

    bootloader_path = bundle_dir / "goal-bootloader.md"
    if not bootloader_path.exists():
        defect("goal-bootloader.md", "critical", "bootloader is missing")
    else:
        bootloader = bootloader_path.read_text(encoding="utf-8")
        if len(bootloader) > 4000:
            defect("goal-bootloader.md", "critical", "bootloader exceeds 4000 characters")
        for phrase in [
            "$goal-main-orchestrator",
            "Bundle root",
            "Repository root",
            "job.manifest.json",
            "main.prompt.md",
            "skill availability",
            "check_goal_skill_availability.py",
            "absolute paths",
            "pins the manifest",
            "Parallelism is the default",
            "never exceed 4",
            "Launch every branch in the current wave concurrently",
            "1 to 4 worker packets",
        ]:
            if phrase not in bootloader:
                defect("goal-bootloader.md", "critical", f"bootloader missing phrase: {phrase}")

    required_branch_keys = [
        "id",
        "wave",
        "prompt",
        "branch_name",
        "worktree_path",
        "status_path",
        "review_path",
        "work_items",
        "max_active_worker_packets",
        "worker_parallelism",
    ]
    for branch in branches:
        for key in required_branch_keys:
            if key not in branch:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} missing key: {key}")
        max_workers = branch.get("max_active_worker_packets")
        if not is_strict_int(max_workers) or max_workers < 1 or max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} max_active_worker_packets must be an integer from 1 to 4")
        work_items = branch.get("work_items", [])
        if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items must contain 1 to 4 worker packets")
        elif any(not isinstance(item, dict) for item in work_items):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items entries must be objects")
        else:
            seen_work_item_ids = set()
            for index, item in enumerate(work_items):
                item_path = f"branch {branch.get('id')} work_items[{index}]"
                item_id = item.get("id")
                if not isinstance(item_id, str) or not SAFE_LABEL_RE.fullmatch(item_id):
                    defect("job.manifest.json", "critical", f"{item_path}.id must match {SAFE_LABEL_RE.pattern}")
                elif item_id in seen_work_item_ids:
                    defect("job.manifest.json", "critical", f"{item_path}.id duplicates {item_id}")
                else:
                    seen_work_item_ids.add(item_id)
                packet_id = item.get("packet_id")
                expected_packet_id = f"{branch.get('id')}-{item_id}" if isinstance(branch.get("id"), str) and isinstance(item_id, str) else ""
                if not isinstance(packet_id, str) or not SAFE_LABEL_RE.fullmatch(packet_id):
                    defect("job.manifest.json", "critical", f"{item_path}.packet_id must match {SAFE_LABEL_RE.pattern}")
                elif expected_packet_id and packet_id != expected_packet_id:
                    defect("job.manifest.json", "critical", f"{item_path}.packet_id must be {expected_packet_id!r}")
                if not isinstance(item.get("objective"), str) or not item.get("objective", "").strip():
                    defect("job.manifest.json", "critical", f"{item_path}.objective must be non-empty")
                for key, min_items in [("owned_paths", 1), ("verification", 1), ("dod", 1), ("context_files", 0), ("depends_on", 0)]:
                    values = item.get(key, [])
                    if key in {"owned_paths", "verification", "dod"} and key not in item:
                        defect("job.manifest.json", "critical", f"{item_path}.{key} is required")
                        continue
                    if not isinstance(values, list) or len(values) < min_items:
                        defect("job.manifest.json", "critical", f"{item_path}.{key} must contain at least {min_items} item(s)")
                        continue
                    for value_index, value in enumerate(values):
                        if not isinstance(value, str) or not value.strip():
                            defect("job.manifest.json", "critical", f"{item_path}.{key}[{value_index}] must be a non-empty string")
                        elif key in {"owned_paths", "context_files"}:
                            message = relative_path_defect(value, f"{item_path}.{key}[{value_index}]")
                            if message:
                                defect("job.manifest.json", "critical", message)
            known_work_item_ids = {item.get("id") for item in work_items if isinstance(item, dict)}
            for index, item in enumerate(work_items):
                if not isinstance(item, dict):
                    continue
                for dep in item.get("depends_on", []):
                    if dep not in known_work_item_ids:
                        defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items[{index}] depends on unknown work item: {dep}")
                    if dep == item.get("id"):
                        defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items[{index}] cannot depend on itself")
        worker_parallelism = branch.get("worker_parallelism", {})
        if not isinstance(worker_parallelism, dict):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism must be an object")
            worker_parallelism = {}
        if worker_parallelism.get("parallelism_default") is not True:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.parallelism_default must be true")
        if worker_parallelism.get("max_active_worker_packets") != max_workers:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.max_active_worker_packets must match branch max_active_worker_packets")
        if worker_parallelism.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.max_worker_packets_per_branch must be 4")
        if not isinstance(worker_parallelism.get("parallelization_rationale"), str) or not worker_parallelism.get("parallelization_rationale", "").strip():
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.parallelization_rationale must be non-empty")
        for key in ["prompt", "status_path", "review_path"]:
            message = relative_path_defect(branch.get(key), key)
            if message:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
        message = relative_path_defect(branch.get("worktree_path"), "worktree_path")
        if message:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
        prompt_value = branch.get("prompt", "")
        if relative_path_defect(prompt_value, "prompt"):
            continue
        prompt_path = resolve(bundle_dir, prompt_value)
        if not prompt_path.exists():
            defect(str(prompt_path), "critical", f"branch prompt missing for {branch.get('id')}")
            continue
        text = prompt_path.read_text(encoding="utf-8")
        for phrase in [
            "Objective",
            "Scope",
            "Work Items",
            "Reviewer Requirement",
            "Bootstrap Requirement",
            "Worker Parallelism",
            "Max active worker packets",
            "Max worker packets for this branch",
            "Never exceed",
            "active worker packets",
            "Worker parallelization rationale",
            "Worker packet id",
            "Lite Advisors",
            "validate_branch_status.py --manifest",
            "Stop Conditions",
            "git diff --check",
            "do not poll active",
        ]:
            if phrase.lower() not in text.lower():
                defect(str(prompt_path), "major", f"branch prompt missing section: {phrase}")
        if not has_dod(text):
            defect(str(prompt_path), "critical", f"branch {branch.get('id')} lacks a falsifiable Definition of Done")

    return result(defects)


def result(defects: list[dict]) -> dict:
    status = "pass" if not any(item["severity"] in {"critical", "major"} for item in defects) else "failed"
    return {"status": status, "defects": defects}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output")
    parser.add_argument("--no-write", action="store_true", help="Print lint JSON to stdout without mutating preflight.lint.json.")
    args = parser.parse_args()

    bundle_dir = resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
    data = lint(bundle_dir)
    if args.no_write:
        if args.output:
            raise SystemExit("--no-write cannot be combined with --output")
        print(json.dumps(data, indent=2))
        return 0 if data["status"] == "pass" else 1
    output_path = (
        resolve_absolute_path(args.output, "--output", must_exist=False)
        if args.output
        else bundle_dir / "preflight.lint.json"
    )
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(output_path)
    return 0 if data["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
