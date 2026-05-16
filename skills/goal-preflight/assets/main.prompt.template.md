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
- Close finished branch orchestrator agents before launching replacements.
- Do not exceed 4 active branch orchestrator agents.
- Do not read `goal-branch-orchestrator/SKILL.md` in main context; dispatch branch sessions that use that skill.
- Require each branch to record `git diff --check {base_ref}...HEAD` before merge readiness.
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
{final_dod}
