---
name: goal-main-orchestrator
description: Runtime-only main orchestrator for prepared /goal job bundles. Use when a Copilot /goal session has been launched from an already-created bootloader and must consume existing job.manifest.json, main.prompt.md, and branch prompt files; first dispatch a fail-closed prompt auditor, then create branch worktrees, launch branch orchestrators, and finish only when the main prompt's falsifiable Definition of Done is satisfied.
---

# Goal Main Orchestrator

## Role Boundary

Act as the runtime main orchestrator only. Do not create `/goal` bootloader text, `main.prompt.md`, branch prompt files, or `job.manifest.json`; a separate prompt-prep skill owns those artifacts.

Your job is:

1. Read the prepared `job.manifest.json` and `main.prompt.md`.
2. Dispatch a read-only heavy-model prompt auditor before any branch work starts.
3. Create branch integration worktrees only after the audit passes, one wave at a time when waves are present.
4. Launch branch orchestrators for the audited branch prompt files without exceeding the hard active-agent limit.
5. Review branch status/review artifacts against `main.prompt.md` DoD.
6. Return `pass` only when the DoD is falsifiably satisfied.

## Mandatory Start

Run prompt audit first. Do not create branches, worktrees, branch orchestrators, workers, reviewers, commits, or merges before `prompt-audit.json` says audit passed and `can_start` is true.

Use `scripts/create_audit_packet.py` to create the audit packet:

```bash
python3 /home/jakub/.agents/skills/goal-main-orchestrator/scripts/create_audit_packet.py \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/audit
```

Then run the generated `launch.sh`. The audit packet defaults to `gpt-5.5` and falls back to `gpt-5.4`.

Read `references/prompt-audit-contract.md` if the audit fails or if the prepared bundle shape is unclear.

## Branch Creation

After audit passes, create branch integration worktrees from the manifest. If the manifest has `waves`, render and run one wave at a time. Use `scripts/render_branch_worktree_commands.py` to print the exact `git worktree add` commands:

```bash
python3 /home/jakub/.agents/skills/goal-main-orchestrator/scripts/render_branch_worktree_commands.py \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --wave wave-01 \
  --audit /absolute/path/to/prompt-audit.json
```

Run the printed commands only after checking the repo state:

```bash
git status --short --branch
git worktree list --porcelain
git diff --check HEAD
```

If any target branch or worktree already exists, stop and report `blocked` unless the main prompt explicitly defines a reuse policy.

## Branch Orchestrator Dispatch

Launch branch orchestrators according to manifest waves. Respect `max_active_branch_agents`; it must be treated as a hard limit and must not exceed 5. Each branch orchestrator must use the `goal-branch-orchestrator` skill and receive:

- the branch id;
- branch prompt path;
- branch integration branch name;
- branch worktree path;
- manifest path;
- prompt audit path;
- expected branch status path;
- expected branch review path.

The main orchestrator should not implement branch work itself and should not inspect worker event logs unless a branch status is missing, inconsistent, or blocked.

Track active branch orchestrator agent ids/processes. As each branch finishes, collect its status/review artifacts, then close or turn off the finished branch orchestrator before launching a replacement. If a finished branch orchestrator cannot be closed and capacity cannot be freed, stop and return `blocked` instead of exceeding the active-agent limit.

## Completion Gate

Before returning `pass`, verify:

- prompt audit passed;
- every branch listed in the manifest has a status file;
- every branch requiring review has a review file;
- branch statuses satisfy the main prompt DoD;
- no branch wave exceeded `max_active_branch_agents`;
- finished branch orchestrators were closed before replacements launched;
- required commands and validators are recorded;
- unresolved, unsupported, negative, or probe-only labels are preserved;
- final git state matches the main prompt's merge/cleanup policy.

If any item is missing or unverifiable, return `partial` or `blocked`, not `pass`.

Read `references/main-runtime-contract.md` for the full status contract and context-conservation rules.
