#!/usr/bin/env python3
"""Shared runtime status-validation helpers for goal orchestration skills."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
from pathlib import Path


LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}
SAFE_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PORCELAIN_PREFIX_RE = re.compile(r"^[ MADRCU?!]{2} ")


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


def load_json_artifact(defects: list[str], path: Path, field: str) -> object:
    try:
        return load_json(path)
    except Exception as exc:  # noqa: BLE001
        defect(defects, field, f"must be readable JSON at {path}: {exc}")
        return {}


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


def is_repo_relative_path(value: str, *, reject_porcelain: bool = False) -> bool:
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
        or (reject_porcelain and PORCELAIN_PREFIX_RE.match(value) is not None)
    )


def is_absolute_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or not path.is_absolute()
        or ".." in path.parts
    )


def validate_lite_source_files(
    defects: list[str],
    value: object,
    path: str,
    *,
    reject_porcelain: bool = False,
) -> list[dict]:
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
        if source_path and not is_repo_relative_path(source_path, reject_porcelain=reject_porcelain):
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


def load_lite_validator(defects: list[str], script_dir: Path, module_name: str):
    path = script_dir / "validate_lite_advice.py"
    if not path.exists():
        defect(defects, "$.lite_advice", f"missing Lite advice validator: {path}")
        return None
    spec = importlib.util.spec_from_file_location(module_name, path)
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


def lite_validation_command(script_dir: Path, advice_path: Path, inputs_path: Path) -> str:
    validator_path = script_dir / "validate_lite_advice.py"
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
    allowed_purposes: set[str],
    skill_name: str,
    scope_label: str,
    malformed_packet_prefix: str,
    required_packet_prefix: str | None = None,
) -> None:
    lite_root = manifest_path.parent / "lite"
    if not lite_root.is_dir():
        return
    for packet_dir in sorted(item for item in lite_root.iterdir() if item.is_dir()):
        inputs_path = packet_dir / "input-files.json"
        advice_path = packet_dir / "advice.json"
        inputs_data: object = {}
        if inputs_path.exists():
            inputs_data = load_json_artifact(defects, inputs_path, f"{path}.{packet_dir.name}.inputs_path")
        elif advice_path.exists() and malformed_packet_prefix and packet_dir.name.startswith(malformed_packet_prefix):
            defect(defects, path, f"unrecorded malformed {scope_label} Lite packet without input-files.json: {packet_dir}")
            continue
        if not isinstance(inputs_data, dict):
            continue
        purpose = inputs_data.get("purpose")
        skill = inputs_data.get("skill")
        input_packet_id = inputs_data.get("packet_id")
        packet_id = input_packet_id if isinstance(input_packet_id, str) and input_packet_id.strip() else packet_dir.name
        prefix_relevant = bool(malformed_packet_prefix) and packet_dir.name.startswith(malformed_packet_prefix)
        prefix_scoped = bool(required_packet_prefix) and (
            packet_dir.name.startswith(required_packet_prefix)
            or packet_id.startswith(required_packet_prefix)
        )
        relevant = (
            purpose in allowed_purposes
            or skill == skill_name
            or prefix_relevant
            or prefix_scoped
        )
        if relevant and required_packet_prefix is not None and not prefix_scoped:
            defect(defects, path, f"{scope_label} Lite packet is not scoped to {required_packet_prefix}: {packet_id} at {packet_dir}")
            continue
        if relevant and packet_id not in reported_ids:
            defect(defects, path, f"unrecorded manifest-owned {scope_label} Lite packet: {packet_id} at {packet_dir}")


def validate_runtime_lite_advice_entries(
    defects: list[str],
    value: object,
    path: str,
    *,
    manifest_path: Path,
    script_dir: Path,
    validator_module_name: str,
    allowed_purposes: set[str],
    skill_name: str,
    scope_label: str,
    malformed_packet_prefix: str,
    required_packet_prefix: str | None = None,
    reject_source_porcelain: bool = False,
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
        if packet_id and not SAFE_PACKET_RE.fullmatch(packet_id):
            defect(defects, f"{item_path}.packet_id", "must be a safe packet id")
        if required_packet_prefix and packet_id and not packet_id.startswith(required_packet_prefix):
            defect(defects, f"{item_path}.packet_id", f"must start with {required_packet_prefix}")
        if packet_id in seen:
            defect(defects, f"{item_path}.packet_id", f"duplicates Lite packet {packet_id!r}")
        seen.add(packet_id)
        if packet_id:
            reported_ids.add(packet_id)
        purpose = require_string(defects, data.get("purpose"), f"{item_path}.purpose")
        if purpose and purpose not in allowed_purposes:
            defect(defects, f"{item_path}.purpose", f"must be one of {sorted(allowed_purposes)}")
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
        source_files = validate_lite_source_files(
            defects,
            data.get("source_files"),
            f"{item_path}.source_files",
            reject_porcelain=reject_source_porcelain,
        )
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
            expected_command = lite_validation_command(script_dir, expected_advice, expected_inputs)
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
            lite_validator = load_lite_validator(defects, script_dir, validator_module_name)
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
        allowed_purposes=allowed_purposes,
        skill_name=skill_name,
        scope_label=scope_label,
        malformed_packet_prefix=malformed_packet_prefix,
        required_packet_prefix=required_packet_prefix,
    )
