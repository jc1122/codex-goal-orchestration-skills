#!/usr/bin/env python3
"""Summarize deterministic telemetry artifacts for a prepared goal bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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


CONTRACT = _load_contract()
is_bridge_alias = CONTRACT.is_bridge_alias

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cached_input_tokens",
    "total_tokens",
)
TELEMETRY_ROOTS = ("audit", "workers", "research", "reviewers", "lite", "amendments")
APPROX_CHARS_PER_TOKEN = 4
TOKEN_PRESSURE_INPUT_WARN_MIN = 20_000
TOKEN_PRESSURE_INPUT_WARN_RATIO = 8
# Alias classification consumes the post-migration route surface from the
# shared contract: native codex (gpt-5.5/gpt-5.4/codex-spark/codex-mini/research)
# plus the opencode-bridge deepseek aliases (ds-pro-max=TOUGH, ds-flash-max=LIGHT).
# Legacy non-codex provider aliases were dropped in the worker migration.
#
# PREMIUM (demanding/heavy) aliases: the bridge "pro-max" deepseek route and the
# native heavy/standard codex routes plus research.
PREMIUM_ALIASES = frozenset(
    {"ds-pro-max", "gpt-5.5", "gpt-5.4", "codex-research"}
    | {alias for alias in CONTRACT.BRIDGE_ROUTE_ALIASES if CONTRACT.bridge_variant(alias) == "max" and "pro" in alias}
)
# LIGHT (cheap/mini) aliases: the bridge "flash-max" deepseek route and the native
# bounded codex routes (spark, mini, research-mini).
LIGHT_ALIASES = frozenset(
    {"ds-flash-max", "codex-spark", "codex-mini", "codex-research-mini"}
    | {alias for alias in CONTRACT.BRIDGE_ROUTE_ALIASES if "flash" in alias}
)
# Per-alias mini/light buckets retained in the cost summary. Kept stable for the
# main-status telemetry-summary validator, extended with the bridge light route.
MINI_SPARK_ALIASES = ("codex-mini", "codex-spark", "ds-flash-max")
DEBUG_TELEMETRY_FILENAME = "telemetry.debug.json"
DEBUG_EVENTS_FILENAME = "debug.events.jsonl"
RUN_TRACE_FILENAME = "run.trace.jsonl"
PREFLIGHT_PIPELINE_FILENAME = "preflight.pipeline.json"
STALE_ARTIFACTS_INDEX = "stale-artifacts.index.json"
KNOWN_CACHE_ROOTS = (".runtime-cache/", ".pytest_cache/", "xdg-cache/", ".cache/")
KNOWN_CACHE_FILES = (
    "tmp/codex-bwrap-synthetic-mount-targets-1000/lock",
    "unleash-repo-schema-v1-codeium-language-server.json",
)
KNOWN_CACHE_EXTENSIONS = (".lock",)
SCOPE_NAMES = ("current", "attempt_history", "stale_archive")
TRACE_SCOPE_ALL = "all"
OUTCOME_BUCKETS = (
    "pass",
    "timeout",
    "transport_disconnect",
    "schema_readback_failure",
    "dirty_stop",
    "manual_or_kill",
    "not_called_dirty_stop",
    "unknown",
)
OUTCOME_KEY_TIMEOUT = "timeout"
OUTCOME_KEY_TRANSPORT = "transport_disconnect"
OUTCOME_KEY_SCHEMA = "schema_readback_failure"
OUTCOME_KEY_DIRTY = "dirty_stop"
OUTCOME_KEY_MANUAL = "manual_or_kill"
OUTCOME_KEY_NOT_CALLED = "not_called_dirty_stop"
OUTCOME_KEY_PASS = "pass"
OUTCOME_KEY_UNKNOWN = "unknown"


def zero_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def add_usage(target: dict[str, int], usage: object) -> None:
    if not isinstance(usage, dict):
        return
    for key in USAGE_KEYS:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            target[key] = target.get(key, 0) + value


def add_number(target: dict[str, Any], key: str, value: object) -> None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        target[key] = target.get(key, 0) + value


def read_attempt_launch_events(launcher_state_path: Path) -> dict[int, dict[str, Any]]:
    data = read_json_object(launcher_state_path)
    if data is None:
        return {}
    events = data.get("events")
    if not isinstance(events, list):
        return {}
    by_attempt_index: dict[int, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        attempt_index = event.get("attempt_index")
        if not isinstance(attempt_index, int):
            continue
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = -1
        existing = by_attempt_index.get(attempt_index)
        if existing is None:
            by_attempt_index[attempt_index] = event
            continue
        existing_seq = existing.get("seq")
        if not isinstance(existing_seq, int) or isinstance(existing_seq, bool):
            existing_seq = -1
        if seq >= existing_seq:
            by_attempt_index[attempt_index] = event
    return by_attempt_index


def attempt_index_from_attempt(attempt: dict[str, Any]) -> int:
    candidate_index = attempt.get("candidate_attempts_index")
    if isinstance(candidate_index, int) and not isinstance(candidate_index, bool):
        return max(0, candidate_index - 1)
    fallback = attempt.get("attempt_index")
    if isinstance(fallback, int) and not isinstance(fallback, bool):
        return fallback
    return -1


def read_packet_debug_end_event(path: Path) -> tuple[float | None, int | None, str | None]:
    debug_events = path / "debug.events.jsonl"
    if not debug_events.exists():
        return None, None, None
    elapsed_seconds: float | None = None
    exit_status: int | None = None
    status: str | None = None
    try:
        for line in debug_events.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") != "end":
                continue
            value = event.get("elapsed_ms")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0 or isinstance(value, float) and value >= 0.0:
                elapsed_seconds = value / 1000
            if isinstance(event.get("exit_status"), int) and not isinstance(event.get("exit_status"), bool):
                exit_status = event.get("exit_status")
            if isinstance(event.get("status"), str):
                status = event.get("status")
        return elapsed_seconds, exit_status, status
    except Exception:
        return None, None, None


def read_debug_event_timing(path: Path) -> tuple[dict[int, float], dict[int, int | None], dict[int, str | None]]:
    debug_events = path / "debug.events.jsonl"
    if not debug_events.exists():
        return {}, {}, {}
    attempt_elapsed: dict[int, float] = {}
    attempt_exit_status: dict[int, int | None] = {}
    attempt_status: dict[int, str | None] = {}
    try:
        for line in debug_events.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            attempt_index = event.get("attempt_index")
            if attempt_index is None:
                attempt_index = -1
            if not isinstance(attempt_index, int) or isinstance(attempt_index, bool):
                continue
            if event.get("event") != "end":
                continue
            value = event.get("elapsed_ms")
            elapsed_seconds: float | None = None
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0 or isinstance(value, float) and value >= 0.0:
                elapsed_seconds = value / 1000
            if elapsed_seconds is not None:
                attempt_elapsed[attempt_index] = elapsed_seconds
            exit_status = event.get("exit_status")
            if isinstance(exit_status, int) and not isinstance(exit_status, bool):
                attempt_exit_status[attempt_index] = exit_status
            status = event.get("status")
            if isinstance(status, str) and status.strip():
                attempt_status[attempt_index] = status
    except Exception:
        return {}, {}, {}
    return attempt_elapsed, attempt_exit_status, attempt_status


def is_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) or isinstance(value, float)


def normalize_text_reason(value: object) -> str:
    return str(value).strip().lower() if isinstance(value, str) else ""


def attempt_elapsed_seconds(
    attempt: dict[str, Any],
    packet_elapse: float | None,
    called_attempt_count: int,
    *,
    called: bool = False,
    attempt_index: int | None = None,
    launch_event: dict[str, Any] | None = None,
    debug_event_elapsed: dict[int, float] | None = None,
) -> tuple[float | None, str | None]:
    timing = attempt.get("timing") if isinstance(attempt.get("timing"), dict) else {}
    if timing is None:
        timing = {}
    elapsed = timing.get("elapsed_seconds")
    if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
        return float(elapsed), "attempt"
    if isinstance(elapsed, float) and elapsed >= 0.0:
        return elapsed, "attempt"
    if called and isinstance(launch_event, dict):
        launch_elapsed = launch_event.get("elapsed")
        if isinstance(launch_elapsed, int) and not isinstance(launch_elapsed, bool) and launch_elapsed >= 0:
            return float(launch_elapsed), "launcher_state"
        if isinstance(launch_elapsed, float) and launch_elapsed >= 0.0:
            return float(launch_elapsed), "launcher_state"
        launch_elapsed_ms = launch_event.get("elapsed_ms")
        if isinstance(launch_elapsed_ms, int) and not isinstance(launch_elapsed_ms, bool) and launch_elapsed_ms >= 0:
            return launch_elapsed_ms / 1000, "launcher_state"
        if isinstance(launch_elapsed_ms, float) and launch_elapsed_ms >= 0.0:
            return launch_elapsed_ms / 1000, "launcher_state"

    if called and debug_event_elapsed is not None and isinstance(attempt_index, int):
        debug_elapsed = debug_event_elapsed.get(attempt_index)
        if isinstance(debug_elapsed, (int, float)) and debug_elapsed >= 0:
            return float(debug_elapsed), "debug_event"
    timing_source = normalize_text_reason(timing.get("timing_source"))
    if (
        called
        and timing_source in {"packet_debug_events", "debug.events", "debug_events", "debug"}
        and called_attempt_count == 1
    ):
        if packet_elapse is not None and packet_elapse >= 0:
            return float(packet_elapse), timing_source
    if (
        called
        and timing_source == "packet_debug_events"
        and called_attempt_count == 1
        and isinstance(packet_elapse, (int, float))
        and packet_elapse >= 0
    ):
        return float(packet_elapse), "packet_debug_events"
    if called and called_attempt_count == 1 and isinstance(packet_elapse, (int, float)) and packet_elapse >= 0:
        return float(packet_elapse), "packet"
    return None, None


def attempt_is_called(attempt: dict[str, Any]) -> bool:
    return attempt.get("called") is True


def attempt_provenance_level(attempt: dict[str, Any], launch_event: dict[str, Any] | None) -> str:
    usage = attempt.get("usage")
    if isinstance(usage, dict):
        has_known_usage = any(
            isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool) for key in USAGE_KEYS
        )
    else:
        has_known_usage = False
    if has_known_usage:
        return "high"
    if launch_event is not None and isinstance(launch_event, dict):
        return "low"
    return "low"


def attempt_exit_code(launch_event: dict[str, Any] | None, attempt: dict[str, Any]) -> int | None:
    if launch_event is None:
        return None
    returncode = launch_event.get("returncode")
    if isinstance(returncode, int) and not isinstance(returncode, bool):
        return returncode
    # Fall back to structured fields if present in debug telemetry for older artifacts.
    if isinstance(attempt.get("return_code"), int) and not isinstance(attempt.get("return_code"), bool):
        return attempt.get("return_code")
    return None


def attempt_timed_out(
    attempt: dict[str, Any], launch_event: dict[str, Any] | None, packet_status: str | None = None
) -> bool | None:
    if attempt.get("called") is not True:
        return None
    timing = attempt.get("timing") if isinstance(attempt.get("timing"), dict) else {}
    timing_timed_out = timing.get("timed_out") if isinstance(timing, dict) else None
    if timing_timed_out is True:
        return True
    if timing_timed_out is False:
        return False
    if launch_event is not None:
        state = normalize_text_reason(launch_event.get("state"))
        if state == "timeout":
            return True
    if packet_status == "timeout":
        return True
    return None


def classify_attempt_outcome(
    attempt: dict[str, Any],
    launch_event: dict[str, Any] | None,
) -> str:
    called = attempt.get("called") is True
    not_executed_reason = normalize_text_reason(attempt.get("not_executed_reason"))
    state = normalize_text_reason(launch_event.get("state")) if isinstance(launch_event, dict) else ""
    route_health = launch_event.get("route_health") if isinstance(launch_event, dict) else {}
    dirty = False
    if isinstance(route_health, dict):
        dirty = route_health.get("dirty") is True
    if attempt.get("accepted") is True:
        return OUTCOME_KEY_PASS
    if state in {"pass", "partial"}:
        return OUTCOME_KEY_PASS

    if not called:
        if "manual" in not_executed_reason or "kill" in not_executed_reason:
            return OUTCOME_KEY_MANUAL
        if "dirty" in not_executed_reason or "fallback" in not_executed_reason:
            return OUTCOME_KEY_NOT_CALLED
        return OUTCOME_KEY_UNKNOWN

    if launch_event is None or not isinstance(launch_event, dict):
        timed_out = attempt_timed_out(attempt, None)
        if timed_out is True:
            return OUTCOME_KEY_TIMEOUT
        return OUTCOME_KEY_UNKNOWN

    failure_class = normalize_text_reason(launch_event.get("failure_class"))
    failure_subclass = normalize_text_reason(launch_event.get("failure_subclass"))
    stop_text = " ".join(part for part in (not_executed_reason, failure_class, failure_subclass) if part)
    timed_out = attempt_timed_out(attempt, launch_event)
    manual_or_kill = "manual" in stop_text or "kill" in stop_text
    route_health = launch_event.get("route_health")
    route_transport_disconnect = False
    if isinstance(route_health, dict):
        route_disconnect_count = route_health.get("transport_disconnect_count")
        route_transport_disconnect = isinstance(route_disconnect_count, int) and route_disconnect_count > 0

    if failure_class in {"manual", "kill", "manual_interrupt", "manual_abort"}:
        return OUTCOME_KEY_MANUAL
    if timed_out is True or failure_class == "timeout":
        return OUTCOME_KEY_TIMEOUT
    if failure_class in {"manual", "kill", "manual_interrupt", "manual_abort"}:
        return OUTCOME_KEY_MANUAL
    if manual_or_kill:
        return OUTCOME_KEY_MANUAL
    if failure_subclass == OUTCOME_KEY_TRANSPORT or route_transport_disconnect:
        return OUTCOME_KEY_TRANSPORT
    if failure_class in {"schema_or_output_readback", "schema", "readback"} or (
        "schema" in failure_class or "schema" in failure_subclass or "readback" in failure_subclass
    ):
        return OUTCOME_KEY_SCHEMA
    if state in {"fail-dirty", "blocked"} and (dirty or launch_event.get("dirty") is True):
        return OUTCOME_KEY_DIRTY
    if state in {"fail-dirty", "fail-clean", "blocked", "partial", "pass"} and "dirty" in stop_text:
        return OUTCOME_KEY_DIRTY
    if timed_out is True or failure_class == "timeout":
        return OUTCOME_KEY_TIMEOUT

    if state == "timeout" or timed_out is True:
        return OUTCOME_KEY_TIMEOUT
    if state in {"blocked", "fail-clean", "fail-dirty"}:
        return OUTCOME_KEY_UNKNOWN
    return OUTCOME_KEY_UNKNOWN


def discover_telemetry_files(bundle_dir: Path, *, debug: bool = False) -> list[Path]:
    filename = DEBUG_TELEMETRY_FILENAME if debug else "telemetry.json"
    files: list[Path] = []
    for root_name in TELEMETRY_ROOTS:
        root = bundle_dir / root_name
        if root.is_dir():
            files.extend(sorted(root.glob(f"**/{filename}")))
    return sorted(files, key=lambda path: path.relative_to(bundle_dir).as_posix())


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("telemetry artifact must be a JSON object")
    return data


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_size(path: Path) -> int | None:
    if not path.exists() or not path.is_file():
        return None
    return path.stat().st_size


def rel_path(bundle_dir: Path, path: Path) -> str:
    return path.relative_to(bundle_dir).as_posix()


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def iso_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return None


def iso_timestamp_str(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).isoformat()
    except Exception:
        return None


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def is_synthetic_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return value.startswith("2000-01-01T00:00:")


def load_stale_artifact_paths(bundle_dir: Path) -> set[str]:
    path = bundle_dir / STALE_ARTIFACTS_INDEX
    data = read_json_object(path)
    if not isinstance(data, dict):
        return set()
    entries = data.get("entries")
    if not isinstance(entries, list):
        return set()
    result: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        artifact_path = entry.get("artifact_path")
        if isinstance(artifact_path, str):
            result.add(artifact_path)
    return result


def classify_telemetry_scope(relative_path: str, stale_artifact_paths: set[str]) -> str:
    if relative_path in stale_artifact_paths:
        return "stale_archive"
    if "/attempts/" in relative_path:
        return "attempt_history"
    return "current"


def is_cache_or_temp_artifact(path: Path) -> bool:
    as_posix = path.as_posix()
    if any(root in as_posix for root in KNOWN_CACHE_ROOTS):
        if any(cache_file in as_posix for cache_file in KNOWN_CACHE_FILES):
            return True
        if any(as_posix.endswith(ext) for ext in KNOWN_CACHE_EXTENSIONS):
            return True
    return False


def summarize_runtime_cache_artifacts(bundle_dir: Path) -> dict[str, Any]:
    artifact_entries: list[dict[str, Any]] = []
    total_bytes = 0
    total_count = 0
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file():
            continue
        if not is_cache_or_temp_artifact(path):
            continue
        sha = file_sha256(path)
        size = file_size(path)
        if size is not None:
            total_bytes += size
        total_count += 1
        artifact_entries.append(
            {
                "path": path.relative_to(bundle_dir).as_posix(),
                "size_bytes": size,
                "sha256": sha,
            }
        )
    return {
        "cache_artifact_count": total_count,
        "cache_artifact_bytes": total_bytes,
        "artifacts": artifact_entries,
        "summary_note": "Runtime cache and temp artifacts are summarized by checksum/size and excluded from retained evidence counts.",
    }


def compact_artifact_ref(bundle_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "path": rel_path(bundle_dir, path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
    }


def relative_or_posix(bundle_dir: Path, value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute():
        try:
            return path.relative_to(bundle_dir).as_posix()
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def append_trace_event(events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    event.setdefault("schema_version", 1)
    events.append(event)


def iter_scheduler_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    scheduler_dir = bundle_dir / "schedulers"
    if not scheduler_dir.is_dir():
        return events
    for path in sorted(scheduler_dir.glob("*.json")):
        data = read_json_object(path)
        if data is None:
            append_trace_event(
                events,
                {
                    "event_type": "trace_defect",
                    "source": rel_path(bundle_dir, path),
                    "message": "scheduler ledger is not readable JSON object",
                },
            )
            continue
        ledger_events = data.get("events") if isinstance(data.get("events"), list) else []
        for item in ledger_events:
            if not isinstance(item, dict):
                continue
            trace = {
                "event_type": "scheduler_event",
                "source": rel_path(bundle_dir, path),
                "scheduler_kind": data.get("scheduler_kind"),
                "scheduler_path": data.get("scheduler_path"),
                "capacity": data.get("capacity"),
                "item_ids": data.get("item_ids") if isinstance(data.get("item_ids"), list) else None,
                "scheduler_seq": item.get("seq"),
                "timestamp": item.get("timestamp"),
                "wall_clock_timestamp": item.get("wall_clock_timestamp"),
                "runtime_ref": item.get("runtime_ref"),
                "event": item.get("event"),
                "id": item.get("id"),
                "status": item.get("status"),
                "reason_code": item.get("reason_code"),
                "reason": item.get("reason"),
                "eligible_ids": item.get("eligible_ids") if isinstance(item.get("eligible_ids"), list) else None,
            }
            append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


def iter_debug_event_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for root_name in TELEMETRY_ROOTS:
        root = bundle_dir / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"**/{DEBUG_EVENTS_FILENAME}")):
            for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    append_trace_event(
                        events,
                        {
                            "event_type": "trace_defect",
                            "source": rel_path(bundle_dir, path),
                            "line": line_no,
                            "message": "debug event line is not valid JSON",
                        },
                    )
                    continue
                if not isinstance(data, dict):
                    continue
                trace = {
                    "event_type": "packet_debug_event",
                    "source": rel_path(bundle_dir, path),
                    "line": line_no,
                    "timestamp": data.get("timestamp"),
                    "packet_id": data.get("packet_id"),
                    "role": data.get("role"),
                    "phase": data.get("phase"),
                    "event": data.get("event"),
                    "elapsed_ms": data.get("elapsed_ms"),
                    "status": data.get("status"),
                    "exit_status": data.get("exit_status"),
                }
                append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


def iter_launcher_state_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for root_name in TELEMETRY_ROOTS:
        root = bundle_dir / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/launcher-state.json")):
            data = read_json_object(path)
            if data is None:
                append_trace_event(
                    events,
                    {
                        "event_type": "trace_defect",
                        "source": rel_path(bundle_dir, path),
                        "message": "launcher state is not readable JSON object",
                    },
                )
                continue
            state_events = data.get("events") if isinstance(data.get("events"), list) else []
            for item in state_events:
                if not isinstance(item, dict):
                    continue
                trace = {
                    "event_type": "launcher_state",
                    "source": rel_path(bundle_dir, path),
                    "packet_id": data.get("packet_id"),
                    "role": data.get("role"),
                    "terminal_state": data.get("terminal_state"),
                    "state_seq": item.get("seq"),
                    "state": item.get("state"),
                    "attempt_index": item.get("attempt_index"),
                    "alias": item.get("alias"),
                    "provider": item.get("provider"),
                    "model": item.get("model"),
                    "returncode": item.get("returncode"),
                    "dirty": item.get("dirty"),
                    "output_nonempty": item.get("output_nonempty"),
                    "message": item.get("message"),
                }
                append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


def iter_debug_telemetry_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    stale_artifact_paths = load_stale_artifact_paths(bundle_dir)
    for path in discover_telemetry_files(bundle_dir, debug=True):
        data = read_json_object(path)
        if data is None:
            append_trace_event(
                events,
                {
                    "event_type": "trace_defect",
                    "source": rel_path(bundle_dir, path),
                    "message": "debug telemetry is not readable JSON object",
                },
            )
            continue
        packet_id = data.get("packet_id")
        role = data.get("role")
        scope = classify_telemetry_scope(path.relative_to(bundle_dir).as_posix(), stale_artifact_paths)
        packet_event = {
            "event_type": "packet_telemetry",
            "source": rel_path(bundle_dir, path),
            "packet_id": packet_id,
            "role": role,
            "scope": scope,
            "route_class": data.get("route_class"),
            "prompt_artifact": data.get("prompt_artifact"),
            "output_artifact": data.get("output_artifact"),
            "text_metrics": data.get("text_metrics") if isinstance(data.get("text_metrics"), dict) else None,
            "success_metrics": data.get("success_metrics") if isinstance(data.get("success_metrics"), dict) else None,
        }
        append_trace_event(events, {key: value for key, value in packet_event.items() if value is not None})
        model_usage = data.get("model_usage") if isinstance(data.get("model_usage"), dict) else {}
        attempts = model_usage.get("attempts") if isinstance(model_usage.get("attempts"), list) else []
        launch_events = read_attempt_launch_events(path.parent / "launcher-state.json")
        debug_event_elapsed, debug_event_exit_status, debug_event_status = read_debug_event_timing(path.parent)
        packet_elapsed_seconds, packet_exit_status, packet_status = read_packet_debug_end_event(path.parent)
        if packet_exit_status is None:
            packet_exit_status = debug_event_exit_status.get(-1)
        if packet_status is None:
            packet_status = debug_event_status.get(-1)
        called_attempt_count = len([item for item in attempts if isinstance(item, dict) and item.get("called") is True])
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            attempt_index = attempt_index_from_attempt(attempt)
            launch_event = launch_events.get(attempt_index)
            outcome = classify_attempt_outcome(attempt, launch_event)
            provenance = attempt_provenance_level(attempt, launch_event)
            exit_code = attempt_exit_code(launch_event, attempt)
            if exit_code is None and packet_exit_status is not None:
                exit_code = packet_exit_status
            elapsed_seconds, elapsed_source = attempt_elapsed_seconds(
                attempt,
                packet_elapsed_seconds,
                called_attempt_count,
                called=attempt.get("called") is True,
                attempt_index=attempt_index,
                launch_event=launch_event,
                debug_event_elapsed=debug_event_elapsed,
            )
            timed_out = attempt_timed_out(attempt, launch_event, packet_status)
            raw_timed_out = timed_out
            if outcome == OUTCOME_KEY_PASS and timed_out is True:
                timed_out = False
            stop_reason = normalize_text_reason(attempt.get("not_executed_reason"))
            if not stop_reason and isinstance(launch_event, dict):
                stop_reason = normalize_text_reason(launch_event.get("failure_subclass"))
            if not stop_reason:
                if timed_out is True:
                    stop_reason = OUTCOME_KEY_TIMEOUT
                elif launch_event is not None and normalize_text_reason(launch_event.get("state")):
                    stop_reason = normalize_text_reason(launch_event.get("state"))
                else:
                    stop_reason = None
            if timed_out is None and isinstance(launch_event, dict) and launch_event.get("state") == "timeout":
                timed_out = True
            if elapsed_source is None:
                elapsed_source = "model_usage"
            trace = {
                "event_type": "model_attempt",
                "source": rel_path(bundle_dir, path),
                "packet_id": packet_id,
                "role": role,
                "attempt_index": attempt_index,
                "alias": attempt.get("alias"),
                "provider": attempt.get("provider"),
                "model": attempt.get("model"),
                "effort": attempt.get("effort"),
                "timeout_seconds": attempt.get("timeout_seconds"),
                "called": attempt.get("called"),
                "accepted": attempt.get("accepted"),
                "outcome": outcome,
                "stop_reason": stop_reason or None,
                "provenance_level": provenance,
                "exit_code": exit_code,
                "stdout_bytes": attempt.get("stdout_bytes"),
                "stderr_bytes": attempt.get("stderr_bytes"),
                "usage": attempt.get("usage") if isinstance(attempt.get("usage"), dict) else None,
                "timed_out": timed_out,
                "raw_timed_out": raw_timed_out,
                "elapsed_seconds": elapsed_seconds,
                "elapsed_seconds_source": elapsed_source,
                "scope": scope,
            }
            append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


TERMINAL_ARTIFACT_PATTERNS = (
    "main.status.json",
    "audit/prompt-audit.json",
    "audit/prompt-audit-phase.json",
    "branches/*.status.json",
    "branches/*.review.json",
    "branches/*.pre_review_gate.json",
    "workers/*/status.json",
    "workers/*/packet.summary.json",
    "research/*/research.json",
    "research/*/packet.summary.json",
    "reviewers/*/review.json",
    "reviewers/*/packet.summary.json",
    "lite/*/advice.json",
    "amendments/*.decision.json",
    "amendments/*.proposal.json",
    "amendments/*.validation.json",
    "amendments/*.accepted.json",
)


STATE_CHANGE_KIND_ACTIONS = {
    "main_status": "main_status_write",
    "prompt_audit": "prompt_audit_write",
    "prompt_audit_phase": "prompt_audit_phase_write",
    "branch_status": "branch_status_write",
    "review": "review_write",
    "pre_review_gate": "pre_review_gate_write",
    "worker_status": "worker_status_write",
    "packet_summary": "packet_summary_write",
    "research": "research_write",
    "lite_advice": "lite_advice_write",
    "amendment_decision": "amendment_decision_write",
    "amendment_proposal": "amendment_proposal_write",
    "amendment_validation": "amendment_validation_write",
    "amendment_acceptance": "amendment_acceptance_write",
}


def terminal_artifact_kind(path: Path) -> str:
    parts = path.parts
    if path.name == "prompt-audit.json":
        return "prompt_audit"
    if path.name == "prompt-audit-phase.json":
        return "prompt_audit_phase"
    if path.name == "main.status.json":
        return "main_status"
    if path.name == "status.json" and "workers" in parts:
        return "worker_status"
    if path.name == "packet.summary.json":
        return "packet_summary"
    if path.name.endswith(".status.json"):
        return "branch_status"
    if path.name.endswith(".review.json") or path.name == "review.json":
        return "review"
    if path.name.endswith(".pre_review_gate.json"):
        return "pre_review_gate"
    if path.name == "research.json":
        return "research"
    if path.name == "advice.json":
        return "lite_advice"
    if path.name.endswith(".decision.json"):
        return "amendment_decision"
    if path.name.endswith(".proposal.json"):
        return "amendment_proposal"
    if path.name.endswith(".validation.json"):
        return "amendment_validation"
    if path.name.endswith(".accepted.json"):
        return "amendment_acceptance"
    return "artifact"


def manifest_epoch_from_bundle(bundle_dir: Path) -> str | None:
    manifest_path = bundle_dir / "job.manifest.json"
    data = read_json_object(manifest_path)
    if not isinstance(data, dict):
        return None
    value = data.get("manifest_epoch") or data.get("epoch") or "current"
    return str(value)


def state_change_actor(kind: str) -> str:
    if kind.startswith("amendment_"):
        return "goal-plan-amender"
    if kind in {"main_status", "prompt_audit", "prompt_audit_phase"}:
        return "goal-main-orchestrator"
    if kind in {"branch_status", "review", "pre_review_gate", "worker_status", "research"}:
        return "goal-branch-orchestrator"
    if kind in {"packet_summary", "lite_advice"}:
        return "runtime-packet-runner"
    return "goal-orchestration-skill"


def state_change_state(data: dict[str, Any] | None, kind: str) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("status", "verdict", "decision", "terminal_state", "can_start"):
        value = data.get(key)
        if isinstance(value, (str, bool)):
            return str(value)
    if kind == "pre_review_gate":
        value = data.get("overall_status")
        if isinstance(value, str):
            return value
    return None


def state_change_previous_state(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    stale = data.get("stale_reason")
    if isinstance(stale, str) and stale.strip():
        return f"stale:{stale}"
    previous = data.get("previous_state")
    if isinstance(previous, str) and previous.strip():
        return previous
    return None


def terminal_artifact_trace_record(bundle_dir: Path, path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    data = read_json_object(path)
    artifact = compact_artifact_ref(bundle_dir, path)
    kind = terminal_artifact_kind(path)
    trace = {
        "event_type": "terminal_artifact",
        "source": rel_path(bundle_dir, path),
        "artifact_kind": kind,
        "artifact": artifact,
        "status": data.get("status") if isinstance(data, dict) else None,
        "review_status": data.get("review_status") if isinstance(data, dict) else None,
        "can_start": data.get("can_start") if isinstance(data, dict) else None,
        "packet_id": data.get("packet_id") if isinstance(data, dict) else None,
        "branch_id": data.get("branch_id") if isinstance(data, dict) else None,
        "job_id": data.get("job_id") if isinstance(data, dict) else None,
        "amendment_id": data.get("amendment_id") if isinstance(data, dict) else None,
    }
    state_change = {
        "event_type": "state_change",
        "source": rel_path(bundle_dir, path),
        "action_type": STATE_CHANGE_KIND_ACTIONS.get(kind, "artifact_write"),
        "actor": state_change_actor(kind),
        "artifact_kind": kind,
        "artifact_paths": [rel_path(bundle_dir, path)],
        "artifact_hashes": {rel_path(bundle_dir, path): artifact.get("sha256")},
        "manifest_epoch": manifest_epoch_from_bundle(bundle_dir),
        "previous_state": state_change_previous_state(data),
        "new_state": state_change_state(data, kind),
        "packet_id": trace.get("packet_id"),
        "branch_id": trace.get("branch_id"),
        "job_id": trace.get("job_id"),
        "amendment_id": trace.get("amendment_id"),
    }
    return (
        {key: value for key, value in trace.items() if value is not None},
        {key: value for key, value in state_change.items() if value is not None},
    )


def iter_terminal_artifact_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    paths: list[Path] = []
    for pattern in TERMINAL_ARTIFACT_PATTERNS:
        paths.extend(sorted(bundle_dir.glob(pattern)))
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        terminal_trace, state_change = terminal_artifact_trace_record(bundle_dir, path)
        append_trace_event(events, terminal_trace)
        append_trace_event(events, state_change)
    return events


def iter_preflight_pipeline_trace_events(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    path = bundle_dir / PREFLIGHT_PIPELINE_FILENAME
    if not path.exists():
        return events
    data = read_json_object(path)
    if data is None:
        append_trace_event(
            events,
            {
                "event_type": "trace_defect",
                "source": PREFLIGHT_PIPELINE_FILENAME,
                "message": "preflight pipeline is not readable JSON object",
            },
        )
        return events
    commands = data.get("commands") if isinstance(data.get("commands"), list) else []
    for index, command in enumerate(commands, start=1):
        if not isinstance(command, dict):
            continue
        artifact_delta = command.get("artifact_delta") if isinstance(command.get("artifact_delta"), dict) else None
        trace = {
            "event_type": "preflight_phase",
            "source": PREFLIGHT_PIPELINE_FILENAME,
            "preflight_seq": index,
            "phase": command.get("phase"),
            "returncode": command.get("returncode"),
            "elapsed_ms": command.get("elapsed_ms"),
            "stdout_bytes": command.get("stdout_bytes"),
            "stderr_bytes": command.get("stderr_bytes"),
            "command_hash": command.get("command_hash"),
            "output": relative_or_posix(bundle_dir, command.get("output")),
            "artifact_delta": artifact_delta,
        }
        append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


def preflight_pipeline_summary(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / PREFLIGHT_PIPELINE_FILENAME
    if not path.exists():
        return {
            "path": PREFLIGHT_PIPELINE_FILENAME,
            "exists": False,
            "phase_count": 0,
            "elapsed_ms": 0,
            "failed_phase_count": 0,
            "phases": [],
        }
    data = read_json_object(path)
    if data is None:
        return {
            "path": PREFLIGHT_PIPELINE_FILENAME,
            "exists": True,
            "readable": False,
            "phase_count": 0,
            "elapsed_ms": 0,
            "failed_phase_count": 0,
            "phases": [],
        }
    phases: list[dict[str, Any]] = []
    elapsed_total = 0
    failed_count = 0
    commands = data.get("commands") if isinstance(data.get("commands"), list) else []
    for command in commands:
        if not isinstance(command, dict):
            continue
        elapsed_ms = command.get("elapsed_ms")
        if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms >= 0:
            elapsed_total += elapsed_ms
        returncode = command.get("returncode")
        if isinstance(returncode, int) and not isinstance(returncode, bool) and returncode != 0:
            failed_count += 1
        artifact_delta = command.get("artifact_delta") if isinstance(command.get("artifact_delta"), dict) else {}
        phases.append(
            {
                "phase": command.get("phase"),
                "elapsed_ms": elapsed_ms,
                "returncode": returncode,
                "stdout_bytes": command.get("stdout_bytes"),
                "stderr_bytes": command.get("stderr_bytes"),
                "artifact_added_count": artifact_delta.get("added_count"),
                "artifact_modified_count": artifact_delta.get("modified_count"),
                "artifact_removed_count": artifact_delta.get("removed_count"),
            }
        )
    return {
        "path": PREFLIGHT_PIPELINE_FILENAME,
        "exists": True,
        "readable": True,
        "phase_count": len(phases),
        "elapsed_ms": elapsed_total,
        "failed_phase_count": failed_count,
        "status": data.get("status"),
        "result_kind": data.get("result_kind"),
        "phases": phases,
    }


def build_run_trace(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for logical_seq, producer in enumerate(
        (
            iter_preflight_pipeline_trace_events,
            iter_scheduler_trace_events,
            iter_debug_event_trace_events,
            iter_launcher_state_trace_events,
            iter_debug_telemetry_trace_events,
            iter_terminal_artifact_trace_events,
        ),
        start=1,
    ):
        producer_events = producer(bundle_dir)
        for event in producer_events:
            event["logical_seq"] = logical_seq * 1_000_000 + event.get("logical_seq", 0)
            events.append(event)

    recorded_at = now_utc_iso()

    def assign_wall_clock(event: dict[str, Any]) -> str | None:
        if isinstance(event.get("event_wall_clock"), str):
            return event.get("event_wall_clock")
        timestamp = event.get("timestamp")
        wall_clock = event.get("wall_clock_timestamp")
        source = event.get("source")
        if not isinstance(source, str):
            source = None
        source_wall_clock = None
        if source:
            try:
                file_path = bundle_dir / source
                if file_path.exists():
                    source_wall_clock = (
                        datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
                    )
            except Exception:
                source_wall_clock = None
        event_wall_clock = iso_timestamp_str(timestamp) or iso_timestamp_str(wall_clock) or source_wall_clock
        if event_wall_clock is not None:
            event["event_wall_clock"] = event_wall_clock
        if event.get("backfilled") is None:
            has_timestamp = isinstance(timestamp, str) or isinstance(wall_clock, str)
            if has_timestamp:
                event["backfilled"] = is_synthetic_timestamp(timestamp)
            else:
                event["backfilled"] = source_wall_clock is not None
        event["recorded_at"] = recorded_at
        event["event_wall_clock"] = event_wall_clock
        return event_wall_clock

    for event in events:
        if event.get("event_wall_clock") is not None:
            continue
        assign_wall_clock(event)

    def sort_key(event: dict[str, Any]) -> tuple[float, int, str, int]:
        event_wall_clock = event.get("event_wall_clock")
        if isinstance(event_wall_clock, str):
            wall_clock_sort = iso_timestamp(event_wall_clock) or 0.0
        else:
            wall_clock_sort = 0.0
        backfilled = 1 if event.get("backfilled") is True else 0
        source = event.get("source") if isinstance(event.get("source"), str) else ""
        source_seq = (
            event.get("scheduler_seq") or event.get("state_seq") or event.get("line") or event.get("attempt_index") or 0
        )
        if not isinstance(source_seq, int) or isinstance(source_seq, bool):
            source_seq = 0
        type_order = {
            "preflight_phase": 5,
            "scheduler_event": 10,
            "packet_debug_event": 20,
            "launcher_state": 30,
            "model_attempt": 40,
            "packet_telemetry": 50,
            "terminal_artifact": 60,
            "state_change": 70,
            "trace_defect": 90,
        }.get(str(event.get("event_type")), 80)
        return (wall_clock_sort, backfilled, type_order, source_seq, source)

    ordered = sorted(events, key=sort_key)
    for seq, event in enumerate(ordered, start=1):
        event["trace_seq"] = seq
    return ordered


def trace_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_types: dict[str, int] = {}
    for event in events:
        event_type = event.get("event_type") if isinstance(event.get("event_type"), str) else "unknown"
        event_types[event_type] = event_types.get(event_type, 0) + 1
    return {
        "path": RUN_TRACE_FILENAME,
        "event_count": len(events),
        "event_types": dict(sorted(event_types.items())),
        "raw_text_included": False,
    }


def ensure_bucket(groups: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
    if key not in groups:
        groups[key] = {
            "packet_count": 0,
            "attempts_declared": 0,
            "attempts_called": 0,
            "accepted_attempts": 0,
            "prompt_chars": 0,
            "prompt_bytes": 0,
            "output_chars": 0,
            "output_bytes": 0,
            "event_log_chars": 0,
            "event_log_bytes": 0,
            "model_prompt_chars_estimate": 0,
            "model_prompt_bytes_estimate": 0,
            "known_usage": zero_usage(),
        }
    return groups[key]


def zero_scope_totals() -> dict[str, Any]:
    return {
        "packet_count": 0,
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "prompt_chars": 0,
        "prompt_bytes": 0,
        "output_chars": 0,
        "output_bytes": 0,
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "model_prompt_chars_estimate": 0,
        "model_prompt_bytes_estimate": 0,
        "attempts_with_known_tokens": 0,
        "known_usage": zero_usage(),
    }


def zero_premium_bucket() -> dict[str, Any]:
    return {
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "known_usage": zero_usage(),
    }


def zero_alias_bucket() -> dict[str, Any]:
    return {
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "known_usage": zero_usage(),
    }


def compact_usage(usage: dict[str, int]) -> dict[str, int]:
    return {key: usage.get(key, 0) for key in USAGE_KEYS if usage.get(key, 0)}


def compact_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    result = dict(bucket)
    result["known_usage"] = compact_usage(result.get("known_usage", {}))
    return result


def compact_model_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    return {
        "attempts_declared": bucket.get("attempts_declared", 0),
        "attempts_called": bucket.get("attempts_called", 0),
        "accepted_attempts": bucket.get("accepted_attempts", 0),
        "known_usage": compact_usage(bucket.get("known_usage", {})),
    }


def attempt_group_key(role: str, attempt: dict[str, Any], scope: str = TRACE_SCOPE_ALL) -> str:
    normalized_scope = scope or TRACE_SCOPE_ALL
    return "\u001f".join(
        [
            role,
            str(attempt.get("provider") or ""),
            str(attempt.get("model") or ""),
            str(attempt.get("effort") or ""),
            str(attempt.get("alias") or ""),
            normalized_scope,
        ]
    )


def attempt_group_row(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    parts = key.split("\u001f")
    if len(parts) < 6:
        parts.extend([TRACE_SCOPE_ALL] * (6 - len(parts)))
    role, provider, model, effort, alias, scope = parts[:6]
    row = {
        "role": role,
        "provider": provider,
        "model": model,
        "effort": effort or None,
        "alias": alias,
        "scope": scope,
    }
    row.update(compact_bucket(bucket))
    return row


def token_pressure_warning(
    *,
    rel: str,
    packet_id: str,
    role: str,
    packet_prompt_chars: int,
    attempt: dict[str, Any],
) -> dict[str, Any] | None:
    usage = attempt.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens < 0:
        return None
    prompt_tokens_estimate = max(1, round(packet_prompt_chars / APPROX_CHARS_PER_TOKEN))
    threshold = max(TOKEN_PRESSURE_INPUT_WARN_MIN, prompt_tokens_estimate * TOKEN_PRESSURE_INPUT_WARN_RATIO)
    if input_tokens < threshold:
        return None
    cached_input_tokens = usage.get("cached_input_tokens")
    return {
        "path": rel,
        "packet_id": packet_id,
        "role": role,
        "alias": attempt.get("alias"),
        "provider": attempt.get("provider"),
        "model": attempt.get("model"),
        "prompt_chars": packet_prompt_chars,
        "prompt_tokens_estimate": prompt_tokens_estimate,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens
        if isinstance(cached_input_tokens, int) and not isinstance(cached_input_tokens, bool)
        else None,
        "threshold": threshold,
        "input_to_prompt_estimate_ratio": round(input_tokens / prompt_tokens_estimate, 2),
        "message": "Known input tokens greatly exceed the packet prompt estimate; inspect launcher flags and inherited context before broad log reads.",
    }


def summarize_standard(bundle_dir: Path) -> dict[str, Any]:
    files = discover_telemetry_files(bundle_dir)
    stale_artifact_paths = load_stale_artifact_paths(bundle_dir)
    totals = {
        "packet_count": 0,
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "prompt_chars": 0,
        "prompt_bytes": 0,
        "output_chars": 0,
        "output_bytes": 0,
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "model_prompt_chars_estimate": 0,
        "model_prompt_bytes_estimate": 0,
        "attempts_with_known_tokens": 0,
        "known_usage": zero_usage(),
    }
    by_role: dict[str, dict[str, Any]] = {}
    by_attempt: dict[str, dict[str, Any]] = {}
    premium_usage = {
        "audit_gpt_5_5": zero_premium_bucket(),
        "amender_gpt_5_5": zero_premium_bucket(),
        "reviewer_gpt_5_5": zero_premium_bucket(),
    }
    declared_aliases: dict[str, int] = {}
    called_aliases: dict[str, int] = {}
    accepted_aliases: dict[str, int] = {}
    mini_spark_usage = {alias: zero_alias_bucket() for alias in MINI_SPARK_ALIASES}
    premium_aliases_declared: dict[str, int] = {}
    premium_aliases_called: dict[str, int] = {}
    premium_aliases_accepted: dict[str, int] = {}
    premium_aliases_avoided: dict[str, int] = {}
    tier_usage = {
        tier: {"attempts_declared": 0, "attempts_called": 0, "accepted_attempts": 0, "bridge_attempts": 0}
        for tier in ("premium", "light", "other")
    }
    fallback_count = 0
    failed_same_class_attempts = 0
    packets = []
    defects = []
    token_pressure_warnings = []
    generic_cli = {
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "provenance_levels": {"high": 0, "low": 0},
        "exit_code_counts": {},
        "stdout_bytes_total": 0,
        "stderr_bytes_total": 0,
        "attempts_with_stdout_bytes": 0,
        "attempts_with_stderr_bytes": 0,
        "attempts_with_exit_code": 0,
    }
    scope_totals: dict[str, dict[str, Any]] = {
        "all": zero_scope_totals(),
        **{scope: zero_scope_totals() for scope in SCOPE_NAMES},
    }

    for path in files:
        rel = path.relative_to(bundle_dir).as_posix()
        scope = classify_telemetry_scope(rel, stale_artifact_paths)
        try:
            data = load_json(path)
        except Exception as exc:  # noqa: BLE001
            defects.append(f"{rel}: unreadable telemetry JSON: {exc}")
            continue
        role = data.get("role") if isinstance(data.get("role"), str) else "unknown"
        packet_id = data.get("packet_id") if isinstance(data.get("packet_id"), str) else path.parent.name
        attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
        launch_events = read_attempt_launch_events(path.parent / "launcher-state.json")
        called_attempts = [item for item in attempts if isinstance(item, dict) and item.get("called") is True]
        accepted_attempts = [item for item in attempts if isinstance(item, dict) and item.get("accepted") is True]
        fallback_count += max(0, len(called_attempts) - 1)
        packet_prompt_chars = data.get("prompt_chars") if isinstance(data.get("prompt_chars"), int) else 0
        packet_prompt_bytes = data.get("prompt_bytes") if isinstance(data.get("prompt_bytes"), int) else 0
        route_class = data.get("route_class") if isinstance(data.get("route_class"), str) else None

        for bucket in (totals, ensure_bucket(by_role, role), scope_totals["all"], scope_totals[scope]):
            bucket["packet_count"] += 1
            bucket["attempts_declared"] += len(attempts)
            bucket["attempts_called"] += len(called_attempts)
            bucket["accepted_attempts"] += len(accepted_attempts)
            add_number(bucket, "prompt_chars", data.get("prompt_chars"))
            add_number(bucket, "prompt_bytes", data.get("prompt_bytes"))
            add_number(bucket, "output_chars", data.get("output_chars"))
            add_number(bucket, "output_bytes", data.get("output_bytes"))
            add_number(bucket, "event_log_chars", data.get("event_log_chars"))
            add_number(bucket, "event_log_bytes", data.get("event_log_bytes"))
            bucket["model_prompt_chars_estimate"] += packet_prompt_chars * len(called_attempts)
            bucket["model_prompt_bytes_estimate"] += packet_prompt_bytes * len(called_attempts)
            totals_usage = data.get("totals") if isinstance(data.get("totals"), dict) else {}
            add_usage(bucket["known_usage"], totals_usage.get("known_usage"))

        packet_attempts = []
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            alias = (
                attempt.get("alias") if isinstance(attempt.get("alias"), str) and attempt.get("alias") else "unknown"
            )
            declared_aliases[alias] = declared_aliases.get(alias, 0) + 1
            if alias in PREMIUM_ALIASES:
                premium_aliases_declared[alias] = premium_aliases_declared.get(alias, 0) + 1
            tier = "premium" if alias in PREMIUM_ALIASES else "light" if alias in LIGHT_ALIASES else "other"
            tier_bucket = tier_usage[tier]
            tier_bucket["attempts_declared"] += 1
            if is_bridge_alias(alias):
                tier_bucket["bridge_attempts"] += 1
            if attempt.get("called") is True:
                tier_bucket["attempts_called"] += 1
            if attempt.get("accepted") is True:
                tier_bucket["accepted_attempts"] += 1
            key = attempt_group_key(role, attempt, scope)
            bucket = ensure_bucket(by_attempt, key)
            bucket["packet_count"] += 1
            bucket["attempts_declared"] += 1
            if attempt.get("called") is True:
                called_aliases[alias] = called_aliases.get(alias, 0) + 1
                if alias in PREMIUM_ALIASES:
                    premium_aliases_called[alias] = premium_aliases_called.get(alias, 0) + 1
                if route_class and attempt.get("accepted") is not True:
                    failed_same_class_attempts += 1
                bucket["attempts_called"] += 1
                bucket["model_prompt_chars_estimate"] += packet_prompt_chars
                bucket["model_prompt_bytes_estimate"] += packet_prompt_bytes
                usage = attempt.get("usage")
                if isinstance(usage, dict) and any(
                    isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool) for key in USAGE_KEYS
                ):
                    totals["attempts_with_known_tokens"] += 1
                    scope_totals["all"]["attempts_with_known_tokens"] += 1
                    scope_totals[scope]["attempts_with_known_tokens"] += 1
            if attempt.get("accepted") is True:
                accepted_aliases[alias] = accepted_aliases.get(alias, 0) + 1
                if alias in PREMIUM_ALIASES:
                    premium_aliases_accepted[alias] = premium_aliases_accepted.get(alias, 0) + 1
                bucket["accepted_attempts"] += 1
            called = attempt.get("called") is True
            attempt_index = attempt_index_from_attempt(attempt)
            launch_event = launch_events.get(attempt_index)
            provenance = attempt_provenance_level(attempt, launch_event)
            if alias in MINI_SPARK_ALIASES:
                alias_bucket = mini_spark_usage[alias]
                alias_bucket["attempts_declared"] += 1
                if called:
                    alias_bucket["attempts_called"] += 1
                if attempt.get("accepted") is True:
                    alias_bucket["accepted_attempts"] += 1
                add_usage(alias_bucket["known_usage"], attempt.get("usage"))
            if alias in PREMIUM_ALIASES and called is not True:
                premium_aliases_avoided[alias] = premium_aliases_avoided.get(alias, 0) + 1
            if attempt.get("provider") == "generic-cli":
                generic_cli["attempts_declared"] += 1
                if called:
                    generic_cli["attempts_called"] += 1
                if attempt.get("accepted") is True:
                    generic_cli["accepted_attempts"] += 1
                if called:
                    stdout_bytes = attempt.get("stdout_bytes")
                    if isinstance(stdout_bytes, int) and not isinstance(stdout_bytes, bool):
                        generic_cli["stdout_bytes_total"] += stdout_bytes
                        generic_cli["attempts_with_stdout_bytes"] += 1
                    stderr_bytes = attempt.get("stderr_bytes")
                    if isinstance(stderr_bytes, int) and not isinstance(stderr_bytes, bool):
                        generic_cli["stderr_bytes_total"] += stderr_bytes
                        generic_cli["attempts_with_stderr_bytes"] += 1
                    exit_code = attempt_exit_code(launch_event, attempt)
                    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                        key = str(exit_code)
                        generic_cli["exit_code_counts"][key] = generic_cli["exit_code_counts"].get(key, 0) + 1
                        generic_cli["attempts_with_exit_code"] += 1
                generic_cli["provenance_levels"][provenance] = generic_cli["provenance_levels"].get(provenance, 0) + 1
            for log_group in ("event_logs", "probe_logs"):
                logs = attempt.get(log_group)
                if isinstance(logs, list):
                    for log in logs:
                        if isinstance(log, dict):
                            add_number(bucket, "event_log_chars", log.get("chars"))
                            add_number(bucket, "event_log_bytes", log.get("bytes"))
            add_usage(bucket["known_usage"], attempt.get("usage"))
            premium_key = None
            if role == "prompt-auditor" and (attempt.get("alias") == "gpt-5.5" or attempt.get("model") == "gpt-5.5"):
                premium_key = "audit_gpt_5_5"
            elif role == "plan_amender" and (attempt.get("alias") == "gpt-5.5" or attempt.get("model") == "gpt-5.5"):
                premium_key = "amender_gpt_5_5"
            elif role == "reviewer" and (attempt.get("alias") == "gpt-5.5" or attempt.get("model") == "gpt-5.5"):
                premium_key = "reviewer_gpt_5_5"
            if premium_key:
                premium_bucket = premium_usage[premium_key]
                premium_bucket["attempts_declared"] += 1
                if attempt.get("called") is True:
                    premium_bucket["attempts_called"] += 1
                if attempt.get("accepted") is True:
                    premium_bucket["accepted_attempts"] += 1
                add_usage(premium_bucket["known_usage"], attempt.get("usage"))
            pressure = token_pressure_warning(
                rel=rel,
                packet_id=packet_id,
                role=role,
                packet_prompt_chars=packet_prompt_chars,
                attempt=attempt,
            )
            if pressure is not None:
                token_pressure_warnings.append(pressure)
            packet_attempts.append(
                {
                    "alias": attempt.get("alias"),
                    "provider": attempt.get("provider"),
                    "model": attempt.get("model"),
                    "effort": attempt.get("effort"),
                    "attempt_id": attempt.get("attempt_id"),
                    "candidate_attempts_index": attempt.get("candidate_attempts_index"),
                    "not_executed_reason": attempt.get("not_executed_reason"),
                    "called": attempt.get("called") is True,
                    "accepted": attempt.get("accepted") is True,
                    "scope": scope,
                    "provenance_level": provenance,
                    "stdout_bytes": attempt.get("stdout_bytes"),
                    "stderr_bytes": attempt.get("stderr_bytes"),
                    "exit_code": attempt_exit_code(launch_event, attempt),
                    "known_usage": attempt.get("usage") if isinstance(attempt.get("usage"), dict) else None,
                }
            )

        packets.append(
            {
                "path": rel,
                "scope": scope,
                "packet_id": packet_id,
                "role": role,
                "route_class": route_class,
                "accepted_alias": data.get("accepted_alias"),
                "prompt_chars": data.get("prompt_chars"),
                "output_chars": data.get("output_chars"),
                "event_log_chars": data.get("event_log_chars"),
                "attempts": packet_attempts,
            }
        )

    known_token_coverage_ratio = (
        round(totals["attempts_with_known_tokens"] / totals["attempts_called"], 6)
        if totals["attempts_called"] > 0
        else None
    )
    token_totals_status = (
        "none"
        if totals["attempts_called"] == 0
        else "complete"
        if totals["attempts_with_known_tokens"] == totals["attempts_called"]
        else "partial"
    )

    cost_summary = {
        "declared_attempts": totals["attempts_declared"],
        "called_attempts": totals["attempts_called"],
        "candidate_attempts": totals["attempts_declared"],
        "executed_attempts": totals["attempts_called"],
        "known_token_coverage": {
            "attempts_with_known_tokens": totals["attempts_with_known_tokens"],
            "executed_attempts": totals["attempts_called"],
            "ratio": known_token_coverage_ratio,
        },
        "token_totals_status": token_totals_status,
        "token_totals_note": "token totals are partial unless every executed attempt exposes token usage",
        "canonical_packet_totals": {
            "packet_count": totals["packet_count"],
            "accepted_packets": sum(accepted_aliases.values()),
        },
        "attempt_totals": {
            "candidate_attempts": totals["attempts_declared"],
            "executed_attempts": totals["attempts_called"],
            "accepted_attempts": totals["accepted_attempts"],
        },
        "retry_history_totals": {
            "fallback_count": fallback_count,
            "failed_same_class_attempts": failed_same_class_attempts,
        },
        "accepted_aliases": dict(sorted(accepted_aliases.items())),
        "declared_aliases": dict(sorted(declared_aliases.items())),
        "called_aliases": dict(sorted(called_aliases.items())),
        "premium_aliases_declared": dict(sorted(premium_aliases_declared.items())),
        "premium_aliases_called": dict(sorted(premium_aliases_called.items())),
        "premium_aliases_accepted": dict(sorted(premium_aliases_accepted.items())),
        "premium_aliases_avoided": dict(sorted(premium_aliases_avoided.items())),
        "mini_spark_usage": {alias: compact_bucket(mini_spark_usage[alias]) for alias in MINI_SPARK_ALIASES},
        "tier_usage": {tier: dict(tier_usage[tier]) for tier in ("premium", "light", "other")},
        "prompt_bytes": totals["prompt_bytes"],
        "output_bytes": totals["output_bytes"],
        "fallback_count": fallback_count,
        "failed_same_class_attempts": failed_same_class_attempts,
    }

    return {
        "schema_version": 1,
        "bundle_dir": bundle_dir.as_posix(),
        "telemetry_files": [path.relative_to(bundle_dir).as_posix() for path in files],
        "telemetry_count": len(files),
        "telemetry_files_scoped": [
            {
                "path": path.relative_to(bundle_dir).as_posix(),
                "scope": classify_telemetry_scope(path.relative_to(bundle_dir).as_posix(), stale_artifact_paths),
            }
            for path in files
        ],
        "defects": defects,
        "totals": compact_bucket(totals),
        "scopes": {key: compact_bucket(scope_totals[key]) for key in sorted(scope_totals)},
        "by_role": {key: compact_bucket(by_role[key]) for key in sorted(by_role)},
        "by_provider_model_alias": [attempt_group_row(key, by_attempt[key]) for key in sorted(by_attempt)],
        "premium_usage": {
            key: {
                **{field: value for field, value in bucket.items() if field != "known_usage"},
                "known_usage": compact_usage(bucket.get("known_usage", {})),
            }
            for key, bucket in premium_usage.items()
        },
        "cost_summary": cost_summary,
        "runtime_cache_artifacts": summarize_runtime_cache_artifacts(bundle_dir),
        "token_pressure": {
            "approx_chars_per_token": APPROX_CHARS_PER_TOKEN,
            "input_warn_min": TOKEN_PRESSURE_INPUT_WARN_MIN,
            "input_warn_ratio": TOKEN_PRESSURE_INPUT_WARN_RATIO,
            "warnings": sorted(
                token_pressure_warnings,
                key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]), str(item["alias"])),
            ),
        },
        "generic_cli": generic_cli,
        "packets": sorted(packets, key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]))),
    }


def summarize_debug(bundle_dir: Path) -> dict[str, Any]:
    files = discover_telemetry_files(bundle_dir, debug=True)
    trace_events = build_run_trace(bundle_dir)
    preflight = preflight_pipeline_summary(bundle_dir)
    stale_artifact_paths = load_stale_artifact_paths(bundle_dir)
    text_totals = {
        "packet_count": 0,
        "prompt_chars": 0,
        "prompt_bytes": 0,
        "output_chars": 0,
        "output_bytes": 0,
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "debug_overhead_chars": 0,
    }
    model_totals = zero_usage()
    model_by_alias: dict[str, dict[str, Any]] = {}
    model_by_role: dict[str, dict[str, Any]] = {}
    success = {
        "packet_count": 0,
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "attempts_with_known_tokens": 0,
        "accepted_aliases": {},
        "fallback_count": 0,
    }
    time_totals = {
        "attempts_declared": 0,
        "attempts_called": 0,
        "attempts_with_timing": 0,
        "attempts_missing_timing": 0,
        "timed_out_attempts": 0,
        "timed_out_known": 0,
        "elapsed_seconds_sum": 0.0,
        "elapsed_seconds_count": 0,
        "debug_event_files": 0,
        "debug_events": 0,
        "preflight_phase_count": preflight["phase_count"],
        "preflight_elapsed_ms": preflight["elapsed_ms"],
        "preflight_failed_phase_count": preflight["failed_phase_count"],
    }
    determinism = {
        "packets_with_artifacts": 0,
        "artifact_counts_by_kind": {},
        "drift_count": 0,
    }
    drift_packet_ids: list[str] = []
    defects = []
    packet_attempts: list[dict[str, Any]] = []
    outcome_counts: dict[str, int] = {key: 0 for key in OUTCOME_BUCKETS}
    provenance_counts: dict[str, int] = {}
    generic_cli = {
        "attempts_declared": 0,
        "attempts_called": 0,
        "accepted_attempts": 0,
        "provenance_levels": {"high": 0, "low": 0},
        "exit_code_counts": {},
        "stdout_bytes_total": 0,
        "stderr_bytes_total": 0,
        "attempts_with_stdout_bytes": 0,
        "attempts_with_stderr_bytes": 0,
        "attempts_with_exit_code": 0,
    }

    for path in files:
        rel = path.relative_to(bundle_dir).as_posix()
        scope = classify_telemetry_scope(rel, stale_artifact_paths)
        try:
            data = load_json(path)
        except Exception as exc:  # noqa: BLE001
            defects.append(f"{rel}: unreadable telemetry JSON: {exc}")
            continue
        debug_text = path.read_text(encoding="utf-8", errors="replace")
        text_totals["debug_overhead_chars"] += len(debug_text)
        debug_events_path = path.parent / DEBUG_EVENTS_FILENAME
        if debug_events_path.exists():
            events_text = debug_events_path.read_text(encoding="utf-8", errors="replace")
            text_totals["debug_overhead_chars"] += len(events_text)
            time_totals["debug_event_files"] += 1
            for line in events_text.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                time_totals["debug_events"] += 1
        debug_event_elapsed, debug_event_exit_status, debug_event_status = read_debug_event_timing(path.parent)
        role = data.get("role") if isinstance(data.get("role"), str) else "unknown"
        packet_id = data.get("packet_id") if isinstance(data.get("packet_id"), str) else path.parent.name

        text_metrics = data.get("text_metrics") if isinstance(data.get("text_metrics"), dict) else {}
        model_usage = data.get("model_usage") if isinstance(data.get("model_usage"), dict) else {}
        success_metrics = data.get("success_metrics") if isinstance(data.get("success_metrics"), dict) else {}
        determinism_payload = data.get("determinism") if isinstance(data.get("determinism"), dict) else {}
        attempts = model_usage.get("attempts") if isinstance(model_usage.get("attempts"), list) else []
        launch_events = read_attempt_launch_events(path.parent / "launcher-state.json")
        packet_elapsed_seconds, packet_exit_status, packet_status = read_packet_debug_end_event(path.parent)
        if packet_exit_status is None:
            packet_exit_status = debug_event_exit_status.get(-1)
        if packet_status is None:
            packet_status = debug_event_status.get(-1)
        called_attempt_count = sum(1 for item in attempts if isinstance(item, dict) and item.get("called") is True)

        prompt_chars = text_metrics.get("prompt_chars")
        prompt_bytes = text_metrics.get("prompt_bytes")
        output_chars = text_metrics.get("output_chars")
        output_bytes = text_metrics.get("output_bytes")
        event_log_chars = text_metrics.get("event_log_chars")
        event_log_bytes = text_metrics.get("event_log_bytes")

        if isinstance(prompt_chars, int) and not isinstance(prompt_chars, bool):
            text_totals["prompt_chars"] += prompt_chars
        if isinstance(prompt_bytes, int) and not isinstance(prompt_bytes, bool):
            text_totals["prompt_bytes"] += prompt_bytes
        if isinstance(output_chars, int) and not isinstance(output_chars, bool):
            text_totals["output_chars"] += output_chars
        if isinstance(output_bytes, int) and not isinstance(output_bytes, bool):
            text_totals["output_bytes"] += output_bytes
        if isinstance(event_log_chars, int) and not isinstance(event_log_chars, bool):
            text_totals["event_log_chars"] += event_log_chars
        if isinstance(event_log_bytes, int) and not isinstance(event_log_bytes, bool):
            text_totals["event_log_bytes"] += event_log_bytes
        text_totals["packet_count"] += 1
        add_usage(model_totals, model_usage.get("totals"))

        attempts_declared = (
            int(success_metrics.get("attempts_declared"))
            if isinstance(success_metrics.get("attempts_declared"), int)
            else len(attempts)
        )
        attempts_called = (
            int(success_metrics.get("attempts_called"))
            if isinstance(success_metrics.get("attempts_called"), int)
            else sum(1 for item in attempts if isinstance(item, dict) and item.get("called") is True)
        )
        accepted_attempts = (
            int(success_metrics.get("accepted_attempts"))
            if isinstance(success_metrics.get("accepted_attempts"), int)
            else sum(1 for item in attempts if isinstance(item, dict) and item.get("accepted") is True)
        )
        accepted_alias = (
            success_metrics.get("accepted_alias") if isinstance(success_metrics.get("accepted_alias"), str) else None
        )
        fallback_count = (
            int(success_metrics.get("fallback_count"))
            if isinstance(success_metrics.get("fallback_count"), int)
            else max(0, attempts_called - 1)
        )
        success["packet_count"] += 1
        success["attempts_declared"] += attempts_declared
        success["attempts_called"] += attempts_called
        success["accepted_attempts"] += accepted_attempts
        success["fallback_count"] += fallback_count
        if accepted_alias is not None:
            success["accepted_aliases"][accepted_alias] = success["accepted_aliases"].get(accepted_alias, 0) + 1
        time_totals["attempts_declared"] += attempts_declared
        time_totals["attempts_called"] += attempts_called

        for item in attempts:
            if not isinstance(item, dict):
                continue
            alias = item.get("alias")
            if not isinstance(alias, str):
                alias = "unknown"
            role_bucket = model_by_role.get(role)
            if role_bucket is None:
                role_bucket = {
                    "attempts_declared": 0,
                    "attempts_called": 0,
                    "accepted_attempts": 0,
                    "known_usage": zero_usage(),
                }
                model_by_role[role] = role_bucket
            role_bucket["attempts_declared"] += 1
            alias_bucket = model_by_alias.get(alias)
            if alias_bucket is None:
                alias_bucket = {
                    "attempts_declared": 0,
                    "attempts_called": 0,
                    "accepted_attempts": 0,
                    "known_usage": zero_usage(),
                }
                model_by_alias[alias] = alias_bucket
            alias_bucket["attempts_declared"] += 1
            called = item.get("called") is True
            if called:
                role_bucket["attempts_called"] += 1
                alias_bucket["attempts_called"] += 1
            if item.get("accepted") is True:
                role_bucket["accepted_attempts"] += 1
                alias_bucket["accepted_attempts"] += 1
            add_usage(role_bucket["known_usage"], item.get("usage"))
            add_usage(alias_bucket["known_usage"], item.get("usage"))
            if (
                called
                and isinstance(item.get("usage"), dict)
                and any(
                    isinstance(item["usage"].get(key), int) and not isinstance(item["usage"].get(key), bool)
                    for key in USAGE_KEYS
                )
            ):
                success["attempts_with_known_tokens"] += 1
            attempt_index = attempt_index_from_attempt(item)
            launch_event = launch_events.get(attempt_index)
            outcome = classify_attempt_outcome(item, launch_event)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            provenance = attempt_provenance_level(item, launch_event)
            provenance_counts[provenance] = provenance_counts.get(provenance, 0) + 1
            if called:
                elapsed, _elapsed_source = attempt_elapsed_seconds(
                    item,
                    packet_elapsed_seconds,
                    called_attempt_count,
                    called=called,
                    attempt_index=attempt_index,
                    launch_event=launch_event,
                    debug_event_elapsed=debug_event_elapsed,
                )
                if elapsed is None:
                    time_totals["attempts_missing_timing"] += 1
                else:
                    time_totals["elapsed_seconds_sum"] += elapsed
                    time_totals["elapsed_seconds_count"] += 1
                    time_totals["attempts_with_timing"] += 1
                timed_out = attempt_timed_out(item, launch_event, packet_status)
                if outcome == OUTCOME_KEY_PASS and timed_out is True:
                    timed_out = False
                if isinstance(timed_out, bool):
                    time_totals["timed_out_known"] += 1
                    if timed_out:
                        time_totals["timed_out_attempts"] += 1
            if item.get("provider") == "generic-cli":
                generic_cli["attempts_declared"] += 1
                if called:
                    generic_cli["attempts_called"] += 1
                if item.get("accepted") is True:
                    generic_cli["accepted_attempts"] += 1
                generic_cli["provenance_levels"][provenance] = generic_cli["provenance_levels"].get(provenance, 0) + 1
                exit_code = attempt_exit_code(launch_event, item)
                if exit_code is None:
                    exit_code = packet_exit_status if attempt_index == -1 else None
                if isinstance(exit_code, int) and not isinstance(exit_code, bool):
                    exit_code_key = str(exit_code)
                    generic_cli["exit_code_counts"][exit_code_key] = (
                        generic_cli["exit_code_counts"].get(exit_code_key, 0) + 1
                    )
                    generic_cli["attempts_with_exit_code"] += 1
                stdout_bytes = item.get("stdout_bytes")
                if isinstance(stdout_bytes, int) and not isinstance(stdout_bytes, bool) and stdout_bytes >= 0:
                    generic_cli["stdout_bytes_total"] += stdout_bytes
                    generic_cli["attempts_with_stdout_bytes"] += 1
                stderr_bytes = item.get("stderr_bytes")
                if isinstance(stderr_bytes, int) and not isinstance(stderr_bytes, bool) and stderr_bytes >= 0:
                    generic_cli["stderr_bytes_total"] += stderr_bytes
                    generic_cli["attempts_with_stderr_bytes"] += 1

        packet_artifact_hashes: dict[str, set[str]] = {}
        artifacts = (
            determinism_payload.get("artifacts") if isinstance(determinism_payload.get("artifacts"), list) else []
        )
        has_artifact = False
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            has_artifact = True
            kind = artifact.get("kind")
            if not isinstance(kind, str) or kind == "":
                continue
            determinism["artifact_counts_by_kind"][kind] = determinism["artifact_counts_by_kind"].get(kind, 0) + 1
            sha256_value = artifact.get("sha256")
            if isinstance(sha256_value, str):
                packet_artifact_hashes.setdefault(kind, set()).add(sha256_value)
        if has_artifact:
            determinism["packets_with_artifacts"] += 1
        drift_for_packet = any(len(values) > 1 for values in packet_artifact_hashes.values())
        if drift_for_packet:
            determinism["drift_count"] += 1
            drift_packet_ids.append(packet_id)

        packet_attempts.append(
            {
                "path": rel,
                "packet_id": packet_id,
                "scope": scope,
                "role": role,
                "accepted_alias": success_metrics.get("accepted_alias"),
                "attempts_declared": attempts_declared,
                "attempts_called": attempts_called,
                "fallback_count": fallback_count,
            }
        )

    known_input_tokens = model_totals.get("input_tokens", 0)
    estimated_input_tokens = max(1, round(text_totals["prompt_chars"] / APPROX_CHARS_PER_TOKEN))
    known_token_coverage_ratio = (
        round(success["attempts_with_known_tokens"] / success["attempts_called"], 6)
        if success["attempts_called"] > 0
        else None
    )
    text_pressure_ratio = round(known_input_tokens / estimated_input_tokens, 6) if estimated_input_tokens else None
    timeout_rate = (
        round(time_totals["timed_out_attempts"] / time_totals["timed_out_known"], 6)
        if time_totals["timed_out_known"] > 0
        else None
    )
    fallback_rate = (
        round(success["fallback_count"] / success["attempts_called"], 6) if success["attempts_called"] > 0 else None
    )
    average_elapsed_seconds = (
        round(time_totals["elapsed_seconds_sum"] / time_totals["elapsed_seconds_count"], 6)
        if time_totals["elapsed_seconds_count"] > 0
        else None
    )

    return {
        "schema_version": 1,
        "bundle_dir": bundle_dir.as_posix(),
        "telemetry_files": [path.relative_to(bundle_dir).as_posix() for path in files],
        "telemetry_count": len(files),
        "telemetry_files_scoped": [
            {
                "path": path.relative_to(bundle_dir).as_posix(),
                "scope": classify_telemetry_scope(path.relative_to(bundle_dir).as_posix(), stale_artifact_paths),
            }
            for path in files
        ],
        "defects": defects,
        "model_usage": {
            "totals": compact_usage(model_totals),
            "by_alias": {alias: compact_model_bucket(bucket) for alias, bucket in model_by_alias.items()},
            "by_role": {role: compact_model_bucket(bucket) for role, bucket in model_by_role.items()},
            "attempts_declared": success["attempts_declared"],
            "attempts_called": success["attempts_called"],
            "candidate_attempts": success["attempts_declared"],
            "executed_attempts": success["attempts_called"],
            "accepted_attempts": success["accepted_attempts"],
            "known_token_coverage_ratio": known_token_coverage_ratio,
            "known_token_coverage": {
                "attempts_with_known_tokens": success["attempts_with_known_tokens"],
                "called_attempts": success["attempts_called"],
                "ratio": known_token_coverage_ratio,
            },
            "text_pressure": {
                "input_tokens": known_input_tokens,
                "estimated_prompt_tokens": estimated_input_tokens,
                "ratio": text_pressure_ratio,
            },
        },
        "text_metrics": {
            **text_totals,
            "debug_overhead_chars": text_totals["debug_overhead_chars"],
        },
        "time_metrics": {
            "attempts_declared": time_totals["attempts_declared"],
            "attempts_called": time_totals["attempts_called"],
            "attempts_with_timing": time_totals["attempts_with_timing"],
            "attempts_missing_timing": time_totals["attempts_missing_timing"],
            "timeout_rate": timeout_rate,
            "timed_out_attempts": time_totals["timed_out_attempts"],
            "timed_out_known": time_totals["timed_out_known"],
            "average_elapsed_seconds": average_elapsed_seconds,
            "debug_event_files": time_totals["debug_event_files"],
            "debug_events": time_totals["debug_events"],
            "preflight_phase_count": time_totals["preflight_phase_count"],
            "preflight_elapsed_ms": time_totals["preflight_elapsed_ms"],
            "preflight_failed_phase_count": time_totals["preflight_failed_phase_count"],
        },
        "outcome_metrics": {
            "attempt_outcomes": dict(sorted(outcome_counts.items())),
            "provenance_levels": dict(sorted(provenance_counts.items())),
            "called_attempts_excluded_from_timeout_when_false": True,
        },
        "generic_cli": generic_cli,
        "runtime_cache_artifacts": summarize_runtime_cache_artifacts(bundle_dir),
        "preflight_pipeline": preflight,
        "determinism": {
            "packet_count": text_totals["packet_count"],
            "packets_with_artifacts": determinism["packets_with_artifacts"],
            "artifact_counts_by_kind": determinism["artifact_counts_by_kind"],
            "drift_count": determinism["drift_count"],
            "drift_rate": (
                round(determinism["drift_count"] / max(1, determinism["packets_with_artifacts"]), 6)
                if determinism["packets_with_artifacts"]
                else None
            ),
            "drift_packet_ids": drift_packet_ids,
        },
        "success_metrics": {
            "packet_count": success["packet_count"],
            "attempts_declared": success["attempts_declared"],
            "attempts_called": success["attempts_called"],
            "candidate_attempts": success["attempts_declared"],
            "executed_attempts": success["attempts_called"],
            "accepted_attempts": success["accepted_attempts"],
            "attempts_with_known_tokens": success["attempts_with_known_tokens"],
            "accepted_aliases": dict(sorted(success["accepted_aliases"].items())),
            "fallback_count": success["fallback_count"],
            "fallback_rate": fallback_rate,
            "text_pressure_ratio": text_pressure_ratio,
        },
        "trace": trace_summary(trace_events),
        "packets": sorted(
            packet_attempts, key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]))
        ),
    }


def summarize(bundle_dir: Path, *, debug: bool = False) -> dict[str, Any]:
    return summarize_debug(bundle_dir) if debug else summarize_standard(bundle_dir)


def manifest_debug_enabled(bundle_dir: Path) -> bool:
    manifest_path = bundle_dir / "job.manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return False
    policy = manifest.get("telemetry_policy")
    return isinstance(policy, dict) and policy.get("mode") == "debug"


def write_run_trace(bundle_dir: Path) -> Path:
    events = build_run_trace(bundle_dir)
    output_path = bundle_dir / RUN_TRACE_FILENAME
    output_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize bundle packet telemetry into telemetry.summary.json. "
            "The output includes telemetry_files, telemetry_count, totals, premium_usage, and token_pressure warnings."
        )
    )
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output", help="Defaults to <bundle-dir>/telemetry.summary.json")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Read telemetry.debug.json artifacts and write telemetry.debug.summary.json.",
    )
    args = parser.parse_args()

    bundle_dir = Path(args.bundle_dir).resolve()
    if not bundle_dir.is_dir():
        raise SystemExit(f"--bundle-dir must be an existing directory: {bundle_dir}")
    output_path = (
        Path(args.output).resolve()
        if args.output
        else bundle_dir / ("telemetry.debug.summary.json" if args.debug else "telemetry.summary.json")
    )
    summary = summarize(bundle_dir, debug=args.debug)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.debug:
        write_run_trace(bundle_dir)
    if not args.debug and manifest_debug_enabled(bundle_dir):
        debug_summary = summarize(bundle_dir, debug=True)
        (bundle_dir / "telemetry.debug.summary.json").write_text(
            json.dumps(debug_summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_run_trace(bundle_dir)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
