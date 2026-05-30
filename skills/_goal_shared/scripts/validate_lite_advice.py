#!/usr/bin/env python3
"""Validate a CLI-only Lite advisory artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path


LITE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_APPROVAL_MODE = "plan"
GEMINI_COMMAND = "gemini"
LITE_STATUS_BEGIN = "BEGIN_LITE_ADVICE_JSON"
LITE_STATUS_END = "END_LITE_ADVICE_JSON"
SKILL_NAME_OVERRIDE: str | None = None
SCRIPT_DIR_OVERRIDE: Path | None = None
STATUSES = {"ok", "partial", "blocked"}
ALL_PURPOSES = {
    "preflight-decomposition",
    "lint-repair",
    "audit-defect-summary",
    "amendment-summary",
    "amendment-defect-summary",
    "branch-packet-planning",
    "context-pack",
    "worker-summary",
    "blocked-triage",
    "main-summary",
}
SKILL_PURPOSES = {
    "goal-preflight": {"preflight-decomposition", "lint-repair"},
    "goal-main-orchestrator": {"audit-defect-summary", "main-summary"},
    "goal-branch-orchestrator": {
        "branch-packet-planning",
        "context-pack",
        "worker-summary",
        "blocked-triage",
    },
    "goal-plan-amender": {"amendment-summary", "amendment-defect-summary"},
}
RISK_LABELS = {"unsupported", "unresolved", "negative", "weakened", "probe-only", "blocked"}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _load_path_rules():
    path = Path(__file__).resolve().parent / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
SAFE_LABEL_RE = PATH_RULES.SAFE_PACKET_LABEL_RE
resolve_absolute_path = PATH_RULES.resolve_absolute_path
is_relative_path = PATH_RULES.is_repo_relative_path


def current_skill_name() -> str:
    if SKILL_NAME_OVERRIDE is not None:
        return SKILL_NAME_OVERRIDE
    try:
        return Path(__file__).resolve().parents[1].name
    except IndexError:
        return ""


def allowed_purposes() -> set[str]:
    skill = current_skill_name()
    if skill not in SKILL_PURPOSES:
        raise SystemExit("Lite advice scripts must be run through a goal skill wrapper, not _goal_shared directly.")
    return SKILL_PURPOSES[skill]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def advice_command(gemini_path: str) -> str:
    command = gemini_path if gemini_path else GEMINI_COMMAND
    return f"{command} --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --skip-trust --output-format text"


def prompt_for(
    packet_id: str,
    purpose: str,
    base_dir: str,
    sources: list[dict],
    extra: str,
    *,
    skill: str,
    model: str,
    gemini_path: str,
    gemini_version: str,
    gemini_sha256: str,
    task_sha256: str,
) -> str:
    source_lines = "\n".join(
        f"- {item['path']} ({item['sha256']}, {item['size_bytes']} bytes)"
        for item in sources
    )
    example_sources = json.dumps(sources, indent=2, sort_keys=True)
    command = advice_command(gemini_path)
    return f"""# Lite Advisory Packet {packet_id}

You are a CLI-only Lite advisor. Do not edit files, create branches, create worktrees, run tests, or decide pass/fail. Your job is to route context cheaply for heavier agents.

Purpose: {purpose}
Base directory: {base_dir}

Deterministic envelope:
- Skill: {skill}
- Model: {model}
- Gemini path: {gemini_path if gemini_path else "unavailable"}
- Gemini version: {gemini_version}
- Gemini sha256: {gemini_sha256}
- Task guidance sha256: {task_sha256}

Read only these explicit input files:
{source_lines if source_lines else "- none"}

Policy:
- Lite output is advisory only.
- Do not decide mergeability, prompt-audit pass/fail, scientific claim support, or Definition-of-Done satisfaction.
- Preserve labels exactly when present: `unsupported`, `unresolved`, `negative`, `weakened`, `probe-only`, `blocked`.
- Recommend targeted original reads with path, anchor, and reason. Do not tell heavy agents to reread every source file by default.
- For any purpose other than `preflight-decomposition`, `recommended_reads` may cite only the explicit input files listed above.
- Use focused context. Do not broaden beyond the listed files unless the purpose is `preflight-decomposition`; even then, only recommend additional paths rather than reading the whole repository.
- If an input file is missing, unreadable, stale, or insufficient, return `status: "blocked"` or `status: "partial"` with blockers.

Additional task guidance:
{extra.strip() if extra.strip() else "- No extra guidance."}

Return exactly one JSON object between these marker lines. Do not print any other JSON object between them. The `source_files` array must echo this exact metadata for every listed input file:

{LITE_STATUS_BEGIN}
{{
  "packet_id": "{packet_id}",
  "role": "lite_advisor",
  "purpose": "{purpose}",
  "status": "ok",
  "source_files": {example_sources},
  "recommended_reads": [],
  "risk_flags": [],
  "advice": {{}},
  "summary": "replace with concise advisory summary",
  "blockers": [],
  "commands_run": [{json.dumps(command)}]
}}
{LITE_STATUS_END}
"""


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


def require_nonnegative_int(defects: list[str], value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        defect(defects, path, "must be a non-negative integer")
        return 0
    return value


def validate_telemetry(defects: list[str], inputs_path: Path | None, *, packet_id: str, lite_status: object) -> None:
    if inputs_path is None:
        defect(defects, "telemetry.json", "requires --inputs so packet telemetry can be verified")
        return
    telemetry_path = inputs_path.parent / "telemetry.json"
    if not telemetry_path.exists():
        defect(defects, "telemetry.json", f"must exist next to advice.json: {telemetry_path}")
        return
    try:
        data = load_json(telemetry_path)
    except Exception as exc:  # noqa: BLE001
        defect(defects, "telemetry.json", f"must be readable JSON: {exc}")
        return
    root = require_object(defects, data, "telemetry.json")
    if root.get("schema_version") != 1:
        defect(defects, "telemetry.json.schema_version", "must be 1")
    if root.get("packet_id") != packet_id:
        defect(defects, "telemetry.json.packet_id", f"must be {packet_id!r}")
    if root.get("role") != "lite_advisor":
        defect(defects, "telemetry.json.role", "must be 'lite_advisor'")
    for key in [
        "prompt_chars",
        "prompt_bytes",
        "output_chars",
        "output_bytes",
        "event_log_chars",
        "event_log_bytes",
    ]:
        require_nonnegative_int(defects, root.get(key), f"telemetry.json.{key}")
    attempts = root.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1:
        defect(defects, "telemetry.json.attempts", "must contain exactly one Lite attempt")
        return
    attempt = require_object(defects, attempts[0], "telemetry.json.attempts[0]")
    if attempt.get("alias") != "gemini-lite":
        defect(defects, "telemetry.json.attempts[0].alias", "must be 'gemini-lite'")
    if attempt.get("provider") != "gemini":
        defect(defects, "telemetry.json.attempts[0].provider", "must be 'gemini'")
    if attempt.get("model") != LITE_MODEL:
        defect(defects, "telemetry.json.attempts[0].model", f"must be {LITE_MODEL!r}")
    if not isinstance(attempt.get("called"), bool):
        defect(defects, "telemetry.json.attempts[0].called", "must be a boolean")
    if not isinstance(attempt.get("accepted"), bool):
        defect(defects, "telemetry.json.attempts[0].accepted", "must be a boolean")
    if lite_status == "ok" and attempt.get("called") is not True:
        defect(defects, "telemetry.json.attempts[0].called", "must be true when Lite advice status is ok")
    if attempt.get("accepted") is True and attempt.get("called") is not True:
        defect(defects, "telemetry.json.attempts[0].accepted", "may be true only when called is true")


def validate_live_sources(defects: list[str], inputs: dict | None) -> None:
    if inputs is None:
        return
    base_dir_value = inputs.get("base_dir")
    if not isinstance(base_dir_value, str) or not base_dir_value.strip():
        defect(defects, "input-files.json.base_dir", "must be a non-empty absolute path")
        return
    base_dir = Path(base_dir_value)
    if not base_dir.is_absolute() or not base_dir.exists():
        defect(defects, "input-files.json.base_dir", f"must exist as an absolute path: {base_dir_value!r}")
        return
    source_files = inputs.get("source_files")
    if not isinstance(source_files, list):
        defect(defects, "input-files.json.source_files", "must be an array")
        return
    base_dir = base_dir.resolve()
    for index, item in enumerate(source_files):
        if not isinstance(item, dict):
            defect(defects, f"input-files.json.source_files[{index}]", "must be an object")
            continue
        rel_path = item.get("path")
        if not isinstance(rel_path, str) or not is_relative_path(rel_path):
            defect(defects, f"input-files.json.source_files[{index}].path", "must be relative without traversal")
            continue
        source_path = (base_dir / rel_path).resolve()
        try:
            source_path.relative_to(base_dir)
        except ValueError:
            defect(defects, f"input-files.json.source_files[{index}].path", "must stay inside base_dir")
            continue
        if not source_path.exists():
            defect(defects, f"input-files.json.source_files[{index}].path", f"does not exist: {source_path}")
            continue
        actual_hash = sha256_file(source_path)
        actual_size = source_path.stat().st_size
        if actual_hash != item.get("sha256") or actual_size != item.get("size_bytes"):
            defect(
                defects,
                f"input-files.json.source_files[{index}]",
                f"stale source metadata for {rel_path}: expected {item.get('sha256')}/{item.get('size_bytes')}, got {actual_hash}/{actual_size}",
            )


def validate_gemini_envelope(defects: list[str], inputs: dict, *, lite_status: object) -> None:
    gemini_path_value = inputs.get("gemini_path")
    gemini_version = inputs.get("gemini_version")
    gemini_sha256 = inputs.get("gemini_sha256")
    blocked = lite_status == "blocked"
    if gemini_path_value == "" and gemini_version == "unavailable" and gemini_sha256 == "unavailable":
        if not blocked:
            defect(defects, "input-files.json.gemini_path", "may be unavailable only for blocked Lite advice")
        return
    if not isinstance(gemini_path_value, str) or not gemini_path_value.strip():
        defect(defects, "input-files.json.gemini_path", "must be a non-empty absolute path or unavailable for blocked advice")
        return
    gemini_path = Path(gemini_path_value)
    if "\\" in gemini_path_value or not gemini_path.is_absolute() or ".." in gemini_path.parts:
        defect(defects, "input-files.json.gemini_path", "must be an absolute path without traversal")
        return
    if not isinstance(gemini_sha256, str) or not SHA256_RE.fullmatch(gemini_sha256):
        defect(defects, "input-files.json.gemini_sha256", "must be sha256:<64 lowercase hex chars>")
    if not isinstance(gemini_version, str) or not gemini_version.strip() or gemini_version == "unavailable":
        defect(defects, "input-files.json.gemini_version", "must be the captured Gemini CLI version")
    if blocked:
        return
    if not gemini_path.exists() or not os.access(gemini_path, os.X_OK):
        defect(defects, "input-files.json.gemini_path", f"must exist and be executable: {gemini_path}")
        return
    actual_hash = sha256_file(gemini_path)
    if actual_hash != gemini_sha256:
        defect(defects, "input-files.json.gemini_sha256", f"stale Gemini binary metadata: expected {gemini_sha256}, got {actual_hash}")
    try:
        completed = subprocess.run(
            [gemini_path.as_posix(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        defect(defects, "input-files.json.gemini_version", f"could not recheck Gemini version: {exc}")
        return
    version_lines = (completed.stdout or completed.stderr).strip().splitlines()
    actual_version = version_lines[0] if version_lines else "version-unavailable"
    if actual_version != gemini_version:
        defect(defects, "input-files.json.gemini_version", f"stale Gemini version metadata: expected {gemini_version!r}, got {actual_version!r}")


def validate_inputs_envelope(
    defects: list[str],
    inputs: dict | None,
    *,
    packet_id: str,
    purpose: str,
    lite_status: object,
) -> None:
    if inputs is None:
        return
    input_packet_id = inputs.get("packet_id")
    if input_packet_id != packet_id:
        defect(defects, "input-files.json.packet_id", f"must match advice packet_id {packet_id!r}")
    if isinstance(input_packet_id, str) and not SAFE_LABEL_RE.fullmatch(input_packet_id):
        defect(defects, "input-files.json.packet_id", "must be a safe packet id")
    input_purpose = inputs.get("purpose")
    if input_purpose != purpose:
        defect(defects, "input-files.json.purpose", f"must match advice purpose {purpose!r}")
    if isinstance(input_purpose, str) and input_purpose not in allowed_purposes():
        defect(defects, "input-files.json.purpose", f"not allowed for {current_skill_name()}: {input_purpose!r}")
    skill = inputs.get("skill")
    if skill != current_skill_name():
        defect(defects, "input-files.json.skill", f"must be {current_skill_name()!r}")
    if inputs.get("model") != LITE_MODEL:
        defect(defects, "input-files.json.model", f"must be {LITE_MODEL!r}")
    task_sha256 = inputs.get("task_sha256")
    if not isinstance(task_sha256, str) or not SHA256_RE.fullmatch(task_sha256):
        defect(defects, "input-files.json.task_sha256", "must be sha256:<64 lowercase hex chars>")
    validate_gemini_envelope(defects, inputs, lite_status=lite_status)


def validate_prompt_hash(defects: list[str], inputs: dict | None, inputs_path: Path | None) -> None:
    if inputs is None:
        return
    expected = inputs.get("prompt_sha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        defect(defects, "input-files.json.prompt_sha256", "must be sha256:<64 lowercase hex chars>")
        return
    if inputs_path is None:
        defect(defects, "input-files.json.prompt_sha256", "requires --inputs so prompt.md can be verified")
        return
    prompt_path = inputs_path.parent / "prompt.md"
    if not prompt_path.exists():
        defect(defects, "prompt.md", f"must exist next to input-files.json: {prompt_path}")
        return
    task_path = inputs_path.parent / "task.md"
    if not task_path.exists():
        defect(defects, "task.md", f"must exist next to input-files.json: {task_path}")
        return
    task_text = task_path.read_text(encoding="utf-8")
    task_sha256 = inputs.get("task_sha256")
    if isinstance(task_sha256, str) and SHA256_RE.fullmatch(task_sha256):
        actual_task = sha256_text(task_text)
        if actual_task != task_sha256:
            defect(defects, "task.md", f"stale task metadata: expected {task_sha256}, got {actual_task}")
    source_files = inputs.get("source_files")
    if not isinstance(source_files, list):
        source_files = []
    regenerated = prompt_for(
        str(inputs.get("packet_id", "")),
        str(inputs.get("purpose", "")),
        str(inputs.get("base_dir", "")),
        source_files,
        task_text,
        skill=str(inputs.get("skill", "")),
        model=str(inputs.get("model", "")),
        gemini_path=str(inputs.get("gemini_path", "")),
        gemini_version=str(inputs.get("gemini_version", "")),
        gemini_sha256=str(inputs.get("gemini_sha256", "")),
        task_sha256=str(inputs.get("task_sha256", "")),
    )
    regenerated_hash = sha256_text(regenerated)
    if regenerated_hash != expected:
        defect(defects, "input-files.json.prompt_sha256", f"must match regenerated prompt from input-files.json/task.md: got {regenerated_hash}")
    actual_text = prompt_path.read_text(encoding="utf-8")
    actual = sha256_text(actual_text)
    if actual != expected:
        defect(defects, "prompt.md", f"stale prompt metadata: expected {expected}, got {actual}")
    if actual_text != regenerated:
        defect(defects, "prompt.md", "must match deterministic prompt regenerated from input-files.json and task.md")


def validate_source_files(defects: list[str], value: object, path: str, expected: list[dict] | None) -> list[str]:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return []
    seen = set()
    actual = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        source_path = require_string(defects, data.get("path"), f"{item_path}.path")
        sha256 = require_string(defects, data.get("sha256"), f"{item_path}.sha256")
        size_bytes = data.get("size_bytes")
        reason = require_string(defects, data.get("reason"), f"{item_path}.reason")
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
                "reason": reason,
            }
        )
    if expected is not None:
        expected_min = [
            {
                    "path": item.get("path"),
                    "sha256": item.get("sha256"),
                    "size_bytes": item.get("size_bytes"),
                    "reason": item.get("reason"),
                }
            for item in expected
        ]
        if actual != expected_min:
            defect(defects, path, "must match input-files.json source metadata exactly and in order")
    return [item["path"] for item in actual if isinstance(item.get("path"), str)]


def validate_recommended_reads(
    defects: list[str],
    value: object,
    path: str,
    *,
    purpose: str,
    source_paths: set[str],
) -> None:
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
        if target and purpose != "preflight-decomposition" and target not in source_paths:
            defect(defects, f"{item_path}.path", "must reference an explicit Lite input for this purpose")


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


def validate(
    data: object,
    *,
    packet_id: str | None,
    purpose: str | None,
    expected_sources: list[dict] | None,
    inputs: dict | None,
    inputs_path: Path | None = None,
) -> list[str]:
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
    if actual_purpose and actual_purpose not in ALL_PURPOSES:
        defect(defects, "$.purpose", f"must be one of {sorted(ALL_PURPOSES)}")
    if actual_purpose and actual_purpose not in allowed_purposes():
        defect(defects, "$.purpose", f"not allowed for {current_skill_name()}: {actual_purpose!r}")
    if purpose and actual_purpose != purpose:
        defect(defects, "$.purpose", f"must be {purpose!r}")
    status = root.get("status")
    if status not in STATUSES:
        defect(defects, "$.status", f"must be one of {sorted(STATUSES)}")
    validate_inputs_envelope(defects, inputs, packet_id=actual_packet_id, purpose=actual_purpose, lite_status=status)
    validate_live_sources(defects, inputs)
    validate_prompt_hash(defects, inputs, inputs_path)
    source_paths = set(validate_source_files(defects, root.get("source_files"), "$.source_files", expected_sources))
    validate_recommended_reads(
        defects,
        root.get("recommended_reads"),
        "$.recommended_reads",
        purpose=actual_purpose,
        source_paths=source_paths,
    )
    validate_risk_flags(defects, root.get("risk_flags"), "$.risk_flags")
    if not isinstance(root.get("advice"), dict):
        defect(defects, "$.advice", "must be an object")
    require_string(defects, root.get("summary"), "$.summary")
    blockers = require_string_list(defects, root.get("blockers"), "$.blockers")
    commands = require_string_list(defects, root.get("commands_run"), "$.commands_run", min_items=1)
    if status == "ok" and blockers:
        defect(defects, "$.blockers", "must be empty when status is ok")
    if status in {"partial", "blocked"} and not blockers:
        defect(defects, "$.blockers", "must explain non-ok Lite advice")
    validate_telemetry(defects, inputs_path, packet_id=actual_packet_id, lite_status=status)
    if inputs is not None:
        expected_command = advice_command(str(inputs.get("gemini_path", "")))
        if commands and expected_command not in commands:
            defect(defects, "$.commands_run", f"must record exact Lite command {expected_command!r}")
    elif commands and not any(LITE_MODEL in command and f"--approval-mode {GEMINI_APPROVAL_MODE}" in command for command in commands):
        defect(defects, "$.commands_run", "must record the fixed Lite model and approval mode")
    return defects


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--advice", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--packet-id")
    parser.add_argument("--purpose", choices=sorted(allowed_purposes()))
    args = parser.parse_args()

    advice_path = resolve_absolute_path(args.advice, "--advice", must_exist=True)
    inputs_path = resolve_absolute_path(args.inputs, "--inputs", must_exist=True)
    expected_sources = None
    inputs = None
    if inputs_path:
        loaded_inputs = load_json(inputs_path)
        inputs = loaded_inputs if isinstance(loaded_inputs, dict) else None
        if inputs is None:
            raise SystemExit("--inputs must point to a JSON object")
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
        inputs=inputs,
        inputs_path=inputs_path,
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
