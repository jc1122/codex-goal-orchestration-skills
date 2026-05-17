#!/usr/bin/env python3
"""Validate a CLI-only Lite advisory artifact."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath


STATUSES = {"ok", "partial", "blocked"}
PURPOSES = {
    "preflight-decomposition",
    "lint-repair",
    "audit-defect-summary",
    "branch-packet-planning",
    "context-pack",
    "worker-summary",
    "blocked-triage",
    "main-summary",
}
RISK_LABELS = {"unsupported", "unresolved", "negative", "weakened", "probe-only", "blocked"}
SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def is_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not (
        "\\" in value
        or value.startswith("/")
        or value.startswith("./")
        or value == "."
        or "/./" in value
        or value.endswith("/.")
        or "//" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    )


def validate_source_files(defects: list[str], value: object, path: str, expected: list[dict] | None) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    seen = set()
    actual = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        source_path = require_string(defects, data.get("path"), f"{item_path}.path")
        sha256 = require_string(defects, data.get("sha256"), f"{item_path}.sha256")
        size_bytes = data.get("size_bytes")
        require_string(defects, data.get("reason"), f"{item_path}.reason")
        if source_path and not is_relative_path(source_path):
            defect(defects, f"{item_path}.path", "must be relative without traversal")
        if source_path in seen:
            defect(defects, f"{item_path}.path", f"duplicates source file {source_path!r}")
        seen.add(source_path)
        if sha256 and not SHA256_RE.fullmatch(sha256):
            defect(defects, f"{item_path}.sha256", "must be sha256:<64 lowercase hex chars>")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            defect(defects, f"{item_path}.size_bytes", "must be a non-negative integer")
        actual.append(
            {
                "path": source_path,
                "sha256": sha256,
                "size_bytes": size_bytes,
            }
        )
    if expected is not None:
        expected_min = [
            {
                "path": item.get("path"),
                "sha256": item.get("sha256"),
                "size_bytes": item.get("size_bytes"),
            }
            for item in expected
        ]
        if sorted(actual, key=lambda item: item["path"]) != sorted(expected_min, key=lambda item: item["path"]):
            defect(defects, path, "must match input-files.json source metadata exactly")


def validate_recommended_reads(defects: list[str], value: object, path: str) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        target = require_string(defects, data.get("path"), f"{item_path}.path")
        require_string(defects, data.get("anchor"), f"{item_path}.anchor")
        require_string(defects, data.get("reason"), f"{item_path}.reason")
        if target and not is_relative_path(target):
            defect(defects, f"{item_path}.path", "must be relative without traversal")


def validate_risk_flags(defects: list[str], value: object, path: str) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        label = require_string(defects, data.get("label"), f"{item_path}.label")
        target = require_string(defects, data.get("path"), f"{item_path}.path")
        require_string(defects, data.get("reason"), f"{item_path}.reason")
        if label and label not in RISK_LABELS:
            defect(defects, f"{item_path}.label", f"must be one of {sorted(RISK_LABELS)}")
        if target and not is_relative_path(target):
            defect(defects, f"{item_path}.path", "must be relative without traversal")


def validate(data: object, *, packet_id: str | None, purpose: str | None, expected_sources: list[dict] | None) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "packet_id",
        "role",
        "purpose",
        "status",
        "source_files",
        "recommended_reads",
        "risk_flags",
        "advice",
        "summary",
        "blockers",
        "commands_run",
    ]
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    actual_packet_id = require_string(defects, root.get("packet_id"), "$.packet_id")
    if actual_packet_id and not SAFE_LABEL_RE.fullmatch(actual_packet_id):
        defect(defects, "$.packet_id", "must be a safe packet id")
    if packet_id and actual_packet_id != packet_id:
        defect(defects, "$.packet_id", f"must be {packet_id!r}")
    if root.get("role") != "lite_advisor":
        defect(defects, "$.role", "must be 'lite_advisor'")
    actual_purpose = require_string(defects, root.get("purpose"), "$.purpose")
    if actual_purpose and actual_purpose not in PURPOSES:
        defect(defects, "$.purpose", f"must be one of {sorted(PURPOSES)}")
    if purpose and actual_purpose != purpose:
        defect(defects, "$.purpose", f"must be {purpose!r}")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    validate_source_files(defects, root.get("source_files"), "$.source_files", expected_sources)
    validate_recommended_reads(defects, root.get("recommended_reads"), "$.recommended_reads")
    validate_risk_flags(defects, root.get("risk_flags"), "$.risk_flags")
    if not isinstance(root.get("advice"), dict):
        defect(defects, "$.advice", "must be an object")
    require_string(defects, root.get("summary"), "$.summary")
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    require_string_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    if status == "ok" and blockers:
        defect(defects, "$.blockers", "must be empty when status is ok")
    if status == "blocked" and not blockers:
        defect(defects, "$.blockers", "must explain blocked Lite advice")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--advice", required=True)
    parser.add_argument("--inputs")
    parser.add_argument("--packet-id")
    parser.add_argument("--purpose", choices=sorted(PURPOSES))
    args = parser.parse_args()

    advice_path = resolve_absolute_path(args.advice, "--advice", must_exist=True)
    inputs_path = (
        resolve_absolute_path(args.inputs, "--inputs", must_exist=True)
        if args.inputs
        else None
    )
    expected_sources = None
    if inputs_path:
        inputs = require_object([], load_json(inputs_path), "inputs")
        expected_sources = inputs.get("source_files") if isinstance(inputs.get("source_files"), list) else []
        if not args.packet_id:
            args.packet_id = inputs.get("packet_id")
        if not args.purpose:
            args.purpose = inputs.get("purpose")

    defects = validate(
        load_json(advice_path),
        packet_id=args.packet_id,
        purpose=args.purpose,
        expected_sources=expected_sources,
    )
    if defects:
        print("status=failed")
        for item in defects:
            print(f"- {item}")
        return 1
    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
