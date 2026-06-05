---
name: goal-plan-amender
version: 0.2.98
description: "Runtime-only plan amender for audited /goal bundles. Use only when goal-main-orchestrator has validated a terminal branch result and needs amendment proposals or deterministic blocker-repair proposals; create file-backed packets, validate safe manifest amendments, and apply accepted amendments without changing active or terminal branch evidence."
---

# Goal Plan Amender

Runtime amendment wrapper only. Do not launch branches, create worktrees, dispatch workers/reviewers, edit scheduler ledgers, inspect active branch internals, decide audit/branch pass-fail, or mark the run `pass`.

## Start

Resolve the installed skills root:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-plan-amender" ] && [ -d "$HOME/.agents/skills/goal-plan-amender" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Use this skill only after main validates terminal branch evidence and closes/removes that branch from the active set.
- Active and terminal branch prompts, worktrees, status paths, review paths, dependencies, owned paths, scheduler ledgers, and runtime artifacts are immutable.
- Prefer deterministic `recommend_amendment_decision.py` and `create_blocker_repair_packet.py` when their conditions apply.
- Model packets must be route-bound, read-only, validated, and limited to future unstarted manifest work.
- Apply only after packet and proposal validation pass.
- Do not read or search `skills/*/scripts/*.py` during normal runtime, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open `references/amendment-contract.md` only when amendment validation defects need interpretation.
