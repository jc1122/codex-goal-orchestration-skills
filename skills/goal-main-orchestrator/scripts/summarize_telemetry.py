#!/usr/bin/env python3
"""Summarize deterministic telemetry artifacts for a prepared goal bundle."""

from __future__ import annotations

import argparse
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


def discover_telemetry_files(bundle_dir: Path) -> list[Path]:
    files: list[Path] = []
    for root_name in TELEMETRY_ROOTS:
        root = bundle_dir / root_name
        if root.is_dir():
            files.extend(sorted(root.glob("**/telemetry.json")))
    return sorted(files, key=lambda path: path.relative_to(bundle_dir).as_posix())


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("telemetry artifact must be a JSON object")
    return data


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


def compact_usage(usage: dict[str, int]) -> dict[str, int]:
    return {key: usage.get(key, 0) for key in USAGE_KEYS if usage.get(key, 0)}


def compact_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    result = dict(bucket)
    result["known_usage"] = compact_usage(result.get("known_usage", {}))
    return result


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


def summarize(bundle_dir: Path) -> dict[str, Any]:
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
        packet_prompt_chars = data.get("prompt_chars") if isinstance(data.get("prompt_chars"), int) else 0
        packet_prompt_bytes = data.get("prompt_bytes") if isinstance(data.get("prompt_bytes"), int) else 0

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
            key = attempt_group_key(role, attempt)
            bucket = ensure_bucket(by_attempt, key)
            bucket["packet_count"] += 1
            bucket["attempts_declared"] += 1
            if attempt.get("called") is True:
                bucket["attempts_called"] += 1
                bucket["model_prompt_chars_estimate"] += packet_prompt_chars
                bucket["model_prompt_bytes_estimate"] += packet_prompt_bytes
            if attempt.get("accepted") is True:
                bucket["accepted_attempts"] += 1
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
                "accepted_alias": data.get("accepted_alias"),
                "prompt_chars": data.get("prompt_chars"),
                "output_chars": data.get("output_chars"),
                "event_log_chars": data.get("event_log_chars"),
                "attempts": packet_attempts,
            }
        )

    return {
        "schema_version": 1,
        "bundle_dir": bundle_dir.as_posix(),
        "telemetry_files": [path.relative_to(bundle_dir).as_posix() for path in files],
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output", help="Defaults to <bundle-dir>/telemetry.summary.json")
    args = parser.parse_args()

    bundle_dir = Path(args.bundle_dir).resolve()
    if not bundle_dir.is_dir():
        raise SystemExit(f"--bundle-dir must be an existing directory: {bundle_dir}")
    output_path = Path(args.output).resolve() if args.output else bundle_dir / "telemetry.summary.json"
    summary = summarize(bundle_dir)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
