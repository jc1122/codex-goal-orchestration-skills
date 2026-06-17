#!/usr/bin/env python3
"""Extract deterministic packet telemetry from launcher artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, NamedTuple
from datetime import datetime


TOKEN_KEYS = {
    "input_tokens": {
        "input_tokens",
        "prompt_tokens",
        "inputTokens",
        "promptTokens",
        "inputTokenCount",
        "promptTokenCount",
    },
    "output_tokens": {
        "output_tokens",
        "completion_tokens",
        "outputTokens",
        "completionTokens",
        "outputTokenCount",
        "completionTokenCount",
    },
    "reasoning_tokens": {"reasoning_tokens", "reasoningTokens", "reasoningTokenCount"},
    "cached_input_tokens": {"cached_input_tokens", "cachedInputTokens", "cachedInputTokenCount"},
    "total_tokens": {"total_tokens", "totalTokens", "totalTokenCount"},
}
RETRY_ORDINAL_RE = re.compile(r"attempt-(\d+)")


def _normalize_path_value(path: Path) -> str:
    return path.as_posix().replace("/./", "/").replace("//", "/")


def _infer_retry_ordinal(packet_dir: Path, provided: object) -> str:
    value = str(provided).strip() if isinstance(provided, str) and provided.strip() else ""
    if value:
        return value
    match = RETRY_ORDINAL_RE.search(_normalize_path_value(packet_dir))
    return match.group(0) if match else ""


def _normalize_execution(spec: dict[str, Any] | Any) -> dict[str, Any] | None:
    if not isinstance(spec, dict):
        return None
    return {
        key: value
        for key in (
            "returncode",
            "elapsed_ms",
            "timed_out",
            "stdout_bytes",
            "stderr_bytes",
            "command",
            "command_parts",
            "stderr_path",
            "started_at",
            "completed_at",
        )
        if (value := spec.get(key)) is not None
    }


def _coerce_str_none(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": path.name,
            "exists": False,
            "bytes": 0,
            "chars": 0,
            "usage": None,
        }
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return {
        "path": path.name,
        "exists": True,
        "bytes": len(raw),
        "chars": len(text),
        "usage": extract_usage(text),
    }


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_usage(data: dict[str, Any]) -> dict[str, int] | None:
    usage: dict[str, int] = {}
    for target, aliases in TOKEN_KEYS.items():
        for key in aliases:
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[target] = value
                break
    return usage or None


def iter_usage_dicts(value: Any):
    if isinstance(value, dict):
        direct = normalize_usage(value)
        if direct:
            yield direct
        nested_usage = value.get("usage")
        if isinstance(nested_usage, dict):
            nested = normalize_usage(nested_usage)
            if nested:
                yield nested
        for item in value.values():
            yield from iter_usage_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_usage_dicts(item)


def extract_usage(text: str) -> dict[str, int] | None:
    candidates: list[dict[str, int]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except Exception:
            continue
        candidates.extend(iter_usage_dicts(data))
    if not candidates:
        try:
            data = json.loads(text)
        except Exception:
            return None
        candidates.extend(iter_usage_dicts(data))
    return candidates[-1] if candidates else None


def sum_usage(logs: list[dict[str, Any]]) -> dict[str, int] | None:
    totals: dict[str, int] = {}
    for item in logs:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return totals or None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def accepted_alias(role: str, output: dict[str, Any] | None, attempts: list[dict[str, Any]]) -> str | None:
    called = [item for item in attempts if item.get("called")]
    if not called or output is None:
        return None
    if role == "worker":
        blockers = output.get("blockers", [])
        blocker_text = " ".join(item for item in blockers if isinstance(item, str))
        terminal_markers = [
            "All selected worker route attempts failed",
            "failed after leaving dirty worktree",
            "refusing fallback",
            "no fallback remains",
        ]
        if any(marker in blocker_text for marker in terminal_markers):
            return None
    if role == "prompt-auditor" and output.get("status") == "blocked" and not output.get("checked_files"):
        return None
    if (
        role == "reviewer"
        and output.get("role") == "reviewer"
        and output.get("findings") == ["Reviewer primary and fallback failed without producing review.json."]
    ):
        return None
    if (
        role == "research-worker"
        and output.get("role") == "research-worker"
        and output.get("findings") == ["Research worker primary and fallback failed without producing research.json."]
    ):
        return None
    if role == "plan_amender":
        operations = output.get("operations")
        if not isinstance(operations, list) or not operations:
            return None
    if role == "lite_advisor" and output.get("status") == "blocked":
        blockers = output.get("blockers", [])
        blocker_text = " ".join(item for item in blockers if isinstance(item, str))
        if "command failed" in blocker_text or "did not produce valid advice JSON" in blocker_text:
            return None
    return str(called[-1]["alias"])


def load_attempts(values: list[str]) -> list[dict[str, Any]]:
    attempts = []
    for index, value in enumerate(values):
        try:
            item = json.loads(value)
        except Exception as exc:
            raise SystemExit(f"--attempt-json[{index}] is not valid JSON: {exc}") from exc
        if not isinstance(item, dict):
            raise SystemExit(f"--attempt-json[{index}] must be a JSON object")
        attempts.append(item)
    return attempts


def _artifact_hash_record(path: Path, kind: str) -> dict[str, Any]:
    stats = file_stats(path)
    return {
        "kind": kind,
        "path": path.name,
        "exists": stats["exists"],
        "bytes": stats["bytes"],
        "chars": stats["chars"],
        "sha256": file_hash(path),
    }


def _timing_from_json_event(data: dict[str, Any]) -> dict[str, Any] | None:
    elapsed_ms = data.get("elapsed_ms")
    exit_status = data.get("exit_status", data.get("returncode"))
    timed_out = data.get("timed_out")
    status_text = str(data.get("status", "")).lower()
    event_text = str(data.get("event", "")).lower()
    message_text = str(data.get("message", "")).lower()
    combined = " ".join(part for part in [status_text, event_text, message_text] if part)
    timeout_detected = timed_out is True or "timed out" in combined or "timeout" in combined
    completed = (
        exit_status == 0
        or status_text in {"ok", "pass", "success", "completed"}
        or event_text in {"end", "complete", "completed"}
    )
    if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms >= 0:
        return {
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at") or data.get("timestamp"),
            "elapsed_seconds": round(elapsed_ms / 1000, 6),
            "timed_out": False if completed else (True if timeout_detected else None),
            "timing_source": "json_event_elapsed_ms",
        }
    if completed:
        return {
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at") or data.get("timestamp"),
            "elapsed_seconds": None,
            "timed_out": False,
            "timing_source": "json_event_completion",
        }
    if timeout_detected:
        return {
            "started_at": data.get("started_at"),
            "completed_at": data.get("completed_at") or data.get("timestamp"),
            "elapsed_seconds": None,
            "timed_out": True,
            "timing_source": "json_event_timeout",
        }
    return None


def _timing_from_jsonl(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    best: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        timing = _timing_from_json_event(data)
        if timing is None:
            continue
        if timing.get("timed_out") is False:
            return timing
        best = timing
    return best


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _elapsed_seconds_from_timestamps(start: object, end: object) -> float | None:
    start_time = _parse_iso_timestamp(start)
    end_time = _parse_iso_timestamp(end)
    if start_time is None or end_time is None:
        return None
    delta = end_time - start_time
    if delta.total_seconds() < 0:
        return None
    return round(delta.total_seconds(), 6)


def _packet_debug_timing(packet_dir: Path) -> dict[str, Any] | None:
    path = packet_dir / "debug.events.jsonl"
    if not path.exists():
        return None
    start_at = None
    completed_at = None
    elapsed_seconds = None
    timed_out = None
    exit_status = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        event_name = str(event.get("event", "")).lower()
        if event_name == "start" and start_at is None:
            start_at = event.get("timestamp")
        if event_name == "end":
            completed_at = event.get("timestamp")
            exit_status = event.get("exit_status")
            elapsed_ms = event.get("elapsed_ms")
            if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms >= 0:
                elapsed_seconds = round(elapsed_ms / 1000, 6)
            timing = _timing_from_json_event(event)
            if timing is not None and timing.get("timed_out") is not None:
                timed_out = timing.get("timed_out")

    if start_at is None and completed_at is None:
        return None
    if elapsed_seconds is None:
        elapsed_seconds = _elapsed_seconds_from_timestamps(start_at, completed_at)
    if timed_out is None:
        timed_out = False if exit_status == 0 else None
    return {
        "started_at": start_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed_seconds,
        "timed_out": timed_out,
        "timing_source": "packet_debug_events",
    }


def _stable_attempt_id(
    packet_id: str,
    role: str,
    alias: str,
    provider: str,
    model: str,
    command: str,
    index: int,
    retry_ordinal: str = "",
) -> str:
    key = "|".join(
        [
            packet_id or "-",
            retry_ordinal or "current",
            role or "-",
            str(index),
            alias or "-",
            provider or "-",
            model or "-",
            command or "-",
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    retry_segment = f":{retry_ordinal}" if retry_ordinal else ""
    return f"{packet_id}{retry_segment}:{role}:{index + 1:03d}:{digest}"


def _normalize_not_executed_reason(
    *,
    index: int,
    called_before: bool,
    called: bool,
    output_json: dict[str, Any] | None,
    spec: dict[str, Any],
) -> str | None:
    if called:
        return None
    if str(spec.get("not_executed_reason", "")).strip():
        return str(spec.get("not_executed_reason"))
    output_status = str(output_json.get("status", "")).lower() if output_json else ""
    if output_status in {"blocked", "fail-clean", "fail-dirty", "failed"}:
        return "dirty_stop"
    if called_before or index > 0:
        return "fallback_not_needed"
    return "catalog_pruned"


def _attempt_timeout_detected(log_paths: list[Path]) -> bool | None:
    if not log_paths:
        return None
    for path in log_paths:
        if not path.exists():
            continue
        timing = _timing_from_jsonl(path)
        if timing is not None and isinstance(timing.get("timed_out"), bool):
            return bool(timing["timed_out"])
        try:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
        except Exception:
            continue
        if "timed out" in text or "timeout" in text:
            return True
    return None


def build_telemetry(
    *,
    packet_dir: Path,
    packet_id: str,
    role: str,
    output_name: str,
    prompt_name: str,
    attempt_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    output_path = packet_dir / output_name
    prompt_path = packet_dir / prompt_name
    prompt = file_stats(prompt_path)
    output_stats = file_stats(output_path)
    output_json = read_json(output_path)
    route_class = (
        output_json.get("route_class")
        if isinstance(output_json, dict) and isinstance(output_json.get("route_class"), str)
        else None
    )
    attempts = []
    for index, spec in enumerate(attempt_specs):
        event_logs = [file_stats(packet_dir / str(path)) for path in spec.get("event_logs", [])]
        probe_logs = [file_stats(packet_dir / str(path)) for path in spec.get("probe_logs", [])]
        retry_ordinal = _infer_retry_ordinal(packet_dir, spec.get("retry_ordinal"))
        called = any(item["exists"] for item in event_logs + probe_logs)
        not_executed_reason = _normalize_not_executed_reason(
            index=index,
            called_before=any(item.get("called") for item in attempts),
            called=called,
            output_json=output_json,
            spec=spec,
        )
        execution = _normalize_execution(spec.get("execution"))
        attempt_id = _stable_attempt_id(
            packet_id=packet_id,
            role=role,
            alias=str(spec.get("alias", "")),
            provider=str(spec.get("provider", "")),
            model=str(spec.get("model", "")),
            command=str(spec.get("command", "")),
            index=index,
            retry_ordinal=retry_ordinal,
        )
        logs = event_logs + probe_logs
        timeout_seconds = spec.get("timeout_seconds")
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            timeout_seconds = None
        attempts.append(
            {
                "alias": spec.get("alias", ""),
                "provider": spec.get("provider", ""),
                "model": spec.get("model", ""),
                "effort": spec.get("effort") or None,
                "command": spec.get("command", ""),
                "timeout_seconds": timeout_seconds,
                "attempt_id": attempt_id,
                "retry_ordinal": _coerce_str_none(retry_ordinal),
                "candidate_attempts_index": index + 1,
                "not_executed_reason": not_executed_reason,
                "called": called,
                "event_logs": event_logs,
                "probe_logs": probe_logs,
                "execution": execution,
                "status_parse": spec.get("status_parse"),
                "route_health": spec.get("route_health") if isinstance(spec.get("route_health"), dict) else None,
                "stop_reason": spec.get("stop_reason"),
                "provenance_level": _coerce_str_none(spec.get("provenance_level")),
                "usage": sum_usage(logs),
            }
        )
    accepted = accepted_alias(role, output_json, attempts)
    for item in attempts:
        item["accepted"] = bool(accepted and item["alias"] == accepted)
    called_attempts = [item for item in attempts if item["called"]]
    total_log_bytes = sum(log["bytes"] for item in called_attempts for log in item["event_logs"] + item["probe_logs"])
    total_log_chars = sum(log["chars"] for item in called_attempts for log in item["event_logs"] + item["probe_logs"])
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": role,
        **({"route_class": route_class} if route_class else {}),
        "output_artifact": output_name,
        "prompt_artifact": prompt_name,
        "prompt_chars": prompt["chars"],
        "prompt_bytes": prompt["bytes"],
        "output_chars": output_stats["chars"],
        "output_bytes": output_stats["bytes"],
        "event_log_chars": total_log_chars,
        "event_log_bytes": total_log_bytes,
        "accepted_alias": accepted,
        "attempts": attempts,
        "totals": {
            "attempts_declared": len(attempts),
            "attempts_called": len(called_attempts),
            "candidate_attempts": len(attempts),
            "executed_attempts": len(called_attempts),
            "event_log_chars": total_log_chars,
            "event_log_bytes": total_log_bytes,
            "known_usage": sum_usage(called_attempts),
        },
    }


class _DebugAttemptInputs(NamedTuple):
    """Per-attempt path/stat inputs derived from a debug-telemetry attempt spec.

    Groups the values the inlined loop computed up front; carries identical data
    in the identical evaluation order.
    """

    event_log_paths: list[Path]
    probe_log_paths: list[Path]
    event_logs: list[dict[str, Any]]
    probe_logs: list[dict[str, Any]]
    called: bool
    retry_ordinal: str
    timeout_seconds: int | None
    timed_out: bool | None
    execution: dict[str, Any] | None


def _debug_attempt_inputs(packet_dir: Path, spec: dict[str, Any]) -> _DebugAttemptInputs:
    """Compute path/stat inputs for one debug attempt, identical to the inlined block."""
    event_log_paths = [packet_dir / str(path) for path in spec.get("event_logs", []) if isinstance(path, str)]
    probe_log_paths = [packet_dir / str(path) for path in spec.get("probe_logs", []) if isinstance(path, str)]
    event_logs = [file_stats(path) for path in event_log_paths]
    probe_logs = [file_stats(path) for path in probe_log_paths]
    called = any(item["exists"] for item in event_logs + probe_logs)
    retry_ordinal = _infer_retry_ordinal(packet_dir, spec.get("retry_ordinal"))
    timeout_seconds = spec.get("timeout_seconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        timeout_seconds = None
    timed_out = _attempt_timeout_detected(event_log_paths + probe_log_paths)
    timed_out = bool(timed_out) if isinstance(timed_out, bool) else None
    execution = _normalize_execution(spec.get("execution"))
    return _DebugAttemptInputs(
        event_log_paths=event_log_paths,
        probe_log_paths=probe_log_paths,
        event_logs=event_logs,
        probe_logs=probe_logs,
        called=called,
        retry_ordinal=retry_ordinal,
        timeout_seconds=timeout_seconds,
        timed_out=timed_out,
        execution=execution,
    )


def _resolve_attempt_timing_initial(
    inputs: _DebugAttemptInputs,
    packet_timing: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """First timing-resolution phase (jsonl scan + packet fallback + default).

    Identical to the inlined lines preceding the determinism-artifact appends.
    Returns the resolved ``attempt_timing`` dict and ``timing_unsupported_reason``.
    """
    attempt_timing = None
    timing_unsupported_reason = None
    for path in inputs.event_log_paths + inputs.probe_log_paths:
        attempt_timing = _timing_from_jsonl(path)
        if attempt_timing is not None:
            break
    if inputs.called and packet_timing is not None and attempt_timing is None:
        attempt_timing = packet_timing
    elif packet_timing is not None and isinstance(attempt_timing, dict):
        packet_timed_out = packet_timing.get("timed_out")
        event_timed_out = attempt_timing.get("timed_out")
        if packet_timed_out is not None and event_timed_out is None:
            attempt_timing["timed_out"] = packet_timed_out
    if attempt_timing is None:
        attempt_timing = {
            "started_at": None,
            "completed_at": None,
            "elapsed_seconds": None,
            "timed_out": inputs.timed_out,
            "timing_source": "timeout_text_scan" if inputs.timed_out is not None else "unknown",
        }
        timing_unsupported_reason = "attempt_timings_unavailable"
    return attempt_timing, timing_unsupported_reason


def _finalize_attempt_timing(
    attempt_timing: dict[str, Any],
    timing_unsupported_reason: str | None,
    inputs: _DebugAttemptInputs,
    packet_timing: dict[str, Any] | None,
) -> str | None:
    """Second timing-resolution phase (run after determinism appends + usage).

    Mutates ``attempt_timing`` in place exactly as the inlined block did and
    returns the updated ``timing_unsupported_reason``.
    """
    if attempt_timing.get("started_at") is not None and attempt_timing.get("completed_at") is not None:
        timing_unsupported_reason = None
    if attempt_timing.get("timed_out") is None and inputs.timed_out is not None:
        attempt_timing["timed_out"] = inputs.timed_out
    if (
        attempt_timing.get("timed_out") is None
        and packet_timing is not None
        and packet_timing.get("timed_out") is not None
    ):
        attempt_timing["timed_out"] = packet_timing.get("timed_out")
    if timing_unsupported_reason is None and inputs.called and attempt_timing.get("timed_out") is None:
        timing_unsupported_reason = "timing_unsupported_or_missing"
    return timing_unsupported_reason


def _build_debug_attempt(
    *,
    index: int,
    spec: dict[str, Any],
    packet_id: str,
    role: str,
    inputs: _DebugAttemptInputs,
    accepted: bool,
    usage: dict[str, int] | None,
    not_executed_reason: str | None,
    timing_unsupported_reason: str | None,
    attempt_timing: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one debug-attempt record, identical to the inlined dict literal."""
    attempt_id = _stable_attempt_id(
        packet_id=packet_id,
        role=role,
        alias=str(spec.get("alias", "")),
        provider=str(spec.get("provider", "")),
        model=str(spec.get("model", "")),
        command=str(spec.get("command", "")),
        index=index,
        retry_ordinal=inputs.retry_ordinal,
    )
    return {
        "alias": spec.get("alias", ""),
        "provider": spec.get("provider", ""),
        "model": spec.get("model", ""),
        "effort": spec.get("effort") or None,
        "command": spec.get("command", ""),
        "timeout_seconds": inputs.timeout_seconds,
        "attempt_id": attempt_id,
        "retry_ordinal": _coerce_str_none(inputs.retry_ordinal),
        "candidate_attempts_index": index + 1,
        "not_executed_reason": not_executed_reason,
        "called": inputs.called,
        "accepted": accepted,
        "execution": inputs.execution,
        "status_parse": spec.get("status_parse"),
        "route_health": spec.get("route_health") if isinstance(spec.get("route_health"), dict) else None,
        "stop_reason": spec.get("stop_reason"),
        "provenance_level": _coerce_str_none(spec.get("provenance_level")),
        "usage": usage,
        "timing_unsupported_reason": timing_unsupported_reason,
        "timing": {
            "configured_timeout_seconds": inputs.timeout_seconds,
            "started_at": attempt_timing.get("started_at"),
            "completed_at": attempt_timing.get("completed_at"),
            "elapsed_seconds": attempt_timing.get("elapsed_seconds"),
            "timed_out": attempt_timing.get("timed_out"),
            "timing_source": attempt_timing.get("timing_source"),
        },
    }


def _assemble_debug_telemetry(
    *,
    packet_id: str,
    role: str,
    route_class: str | None,
    output_name: str,
    prompt_name: str,
    prompt_stats: dict[str, Any],
    output_stats: dict[str, Any],
    telemetry: dict[str, Any],
    attempts: list[dict[str, Any]],
    determinism_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the final debug-telemetry dict, identical to the inlined return."""
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": role,
        **({"route_class": route_class} if route_class else {}),
        "output_artifact": output_name,
        "prompt_artifact": prompt_name,
        "text_metrics": {
            "prompt_chars": prompt_stats["chars"],
            "prompt_bytes": prompt_stats["bytes"],
            "output_chars": output_stats["chars"],
            "output_bytes": output_stats["bytes"],
            "event_log_chars": telemetry["event_log_chars"],
            "event_log_bytes": telemetry["event_log_bytes"],
            "debug_overhead_chars": 0,
        },
        "model_usage": {
            "attempts": attempts,
            "totals": telemetry["totals"]["known_usage"] if isinstance(telemetry.get("totals"), dict) else None,
        },
        "time_metrics": {
            "attempts": [
                {
                    "alias": attempt["alias"],
                    "provider": attempt["provider"],
                    "model": attempt["model"],
                    "timing": attempt["timing"],
                }
                for attempt in attempts
            ],
        },
        "determinism": {
            "artifacts": determinism_artifacts,
        },
        "success_metrics": {
            "attempts_declared": len(attempts),
            "attempts_called": sum(1 for attempt in attempts if attempt.get("called") is True),
            "candidate_attempts": len(attempts),
            "executed_attempts": sum(1 for attempt in attempts if attempt.get("called") is True),
            "accepted_alias": telemetry["accepted_alias"],
            "accepted_attempts": sum(1 for attempt in attempts if attempt.get("accepted") is True),
            "fallback_count": max(0, sum(1 for attempt in attempts if attempt.get("called") is True) - 1),
            "route_class": route_class,
        },
    }


def build_debug_telemetry(
    *,
    packet_dir: Path,
    packet_id: str,
    role: str,
    output_name: str,
    prompt_name: str,
    attempt_specs: list[dict[str, Any]],
    output_json: dict[str, Any] | None,
    output_stats: dict[str, Any],
    prompt_stats: dict[str, Any],
    telemetry: dict[str, Any],
) -> dict[str, Any]:
    route_class = (
        output_json.get("route_class")
        if isinstance(output_json, dict) and isinstance(output_json.get("route_class"), str)
        else None
    )
    attempts: list[dict[str, Any]] = []
    packet_timing = _packet_debug_timing(packet_dir)
    determinism_artifacts: list[dict[str, Any]] = [
        _artifact_hash_record(packet_dir / prompt_name, "prompt"),
        _artifact_hash_record(packet_dir / output_name, "output"),
    ]
    for index, spec in enumerate(attempt_specs):
        inputs = _debug_attempt_inputs(packet_dir, spec)
        attempt_timing, timing_unsupported_reason = _resolve_attempt_timing_initial(inputs, packet_timing)
        accepted = bool(telemetry["accepted_alias"] == spec.get("alias", ""))
        for path in inputs.event_log_paths:
            determinism_artifacts.append(_artifact_hash_record(path, "event_logs"))
        for path in inputs.probe_log_paths:
            determinism_artifacts.append(_artifact_hash_record(path, "probe_logs"))
        usage = sum_usage(inputs.event_logs + inputs.probe_logs)
        timing_unsupported_reason = _finalize_attempt_timing(
            attempt_timing, timing_unsupported_reason, inputs, packet_timing
        )
        not_executed_reason = _normalize_not_executed_reason(
            index=index,
            called_before=any(item.get("called") for item in attempts),
            called=inputs.called,
            output_json=output_json,
            spec=spec,
        )
        attempts.append(
            _build_debug_attempt(
                index=index,
                spec=spec,
                packet_id=packet_id,
                role=role,
                inputs=inputs,
                accepted=accepted,
                usage=usage,
                not_executed_reason=not_executed_reason,
                timing_unsupported_reason=timing_unsupported_reason,
                attempt_timing=attempt_timing,
            )
        )

    return _assemble_debug_telemetry(
        packet_id=packet_id,
        role=role,
        route_class=route_class,
        output_name=output_name,
        prompt_name=prompt_name,
        prompt_stats=prompt_stats,
        output_stats=output_stats,
        telemetry=telemetry,
        attempts=attempts,
        determinism_artifacts=determinism_artifacts,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument(
        "--role",
        choices=["worker", "research-worker", "reviewer", "prompt-auditor", "plan_amender", "lite_advisor"],
        required=True,
    )
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--prompt-name", default="prompt.md")
    parser.add_argument("--attempt-json", action="append", default=[])
    parser.add_argument("--output", default="telemetry.json")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write packet-level telemetry.debug.json with detailed timing/provenance diagnostics.",
    )
    parser.add_argument("--debug-output", default="telemetry.debug.json")
    args = parser.parse_args()

    packet_dir = Path(args.packet_dir).resolve()
    if not packet_dir.is_dir():
        raise SystemExit(f"--packet-dir must be an existing directory: {packet_dir}")
    attempt_specs = load_attempts(args.attempt_json)
    telemetry = build_telemetry(
        packet_dir=packet_dir,
        packet_id=args.packet_id,
        role=args.role,
        output_name=args.output_name,
        prompt_name=args.prompt_name,
        attempt_specs=attempt_specs,
    )
    prompt_path = packet_dir / args.prompt_name
    output_path = packet_dir / args.output_name
    prompt_stats = file_stats(prompt_path)
    output_stats = file_stats(output_path)
    output_json = read_json(output_path)
    telemetry_path = packet_dir / args.output
    telemetry_path.write_text(json.dumps(telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.debug:
        debug_path = packet_dir / args.debug_output
        debug_telemetry = build_debug_telemetry(
            packet_dir=packet_dir,
            packet_id=args.packet_id,
            role=args.role,
            output_name=args.output_name,
            prompt_name=args.prompt_name,
            attempt_specs=attempt_specs,
            output_json=output_json,
            output_stats=output_stats,
            prompt_stats=prompt_stats,
            telemetry=telemetry,
        )
        debug_path.write_text(json.dumps(debug_telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(debug_path)
    print(telemetry_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
