#!/usr/bin/env python3
"""Deterministically lint a goal preflight bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import re
from pathlib import Path


PREFLIGHT_LITE_PURPOSES = {"preflight-decomposition", "lint-repair"}
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
SAFE_ID_RE = PATH_RULES.SAFE_ID_RE
SAFE_LABEL_RE = PATH_RULES.SAFE_LABEL_RE
is_strict_int = PATH_RULES.is_strict_int
resolve_absolute_path = PATH_RULES.resolve_absolute_path
resolve = PATH_RULES.resolve
relative_path_defect = PATH_RULES.relative_path_defect
safe_branch_name = PATH_RULES.safe_branch_name
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
MAX_WAVES = CONTRACT.MAX_WAVES
DEFAULT_TOTAL_BRANCH_CAP = CONTRACT.DEFAULT_TOTAL_BRANCH_CAP
DEFAULT_WORKER_LADDER = CONTRACT.worker_ladder_list()
MANIFEST_WORKER_ROUTE_CLASSES = CONTRACT.MANIFEST_WORKER_ROUTE_CLASSES
AMENDER_MODEL_POLICY = CONTRACT.AMENDER_MODEL_POLICY
LITE_MODEL_POLICY = CONTRACT.LITE_MODEL_POLICY
LITE_ADVISOR_POLICY = CONTRACT.LITE_ADVISOR_POLICY
RESEARCH_WORKER_TYPE = CONTRACT.RESEARCH_WORKER_TYPE
REVIEW_MODEL_POLICY = CONTRACT.REVIEW_MODEL_POLICY
ORCHESTRATION_WATCHDOG = CONTRACT.ORCHESTRATION_WATCHDOG
TELEMETRY_POLICY_SCHEMA_VERSION = CONTRACT.TELEMETRY_POLICY_SCHEMA_VERSION
TELEMETRY_POLICY_MODES = CONTRACT.TELEMETRY_POLICY_MODES
TELEMETRY_COLLECT_ITEMS = CONTRACT.TELEMETRY_COLLECT_ITEMS
VALIDATOR_COMMAND_STATUS_HINTS = {
    "validate_branch_status.py": "--status /absolute/path/to/bundle/branches/Bxx.status.json",
    "validate_main_status.py": "--status /absolute/path/to/bundle/main.status.json",
}
VALIDATOR_COMMAND_RE = re.compile(r"(?P<script>validate_(?:branch|main)_status\.py)\b(?P<tail>[^`\n]*)")
STATUS_TARGET_PREFIXES = (
    "/absolute/path/to/bundle/",
    "/absolute/path/to/",
    "/abs/bundle/",
    "/abs/",
)
REQUIRED_BUNDLE_DIRS = ("branches", "workers", "research", "reviewers", "audit", "lite", "schedulers", "amendments")
MANIFEST_REQUIRED_KEYS = (
    "job_id",
    "main_prompt",
    "base_ref",
    "artifact_policy",
    "cleanup_policy",
    "branches",
    "waves",
    "max_active_branch_agents",
    "parallelization",
    "adaptation_policy",
    "worker_model_policy",
    "amender_model_policy",
    "lite_model_policy",
    "lite_advisor_policy",
    "review_model_policy",
    "orchestration_watchdog",
    "preflight_lite_advice",
)
MAIN_PROMPT_REQUIRED_PHRASES = (
    "manifest paths",
    "repository root",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "skill availability bootstrap",
    "prompt audit",
    "prompt-audit.json",
    "pins this manifest",
    "max_active_branch_agents",
    CONTRACT.MAIN_SCHEDULER_PATH,
    "branch_parallelism.scheduler_path",
    "Parallelism is the default",
    "never exceed 4",
    "Saturate branch orchestrator slots",
    "Launch the next eligible branch",
    "depends_on",
    "waves as scheduling/order groups",
    "do not poll active branch",
    "git diff --check",
    "Cleanup Policy",
    "Artifact Policy",
    "close finished branch orchestrator agents",
    "rolling saturated pool",
    "scheduler ledger",
    "orchestration_watchdog.main_no_completion_wait_limit",
    "validate_branch_status.py --manifest",
    "summarize_telemetry.py --bundle-dir",
    "telemetry.summary.json",
    "validate_main_status.py --manifest",
    "Optional Lite advisors",
)
BOOTLOADER_REQUIRED_PHRASES = (
    "$goal-main-orchestrator",
    "Bundle root",
    "Repository root",
    "job.manifest.json",
    "main.prompt.md",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "skill availability",
    "check_goal_skill_availability.py",
    "absolute paths",
    "pins the manifest",
    "Parallelism is the default",
    "never exceed 4",
    "branch orchestrator slots saturated",
    "depends_on",
    "Waves are scheduling/order groups",
    "1 to 4 worker packets",
    "rolling saturated pool",
)
BRANCH_REQUIRED_KEYS = (
    "id",
    "wave",
    "prompt",
    "branch_name",
    "worktree_path",
    "status_path",
    "review_path",
    "pre_review_gate_path",
    "depends_on",
    "owned_paths",
    "work_items",
    "max_active_worker_packets",
    "worker_parallelism",
)
BRANCH_PROMPT_PHRASES_BEFORE_SCHEDULER = (
    "Objective",
    "Scope",
    "Depends on branches",
    "Work Items",
    "Reviewer Requirement",
    "Bootstrap Requirement",
    "Worker Parallelism",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "Max active worker packets",
    "Max worker packets for this branch",
    "Never exceed",
    "active worker packets",
    "rolling saturated pool",
    "render_worker_schedule.py",
)
BRANCH_PROMPT_PHRASES_AFTER_SCHEDULER = (
    "worker_parallelism.scheduler_path",
    "Worker parallelization rationale",
    "Worker Model Routing",
    "Selected worker ladders",
    "Worker packet id",
    "Route class reason",
    "telemetry.json",
    "Lite Advisors",
    "orchestration_watchdog.branch_no_completion_wait_limit",
    "validate_branch_status.py --manifest",
    "Stop Conditions",
    "git diff --check",
    "pre_review_gate.json",
    "semantic_input_hashes",
    "do not poll active",
)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def has_dod(text: str) -> bool:
    lowered = text.lower()
    if "definition of done" not in lowered:
        return False
    after = lowered.split("definition of done", 1)[1]
    return "- " in after


def canonical_status_target(value: str) -> str:
    target = value.strip().strip("`'\"").rstrip(".,;)")
    for prefix in STATUS_TARGET_PREFIXES:
        if target.startswith(prefix):
            target = target[len(prefix):]
            break
    if target.startswith("branches/"):
        return target
    if "/branches/" in target:
        return "branches/" + target.rsplit("/branches/", 1)[1]
    if target.endswith("/main.status.json"):
        return "main.status.json"
    return target


def status_target_from_line(script_name: str, line: str) -> str | None:
    for match in VALIDATOR_COMMAND_RE.finditer(line):
        if match.group("script") != script_name:
            continue
        snippet = script_name + match.group("tail")
        try:
            tokens = shlex.split(snippet)
        except ValueError:
            return None
        for index, token in enumerate(tokens):
            if token == "--status" and index + 1 < len(tokens):
                return canonical_status_target(tokens[index + 1])
    return None


def lint_validator_command_snippets(defect, path: str, text: str, expected_status_targets: dict[str, str]) -> None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        for script_name, expected_target in expected_status_targets.items():
            status_hint = VALIDATOR_COMMAND_STATUS_HINTS[script_name]
            if f"{script_name} --manifest" not in line:
                continue
            actual_target = status_target_from_line(script_name, line)
            if actual_target != expected_target:
                if actual_target:
                    action = f"use {expected_target} as status target"
                else:
                    action = f"include {status_hint}"
                defect(
                    path,
                    "major",
                    f"line {lineno}: {script_name} command snippet must {action} on same line",
                )
                continue


def require_text_phrases(
    defect,
    path: str,
    text: str,
    phrases: tuple[str, ...],
    *,
    severity: str,
    message_prefix: str,
    case_sensitive: bool = False,
) -> None:
    haystack = text if case_sensitive else text.lower()
    for phrase in phrases:
        needle = phrase if case_sensitive else phrase.lower()
        if needle not in haystack:
            defect(path, severity, f"{message_prefix}: {phrase}")


def branch_prompt_required_phrases(branch_id: object) -> tuple[str, ...]:
    scheduler_path = CONTRACT.worker_scheduler_path(str(branch_id)) if isinstance(branch_id, str) else "worker scheduler"
    return (
        *BRANCH_PROMPT_PHRASES_BEFORE_SCHEDULER,
        scheduler_path,
        *BRANCH_PROMPT_PHRASES_AFTER_SCHEDULER,
    )


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


def paths_overlap(left: str, right: str) -> bool:
    left_norm = left.rstrip("/")
    right_norm = right.rstrip("/")
    return left_norm == right_norm or left_norm.startswith(right_norm + "/") or right_norm.startswith(left_norm + "/")


def has_contention_reason(*values: object) -> bool:
    for value in values:
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value):
            return True
    return False


def ready_branch_count(branches: list[dict]) -> int:
    return len([branch for branch in branches if isinstance(branch, dict) and not branch.get("depends_on")])


def longest_branch_chain(branches: list[dict]) -> int:
    lengths: dict[str, int] = {}
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        deps = branch.get("depends_on", []) if isinstance(branch.get("depends_on"), list) else []
        lengths[branch["id"]] = 1 + max([lengths.get(dep, 1) for dep in deps if isinstance(dep, str)] or [0])
    return max(lengths.values(), default=0)


def ready_worker_count(work_items: list[dict]) -> int:
    return len([item for item in work_items if isinstance(item, dict) and not item.get("depends_on")])


def longest_worker_chain(work_items: list[dict]) -> int:
    lengths: dict[str, int] = {}
    for item in work_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        deps = item.get("depends_on", []) if isinstance(item.get("depends_on"), list) else []
        lengths[item["id"]] = 1 + max([lengths.get(dep, 1) for dep in deps if isinstance(dep, str)] or [0])
    return max(lengths.values(), default=0)


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
            "avoids_action",
            "expected_savings_reason",
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
        if not isinstance(record.get("avoids_action"), str) or not record.get("avoids_action", "").strip():
            defect("job.manifest.json", "critical", f"{path}.avoids_action must be a non-empty string")
        if not isinstance(record.get("expected_savings_reason"), str) or not record.get("expected_savings_reason", "").strip():
            defect("job.manifest.json", "critical", f"{path}.expected_savings_reason must be a non-empty string")
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
        if record.get("avoids_action") != inputs_data.get("avoids_action"):
            defect("job.manifest.json", "critical", f"{path}.avoids_action must match input-files.json")
        if record.get("expected_savings_reason") != inputs_data.get("expected_savings_reason"):
            defect("job.manifest.json", "critical", f"{path}.expected_savings_reason must match input-files.json")
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
            defect(
                "job.manifest.json",
                "critical",
                f"unrecorded manifest-owned preflight Lite packet: {packet_id} at {packet_dir}",
            )


def validate_telemetry_policy(defect, manifest: dict) -> None:
    policy = manifest.get("telemetry_policy")
    if policy is None:
        defect(
            "job.manifest.json",
            "warning",
            "telemetry_policy is missing; assuming {\"schema_version\": 1, \"mode\": \"standard\", \"raw_text\": false, \"collect\": []}",
        )
        return
    if not isinstance(policy, dict):
        defect("job.manifest.json", "critical", "telemetry_policy must be an object")
        return

    schema_version = policy.get("schema_version", TELEMETRY_POLICY_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version != TELEMETRY_POLICY_SCHEMA_VERSION:
        defect("job.manifest.json", "critical", f"telemetry_policy.schema_version must be {TELEMETRY_POLICY_SCHEMA_VERSION}")

    mode = policy.get("mode")
    if mode not in TELEMETRY_POLICY_MODES:
        mode_display = ", ".join(TELEMETRY_POLICY_MODES)
        defect("job.manifest.json", "critical", f"telemetry_policy.mode must be one of [{mode_display}]")

    raw_text = policy.get("raw_text")
    if raw_text is not False:
        defect("job.manifest.json", "critical", "telemetry_policy.raw_text must be false")

    collect = policy.get("collect", [])
    if collect is None:
        collect = []
    elif isinstance(collect, str):
        collect = [collect]
    elif not isinstance(collect, list):
        defect("job.manifest.json", "critical", "telemetry_policy.collect must be a list")
        collect = []

    unsupported = []
    for index, item in enumerate(collect):
        if not isinstance(item, str) or not item.strip():
            defect("job.manifest.json", "critical", f"telemetry_policy.collect[{index}] must be a non-empty string")
            continue
        if item not in TELEMETRY_COLLECT_ITEMS:
            unsupported.append(item)
    if unsupported:
        defect(
            "job.manifest.json",
            "critical",
            f"telemetry_policy.collect has unsupported names: {', '.join(sorted(unsupported))}",
        )

    for key in policy:
        lowered = str(key).lower()
        if "usd" in lowered or "pricing" in lowered:
            defect("job.manifest.json", "critical", f"telemetry_policy contains unsupported billing field: {key}")

    allowed_keys = {"schema_version", "mode", "raw_text", "collect"}
    unknown = sorted(set(policy.keys()) - allowed_keys)
    if unknown:
        defect("job.manifest.json", "critical", f"telemetry_policy contains unsupported keys: {', '.join(unknown)}")


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

    for dirname in REQUIRED_BUNDLE_DIRS:
        if not (bundle_dir / dirname).is_dir():
            defect(dirname + "/", "critical", f"required bundle directory is missing: {dirname}/")

    for key in MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            defect("job.manifest.json", "critical", f"missing key: {key}")
    validate_preflight_lite_records(defect, bundle_dir, manifest)
    validate_telemetry_policy(defect, manifest)
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
    if parallelization.get("scheduling_mode") != "rolling":
        defect("job.manifest.json", "critical", "parallelization.scheduling_mode must be rolling")
    if parallelization.get("scheduler_path") != CONTRACT.MAIN_SCHEDULER_PATH:
        defect("job.manifest.json", "critical", f"parallelization.scheduler_path must be {CONTRACT.MAIN_SCHEDULER_PATH!r}")
    if not isinstance(parallelization.get("dependency_policy"), str) or not parallelization.get("dependency_policy", "").strip():
        defect("job.manifest.json", "critical", "parallelization.dependency_policy must be non-empty")
    wave_execution = parallelization.get("wave_execution", "")
    if not isinstance(wave_execution, str) or "saturat" not in wave_execution.lower() or "depends_on" not in wave_execution:
        defect("job.manifest.json", "critical", "parallelization.wave_execution must describe rolling saturation and depends_on deferral")
    if "serial_reason" in parallelization:
        defect("job.manifest.json", "critical", "parallelization.serial_reason is obsolete; use serial_reasons")
    serial_reasons = parallelization.get("serial_reasons", [])
    if not isinstance(serial_reasons, list) or any(not isinstance(item, str) or not item.strip() for item in serial_reasons):
        defect("job.manifest.json", "critical", "parallelization.serial_reasons must be an array of non-empty strings")
        serial_reasons = []
    has_serial_reason = bool(serial_reasons)

    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        defect("job.manifest.json", "critical", "adaptation_policy must match the shared amendment proposal policy")

    branches = manifest.get("branches", [])
    if not branches:
        defect("job.manifest.json", "critical", "branches must be non-empty")
    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        defect("job.manifest.json", "critical", "more than 20 branches exceeds 5 waves of 4")
    if len(branches) == 1 and not serial_reasons:
        defect("job.manifest.json", "critical", "single-branch bundles require parallelization.serial_reasons")
    if is_strict_int(max_active) and max_active < MAX_ACTIVE_BRANCH_AGENTS and not has_serial_reason:
        defect("job.manifest.json", "critical", "max_active_branch_agents below 4 requires parallelization.serial_reasons")
    if (
        is_strict_int(max_active)
        and isinstance(branches, list)
        and ready_branch_count(branches) < min(max_active, len(branches))
        and not has_serial_reason
    ):
        defect("job.manifest.json", "critical", "too few initially eligible branches under max_active_branch_agents requires parallelization.serial_reasons")

    worker_model_policy = manifest.get("worker_model_policy", {})
    if not isinstance(worker_model_policy, dict):
        defect("job.manifest.json", "critical", "worker_model_policy must be an object")
        worker_model_policy = {}
    if worker_model_policy.get("default_ladder") != DEFAULT_WORKER_LADDER:
        defect("job.manifest.json", "critical", f"worker_model_policy.default_ladder must be {DEFAULT_WORKER_LADDER!r}")
    if worker_model_policy.get("allowed_routes") != DEFAULT_WORKER_LADDER:
        defect("job.manifest.json", "critical", f"worker_model_policy.allowed_routes must be {DEFAULT_WORKER_LADDER!r}")
    if worker_model_policy.get("branch_may_select_worker_route") is not True:
        defect("job.manifest.json", "critical", "worker_model_policy.branch_may_select_worker_route must be true")
    if worker_model_policy.get("selection_reason_required") is not True:
        defect("job.manifest.json", "critical", "worker_model_policy.selection_reason_required must be true")
    if not isinstance(worker_model_policy.get("ordering_rule"), str) or not worker_model_policy.get("ordering_rule", "").strip():
        defect("job.manifest.json", "critical", "worker_model_policy.ordering_rule must be non-empty")

    review_model_policy = manifest.get("review_model_policy", {})
    if review_model_policy != REVIEW_MODEL_POLICY:
        defect("job.manifest.json", "critical", "review_model_policy must match the shared deterministic review router policy")

    amender_model_policy = manifest.get("amender_model_policy", {})
    if amender_model_policy != AMENDER_MODEL_POLICY:
        defect("job.manifest.json", "critical", "amender_model_policy must match the shared deterministic plan-amender router policy")

    lite_model_policy = manifest.get("lite_model_policy", {})
    if lite_model_policy != LITE_MODEL_POLICY:
        defect("job.manifest.json", "critical", "lite_model_policy must match the shared deterministic Lite model policy")

    lite_advisor_policy = manifest.get("lite_advisor_policy", {})
    if lite_advisor_policy != LITE_ADVISOR_POLICY:
        defect("job.manifest.json", "critical", "lite_advisor_policy must match the shared deterministic Lite advisor policy")

    watchdog = manifest.get("orchestration_watchdog", {})
    if watchdog != ORCHESTRATION_WATCHDOG:
        defect("job.manifest.json", "critical", "orchestration_watchdog must match shared watchdog defaults")

    research_worker_policy = manifest.get("research_worker_policy")
    if research_worker_policy is not None:
        if not isinstance(research_worker_policy, dict):
            defect("job.manifest.json", "critical", "research_worker_policy must be an object when present")
            research_worker_policy = {}
        if research_worker_policy.get("enabled") is not True:
            defect("job.manifest.json", "critical", "research_worker_policy.enabled must be true when present")
        if research_worker_policy.get("worker_type") != RESEARCH_WORKER_TYPE:
            defect("job.manifest.json", "critical", "research_worker_policy.worker_type must be 'research-worker'")
        for key in ["launcher", "network_scope", "local_access"]:
            if not isinstance(research_worker_policy.get(key), str) or not research_worker_policy.get(key, "").strip():
                defect("job.manifest.json", "critical", f"research_worker_policy.{key} must be non-empty")
        rejected_phrases, required_phrases = CONTRACT.research_policy_defects(research_worker_policy)
        for phrase in rejected_phrases:
            defect("job.manifest.json", "critical", f"research_worker_policy contains obsolete narrow-access phrase: {phrase}")
        for phrase in required_phrases:
            defect("job.manifest.json", "critical", f"research_worker_policy must mention {phrase}")

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
        for key in ["prompt", "status_path", "review_path", "pre_review_gate_path"]:
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
        wave_branch_ids.extend(branch_ids)
    if len(wave_ids) != len(set(wave_ids)):
        defect("job.manifest.json", "critical", "wave ids must be unique")
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        defect("job.manifest.json", "critical", "branch ids must not appear in more than one wave")
    if set(wave_branch_ids) != set(ids):
        defect("job.manifest.json", "critical", "waves must cover exactly the manifest branch ids")
    if isinstance(branches, list) and not has_serial_reason:
        chain = longest_branch_chain(branches)
        if len(branches) > 2 and chain >= len(branches) - 1:
            defect("job.manifest.json", "critical", "long serial branch dependency chains require parallelization.serial_reasons")

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
        lint_validator_command_snippets(
            defect,
            str(main_path),
            main_text,
            {
                "validate_branch_status.py": "branches/Bxx.status.json",
                "validate_main_status.py": "main.status.json",
            },
        )
        require_text_phrases(
            defect,
            str(main_path),
            main_text,
            MAIN_PROMPT_REQUIRED_PHRASES,
            severity="critical",
            message_prefix="main prompt missing required phrase",
        )
        if not has_dod(main_text):
            defect(str(main_path), "critical", "main prompt lacks a falsifiable Definition of Done")

    bootloader_path = bundle_dir / "goal-bootloader.md"
    if not bootloader_path.exists():
        defect("goal-bootloader.md", "critical", "bootloader is missing")
    else:
        bootloader = bootloader_path.read_text(encoding="utf-8")
        if len(bootloader) > 4000:
            defect("goal-bootloader.md", "critical", "bootloader exceeds 4000 characters")
        require_text_phrases(
            defect,
            "goal-bootloader.md",
            bootloader,
            BOOTLOADER_REQUIRED_PHRASES,
            severity="critical",
            message_prefix="bootloader missing phrase",
            case_sensitive=True,
        )

    has_research_work_item = False
    worker_route_class_count = 0
    default_normal_route_count = 0
    for branch in branches:
        for key in BRANCH_REQUIRED_KEYS:
            if key not in branch:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} missing key: {key}")
        depends_on = branch.get("depends_on", [])
        if not isinstance(depends_on, list):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on must be a list")
            depends_on = []
        seen_branch_deps = set()
        branch_index = ids.index(branch.get("id")) if branch.get("id") in ids else -1
        for dep_index, dep in enumerate(depends_on):
            if not isinstance(dep, str) or not SAFE_ID_RE.fullmatch(dep):
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on[{dep_index}] is not a safe branch id")
                continue
            if dep in seen_branch_deps:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on repeats branch {dep}")
            seen_branch_deps.add(dep)
            if dep not in ids:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends on unknown branch {dep}")
            elif dep == branch.get("id"):
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} cannot depend on itself")
            elif ids.index(dep) >= branch_index:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on must reference only prior branch ids; invalid dependency: {dep}")
        max_workers = branch.get("max_active_worker_packets")
        if not is_strict_int(max_workers) or max_workers < 1 or max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} max_active_worker_packets must be an integer from 1 to 4")
        branch_owned_paths_value = branch.get("owned_paths", [])
        if not isinstance(branch_owned_paths_value, list) or any(not isinstance(item, str) or not item.strip() for item in branch_owned_paths_value):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} owned_paths must be a derived array of non-empty strings")
            branch_owned_paths_value = []
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
                worker_type = item.get("worker_type", "worker")
                if worker_type not in {"worker", RESEARCH_WORKER_TYPE}:
                    defect("job.manifest.json", "critical", f"{item_path}.worker_type must be 'worker' or 'research-worker'")
                if worker_type == RESEARCH_WORKER_TYPE:
                    has_research_work_item = True
                    if "route_class" in item:
                        defect("job.manifest.json", "critical", f"{item_path}.route_class must be omitted for research-worker items")
                else:
                    worker_route_class_count += 1
                    route_class = item.get("route_class")
                    if route_class not in MANIFEST_WORKER_ROUTE_CLASSES:
                        defect("job.manifest.json", "critical", f"{item_path}.route_class must be one of {', '.join(MANIFEST_WORKER_ROUTE_CLASSES)}")
                    route_reason = item.get("route_class_reason")
                    if not isinstance(route_reason, str) or not route_reason.strip():
                        defect("job.manifest.json", "critical", f"{item_path}.route_class_reason must be a non-empty string")
                    elif route_class == "normal-code" and "default normal-code inference" in route_reason.lower():
                        default_normal_route_count += 1
                if worker_type == RESEARCH_WORKER_TYPE:
                    route_reason = item.get("route_class_reason")
                    if not isinstance(route_reason, str) or not route_reason.strip():
                        defect("job.manifest.json", "critical", f"{item_path}.route_class_reason must explain research-worker routing")
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
            work_item_order = {
                item.get("id"): index
                for index, item in enumerate(work_items)
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            for index, item in enumerate(work_items):
                if not isinstance(item, dict):
                    continue
                for dep in item.get("depends_on", []):
                    if dep not in known_work_item_ids:
                        defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items[{index}] depends on unknown work item: {dep}")
                    elif work_item_order.get(dep, index) >= index:
                        defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items[{index}] depends_on must reference only prior work item ids: {dep}")
            for left_index, left in enumerate(work_items):
                if not isinstance(left, dict):
                    continue
                for right_index in range(left_index + 1, len(work_items)):
                    right = work_items[right_index]
                    if not isinstance(right, dict):
                        continue
                    left_paths = left.get("owned_paths", []) if isinstance(left.get("owned_paths"), list) else []
                    right_paths = right.get("owned_paths", []) if isinstance(right.get("owned_paths"), list) else []
                    overlaps = [
                        (left_path, right_path)
                        for left_path in left_paths
                        for right_path in right_paths
                        if isinstance(left_path, str) and isinstance(right_path, str) and paths_overlap(left_path, right_path)
                    ]
                    if not overlaps:
                        continue
                    left_id = left.get("id")
                    right_id = right.get("id")
                    dependency_serialized = (
                        isinstance(left_id, str)
                        and isinstance(right.get("depends_on"), list)
                        and left_id in right.get("depends_on", [])
                    ) or (
                        isinstance(right_id, str)
                        and isinstance(left.get("depends_on"), list)
                        and right_id in left.get("depends_on", [])
                    )
                    if not dependency_serialized and not has_contention_reason(
                        left.get("contention_reason"),
                        right.get("contention_reason"),
                        branch.get("worker_contention_reason"),
                    ):
                        overlap_text = ", ".join(f"{left_path} vs {right_path}" for left_path, right_path in overlaps)
                        defect("job.manifest.json", "critical", f"branch {branch.get('id')} work item owned_paths overlap without dependency or contention_reason: {left_id} and {right_id}: {overlap_text}")
            derived_branch_owned = []
            for item in work_items:
                for value in item.get("owned_paths", []):
                    if isinstance(value, str) and value not in derived_branch_owned:
                        derived_branch_owned.append(value)
            if branch_owned_paths_value != derived_branch_owned:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} owned_paths must equal the ordered union of work item owned_paths")
        worker_parallelism = branch.get("worker_parallelism", {})
        if not isinstance(worker_parallelism, dict):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism must be an object")
            worker_parallelism = {}
        if worker_parallelism.get("parallelism_default") is not True:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.parallelism_default must be true")
        if worker_parallelism.get("scheduling_mode") != "rolling":
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.scheduling_mode must be rolling")
        expected_scheduler_path = CONTRACT.worker_scheduler_path(str(branch.get("id", ""))) if isinstance(branch.get("id"), str) else ""
        if worker_parallelism.get("scheduler_path") != expected_scheduler_path:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.scheduler_path must be {expected_scheduler_path!r}")
        if worker_parallelism.get("max_active_worker_packets") != max_workers:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.max_active_worker_packets must match branch max_active_worker_packets")
        if worker_parallelism.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.max_worker_packets_per_branch must be 4")
        if "serial_reason" in worker_parallelism:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.serial_reason is obsolete; use serial_reasons")
        worker_serial_reasons = worker_parallelism.get("serial_reasons", [])
        if not isinstance(worker_serial_reasons, list) or any(not isinstance(item, str) or not item.strip() for item in worker_serial_reasons):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.serial_reasons must be an array of non-empty strings")
            worker_serial_reasons = []
        if not isinstance(worker_parallelism.get("parallelization_rationale"), str) or not worker_parallelism.get("parallelization_rationale", "").strip():
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.parallelization_rationale must be non-empty")
        if is_strict_int(max_workers) and max_workers < MAX_WORKER_PACKETS_PER_BRANCH and not worker_serial_reasons:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} max_active_worker_packets below 4 requires worker_parallelism.serial_reasons")
        if is_strict_int(max_workers) and isinstance(work_items, list) and len(work_items) == 1 and max_workers > 1 and not worker_serial_reasons:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} has one worker while max_active_worker_packets > 1 without worker_parallelism.serial_reasons")
        if (
            is_strict_int(max_workers)
            and isinstance(work_items, list)
            and ready_worker_count(work_items) < min(max_workers, len(work_items))
            and not worker_serial_reasons
        ):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} has too few initially eligible worker items under max_active_worker_packets without worker_parallelism.serial_reasons")
        if (
            isinstance(work_items, list)
            and len(work_items) > 2
            and longest_worker_chain(work_items) >= len(work_items) - 1
            and not worker_serial_reasons
        ):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker dependency chain serializes most work without worker_parallelism.serial_reasons")
        dependency_policy = worker_parallelism.get("dependency_policy", "")
        if not isinstance(dependency_policy, str) or "depends_on" not in dependency_policy:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.dependency_policy must mention depends_on")
        slot_refill = worker_parallelism.get("slot_refill", "")
        if not isinstance(slot_refill, str) or "launch" not in slot_refill.lower():
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism.slot_refill must describe launching replacements")
        for key in ["prompt", "status_path", "review_path"]:
            message = relative_path_defect(branch.get(key), key)
            if message:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
        expected_gate_path = CONTRACT.pre_review_gate_path(str(branch.get("id", ""))) if isinstance(branch.get("id"), str) else ""
        if branch.get("pre_review_gate_path") != expected_gate_path:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} pre_review_gate_path must be {expected_gate_path!r}")
        message = relative_path_defect(branch.get("pre_review_gate_path"), "pre_review_gate_path")
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
        expected_status_path = branch.get("status_path") if isinstance(branch.get("status_path"), str) else "branches/Bxx.status.json"
        lint_validator_command_snippets(
            defect,
            str(prompt_path),
            text,
            {"validate_branch_status.py": expected_status_path},
        )
        require_text_phrases(
            defect,
            str(prompt_path),
            text,
            branch_prompt_required_phrases(branch.get("id")),
            severity="major",
            message_prefix="branch prompt missing section",
        )
        if not has_dod(text):
            defect(str(prompt_path), "critical", f"branch {branch.get('id')} lacks a falsifiable Definition of Done")

    if worker_route_class_count and default_normal_route_count == worker_route_class_count:
        defect(
            "job.manifest.json",
            "warning",
            "all worker route classes fell back to default normal-code; check whether docs, mechanical, small-edit, complex-code, or research-worker routing should apply",
        )

    if has_research_work_item:
        if research_worker_policy is None:
            defect("job.manifest.json", "critical", "research_worker_policy is required when any work item uses worker_type='research-worker'")
        if not (bundle_dir / "research").is_dir():
            defect("research/", "critical", "research work items require research/ packet directory")

    branch_owned_paths: dict[str, list[str]] = {}
    branch_deps: dict[str, list[str]] = {}
    branch_contention: dict[str, object] = {}
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        bid = branch["id"]
        branch_deps[bid] = branch.get("depends_on", []) if isinstance(branch.get("depends_on"), list) else []
        branch_contention[bid] = branch.get("contention_reason")
        owned = [path for path in branch.get("owned_paths", []) if isinstance(path, str)] if isinstance(branch.get("owned_paths"), list) else []
        branch_owned_paths[bid] = owned
    branch_ids_for_overlap = list(branch_owned_paths)
    for left_index, left_id in enumerate(branch_ids_for_overlap):
        for right_id in branch_ids_for_overlap[left_index + 1 :]:
            overlaps = [
                (left_path, right_path)
                for left_path in branch_owned_paths[left_id]
                for right_path in branch_owned_paths[right_id]
                if paths_overlap(left_path, right_path)
            ]
            if not overlaps:
                continue
            dependency_serialized = left_id in branch_deps.get(right_id, []) or right_id in branch_deps.get(left_id, [])
            if not dependency_serialized and not has_contention_reason(
                branch_contention.get(left_id),
                branch_contention.get(right_id),
                serial_reasons,
            ):
                overlap_text = ", ".join(f"{left_path} vs {right_path}" for left_path, right_path in overlaps)
                defect("job.manifest.json", "critical", f"branch owned_paths overlap without dependency or contention_reason: {left_id} and {right_id}: {overlap_text}")

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
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0 if data["status"] == "pass" else 1
    output_path = (
        resolve_absolute_path(args.output, "--output", must_exist=False)
        if args.output
        else bundle_dir / "preflight.lint.json"
    )
    output_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output_path)
    return 0 if data["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
