---
name: goal-preflight
version: 0.2.92
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

For a normal new bundle, run the guided pipeline after writing the brief:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/prepare_goal_bundle.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo \
  --out-dir /abs/bundle \
  --output /abs/bundle/preflight.pipeline.json
```

For a compact state snapshot (no jq), run:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py" --bundle-dir /abs/bundle --readiness
```

Use `--json` for machine-readable readiness checks. The guided pipeline writes `preflight.brief.lint.json`, `preflight.lint.json`, `repair-gate.json`, `readiness.json`, `goal-config-selection.json`, and compact `preflight.pipeline.json`; add `prepare_goal_bundle.py --verbose` only when full embedded selection/readiness payloads are needed.

For compact prompt handoff, share only the `--readiness --json` payload and next-command guidance instead of full manifests.

## Runtime Rules

- Produce a structured brief JSON, then let scripts generate and lint the bundle.
- If the user asks for debug mode or debug telemetry, set `telemetry_mode: "debug"` in the structured brief; bundle creation expands the full safe debug telemetry policy.
- If the user supplied or requested a goal configuration, prefer `prepare_goal_bundle.py`; it auto-detects candidate configs, runs `check_goal_config.py --for-preflight`, writes remediation output when caps/telemetry fields can be sanitized, and records the decision in `goal-config-selection.json`. Use manual `--goal-config /abs/goal.config.json --goal-config-check /abs/goal-config-check.json` only when debugging a specific stage.
- If brief shape is unclear, run `create_goal_bundle.py --brief-schema-json` or `--example-brief`; do not inspect script source for schema.
- Parallelism is default: prefer independent branches and worker-sized work items; record serial reasons when capacity is intentionally underfilled.
- When preparing a new bundle, preserve intermediate artifacts and use the guided preflight pipeline. If a manual fallback is needed, run `brief lint`, `create_goal_bundle`, `lint_goal_bundle`, `script_only_repair_gate`, then `render_goal_bootloader` with `--readiness` before returning bootloader text.
- Readiness must treat non-git repository roots as blocked for runtime branch/worktree orchestration unless a future explicit no-git runtime mode is implemented; blocked bootloaders are corrective-only and must not render `$goal-main-orchestrator` launch handoff commands.
- Ask the user only for gaps that would change branch boundaries, DoD, merge policy, or runtime safety.
- Use Lite only as optional context routing for large/vague source material or lint repair.
- Return the exact `goal-bootloader.md` text after lint passes and readiness launch is allowed; if readiness is blocked, return the blocked readiness fix/recheck command instead.
- Do not read or search `skills/*/scripts/*.py` during normal preflight, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open detailed references only after a phase script or linter points at an ambiguity:

- `references/actionability-rubric.md` for vague source material.
- `references/bundle-contract.md` for bundle lint defects.
- `references/parallelization-rules.md` for decomposition tradeoffs.
- `references/lite-advisor-contract.md` before creating Lite packets.
