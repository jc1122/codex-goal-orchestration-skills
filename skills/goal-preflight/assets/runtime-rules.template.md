# Shared Branch Runtime Rules

Bundle job id: {job_id}
Base ref: {base_ref}
Telemetry policy mode: {telemetry_policy_mode}
Main scheduler ledger: {main_scheduler_path}

This file is the shared runtime appendix for every branch prompt in the bundle. Branch prompts provide branch-specific work items, caps, scheduler paths, route ladders, and validator commands. Treat `job.manifest.json`, the branch prompt, and this appendix as one contract.

## Worker Parallelism

Use $goal-branch-orchestrator. Treat `job.manifest.json` as the policy source and run `runtime_phase_manifest.py --markdown`; do not read skill Python source unless debugging a failed script.

Before worker dispatch or reviewer dispatch, run `script_only_repair_gate.py --scope branch`. Complete any `script_actions_needed` commands first; launch workers or reviewers only after the gate returns `pass_no_actions` or reviewer reuse has been accepted with telemetry.

Respect each branch prompt's active worker packet cap and declared worker set. Never exceed the active cap. Launch ready workers as a rolling saturated pool with `render_worker_schedule.py --list-ready` before first launch and after each completion.

Record ready, launch, finish, close, refill, defer, under_capacity, and blocked evidence with scheduler scripts. Use each listed Worker packet id exactly once unless replacing a non-pass attempt with `create_runtime_packet.py --replace`.

Worker, research-worker, reviewer, and Lite packets must write same-packet `telemetry.json`. Worker/research/reviewer launchers also write `launcher-state.json`; consume terminal packet artifacts and scheduler events, not silence.

Outside the watchdog exception below, wait for launchers and do not poll active worker/reviewer logs, process tables, or status files. If no worker/reviewer completes after `orchestration_watchdog.branch_no_completion_wait_limit` consecutive waits, inspect only native agent/process state. Do not block, close, replace, or relaunch a packet while a matching native process or launcher state is still live and reachable. Close only unreachable or stale active packets with `scheduler_tick.py --blocked/--close --reason-code stale_active|native_agent_unreachable|timeout`, then refill eligible capacity.

## Worker Model Routing

Worker route classes and compact route reason codes are declared per work item in `job.manifest.json`. Use the declared class unless a more specific route is justified. Prefer the recorded route-class ladder for mechanical, docs, small-edit, and normal-code work; use the full configured ladder for complex-code work when branch risk justifies it.

Selected worker ladders must be an ordered non-empty subsequence of the default ladder with a `selection_reason`; do not invent aliases or reorder providers. When a branch prompt says route availability is deferred, capture a fresh model catalog or accepted-route smoke check before selecting concrete aliases.

In debug mode, telemetry collection remains passive and does not alter route selection, polling cadence, or watchdog thresholds.

## Lite Advisors

Optional Lite Advisors are context routers only. Use them after bootstrap or completed packets, never while worker/research-worker/reviewer launchers are active, and never as pass/review/mergeability/DoD evidence.

## Reviewer Requirement

Before reviewer launch, create schema v2 branch `pre_review_gate.json` with passing checks, current `semantic_input_hashes`, route-policy freshness, and reuse eligibility. Dispatch a read-only reviewer only after the gate passes and reviewer reuse is not accepted.

Pass requires mergeable review or accepted reviewer reuse with telemetry, matching hashes, same-branch reviewer artifact, `telemetry.json`, no verification gaps, and `git diff --check {base_ref}...HEAD`.

## Bootstrap Requirement

Before worker dispatch, run the branch prompt's exact bootstrap command. Return blocked if either skill availability or model catalog capture fails.

## Validation DoD

- Active worker/research-worker/reviewer launchers were waited on rather than polled.
- Every worker status records `selected_ladder` and `selection_reason`, and selected ladders preserve the allowed worker route order.
- Every normal worker status records `route_class`; the route artifact and status agree, and low-cost route classes do not use premium/full ladders.
- Every launcher terminal condition is represented by packet artifacts, including `launcher-state.json` transitions through active, timeout, fail-clean, fail-dirty, pass, or blocked.
- `lite_advice` records are present, even when empty; every relevant branch Lite packet directory is recorded, validated, and treated only as advisory context routing.
