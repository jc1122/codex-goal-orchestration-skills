---
name: goal-preflight
version: 0.2.47
description: "Prepare path-hardened /goal orchestration bundles from a report, roadmap, diagnosis, or rough goal brief. Use when the user needs prompt infrastructure for goal-main-orchestrator: optionally use CLI-only Lite advisors for source digestion or lint-repair advice, synthesize rolling-scheduled branch groups and worker-sized work items when missing, enforce reproducible manifest paths and telemetry requirements, write job.manifest.json/main.prompt.md/branch prompts/location-bound goal-bootloader.md, run deterministic lint, and present the exact bootloader text for manual /goal launch."
---

# Goal Preflight

Prompt-prep wrapper only. Do not launch `/goal`, runtime auditors, branch orchestrators, reviewers, workers, or plan-amenders.

## Start

Resolve the installed skills root:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-preflight" ] && [ -d "$HOME/.agents/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Produce a structured brief JSON, then let scripts generate and lint the bundle.
- If brief shape is unclear, run `create_goal_bundle.py --brief-schema-json` or `--example-brief`; do not inspect script source for schema.
- Parallelism is default: prefer independent branches and worker-sized work items; record serial reasons when capacity is intentionally underfilled.
- Ask the user only for gaps that would change branch boundaries, DoD, merge policy, or runtime safety.
- Use Lite only as optional context routing for large/vague source material or lint repair.
- Return the exact `goal-bootloader.md` text after lint passes.
- Do not read or search `skills/*/scripts/*.py` during normal preflight, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open detailed references only after a phase script or linter points at an ambiguity:

- `references/actionability-rubric.md` for vague source material.
- `references/bundle-contract.md` for bundle lint defects.
- `references/parallelization-rules.md` for decomposition tradeoffs.
- `references/lite-advisor-contract.md` before creating Lite packets.
