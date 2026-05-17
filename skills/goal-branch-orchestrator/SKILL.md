---
name: goal-branch-orchestrator
description: Runtime-only branch orchestrator for an audited branch prompt and existing branch worktree. Use when goal-main-orchestrator has passed prompt audit, created a branch integration worktree, and launched a branch session that must run skill/CLI bootstrap, optionally use CLI-only Lite advisors for packet planning/context packing/completed-worker summaries/blocked triage, create path-safe worker/reviewer packets, dispatch granular Gemini Pro/Flash-first workers with GitHub Copilot gpt-5.4 high-effort and Codex Spark/5.4-mini fallbacks, integrate results, dispatch a read-only heavy-model reviewer, and return only when the branch prompt's falsifiable Definition of Done is satisfied or blocked.
---

# Goal Branch Orchestrator

## Role Boundary

Act as a branch orchestrator only. Do not create or rewrite `main.prompt.md`, branch prompt files, the `/goal` bootloader, or `job.manifest.json`. Do not create the branch integration worktree; the main orchestrator owns that.

Your job is:

1. Run the skill and CLI availability bootstrap.
2. Read the assigned branch prompt file.
3. Verify the global prompt audit passed.
4. Optionally use Lite advisors for packet planning or context routing after required start checks pass.
5. Create granular worker packets and worker child worktrees as needed.
6. Launch independent worker packets concurrently when their owned paths and verification commands do not conflict, using Gemini Pro/Flash-first workers with GitHub Copilot `gpt-5.4` high-effort and Codex Spark/mini fallbacks.
7. Inspect worker status, diffs, and focused verification evidence.
8. Optionally use Lite advisors for completed-worker summaries or blocked triage after launchers exit.
9. Dispatch a read-only heavy-model reviewer.
10. Return branch status only when the branch prompt DoD is satisfied or explicitly blocked.

## Required Start

Before dispatching workers, resolve the skills root and verify the branch skill plus Codex CLI are available:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-branch-orchestrator" ] && [ -d "$HOME/.agents/skills/goal-branch-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/check_goal_skill_availability.py" \
  --skills-root "$GOAL_SKILLS_ROOT" \
  --require goal-branch-orchestrator \
  --require-codex-cli
```

If this fails, return `blocked` before launching workers or reviewers.

Then run:

```bash
pwd
git status --short --branch
git worktree list --porcelain
git diff --check HEAD
```

Confirm:

- the current checkout is the branch integration worktree assigned by the main orchestrator;
- the prompt audit file says `status == "pass"` and `can_start == true`;
- the branch prompt is the assigned prompt;
- the branch prompt has an actionable, falsifiable DoD.
- assigned prompt, status, review, worker, and reviewer packet paths are absolute or are resolved from the bundle root before use.

If any check fails, do not launch workers. Return `blocked`.

## Lite Advisors

Lite advisors are optional context routers, not workers, reviewers, or authorities. Branch may launch Lite only after required start checks pass and never while worker/reviewer launchers are active:

- `branch-packet-planning`: branch prompt, manifest branch entry, and prompt audit to worker packet advice;
- `context-pack`: selected branch prompt/read-first files to focused worker context advice;
- `worker-summary`: completed worker statuses, diff names, and test evidence to summary advice;
- `blocked-triage`: blocked/failed worker status plus relevant failure excerpt to next repair-packet advice.

Read Lite `advice.json` first, then open only cited original files or spans needed for verification. Do not treat Lite as evidence for worker pass, review pass, mergeability, scientific claim support, or DoD satisfaction.

Example:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_lite_advice_packet.py" \
  --packet-id B01-L01 \
  --purpose context-pack \
  --base-dir /absolute/path/to/repo \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/lite \
  --input-file /absolute/path/to/repo/plans/orchestration/<job-id>/branches/B01.prompt.md
```

After running the generated `launch.sh`, validate `advice.json` with `scripts/validate_lite_advice.py`. If Lite is blocked, invalid, stale, or contradicted by worker artifacts or original files, ignore it. The branch Lite scripts enforce the branch-only purpose allowlist (`branch-packet-planning`, `context-pack`, `worker-summary`, `blocked-triage`), capture the absolute Gemini CLI path and version at packet creation, rehash all source inputs and `prompt.md` before launch and during validation, and reject runtime-purpose recommendations outside the explicit input set.

## Worker Packets

Workers must fit the smallest intended worker context across the fixed fallback chain, so keep packets below roughly 80k-100k total input context by using:

- one objective;
- narrow owned files/modules;
- a short read-first list;
- exact verification commands;
- a falsifiable worker DoD;
- required JSON status output.

Parallel worker packets are the default for independent work items. Use separate child worktrees for workers that can proceed without sharing writable files. A branch uses 1 to 4 prepared worker packets total. Launch independent worker packets concurrently up to the branch prompt's `max_active_worker_packets` value. That value is a hard cap and must never exceed 4. If branch work must run serially or below the worker cap, record the reason in `worker_parallelism.serial_reasons` rather than silently serializing it.

Use `scripts/create_runtime_packet.py` to create worker packets:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py" \
  --role worker \
  --packet-id B01-W01 \
  --branch <branch-name>-W01 \
  --worktree /absolute/path/to/.worktrees/<branch-name>-W01 \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/workers \
  --owned-file src/example.py \
  --context-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.prompt.md
```

The packet generator enforces absolute `--worktree`, `--out-dir`, `--task-file`, and `--context-file` paths. Worker prompts render worktree-local context files as relative paths and embed out-of-worktree context snapshots so workspace-restricted CLIs do not need to read bundle paths outside the worker worktree. Generated worker launchers use exactly this fixed order: Gemini CLI with `gemini-3.1-pro-preview`, Gemini CLI with `gemini-3-flash-preview`, GitHub Copilot CLI with `gpt-5.4` and `--effort high`, `gpt-5.3-codex-spark`, then `gpt-5.4-mini`. No model, effort, approval-mode, or permission overrides are accepted. All worker providers receive the same generated prompt and must satisfy the same worker status schema; CLI permission controls are provider-specific, so acceptance still depends on schema validation, clean fallback boundaries, branch diff inspection, and branch-level tests. Before each full Gemini worker attempt, the launcher runs a 20-second headless probe with the same Gemini model. Before the full Copilot worker attempt, the launcher runs a 20-second no-tool probe with `gpt-5-mini` and `--effort low` to verify Copilot CLI/auth/routing without spending a `gpt-5.4` call. Gemini and Copilot are best-effort: if the command is unavailable, quota-limited, unavailable, or fails without dirtying the worker worktree, the launcher continues to the next worker. Copilot runs the real worker in programmatic mode with `gpt-5.4`, `--effort high`, minimal tool permissions, JSONL events, and a Markdown session share; because Copilot has no local `--output-schema` equivalent, the launcher accepts only the marked final worker JSON and still requires orchestrator diff/test verification. If Gemini or Copilot returns a marked worker status with the provider alias `status: "success"`, the launcher normalizes it to canonical `pass` before schema validation, without adding command evidence. If Gemini Pro, Gemini Flash, Copilot, Spark, or mini leaves dirty partial work without a valid `status.json`, the launcher refuses fallback, writes `fallback.blocked.txt`, and writes a terminal blocked `status.json`. If all attempts fail cleanly, the launcher writes a terminal blocked `status.json`.

After launching worker packets, wait for the launcher processes to finish. If a worker launcher is still active, do not poll its worktree, event logs, process table, or `status.json`, and do not send status nudges. Inspect worker status files and diffs only after the launcher exits, a worker reports `blocked`/`failed`/`partial`, or the user explicitly enters debug mode.

## Reviewer Packet

After integrating worker results and running branch-level checks, dispatch a read-only reviewer:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py" \
  --role reviewer \
  --packet-id B01-R01 \
  --branch <branch-name> \
  --worktree /absolute/path/to/.worktrees/<branch-name> \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/reviewers \
  --context-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.prompt.md
```

Reviewer launchers use `gpt-5.5` first and fall back to `gpt-5.4`, read-only.

After launching a reviewer packet, wait for the reviewer launcher to finish. If it is still active, do not poll `events-*.jsonl`, process tables, or `review.json`; a quiet read-only reviewer is not evidence of a stall. Inspect reviewer artifacts only after the launcher exits, returns nonzero, or the user explicitly enters debug mode.

## Branch Status Validation

Before returning, write the branch status JSON to the expected branch status path and validate it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/validate_branch_status.py" \
  --status /absolute/path/to/branches/B01.status.json \
  --manifest /absolute/path/to/job.manifest.json \
  --branch-id B01 \
  --branch <branch-name> \
  --worktree /absolute/path/to/.worktrees/<branch-name>
```

If validation fails, fix the status artifact or return `blocked`; do not claim `pass` with an invalid branch status. A passing or partial branch status must include exactly one worker status for every manifest work item packet id and no extra worker packet ids. A passing branch status must include only `pass` worker statuses backed by existing manifest-owned `workers/<packet_id>/status.json` artifacts, `review_status: "mergeable"` backed by the manifest review artifact, reviewer packet ids for the same branch, exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, a `lite_advice` array (empty when no Lite was used; manifest-owned auditable records otherwise, with `validation_status` and `validation_defects` matching actual validation), a non-empty DoD checklist, and no blockers.

## Completion Gate

Before returning `pass`, verify:

- skill and CLI availability bootstrap passed;
- every manifest worker status is `pass`;
- 1 to 4 worker packets were used for the branch;
- no more than 4 active worker packets ran at once, and branch status records the worker parallelism cap, concurrent launch evidence, and any serial/under-capacity reason;
- accepted worker branches have clean `git diff --check`;
- focused tests and validators named in the branch prompt ran and are recorded;
- base-range whitespace validation such as `git diff --check <base-ref>...HEAD` ran and is recorded before review or merge readiness;
- reviewer verdict is `mergeable`;
- reviewer verification gaps are empty for `mergeable`;
- branch orchestration did not poll active worker/reviewer launchers' event logs, process tables, status files, review files, or worktrees while waiting;
- unsupported, unresolved, negative, or probe-only labels are preserved;
- branch status file records changed files, commands, tests, blockers, worker parallelism, and final DoD checklist.
- manifest-bound `validate_branch_status.py` passed for the final branch status file.
- `lite_advice` records are present, even when empty; any Lite advice used was validated and treated only as advisory context routing, not worker/review/DoD evidence.

If evidence is missing, return `partial` or `blocked`, not `pass`.

Read `references/branch-runtime-contract.md` for status shape, integration rules, and context-conservation guidance. Read `references/lite-advisor-contract.md` before creating Lite packets.
