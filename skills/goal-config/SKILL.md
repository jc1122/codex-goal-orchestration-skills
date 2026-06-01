---
name: goal-config
version: 0.2.57
description: "Configure and verify goal orchestration model/provider profiles. Use when the user wants lean agent UX for model ladders, harness providers, branch/worker aggressiveness, token/character/time effort settings, or a fail-closed smoke test before goal-preflight or runtime orchestration."
---

# Goal Config

Configuration wrapper only. Do not launch `/goal`, preflight bundles, branch runtimes, reviewers, workers, or plan-amenders.

## Start

Resolve the installed skills root:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-config" ] && [ -d "$HOME/.agents/skills/goal-config" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Use `scan_configurables.py --json` before proposing configuration changes.
- Write an explicit `goal.config.json` with `create_goal_config.py`; do not rely on hidden defaults.
- When the user names models or harnesses, encode them directly with `--role-model ROLE:HARNESS:PROVIDER/MODEL[:ALIAS[:PURPOSE]]`; do not substitute a different provider/model without asking.
- For custom harnesses, pass `--harness-spec /abs/spec.json`. The spec must include a harness `name`, `kind`, `command`, smoke invocation, and runtime invocation when it will be used by worker/reviewer launchers.
- Run `check_goal_config.py --require-models` before trusting configured model routes.
- Run `check_goal_config.py --smoke` when the user requests harness validation or a new provider/model profile.
- Treat missing binaries, missing models, missing assistant output, or smoke text mismatches as blocked evidence.
- If the config will be used for `/goal`, pass both the config and passing check report to `goal-preflight` with `--goal-config` and `--goal-config-check`; this is how manifest policies and runtime packet launch attempts are configured.
- Record token counts, character counts, elapsed milliseconds, provider, model, harness, and role separately.
- Do not record USD/pricing fields. Do not inspect provider credentials or unrelated opencode database tables.
- Do not read or search `skills/*/scripts/*.py` during normal configuration, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open `references/configuration-contract.md` only after a phase script or checker reports an ambiguity.
