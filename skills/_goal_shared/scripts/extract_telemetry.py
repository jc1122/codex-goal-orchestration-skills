#!/usr/bin/env python3
"""Extract deterministic packet telemetry from launcher artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


TOKEN_KEYS = {
    "input_tokens": {"input_tokens", "prompt_tokens", "inputTokens", "promptTokens", "inputTokenCount", "promptTokenCount"},
    "output_tokens": {"output_tokens", "completion_tokens", "outputTokens", "completionTokens", "outputTokenCount", "completionTokenCount"},
    "reasoning_tokens": {"reasoning_tokens", "reasoningTokens", "reasoningTokenCount"},
    "cached_input_tokens": {"cached_input_tokens", "cachedInputTokens", "cachedInputTokenCount"},
    "total_tokens": {"total_tokens", "totalTokens", "totalTokenCount"},
}


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
    if role == "reviewer" and output.get("role") == "reviewer" and output.get("findings") == ["Reviewer primary and fallback failed without producing review.json."]:
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
    attempts = []
    for spec in attempt_specs:
        event_logs = [file_stats(packet_dir / str(path)) for path in spec.get("event_logs", [])]
        probe_logs = [file_stats(packet_dir / str(path)) for path in spec.get("probe_logs", [])]
        called = any(item["exists"] for item in event_logs + probe_logs)
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
                "called": called,
                "event_logs": event_logs,
                "probe_logs": probe_logs,
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
            "event_log_chars": total_log_chars,
            "event_log_bytes": total_log_bytes,
            "known_usage": sum_usage(called_attempts),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--role", choices=["worker", "research-worker", "reviewer", "prompt-auditor", "plan_amender", "lite_advisor"], required=True)
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--prompt-name", default="prompt.md")
    parser.add_argument("--attempt-json", action="append", default=[])
    parser.add_argument("--output", default="telemetry.json")
    args = parser.parse_args()

    packet_dir = Path(args.packet_dir).resolve()
    if not packet_dir.is_dir():
        raise SystemExit(f"--packet-dir must be an existing directory: {packet_dir}")
    telemetry = build_telemetry(
        packet_dir=packet_dir,
        packet_id=args.packet_id,
        role=args.role,
        output_name=args.output_name,
        prompt_name=args.prompt_name,
        attempt_specs=load_attempts(args.attempt_json),
    )
    output_path = packet_dir / args.output
    output_path.write_text(json.dumps(telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
