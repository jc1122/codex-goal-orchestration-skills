---
name: goal-branch-orchestrator
description: Runtime-only branch orchestrator for an audited branch prompt and existing branch worktree. Use when goal-main-orchestrator has passed prompt audit, created a branch integration worktree, and launched a branch session that must run skill/CLI bootstrap, optionally use CLI-only Lite advisors for packet planning/context packing/completed-worker summaries/blocked triage, create path-safe worker/research-worker/reviewer packets with telemetry, choose allowed per-worker routes from the Gemini Pro -> Gemini Flash -> Codex Spark -> GitHub Copilot gpt-5.4 -> Codex mini ladder for normal workers, keep worker launcher slots saturated with ready workers, integrate results, dispatch a read-only heavy-model reviewer, and return only when the branch prompt's falsifiable Definition of Done is satisfied or blocked.
---

# Goal Branch Orchestrator

## Role Boundary

Act as a branch orchestrator only. Do not create or rewrite `main.prompt.md`, branch prompt files, the `/goal` bootloader, or `job.manifest.json`. Do not create the branch integration worktree; the main orchestrator owns that.

Your job is:

1. Run the skill and CLI availability bootstrap.
2. Read the assigned branch prompt file.
3. Verify the global prompt audit passed.
4. Optionally use Lite advisors for packet planning or context routing after required start checks pass.
5. Create granular worker, research-worker, and reviewer packets as needed.
6. Launch independent worker packets as a rolling saturated pool when their owned paths and verification commands do not conflict, using normal workers for code/config/test/doc edits and `research-worker` packets for outside-information gathering.
7. Inspect worker/research status, diffs, and focused verification evidence.
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

Lite advisors are optional context routers, not workers, reviewers, or authorities. Branch may launch Lite only after required start checks pass and never while worker/research-worker/reviewer launchers are active:

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

After running the generated `launch.sh`, validate `advice.json` with `scripts/validate_lite_advice.py`. If Lite is blocked, invalid, stale, or contradicted by worker artifacts or original files, ignore it. The branch Lite scripts enforce the branch-only purpose allowlist (`branch-packet-planning`, `context-pack`, `worker-summary`, `blocked-triage`) and branch-scoped packet ids (`<branch-id>-L<suffix>`), capture the absolute Gemini CLI path/version/binary sha256 at packet creation, rehash all source inputs, `task.md`, `prompt.md`, and the Gemini binary during launch/validation, regenerate the prompt from `input-files.json` plus `task.md`, write packet-local `telemetry.json`, and reject runtime-purpose recommendations outside the explicit input set. Branch status validation scans manifest-owned `lite/` for relevant branch Lite packet directories and fails if they are not recorded in `lite_advice`; recorded Lite validation commands must be the exact `python3 <skill>/scripts/validate_lite_advice.py --advice <packet>/advice.json --inputs <packet>/input-files.json` command for that manifest-owned packet.

## Worker Packets

Workers must fit the smallest intended worker context across the fixed fallback chain, so keep packets below roughly 80k-100k total input context by using:

- one objective;
- narrow owned files/modules;
- a short read-first list;
- exact verification commands;
- a falsifiable worker DoD;
- required JSON status output.

Parallel worker packets are the default for independent work items. Use separate child worktrees for normal workers that can proceed without sharing writable files. A branch uses 1 to 4 prepared worker packets total, including research-worker packets. Launch independent worker packets as a rolling saturated pool up to the branch prompt's `max_active_worker_packets` value. That value is a hard cap and must never exceed 4. When any worker launcher exits, collect and integrate its status/diff or research findings, remove it from the active set, and launch the next eligible worker immediately if capacity is available. Defer a worker only while one of its manifest work-item `depends_on` ids is incomplete. If branch work must run serially or below the worker cap, record the reason in `worker_parallelism.serial_reasons` rather than silently serializing it.

Use `scripts/render_worker_schedule.py` to list currently ready worker packet ids as active workers start/finish:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/render_worker_schedule.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --branch-id B01 \
  --list-ready \
  --completed-worker B01-W01 \
  --active-worker B01-W02 \
  --limit 2
```

Track active worker launcher process ids and packet ids. If active worker count is below `max_active_worker_packets`, run the ready-list command and launch returned worker packets until capacity is full or no eligible worker remains. If no worker is ready and at least one worker is active, wait. If no worker is ready, none is active, and manifest work items remain incomplete, return `blocked` with the unresolved dependency or failed-worker reason.

Use `scripts/create_runtime_packet.py` to create worker packets. The default route is the standard ladder:

1. `gemini-pro`
2. `gemini-flash`
3. `codex-spark`
4. `copilot-gpt-5.4`
5. `codex-mini`

The branch orchestrator may choose a narrower non-empty ordered subsequence for a worker packet when task hardness, context size, quota pressure, or provider availability justify it. Do not reorder aliases, invent aliases, or use reviewer/auditor models for workers. If you pass any `--worker-route`, also pass a non-empty `--selection-reason`; the packet generator writes `route.json`, and worker/branch status validation rejects route drift.

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py" \
  --role worker \
  --packet-id B01-W01 \
  --branch <branch-name>-W01 \
  --worktree /absolute/path/to/.worktrees/<branch-name>-W01 \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/workers \
  --owned-file src/example.py \
  --context-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.prompt.md \
  --worker-route codex-spark \
  --worker-route copilot-gpt-5.4 \
  --selection-reason "Bounded implementation packet; preserve Gemini quota and prefer Spark before Copilot."
```

Use `--role research-worker` only for research-only packets that need broad read-only information retrieval and local file reads but must not edit files:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py" \
  --role research-worker \
  --packet-id B01-W01 \
  --branch <branch-name>-W01 \
  --worktree /absolute/path/to/.worktrees/<branch-name>-W01 \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/research \
  --owned-file docs/example.md \
  --context-file /absolute/path/to/plans/orchestration/<job-id>/branches/B01.prompt.md
```

Research workers use `codex --search exec --ephemeral -s read-only` without user-config suppression, so they may use Codex native search, configured read-only CLI/MCP/connector/browser/search tools, package metadata lookups, remote APIs, shell/network inspection commands, read-only local file access, and configured tool/skill documentation when relevant. They must not edit files, inspect secrets or unrelated private files, or perform state-changing, destructive, credential, posting, purchasing, or remote mutation actions. A passing research status must capture `search_queries` when search was used, `source_urls`, `tools_used`, `local_files_read`, exact `commands_run`, and findings. Research-worker statuses belong in the branch `worker_statuses` rollup with `role: "research-worker"` and `status_path` pointing to manifest-owned `research/<packet_id>/research.json`; validation rejects obvious state-changing commands, package/system mutation, file writes, shell redirection to files, environment dumps, and secret/credential path reads.

The packet generator enforces absolute `--worktree`, `--out-dir`, `--task-file`, and `--context-file` paths. Worker prompts render worktree-local context files as relative paths and embed out-of-worktree context snapshots so workspace-restricted CLIs do not need to read bundle paths outside the worker worktree. Generated worker launchers use the selected ordered route; the default is Gemini CLI with `gemini-3.1-pro-preview`, Gemini CLI with `gemini-3-flash-preview`, `gpt-5.3-codex-spark`, GitHub Copilot CLI with `gpt-5.4` and `--effort high`, then `gpt-5.4-mini`. No model, effort, approval-mode, or permission overrides are accepted. All full launcher attempts run through `timeout --foreground --kill-after=30s`: normal workers default to 3600 seconds per route attempt, research workers to 1200 seconds, reviewers to 1800 seconds, prompt audit to 1200 seconds, and Lite advisors to 600 seconds. Timeout is a failed attempt, not a reason to poll active logs; the launcher tries the next allowed fallback only when no valid artifact exists and, for write-capable workers, the worker worktree is clean. There is no same-alias retry loop. All worker providers receive the same generated prompt and must satisfy the same worker status schema, including `selected_ladder` and `selection_reason`; CLI permission controls are provider-specific, so acceptance still depends on schema validation, clean fallback boundaries, branch diff inspection, and branch-level tests. Before each full Gemini worker attempt, the launcher runs a 20-second headless probe with the same Gemini model. Before the full Copilot worker attempt, the launcher runs a 20-second no-tool probe with `gpt-5-mini` and `--effort low` to verify Copilot CLI/auth/routing without spending a `gpt-5.4` call. Gemini and Copilot are best-effort: if the command is unavailable, quota-limited, unavailable, timed out, or fails without dirtying the worker worktree, the launcher continues to the next selected worker. Copilot runs the real worker in programmatic mode with `gpt-5.4`, `--effort high`, minimal tool permissions, JSONL events, and a Markdown session share; because Copilot has no local `--output-schema` equivalent, the launcher accepts only the marked final worker JSON and still requires orchestrator diff/test verification. Each terminal launcher path writes `telemetry.json` with declared/called/accepted route aliases, provider/model ids, prompt/output/log character and byte counts, `timeout_seconds`, and best-effort token usage parsed from provider logs when exposed. If Gemini or Copilot returns a marked worker status with the provider alias `status: "success"`, the launcher normalizes it to canonical `pass` before schema validation, without adding command evidence. If Gemini Pro, Gemini Flash, Spark, Copilot, or mini leaves dirty partial work without a valid `status.json`, the launcher refuses fallback, writes `fallback.blocked.txt`, writes a terminal blocked `status.json`, and writes `telemetry.json`. If all selected attempts fail cleanly, the launcher writes a terminal blocked `status.json` and `telemetry.json`.

After launching worker packets, wait for the next launcher process to finish. If a worker or research-worker launcher is still active, do not poll its worktree, event logs, process table, `status.json`, or `research.json`, and do not send status nudges. Inspect worker status files, research files, and diffs only after the launcher exits, a worker reports `blocked`/`failed`/`partial`, or the user explicitly enters debug mode. Once an exited worker is integrated, free its active slot and immediately refill from the ready worker queue when possible.

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

Reviewer launchers use `gpt-5.5` first and fall back to `gpt-5.4`, read-only, and write packet-local `telemetry.json` on every terminal path.

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

If validation fails, fix the status artifact or return `blocked`; do not claim `pass` with an invalid branch status. A passing or partial branch status must include exactly one worker status for every manifest work item packet id and no extra worker packet ids. Normal worker statuses are backed by manifest-owned `workers/<packet_id>/status.json`; research-worker statuses are backed by manifest-owned `research/<packet_id>/research.json`. A passing branch status must include only `pass` worker/research-worker statuses with same-packet `telemetry.json`, `review_status: "mergeable"` backed by the manifest review artifact and reviewer `telemetry.json`, reviewer packet ids for the same branch, exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, a `lite_advice` array (empty only when no relevant branch Lite packet exists; manifest-owned auditable records otherwise, with `validation_status` and `validation_defects` matching actual validation), a non-empty DoD checklist, and no blockers.

## Completion Gate

Before returning `pass`, verify:

- skill and CLI availability bootstrap passed;
- every manifest worker status is `pass`;
- every worker, research-worker, reviewer, and used/ignored Lite packet wrote `telemetry.json`;
- 1 to 4 worker packets were used for the branch;
- no more than 4 active worker packets ran at once, and branch status records the worker parallelism cap, rolling scheduling mode, concurrent launch evidence, refill events when replacements were needed, and any serial/under-capacity reason;
- every worker status records `selected_ladder` and `selection_reason`, and any non-default route is justified by task hardness, context size, quota pressure, or provider availability;
- every research-worker status records search queries when search was used, source URLs, tool families used, local files read, exact local/shell inspection commands, and passes read-only security validation;
- launcher telemetry records positive `timeout_seconds` for every declared model attempt;
- accepted worker branches have clean `git diff --check`;
- focused tests and validators named in the branch prompt ran and are recorded;
- base-range whitespace validation such as `git diff --check <base-ref>...HEAD` ran and is recorded before review or merge readiness;
- reviewer verdict is `mergeable`;
- reviewer verification gaps are empty for `mergeable`;
- branch orchestration did not poll active worker/research-worker/reviewer launchers' event logs, process tables, status files, review files, or worktrees while waiting;
- unsupported, unresolved, negative, or probe-only labels are preserved;
- branch status file records changed files, commands, tests, blockers, worker parallelism, and final DoD checklist.
- manifest-bound `validate_branch_status.py` passed for the final branch status file.
- `lite_advice` records are present, even when empty; any Lite advice used was validated and treated only as advisory context routing, not worker/review/DoD evidence.

If evidence is missing, return `partial` or `blocked`, not `pass`.

Read `references/branch-runtime-contract.md` for status shape, integration rules, and context-conservation guidance. Read `references/lite-advisor-contract.md` before creating Lite packets.
