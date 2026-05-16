# {title}

Job id: {job_id}
Base ref: {base_ref}

## Goal

{goal}

## Source Summary

{source_summary}

## Runtime Rules

- Use $goal-main-orchestrator.
- Run prompt audit first.
- Do not create branch worktrees until prompt audit passes.
- Respect max_active_branch_agents=5.
- Run branch waves sequentially.
- Close finished branch orchestrator agents before launching replacements.
- Do not exceed 5 active branch orchestrator agents.
- Preserve unsupported, unresolved, negative, and probe-only labels.

## Branch Waves

{branch_waves}

## Merge Policy

{merge_policy}

## Cleanup Policy

{cleanup_policy}

## Required Evidence

{required_evidence}

## Definition of Done

{final_dod}
