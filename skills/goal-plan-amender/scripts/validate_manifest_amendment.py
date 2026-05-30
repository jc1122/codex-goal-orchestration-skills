#!/usr/bin/env python3
"""Validate a goal manifest amendment proposal without mutating the bundle."""

from __future__ import annotations

import argparse
import json

from amendment_lib import resolve_absolute_path, validate_proposal, write_json


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
    if args.output:
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False)
        write_json(output_path, validation)
    if args.json or not args.output:
        print(json.dumps(validation, indent=2, sort_keys=True))
    else:
        print(args.output)
    return 0 if validation["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
