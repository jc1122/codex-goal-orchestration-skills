---
name: goal-branch-orchestrator
description: Runtime-only branch orchestrator for an audited branch prompt and existing branch worktree. Use when a main orchestrator has already passed prompt audit, created a branch integration worktree, and launched a branch session that must dispatch granular Spark-first CLI workers, integrate their results, dispatch a read-only reviewer, and return only when the branch prompt's falsifiable Definition of Done is satisfied or blocked.
---

# Goal Branch Orchestrator

## Role Boundary

Act as a branch orchestrator only. Do not create or rewrite `main.prompt.md`, branch prompt files, the `/goal` bootloader, or `job.manifest.json`. Do not create the branch integration worktree; the main orchestrator owns that.

Your job is:

1. Read the assigned branch prompt file.
2. Verify the global prompt audit passed.
3. Create granular worker packets and worker child worktrees as needed.
4. Launch Spark-first workers through `codex exec`.
5. Inspect worker status, diffs, and focused verification evidence.
6. Dispatch a read-only heavy-model reviewer.
7. Return branch status only when the branch prompt DoD is satisfied or explicitly blocked.

## Required Start

Before dispatching workers:

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

If any check fails, do not launch workers. Return `blocked`.

## Worker Packets

Workers must be granular enough for Spark's 128k context window. Keep each packet below roughly 80k-100k total input context by using:

- one objective;
- narrow owned files/modules;
- a short read-first list;
- exact verification commands;
- a falsifiable worker DoD;
- required JSON status output.

Use `scripts/create_runtime_packet.py` to create worker packets:

```bash
python3 /home/jakub/.agents/skills/goal-branch-orchestrator/scripts/create_runtime_packet.py \
  --role worker \
  --packet-id B01-W01 \
  --branch <branch-name>-W01 \
  --worktree /absolute/path/to/.worktrees/<branch-name>-W01 \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/workers \
  --owned-file src/example.py \
  --context-file branches/B01.prompt.md
```

The generated worker launcher uses `gpt-5.3-codex-spark` first and falls back to `gpt-5.4-mini` only if no status was produced and the worker worktree stayed clean. If Spark leaves dirty partial work without a status file, the launcher refuses fallback and writes `fallback.blocked.txt`.

## Reviewer Packet

After integrating worker results and running branch-level checks, dispatch a read-only reviewer:

```bash
python3 /home/jakub/.agents/skills/goal-branch-orchestrator/scripts/create_runtime_packet.py \
  --role reviewer \
  --packet-id B01-R01 \
  --branch <branch-name> \
  --worktree /absolute/path/to/.worktrees/<branch-name> \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/reviewers \
  --context-file branches/B01.prompt.md
```

Reviewer launchers use `gpt-5.5` first and fall back to `gpt-5.4`, read-only.

## Completion Gate

Before returning `pass`, verify:

- every worker needed by the branch DoD has status `pass` or an explicitly acceptable `partial`;
- accepted worker branches have clean `git diff --check`;
- focused tests and validators named in the branch prompt ran and are recorded;
- reviewer verdict is `mergeable` or the branch prompt defines an acceptable weaker state;
- unsupported, unresolved, negative, or probe-only labels are preserved;
- branch status file records changed files, commands, tests, blockers, and final DoD checklist.

If evidence is missing, return `partial` or `blocked`, not `pass`.

Read `references/branch-runtime-contract.md` for status shape, integration rules, and context-conservation guidance.
