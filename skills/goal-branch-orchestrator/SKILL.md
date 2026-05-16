---
name: goal-branch-orchestrator
description: Runtime-only branch orchestrator for an audited branch prompt and existing branch worktree. Use when goal-main-orchestrator has passed prompt audit, created a branch integration worktree, and launched a branch session that must run skill/CLI bootstrap, create path-safe worker/reviewer packets, dispatch granular Gemini Pro/Flash-first workers with Codex Spark and 5.4-mini fallback, integrate results, dispatch a read-only heavy-model reviewer, and return only when the branch prompt's falsifiable Definition of Done is satisfied or blocked.
---

# Goal Branch Orchestrator

## Role Boundary

Act as a branch orchestrator only. Do not create or rewrite `main.prompt.md`, branch prompt files, the `/goal` bootloader, or `job.manifest.json`. Do not create the branch integration worktree; the main orchestrator owns that.

Your job is:

1. Run the skill and CLI availability bootstrap.
2. Read the assigned branch prompt file.
3. Verify the global prompt audit passed.
4. Create granular worker packets and worker child worktrees as needed.
5. Launch independent worker packets concurrently when their owned paths and verification commands do not conflict, using Gemini Pro/Flash-first workers with Codex Spark and mini fallback.
6. Inspect worker status, diffs, and focused verification evidence.
7. Dispatch a read-only heavy-model reviewer.
8. Return branch status only when the branch prompt DoD is satisfied or explicitly blocked.

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

## Worker Packets

Workers must fit the smallest intended worker context. Spark fallback has a local context window of about 128k tokens, so keep packets below roughly 80k-100k total input context by using:

- one objective;
- narrow owned files/modules;
- a short read-first list;
- exact verification commands;
- a falsifiable worker DoD;
- required JSON status output.

Parallel worker packets are the default for independent work items. Use separate child worktrees for workers that can proceed without sharing writable files. If branch work must run serially, record the reason in branch status rather than silently serializing it.

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

The packet generator enforces absolute `--worktree`, `--out-dir`, `--task-file`, and `--context-file` paths. Generated worker launchers use exactly this fixed order: Gemini CLI with `gemini-3.1-pro-preview`, Gemini CLI with `gemini-3-flash-preview`, `gpt-5.3-codex-spark`, then `gpt-5.4-mini`. No model or approval-mode overrides are accepted. Before each full Gemini worker attempt, the launcher runs a 20-second headless probe with the same model so renamed, retired, unauthorized, or quota-blocked model IDs fail while the worktree is still clean. Gemini is best-effort: if the Gemini command is unavailable, quota-limited, unavailable, or fails without dirtying the worker worktree, the launcher continues to the next worker. If Gemini Pro, Gemini Flash, or Spark leaves dirty partial work without a valid `status.json`, the launcher refuses fallback, writes `fallback.blocked.txt`, and writes a terminal blocked `status.json`. If all attempts fail cleanly, the launcher writes a terminal blocked `status.json`.

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

## Completion Gate

Before returning `pass`, verify:

- skill and CLI availability bootstrap passed;
- every worker needed by the branch DoD has status `pass` or an explicitly acceptable `partial`;
- accepted worker branches have clean `git diff --check`;
- focused tests and validators named in the branch prompt ran and are recorded;
- base-range whitespace validation such as `git diff --check <base-ref>...HEAD` ran and is recorded before review or merge readiness;
- reviewer verdict is `mergeable` or the branch prompt defines an acceptable weaker state;
- unsupported, unresolved, negative, or probe-only labels are preserved;
- branch status file records changed files, commands, tests, blockers, and final DoD checklist.

If evidence is missing, return `partial` or `blocked`, not `pass`.

Read `references/branch-runtime-contract.md` for status shape, integration rules, and context-conservation guidance.
