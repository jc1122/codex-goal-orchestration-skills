#!/usr/bin/env python3
"""Apply a validated goal manifest amendment."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from amendment_lib import (
    CONTRACT,
    PREFLIGHT,
    add_lineage_stage,
    amendment_lineage_path,
    enrich_brief_runtime_metadata,
    ensure_amendment_id,
    latest_lineage_sha,
    lineage_path_rel,
    load_lineage,
    load_json_object,
    prompt_regeneration_branch_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    validate_proposal,
    write_json,
    write_runtime_index,
)
from validate_amender_packet import validate_packet
import contextlib


def atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp_name = handle.name
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_name, path)


def branch_prompt_paths(bundle_dir: Path, candidate: dict, branch_ids: set[str]) -> list[Path]:
    paths: list[Path] = []
    for branch in candidate.get("branches", []):
        if not isinstance(branch, dict) or branch.get("id") not in branch_ids:
            continue
        prompt = branch.get("prompt")
        if not isinstance(prompt, str) or relative_path_defect(prompt, "prompt"):
            continue
        paths.append(bundle_dir / prompt)
    return paths


def restore_prompt_backups(backups: dict[Path, str | None]) -> None:
    for path, content in backups.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def mark_preflight_report_initial_epoch(bundle_dir: Path, amendment_id: str, manifest_sha256: str) -> None:
    report_path = bundle_dir / "PREFLIGHT_REPORT.md"
    if not report_path.exists():
        return
    text = report_path.read_text(encoding="utf-8")
    marker = f"Accepted amendment: {amendment_id}"
    if marker in text and "initial_epoch_only" in text:
        return
    notice = "\n".join(
        [
            "",
            "## Runtime Amendment Notice",
            "",
            "Status: initial_epoch_only",
            f"Accepted amendment: {amendment_id}",
            f"Current manifest sha256: {manifest_sha256}",
            "This preflight report describes the initial bundle topology. Use job.manifest.json, runtime.index.json, amendment accepted records, and current scheduler/status artifacts for amended runtime topology.",
            "",
        ]
    )
    report_path.write_text(text.rstrip() + "\n" + notice, encoding="utf-8")


def require_launch_packet_validation(manifest_path: Path, proposal_path: Path, amendment_id: str) -> None:
    amendments_dir = manifest_path.parent / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    packet_dir = amendments_dir / f"{amendment_id}.packet"
    packet_validation_path = packet_dir / "packet.validation.json"
    if not decision_path.exists():
        raise SystemExit(f"missing amendment launch decision artifact: {decision_path}")
    decision = load_json_object(decision_path)
    if decision.get("decision") != "launch":
        raise SystemExit("refusing to apply amendment because its decision artifact is not a launch decision")
    if decision.get("reason_code") not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("refusing to apply amendment because its launch decision reason_code is invalid")
    if not packet_validation_path.exists():
        raise SystemExit(f"missing route-bound amender packet validation artifact: {packet_validation_path}")
    packet_validation = load_json_object(packet_validation_path)
    if packet_validation.get("status") != "pass":
        raise SystemExit("refusing to apply amendment because amender packet validation status is not pass")
    expected = {
        "manifest": manifest_path.as_posix(),
        "proposal": proposal_path.as_posix(),
        "packet_dir": packet_dir.as_posix(),
        "decision": decision_path.as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
        "proposal_sha256": sha256_file(proposal_path),
    }
    for key, value in expected.items():
        if packet_validation.get(key) != value:
            raise SystemExit(f"amender packet validation {key} does not match current launch state")
    if packet_validation.get("defects"):
        raise SystemExit("refusing to apply amendment because amender packet validation defects are not empty")
    fresh_packet_validation = validate_packet(
        manifest_path=manifest_path,
        amendment_id=amendment_id,
        packet_dir=packet_dir,
    )
    if fresh_packet_validation.get("status") != "pass":
        defects = fresh_packet_validation.get("defects")
        detail = (
            "; ".join(str(item) for item in defects)
            if isinstance(defects, list)
            else "unknown packet validation defect"
        )
        raise SystemExit(f"fresh amender packet validation failed; live manifest was not changed: {detail}")
    for key in ["manifest_sha256", "proposal_sha256", "packet_dir", "decision", "route", "telemetry", "proposal"]:
        if packet_validation.get(key) != fresh_packet_validation.get(key):
            raise SystemExit(f"recorded amender packet validation is stale for {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--validation", required=True)
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    proposal_path = resolve_absolute_path(args.proposal, "--proposal", must_exist=True)
    validation_path = resolve_absolute_path(args.validation, "--validation", must_exist=True)
    validation = load_json_object(validation_path)
    if validation.get("status") != "pass":
        raise SystemExit("refusing to apply amendment because validation status is not pass")
    if validation.get("manifest") != manifest_path.as_posix():
        raise SystemExit("validation manifest path does not match --manifest")
    if validation.get("proposal") != proposal_path.as_posix():
        raise SystemExit("validation proposal path does not match --proposal")
    if validation.get("manifest_sha256_before") != sha256_file(manifest_path):
        raise SystemExit("live manifest sha256 does not match validation manifest_sha256_before")
    if validation.get("proposal_sha256") != sha256_file(proposal_path):
        raise SystemExit("proposal sha256 does not match validation proposal_sha256")

    amendment_id = ensure_amendment_id(validation.get("amendment_id"))
    bundle_dir = manifest_path.parent
    require_launch_packet_validation(manifest_path, proposal_path, amendment_id)

    active_ids = validation.get("active_branch_ids") if isinstance(validation.get("active_branch_ids"), list) else []
    terminal_ids = (
        validation.get("terminal_branch_ids") if isinstance(validation.get("terminal_branch_ids"), list) else []
    )
    fresh_validation, candidate, normalized_brief = validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=[str(item) for item in active_ids],
        terminal_branch_ids=[str(item) for item in terminal_ids],
        infer_scheduler=True,
        run_lint=True,
    )
    if fresh_validation.get("status") != "pass" or candidate is None or normalized_brief is None:
        write_json(validation_path, fresh_validation)
        raise SystemExit("fresh amendment validation failed; live manifest was not changed")

    lineage_path = amendment_lineage_path(bundle_dir, amendment_id)
    lineage = load_lineage(lineage_path, amendment_id=amendment_id)
    parent_sha = latest_lineage_sha(lineage)
    add_lineage_stage(
        lineage,
        stage="final_proposal",
        path=lineage_path_rel(bundle_dir, proposal_path),
        sha256=validation.get("proposal_sha256", fresh_validation.get("proposal_sha256", "sha256:")),
        parent_sha256=parent_sha,
    )

    amendments_dir = bundle_dir / "amendments"
    archive_path = amendments_dir / f"{amendment_id}.job.manifest.before.json"
    accepted_path = amendments_dir / f"{amendment_id}.accepted.json"
    if accepted_path.exists():
        raise SystemExit(f"accepted amendment artifact already exists: {accepted_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    protected_branch_ids = set(
        str(item) for item in fresh_validation.get("protected_branch_ids", []) if isinstance(item, str)
    )
    regenerated_branch_ids = set(prompt_regeneration_branch_ids(candidate, protected_branch_ids))
    prompt_backups = {
        path: path.read_text(encoding="utf-8") if path.exists() else None
        for path in branch_prompt_paths(bundle_dir, candidate, regenerated_branch_ids)
    }
    report_path = bundle_dir / "PREFLIGHT_REPORT.md"
    report_backup = report_path.read_text(encoding="utf-8") if report_path.exists() else None
    runtime_index_path = bundle_dir / "runtime.index.json"
    runtime_index_backup = runtime_index_path.read_text(encoding="utf-8") if runtime_index_path.exists() else None
    try:
        atomic_write_json(manifest_path, candidate)
        write_runtime_index(bundle_dir, candidate)
        prompt_brief = enrich_brief_runtime_metadata(normalized_brief, candidate, bundle_dir=bundle_dir)
        PREFLIGHT.write_bundle_prompts(prompt_brief, bundle_dir, branch_ids=regenerated_branch_ids, write_main=False)
        lint = PREFLIGHT.lint_bundle(bundle_dir, write_output=True)
        if lint.get("status") != "pass":
            raise SystemExit("amended manifest failed lint and was restored")
        if fresh_validation.get("candidate_manifest_sha256") != sha256_file(manifest_path):
            raise SystemExit("written manifest sha256 does not match validated candidate manifest")
        mark_preflight_report_initial_epoch(bundle_dir, amendment_id, sha256_file(manifest_path))
    except BaseException:
        if archive_path.exists():
            manifest_path.write_text(archive_path.read_text(encoding="utf-8"), encoding="utf-8")
        restore_prompt_backups(prompt_backups)
        if report_backup is None:
            with contextlib.suppress(FileNotFoundError):
                report_path.unlink()
        else:
            report_path.write_text(report_backup, encoding="utf-8")
        if runtime_index_backup is None:
            with contextlib.suppress(FileNotFoundError):
                runtime_index_path.unlink()
        else:
            runtime_index_path.write_text(runtime_index_backup, encoding="utf-8")
        raise
    manifest_sha256_after = sha256_file(manifest_path)
    add_lineage_stage(
        lineage,
        stage="manifest_before",
        path=lineage_path_rel(bundle_dir, archive_path),
        sha256=fresh_validation["manifest_sha256_before"],
        parent_sha256=latest_lineage_sha(lineage),
    )
    add_lineage_stage(
        lineage,
        stage="manifest_after",
        path=lineage_path_rel(bundle_dir, manifest_path),
        sha256=manifest_sha256_after,
        parent_sha256=latest_lineage_sha(lineage),
    )

    accepted = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "status": "accepted",
        "manifest": manifest_path.as_posix(),
        "proposal": proposal_path.as_posix(),
        "validation": validation_path.as_posix(),
        "archived_manifest": archive_path.as_posix(),
        "manifest_sha256_before": fresh_validation["manifest_sha256_before"],
        "manifest_sha256_after": sha256_file(manifest_path),
        "candidate_manifest_sha256": fresh_validation["candidate_manifest_sha256"],
        "proposal_sha256": fresh_validation["proposal_sha256"],
        "changed_branch_ids": fresh_validation["changed_branch_ids"],
        "regenerated_prompts": [
            branch["prompt"]
            for branch in candidate.get("branches", [])
            if isinstance(branch, dict)
            and branch.get("id") in regenerated_branch_ids
            and isinstance(branch.get("prompt"), str)
        ],
        "lint_status": lint.get("status"),
        "lineage_path": lineage_path.as_posix(),
    }
    write_json(accepted_path, accepted)
    add_lineage_stage(
        lineage,
        stage="acceptance",
        path=lineage_path_rel(bundle_dir, accepted_path),
        sha256=sha256_file(accepted_path),
        parent_sha256=latest_lineage_sha(lineage),
    )
    write_json(lineage_path, lineage)
    print(accepted_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
