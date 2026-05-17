#!/usr/bin/env python3
"""Validate a goal main-orchestrator status artifact."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
from pathlib import Path


STATUSES = {"pass", "partial", "blocked", "failed"}
AUDIT_STATUSES = {"pass", "failed", "blocked", "missing"}
REVIEW_STATUSES = {"mergeable", "mergeable_after_fixes", "blocked", "reject", "missing"}
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}
MAIN_LITE_PURPOSES = {"audit-defect-summary", "main-summary"}
MAX_TOTAL_BRANCHES = 20
SAFE_REVIEW_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
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
    )


def is_absolute_path(value: str) -> bool:
    path = Path(value)
    return not (
        "\\" in value
        or not path.is_absolute()
        or ".." in path.parts
    )


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
    spec = importlib.util.spec_from_file_location("goal_main_validate_lite_advice", path)
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


def validate_lite_advice_entries(defects: list[str], value: object, path: str, *, manifest_path: Path) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    lite_validator = None
    seen = set()
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
        if packet_id in seen:
            defect(defects, f"{item_path}.packet_id", f"duplicates Lite packet {packet_id!r}")
        seen.add(packet_id)
        purpose = require_string(defects, data.get("purpose"), f"{item_path}.purpose")
        if purpose and purpose not in MAIN_LITE_PURPOSES:
            defect(defects, f"{item_path}.purpose", f"must be one of {sorted(MAIN_LITE_PURPOSES)}")
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
        if validation_command and not all(token in validation_command for token in ["validate_lite_advice.py", "--advice", "--inputs"]):
            defect(defects, f"{item_path}.validation_command", "must record validate_lite_advice.py with --advice and --inputs")
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


def validate_branch_summary(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    required = ["branch_id", "status", "status_path", "review_path", "review_status"]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    require_string(defects, data.get("branch_id"), f"{path}.branch_id")
    if data.get("status") not in STATUSES:
        defect(defects, f"{path}.status", f"must be one of {sorted(STATUSES)}")
    status_path = require_string(defects, data.get("status_path"), f"{path}.status_path")
    review_path = require_string(defects, data.get("review_path"), f"{path}.review_path")
    if status_path and not is_repo_relative_path(status_path):
        defect(defects, f"{path}.status_path", "must be a repo-relative path without traversal")
    if review_path and not is_repo_relative_path(review_path):
        defect(defects, f"{path}.review_path", "must be a repo-relative path without traversal")
    review_status = data.get("review_status")
    if review_status not in REVIEW_STATUSES:
        defect(defects, f"{path}.review_status", f"must be one of {sorted(REVIEW_STATUSES)}")
    if data.get("status") == "pass" and review_status != "mergeable":
        defect(defects, f"{path}.review_status", "must be mergeable when branch status is pass")


def load_json_artifact(defects: list[str], path: Path, field: str) -> object:
    try:
        return load_json(path)
    except Exception as exc:  # noqa: BLE001
        defect(defects, field, f"must be readable JSON at {path}: {exc}")
        return {}


def load_branch_status_validator(defects: list[str]):
    path = Path(__file__).resolve().parents[2] / "goal-branch-orchestrator" / "scripts" / "validate_branch_status.py"
    if not path.exists():
        defect(defects, "$", f"missing branch status validator: {path}")
        return None
    spec = importlib.util.spec_from_file_location("goal_branch_validate_branch_status", path)
    if spec is None or spec.loader is None:
        defect(defects, "$", f"could not load branch status validator: {path}")
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        defect(defects, "$", f"could not import branch status validator {path}: {exc}")
        return None
    return module


def expected_branches_from_manifest(defects: list[str], manifest: object) -> dict[str, dict[str, str]]:
    data = require_object(defects, manifest, "manifest")
    branches = data.get("branches")
    expected: dict[str, dict[str, str]] = {}
    seen_values: dict[str, dict[str, str]] = {
        "id": {},
        "branch_name": {},
        "status_path": {},
        "review_path": {},
        "worktree_path": {},
    }
    if not isinstance(branches, list) or not branches:
        defect(defects, "manifest.branches", "must be a non-empty array")
        return expected
    for index, branch in enumerate(branches):
        branch_data = require_object(defects, branch, f"manifest.branches[{index}]")
        branch_id = require_string(defects, branch_data.get("id"), f"manifest.branches[{index}].id")
        branch_name = require_string(defects, branch_data.get("branch_name"), f"manifest.branches[{index}].branch_name")
        status_path = require_string(defects, branch_data.get("status_path"), f"manifest.branches[{index}].status_path")
        review_path = require_string(defects, branch_data.get("review_path"), f"manifest.branches[{index}].review_path")
        worktree_path = require_string(defects, branch_data.get("worktree_path"), f"manifest.branches[{index}].worktree_path")
        for field, value in [
            ("id", branch_id),
            ("branch_name", branch_name),
            ("status_path", status_path),
            ("review_path", review_path),
            ("worktree_path", worktree_path),
        ]:
            if not value:
                continue
            owner = seen_values[field].get(value)
            if owner is not None:
                defect(defects, f"manifest.branches[{index}].{field}", f"duplicates branch {owner}: {value!r}")
            else:
                seen_values[field][value] = branch_id
        if status_path and not is_repo_relative_path(status_path):
            defect(defects, f"manifest.branches[{index}].status_path", "must be a repo-relative path without traversal")
        if review_path and not is_repo_relative_path(review_path):
            defect(defects, f"manifest.branches[{index}].review_path", "must be a repo-relative path without traversal")
        if worktree_path and not is_repo_relative_path(worktree_path):
            defect(defects, f"manifest.branches[{index}].worktree_path", "must be a repo-relative path without traversal")
        if branch_id and status_path and review_path:
            expected[branch_id] = {
                "status_path": status_path,
                "review_path": review_path,
                "branch_name": branch_name,
            }
    return expected


def validate_manifest_branch_coverage(defects: list[str], root: dict, expected: dict[str, dict[str, str]]) -> None:
    branch_statuses = root.get("branch_statuses")
    if not isinstance(branch_statuses, list):
        return
    seen: dict[str, int] = {}
    for index, item in enumerate(branch_statuses):
        if not isinstance(item, dict):
            continue
        branch_id = item.get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            continue
        if branch_id in seen:
            defect(defects, f"$.branch_statuses[{index}].branch_id", f"duplicates branch summary for {branch_id!r}")
            continue
        seen[branch_id] = index
        expected_entry = expected.get(branch_id)
        if expected_entry is None:
            defect(defects, f"$.branch_statuses[{index}].branch_id", "is not declared in manifest")
            continue
        if item.get("status_path") != expected_entry["status_path"]:
            defect(defects, f"$.branch_statuses[{index}].status_path", "must match manifest branch status_path")
        if item.get("review_path") != expected_entry["review_path"]:
            defect(defects, f"$.branch_statuses[{index}].review_path", "must match manifest branch review_path")
    if root.get("status") in {"pass", "partial"}:
        missing = sorted(set(expected) - set(seen))
        extra = sorted(set(seen) - set(expected))
        if missing:
            defect(defects, "$.branch_statuses", f"missing manifest branch summaries: {', '.join(missing)}")
        if extra:
            defect(defects, "$.branch_statuses", f"contains branch summaries not declared in manifest: {', '.join(extra)}")


def validate_review_artifact(
    defects: list[str],
    data: object,
    expected_verdict: str,
    path: str,
    *,
    manifest: object,
    branch_id: str | None,
) -> None:
    review = require_object(defects, data, path)
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
        if key not in review:
            defect(defects, path, f"missing key: {key}")
    packet_id = require_string(defects, review.get("packet_id"), f"{path}.packet_id")
    if packet_id and not SAFE_REVIEW_PACKET_RE.fullmatch(packet_id):
        defect(defects, f"{path}.packet_id", "must be a safe packet id")
    if branch_id and packet_id and not packet_id.startswith(f"{branch_id}-R"):
        defect(defects, f"{path}.packet_id", f"must start with {branch_id}-R")
    if review.get("role") != "reviewer":
        defect(defects, f"{path}.role", "must be 'reviewer'")
    verdict = review.get("verdict")
    if verdict not in REVIEW_STATUSES - {"missing"}:
        defect(defects, f"{path}.verdict", f"must be one of {sorted(REVIEW_STATUSES - {'missing'})}")
    if expected_verdict != "missing" and verdict != expected_verdict:
        defect(defects, f"{path}.verdict", "must match branch summary review_status")
    require_string_list(defects, review.get("findings"), f"{path}.findings")
    validate_base_range_diff_check(defects, review.get("commands_run"), f"{path}.commands_run", manifest)
    verification_gaps = require_string_list(defects, review.get("verification_gaps"), f"{path}.verification_gaps")
    if verdict == "mergeable" and verification_gaps:
        defect(defects, f"{path}.verification_gaps", "must be empty when verdict is mergeable")
    require_string_list(defects, review.get("residual_risks"), f"{path}.residual_risks")
    require_string(defects, review.get("summary"), f"{path}.summary")


def validate_branch_artifacts(
    defects: list[str],
    root: dict,
    expected: dict[str, dict[str, str]],
    *,
    manifest: object,
    manifest_path: Path,
    require_artifacts: bool,
) -> None:
    branch_statuses = root.get("branch_statuses")
    if not isinstance(branch_statuses, list):
        return
    branch_validator = None
    for index, item in enumerate(branch_statuses):
        if not isinstance(item, dict):
            continue
        item_path = f"$.branch_statuses[{index}]"
        branch_id = item.get("branch_id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            continue
        expected_entry = expected.get(branch_id)
        if expected_entry is None:
            continue
        status_artifact = (manifest_path.parent / expected_entry["status_path"]).resolve()
        review_artifact = (manifest_path.parent / expected_entry["review_path"]).resolve()

        if not status_artifact.exists():
            defect(defects, f"{item_path}.status_path", f"artifact does not exist: {status_artifact}")
        else:
            branch_status = load_json_artifact(defects, status_artifact, f"{item_path}.status_path")
            if branch_validator is None:
                branch_validator = load_branch_status_validator(defects)
            if branch_validator is not None:
                branch_defects = branch_validator.validate_branch_status(
                    branch_status,
                    branch_id=branch_id,
                    branch=expected_entry.get("branch_name") or None,
                    worktree=None,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    status_path=status_artifact,
                )
                for branch_defect in branch_defects:
                    defect(defects, f"{item_path}.status_path", f"invalid branch status artifact: {branch_defect}")
            if isinstance(branch_status, dict):
                if branch_status.get("status") != item.get("status"):
                    defect(defects, f"{item_path}.status", "must match branch status artifact status")
                if branch_status.get("review_status") != item.get("review_status"):
                    defect(defects, f"{item_path}.review_status", "must match branch status artifact review_status")

        require_review_artifact = require_artifacts or item.get("review_status") != "missing"
        if not review_artifact.exists():
            if require_review_artifact:
                defect(defects, f"{item_path}.review_path", f"artifact does not exist: {review_artifact}")
        else:
            validate_review_artifact(
                defects,
                load_json_artifact(defects, review_artifact, f"{item_path}.review_path"),
                str(item.get("review_status", "")),
                f"{item_path}.review_path",
                manifest=manifest,
                branch_id=branch_id,
            )


def validate_main_status(data: object, *, job_id: str | None, manifest: object, manifest_path: Path) -> list[str]:
    defects: list[str] = []
    root = require_object(defects, data, "$")
    required = [
        "job_id",
        "status",
        "audit_status",
        "branch_statuses",
        "lite_advice",
        "commands_run",
        "dod_checklist",
        "blockers",
        "summary",
    ]
    for key in required:
        if key not in root:
            defect(defects, "$", f"missing key: {key}")
    if job_id and root.get("job_id") != job_id:
        defect(defects, "$.job_id", f"must be {job_id!r}")
    manifest_root = require_object(defects, manifest, "manifest")
    manifest_job_id = manifest_root.get("job_id")
    if isinstance(manifest_job_id, str) and manifest_job_id.strip() and root.get("job_id") != manifest_job_id:
        defect(defects, "$.job_id", f"must match manifest job_id {manifest_job_id!r}")
    expected_branches = expected_branches_from_manifest(defects, manifest)
    require_string(defects, root.get("job_id"), "$.job_id")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    audit_status = root.get("audit_status")
    if audit_status not in AUDIT_STATUSES:
        defect(defects, "$.audit_status", f"must be one of {sorted(AUDIT_STATUSES)}")
    if status == "pass" and audit_status != "pass":
        defect(defects, "$.audit_status", "must be pass when main status is pass")
    branch_statuses = root.get("branch_statuses")
    min_branches = 1 if status in {"pass", "partial"} else 0
    if not isinstance(branch_statuses, list) or len(branch_statuses) < min_branches or len(branch_statuses) > MAX_TOTAL_BRANCHES:
        defect(defects, "$.branch_statuses", f"must contain {min_branches} to {MAX_TOTAL_BRANCHES} item(s)")
    else:
        for index, item in enumerate(branch_statuses):
            validate_branch_summary(defects, item, f"$.branch_statuses[{index}]")
        if status == "pass":
            for index, item in enumerate(branch_statuses):
                if isinstance(item, dict) and item.get("status") != "pass":
                    defect(defects, f"$.branch_statuses[{index}].status", "must be pass when main status is pass")
    validate_manifest_branch_coverage(defects, root, expected_branches)
    validate_branch_artifacts(
        defects,
        root,
        expected_branches,
        manifest=manifest,
        manifest_path=manifest_path,
        require_artifacts=status == "pass",
    )
    validate_lite_advice_entries(defects, root.get("lite_advice"), "$.lite_advice", manifest_path=manifest_path)
    require_string_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    require_string_list(defects, root.get("dod_checklist"), "$.dod_checklist", min_items=1)
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    if status == "pass" and blockers:
        defect(defects, "$.blockers", "must be empty when status is pass")
    if status in {"partial", "blocked", "failed"} and not blockers:
        defect(defects, "$.blockers", "must explain non-pass status")
    require_string(defects, root.get("summary"), "$.summary")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--job-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status_path = resolve_absolute_path(args.status, "--status", must_exist=True)
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    defects = validate_main_status(
        load_json(status_path),
        job_id=args.job_id,
        manifest=load_json(manifest_path),
        manifest_path=manifest_path,
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
