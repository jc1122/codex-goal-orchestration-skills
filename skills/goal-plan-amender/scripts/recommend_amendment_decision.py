#!/usr/bin/env python3
"""Recommend deterministic amendment launch-or-skip decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

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


def branch_entries(manifest: dict) -> list[dict]:
    branches = manifest.get("branches")
    if not isinstance(branches, list) or not branches:
        raise SystemExit("manifest branches must be a non-empty array")
    result = []
    for index, branch in enumerate(branches):
        if not isinstance(branch, dict):
            raise SystemExit(f"manifest branches[{index}] must be an object")
        branch_id = branch.get("id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            raise SystemExit(f"manifest branches[{index}].id must be a non-empty string")
        result.append(branch)
    return result


def load_terminal_status_files(manifest_path: Path, branches: list[dict]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    bundle_dir = manifest_path.parent
    for branch in branches:
        branch_id = str(branch.get("id"))
        rel_path = branch.get("status_path")
        if not isinstance(rel_path, str) or relative_path_defect(rel_path, f"branch {branch_id}.status_path"):
            continue
        status_path = bundle_dir / rel_path
        if not status_path.exists():
            continue
        try:
            status = load_json_object(status_path)
        except Exception:
            continue
        value = status.get("status")
        if value in CONTRACT.STATUSES:
            statuses[branch_id] = str(value)
    return statuses


def branch_dependencies(branches: list[dict]) -> dict[str, list[str]]:
    known = [str(branch["id"]) for branch in branches]
    known_set = set(known)
    dependencies: dict[str, list[str]] = {}
    for branch in branches:
        branch_id = str(branch["id"])
        raw_deps = branch.get("depends_on", [])
        if raw_deps is None:
            raw_deps = []
        if not isinstance(raw_deps, list):
            raise SystemExit(f"branch {branch_id}.depends_on must be an array")
        deps = []
        for dep in raw_deps:
            if not isinstance(dep, str) or not dep.strip():
                raise SystemExit(f"branch {branch_id}.depends_on entries must be non-empty strings")
            if dep not in known_set:
                raise SystemExit(f"branch {branch_id} depends on unknown branch id: {dep}")
            deps.append(dep)
        dependencies[branch_id] = deps
    return dependencies


def recommendation(manifest_path: Path, manifest: dict, *, active_ids: list[str], terminal_ids: list[str]) -> dict:
    branches = branch_entries(manifest)
    branch_ids = [str(branch["id"]) for branch in branches]
    deps = branch_dependencies(branches)
    active, terminal, terminal_status = protected_ids(
        manifest_path,
        manifest,
        active_ids=active_ids,
        terminal_ids=terminal_ids,
        infer_scheduler=True,
    )
    terminal_status.update(load_terminal_status_files(manifest_path, branches))
    terminal |= set(terminal_status)
    unknown_active = sorted(active - set(branch_ids))
    unknown_terminal = sorted(terminal - set(branch_ids))
    if unknown_active or unknown_terminal:
        raise SystemExit(
            f"protected ids are not manifest branches: active={unknown_active}, terminal={unknown_terminal}"
        )
    if not terminal:
        raise SystemExit("at least one terminal branch checkpoint is required")

    pass_ids = {branch_id for branch_id, status in terminal_status.items() if status == "pass"}
    nonpass_ids = {
        branch_id
        for branch_id, status in terminal_status.items()
        if branch_id in branch_ids and status in {"partial", "blocked", "failed"}
    }
    unstarted = [branch_id for branch_id in branch_ids if branch_id not in terminal and branch_id not in active]
    eligible = [branch_id for branch_id in unstarted if all(dep in pass_ids for dep in deps.get(branch_id, []))]
    stalled_by_nonpass = [
        branch_id for branch_id in unstarted if any(dep in nonpass_ids for dep in deps.get(branch_id, []))
    ]
    pending_on_active = [
        branch_id
        for branch_id in unstarted
        if branch_id not in eligible and any(dep in active for dep in deps.get(branch_id, []))
    ]

    if stalled_by_nonpass:
        decision = "launch"
        reason_code = "blocker_stalls_downstream"
        reason = "Non-pass terminal branch dependency stalls downstream unstarted branch ids: " + ", ".join(
            stalled_by_nonpass
        )
    elif eligible:
        decision = "skip"
        reason_code = "eligible_work_remains"
        reason = "Existing manifest work remains eligible without an amendment: " + ", ".join(eligible)
    elif active or pending_on_active:
        decision = "skip"
        reason_code = "finalization_still_plausible"
        reason = "Existing active or pending dependency work can still complete without an amendment."
    elif set(branch_ids) <= terminal and all(terminal_status.get(branch_id) == "pass" for branch_id in branch_ids):
        decision = "skip"
        reason_code = "no_adaptation_needed"
        reason = "All manifest branches are terminal pass; no future adaptation is needed."
    else:
        decision = "launch"
        reason_code = "no_eligible_branch"
        reason = "No manifest branch is eligible after the terminal checkpoint; a future-work amendment may be needed."

    return {
        "decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "eligible_branch_ids": eligible,
        "unstarted_branch_ids": unstarted,
        "stalled_branch_ids": stalled_by_nonpass,
        "pending_on_active_branch_ids": pending_on_active,
    }


def write_decision(
    manifest_path: Path, manifest: dict, amendment_id: str, rec: dict, *, scheduler_event_seq: int | None, replace: bool
) -> Path:
    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        raise SystemExit("manifest adaptation_policy does not match the shared amendment proposal policy")
    try:
        validate_amender_model_policy(manifest, manifest_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    amendment_id = ensure_amendment_id(amendment_id)
    decision = rec["decision"]
    reason_code = rec["reason_code"]
    if decision == "launch" and reason_code not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("recommended reason_code is not valid for a launch decision")
    if decision == "skip" and reason_code in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit(
            "recommended reason_code describes a launch-required condition and is not valid for a skip decision"
        )
    overlap = sorted(set(rec["active_branch_ids"]) & set(rec["terminal_branch_ids"]))
    if overlap:
        raise SystemExit(
            "branch ids cannot be both active and terminal in an amendment decision: " + ", ".join(overlap)
        )
    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    if decision_path.exists() and not replace:
        raise SystemExit(f"amendment decision already exists; pass --replace to recreate: {decision_path}")
    scheduler_path = (
        manifest.get("parallelization", {}).get("scheduler_path")
        if isinstance(manifest.get("parallelization"), dict)
        else None
    )
    scheduler_rel = (
        scheduler_path
        if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path")
        else CONTRACT.MAIN_SCHEDULER_PATH
    )
    data = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "decision": decision,
        "reason_code": reason_code,
        "reason": rec["reason"],
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
        "scheduler_path": scheduler_rel,
        "scheduler_event_seq": scheduler_event_seq,
        "active_branch_ids": rec["active_branch_ids"],
        "terminal_branch_ids": rec["terminal_branch_ids"],
        "terminal_branch_statuses": rec["terminal_branch_statuses"],
        "packet_path": (amendments_dir / f"{amendment_id}.packet").as_posix() if decision == "launch" else None,
        "proposal_path": (amendments_dir / f"{amendment_id}.proposal.json").as_posix()
        if decision == "launch"
        else None,
        "validation_path": (amendments_dir / f"{amendment_id}.validation.json").as_posix()
        if decision == "launch"
        else None,
        "accepted_path": (amendments_dir / f"{amendment_id}.accepted.json").as_posix()
        if decision == "launch"
        else None,
    }
    write_json(decision_path, data)
    return decision_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--amendment-id")
    parser.add_argument("--write-decision", action="store_true")
    parser.add_argument("--scheduler-event-seq", type=int)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    manifest = load_json_object(manifest_path)
    rec = recommendation(manifest_path, manifest, active_ids=args.active_branch, terminal_ids=args.terminal_branch)
    decision_path = None
    if args.write_decision:
        if not args.amendment_id:
            raise SystemExit("--write-decision requires --amendment-id")
        decision_path = write_decision(
            manifest_path,
            manifest,
            args.amendment_id,
            rec,
            scheduler_event_seq=args.scheduler_event_seq,
            replace=args.replace,
        )
    output = {**rec, "decision_path": decision_path.as_posix() if decision_path else None}
    if args.json or not decision_path:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(decision_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
