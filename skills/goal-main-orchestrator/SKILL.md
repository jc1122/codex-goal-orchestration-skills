---
name: goal-main-orchestrator
version: 0.2.88
description: Runtime-only main orchestrator for prepared /goal job bundles. Use when a /goal session has been launched from a goal-preflight bootloader and must consume existing job.manifest.json, main.prompt.md, and branch prompts; first run skill availability bootstrap and fail-closed prompt audit with telemetry, optionally use CLI-only Lite advisors after audit or completed branch artifacts for advisory summaries, then create path-validated branch worktrees, dispatch goal-branch-orchestrator sessions within the hard agent limit, summarize packet telemetry, and finish only when the main prompt's falsifiable Definition of Done is satisfied.
---

# Goal Main Orchestrator

Runtime wrapper only. Do not implement branch work, rewrite preflight artifacts, or absorb `goal-branch-orchestrator` instructions into main context.

## Start

Resolve the installed skills root:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-main-orchestrator" ] && [ -d "$HOME/.agents/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Use `job.manifest.json`, `main.prompt.md`, `prompt-audit.json`, scheduler JSON, branch status/review JSON, telemetry JSON, and validator defects as the working surface.
- In debug telemetry mode, `summarize_telemetry.py` writes `telemetry.debug.summary.json` and root `run.trace.jsonl`; use those for efficiency, stall, fallback, and token-pressure analysis before raw event logs.
- Run the model catalog phase before choosing or launching model routes.
- Prompt audit must pass before branch worktree creation or branch dispatch.
- For resumable or interrupted runs, run `reconcile_goal_run.py` first to materialize `orchestration.state.json`/`resume.report.json`, then launch only work that is safe to resume.
- Keep branch orchestrator slots saturated up to `max_active_branch_agents`; waves are scheduling order, not dependency barriers.
- Wait on active branch agents; do not poll branch worktrees, worker packets, reviewer packets, or logs while branch agents are active.
- Branch/reviewer status promotion and launch-config integrity are validator-enforced; treat `assemble_branch_status.py`/`validate_branch_status.py` output as the source of truth before relaunching branch work.
- After each dispatch loop and before final reporting, reconcile terminal evidence and run `validate_main_status.py` on a freshly assembled `main.status.json`.
- Use `goal-plan-amender` only after validated terminal branch evidence and only for future unstarted work.
- Do not read or search `skills/*/scripts/*.py` during normal runtime, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open detailed references only after a phase script or validator points at an ambiguity:

- `references/prompt-audit-contract.md` for prompt-audit artifact semantics.
- `references/main-runtime-contract.md` for `validate_main_status.py` defects.
- `references/lite-advisor-contract.md` before creating Lite packets.
