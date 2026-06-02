# {title}

Job id: {job_id}
Base ref: {base_ref}

## Goal

{goal}

## Source Summary

{source_summary}

## Source Attachments

{source_attachments}

## Runtime Cap

{runtime_cap}

## Runtime Readiness Gate

{runtime_readiness_gate}

## Runtime Rules

- Use $goal-main-orchestrator. Treat `job.manifest.json` as the contract and run `runtime_phase_manifest.py --markdown`; do not read skill Python source unless debugging a failed script.
- If the runtime readiness gate is blocked, do not launch `/goal` from this prompt; use `goal-bootloader.md` and `readiness.json` corrective commands first.
- Treat manifest paths as bundle-root relative and worktree paths as repo-root relative. Reject absolute paths, backslashes, and `..` traversal.
- Run availability bootstrap and fresh model-catalog capture before prompt audit. Do not create branches until `prompt-audit.json` pins this manifest and repository root with `status=pass`.
- Run `script_only_repair_gate.py --scope main` before prompt audit or branch launch. Complete any `script_actions_needed` commands first; launch a model only after the gate returns `pass_no_actions`.
- Parallelism is the default. Respect max_active_branch_agents={max_active_branch_agents}; never exceed 4. Saturate branch orchestrator slots and close finished branch orchestrator agents. Set absolute bundle paths and run:

```bash
B="/absolute/path/to/bundle"
MANIFEST="$B/job.manifest.json"
AUDIT="$B/audit/prompt-audit.json"

python3 "${{GOAL_SKILLS_ROOT}}"/goal-main-orchestrator/scripts/render_branch_worktree_commands.py --manifest "$MANIFEST" --repo-root /absolute/path/to/repo --audit "$AUDIT" --list-ready --limit 4
```

Launch the next eligible branch from that output only.
- Defer only unresolved manifest `depends_on` entries. Treat waves as scheduling/order groups, not barriers; non-pass dependencies require structured `dependency_failed` evidence.
- Record the scheduler ledger at `{main_scheduler_path}` with schema v2 events. `branch_parallelism.scheduler_path` in `main.status.json` must be `{main_scheduler_path}`.
- If no branch completes after `orchestration_watchdog.main_no_completion_wait_limit` consecutive waits, inspect only native agent/process state, close unreachable or stale active branches with `scheduler_tick.py --blocked/--close --reason-code stale_active|native_agent_unreachable|timeout`, then refill eligible capacity.
- Outside that watchdog exception, wait for branch agents and do not poll active branch worktrees, worker packets, reviewer packets, process tables, or status files.
- Branch sessions must launch workers as a rolling saturated pool up to each branch cap. Research-worker, reviewer, Lite, and amendment policy lives in `job.manifest.json` and packet validators.
- Before merge readiness, require `git diff --check {base_ref}...HEAD`; accept each branch after `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/Bxx.status.json`.
- Before final pass, run `summarize_telemetry.py --bundle-dir /absolute/path/to/bundle` and require current `telemetry.summary.json` usage telemetry under the legacy `cost_summary` field name, `main.status.json` `cost_summary_path`, plus `validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/main.status.json`.
- Optional Lite advisors are context routers only, never audit/review/mergeability/DoD evidence. Preserve unsupported, unresolved, negative, and probe-only labels.
- Telemetry policy mode is `{telemetry_policy_mode}`. In debug mode, telemetry is passive only: collect extra runtime telemetry context but do not change model-route selection, selection reasons, polling windows, or watchdog behavior.

## Parallelization Rationale

{parallelization_rationale}

## Branch Waves

{branch_waves}

## Branch Dependencies

{branch_dependencies}

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
- Packet telemetry exists for prompt audit, workers, research-workers, reviewers, any Lite packets, and any plan-amender packets; each declared attempt records `timeout_seconds`; `telemetry.summary.json` was regenerated.
- `telemetry.summary.json` includes deterministic token/character/time usage fields under the legacy `cost_summary` field name for declared/called attempts, aliases, premium aliases avoided, mini/spark usage, prompt/output bytes, fallbacks, and failed same-class attempts; `main.status.json` points to it.
- `{main_scheduler_path}` exists, matches the current manifest hash, and proves branch slot saturation with schema v2 event metadata plus explicit refill/deferral/blocking evidence.
- Every branch status passed manifest-bound `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/Bxx.status.json`.
- Every terminal branch summary has an `amendment_decisions` launch or skip record; every launched amender has passing packet validation.
- Every mergeable review recorded base-range whitespace evidence and no verification gaps.
- Final `main.status.json` passed manifest-bound `validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/main.status.json`.
- `lite_advice` records are present, even when empty; every relevant main Lite packet directory is recorded, validated, and treated only as advisory context routing.
{final_dod}
