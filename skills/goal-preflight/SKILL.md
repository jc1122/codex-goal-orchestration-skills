---
name: goal-preflight
description: "Prepare path-hardened /goal orchestration bundles from a report, roadmap, diagnosis, or rough goal brief. Use when the user needs prompt infrastructure for goal-main-orchestrator: optionally use CLI-only Lite advisors for source digestion or lint-repair advice, synthesize rolling-scheduled branch groups and worker-sized work items when missing, enforce reproducible manifest paths and telemetry requirements, write job.manifest.json/main.prompt.md/branch prompts/location-bound goal-bootloader.md, run deterministic lint, and present the exact bootloader text for manual /goal launch."
---

# Goal Preflight

## Role Boundary

Prepare prompt infrastructure only. Do not launch `/goal`, create branches, create worktrees, run model auditors, dispatch branch orchestrators, or run workers. You may launch CLI-only Lite advisory packets for source digestion or lint-repair advice, but Lite output is advisory and never replaces deterministic lint, prompt audit, or runtime validators.

The runtime owner is `goal-main-orchestrator`; this skill must produce files compatible with it.

## Workflow

1. Run the skill availability bootstrap below.
2. Read the source report, diagnosis, roadmap, or goal brief.
3. Optionally create a Lite advisory packet when source material is large/vague or after deterministic lint fails.
4. Extract or synthesize:
   - `job_id`;
   - top-level goal;
   - base ref;
   - merge and cleanup policy;
   - artifact preservation policy;
   - branch list or independent branch decomposition that maximizes safe rolling parallelism;
   - `serial_reason` when the job cannot be split into at least two branches;
   - 1 to 4 bounded worker-sized work items per branch;
   - falsifiable DoD and evidence requirements.
5. Ask the user only for gaps that would change branch boundaries, DoD, merge policy, or runtime safety.
6. Write a structured brief JSON.
7. Generate the bundle with `scripts/create_goal_bundle.py`.
8. Run deterministic lint with `scripts/lint_goal_bundle.py`.
9. Present the exact `goal-bootloader.md` text in the final response.

## Skill Availability Bootstrap

Every run starts by confirming the three goal skills are installed in the same discoverable skills root and that their required scripts exist. Resolve the skills root once:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-preflight" ] && [ -d "$HOME/.agents/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/check_goal_skill_availability.py" \
  --skills-root "$GOAL_SKILLS_ROOT" \
  --require goal-preflight \
  --require goal-main-orchestrator \
  --require goal-branch-orchestrator
```

If this fails, stop before writing prompt files and tell the user to install or repair the skills package.

## Lite Advisors

Lite advisors are optional context routers, not authorities. Use `scripts/create_lite_advice_packet.py` only when it is likely to reduce heavy-agent context:

- `preflight-decomposition`: source report, roadmap, or diagnosis to branch/work-item advice;
- `lint-repair`: `preflight.lint.json`, `job.manifest.json`, and affected prompts to minimal repair advice.

Run Lite with focused explicit input files. Broad input is allowed only for source digestion from a long report or roadmap. Do not pass full repository dumps or unrelated result histories. Read `advice.json` first, then open only cited originals needed to verify or implement the advice.

Example:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_lite_advice_packet.py" \
  --packet-id P01-L01 \
  --purpose preflight-decomposition \
  --base-dir /absolute/path/to/repo \
  --out-dir /absolute/path/to/repo/plans/orchestration/<job-id>/lite \
  --input-file /absolute/path/to/repo/plans/source-report.md
```

Then run the generated `launch.sh` and validate. Packet ids are immutable by default; pass `--replace` only when intentionally regenerating a packet after removing the prior packet directory:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/validate_lite_advice.py" \
  --advice /absolute/path/to/repo/plans/orchestration/<job-id>/lite/P01-L01/advice.json \
  --inputs /absolute/path/to/repo/plans/orchestration/<job-id>/lite/P01-L01/input-files.json
```

If Lite is blocked, invalid, stale, or contradicted by source files, ignore the advice and continue with the normal preflight workflow. Do not treat Lite output as lint status, audit status, or DoD evidence. If any preflight Lite packet exists under the bundle `lite/` directory, record it in `job.manifest.json.preflight_lite_advice` with the exact expanded validation command shown above; the linter fails on unrecorded preflight Lite packet directories or non-canonical validation commands.

The Lite scripts enforce the preflight purpose allowlist (`preflight-decomposition`, `lint-repair`), capture the absolute Gemini CLI path/version/binary sha256 at packet creation, rehash all source inputs, `task.md`, `prompt.md`, and the Gemini binary during launch/validation, regenerate the prompt from `input-files.json` plus `task.md`, write packet-local `telemetry.json`, and reject runtime-purpose recommendations that are outside the explicit input set.

## Parallelization Rules

Parallelism is the default. When the source material does not define branches/work items, divide work for maximum viable parallelism:

- split branches by independent outcomes;
- prefer 3-4 branches for normal jobs;
- allow up to 20 branches as 5 scheduling groups of up to 4 branches;
- minimize shared-file overlap among branches likely to run together;
- use branch-level `depends_on` only for explicit prior-branch dependencies that must complete before a branch can start;
- use 1 to 4 work items per branch, and make every work item worker-sized: one objective, narrow ownership, short context list, exact verification commands, falsifiable DoD;
- make independent work items parallel by default so branch orchestrators dispatch them as a rolling saturated worker pool up to the branch worker cap;
- include the hard runtime rule that at most 4 branch orchestrator agents may be active, slots should stay saturated with eligible branches, and finished agents must be closed before launching replacements;
- require prompt-audit, worker, reviewer, and Lite packet `telemetry.json` plus a final `telemetry.summary.json`;
- require `serial_reason` for a single-branch bundle;
- require `parallelization_rationale` or `serial_reason` for any `max_active_branch_agents` below 4.

Read `references/parallelization-rules.md` for branch decomposition guidance.

## Bundle Generation

Create a structured brief JSON and run:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" \
  --brief /absolute/path/to/brief.json \
  --repo-root /absolute/path/to/repo
```

`--brief`, `--repo-root`, optional `--out-dir`, lint `--bundle-dir`, lint `--output`, and bootloader render `--bundle-dir`/`--repo-root` must be absolute paths with no `..` traversal. The scripts reject cwd-relative entry paths.

Manifest-owned paths must be reproducible POSIX-relative paths: prompt/status/review paths are relative to the bundle root, worktree paths are relative to the repository root, and work item `owned_paths`/`context_files` are repo-relative. Do not use absolute paths, backslashes, or `..` in the brief.

Generated `goal-bootloader.md` is location-bound: it embeds absolute bundle and repository roots. If the bundle or repository checkout moves, rerun this skill or run `render_goal_bootloader.py --repo-root /absolute/path/to/repo --write`; do not hand-edit bootloader paths.

If the source brief does not define artifact or cleanup handling, generated prompts use deterministic defaults: preserve the orchestration bundle; do not commit preflight/runtime artifacts unless explicitly requested by the user or main prompt; preserve branches, worktrees, packets, and logs after partial/blocked/failed runs.

By default, output goes to:

```text
plans/orchestration/<job-id>/
```

Run lint:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_goal_bundle.py" \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id>
```

When checking an existing bundle only for compatibility, avoid rewriting prior evidence:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_goal_bundle.py" \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id> \
  --no-write
```

Print the bootloader:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py" \
  --bundle-dir /absolute/path/to/plans/orchestration/<job-id>
```

## Required Final Response

After a successful preflight, include:

- bundle path;
- lint status;
- exact bootloader text under a heading like `Paste this into Copilot /goal:`;
- any warnings or user decisions embedded in the prompts.

Do not make the user open `goal-bootloader.md` manually.

Read `references/bundle-contract.md` for required files and manifest shape. Read `references/actionability-rubric.md` before finalizing prompts from vague source material. Read `references/lite-advisor-contract.md` before creating Lite packets.
