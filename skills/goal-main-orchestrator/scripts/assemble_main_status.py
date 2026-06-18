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


def _load_main_validator():
    path = Path(__file__).resolve().parent / "validate_main_status.py"
    if not path.exists():
        raise SystemExit(f"missing main status validator: {path}")
    spec = importlib.util.spec_from_file_location("goal_main_validate_main_status", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load main status validator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
STATUS_VALIDATION = _load_status_validation()
MAIN_VALIDATOR = _load_main_validator()
STATUSES = set(CONTRACT.STATUSES)
REVIEW_STATUSES = set(CONTRACT.REVIEW_STATUSES)

resolve_absolute_path = STATUS_VALIDATION.resolve_absolute_path
load_json = STATUS_VALIDATION.load_json
sha256_file = STATUS_VALIDATION.sha256_file


def _nonempty_str_list(value: object) -> list[str]:
    """A semi-trusted artifact's id list (e.g. branch_parallelism.active_ids) may be a present
    non-list; iterate as empty rather than TypeError."""
    return [item for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_load_object(path: Path, blockers: list[str], label: str) -> dict:
    """Load a semi-trusted runtime artifact as an object.

    Branch/audit/amendment/scheduler artifacts are produced by branch agents and CLI
    workers, so a malformed or non-object file must degrade to a conservative blocker
    rather than crash this assembler with an unhandled traceback.
    """
    try:
        data = load_json(path)
    except Exception as exc:  # noqa: BLE001 - tolerant read: any parse/IO failure is a blocker
        blockers.append(f"{label} is not readable JSON at {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        blockers.append(f"{label} must be a JSON object at {path}")
        return {}
    return data


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
    data = safe_load_object(path, blockers, "prompt audit artifact")
    status = data.get("status")
    if not isinstance(status, str) or status not in {"pass", "failed", "blocked"}:
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
        status_data = safe_load_object(status_path, blockers, f"branch {branch_id} status artifact")
        status_value = status_data.get("status")
        review_status = status_data.get("review_status", "missing")
        if not isinstance(status_value, str) or status_value not in STATUSES:
            blockers.append(f"branch {branch_id} status artifact has invalid status: {status_value!r}")
            status_value = "failed"
        if not isinstance(review_status, str) or review_status not in REVIEW_STATUSES:
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
        status_data = (
            safe_load_object(bundle_dir / status_rel, blockers, f"branch {branch_id} status artifact")
            if status_rel and (bundle_dir / status_rel).exists()
            else {}
        )
        raw_blockers = status_data.get("blockers")
        for item in raw_blockers if isinstance(raw_blockers, list) else []:
            if isinstance(item, str) and item.strip():
                blockers.append(f"{branch_id}: {item.strip()}")
        if not status_data.get("blockers"):
            blockers.append(f"branch {branch_id} ended {summary.get('status')}")
    return summaries


def scheduler_rollup(
    manifest_path: Path, manifest: dict, branches: list[dict], *, status: str, blockers: list[str]
) -> dict:
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
        # Accept the pre-amendment (archived) manifest sha like validate_main_scheduler does;
        # otherwise a legitimately-passing post-amendment run is downgraded to "partial" by a
        # spurious "manifest_sha256 must match current job.manifest.json" blocker the validator
        # (using archived shas) never raises.
        allowed_manifest_sha256s=STATUS_VALIDATION.archived_manifest_sha256s(manifest_path),
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


def _terminal_statuses(branch_statuses: list[dict]) -> dict[str, str]:
    return {
        str(item["branch_id"]): str(item["status"])
        for item in branch_statuses
        if item.get("status") in STATUSES and isinstance(item.get("branch_id"), str)
    }


def current_amendment_records(
    manifest_path: Path,
    branch_statuses: list[dict],
    branch_parallelism: dict,
    blockers: list[str],
) -> tuple[list[dict], set[str], list[str]]:
    amendments_dir = manifest_path.parent / "amendments"
    if not amendments_dir.is_dir():
        return [], set(), []
    terminal_statuses = _terminal_statuses(branch_statuses)
    active_ids = set(_nonempty_str_list(branch_parallelism.get("active_ids")))
    # Accept the pre-amendment (archived) manifest sha like the scheduler rollup below does;
    # a launch decision records the sha at decision time, but apply_manifest_amendment then
    # rewrites the live manifest (new sha) without refreshing the decision, so a strict
    # current-sha match would drop a legitimate decision and block main `pass` post-amendment.
    allowed_manifest_shas = STATUS_VALIDATION.archived_manifest_sha256s(manifest_path)
    records = []
    covered_branch_ids: set[str] = set()
    ignored: list[str] = []
    for path in sorted(amendments_dir.glob("*.decision.json")):
        data = safe_load_object(path, blockers, f"amendment decision {path.name}")
        amendment_id = data.get("amendment_id")
        decision = data.get("decision")
        if not isinstance(amendment_id, str) or decision not in CONTRACT.AMENDMENT_DECISIONS:
            continue
        terminal_ids = (
            [item for item in data.get("terminal_branch_ids", []) if isinstance(item, str) and item.strip()]
            if isinstance(data.get("terminal_branch_ids"), list)
            else []
        )
        reasons: list[str] = []
        if data.get("manifest") != manifest_path.as_posix():
            reasons.append("manifest path mismatch")
        manifest_sha = data.get("manifest_sha256")
        if not isinstance(manifest_sha, str) or manifest_sha not in allowed_manifest_shas:
            reasons.append("manifest sha256 mismatch")
        overlap = sorted(active_ids & set(terminal_ids))
        if overlap:
            reasons.append("active_branch_ids overlaps terminal_branch_ids: " + ", ".join(overlap))
        missing = sorted(set(terminal_ids) - set(terminal_statuses))
        if missing:
            reasons.append("terminal_branch_ids not present in current branch summaries: " + ", ".join(missing))
        decision_statuses = data.get("terminal_branch_statuses")
        if isinstance(decision_statuses, dict):
            drift = sorted(
                branch_id
                for branch_id in terminal_ids
                if branch_id in terminal_statuses and decision_statuses.get(branch_id) != terminal_statuses[branch_id]
            )
            if drift:
                reasons.append("terminal_branch_statuses drifted for: " + ", ".join(drift))
        else:
            reasons.append("terminal_branch_statuses missing or invalid")
        if reasons:
            ignored.append(f"{path.relative_to(manifest_path.parent).as_posix()}: " + "; ".join(reasons))
            continue
        covered_branch_ids.update(terminal_ids)
        records.append(
            {
                "amendment_id": amendment_id,
                "decision": decision,
                "decision_path": path.relative_to(manifest_path.parent).as_posix(),
                "packet_validation_path": (
                    f"amendments/{amendment_id}.packet/packet.validation.json" if decision == "launch" else None
                ),
            }
        )
    for item in ignored:
        blockers.append("ignored stale amendment decision artifact: " + item)
    return records, covered_branch_ids, ignored


def ensure_skip_decision(
    manifest_path: Path,
    manifest: dict,
    branch_statuses: list[dict],
    branch_parallelism: dict,
    *,
    allow_write: bool,
) -> tuple[list[dict], set[str], list[str]]:
    blockers: list[str] = []
    existing, covered, ignored = current_amendment_records(manifest_path, branch_statuses, branch_parallelism, blockers)
    if existing or not branch_statuses:
        return existing, covered, ignored
    if not allow_write:
        return [], set(), ignored
    terminal_statuses = _terminal_statuses(branch_statuses)
    if not terminal_statuses:
        return [], set(), ignored
    amendment_id = "A000"
    decision = "skip"
    all_pass = all(value == "pass" for value in terminal_statuses.values())
    if not all_pass:
        return [], set(), ignored
    # Past the guard above, every terminal status is pass; non-pass terminals are routed to
    # amendment_decision_blockers instead of an auto-written skip decision.
    reason_code = "no_adaptation_needed"
    reason = "All terminal branch checkpoints are pass; no future-work amendment is needed."
    amendments_dir = manifest_path.parent / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    events_path = manifest_path.parent / CONTRACT.MAIN_SCHEDULER_PATH
    scheduler_event_seq = None
    if events_path.exists():
        events = safe_load_object(events_path, blockers, "main scheduler ledger").get("events")
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
        "active_branch_ids": [
            *_nonempty_str_list(branch_parallelism.get("active_ids")),
        ],
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
    return current_amendment_records(manifest_path, branch_statuses, branch_parallelism, blockers)


def amendment_decision_blockers(
    branch_statuses: list[dict],
    branch_parallelism: dict,
    covered_branch_ids: set[str],
    ignored_decisions: list[str],
) -> list[dict]:
    terminal_statuses = _terminal_statuses(branch_statuses)
    if not terminal_statuses:
        return []
    missing = sorted(set(terminal_statuses) - covered_branch_ids)
    if not missing:
        return []
    if all(terminal_statuses[branch_id] == "pass" for branch_id in missing):
        return []
    evidence_paths = [
        str(item.get("status_path"))
        for item in branch_statuses
        if isinstance(item, dict) and item.get("branch_id") in missing and isinstance(item.get("status_path"), str)
    ]
    return [
        {
            "schema_version": 1,
            "reason_code": "amendment_creation_blocked",
            "reason": (
                "Terminal branch checkpoints require amendment launch-or-skip handling, but no current valid "
                "decision artifact is available for these checkpoints. Preserve this as blocked evidence "
                "instead of reusing stale amendment decisions."
            ),
            "active_branch_ids": _nonempty_str_list(branch_parallelism.get("active_ids")),
            "terminal_branch_ids": missing,
            "terminal_branch_statuses": {branch_id: terminal_statuses[branch_id] for branch_id in missing},
            "evidence_paths": evidence_paths,
            "ignored_decision_artifacts": ignored_decisions,
        }
    ]


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


def self_validation_blocking_defects(defects: list[str]) -> list[str]:
    deferred_prefix = "$.telemetry_summary"
    return [
        item
        for item in defects
        if not (item.startswith(deferred_prefix) and "telemetry summary does not exist" in item)
    ]


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
    # A "reject" verdict is a hard-negative review and must roll up as blocked (it previously
    # fell through to "missing", i.e. was reported as merely unreviewed). "mergeable_after_fixes"
    # is a valid REVIEW_STATUSES value (accepted by both validators) that is likewise not cleanly
    # mergeable; it had the same "missing" fall-through, so roll it up with the non-mergeable states.
    if review_statuses & {"failed", "blocked", "reject", "mergeable_after_fixes"}:
        return "blocked"
    return "missing"


def assemble(manifest_path: Path, *, out_path: Path, write_decision: bool, summary_text: str | None) -> dict:
    try:
        manifest = load_json(manifest_path)
    except Exception as exc:  # noqa: BLE001 - manifest is the trusted input contract: fail clean, not traceback
        raise SystemExit(f"manifest is not readable JSON: {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SystemExit(f"manifest must be a JSON object: {manifest_path}")
    branches = branch_entries(manifest)
    blockers: list[str] = []
    audit = audit_status(manifest_path.parent, blockers)
    branch_statuses = branch_summaries(manifest_path.parent, branches, blockers)
    status = choose_status(audit, branch_statuses, len(branches), blockers)
    branch_parallelism = scheduler_rollup(manifest_path, manifest, branches, status=status, blockers=blockers)
    status = choose_status(audit, branch_statuses, len(branches), blockers)
    amendments, covered_amendment_branches, ignored_amendments = ensure_skip_decision(
        manifest_path,
        manifest,
        branch_statuses,
        branch_parallelism,
        allow_write=write_decision,
    )
    amendment_blockers = amendment_decision_blockers(
        branch_statuses,
        branch_parallelism,
        covered_amendment_branches,
        ignored_amendments,
    )
    if amendment_blockers:
        blockers.extend(
            "amendment decision blocked for terminal branches "
            + ", ".join(item["terminal_branch_ids"])
            + ": "
            + item["reason"]
            for item in amendment_blockers
        )
    elif branch_statuses and not amendments:
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
        "amendment_decision_blockers": amendment_blockers,
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
    validation_defects = MAIN_VALIDATOR.validate_main_status(
        data,
        job_id=None,
        manifest=manifest,
        manifest_path=manifest_path,
    )
    blocking_validation_defects = self_validation_blocking_defects(validation_defects)
    if validation_defects and not blocking_validation_defects:
        data["deferred_validation_defects"] = validation_defects
        write_json(out_path, data)
    if blocking_validation_defects:
        data["status"] = "blocked" if data.get("status") != "failed" else "failed"
        data["schema_status"] = "failed"
        data["runtime_status"] = data["status"]
        data["dod_status"] = "incomplete"
        data["artifact_valid"] = False
        data["runtime_success"] = False
        data["dod_complete"] = False
        data["resume_action"] = "resume_or_repair"
        existing_blockers = [item for item in data.get("blockers", []) if isinstance(item, str)]
        validation_blockers = [f"main status validation: {item}" for item in blocking_validation_defects]
        data["blockers"] = sorted(dict.fromkeys([*existing_blockers, *validation_blockers]))
        data["validation_defects"] = blocking_validation_defects
        if len(blocking_validation_defects) != len(validation_defects):
            data["deferred_validation_defects"] = [
                item for item in validation_defects if item not in set(blocking_validation_defects)
            ]
        data["summary"] = (
            summary_text
            or "Main status assembly found invalid terminal evidence and preserved validator defects for repair."
        )
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
