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

Parallel worker packets are the default for independent work items. This branch contains 1 to 4 worker packets total. Launch independent workers as a rolling saturated pool in separate child worktrees whenever their owned paths and verification commands do not conflict. Never exceed {max_active_worker_packets} active worker packets for this branch, and never exceed 4 active worker packets under any circumstance. If a work item has a `Depends on` entry, do not launch it until the dependency's output is integrated and available as context. When any worker launcher exits, collect and integrate its status/diff, free its active slot, and launch the next eligible worker immediately if capacity is available. If this branch is executed serially or below the worker cap, record the reason in `worker_parallelism.serial_reasons`.

Worker scheduler ledger: {worker_scheduler_path}

Record `ready`, `launch`, `finish`, `close`, `refill`, `defer`, `under_capacity`, and `blocked` events in that scheduler ledger. `worker_parallelism.scheduler_path` in branch status must be `{worker_scheduler_path}`. Final validation reconstructs active worker counts from this ledger and rejects duplicate launches, launches above cap, missing finishes/closes, missing refill events, and eligible-idle gaps even if status prose claims saturation.

Worker parallelization rationale: {worker_parallelization_rationale}

Use `render_worker_schedule.py --list-ready` with the current completed and active worker packet ids before initial launch and after every worker completion.

Use the listed Worker packet id for each worker packet. A `pass` or `partial` branch status must include one worker status for every manifest work item packet id and no extra worker packet ids. Branch `pass` requires every normal worker status to be `pass` and backed by the manifest-owned `workers/<packet_id>/status.json` plus same-packet `telemetry.json`.

If a work item lists `Worker type: research-worker`, create it with `create_runtime_packet.py --role research-worker`, put the packet under manifest-owned `research/<packet_id>/`, and use it only for outside information gathering. Research workers run `codex --search exec --ephemeral -s read-only` without user-config suppression, so they may use Codex native search, configured read-only CLI/MCP/connector/browser/search tools, package metadata lookups, remote APIs, shell/network inspection commands, read-only local file access, and configured tool/skill documentation when relevant. They must not edit files, inspect secrets or unrelated private files, or perform state-changing, destructive, credential, posting, purchasing, or remote mutation actions. Branch `pass` requires every research-worker status to be `pass` and backed by `research/<packet_id>/research.json` plus same-packet `telemetry.json`.

After worker dispatch, wait for the next active worker launcher to exit; do not poll active worker worktrees, event logs, process tables, or status files unless the user explicitly enters debug mode or a launcher exits without a valid status. After integrating an exited worker, refill capacity from eligible work items rather than waiting for all currently active workers to finish.

## Worker Model Routing

Default worker ladder: {default_worker_ladder}

Allowed worker route aliases: {allowed_worker_routes}

Selected worker ladders may be chosen by the branch orchestrator per worker packet when task hardness, context size, quota pressure, or provider availability justify it. A selected ladder must be a non-empty ordered subsequence of the default worker ladder; do not reorder providers, invent model aliases, change model ids, or use reviewer/auditor models for worker packets. Prefer Spark immediately after Gemini Pro and Gemini Flash; Copilot `gpt-5.4` comes only after Spark in the standard ladder.

When creating a worker packet with a non-default route, pass repeated `--worker-route <alias>` values and a non-empty `--selection-reason`. Every worker status and branch rollup must record `selected_ladder` and `selection_reason`, and the final validator must reject route drift from the packet's manifest-owned `route.json` and `telemetry.json`.

## Lite Advisors

Optional Lite advisors are context routers only. After required start checks pass, the branch may use validated Lite advice for worker packet planning or context packing. After worker launchers finish, the branch may use validated Lite advice for worker summaries or blocked triage. Never launch Lite while worker/research-worker/reviewer launchers are active, and never treat Lite as worker pass, review pass, mergeability, scientific claim, or Definition-of-Done evidence. Branch Lite packet ids must be scoped as `<branch-id>-L<suffix>`. Record `lite_advice: []` only when no relevant branch Lite packet exists; otherwise record each packet with purpose, status, disposition, manifest-owned advice/input paths, source hashes, exact validation command, validation status, validation defects, telemetry, and reason.

## Tests And Validators

{tests}

## Reviewer Requirement

Before reviewer packet generation, write `{pre_review_gate_path}` with `status: "pass"`, current input hashes, validator/test/diff/ownership/DoD gate results, and reviewer reuse policy. Dispatch a read-only heavy-model reviewer only after that deterministic pre-review gate passes. The branch may return pass only if the reviewer verdict is `mergeable`, the reviewer packet id belongs to this branch, the reviewer artifact and same-packet `telemetry.json` exist, reviewer input hashes match `{pre_review_gate_path}` exactly, reviewer reuse is accepted only when all recorded hashes match, verification gaps are empty, and exact base-range whitespace evidence from `git diff --check {base_ref}...HEAD` is recorded.

After reviewer dispatch, wait for the reviewer launcher; do not poll active reviewer event logs, process tables, or review files unless the user explicitly enters debug mode or the launcher exits without a valid review.

## Bootstrap Requirement

Run the branch skill and Codex CLI availability bootstrap before worker dispatch. Return blocked if the bootstrap fails.

## Stop Conditions

{stop_conditions}

## Definition of Done

- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- 1 to 4 worker packets were used for this branch.
- Worker, research-worker, reviewer, and any Lite packets wrote same-packet `telemetry.json`.
- Research-worker packets, when present, used broad read-only information retrieval, recorded `tools_used` and source URLs, passed read-only security validation, and wrote same-packet `telemetry.json`.
- Packet telemetry records positive `timeout_seconds` for every declared model attempt.
- Independent worker packets launched as a rolling saturated pool up to max_active_worker_packets, or branch status records the serial/under-capacity reason.
- `{worker_scheduler_path}` exists, matches the current manifest hash, and proves worker slot saturation with explicit refill/deferral evidence.
- Every worker status records `selected_ladder` and `selection_reason`, and selected ladders preserve the allowed worker route order.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- `{pre_review_gate_path}` passed before reviewer launch; the reviewer artifact exists, is `mergeable`, records matching reviewer input hashes and reuse policy, records `git diff --check {base_ref}...HEAD`, and has no verification gaps.
- Active worker/research-worker/reviewer launchers were waited on rather than polled.
- Final branch status JSON passed manifest-bound `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json`.
- `lite_advice` records are present, even when empty; every relevant branch Lite packet directory is recorded, validated, and treated only as advisory context routing.
{dod}
