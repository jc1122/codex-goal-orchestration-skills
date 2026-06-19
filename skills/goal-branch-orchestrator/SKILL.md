---
name: goal-branch-orchestrator
version: 0.2.111
description: Runtime-only branch orchestrator for an audited branch prompt and existing branch worktree. Use when goal-main-orchestrator has passed prompt audit, created a branch integration worktree, and launched a branch session that must run skill/CLI bootstrap, optionally use CLI-only Lite advisors for packet planning/context packing/completed-worker summaries/blocked triage, create path-safe worker/research-worker/reviewer packets with telemetry, choose allowed per-worker routes from the ds-pro-max -> ds-flash-max -> codex-spark -> codex-mini ladder (bridge deepseek leading, native Codex fallback) for normal workers, keep worker launcher slots saturated with ready workers, integrate results, dispatch a read-only heavy-model reviewer, and return only when the branch prompt's falsifiable Definition of Done is satisfied or blocked.
---

# Goal Branch Orchestrator

Runtime wrapper for one audited branch worktree. Do not create the branch integration worktree, rewrite manifest/prompts, launch plan-amender, or do worker implementation yourself.

## Start

Resolve the installed skills root:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-branch-orchestrator" ] && [ -d "$HOME/.agents/skills/goal-branch-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Use generated packets, route JSON, scheduler JSON, status/review JSON, telemetry JSON, diffs, tests, and validator defects as the working surface.
- Verify prompt audit passed and run the model catalog phase before choosing worker routes.
- Launch independent worker packets as a rolling saturated pool up to `max_active_worker_packets`.
- Preserve the manifest-configured worker route-class ladder by default. Do not pass `--allow-route-pruning` unless a validator, model catalog, route-health artifact, explicit operator request, timeout, budget cap, or provider failure justifies the shorter ladder with a concrete reason.
- Create reviewer packets only when pre-review validation succeeds and the branch status has no unresolved blocker evidence.
- Reviewer promotion is validator-owned: use `validate_branch_status.py`/`create_pre_review_gate.py` to ensure the branch review artifact is promoted to the canonical branch review path and launcher config route policy is consistent.
- If deterministic branch repair was command-verified after worker route failures, promote it through `promote_worker_repair_evidence.py` before pre-review; do not commit repair code while leaving the canonical worker status blocked.
- Use separate child worktrees for write-capable workers when owned paths do not conflict.
- Do not poll active worker/research/reviewer packet logs. Inspect artifacts only after launchers exit or return terminal status.
- Use `context_pack.py`/packet context excerpts instead of broad reads.
- In CLI branch-control mode, do not read, tail, grep, cat, or inspect your own redirected `branches/Bxx.codex.log` or `branches/Bxx.codex.final.md`. Do not read memory files or produce memory citations. If worker scheduler evidence has ready unlaunched work, create/run the worker packet; do not self-monitor. Finish only after writing and validating `branches/Bxx.status.json`, or after writing validator-visible blocked/partial branch evidence.
- Do not read or search `skills/*/scripts/*.py` during normal orchestration, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open detailed references only after a phase script or validator points at an ambiguity:

- `references/branch-runtime-contract.md` for `validate_branch_status.py` defects.
- `references/lite-advisor-contract.md` before creating Lite packets.
