---
name: goal-main-orchestrator
description: Runtime-only main orchestrator for prepared /goal job bundles. Use when a Copilot /goal session has been launched from a goal-preflight bootloader and must consume existing job.manifest.json, main.prompt.md, and branch prompts; first run skill availability bootstrap and fail-closed prompt audit, then create path-validated branch worktrees, dispatch goal-branch-orchestrator sessions within the hard agent limit, and finish only when the main prompt's falsifiable Definition of Done is satisfied.
---

# Goal Main Orchestrator

## Role Boundary

Act as the runtime main orchestrator only. Do not create `/goal` bootloader text, `main.prompt.md`, branch prompt files, or `job.manifest.json`; a separate prompt-prep skill owns those artifacts.

Your job is:

1. Run the skill availability bootstrap.
2. Read the prepared `job.manifest.json` and `main.prompt.md`.
3. Dispatch a read-only heavy-model prompt auditor before any branch work starts.
4. Create branch integration worktrees only after the audit passes, one wave at a time when waves are present.
5. Launch all branch orchestrators in the current wave concurrently without exceeding the hard active-agent limit.
6. Review branch status/review artifacts against `main.prompt.md` DoD.
7. Return `pass` only when the DoD is falsifiably satisfied.

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
  --require goal-branch-orchestrator
```

If this fails, stop immediately and return `blocked` with the missing skill/script names. Do not run prompt audit or create worktrees until bootstrap passes.

## Mandatory Start

After bootstrap passes, run prompt audit. Do not create branches, worktrees, branch orchestrators, workers, reviewers, commits, or merges before `prompt-audit.json` says audit passed and `can_start` is true.

Use the bundle root and repository root from the bootloader. Manifest prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root. Treat absolute paths, backslashes, and `..` traversal in manifest-owned paths as `blocked`.

Use `scripts/create_audit_packet.py` to create the audit packet:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/create_audit_packet.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --repo-root /absolute/path/to/repo \
  --out-dir /absolute/path/to/plans/orchestration/<job-id>/audit
```

Then run the generated `launch.sh`. The audit packet uses exactly `gpt-5.5`, then `gpt-5.4`; no model overrides are accepted. The packet schema pins the exact manifest path and repository root. If both audit attempts fail without producing a valid `prompt-audit.json`, the launcher writes a terminal blocked `prompt-audit.json`.

Read `references/prompt-audit-contract.md` if the audit fails or if the prepared bundle shape is unclear.

## Branch Creation

After audit passes, create branch integration worktrees from the manifest. If the manifest has `waves`, render and run one wave at a time. Use `scripts/render_branch_worktree_commands.py` to print the exact `git worktree add` commands:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/render_branch_worktree_commands.py" \
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

Launch branch orchestrators according to manifest waves. Parallelism is the default: launch every branch in the current wave concurrently up to `max_active_branch_agents`, then wait for that wave to finish before launching the next wave. Respect `max_active_branch_agents`; it must be treated as a hard limit and must not exceed 4. Each branch orchestrator must use the `goal-branch-orchestrator` skill and receive:

- the branch id;
- branch prompt path;
- branch integration branch name;
- branch worktree path;
- manifest path;
- prompt audit path;
- expected branch status path;
- expected branch review path.

Before dispatching a branch, verify its manifest entry declares 1 to 4 `work_items`, `max_active_worker_packets` from 1 to 4, and `worker_parallelism.parallelism_default=true`. If not, return `blocked`; do not let a branch session infer missing worker-packet policy.

The main orchestrator should not implement branch work itself and should not inspect worker event logs unless a branch status is missing, inconsistent, or blocked.

Do not open or read `goal-branch-orchestrator/SKILL.md` in the main orchestrator context. Treat that skill as a branch-session launch target: verify it exists during bootstrap, then dispatch a branch orchestrator session that loads and follows it with the inputs above. If branch-session launch is impossible, return `blocked` instead of absorbing the branch runtime instructions into main.

After dispatch, use the native agent wait mechanism with the longest practical timeout. If a wait returns with no completed branch agents, do not poll branch worktrees, worker packets, reviewer packets, process tables, or status files, and do not send status-check nudges. Continue waiting unless the user explicitly enters debug mode or a branch agent returns `blocked`/`failed`/`partial`.

Track active branch orchestrator agent ids/processes. As each branch in a wave finishes, collect its status/review artifacts, then close or turn off the finished branch orchestrator. Launch the next wave only after the current wave is collected and capacity is freed. If a finished branch orchestrator cannot be closed and capacity cannot be freed, stop and return `blocked` instead of exceeding the active-agent limit.

## Completion Gate

Before returning `pass`, verify:

- skill availability bootstrap passed for `goal-main-orchestrator` and `goal-branch-orchestrator`;
- prompt audit passed;
- manifest cleanup and artifact policies are present and are not contradicted by `main.prompt.md`;
- every branch listed in the manifest has a status file;
- every branch requiring review has a review file;
- branch statuses satisfy the main prompt DoD;
- branch statuses/reviews record base-range whitespace validation before merge readiness;
- branch statuses record the branch worker-packet cap and concurrent worker launch evidence or a serial/under-capacity reason;
- no branch wave exceeded `max_active_branch_agents`;
- main did not poll active branch agents' worker packets, reviewer packets, worktrees, or process tables while waiting;
- finished branch orchestrators were closed before replacements launched;
- required commands and validators are recorded;
- unresolved, unsupported, negative, or probe-only labels are preserved;
- final git state matches the main prompt's merge/cleanup policy.

If any item is missing or unverifiable, return `partial` or `blocked`, not `pass`.

Read `references/main-runtime-contract.md` for the full status contract and context-conservation rules.
