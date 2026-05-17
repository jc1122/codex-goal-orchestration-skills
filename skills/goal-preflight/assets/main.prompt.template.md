# {title}

Job id: {job_id}
Base ref: {base_ref}

## Goal

{goal}

## Source Summary

{source_summary}

## Runtime Rules

- Use $goal-main-orchestrator.
- Treat manifest paths as relative to the bundle root and worktree paths as relative to the repository root.
- Reject absolute paths, backslashes, and `..` traversal in manifest-owned paths.
- Run skill availability bootstrap before prompt audit.
- Run prompt audit before branch work.
- Do not create branch worktrees until prompt audit passes and `prompt-audit.json` pins this manifest and repository root.
- Parallelism is the default; serialization must be justified in `job.manifest.json`.
- Respect max_active_branch_agents={max_active_branch_agents}; it must never exceed 4.
- Launch all branches in each wave concurrently up to max_active_branch_agents, then run waves sequentially.
- After branch dispatch, wait for branch agents; do not poll active branch worktrees, worker packets, reviewer packets, process tables, or status files.
- Close finished branch orchestrator agents before launching replacements.
- Do not exceed 4 active branch orchestrator agents.
- Do not read `goal-branch-orchestrator/SKILL.md` in main context; dispatch branch sessions that use that skill.
- Require each branch to record `git diff --check {base_ref}...HEAD` before merge readiness.
- Require every branch status to pass `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json` before accepting it.
- Require final `main.status.json` to pass `validate_main_status.py --manifest /absolute/path/to/job.manifest.json`.
- Main `pass` requires `audit_status: "pass"`, exactly the manifest branch summary set with manifest-matching status/review paths, every branch summary `status: "pass"`, passing branch summaries with `review_status: "mergeable"`, manifest-owned worker artifacts and same-branch reviewer artifacts, exact base-range whitespace command evidence from `git diff --check {base_ref}...HEAD`, no mergeable reviewer verification gaps, DoD evidence, `lite_advice` audit records, and no blockers.
- Optional Lite advisors are context routers only. Do not launch Lite before prompt audit except for an audit-defect summary after a failed/blocked audit. Validated Lite advice may guide targeted original reads, but it is not audit, review, mergeability, or DoD evidence. Record `lite_advice: []` when no Lite packet was used; otherwise record each packet with purpose, status, disposition, manifest-owned advice/input paths, source hashes, validation command, validation status, validation defects, and reason.
- Preserve unsupported, unresolved, negative, and probe-only labels.

## Parallelization Rationale

{parallelization_rationale}

## Branch Waves

{branch_waves}

## Merge Policy

{merge_policy}

## Cleanup Policy

{cleanup_policy}

## Artifact Policy

{artifact_policy}

## Required Evidence

{required_evidence}

## Definition of Done

- Skill availability bootstrap passed for runtime skills before prompt audit.
- Every branch status passed manifest-bound `validate_branch_status.py`.
- Every mergeable review recorded base-range whitespace evidence and no verification gaps.
- Final `main.status.json` passed manifest-bound `validate_main_status.py`.
- `lite_advice` records are present, even when empty; every relevant main Lite packet directory is recorded, validated, and treated only as advisory context routing.
{final_dod}
