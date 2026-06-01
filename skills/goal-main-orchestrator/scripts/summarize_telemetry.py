#!/usr/bin/env python3
"""Summarize deterministic telemetry artifacts for a prepared goal bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


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
PREMIUM_ALIASES = {
    "gpt-5.5",
    "gemini-pro",
    "gemini-flash",
    "copilot-gpt-5.4",
    "codex-research",
}
MINI_SPARK_ALIASES = ("codex-mini", "codex-spark")
DEBUG_TELEMETRY_FILENAME = "telemetry.debug.json"
DEBUG_EVENTS_FILENAME = "debug.events.jsonl"
RUN_TRACE_FILENAME = "run.trace.jsonl"


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


def compact_artifact_ref(bundle_dir: Path, path: Path) -> dict[str, Any]:
    return {
        "path": rel_path(bundle_dir, path),
        "sha256": file_sha256(path),
        "size_bytes": file_size(path),
    }


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
        packet_event = {
            "event_type": "packet_telemetry",
            "source": rel_path(bundle_dir, path),
            "packet_id": packet_id,
            "role": role,
            "route_class": data.get("route_class"),
            "prompt_artifact": data.get("prompt_artifact"),
            "output_artifact": data.get("output_artifact"),
            "text_metrics": data.get("text_metrics") if isinstance(data.get("text_metrics"), dict) else None,
            "success_metrics": data.get("success_metrics") if isinstance(data.get("success_metrics"), dict) else None,
        }
        append_trace_event(events, {key: value for key, value in packet_event.items() if value is not None})
        model_usage = data.get("model_usage") if isinstance(data.get("model_usage"), dict) else {}
        attempts = model_usage.get("attempts") if isinstance(model_usage.get("attempts"), list) else []
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                continue
            timing = attempt.get("timing") if isinstance(attempt.get("timing"), dict) else {}
            trace = {
                "event_type": "model_attempt",
                "source": rel_path(bundle_dir, path),
                "packet_id": packet_id,
                "role": role,
                "attempt_index": index,
                "alias": attempt.get("alias"),
                "provider": attempt.get("provider"),
                "model": attempt.get("model"),
                "effort": attempt.get("effort"),
                "timeout_seconds": attempt.get("timeout_seconds"),
                "called": attempt.get("called"),
                "accepted": attempt.get("accepted"),
                "usage": attempt.get("usage") if isinstance(attempt.get("usage"), dict) else None,
                "timed_out": timing.get("timed_out"),
                "elapsed_seconds": timing.get("elapsed_seconds"),
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
    "research/*/research.json",
    "reviewers/*/review.json",
    "lite/*/advice.json",
    "amendments/*.decision.json",
    "amendments/*.proposal.json",
    "amendments/*.validation.json",
    "amendments/*.accepted.json",
)


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
        data = read_json_object(path)
        artifact = compact_artifact_ref(bundle_dir, path)
        trace = {
            "event_type": "terminal_artifact",
            "source": rel_path(bundle_dir, path),
            "artifact_kind": terminal_artifact_kind(path),
            "artifact": artifact,
            "status": data.get("status") if isinstance(data, dict) else None,
            "review_status": data.get("review_status") if isinstance(data, dict) else None,
            "can_start": data.get("can_start") if isinstance(data, dict) else None,
            "packet_id": data.get("packet_id") if isinstance(data, dict) else None,
            "branch_id": data.get("branch_id") if isinstance(data, dict) else None,
            "job_id": data.get("job_id") if isinstance(data, dict) else None,
            "amendment_id": data.get("amendment_id") if isinstance(data, dict) else None,
        }
        append_trace_event(events, {key: value for key, value in trace.items() if value is not None})
    return events


def build_run_trace(bundle_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for producer in (
        iter_scheduler_trace_events,
        iter_debug_event_trace_events,
        iter_launcher_state_trace_events,
        iter_debug_telemetry_trace_events,
        iter_terminal_artifact_trace_events,
    ):
        events.extend(producer(bundle_dir))

    def sort_key(event: dict[str, Any]) -> tuple[str, str, int, int]:
        timestamp = event.get("timestamp") if isinstance(event.get("timestamp"), str) else ""
        source = event.get("source") if isinstance(event.get("source"), str) else ""
        source_seq = event.get("scheduler_seq") or event.get("state_seq") or event.get("line") or event.get("attempt_index") or 0
        if not isinstance(source_seq, int) or isinstance(source_seq, bool):
            source_seq = 0
        type_order = {
            "scheduler_event": 10,
            "packet_debug_event": 20,
            "launcher_state": 30,
            "model_attempt": 40,
            "packet_telemetry": 50,
            "terminal_artifact": 60,
            "trace_defect": 90,
        }.get(str(event.get("event_type")), 80)
        return (timestamp, source, type_order, source_seq)

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


def attempt_group_key(role: str, attempt: dict[str, Any]) -> str:
    return "\u001f".join(
        [
            role,
            str(attempt.get("provider") or ""),
            str(attempt.get("model") or ""),
            str(attempt.get("effort") or ""),
            str(attempt.get("alias") or ""),
        ]
    )


def attempt_group_row(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    role, provider, model, effort, alias = key.split("\u001f")
    row = {
        "role": role,
        "provider": provider,
        "model": model,
        "effort": effort or None,
        "alias": alias,
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
        "cached_input_tokens": cached_input_tokens if isinstance(cached_input_tokens, int) and not isinstance(cached_input_tokens, bool) else None,
        "threshold": threshold,
        "input_to_prompt_estimate_ratio": round(input_tokens / prompt_tokens_estimate, 2),
        "message": "Known input tokens greatly exceed the packet prompt estimate; inspect launcher flags and inherited context before broad log reads.",
    }


def summarize_standard(bundle_dir: Path) -> dict[str, Any]:
    files = discover_telemetry_files(bundle_dir)
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
    fallback_count = 0
    failed_same_class_attempts = 0
    packets = []
    defects = []
    token_pressure_warnings = []

    for path in files:
        rel = path.relative_to(bundle_dir).as_posix()
        try:
            data = load_json(path)
        except Exception as exc:  # noqa: BLE001
            defects.append(f"{rel}: unreadable telemetry JSON: {exc}")
            continue
        role = data.get("role") if isinstance(data.get("role"), str) else "unknown"
        packet_id = data.get("packet_id") if isinstance(data.get("packet_id"), str) else path.parent.name
        attempts = data.get("attempts") if isinstance(data.get("attempts"), list) else []
        called_attempts = [item for item in attempts if isinstance(item, dict) and item.get("called") is True]
        accepted_attempts = [item for item in attempts if isinstance(item, dict) and item.get("accepted") is True]
        fallback_count += max(0, len(called_attempts) - 1)
        packet_prompt_chars = data.get("prompt_chars") if isinstance(data.get("prompt_chars"), int) else 0
        packet_prompt_bytes = data.get("prompt_bytes") if isinstance(data.get("prompt_bytes"), int) else 0
        route_class = data.get("route_class") if isinstance(data.get("route_class"), str) else None

        for bucket in (totals, ensure_bucket(by_role, role)):
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
            alias = attempt.get("alias") if isinstance(attempt.get("alias"), str) and attempt.get("alias") else "unknown"
            declared_aliases[alias] = declared_aliases.get(alias, 0) + 1
            if alias in PREMIUM_ALIASES:
                premium_aliases_declared[alias] = premium_aliases_declared.get(alias, 0) + 1
            key = attempt_group_key(role, attempt)
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
            if attempt.get("accepted") is True:
                accepted_aliases[alias] = accepted_aliases.get(alias, 0) + 1
                if alias in PREMIUM_ALIASES:
                    premium_aliases_accepted[alias] = premium_aliases_accepted.get(alias, 0) + 1
                bucket["accepted_attempts"] += 1
            if alias in MINI_SPARK_ALIASES:
                alias_bucket = mini_spark_usage[alias]
                alias_bucket["attempts_declared"] += 1
                if attempt.get("called") is True:
                    alias_bucket["attempts_called"] += 1
                if attempt.get("accepted") is True:
                    alias_bucket["accepted_attempts"] += 1
                add_usage(alias_bucket["known_usage"], attempt.get("usage"))
            if alias in PREMIUM_ALIASES and attempt.get("called") is not True:
                premium_aliases_avoided[alias] = premium_aliases_avoided.get(alias, 0) + 1
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
                    "called": attempt.get("called") is True,
                    "accepted": attempt.get("accepted") is True,
                    "known_usage": attempt.get("usage") if isinstance(attempt.get("usage"), dict) else None,
                }
            )

        packets.append(
            {
                "path": rel,
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

    cost_summary = {
        "declared_attempts": totals["attempts_declared"],
        "called_attempts": totals["attempts_called"],
        "accepted_aliases": dict(sorted(accepted_aliases.items())),
        "declared_aliases": dict(sorted(declared_aliases.items())),
        "called_aliases": dict(sorted(called_aliases.items())),
        "premium_aliases_declared": dict(sorted(premium_aliases_declared.items())),
        "premium_aliases_called": dict(sorted(premium_aliases_called.items())),
        "premium_aliases_accepted": dict(sorted(premium_aliases_accepted.items())),
        "premium_aliases_avoided": dict(sorted(premium_aliases_avoided.items())),
        "mini_spark_usage": {
            alias: compact_bucket(mini_spark_usage[alias])
            for alias in MINI_SPARK_ALIASES
        },
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
        "defects": defects,
        "totals": compact_bucket(totals),
        "by_role": {key: compact_bucket(by_role[key]) for key in sorted(by_role)},
        "by_provider_model_alias": [
            attempt_group_row(key, by_attempt[key])
            for key in sorted(by_attempt)
        ],
        "premium_usage": {
            key: {
                **{field: value for field, value in bucket.items() if field != "known_usage"},
                "known_usage": compact_usage(bucket.get("known_usage", {})),
            }
            for key, bucket in premium_usage.items()
        },
        "cost_summary": cost_summary,
        "token_pressure": {
            "approx_chars_per_token": APPROX_CHARS_PER_TOKEN,
            "input_warn_min": TOKEN_PRESSURE_INPUT_WARN_MIN,
            "input_warn_ratio": TOKEN_PRESSURE_INPUT_WARN_RATIO,
            "warnings": sorted(
                token_pressure_warnings,
                key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]), str(item["alias"])),
            ),
        },
        "packets": sorted(packets, key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]))),
    }


def summarize_debug(bundle_dir: Path) -> dict[str, Any]:
    files = discover_telemetry_files(bundle_dir, debug=True)
    trace_events = build_run_trace(bundle_dir)
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
    }
    determinism = {
        "packets_with_artifacts": 0,
        "artifact_counts_by_kind": {},
        "drift_count": 0,
    }
    drift_packet_ids: list[str] = []
    packets = []
    defects = []
    packet_attempts: list[dict[str, Any]] = []

    for path in files:
        rel = path.relative_to(bundle_dir).as_posix()
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
                elapsed_ms = event.get("elapsed_ms")
                if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms >= 0:
                    time_totals["elapsed_seconds_sum"] += elapsed_ms / 1000
                    time_totals["elapsed_seconds_count"] += 1
        role = data.get("role") if isinstance(data.get("role"), str) else "unknown"
        packet_id = data.get("packet_id") if isinstance(data.get("packet_id"), str) else path.parent.name

        text_metrics = data.get("text_metrics") if isinstance(data.get("text_metrics"), dict) else {}
        model_usage = data.get("model_usage") if isinstance(data.get("model_usage"), dict) else {}
        time_metrics = data.get("time_metrics") if isinstance(data.get("time_metrics"), dict) else {}
        success_metrics = data.get("success_metrics") if isinstance(data.get("success_metrics"), dict) else {}
        determinism_payload = data.get("determinism") if isinstance(data.get("determinism"), dict) else {}
        attempts = model_usage.get("attempts") if isinstance(model_usage.get("attempts"), list) else []

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

        attempts_declared = int(success_metrics.get("attempts_declared")) if isinstance(success_metrics.get("attempts_declared"), int) else len(attempts)
        attempts_called = int(success_metrics.get("attempts_called")) if isinstance(success_metrics.get("attempts_called"), int) else sum(
            1 for item in attempts if isinstance(item, dict) and item.get("called") is True
        )
        accepted_attempts = (
            int(success_metrics.get("accepted_attempts"))
            if isinstance(success_metrics.get("accepted_attempts"), int)
            else sum(1 for item in attempts if isinstance(item, dict) and item.get("accepted") is True)
        )
        accepted_alias = success_metrics.get("accepted_alias") if isinstance(success_metrics.get("accepted_alias"), str) else None
        fallback_count = (
            int(success_metrics.get("fallback_count")) if isinstance(success_metrics.get("fallback_count"), int) else max(0, attempts_called - 1)
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
            else:
                time_totals["attempts_missing_timing"] += 1
            if item.get("accepted") is True:
                role_bucket["accepted_attempts"] += 1
                alias_bucket["accepted_attempts"] += 1
            add_usage(role_bucket["known_usage"], item.get("usage"))
            add_usage(alias_bucket["known_usage"], item.get("usage"))
            if called and isinstance(item.get("usage"), dict) and any(
                isinstance(item["usage"].get(key), int) and not isinstance(item["usage"].get(key), bool)
                for key in USAGE_KEYS
            ):
                success["attempts_with_known_tokens"] += 1

            timing = item.get("timing") if isinstance(item.get("timing"), dict) else None
            if called and isinstance(timing, dict):
                elapsed = timing.get("elapsed_seconds")
                if isinstance(elapsed, int) and elapsed >= 0 and not isinstance(elapsed, bool):
                    time_totals["elapsed_seconds_sum"] += elapsed
                    time_totals["elapsed_seconds_count"] += 1
                elif isinstance(elapsed, float) and elapsed >= 0.0:
                    time_totals["elapsed_seconds_sum"] += elapsed
                    time_totals["elapsed_seconds_count"] += 1
                else:
                    time_totals["attempts_missing_timing"] += 1
                if isinstance(elapsed, int) or isinstance(elapsed, float):
                    time_totals["attempts_with_timing"] += 1
                timed_out = timing.get("timed_out")
                if isinstance(timed_out, bool):
                    time_totals["timed_out_known"] += 1
                    if timed_out:
                        time_totals["timed_out_attempts"] += 1

        packet_artifact_hashes: dict[str, set[str]] = {}
        artifacts = determinism_payload.get("artifacts") if isinstance(determinism_payload.get("artifacts"), list) else []
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
        "defects": defects,
        "model_usage": {
            "totals": compact_usage(model_totals),
            "by_alias": {alias: compact_model_bucket(bucket) for alias, bucket in model_by_alias.items()},
            "by_role": {role: compact_model_bucket(bucket) for role, bucket in model_by_role.items()},
            "attempts_declared": success["attempts_declared"],
            "attempts_called": success["attempts_called"],
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
        },
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
            "accepted_attempts": success["accepted_attempts"],
            "attempts_with_known_tokens": success["attempts_with_known_tokens"],
            "accepted_aliases": dict(sorted(success["accepted_aliases"].items())),
            "fallback_count": success["fallback_count"],
            "fallback_rate": fallback_rate,
            "text_pressure_ratio": text_pressure_ratio,
        },
        "trace": trace_summary(trace_events),
        "packets": sorted(packet_attempts, key=lambda item: (str(item["role"]), str(item["packet_id"]), str(item["path"]))),
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
