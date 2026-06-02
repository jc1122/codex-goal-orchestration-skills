# {branch_id}: {title}

Branch id: {branch_id}
Base ref: {base_ref}
Branch name: {branch_name}
Worktree path: {worktree_path}
Wave: {wave}
Depends on branches:
{depends_on}
Dependency context: {dependency_context}

Runtime rules appendix: {runtime_rules_path}
Runtime rules sha256: {runtime_rules_sha256}

## Objective

{objective}

## Scope

{scope}

## Owned Paths

{owned_paths}

## Work Items

{work_items}

## Branch Runtime Parameters

Use $goal-branch-orchestrator. Read `{runtime_rules_path}` before dispatch; it is part of this prompt contract.

Max active worker packets: {max_active_worker_packets}
Effective worker launch cap: {effective_worker_cap}
Declared worker packets: {worker_packet_count}
Configured package max worker packets per branch: {max_worker_packets_per_branch}

Configured cap: {max_active_worker_packets} active; effective launch cap for this branch: {effective_worker_cap}. Never exceed the active cap or declared worker set.

Branch scheduler serial/under-capacity reasons:
{branch_serial_reasons}

Worker scheduler serial/under-capacity reasons:
{worker_serial_reasons}

Worker scheduler ledger: {worker_scheduler_path}. `worker_parallelism.scheduler_path` in branch status must be `{worker_scheduler_path}`.

Worker parallelization rationale: {worker_parallelization_rationale}

Pre-review gate path: {pre_review_gate_path}

Default worker ladder: {default_worker_ladder}

Allowed worker route aliases: {allowed_worker_routes}

Route-class ladders:
{route_class_ladders}

Telemetry policy mode is `{telemetry_policy_mode}`.

## Bootstrap Command

```bash
python3 "$GOAL_SKILLS_ROOT"/goal-branch-orchestrator/scripts/check_goal_skill_availability.py --skills-root "$GOAL_SKILLS_ROOT" --require goal-branch-orchestrator --require-codex-cli && \
python3 "$GOAL_SKILLS_ROOT"/goal-branch-orchestrator/scripts/check_model_catalog.py --json --require-codex > {branch_model_catalog_path_shell}
```

Return blocked if either command fails.

## Additional Validators

{tests}

## Stop Conditions

{stop_conditions}

## Definition of Done

- `{runtime_rules_path}` was read and followed before worker, reviewer, or Lite packet dispatch.
- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- 1 to {worker_packet_count} worker packets were used for this branch; worker/research-worker/reviewer/Lite packets wrote same-packet `telemetry.json`.
- `{worker_scheduler_path}` exists, matches the current manifest hash, and proves worker slot saturation with schema v2 event metadata plus explicit refill/deferral/blocking evidence.
- `{pre_review_gate_path}` passed before reviewer launch; reviewer `route.json` exists, records matching `semantic_input_hashes`, and has no verification gaps.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- Final branch status JSON passed manifest-bound `validate_branch_status.py --manifest {manifest_path_shell} --status {branch_status_path_shell}`.
{dod}
