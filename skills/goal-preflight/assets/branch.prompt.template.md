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

Parallel worker packets are the default for independent work items. This branch contains 1 to 4 worker packets total. Launch independent workers in separate child worktrees whenever their owned paths and verification commands do not conflict. Never exceed {max_active_worker_packets} active worker packets for this branch, and never exceed 4 active worker packets under any circumstance. If a work item has a `Depends on` entry, do not launch it until the dependency's output is integrated and available as context. If this branch is executed serially or below the worker cap, record the reason in `worker_parallelism.serial_reasons`.

Worker parallelization rationale: {worker_parallelization_rationale}

Use the listed Worker packet id for each worker packet. A `pass` or `partial` branch status must include one worker status for every manifest work item packet id and no extra worker packet ids. Branch `pass` requires every worker status to be `pass` and backed by the manifest-owned `workers/<packet_id>/status.json`.

After worker dispatch, wait for active worker launchers; do not poll active worker worktrees, event logs, process tables, or status files unless the user explicitly enters debug mode or a launcher exits without a valid status.

## Worker Model Routing

Default worker ladder: {default_worker_ladder}

Allowed worker route aliases: {allowed_worker_routes}

Selected worker ladders may be chosen by the branch orchestrator per worker packet when task hardness, context size, quota pressure, or provider availability justify it. A selected ladder must be a non-empty ordered subsequence of the default worker ladder; do not reorder providers, invent model aliases, change model ids, or use reviewer/auditor models for worker packets. Prefer Spark immediately after Gemini Pro and Gemini Flash; Copilot `gpt-5.4` comes only after Spark in the standard ladder.

When creating a worker packet with a non-default route, pass repeated `--worker-route <alias>` values and a non-empty `--selection-reason`. Every worker status and branch rollup must record `selected_ladder` and `selection_reason`, and the final validator must reject route drift from the packet's manifest-owned `route.json`.

## Lite Advisors

Optional Lite advisors are context routers only. After required start checks pass, the branch may use validated Lite advice for worker packet planning or context packing. After worker launchers finish, the branch may use validated Lite advice for worker summaries or blocked triage. Never launch Lite while worker/reviewer launchers are active, and never treat Lite as worker pass, review pass, mergeability, scientific claim, or Definition-of-Done evidence. Branch Lite packet ids must be scoped as `<branch-id>-L<suffix>`. Record `lite_advice: []` only when no relevant branch Lite packet exists; otherwise record each packet with purpose, status, disposition, manifest-owned advice/input paths, source hashes, exact validation command, validation status, validation defects, and reason.

## Tests And Validators

{tests}

## Reviewer Requirement

Dispatch a read-only heavy-model reviewer after worker integration. The branch may return pass only if the reviewer verdict is `mergeable`, the reviewer packet id belongs to this branch, the reviewer artifact exists, verification gaps are empty, and exact base-range whitespace evidence from `git diff --check {base_ref}...HEAD` is recorded.

After reviewer dispatch, wait for the reviewer launcher; do not poll active reviewer event logs, process tables, or review files unless the user explicitly enters debug mode or the launcher exits without a valid review.

## Bootstrap Requirement

Run the branch skill and Codex CLI availability bootstrap before worker dispatch. Return blocked if the bootstrap fails.

## Stop Conditions

{stop_conditions}

## Definition of Done

- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- 1 to 4 worker packets were used for this branch.
- Independent worker packets launched concurrently up to max_active_worker_packets, or branch status records the serial/under-capacity reason.
- Every worker status records `selected_ladder` and `selection_reason`, and selected ladders preserve the allowed worker route order.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- The reviewer artifact exists, is `mergeable`, records `git diff --check {base_ref}...HEAD`, and has no verification gaps.
- Active worker/reviewer launchers were waited on rather than polled.
- Final branch status JSON passed manifest-bound `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json`.
- `lite_advice` records are present, even when empty; every relevant branch Lite packet directory is recorded, validated, and treated only as advisory context routing.
{dod}
