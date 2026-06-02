# {branch_id}: {title}

Branch id: {branch_id}
Base ref: {base_ref}
Branch name: {branch_name}
Worktree path: {worktree_path}
Wave: {wave}
Depends on branches:
{depends_on}
Max active worker packets: {max_active_worker_packets}
Max worker packets for this branch: 4

## Objective

{objective}

## Scope

{scope}

## Owned Paths

{owned_paths}

## Work Items

{work_items}

## Worker Parallelism

Use $goal-branch-orchestrator. Treat `job.manifest.json` as the policy source and run `runtime_phase_manifest.py --markdown`; do not read skill Python source unless debugging a failed script.

Before worker dispatch or reviewer dispatch, run `script_only_repair_gate.py --scope branch`. Complete any `script_actions_needed` commands first; launch workers or reviewers only after the gate returns `pass_no_actions` or reviewer reuse has been accepted with telemetry.

Cap: {max_active_worker_packets} active, 4 total; never exceed either. Launch ready workers as a rolling saturated pool with `render_worker_schedule.py --list-ready` before first launch and after each completion.

Worker scheduler ledger: {worker_scheduler_path}. `worker_parallelism.scheduler_path` in branch status must be `{worker_scheduler_path}`. Record ready/launch/finish/close/refill/defer/under_capacity/blocked evidence with scheduler scripts.

Worker parallelization rationale: {worker_parallelization_rationale}

Use each listed Worker packet id exactly once unless replacing a non-pass attempt with `create_runtime_packet.py --replace`. Worker, research-worker, reviewer, and Lite packets must write same-packet `telemetry.json`. Worker/research/reviewer launchers also write `launcher-state.json`; consume terminal packet artifacts and scheduler events, not silence. Outside the watchdog threshold exception below, wait for launchers and do not poll active worker/reviewer logs, process tables, or status files.

If no worker/reviewer completes after `orchestration_watchdog.branch_no_completion_wait_limit` consecutive waits, inspect only native agent/process state, close unreachable or stale active packets with `scheduler_tick.py --blocked/--close --reason-code stale_active|native_agent_unreachable|timeout`, then refill eligible capacity.

## Worker Model Routing

Default worker ladder: {default_worker_ladder}

Allowed worker route aliases: {allowed_worker_routes}

Worker route classes and route class reason are declared per work item in `job.manifest.json`. Use the declared class unless a more specific route is justified: mechanical/docs -> Codex mini; small-edit/normal-code -> Codex Spark then Codex mini; complex-code -> full ladder.

Selected worker ladders must be an ordered non-empty subsequence of the default ladder with a `selection_reason`; do not invent aliases or reorder providers.

Telemetry policy mode is `{telemetry_policy_mode}`. In debug mode, telemetry collection remains passive and does not alter route selection, polling cadence, or watchdog thresholds.

## Lite Advisors

Optional Lite Advisors are context routers only. Use them after bootstrap or completed packets, never while worker/research-worker/reviewer launchers are active, and never as pass/review/mergeability/DoD evidence.

## Tests And Validators

{tests}

## Reviewer Requirement

Before reviewer launch, create schema v2 `{pre_review_gate_path}` / `pre_review_gate.json` with passing checks, current `semantic_input_hashes`, route-policy freshness, and reuse eligibility. Dispatch a read-only reviewer only after the gate passes and reviewer reuse is not accepted. Pass requires mergeable review or accepted reviewer reuse with telemetry, matching hashes, same-branch reviewer artifact, `telemetry.json`, no verification gaps, and `git diff --check {base_ref}...HEAD`.

## Bootstrap Requirement

Before worker dispatch, run:

```bash
python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/check_goal_skill_availability.py --skills-root $GOAL_SKILLS_ROOT --require goal-branch-orchestrator --require-codex-cli && \
python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/check_model_catalog.py --json --require-codex > /absolute/path/to/bundle/branches/{branch_id}.model-catalog.json
```

Return blocked if either command fails.

## Stop Conditions

{stop_conditions}

## Definition of Done

- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- 1 to 4 worker packets were used for this branch; worker/research-worker/reviewer/Lite packets wrote same-packet `telemetry.json`.
- Independent worker packets launched as a rolling saturated pool up to max_active_worker_packets, or branch status records the serial/under-capacity reason.
- `{worker_scheduler_path}` exists, matches the current manifest hash, and proves worker slot saturation with schema v2 event metadata plus explicit refill/deferral/blocking evidence.
- Every worker status records `selected_ladder` and `selection_reason`, and selected ladders preserve the allowed worker route order.
- Every normal worker status records `route_class`; the route artifact and status agree, and low-cost route classes do not use premium/full ladders.
- Every launcher terminal condition is represented by packet artifacts, including `launcher-state.json` transitions through active, timeout/fail-clean/fail-dirty/pass/blocked.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- `{pre_review_gate_path}` passed before reviewer launch; reviewer `route.json` exists; the reviewer artifact exists, is `mergeable`, records matching `semantic_input_hashes` and reuse policy, records `git diff --check {base_ref}...HEAD`, and has no verification gaps.
- Active worker/research-worker/reviewer launchers were waited on rather than polled.
- Final branch status JSON passed manifest-bound `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/{branch_id}.status.json`.
- `lite_advice` records are present, even when empty; every relevant branch Lite packet directory is recorded, validated, and treated only as advisory context routing.
{dod}
