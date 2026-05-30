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
- Saturate branch orchestrator slots up to max_active_branch_agents.
- Launch the next eligible branch as soon as capacity is freed; do not wait for a whole wave to finish.
- Defer a branch only while one of its manifest `depends_on` branch ids is incomplete. A dependency unlocks downstream work only when it finished with `pass`; `partial`, `blocked`, and `failed` require downstream items to be deferred or blocked with `reason_code: "dependency_failed"` unless a future explicit contract says otherwise.
- Treat waves as scheduling/order groups only, not as implicit dependency barriers.
- Record scheduler evidence in `{main_scheduler_path}` using schema v2 `ready`, `launch`, `finish`, `close`, `refill`, `defer`, `under_capacity`, and `blocked` events. Every scheduler event must include ordered `seq`, `timestamp`, and `runtime_ref`; `defer`, `under_capacity`, and `blocked` must include enum `reason_code` plus `reason`. Use `append_scheduler_event.py` rather than hand-editing ledgers when possible. `branch_parallelism.scheduler_path` in `main.status.json` must be `{main_scheduler_path}`.
- After branch dispatch, wait for branch agents; do not poll active branch worktrees, worker packets, research-worker packets, reviewer packets, process tables, or status files.
- If a native wait returns with no completed branches 3 consecutive times, inspect only native agent/process state, then close unreachable or stale work as structured `blocked` evidence and immediately refill capacity.
- Close finished branch orchestrator agents before launching replacements.
- Do not exceed 4 active branch orchestrator agents.
- Do not read `goal-branch-orchestrator/SKILL.md` in main context; dispatch branch sessions that use that skill.
- After every validated terminal branch result, record an amendment decision artifact under `amendments/Axxx.decision.json`; either skip adaptation with a reason or launch `goal-plan-amender` only to amend future unstarted manifest work through a route-bound `amendments/Axxx.packet/` with `route.json`, `telemetry.json`, `packet.validation.json`, proposal, validation, accepted amendment, archived prior manifest, regenerated future branch prompts, and lint artifacts under `amendments/`.
- Never let an amendment mutate active or terminal branch prompts, worktrees, status paths, review paths, dependencies, owned paths, scheduler ledgers, or runtime artifacts.
- Require each branch to launch independent workers as a rolling saturated pool up to its `max_active_worker_packets` cap.
- Research-only work items must use `research-worker` packets: `codex --search exec --ephemeral -s read-only` without user-config suppression, allowing broad read-only information retrieval through configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, read-only local file access, and configured tool/skill documentation when relevant while prohibiting file edits, secret inspection, unrelated private-file reads, and state-changing actions.
- Require each branch to record `git diff --check {base_ref}...HEAD` before merge readiness.
- Require every branch status to pass `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json` before accepting it.
- Run `summarize_telemetry.py --bundle-dir /absolute/path/to/bundle` before final validation.
- Require final `main.status.json` to pass `validate_main_status.py --manifest /absolute/path/to/job.manifest.json`.
- Final validation reconstructs active branch counts from the scheduler ledger; duplicate launches, launches above cap, dependency launches after non-pass dependencies, missing finishes/closes, missing refill events, vague reason text, and eligible-idle gaps fail even if `main.status.json` claims saturation.
- Treat packet timeouts as failed attempts, not as permission to poll active artifacts; accepted telemetry must record positive `timeout_seconds` for every declared model attempt.
- Main `pass` requires `audit_status: "pass"`, prompt-audit/worker/research-worker/reviewer/Lite and any plan-amender `telemetry.json`, bundle `telemetry.summary.json`, exactly the manifest branch summary set with manifest-matching status/review paths, every branch summary `status: "pass"`, passing branch summaries with `review_status: "mergeable"`, `amendment_decisions` covering every terminal branch summary with packet validation for launched amenders, manifest-owned worker/research artifacts and same-branch reviewer artifacts, exact base-range whitespace command evidence from `git diff --check {base_ref}...HEAD`, no mergeable reviewer verification gaps, DoD evidence, `lite_advice` audit records, and no blockers. `partial` is allowed only when some work completed and scheduler v2 explains every omitted branch or worker id with terminal structured evidence.
- Optional Lite advisors are context routers only. Do not launch Lite before prompt audit except for an audit-defect summary after a failed/blocked audit. Validated Lite advice may guide targeted original reads, but it is not audit, review, mergeability, or DoD evidence. Record `lite_advice: []` only when no relevant main Lite packet exists; otherwise record each packet with purpose, status, disposition, manifest-owned advice/input paths, source hashes, exact validation command, validation status, validation defects, and reason.
- Preserve unsupported, unresolved, negative, and probe-only labels.

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
- `{main_scheduler_path}` exists, matches the current manifest hash, and proves branch slot saturation with schema v2 event metadata plus explicit refill/deferral/blocking evidence.
- Every branch status passed manifest-bound `validate_branch_status.py`.
- Every terminal branch summary has an `amendment_decisions` launch or skip record; every launched amender has passing packet validation.
- Every mergeable review recorded base-range whitespace evidence and no verification gaps.
- Final `main.status.json` passed manifest-bound `validate_main_status.py`.
- `lite_advice` records are present, even when empty; every relevant main Lite packet directory is recorded, validated, and treated only as advisory context routing.
{final_dod}
