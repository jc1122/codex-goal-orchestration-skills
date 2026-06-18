#!/usr/bin/env python3
"""Validate a goal manifest amendment proposal without mutating the bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from amendment_lib import (
    add_lineage_stage,
    amendment_lineage_path,
    ensure_amendment_id,
    latest_lineage_sha,
    lineage_path_rel,
    load_lineage,
    resolve_absolute_path,
    sha256_text,
    validate_proposal,
    write_json,
    json_text,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--output")
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument("--no-infer-scheduler", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    proposal_path = resolve_absolute_path(args.proposal, "--proposal", must_exist=True)
    validation, _candidate, _brief = validate_proposal(
        manifest_path=manifest_path,
        proposal_path=proposal_path,
        active_branch_ids=args.active_branch,
        terminal_branch_ids=args.terminal_branch,
        infer_scheduler=not args.no_infer_scheduler,
        run_lint=True,
    )
    proposed_amendment_id = (
        validation.get("amendment_id")
        if isinstance(validation.get("amendment_id"), str) and validation.get("amendment_id").strip()
        # Use the leading id segment of the proposal filename (A1.proposal.json -> A1), not Path.stem,
        # which yields the double-extension "A1.proposal" that ensure_amendment_id always rejects.
        else proposal_path.name.split(".")[0]
    )
    try:
        amendment_id = ensure_amendment_id(proposed_amendment_id)
    except (ValueError, SystemExit):
        # The proposal's amendment_id is malformed; validate_proposal already recorded the structured
        # defect (status != "pass"). Emit that failed validation result instead of crashing and writing
        # no artifact at all (the fail-closed convention requires a clean failed result, and a valid
        # amendment_id is needed to build the lineage path below). ensure_amendment_id raises ValueError
        # for an empty/non-string id and SystemExit (via require_safe_id) for a malformed non-empty id.
        if args.output:
            write_json(resolve_absolute_path(args.output, "--output", must_exist=False), validation)
        if args.json or not args.output:
            print(json.dumps(validation, indent=2, sort_keys=True))
        else:
            print(args.output)
        return 0 if validation["status"] == "pass" else 1
    lineage_path = amendment_lineage_path(manifest_path.parent, amendment_id)
    lineage = load_lineage(lineage_path, amendment_id=amendment_id)
    parent_sha = latest_lineage_sha(lineage)
    proposal_rel = lineage_path_rel(manifest_path.parent, proposal_path)
    add_lineage_stage(
        lineage,
        stage="final_proposal",
        path=proposal_rel,
        sha256=validation.get("proposal_sha256", "sha256:"),
        parent_sha256=parent_sha,
    )
    validation_path = (
        Path(args.output) if args.output else (manifest_path.parent / "amendments" / f"{amendment_id}.validation.json")
    )
    validation_rel = lineage_path_rel(manifest_path.parent, validation_path)
    validation["lineage_path"] = lineage_path.as_posix()
    validation["lineage_stages"] = list(lineage.get("stages", [])) if isinstance(lineage.get("stages"), list) else []
    validation_sha256 = sha256_text(json_text(validation))
    add_lineage_stage(
        lineage,
        stage="validation",
        path=validation_rel,
        sha256=validation_sha256,
        parent_sha256=latest_lineage_sha(lineage),
    )
    if args.output:
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False)
        write_json(output_path, validation)
        lineage["artifact"] = output_path.as_posix()
        write_json(lineage_path, lineage)
    if args.json or not args.output:
        print(json.dumps(validation, indent=2, sort_keys=True))
    else:
        print(args.output)
    return 0 if validation["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
