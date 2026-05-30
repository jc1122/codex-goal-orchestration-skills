#!/usr/bin/env python3
"""Validate prompt-audit artifact and telemetry before branch scheduling."""

from __future__ import annotations

import argparse
import importlib.util
import json
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
SHARED_SCRIPTS = SCRIPT_DIR.parents[1] / "_goal_shared" / "scripts"
STATUS_VALIDATION = _load_module("goal_shared_status_validation", SHARED_SCRIPTS / "status_validation.py")

resolve_absolute_path = STATUS_VALIDATION.resolve_absolute_path
load_json_artifact = STATUS_VALIDATION.load_json_artifact
defect = STATUS_VALIDATION.defect
require_object = STATUS_VALIDATION.require_object
require_string = STATUS_VALIDATION.require_string
require_string_list = STATUS_VALIDATION.require_string_list
validate_telemetry_artifact = STATUS_VALIDATION.validate_telemetry_artifact

AUDIT_STATUSES = {"pass", "failed", "blocked"}
AUDIT_LADDER = ["gpt-5.5", "gpt-5.4"]


def validate_defects(defects: list[str], value: object) -> None:
    if not isinstance(value, list):
        defect(defects, "$.defects", "must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"$.defects[{index}]"
        data = require_object(defects, item, item_path)
        severity = data.get("severity")
        if severity not in {"critical", "major", "minor"}:
            defect(defects, f"{item_path}.severity", "must be one of critical, major, minor")
        require_string(defects, data.get("file"), f"{item_path}.file")
        require_string(defects, data.get("message"), f"{item_path}.message")


def validate_telemetry(defects: list[str], audit_path: Path, *, audit_status: str) -> None:
    telemetry_path = audit_path.parent / "telemetry.json"
    telemetry = validate_telemetry_artifact(
        defects,
        telemetry_path,
        "$.telemetry",
        packet_id="prompt-audit",
        role="prompt-auditor",
        allowed_aliases=AUDIT_LADDER,
        require_called=audit_status == "pass",
    )
    attempts = telemetry.get("attempts")
    if not isinstance(attempts, list):
        return
    aliases = [attempt.get("alias") for attempt in attempts if isinstance(attempt, dict)]
    if aliases != AUDIT_LADDER:
        defect(defects, "$.telemetry.attempts", "must declare gpt-5.5 then gpt-5.4")
    called = [attempt.get("alias") for attempt in attempts if isinstance(attempt, dict) and attempt.get("called") is True]
    if called and called != AUDIT_LADDER[: len(called)]:
        defect(defects, "$.telemetry.attempts", "called attempts must be a non-empty prefix of the audit ladder")
    accepted = [attempt.get("alias") for attempt in attempts if isinstance(attempt, dict) and attempt.get("accepted") is True]
    if audit_status == "pass" and len(accepted) != 1:
        defect(defects, "$.telemetry.attempts", "passing prompt audit telemetry must identify exactly one accepted model")


def validate_prompt_audit(audit_path: Path, manifest_path: Path, repo_root: Path, *, require_pass: bool) -> list[str]:
    defects: list[str] = []
    audit = require_object(defects, load_json_artifact(defects, audit_path, "$"), "$")
    if audit.get("manifest") != manifest_path.as_posix():
        defect(defects, "$.manifest", "must match --manifest")
    if audit.get("repo_root") != repo_root.as_posix():
        defect(defects, "$.repo_root", "must match --repo-root")
    status = audit.get("status")
    if status not in AUDIT_STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(AUDIT_STATUSES)}")
        status = "failed"
    if require_pass and status != "pass":
        defect(defects, "$.status", "must be pass when --require-pass is set")
    if not isinstance(audit.get("can_start"), bool):
        defect(defects, "$.can_start", "must be a boolean")
    checked_files = require_string_list(defects, audit.get("checked_files"), "$.checked_files")
    commands_run = require_string_list(defects, audit.get("commands_run"), "$.commands_run", min_items=1)
    missing_dod = require_string_list(defects, audit.get("missing_dod_items"), "$.missing_dod_items")
    validate_defects(defects, audit.get("defects"))
    if status == "pass":
        if audit.get("can_start") is not True:
            defect(defects, "$.can_start", "must be true when status is pass")
        if not checked_files:
            defect(defects, "$.checked_files", "must be non-empty when status is pass")
        if not commands_run:
            defect(defects, "$.commands_run", "must be non-empty when status is pass")
        if missing_dod:
            defect(defects, "$.missing_dod_items", "must be empty when status is pass")
        for index, item in enumerate(audit.get("defects", []) if isinstance(audit.get("defects"), list) else []):
            if isinstance(item, dict) and item.get("severity") in {"critical", "major"}:
                defect(defects, f"$.defects[{index}].severity", "passing audit must not contain critical or major defects")
    else:
        if audit.get("can_start") is True:
            defect(defects, "$.can_start", "must be false unless status is pass")
    validate_telemetry(defects, audit_path, audit_status=str(status))
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--require-pass", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    audit_path = resolve_absolute_path(args.audit, "--audit", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    defects = validate_prompt_audit(audit_path, manifest_path, repo_root, require_pass=args.require_pass)
    result = {
        "status": "pass" if not defects else "failed",
        "audit": audit_path.as_posix(),
        "defects": defects,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']}")
        for item in defects:
            print(f"- {item}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
