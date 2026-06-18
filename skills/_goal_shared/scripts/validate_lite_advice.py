#!/usr/bin/env python3
"""Validate a CLI-only Lite advisory artifact."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path


LITE_ROUTE_ALIAS = "ds-flash-max"
LITE_PERMISSION_PROFILE = "read-only"
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


def _load_shared_module(filename: str, module_name: str, label: str):
    path = Path(__file__).resolve().parent / filename
    if not path.exists():
        raise SystemExit(f"missing {label}: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {label}: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_path_rules():
    return _load_shared_module("path_rules.py", "goal_shared_path_rules", "shared path rules")


def _load_contract():
    return _load_shared_module(
        "orchestration_contract.py", "goal_shared_orchestration_contract", "shared orchestration contract"
    )


def _load_lite_prompt():
    # Resolved next to this shared module so create and validate share ONE prompt
    # builder (Path(__file__) points at _goal_shared even through a wrapper).
    return _load_shared_module("lite_prompt.py", "goal_shared_lite_prompt", "shared lite prompt builder")


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
LITE_PROMPT = _load_lite_prompt()
SAFE_LABEL_RE = PATH_RULES.SAFE_PACKET_LABEL_RE
resolve_absolute_path = PATH_RULES.resolve_absolute_path
is_relative_path = PATH_RULES.is_repo_relative_path
build_lite_prompt = LITE_PROMPT.build_lite_prompt
bridge_advice_command = LITE_PROMPT.bridge_advice_command
LITE_STATUS_BEGIN = LITE_PROMPT.LITE_STATUS_BEGIN
LITE_STATUS_END = LITE_PROMPT.LITE_STATUS_END
BRIDGE_PROVIDER_ID = CONTRACT.BRIDGE_PROVIDER_ID
BRIDGE_HARNESS_KIND = CONTRACT.BRIDGE_HARNESS_KIND
LITE_MODEL = CONTRACT.bridge_model(LITE_ROUTE_ALIAS)
LITE_VARIANT = CONTRACT.bridge_variant(LITE_ROUTE_ALIAS)


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


def advice_command(control_script: str) -> str:
    return bridge_advice_command(
        control_script=control_script,
        provider=BRIDGE_PROVIDER_ID,
        model=LITE_MODEL,
        variant=LITE_VARIANT,
        permission_profile=LITE_PERMISSION_PROFILE,
    )


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
    if attempt.get("alias") != LITE_ROUTE_ALIAS:
        defect(defects, "telemetry.json.attempts[0].alias", f"must be {LITE_ROUTE_ALIAS!r}")
    if attempt.get("provider") != BRIDGE_HARNESS_KIND:
        defect(defects, "telemetry.json.attempts[0].provider", f"must be {BRIDGE_HARNESS_KIND!r}")
    if attempt.get("model") != LITE_MODEL:
        defect(defects, "telemetry.json.attempts[0].model", f"must be {LITE_MODEL!r}")
    if attempt.get("variant") not in (None, LITE_VARIANT):
        defect(defects, "telemetry.json.attempts[0].variant", f"must be {LITE_VARIANT!r}")
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


def validate_bridge_envelope(defects: list[str], inputs: dict, *, lite_status: object) -> None:
    """Validate the recorded opencode-worker-bridge / deepseek invocation envelope.

    No live deepseek delegate is invoked here (mirrors B4's no-network telemetry
    handling); the validator checks shape + that the control script still exists
    when it was recorded as available. A blocked Lite packet may legitimately
    record an unavailable bridge control script.
    """
    control_script = inputs.get("bridge_control_script")
    control_version = inputs.get("bridge_control_version")
    provider = inputs.get("provider")
    variant = inputs.get("variant")
    permission_profile = inputs.get("permission_profile")
    blocked = lite_status == "blocked"
    if provider != BRIDGE_PROVIDER_ID:
        defect(defects, "input-files.json.provider", f"must be {BRIDGE_PROVIDER_ID!r}")
    if variant != LITE_VARIANT:
        defect(defects, "input-files.json.variant", f"must be {LITE_VARIANT!r}")
    if permission_profile != LITE_PERMISSION_PROFILE:
        defect(defects, "input-files.json.permission_profile", f"must be {LITE_PERMISSION_PROFILE!r}")
    if control_script == "" and control_version == "unavailable":
        if not blocked:
            defect(defects, "input-files.json.bridge_control_script", "may be unavailable only for blocked Lite advice")
        return
    if not isinstance(control_script, str) or not control_script.strip():
        defect(
            defects,
            "input-files.json.bridge_control_script",
            "must be a non-empty absolute path or unavailable for blocked advice",
        )
        return
    control_path = Path(control_script)
    if "\\" in control_script or not control_path.is_absolute() or ".." in control_path.parts:
        defect(defects, "input-files.json.bridge_control_script", "must be an absolute path without traversal")
        return
    if control_path.name != "opencode_worker.py":
        defect(
            defects,
            "input-files.json.bridge_control_script",
            "must point at the bridge control script opencode_worker.py",
        )
    if not isinstance(control_version, str) or not control_version.strip() or control_version == "unavailable":
        defect(defects, "input-files.json.bridge_control_version", "must be the captured bridge control-script version")
    if blocked:
        return
    if not control_path.exists():
        defect(defects, "input-files.json.bridge_control_script", f"must exist: {control_path}")


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
    require_string(defects, inputs.get("avoids_action"), "input-files.json.avoids_action")
    require_string(defects, inputs.get("expected_savings_reason"), "input-files.json.expected_savings_reason")
    skill = inputs.get("skill")
    if skill != current_skill_name():
        defect(defects, "input-files.json.skill", f"must be {current_skill_name()!r}")
    if inputs.get("model") != LITE_MODEL:
        defect(defects, "input-files.json.model", f"must be {LITE_MODEL!r}")
    if inputs.get("alias") not in (None, LITE_ROUTE_ALIAS):
        defect(defects, "input-files.json.alias", f"must be {LITE_ROUTE_ALIAS!r}")
    task_sha256 = inputs.get("task_sha256")
    if not isinstance(task_sha256, str) or not SHA256_RE.fullmatch(task_sha256):
        defect(defects, "input-files.json.task_sha256", "must be sha256:<64 lowercase hex chars>")
    validate_bridge_envelope(defects, inputs, lite_status=lite_status)


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
    # errors="replace": a non-UTF-8 task.md must produce a stale-hash defect, not a
    # UnicodeDecodeError — this validator is also run in-process by status_validation.
    task_text = task_path.read_text(encoding="utf-8", errors="replace")
    task_sha256 = inputs.get("task_sha256")
    if isinstance(task_sha256, str) and SHA256_RE.fullmatch(task_sha256):
        actual_task = sha256_text(task_text)
        if actual_task != task_sha256:
            defect(defects, "task.md", f"stale task metadata: expected {task_sha256}, got {actual_task}")
    source_files = inputs.get("source_files")
    if not isinstance(source_files, list):
        source_files = []
    # Regenerate with the SAME shared builder create used, fed from the recorded
    # envelope, then compare hashes. This is the determinism guarantee P1 fixes.
    regenerated = build_lite_prompt(
        str(inputs.get("packet_id", "")),
        str(inputs.get("purpose", "")),
        str(inputs.get("base_dir", "")),
        source_files,
        task_text,
        skill=str(inputs.get("skill", "")),
        model=str(inputs.get("model", "")),
        provider=str(inputs.get("provider", "")),
        variant=str(inputs.get("variant", "")),
        control_script=str(inputs.get("bridge_control_script", "")),
        control_version=str(inputs.get("bridge_control_version", "")),
        permission_profile=str(inputs.get("permission_profile", "")),
        task_sha256=str(inputs.get("task_sha256", "")),
        avoids_action=str(inputs.get("avoids_action", "")),
        expected_savings_reason=str(inputs.get("expected_savings_reason", "")),
    )
    regenerated_hash = sha256_text(regenerated)
    if regenerated_hash != expected:
        defect(
            defects,
            "input-files.json.prompt_sha256",
            f"must match regenerated prompt from input-files.json/task.md: got {regenerated_hash}",
        )
    actual_text = prompt_path.read_text(encoding="utf-8", errors="replace")
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
        "avoids_action",
        "expected_savings_reason",
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
    avoids_action = require_string(defects, root.get("avoids_action"), "$.avoids_action")
    expected_savings_reason = require_string(defects, root.get("expected_savings_reason"), "$.expected_savings_reason")
    if inputs is not None:
        if avoids_action and avoids_action != inputs.get("avoids_action"):
            defect(defects, "$.avoids_action", "must match input-files.json avoids_action")
        if expected_savings_reason and expected_savings_reason != inputs.get("expected_savings_reason"):
            defect(defects, "$.expected_savings_reason", "must match input-files.json expected_savings_reason")
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
        expected_command = advice_command(str(inputs.get("bridge_control_script", "")))
        if commands and expected_command not in commands:
            defect(defects, "$.commands_run", f"must record exact Lite command {expected_command!r}")
    elif commands and not any(
        LITE_MODEL in command and f"--permission-profile {LITE_PERMISSION_PROFILE}" in command for command in commands
    ):
        defect(defects, "$.commands_run", "must record the fixed Lite deepseek model and read-only permission profile")
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
        try:
            loaded_inputs = load_json(inputs_path)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SystemExit(f"--inputs is not valid JSON: {exc}") from exc
        inputs = loaded_inputs if isinstance(loaded_inputs, dict) else None
        if inputs is None:
            raise SystemExit("--inputs must point to a JSON object")
        expected_sources = inputs.get("source_files") if isinstance(inputs.get("source_files"), list) else []
        if not args.packet_id:
            args.packet_id = inputs.get("packet_id")
        if not args.purpose:
            args.purpose = inputs.get("purpose")

    try:
        advice_data = load_json(advice_path)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"--advice is not valid JSON: {exc}") from exc
    defects = validate(
        advice_data,
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
