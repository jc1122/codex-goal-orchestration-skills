---
name: goal-config
version: 0.2.60
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
- Before creating a config, run `scan_configurables.py --questions-json`. If preferences are missing, follow `interaction.ask_order`: model/harness profile first, effort/aggressiveness second, validation/smoke/debug third.
- Keep intake lean but explicit: ask one missing section at a time, show every option listed for that section with its short description, then move to the next missing section after the user answers. Do not silently generate a default profile.
- Proceed without extra prompting only when the user explicitly says to use defaults, supplies all preferences, or selects an existing checked profile.
- Write an explicit `goal.config.json` with `create_goal_config.py`; do not rely on hidden defaults.
- Treat create flags as binding: if caps, waves, timeouts, ladders, role-models, or harness specs are requested, the rendered config must apply them or the command must fail.
- When the user names models or harnesses, encode them directly with `--role-model ROLE:HARNESS:PROVIDER/MODEL[:ALIAS[:PURPOSE]]`; do not substitute a different provider/model without asking.
- For `codex` and `gemini` role models, the config may record the provider separately while passing the provider-free model id to the CLI.
- For custom harnesses, pass `--harness-spec /abs/spec.json`. The spec must include a harness `name`, `kind`, `command`, smoke invocation, and runtime invocation when it will be used by worker/reviewer launchers.
- The config must include `harness_smokes` for every configured model role. If any selected role lacks a smoke definition, stop before running smoke tests.
- Run `check_goal_config.py --require-models` before trusting configured model routes.
- Run `check_goal_config.py --smoke` when the user requests harness validation, a new provider/model profile, or a discovery-style "use all available" profile.
- Treat missing binaries, missing models, missing assistant output, auth/API errors, or smoke text mismatches as blocked evidence. Preserve checker-reported provider status/message fields.
- If the config will be used for `/goal`, pass both the config and passing check report to `goal-preflight` with `--goal-config` and `--goal-config-check`; this is how manifest policies and runtime packet launch attempts are configured.
- Record token counts, character counts, elapsed milliseconds, provider, model, harness, and role separately.
- Do not record USD/pricing fields. Do not inspect provider credentials or unrelated opencode database tables.
- Do not read or search `skills/*/scripts/*.py` during normal configuration, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open `references/configuration-contract.md` only after a phase script or checker reports an ambiguity.
