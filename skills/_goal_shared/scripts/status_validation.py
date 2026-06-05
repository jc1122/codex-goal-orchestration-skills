#!/usr/bin/env python3
"""Shared runtime status-validation helpers for goal orchestration skills."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import re
import shlex
from pathlib import Path


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


def _load_contract():
    path = Path(__file__).resolve().parent / "orchestration_contract.py"
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
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}
SAFE_PACKET_RE = PATH_RULES.SAFE_PACKET_LABEL_RE
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
SCHEDULER_EVENT_SCHEMA_VERSION = 2
is_strict_int = PATH_RULES.is_strict_int
resolve_absolute_path = PATH_RULES.resolve_absolute_path


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


def require_nonnegative_int(defects: list[str], value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        defect(defects, path, "must be a non-negative integer")
        return 0
    return value


def validate_usage(defects: list[str], value: object, path: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        defect(defects, path, "must be null or an object")
        return
    allowed = {
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "total_tokens",
    }
    for key, item in value.items():
        if key not in allowed:
            defect(defects, f"{path}.{key}", f"unsupported usage key; allowed keys are {sorted(allowed)}")
            continue
        require_nonnegative_int(defects, item, f"{path}.{key}")


def validate_telemetry_logs(defects: list[str], value: object, path: str) -> None:
    if not isinstance(value, list):
        defect(defects, path, "must be an array")
        return
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        data = require_object(defects, item, item_path)
        require_string(defects, data.get("path"), f"{item_path}.path")
        if not isinstance(data.get("exists"), bool):
            defect(defects, f"{item_path}.exists", "must be a boolean")
        require_nonnegative_int(defects, data.get("bytes"), f"{item_path}.bytes")
        require_nonnegative_int(defects, data.get("chars"), f"{item_path}.chars")
        validate_usage(defects, data.get("usage"), f"{item_path}.usage")


def validate_telemetry_artifact(
    defects: list[str],
    telemetry_path: Path,
    path: str,
    *,
    packet_id: str | None = None,
    role: str | None = None,
    allowed_aliases: list[str] | tuple[str, ...] | set[str] | None = None,
    require_called: bool = True,
) -> dict:
    if not telemetry_path.exists():
        defect(defects, path, f"missing telemetry artifact: {telemetry_path}")
        return {}
    data = require_object(defects, load_json_artifact(defects, telemetry_path, path), path)
    if data.get("schema_version") != 1:
        defect(defects, f"{path}.schema_version", "must be 1")
    actual_packet = require_string(defects, data.get("packet_id"), f"{path}.packet_id")
    if packet_id is not None and actual_packet and actual_packet != packet_id:
        defect(defects, f"{path}.packet_id", f"must be {packet_id!r}")
    actual_role = require_string(defects, data.get("role"), f"{path}.role")
    if role is not None and actual_role and actual_role != role:
        defect(defects, f"{path}.role", f"must be {role!r}")
    for key in [
        "output_artifact",
        "prompt_artifact",
    ]:
        require_string(defects, data.get(key), f"{path}.{key}")
    for key in [
        "prompt_chars",
        "prompt_bytes",
        "output_chars",
        "output_bytes",
        "event_log_chars",
        "event_log_bytes",
    ]:
        require_nonnegative_int(defects, data.get(key), f"{path}.{key}")
    if data.get("accepted_alias") is not None:
        require_string(defects, data.get("accepted_alias"), f"{path}.accepted_alias")
    attempts = data.get("attempts")
    if not isinstance(attempts, list):
        defect(defects, f"{path}.attempts", "must be an array")
        attempts = []
    allowed_alias_set = set(allowed_aliases or [])
    called_aliases = []
    accepted_aliases = []
    for index, item in enumerate(attempts):
        item_path = f"{path}.attempts[{index}]"
        attempt = require_object(defects, item, item_path)
        alias = require_string(defects, attempt.get("alias"), f"{item_path}.alias")
        provider = require_string(defects, attempt.get("provider"), f"{item_path}.provider")
        model = require_string(defects, attempt.get("model"), f"{item_path}.model")
        if provider == "codex" and alias in CONTRACT.CODEX_ROUTE_MODELS:
            expected_model = CONTRACT.codex_model(alias)
            if model != expected_model:
                defect(defects, f"{item_path}.model", f"must be {expected_model!r} for alias {alias!r}")
        if attempt.get("effort") is not None:
            require_string(defects, attempt.get("effort"), f"{item_path}.effort")
        require_string(defects, attempt.get("command"), f"{item_path}.command")
        timeout_seconds = attempt.get("timeout_seconds")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            defect(defects, f"{item_path}.timeout_seconds", "must be a positive integer")
        called = attempt.get("called")
        accepted = attempt.get("accepted")
        if not isinstance(called, bool):
            defect(defects, f"{item_path}.called", "must be a boolean")
            called = False
        if not isinstance(accepted, bool):
            defect(defects, f"{item_path}.accepted", "must be a boolean")
            accepted = False
        if alias and allowed_alias_set and alias not in allowed_alias_set:
            defect(defects, f"{item_path}.alias", f"must be one of {sorted(allowed_alias_set)}")
        if called and alias:
            called_aliases.append(alias)
        if accepted:
            if not called:
                defect(defects, f"{item_path}.accepted", "may be true only for called attempts")
            if alias:
                accepted_aliases.append(alias)
        validate_telemetry_logs(defects, attempt.get("event_logs"), f"{item_path}.event_logs")
        validate_telemetry_logs(defects, attempt.get("probe_logs"), f"{item_path}.probe_logs")
        validate_usage(defects, attempt.get("usage"), f"{item_path}.usage")
    if require_called and not called_aliases:
        defect(defects, f"{path}.attempts", "must record at least one called model attempt")
    if len(accepted_aliases) > 1:
        defect(defects, f"{path}.attempts", "must mark at most one accepted attempt")
    if isinstance(data.get("accepted_alias"), str):
        if data["accepted_alias"] not in accepted_aliases:
            defect(defects, f"{path}.accepted_alias", "must match the accepted attempt alias")
    elif accepted_aliases:
        defect(defects, f"{path}.accepted_alias", "must be set when an attempt is marked accepted")
    totals = require_object(defects, data.get("totals"), f"{path}.totals")
    attempts_declared = require_nonnegative_int(defects, totals.get("attempts_declared"), f"{path}.totals.attempts_declared")
    attempts_called = require_nonnegative_int(defects, totals.get("attempts_called"), f"{path}.totals.attempts_called")
    if attempts_declared != len(attempts):
        defect(defects, f"{path}.totals.attempts_declared", "must match attempts length")
    if attempts_called != len(called_aliases):
        defect(defects, f"{path}.totals.attempts_called", "must match called attempt count")
    require_nonnegative_int(defects, totals.get("event_log_chars"), f"{path}.totals.event_log_chars")
    require_nonnegative_int(defects, totals.get("event_log_bytes"), f"{path}.totals.event_log_bytes")
    validate_usage(defects, totals.get("known_usage"), f"{path}.totals.known_usage")
    return data


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


is_repo_relative_path = PATH_RULES.is_repo_relative_path
is_absolute_path = PATH_RULES.is_absolute_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def archived_manifest_sha256s(manifest_path: Path) -> set[str]:
    hashes: set[str] = set()
    if manifest_path.exists() and manifest_path.is_file():
        hashes.add(sha256_file(manifest_path))
    amendments_dir = manifest_path.parent / "amendments"
    if amendments_dir.is_dir():
        for archived in sorted(amendments_dir.glob("*.job.manifest.before.json")):
            if archived.is_file():
                hashes.add(sha256_file(archived))
    return hashes


def archived_manifest_hashes_by_rel_path(manifest_path: Path) -> dict[str, set[str]]:
    return {manifest_path.name: archived_manifest_sha256s(manifest_path)}


def relative_hashes(
    defects: list[str],
    value: object,
    path: str,
    *,
    root_dir: Path,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None = None,
) -> dict[str, str]:
    if not isinstance(value, dict):
        defect(defects, path, "must be an object mapping relative paths to sha256 digests")
        return {}
    result: dict[str, str] = {}
    for key, digest in value.items():
        item_path = f"{path}.{key}" if isinstance(key, str) else f"{path}.<invalid>"
        if not isinstance(key, str) or not key.strip():
            defect(defects, path, "hash keys must be non-empty relative paths")
            continue
        if not is_repo_relative_path(key):
            defect(defects, item_path, "hash key must be relative without traversal")
            continue
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            defect(defects, item_path, "must be sha256:<64 lowercase hex chars>")
            continue
        target = (root_dir / key).resolve()
        try:
            target.relative_to(root_dir.resolve())
        except ValueError:
            defect(defects, item_path, "hash target escapes bundle root")
            continue
        if not target.exists():
            defect(defects, item_path, f"hash target does not exist: {target}")
            continue
        actual = sha256_file(target)
        allowed_hashes = allowed_hashes_by_rel_path.get(key, set()) if allowed_hashes_by_rel_path else set()
        if digest != actual and digest not in allowed_hashes:
            defect(defects, item_path, "must match current file sha256")
        result[key] = digest
    return result


def validate_reuse_policy(defects: list[str], value: object, path: str) -> None:
    data = require_object(defects, value, path)
    mode = data.get("mode")
    if mode not in {"new", "reuse"}:
        defect(defects, f"{path}.mode", "must be 'new' or 'reuse'")
    if not isinstance(data.get("accepted"), bool):
        defect(defects, f"{path}.accepted", "must be a boolean")
    hashes_match = data.get("semantic_hashes_match", data.get("input_hashes_match"))
    if not isinstance(hashes_match, bool):
        defect(defects, f"{path}.semantic_hashes_match", "must be a boolean")
    source = data.get("source_review_path")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        defect(defects, f"{path}.source_review_path", "must be null or a non-empty string")
    source_telemetry = data.get("source_telemetry_path")
    if source_telemetry is not None and (not isinstance(source_telemetry, str) or not source_telemetry.strip()):
        defect(defects, f"{path}.source_telemetry_path", "must be null or a non-empty string")
    if data.get("accepted") is True:
        if mode != "reuse":
            defect(defects, f"{path}.mode", "must be 'reuse' when accepted is true")
        if hashes_match is not True:
            defect(defects, f"{path}.semantic_hashes_match", "must be true when reviewer reuse is accepted")
        if not isinstance(source, str) or not source.strip():
            defect(defects, f"{path}.source_review_path", "must identify the reused review artifact when accepted is true")
        if not isinstance(source_telemetry, str) or not source_telemetry.strip():
            defect(defects, f"{path}.source_telemetry_path", "must identify the reused reviewer telemetry artifact when accepted is true")


def validate_reuse_eligibility(defects: list[str], value: object, path: str, *, semantic_hashes: dict[str, str]) -> None:
    data = require_object(defects, value, path)
    if not isinstance(data.get("eligible"), bool):
        defect(defects, f"{path}.eligible", "must be a boolean")
    require_string(defects, data.get("reason"), f"{path}.reason")
    required_hashes = require_string_list(defects, data.get("required_hashes"), f"{path}.required_hashes")
    expected_hashes = sorted(semantic_hashes)
    if required_hashes and required_hashes != expected_hashes:
        defect(defects, f"{path}.required_hashes", "must list semantic_input_hashes keys exactly")
    require_string(defects, data.get("route_policy_path"), f"{path}.route_policy_path")
    for key in ["source_review_path", "source_telemetry_path"]:
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            defect(defects, f"{path}.{key}", "must be null or a non-empty string")


def validate_pre_review_volatile_inputs(defects: list[str], value: object, path: str) -> None:
    if value in (None, {}):
        return
    data = require_object(defects, value, path)
    head = data.get("worktree_head")
    if head is not None:
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            defect(defects, f"{path}.worktree_head", "must be a 40- or 64-character git commit hash")
    for key in ["diff_name_status_sha256", "diff_binary_sha256"]:
        digest = data.get(key)
        if digest is not None and (not isinstance(digest, str) or not SHA256_RE.fullmatch(digest)):
            defect(defects, f"{path}.{key}", "must be sha256:<64 lowercase hex chars>")
    commands = data.get("commands_run")
    if commands is not None:
        require_string_list(defects, commands, f"{path}.commands_run", min_items=1)


def _ordered_subset(values: set[str], order: list[str]) -> list[str]:
    return [item for item in order if item in values]


def _event_reason(defects: list[str], event: dict, path: str) -> str:
    reason = require_string(defects, event.get("reason"), f"{path}.reason")
    return reason


def _event_reason_code(defects: list[str], event: dict, path: str, *, required: bool) -> str:
    code = event.get("reason_code")
    allowed = {
        "artifact_invalid",
        "capacity_limit",
        "contention",
        "dependency_failed",
        "dependency_pending",
        "launcher_failed",
        "native_agent_unreachable",
        "no_ready_work",
        "operator_requested",
        "process_exited_blocked",
        "stale_active",
        "timeout",
    }
    if code is None and not required:
        return ""
    if code not in allowed:
        defect(defects, f"{path}.reason_code", f"must be one of {sorted(allowed)}")
        return ""
    return str(code)


def validate_scheduler_ledger(
    defects: list[str],
    ledger_value: object,
    path: str,
    *,
    scheduler_kind: str,
    expected_path: str,
    expected_ids: list[str],
    dependencies: dict[str, list[str]],
    capacity: int,
    manifest_path: Path | None = None,
    allowed_manifest_sha256s: set[str] | None = None,
    require_all_launched: bool = False,
) -> dict[str, list[str] | int]:
    root = require_object(defects, ledger_value, path)
    if root.get("schema_version") != 2:
        defect(defects, f"{path}.schema_version", "must be 2")
    if root.get("scheduler_kind") != scheduler_kind:
        defect(defects, f"{path}.scheduler_kind", f"must be {scheduler_kind!r}")
    if root.get("scheduler_path") != expected_path:
        defect(defects, f"{path}.scheduler_path", f"must be {expected_path!r}")
    if root.get("capacity") != capacity:
        defect(defects, f"{path}.capacity", f"must be {capacity}")
    if root.get("item_ids") != expected_ids:
        defect(defects, f"{path}.item_ids", "must match manifest item order exactly")
    if manifest_path is not None:
        manifest_sha = root.get("manifest_sha256")
        if not isinstance(manifest_sha, str) or not SHA256_RE.fullmatch(manifest_sha):
            defect(defects, f"{path}.manifest_sha256", "must be sha256:<64 lowercase hex chars>")
        else:
            actual_manifest_sha = sha256_file(manifest_path)
            accepted_manifest_shas = allowed_manifest_sha256s or {actual_manifest_sha}
            if manifest_sha != actual_manifest_sha and manifest_sha not in accepted_manifest_shas:
                defect(defects, f"{path}.manifest_sha256", "must match current job.manifest.json sha256")
    events = root.get("events")
    if not isinstance(events, list) or not events:
        defect(defects, f"{path}.events", "must be a non-empty array")
        events = []

    expected_set = set(expected_ids)
    active: set[str] = set()
    ready_seen: set[str] = set()
    launched: set[str] = set()
    finished: set[str] = set()
    finished_status: dict[str, str] = {}
    closed: set[str] = set()
    deferred: dict[str, str] = {}
    deferred_reason_codes: dict[str, str] = {}
    blocked: dict[str, str] = {}
    blocked_reason_codes: dict[str, str] = {}
    repair_relaunch_ids: set[str] = set()
    under_capacity: dict[str, str] = {}
    under_capacity_reason_codes: dict[str, str] = {}
    deferred_excuses: set[str] = set()
    blocked_excuses: set[str] = set()
    under_capacity_excuses: set[str] = set()
    max_observed = 0
    refill_required = False
    event_manifest_allowed: set[str] = set(allowed_manifest_sha256s or [])
    if manifest_path is not None and not event_manifest_allowed:
        event_manifest_allowed = {sha256_file(manifest_path)}

    def eligible_ids() -> list[str]:
        eligible = []
        for item_id in expected_ids:
            if item_id in active:
                continue
            if item_id in launched:
                if (
                    scheduler_kind != "branch-worker-pool"
                    or item_id not in closed
                    or finished_status.get(item_id) == "pass"
                    or item_id not in repair_relaunch_ids
                ):
                    continue
            deps = dependencies.get(item_id, [])
            if all(dep in closed and finished_status.get(dep) == "pass" for dep in deps):
                eligible.append(item_id)
        return eligible

    def failed_dependency_ids(item_id: str) -> list[str]:
        failed = []
        for dep in dependencies.get(item_id, []):
            status = finished_status.get(dep)
            if dep in closed and status in {"partial", "blocked", "failed"}:
                failed.append(dep)
        return failed

    def unexcused_eligible() -> list[str]:
        excused = deferred_excuses | blocked_excuses | under_capacity_excuses
        return [item_id for item_id in eligible_ids() if item_id not in excused]

    for index, raw_event in enumerate(events):
        event_path = f"{path}.events[{index}]"
        event = require_object(defects, raw_event, event_path)
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq != index + 1:
            defect(defects, f"{event_path}.seq", f"must be ordered integer {index + 1}")
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp.strip() or TIMESTAMP_RE.search(timestamp) is None:
            defect(defects, f"{event_path}.timestamp", "must be an ISO-like timestamp string")
        wall_clock_timestamp = event.get("wall_clock_timestamp")
        if (
            not isinstance(wall_clock_timestamp, str)
            or not wall_clock_timestamp.strip()
            or TIMESTAMP_RE.search(wall_clock_timestamp) is None
        ):
            defect(defects, f"{event_path}.wall_clock_timestamp", "must be an ISO-like timestamp string")
        require_string(defects, event.get("runtime_ref"), f"{event_path}.runtime_ref")
        event_name = event.get("event")
        if event_name not in {
            "ready",
            "launch",
            "finish",
            "close",
            "refill",
            "defer",
            "under_capacity",
            "blocked",
        }:
            defect(defects, f"{event_path}.event", "must be a supported scheduler event")
            continue
        if event.get("reason") is not None and event_name not in {"defer", "under_capacity", "blocked"}:
            defect(defects, f"{event_path}.reason", "is allowed only on defer, under_capacity, or blocked events")
        _event_reason_code(
            defects,
            event,
            event_path,
            required=event_name in {"defer", "under_capacity", "blocked"},
        )

        before_eligible = eligible_ids()
        before_unexcused = unexcused_eligible()

        event_schema_version = event.get("schema_version")
        has_event_schema_version = event_schema_version is not None
        if has_event_schema_version and not isinstance(event_schema_version, int):
            defect(defects, f"{event_path}.schema_version", "must be an integer")
            event_schema_version = None

        if has_event_schema_version and event_schema_version is not None and event_schema_version >= SCHEDULER_EVENT_SCHEMA_VERSION:
            event_manifest_sha = event.get("manifest_sha256")
            if not isinstance(event_manifest_sha, str) or not SHA256_RE.fullmatch(event_manifest_sha):
                defect(defects, f"{event_path}.manifest_sha256", "must be sha256:<64 lowercase hex chars>")
            elif manifest_path is not None and event_manifest_sha not in event_manifest_allowed:
                defect(defects, f"{event_path}.manifest_sha256", "must match current or archived manifest hash")

            event_epoch = event.get("manifest_epoch")
            if not isinstance(event_epoch, str) or not event_epoch.strip():
                defect(defects, f"{event_path}.manifest_epoch", "must be a non-empty string")
        if before_unexcused and len(active) < capacity:
            addresses_idle = False
            event_id = event.get("id")
            if event_name in {"ready", "launch", "defer", "blocked"} and isinstance(event_id, str):
                addresses_idle = event_id in before_unexcused
            elif event_name in {"refill", "under_capacity"}:
                eligible_values = event.get("eligible_ids")
                addresses_idle = (
                    isinstance(eligible_values, list)
                    and any(isinstance(item, str) and item in before_unexcused for item in eligible_values)
                )
            if not addresses_idle:
                defect(
                    defects,
                    event_path,
                    "eligible items were idle below capacity without launch, refill, defer, under_capacity, or blocked evidence: "
                    + ", ".join(before_unexcused),
                )
            if refill_required and event_name != "refill":
                defect(defects, event_path, "missing refill event after capacity was freed with eligible items waiting")

        event_id = event.get("id")
        if event_name not in {"under_capacity", "refill"}:
            event_id = require_string(defects, event_id, f"{event_path}.id")
            if event_id and event_id not in expected_set:
                defect(defects, f"{event_path}.id", "is not declared in the manifest scheduler item set")

        if event_name == "ready":
            if event_id in ready_seen:
                defect(defects, f"{event_path}.id", "duplicates ready event")
            if event_id and event_id not in before_eligible:
                defect(defects, f"{event_path}.id", "may be ready only after dependencies are closed and before launch")
            ready_seen.add(str(event_id))
            continue

        if event_name == "refill":
            eligible_values = event.get("eligible_ids")
            if not isinstance(eligible_values, list) or not eligible_values:
                defect(defects, f"{event_path}.eligible_ids", "must list eligible items considered for refill")
                eligible_values = []
            for value_index, value in enumerate(eligible_values):
                repair_relaunch_eligible = bool(
                    scheduler_kind == "branch-worker-pool"
                    and isinstance(value, str)
                    and value in launched
                    and value in closed
                    and finished_status.get(value) != "pass"
                )
                if not isinstance(value, str) or (value not in before_eligible and not repair_relaunch_eligible):
                    defect(defects, f"{event_path}.eligible_ids[{value_index}]", "must be currently eligible and unlaunched")
            if len(active) >= capacity:
                defect(defects, event_path, "refill requires free capacity")
            refill_required = False
            continue

        if event_name == "launch":
            repair_relaunch = bool(
                scheduler_kind == "branch-worker-pool"
                and event_id
                and event_id in launched
                and event_id in closed
                and finished_status.get(str(event_id)) != "pass"
            )
            if event_id in launched and not repair_relaunch:
                defect(defects, f"{event_path}.id", "duplicates launch event")
            if event_id and event_id not in before_eligible and not repair_relaunch:
                failed_deps = failed_dependency_ids(str(event_id))
                if failed_deps:
                    defect(defects, f"{event_path}.id", "cannot launch after dependency finished non-pass: " + ", ".join(failed_deps))
                else:
                    defect(defects, f"{event_path}.id", "cannot launch before dependencies pass")
            if len(active) >= capacity:
                defect(defects, event_path, "active scheduler count would exceed capacity")
            if event_id:
                launched.add(str(event_id))
                active.add(str(event_id))
                if repair_relaunch:
                    finished.discard(str(event_id))
                    closed.discard(str(event_id))
                    finished_status.pop(str(event_id), None)
                under_capacity.pop(str(event_id), None)
                deferred.pop(str(event_id), None)
                blocked.pop(str(event_id), None)
                under_capacity_excuses.discard(str(event_id))
                deferred_excuses.discard(str(event_id))
                blocked_excuses.discard(str(event_id))
                max_observed = max(max_observed, len(active))
            continue

        if event_name == "finish":
            if event_id and event_id not in active:
                defect(defects, f"{event_path}.id", "cannot finish an item that is not active")
            if event_id in finished:
                defect(defects, f"{event_path}.id", "duplicates finish event")
            status = event.get("status")
            if status not in {"pass", "partial", "blocked", "failed"}:
                defect(defects, f"{event_path}.status", "must be one of ['blocked', 'failed', 'partial', 'pass']")
            if event_id:
                finished.add(str(event_id))
                if isinstance(status, str):
                    finished_status[str(event_id)] = status
            continue

        if event_name == "close":
            if event_id and event_id not in active:
                defect(defects, f"{event_path}.id", "cannot close an item that is not active")
            if event_id and event_id not in finished:
                defect(defects, f"{event_path}.id", "cannot close an item before finish")
            if event_id in closed:
                defect(defects, f"{event_path}.id", "duplicates close event")
            if event_id:
                active.discard(str(event_id))
                closed.add(str(event_id))
                if finished_status.get(str(event_id)) != "pass":
                    under_capacity_excuses.discard(str(event_id))
                    deferred_excuses.discard(str(event_id))
                    blocked_excuses.discard(str(event_id))
            if unexcused_eligible() and len(active) < capacity:
                refill_required = True
            continue

        if event_name == "defer":
            reason = _event_reason(defects, event, event_path)
            reason_code = _event_reason_code(defects, event, event_path, required=True)
            if event_id:
                deferred[str(event_id)] = reason
                deferred_reason_codes[str(event_id)] = reason_code
                deferred_excuses.add(str(event_id))
            continue

        if event_name == "blocked":
            reason = _event_reason(defects, event, event_path)
            reason_code = _event_reason_code(defects, event, event_path, required=True)
            if event_id:
                blocked[str(event_id)] = reason
                blocked_reason_codes[str(event_id)] = reason_code
                blocked_excuses.add(str(event_id))
                if any(marker in reason.lower() for marker in ("repair", "retry", "amendment", "reviewer-feedback")):
                    repair_relaunch_ids.add(str(event_id))
            continue

        if event_name == "under_capacity":
            reason = _event_reason(defects, event, event_path)
            reason_code = _event_reason_code(defects, event, event_path, required=True)
            eligible_values = event.get("eligible_ids")
            if not isinstance(eligible_values, list) or not eligible_values:
                defect(defects, f"{event_path}.eligible_ids", "must list idle eligible items")
                eligible_values = []
            for value_index, value in enumerate(eligible_values):
                if not isinstance(value, str) or value not in before_eligible:
                    defect(defects, f"{event_path}.eligible_ids[{value_index}]", "must be currently eligible and unlaunched")
                    continue
                under_capacity[value] = reason
                under_capacity_reason_codes[value] = reason_code
                under_capacity_excuses.add(value)

    final_unexcused = unexcused_eligible()
    if final_unexcused and len(active) < capacity:
        defect(
            defects,
            path,
            "final scheduler state leaves eligible items idle below capacity without launch, refill, defer, under_capacity, or blocked evidence: "
            + ", ".join(final_unexcused),
        )
    for item_id in _ordered_subset(launched - finished, expected_ids):
        defect(defects, path, f"launched item is missing a finish event: {item_id}")
    for item_id in _ordered_subset(launched - closed, expected_ids):
        defect(defects, path, f"launched item is missing a close event: {item_id}")
    if active:
        defect(defects, path, "final active scheduler set must be empty after validation: " + ", ".join(_ordered_subset(active, expected_ids)))
    missing_launches = [item_id for item_id in expected_ids if item_id not in launched]
    if require_all_launched and missing_launches:
        defect(defects, path, "must launch every manifest scheduler item for pass/partial status: " + ", ".join(missing_launches))
    for item_id in missing_launches:
        failed_deps = failed_dependency_ids(item_id)
        if failed_deps:
            reason_code = (
                blocked_reason_codes.get(item_id)
                or deferred_reason_codes.get(item_id)
                or under_capacity_reason_codes.get(item_id)
            )
            if reason_code != "dependency_failed":
                defect(
                    defects,
                    path,
                    f"unlaunched item with non-pass dependency must record dependency_failed reason_code: {item_id} depends on {', '.join(failed_deps)}",
                )
        if item_id not in deferred and item_id not in blocked and item_id not in under_capacity:
            defect(defects, path, f"unlaunched manifest item lacks structured defer/under_capacity/blocked reason: {item_id}")

    return {
        "launched": _ordered_subset(launched, expected_ids),
        "finished": _ordered_subset(finished, expected_ids),
        "active": _ordered_subset(active, expected_ids),
        "blocked": _ordered_subset(set(blocked), expected_ids),
        "deferred": _ordered_subset(set(deferred) | set(under_capacity), expected_ids),
        "finished_status": {item_id: finished_status[item_id] for item_id in expected_ids if item_id in finished_status},
        "max_observed_active": max_observed,
    }


def validate_scheduler_artifact(
    defects: list[str],
    scheduler_path: Path,
    path: str,
    *,
    scheduler_kind: str,
    expected_path: str,
    expected_ids: list[str],
    dependencies: dict[str, list[str]],
    capacity: int,
    manifest_path: Path | None = None,
    allowed_manifest_sha256s: set[str] | None = None,
    require_all_launched: bool = False,
) -> dict[str, list[str] | int]:
    if not scheduler_path.exists():
        defect(defects, path, f"scheduler artifact does not exist: {scheduler_path}")
        return {
            "launched": [],
            "finished": [],
            "active": [],
            "blocked": [],
            "deferred": [],
            "finished_status": {},
            "max_observed_active": 0,
        }
    ledger = load_json_artifact(defects, scheduler_path, path)
    return validate_scheduler_ledger(
        defects,
        ledger,
        path,
        scheduler_kind=scheduler_kind,
        expected_path=expected_path,
        expected_ids=expected_ids,
        dependencies=dependencies,
        capacity=capacity,
        manifest_path=manifest_path,
        allowed_manifest_sha256s=allowed_manifest_sha256s,
        require_all_launched=require_all_launched,
    )


def validate_scheduler_rollup(
    defects: list[str],
    value: object,
    path: str,
    *,
    expected_path: str,
    summary: dict[str, list[str] | int],
    max_capacity: int,
) -> str:
    data = require_object(defects, value, path)
    required = [
        "scheduler_path",
        "launched_ids",
        "finished_ids",
        "active_ids",
        "blocked_ids",
        "deferred_ids",
        "max_observed_active",
    ]
    for key in required:
        if key not in data:
            defect(defects, path, f"missing key: {key}")
    scheduler_path = require_string(defects, data.get("scheduler_path"), f"{path}.scheduler_path")
    if scheduler_path and scheduler_path != expected_path:
        defect(defects, f"{path}.scheduler_path", f"must be {expected_path!r}")
    for field, summary_key in [
        ("launched_ids", "launched"),
        ("finished_ids", "finished"),
        ("active_ids", "active"),
        ("blocked_ids", "blocked"),
        ("deferred_ids", "deferred"),
    ]:
        values = require_string_list(defects, data.get(field), f"{path}.{field}")
        expected_values = summary.get(summary_key, [])
        if isinstance(expected_values, list) and values != expected_values:
            defect(defects, f"{path}.{field}", "must match scheduler ledger reconstruction exactly")
    observed = data.get("max_observed_active")
    if not isinstance(observed, int) or isinstance(observed, bool) or observed < 0 or observed > max_capacity:
        defect(defects, f"{path}.max_observed_active", f"must be an integer from 0 to {max_capacity}")
    elif observed != summary.get("max_observed_active"):
        defect(defects, f"{path}.max_observed_active", "must match scheduler ledger reconstruction exactly")
    return scheduler_path


def validate_pre_review_gate_artifact(
    defects: list[str],
    gate_path: Path,
    path: str,
    *,
    manifest_path: Path,
    branch_id: str,
    review_packet_id: str | None = None,
    required_input_paths: list[str] | None = None,
    allowed_hashes_by_rel_path: dict[str, set[str]] | None = None,
) -> dict:
    if not gate_path.exists():
        defect(defects, path, f"pre-review gate artifact does not exist: {gate_path}")
        return {}
    gate = require_object(defects, load_json_artifact(defects, gate_path, path), path)
    if gate.get("schema_version") != 2:
        defect(defects, f"{path}.schema_version", "must be 2")
    if gate.get("branch_id") != branch_id:
        defect(defects, f"{path}.branch_id", f"must be {branch_id!r}")
    if gate.get("status") not in {"pass", "failed"}:
        defect(defects, f"{path}.status", "must be 'pass' or 'failed'")
    if gate.get("status") != "pass":
        defect(defects, f"{path}.status", "must be pass before reviewer launch or accepted review")
    packet_value = require_string(defects, gate.get("review_packet_id"), f"{path}.review_packet_id")
    if review_packet_id and packet_value and packet_value != review_packet_id:
        defect(defects, f"{path}.review_packet_id", f"must be {review_packet_id!r}")

    checks = require_object(defects, gate.get("checks"), f"{path}.checks")
    required_checks = [
        "manifest_validation",
        "status_validation",
        "tests",
        "diff_check",
        "artifacts_fresh",
        "worker_evidence",
        "ownership",
        "dod_evidence",
    ]
    for key in required_checks:
        check = require_object(defects, checks.get(key), f"{path}.checks.{key}")
        status = check.get("status")
        if key == "tests" and status == "skipped":
            if check.get("skip_allowed") is not True:
                defect(defects, f"{path}.checks.{key}.skip_allowed", "must be true when tests are skipped")
            require_string(defects, check.get("reason"), f"{path}.checks.{key}.reason")
            continue
        if status != "pass":
            defect(defects, f"{path}.checks.{key}.status", "must be pass")
    commands = require_string_list(defects, gate.get("commands_run"), f"{path}.commands_run", min_items=1)
    manifest = load_json_artifact(defects, manifest_path, f"{path}.manifest")
    validate_base_range_diff_check(defects, commands, f"{path}.commands_run", manifest)
    dod = require_object(defects, checks.get("dod_evidence"), f"{path}.checks.dod_evidence")
    require_string_list(defects, dod.get("items"), f"{path}.checks.dod_evidence.items", min_items=1)
    semantic_hashes = relative_hashes(
        defects,
        gate.get("semantic_input_hashes"),
        f"{path}.semantic_input_hashes",
        root_dir=manifest_path.parent,
        allowed_hashes_by_rel_path=allowed_hashes_by_rel_path,
    )
    validate_pre_review_volatile_inputs(defects, gate.get("volatile_input_hashes", {}), f"{path}.volatile_input_hashes")
    for rel_path in required_input_paths or []:
        if rel_path not in semantic_hashes:
            defect(defects, f"{path}.semantic_input_hashes", f"must include current semantic input hash for {rel_path}")
    validate_reuse_eligibility(defects, gate.get("reuse_eligibility"), f"{path}.reuse_eligibility", semantic_hashes=semantic_hashes)
    validate_reuse_policy(defects, gate.get("reuse_policy"), f"{path}.reuse_policy")
    return gate


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
        avoids_action = require_string(defects, data.get("avoids_action"), f"{item_path}.avoids_action")
        expected_savings_reason = require_string(defects, data.get("expected_savings_reason"), f"{item_path}.expected_savings_reason")
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
        if avoids_action and avoids_action != inputs_data.get("avoids_action"):
            defect(defects, f"{item_path}.avoids_action", "must match input-files.json avoids_action")
        if expected_savings_reason and expected_savings_reason != inputs_data.get("expected_savings_reason"):
            defect(defects, f"{item_path}.expected_savings_reason", "must match input-files.json expected_savings_reason")
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
