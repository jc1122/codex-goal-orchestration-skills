#!/usr/bin/env python3
"""Assemble a conservative main status artifact from bundle-owned runtime artifacts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


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


def _load_status_validation():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "status_validation.py"
    if not path.exists():
        raise SystemExit(f"missing shared status validation helpers: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_status_validation", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared status validation helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
STATUS_VALIDATION = _load_status_validation()
STATUSES = set(CONTRACT.STATUSES)
REVIEW_STATUSES = set(CONTRACT.REVIEW_STATUSES)

resolve_absolute_path = STATUS_VALIDATION.resolve_absolute_path
load_json = STATUS_VALIDATION.load_json
sha256_file = STATUS_VALIDATION.sha256_file


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def branch_dependencies(branches: list[dict]) -> dict[str, list[str]]:
    known = {str(branch["id"]) for branch in branches}
    dependencies: dict[str, list[str]] = {}
    for branch in branches:
        branch_id = str(branch["id"])
        raw = branch.get("depends_on", [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise SystemExit(f"branch {branch_id}.depends_on must be an array")
        deps = []
        for item in raw:
            if not isinstance(item, str) or item not in known:
                raise SystemExit(f"branch {branch_id} depends on unknown branch id: {item!r}")
            deps.append(item)
        dependencies[branch_id] = deps
    return dependencies


def audit_status(bundle_dir: Path, blockers: list[str]) -> str:
    path = bundle_dir / "audit" / "prompt-audit.json"
    if not path.exists():
        blockers.append(f"prompt audit artifact is missing: {path}")
        return "missing"
    data = load_json(path)
    status = data.get("status")
    if status not in {"pass", "failed", "blocked"}:
        blockers.append(f"prompt audit artifact has invalid status: {status!r}")
        return "blocked"
    if status != "pass":
        blockers.append(f"prompt audit status is {status}")
    return str(status)


def recovery_map(branches: list[dict]) -> dict[str, list[str]]:
    recoveries: dict[str, list[str]] = {}
    for branch in branches:
        branch_id = branch.get("id")
        if not isinstance(branch_id, str) or not branch_id.strip():
            continue
        for field in ["recovers_from", "supersedes"]:
            values = branch.get(field)
            if not isinstance(values, list):
                continue
            for target in values:
                if isinstance(target, str) and target.strip():
                    recoveries.setdefault(target, [])
                    if branch_id not in recoveries[target]:
                        recoveries[target].append(branch_id)
    return recoveries


def recovery_branch_ids(summary: dict) -> list[str]:
    value = summary.get("recovered_by")
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def branch_is_successful(summary: dict | None) -> bool:
    return bool(summary and summary.get("status") == "pass" and summary.get("review_status") == "mergeable")


def branch_is_complete_or_recovered(summary: dict, by_id: dict[str, dict]) -> bool:
    if branch_is_successful(summary):
        return True
    if summary.get("recovery_status") != "recovered":
        return False
    return any(branch_is_successful(by_id.get(branch_id)) for branch_id in recovery_branch_ids(summary))


def branch_summaries(bundle_dir: Path, branches: list[dict], blockers: list[str]) -> list[dict]:
    summaries = []
    branch_recoveries = recovery_map(branches)
    for branch in branches:
        branch_id = str(branch["id"])
        status_rel = str(branch.get("status_path", ""))
        review_rel = str(branch.get("review_path", ""))
        status_path = bundle_dir / status_rel
        if not status_path.exists():
            blockers.append(f"branch status artifact is missing for {branch_id}: {status_path}")
            continue
        status_data = load_json(status_path)
        status_value = status_data.get("status")
        review_status = status_data.get("review_status", "missing")
        if status_value not in STATUSES:
            blockers.append(f"branch {branch_id} status artifact has invalid status: {status_value!r}")
            status_value = "failed"
        if review_status not in REVIEW_STATUSES:
            blockers.append(f"branch {branch_id} status artifact has invalid review_status: {review_status!r}")
            review_status = "missing"
        summary = {
            "branch_id": branch_id,
            "status": status_value,
            "status_path": status_rel,
            "review_path": review_rel,
            "review_status": review_status,
        }
        recovered_by = branch_recoveries.get(branch_id, [])
        if recovered_by:
            summary["recovered_by"] = recovered_by
            summary["recovery_status"] = "pending"
        review_waiver_path = status_data.get("review_waiver_path")
        if isinstance(review_waiver_path, str) and review_waiver_path.strip():
            summary["review_waiver_path"] = review_waiver_path
        summaries.append(summary)
    by_id = {str(item["branch_id"]): item for item in summaries if isinstance(item.get("branch_id"), str)}
    for summary in summaries:
        branch_id = str(summary["branch_id"])
        if summary.get("status") == "pass":
            if summary.get("review_status") != "mergeable":
                blockers.append(f"branch {branch_id} passed but review_status is {summary.get('review_status')}")
            continue
        recovered_by = recovery_branch_ids(summary)
        if recovered_by and any(branch_is_successful(by_id.get(recovery_id)) for recovery_id in recovered_by):
            summary["recovery_status"] = "recovered"
            continue
        if recovered_by:
            summary["recovery_status"] = "pending"
        status_rel = str(summary.get("status_path", ""))
        status_data = load_json(bundle_dir / status_rel) if status_rel and (bundle_dir / status_rel).exists() else {}
        for item in status_data.get("blockers", []):
            if isinstance(item, str) and item.strip():
                blockers.append(f"{branch_id}: {item.strip()}")
        if not status_data.get("blockers"):
            blockers.append(f"branch {branch_id} ended {summary.get('status')}")
    return summaries


def scheduler_rollup(manifest_path: Path, manifest: dict, branches: list[dict], *, status: str, blockers: list[str]) -> dict:
    defects: list[str] = []
    max_active = manifest.get("max_active_branch_agents", CONTRACT.MAX_ACTIVE_BRANCH_AGENTS)
    if not isinstance(max_active, int) or isinstance(max_active, bool):
        max_active = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
    expected_ids = [str(branch["id"]) for branch in branches]
    summary = STATUS_VALIDATION.validate_scheduler_artifact(
        defects,
        manifest_path.parent / CONTRACT.MAIN_SCHEDULER_PATH,
        "$.branch_parallelism.scheduler_path",
        scheduler_kind="main-branch-pool",
        expected_path=CONTRACT.MAIN_SCHEDULER_PATH,
        expected_ids=expected_ids,
        dependencies=branch_dependencies(branches),
        capacity=max_active,
        manifest_path=manifest_path,
        require_all_launched=status == "pass",
    )
    blockers.extend(f"main scheduler: {item}" for item in defects)
    return {
        "scheduler_path": CONTRACT.MAIN_SCHEDULER_PATH,
        "launched_ids": summary.get("launched", []),
        "finished_ids": summary.get("finished", []),
        "active_ids": summary.get("active", []),
        "blocked_ids": summary.get("blocked", []),
        "deferred_ids": summary.get("deferred", []),
        "max_observed_active": summary.get("max_observed_active", 0),
    }


def amendment_records(bundle_dir: Path) -> list[dict]:
    amendments_dir = bundle_dir / "amendments"
    if not amendments_dir.is_dir():
        return []
    records = []
    for path in sorted(amendments_dir.glob("*.decision.json")):
        data = load_json(path)
        amendment_id = data.get("amendment_id")
        decision = data.get("decision")
        if not isinstance(amendment_id, str) or decision not in CONTRACT.AMENDMENT_DECISIONS:
            continue
        records.append(
            {
                "amendment_id": amendment_id,
                "decision": decision,
                "decision_path": path.relative_to(bundle_dir).as_posix(),
                "packet_validation_path": (
                    f"amendments/{amendment_id}.packet/packet.validation.json"
                    if decision == "launch"
                    else None
                ),
            }
        )
    return records


def ensure_skip_decision(
    manifest_path: Path,
    manifest: dict,
    branch_statuses: list[dict],
    branch_parallelism: dict,
    *,
    allow_write: bool,
) -> list[dict]:
    existing = amendment_records(manifest_path.parent)
    if existing or not branch_statuses:
        return existing
    if not allow_write:
        return []
    terminal_statuses = {
        str(item["branch_id"]): str(item["status"])
        for item in branch_statuses
        if item.get("status") in STATUSES
    }
    if not terminal_statuses:
        return []
    amendment_id = "A000"
    decision = "skip"
    all_pass = all(value == "pass" for value in terminal_statuses.values())
    reason_code = "no_adaptation_needed" if all_pass else "finalization_still_plausible"
    reason = (
        "All terminal branch checkpoints are pass; no future-work amendment is needed."
        if all_pass
        else "Deterministic main closeout recorded a non-pass terminal state without launching future-work adaptation."
    )
    amendments_dir = manifest_path.parent / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    events_path = manifest_path.parent / CONTRACT.MAIN_SCHEDULER_PATH
    scheduler_event_seq = None
    if events_path.exists():
        events = load_json(events_path).get("events")
        if isinstance(events, list):
            seqs = [event.get("seq") for event in events if isinstance(event, dict)]
            ints = [seq for seq in seqs if isinstance(seq, int) and not isinstance(seq, bool)]
            scheduler_event_seq = max(ints) if ints else None
    data = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "decision": decision,
        "reason_code": reason_code,
        "reason": reason,
        "manifest": manifest_path.as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
        "scheduler_path": CONTRACT.MAIN_SCHEDULER_PATH,
        "scheduler_event_seq": scheduler_event_seq,
        "active_branch_ids": branch_parallelism.get("active_ids", []),
        "terminal_branch_ids": sorted(terminal_statuses),
        "terminal_branch_statuses": {
            branch_id: terminal_statuses[branch_id] for branch_id in sorted(terminal_statuses)
        },
        "packet_path": None,
        "proposal_path": None,
        "validation_path": None,
        "accepted_path": None,
    }
    write_json(decision_path, data)
    return amendment_records(manifest_path.parent)


def choose_status(audit: str, branches: list[dict], expected_branch_count: int, blockers: list[str]) -> str:
    if audit == "failed":
        return "failed"
    if audit in {"blocked", "missing"}:
        return "blocked"
    complete_branch_set = len(branches) == expected_branch_count
    by_id = {str(item["branch_id"]): item for item in branches if isinstance(item.get("branch_id"), str)}
    all_branch_pass = complete_branch_set and all(branch_is_complete_or_recovered(item, by_id) for item in branches)
    if all_branch_pass and not blockers:
        return "pass"
    if branches:
        return "partial"
    return "blocked"


def aggregate_review_status(branch_statuses: list[dict], expected_branch_count: int) -> str:
    if not branch_statuses:
        return "missing"
    if len(branch_statuses) < expected_branch_count:
        return "missing"
    by_id = {str(item["branch_id"]): item for item in branch_statuses if isinstance(item.get("branch_id"), str)}
    review_statuses = {
        "mergeable" if branch_is_complete_or_recovered(item, by_id) else str(item.get("review_status", "missing"))
        for item in branch_statuses
    }
    if review_statuses == {"mergeable"}:
        return "mergeable"
    if "failed" in review_statuses or "blocked" in review_statuses:
        return "blocked"
    return "missing"


def assemble(manifest_path: Path, *, out_path: Path, write_decision: bool, summary_text: str | None) -> dict:
    manifest = load_json(manifest_path)
    branches = branch_entries(manifest)
    blockers: list[str] = []
    audit = audit_status(manifest_path.parent, blockers)
    branch_statuses = branch_summaries(manifest_path.parent, branches, blockers)
    status = choose_status(audit, branch_statuses, len(branches), blockers)
    branch_parallelism = scheduler_rollup(manifest_path, manifest, branches, status=status, blockers=blockers)
    status = choose_status(audit, branch_statuses, len(branches), blockers)
    amendments = ensure_skip_decision(
        manifest_path,
        manifest,
        branch_statuses,
        branch_parallelism,
        allow_write=write_decision,
    )
    if branch_statuses and not amendments:
        blockers.append("amendment launch-or-skip decision artifact is missing for terminal branch checkpoints")
        status = "partial" if status == "pass" else status
    dod = [f"prompt audit status: {audit}"]
    dod.extend(
        f"branch {item['branch_id']} status: {item['status']} review_status: {item['review_status']}"
        for item in branch_statuses
    )
    if not branch_statuses:
        dod.append("no branch status artifacts were available")
    review_status = aggregate_review_status(branch_statuses, len(branches))
    data = {
        "job_id": manifest.get("job_id"),
        "status": status,
        "schema_status": "assembled",
        "runtime_status": status,
        "dod_status": "pass" if status == "pass" and not blockers else "incomplete",
        "review_status": review_status,
        "artifact_valid": True,
        "runtime_success": status == "pass",
        "dod_complete": status == "pass" and not blockers,
        "review_complete": review_status == "mergeable",
        "resume_action": "reuse_terminal_status" if status == "pass" and not blockers else "resume_or_repair",
        "audit_status": audit,
        "branch_parallelism": branch_parallelism,
        "branch_statuses": branch_statuses,
        "amendment_decisions": amendments,
        "lite_advice": [],
        "cost_summary_path": "telemetry.summary.json#cost_summary",
        "commands_run": [
            f"python3 {Path(__file__).resolve().as_posix()} --manifest {manifest_path.as_posix()} --out {out_path.as_posix()}"
        ],
        "dod_checklist": dod,
        "blockers": sorted(dict.fromkeys(blockers)),
        "summary": summary_text
        or (
            f"Main status assembled deterministically as {status} from prompt audit, "
            f"{len(branch_statuses)} branch status artifact(s), scheduler ledger, and telemetry summary."
        ),
    }
    write_json(out_path, data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", help="Output path. Defaults to <bundle>/main.status.json.")
    parser.add_argument("--no-write-amendment-decision", action="store_true")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--summary")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    out_path = (
        resolve_absolute_path(args.out, "--out", must_exist=False)
        if args.out
        else manifest_path.parent / "main.status.json"
    )
    if out_path.exists() and not args.replace:
        raise SystemExit(f"main status already exists; pass --replace to recreate: {out_path}")
    data = assemble(
        manifest_path,
        out_path=out_path,
        write_decision=not args.no_write_amendment_decision,
        summary_text=args.summary,
    )
    result = {
        "status": data["status"],
        "status_path": out_path.as_posix(),
        "blockers": data["blockers"],
        "artifact_valid": data.get("artifact_valid"),
        "runtime_success": data.get("runtime_success"),
        "dod_complete": data.get("dod_complete"),
        "review_complete": data.get("review_complete"),
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']} path={result['status_path']}")
        for blocker in result["blockers"]:
            print(f"blocker: {blocker}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
