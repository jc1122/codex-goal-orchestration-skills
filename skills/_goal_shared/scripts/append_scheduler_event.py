#!/usr/bin/env python3
"""Append a schema v2 scheduler event atomically."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


SCHEDULER_EVENTS = {
    "ready",
    "launch",
    "finish",
    "close",
    "refill",
    "defer",
    "under_capacity",
    "blocked",
}
TERMINAL_STATUSES = {"pass", "partial", "blocked", "failed"}
REASON_REQUIRED_EVENTS = {"defer", "under_capacity", "blocked"}
REASON_CODES = {
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


def load_ledger(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"scheduler ledger does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"scheduler ledger must be a JSON object: {path}")
    events = data.get("events")
    if not isinstance(events, list):
        raise SystemExit("scheduler ledger events must be an array")
    return data


def deterministic_timestamp(seq: int) -> str:
    return (
        (datetime(2000, 1, 1, tzinfo=UTC) + timedelta(seconds=seq)).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp_name = handle.name
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_name, path)


def split_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                result.append(item)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--event", choices=sorted(SCHEDULER_EVENTS), required=True)
    parser.add_argument("--id")
    parser.add_argument("--status", choices=sorted(TERMINAL_STATUSES))
    parser.add_argument("--eligible-id", action="append", default=[])
    parser.add_argument("--reason-code", choices=sorted(REASON_CODES))
    parser.add_argument("--reason")
    parser.add_argument("--runtime-ref", required=True)
    parser.add_argument(
        "--timestamp", help="Defaults to a deterministic synthetic ISO timestamp derived from the event sequence."
    )
    args = parser.parse_args()

    ledger_path = Path(args.ledger).resolve()
    ledger = load_ledger(ledger_path)
    if ledger.get("schema_version") != 2:
        raise SystemExit("scheduler ledger schema_version must be 2")
    events = ledger["events"]
    seq = len(events) + 1
    event = {
        "seq": seq,
        "timestamp": args.timestamp or deterministic_timestamp(seq),
        "wall_clock_timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "runtime_ref": args.runtime_ref,
        "event": args.event,
    }
    if args.event not in {"refill", "under_capacity"}:
        if not args.id:
            raise SystemExit(f"{args.event} event requires --id")
        event["id"] = args.id
    if args.event in {"finish"}:
        if not args.status:
            raise SystemExit("finish event requires --status")
        event["status"] = args.status
    if args.event in {"refill", "under_capacity"}:
        eligible_ids = split_values(args.eligible_id)
        if not eligible_ids:
            raise SystemExit(f"{args.event} event requires at least one --eligible-id")
        event["eligible_ids"] = eligible_ids
    if args.event in REASON_REQUIRED_EVENTS:
        if not args.reason_code:
            raise SystemExit(f"{args.event} event requires --reason-code")
        if not args.reason:
            raise SystemExit(f"{args.event} event requires --reason")
        event["reason_code"] = args.reason_code
        event["reason"] = args.reason
    elif args.reason or args.reason_code:
        raise SystemExit("--reason and --reason-code are allowed only for defer, under_capacity, or blocked events")
    events.append(event)
    atomic_write_json(ledger_path, ledger)
    print(ledger_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
