---
name: goal-preflight
description: "Prepare runtime-compatible /goal orchestration bundles from a report, diagnosis, roadmap, or rough goal brief. Use when the user needs actionable prompt infrastructure for goal-main-orchestrator: synthesize independent branch waves and Spark-sized work items when missing, write job.manifest.json/main.prompt.md/branch prompts/goal-bootloader.md, run deterministic lint, and present the exact bootloader text for the user to launch manually."
---

# Goal Preflight

## Role Boundary

Prepare prompt infrastructure only. Do not launch `/goal`, create branches, create worktrees, run model auditors, dispatch branch orchestrators, or run workers.

The runtime owner is `goal-main-orchestrator`; this skill must produce files compatible with it.

## Workflow

1. Read the source report, diagnosis, roadmap, or goal brief.
2. Extract or synthesize:
   - `job_id`;
   - top-level goal;
   - base ref;
   - merge and cleanup policy;
   - branch list or independent branch decomposition;
   - Spark-sized work items per branch;
   - falsifiable DoD and evidence requirements.
3. Ask the user only for gaps that would change branch boundaries, DoD, merge policy, or runtime safety.
4. Write a structured brief JSON.
5. Generate the bundle with `scripts/create_goal_bundle.py`.
6. Run deterministic lint with `scripts/lint_goal_bundle.py`.
7. Present the exact `goal-bootloader.md` text in the final response.

## Parallelization Rules

When the source material does not define branches/work items, divide work for maximum viable parallelism:

- split branches by independent outcomes;
- prefer 3-5 branches for normal jobs;
- allow up to 25 branches as 5 waves of up to 5 branches;
- minimize shared-file overlap within a wave;
- make every work item Spark-sized: one objective, narrow ownership, short context list, exact verification commands, falsifiable DoD;
- include the hard runtime rule that at most 5 branch orchestrator agents may be active and finished agents must be closed before launching replacements.

Read `references/parallelization-rules.md` for branch decomposition guidance.

## Bundle Generation

Create a structured brief JSON and run:

```bash
python3 /home/jakub/.agents/skills/goal-preflight/scripts/create_goal_bundle.py \
  --brief /absolute/path/to/brief.json \
  --repo-root /absolute/path/to/repo
```

By default, output goes to:

```text
plans/orchestration/<job-id>/
```

Run lint:

```bash
python3 /home/jakub/.agents/skills/goal-preflight/scripts/lint_goal_bundle.py \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id>
```

Print the bootloader:

```bash
python3 /home/jakub/.agents/skills/goal-preflight/scripts/render_goal_bootloader.py \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id>
```

## Required Final Response

After a successful preflight, include:

- bundle path;
- lint status;
- exact bootloader text under a heading like `Paste this into Copilot /goal:`;
- any warnings or user decisions embedded in the prompts.

Do not make the user open `goal-bootloader.md` manually.

Read `references/bundle-contract.md` for required files and manifest shape. Read `references/actionability-rubric.md` before finalizing prompts from vague source material.
