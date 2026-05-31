---
name: goal-main-orchestrator
description: Runtime-only main orchestrator for prepared /goal job bundles. Use when a Copilot /goal session has been launched from a goal-preflight bootloader and must consume existing job.manifest.json, main.prompt.md, and branch prompts; first run skill availability bootstrap and fail-closed prompt audit with telemetry, optionally use CLI-only Lite advisors after audit or completed branch artifacts for advisory summaries, then create path-validated branch worktrees, dispatch goal-branch-orchestrator sessions within the hard agent limit, summarize packet telemetry, and finish only when the main prompt's falsifiable Definition of Done is satisfied.
---

# Goal Main Orchestrator

## Role Boundary

Act as the runtime main orchestrator only. Do not create `/goal` bootloader text, `main.prompt.md`, branch prompt files, or `job.manifest.json`; a separate prompt-prep skill owns those artifacts.

Your job is:

1. Run the skill availability bootstrap.
2. Read the prepared `job.manifest.json` and `main.prompt.md`.
3. Dispatch a read-only heavy-model prompt auditor before any branch work starts.
4. Create branch integration worktrees only after the audit passes, one eligible branch at a time as branch-orchestrator capacity is available.
5. Keep branch orchestrator slots saturated up to the hard active-agent limit; defer only branches with incomplete manifest `depends_on` branch ids.
6. Optionally use Lite advisors only after prompt audit or after branch artifacts are complete.
7. Review branch status/review artifacts against `main.prompt.md` DoD.
8. Return `pass` only when the DoD is falsifiably satisfied.

Runtime token discipline: use script outputs, JSON artifacts, and validator defects as the working surface. Do not open `skills/*/scripts/*.py` during normal runtime; inspect Python source only when a script itself fails and debugging the script is the assigned task.

## Skill Availability Bootstrap

Every `/goal` run starts by checking runtime skill availability before prompt audit, branch creation, or agent dispatch. Resolve the skills root once:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-main-orchestrator" ] && [ -d "$HOME/.agents/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_goal_skill_availability.py" \
  --skills-root "$GOAL_SKILLS_ROOT" \
  --require goal-main-orchestrator \
  --require goal-branch-orchestrator \
  --require goal-plan-amender
```

If this fails, stop immediately and return `blocked` with the missing skill/script names. Do not run prompt audit, create worktrees, or apply amendments until bootstrap passes.

Then record the fresh Codex model catalog for this run before selecting or launching model routes:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py" --json --require-codex \
  > /absolute/path/to/plans/orchestration/<job-id>/model-catalog.json
```

Use the live `codex debug models` source when available. Treat bundled-only results as fallback evidence, not as authoritative proof that an account-visible model is unavailable.

## Mandatory Start

After bootstrap passes, run prompt audit. Do not create Lite packets, branches, worktrees, branch orchestrators, workers, reviewers, commits, or merges before `prompt-audit.json` says audit passed and `can_start` is true, except that a Lite `audit-defect-summary` packet may be used after a failed or blocked audit to summarize defects for handoff.

Use the bundle root and repository root from the bootloader. Manifest prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root. Treat absolute paths, backslashes, and `..` traversal in manifest-owned paths as `blocked`.

Use `scripts/create_audit_packet.py` to create the audit packet:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/create_audit_packet.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/audit
```

Then run the generated `launch.sh`. The audit packet uses exactly `gpt-5.5`, then `gpt-5.4`; no model overrides are accepted. The packet schema pins the exact manifest path and repository root. The launcher always writes packet-local `telemetry.json` with declared/called/accepted model aliases, model ids, prompt/output/log character and byte counts, and best-effort token usage when provider logs expose it. If both audit attempts fail without producing a valid `prompt-audit.json`, the launcher writes a terminal blocked `prompt-audit.json`. Validate the artifact before branch scheduling:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/validate_prompt_audit.py" \
  --audit /absolute/path/to/plans/orchestration/<job-id>/audit/prompt-audit.json \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --require-pass
```

Read `references/prompt-audit-contract.md` if the audit fails or if the prepared bundle shape is unclear.

## Lite Advisors

Lite advisors are optional context routers, not authorities. Main may launch Lite only after prompt audit has completed:

- `audit-defect-summary`: summarize a failed or blocked `prompt-audit.json` for handoff;
- `main-summary`: summarize completed branch status/review artifacts before writing `main.status.json`.

Do not use Lite before prompt audit to pre-screen prompts. Do not let Lite decide audit pass/fail, branch pass/fail, mergeability, cleanup, or DoD satisfaction. Use Lite output to choose targeted originals; validators and heavy reviewers remain authoritative.

Example:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/create_lite_advice_packet.py" \
  --packet-id M01-L01 \
  --purpose main-summary \
  --base-dir /absolute/path/to/repo \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/lite \
  --input-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.status.json \
  --input-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.review.json
```

After running the generated `launch.sh`, validate `advice.json` with `scripts/validate_lite_advice.py`. If Lite is blocked, invalid, stale, or contradicted by branch artifacts, ignore it. The main Lite scripts enforce the main-only purpose allowlist (`audit-defect-summary`, `main-summary`), capture the absolute Gemini CLI path/version/binary sha256 at packet creation, rehash all source inputs, `task.md`, `prompt.md`, and the Gemini binary during launch/validation, regenerate the prompt from `input-files.json` plus `task.md`, write packet-local `telemetry.json`, and reject runtime-purpose recommendations outside the explicit input set. Main status validation scans manifest-owned `lite/` for relevant main Lite packet directories and fails if they are not recorded in `lite_advice`; recorded Lite validation commands must be the exact `python3 <skill>/scripts/validate_lite_advice.py --advice <packet>/advice.json --inputs <packet>/input-files.json` command for that manifest-owned packet.

## Branch Creation

After audit passes, create branch integration worktrees from the manifest. Use rolling scheduling: render and run worktree commands only for branches that are eligible to launch now. A branch is eligible when it is not already active or complete and every branch id in its manifest `depends_on` list is complete. Use `scripts/render_branch_worktree_commands.py` to list ready branch ids or print the exact `git worktree add` command for a branch:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/render_branch_worktree_commands.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --audit /absolute/path/to/prompt-audit.json \
  --list-ready \
  --completed-branch B01 \
  --active-branch B02 \
  --limit 3
```

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/render_branch_worktree_commands.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --audit /absolute/path/to/prompt-audit.json \
  --branch B03 \
  --completed-branch B01
```

Run the printed commands only after checking the repo state:

```bash
git status --short --branch
git worktree list --porcelain
git diff --check HEAD
```

If any target branch or worktree already exists, stop and report `blocked` unless the main prompt explicitly defines a reuse policy.

## Branch Orchestrator Dispatch

Launch branch orchestrators as a rolling saturated pool. Parallelism is the default: keep up to `max_active_branch_agents` branch orchestrators active whenever eligible branches remain. Respect `max_active_branch_agents`; it must be treated as a hard limit and must not exceed 4. Record schema v2 branch scheduler events in `schedulers/main.scheduler.json`: `ready`, `launch`, `finish`, `close`, `refill`, `defer`, `under_capacity`, and `blocked` as applicable. Every event must include ordered `seq`, `timestamp`, and `runtime_ref`; `defer`, `under_capacity`, and `blocked` must include enum `reason_code` plus `reason`. Use `scheduler_tick.py` for normal ready/launch/finish/close/refill bookkeeping, and reserve `append_scheduler_event.py` for unusual explicit events. For example:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --scope main \
  --runtime-ref goal-main-orchestrator \
  --init \
  --record-ready \
  --launch B01
```

Do not wait for a whole wave to finish. When any branch finishes with `pass` and is validated/closed, record `finish`/`close` through `scheduler_tick.py`; it records `refill` when capacity frees with eligible branches waiting, then launch the next eligible branch immediately. Branches that depend on `partial`, `blocked`, or `failed` branches must be blocked or deferred with `reason_code: "dependency_failed"` evidence. Defer a branch only while one of its manifest `depends_on` branch ids is incomplete or with a structured contention/blocked reason; waves are scheduling/order groups, not implicit dependency barriers. Each branch orchestrator must use the `goal-branch-orchestrator` skill and receive:

- the branch id;
- branch prompt path;
- branch integration branch name;
- branch worktree path;
- manifest path;
- prompt audit path;
- expected branch status path;
- expected branch review path.

Before dispatching a branch, verify its manifest entry declares 1 to 4 `work_items`, deterministic worker `packet_id` values in `<branch_id>-<work_item_id>` form, optional `worker_type` values only from `worker` or `research-worker`, `max_active_worker_packets` from 1 to 4, `worker_parallelism.parallelism_default=true`, and `worker_parallelism.scheduling_mode=rolling`. If any work item uses `research-worker`, verify the manifest includes `research_worker_policy` requiring `codex --search exec --ephemeral -s read-only` without user-config suppression, broad read-only information retrieval through configured CLI/MCP/connector/browser/search tools plus shell/network inspection commands, and no file edits or state-changing actions. If not, return `blocked`; do not let a branch session infer missing worker-packet policy.

Runtime packet launchers are bounded. Prompt audit attempts default to 1200 seconds, normal worker route attempts to 3600 seconds, research-worker attempts to 1200 seconds, reviewer attempts to 1800 seconds, and Lite advisor attempts to 600 seconds, all with a 30-second kill-after window. Treat a timeout as a failed attempt; do not poll active branch or worker artifacts while waiting for it. Accepted telemetry must include positive `timeout_seconds` on every declared attempt.

The main orchestrator should not implement branch work itself and should not inspect worker event logs unless a branch status is missing, inconsistent, or blocked.

Do not open or read `goal-branch-orchestrator/SKILL.md` in the main orchestrator context. Treat that skill as a branch-session launch target: verify it exists during bootstrap, then dispatch a branch orchestrator session that loads and follows it with the inputs above. If branch-session launch is impossible, return `blocked` instead of absorbing the branch runtime instructions into main.

After dispatch, use the native agent wait mechanism with the longest practical timeout. If a wait returns with no completed branch agents, do not poll branch worktrees, worker packets, research-worker packets, reviewer packets, process tables, or status files, and do not send status-check nudges. After 3 consecutive no-completion waits, inspect only native agent/process state, then close unreachable or stale work as structured `blocked` evidence and refill capacity. Continue waiting unless the user explicitly enters debug mode or a branch agent returns `blocked`/`failed`/`partial`.

Track active branch orchestrator agent ids/processes. As each branch finishes, collect its status/review artifacts, validate them, then close or turn off the finished branch orchestrator. If capacity is freed and an eligible unstarted branch remains, create its worktree and launch it immediately. If a finished branch orchestrator cannot be closed and capacity cannot be freed, stop and return `blocked` instead of exceeding the active-agent limit.

## Plan Amendments

After every branch reaches terminal status, its branch status passed `validate_branch_status.py`, and the finished branch orchestrator has been closed, run `goal-plan-amender/scripts/recommend_amendment_decision.py` for the deterministic cases. It can write the decision artifact directly for no eligible branch, dependency-blocked downstream work, eligible work remaining, active work still pending, or all-pass/no-adaptation cases:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/recommend_amendment_decision.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --amendment-id A001 \
  --write-decision
```

If the recommender cannot cover the semantic case, record a deterministic amendment decision with `goal-plan-amender/scripts/create_amendment_decision.py`. The decision is either `skip` when no adaptation is needed or `launch` when no manifest branch is eligible, blockers would stall downstream work, remaining unstarted work no longer covers the main DoD, or finalization is impossible but plausible recovery work can be added safely.

The amender may create `amendments/Axxx.packet/`, `Axxx.packet/packet.validation.json`, `Axxx.proposal.json`, `Axxx.validation.json`, `Axxx.accepted.json`, and archived manifest copies. Launch it with a selected ordered amender model ladder from `amender_model_policy.allowed_routes`; the packet must write `route.json` and `telemetry.json` under `amendments/Axxx.packet/`, then pass `validate_amender_packet.py` before proposal validation or apply. It must not launch branches, edit scheduler ledgers, inspect active branch internals, mutate active or terminal branch prompts/status/review/worktree paths, or mark the run `pass`. If validation fails, preserve the defects and continue with any still-eligible existing work. If no work remains, return `partial` or `blocked` with the amendment failure evidence.

Accepted amendments update only `job.manifest.json` future work and changed future branch prompts, then rerun preflight lint. Treat the updated manifest as the live scheduling source after acceptance.

## Status Validation

Validate every finished branch status before accepting it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/validate_branch_status.py" \
  --status /absolute/path/to/branches/B01.status.json \
  --manifest /absolute/path/to/job.manifest.json \
  --branch-id B01 \
  --branch <branch-name> \
  --worktree /absolute/path/to/.worktrees/<branch-name>
```

Before final return, summarize all packet telemetry, write `main.status.json`, and validate it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py" \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id>
```

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/validate_main_status.py" \
  --status /absolute/path/to/main.status.json \
  --manifest /absolute/path/to/job.manifest.json \
  --job-id <job-id>
```

If either validator fails, return `blocked` or `partial`; do not claim `pass`. A passing main status must include `audit_status: "pass"`, exactly the manifest branch summary set with manifest-matching status/review paths, all branch summaries as `status: "pass"`, passing branch summaries with `review_status: "mergeable"`, manifest-owned worker/research-worker artifacts and same-branch reviewer artifacts backing those claims, prompt-audit/worker/research-worker/reviewer/Lite `telemetry.json` artifacts, `amendment_decisions` records covering every terminal branch summary with launch decisions pointing to passing packet validations, bundle `telemetry.summary.json`, exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, no mergeable reviewer verification gaps, a `lite_advice` array (empty only when no relevant main Lite packet exists; manifest-owned auditable records otherwise, with `validation_status` and `validation_defects` matching actual validation), a non-empty command list, a non-empty DoD checklist, and no blockers. A partial main status may omit unlaunched branch artifacts only when schema v2 scheduler evidence explains every omitted branch id with terminal structured evidence.

## Completion Gate

Before returning `pass`, verify:

- skill availability bootstrap passed for `goal-main-orchestrator` and `goal-branch-orchestrator`;
- prompt audit passed;
- prompt audit, worker, research-worker, reviewer, used/ignored Lite packets, and any plan-amender packets wrote `telemetry.json`;
- `summarize_telemetry.py --bundle-dir <bundle>` wrote `telemetry.summary.json`;
- manifest cleanup and artifact policies are present and are not contradicted by `main.prompt.md`;
- every branch listed in the manifest has a status file;
- every branch requiring review has a review file;
- every branch status passed manifest-bound `validate_branch_status.py`;
- every terminal branch summary has a recorded amender launch or skip decision;
- every branch summary in `main.status.json` is `pass` with `review_status: "mergeable"`;
- every mergeable review has empty verification gaps and base-range whitespace evidence;
- branch statuses satisfy the main prompt DoD;
- branch statuses/reviews record base-range whitespace validation before merge readiness;
- branch statuses record the branch worker-packet cap and concurrent worker launch evidence or a serial/under-capacity reason;
- no more than `max_active_branch_agents` branch orchestrators were active at once;
- branch starts were deferred only for incomplete manifest `depends_on` branch ids;
- main did not poll active branch agents' worker packets, research-worker packets, reviewer packets, worktrees, or process tables while waiting;
- finished branch orchestrators were closed before replacements launched;
- required commands and validators are recorded;
- manifest-bound `validate_main_status.py` passed for the final main status file;
- unresolved, unsupported, negative, or probe-only labels are preserved;
- final git state matches the main prompt's merge/cleanup policy.
- `lite_advice` records are present, even when empty; any Lite advice used was validated and treated only as advisory context routing, not DoD evidence.

If any item is missing or unverifiable, return `partial` or `blocked`, not `pass`.

Read `references/main-runtime-contract.md` for the full status contract and context-conservation rules. Read `references/lite-advisor-contract.md` before creating Lite packets.
