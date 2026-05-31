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

Parallel worker packets are the default. Max active worker packets: {max_active_worker_packets}. Max worker packets for this branch: 4. Never exceed {max_active_worker_packets} active worker packets here or 4 total. Launch ready workers as a rolling saturated pool, using `render_worker_schedule.py --list-ready` before initial launch and after every completion.

Worker scheduler ledger: {worker_scheduler_path}. `worker_parallelism.scheduler_path` in branch status must be `{worker_scheduler_path}`. Record ready/launch/finish/close/refill/defer/under_capacity/blocked evidence with scheduler scripts.

Worker parallelization rationale: {worker_parallelization_rationale}

Use each listed Worker packet id exactly once unless replacing a non-pass attempt with `create_runtime_packet.py --replace`. Worker, research-worker, reviewer, and Lite packets must write same-packet `telemetry.json`. After dispatch, wait for launchers; do not poll active worker/reviewer logs, process tables, or status files.

## Worker Model Routing

Default worker ladder: {default_worker_ladder}

Allowed worker route aliases: {allowed_worker_routes}

Selected worker ladders must be an ordered non-empty subsequence of the default ladder with a `selection_reason`; do not invent aliases or reorder providers.

## Lite Advisors

Optional Lite Advisors are context routers only. Use them after bootstrap or completed packets, never while worker/research-worker/reviewer launchers are active, and never as pass/review/mergeability/DoD evidence.

## Tests And Validators

{tests}

## Reviewer Requirement

Before reviewer launch, create schema v2 `{pre_review_gate_path}` / `pre_review_gate.json` with passing checks and current `semantic_input_hashes`. Dispatch a read-only reviewer only after the gate passes. Pass requires mergeable review, matching hashes, same-branch reviewer artifact, `telemetry.json`, no verification gaps, and `git diff --check {base_ref}...HEAD`.

## Bootstrap Requirement

Run the branch skill and Codex CLI availability bootstrap before worker dispatch. Return blocked if the bootstrap fails.

## Stop Conditions

{stop_conditions}

## Definition of Done

- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- 1 to 4 worker packets were used for this branch; worker/research-worker/reviewer/Lite packets wrote same-packet `telemetry.json`.
- Independent worker packets launched as a rolling saturated pool up to max_active_worker_packets, or branch status records the serial/under-capacity reason.
- `{worker_scheduler_path}` exists, matches the current manifest hash, and proves worker slot saturation with schema v2 event metadata plus explicit refill/deferral/blocking evidence.
- Every worker status records `selected_ladder` and `selection_reason`, and selected ladders preserve the allowed worker route order.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- `{pre_review_gate_path}` passed before reviewer launch; reviewer `route.json` exists; the reviewer artifact exists, is `mergeable`, records matching `semantic_input_hashes` and reuse policy, records `git diff --check {base_ref}...HEAD`, and has no verification gaps.
- Active worker/research-worker/reviewer launchers were waited on rather than polled.
- Final branch status JSON passed manifest-bound `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json`.
- `lite_advice` records are present, even when empty; every relevant branch Lite packet directory is recorded, validated, and treated only as advisory context routing.
{dod}
