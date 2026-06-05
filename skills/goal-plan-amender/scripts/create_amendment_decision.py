#!/usr/bin/env python3
"""Create a deterministic amendment launch-or-skip decision artifact."""

from __future__ import annotations

import argparse

from amendment_lib import (
    CONTRACT,
    ensure_amendment_id,
    load_json_object,
    protected_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    validate_amender_model_policy,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--amendment-id", required=True)
    parser.add_argument("--decision", choices=list(CONTRACT.AMENDMENT_DECISIONS), required=True)
    parser.add_argument("--reason-code", choices=list(CONTRACT.AMENDMENT_DECISION_REASON_CODES), required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--scheduler-event-seq", type=int)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    amendment_id = ensure_amendment_id(args.amendment_id)
    manifest = load_json_object(manifest_path)
    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        raise SystemExit("manifest adaptation_policy does not match the shared amendment proposal policy")
    try:
        validate_amender_model_policy(manifest, manifest_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.decision == "launch" and args.reason_code not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("--reason-code is not valid for a launch decision")
    if args.decision == "skip" and args.reason_code in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("--reason-code describes a launch-required condition and is not valid for a skip decision")
    if not args.reason.strip():
        raise SystemExit("--reason must be non-empty")

    try:
        active, terminal, terminal_status = protected_ids(
            manifest_path,
            manifest,
            active_ids=args.active_branch,
            terminal_ids=args.terminal_branch,
            infer_scheduler=True,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not terminal:
        raise SystemExit("at least one terminal branch id is required for an amendment decision")
    overlap = sorted(active & terminal)
    if overlap:
        raise SystemExit("branch ids cannot be both active and terminal in an amendment decision: " + ", ".join(overlap))

    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    if decision_path.exists() and not args.replace:
        raise SystemExit(f"amendment decision already exists; pass --replace to recreate: {decision_path}")
    scheduler_path = manifest.get("parallelization", {}).get("scheduler_path") if isinstance(manifest.get("parallelization"), dict) else None
    scheduler_rel = scheduler_path if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path") else CONTRACT.MAIN_SCHEDULER_PATH
    data = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "decision": args.decision,
        "reason_code": args.reason_code,
        "reason": args.reason.strip(),
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
        "scheduler_path": scheduler_rel,
        "scheduler_event_seq": args.scheduler_event_seq,
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "packet_path": (amendments_dir / f"{amendment_id}.packet").as_posix() if args.decision == "launch" else None,
        "proposal_path": (amendments_dir / f"{amendment_id}.proposal.json").as_posix() if args.decision == "launch" else None,
        "validation_path": (amendments_dir / f"{amendment_id}.validation.json").as_posix() if args.decision == "launch" else None,
        "accepted_path": (amendments_dir / f"{amendment_id}.accepted.json").as_posix() if args.decision == "launch" else None,
    }
    write_json(decision_path, data)
    print(decision_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
