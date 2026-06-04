---
name: goal-config
version: 0.2.88
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

For runtime guidance, open `"$GOAL_SKILLS_ROOT/goal-config/SKILL.md"`.
`"$HOME/.agents/skills/.system"` contains system wrappers and system-level metadata; use it only when explicitly needed for system-skill tooling.

Then print the compact deterministic phase table and follow it:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/runtime_phase_manifest.py" --markdown
```

## Runtime Rules

- Use `scan_configurables.py --json` before proposing configuration changes.
- Before creating a config, run `scan_configurables.py --questions-json`. If preferences are missing, follow `interaction.ask_order`: model/harness profile first, effort/aggressiveness second, validation/smoke/debug third.
- Prefer smoke validation by default; only use debug mode when the user explicitly asks for trace analysis or stall diagnosis.
- Keep intake lean but explicit: show every option for each missing section with its short description. Ask sections in order, but when the user says to continue or wants completion, ask/apply all remaining missing sections in one compact pass. Do not silently generate a default profile.
- Proceed without extra prompting only when the user explicitly says to use defaults, supplies all preferences, or selects an existing checked profile.
- Write an explicit `goal.config.json` with `create_goal_config.py`; use `--effort-profile lean|balanced|thorough`, `--validation-mode model-check|smoke|debug`, and `--state-output /abs/goal-config-state.json`; do not rely on hidden defaults.
- Treat create flags as binding: if caps, waves, timeouts, ladders, role-models, or harness specs are requested, the rendered config must apply them or the command must fail.
- When the user names models or harnesses, encode them directly with `--role-model ROLE:HARNESS:PROVIDER/MODEL[:ALIAS[:PURPOSE]]`; do not substitute a different provider/model without asking.
- For `codex` and `gemini` role models, the config may use bare provider-implied model ids such as `--role-model lite_agent:gemini:gemini-3-flash-preview`; it records the provider separately while passing the provider-free model id to the CLI.
- For custom harnesses, pass `--harness-spec /abs/spec.json` or inline JSON. The spec must include a harness `name`, `kind`, `command`, smoke invocation, and runtime invocation when it will be used by worker/reviewer launchers.
- The config must include `harness_smokes` for every configured model role. If any selected role lacks a smoke definition, stop before running smoke tests.
- For discovery/use-all-available, first create or reuse a seed config, then run `check_goal_config.py --config /abs/seed.goal.config.json --discover-profile mixed-fast --discover-all-candidates --smoke --stdout summary --output /abs/goal-config-discovery.json --state-output /abs/goal-config-state.json`, inspect accepted/rejected/skipped/unvisited routes, then create the final config with `create_goal_config.py --from-discovery /abs/goal-config-discovery.json --mapping auto`.
- If the final `from-discovery` accepted route set is unchanged, reuse smoke evidence with
  `--reuse-smoke-report /abs/goal-config-discovery.json` on the subsequent config smoke check to avoid duplicate harness calls.
- Run `check_goal_config.py --require-models --stdout summary --output /abs/goal-config-check.json --state-output /abs/goal-config-state.json` before trusting configured model routes.
- Before heavy checks, run `check_goal_config.py --config /abs/goal.config.json --for-preflight --state-output /abs/goal-config-state.json` to fail early on preflight schema/capability mismatches.
- Run `check_goal_config.py --smoke --stdout summary` when the user requests harness validation, a new provider/model profile, or trace-analysis debug telemetry.
- Do not combine `--discover-all-candidates` and final route smoke as a performance default; that sequence is a discovery-path test, not a routine validation path.
- Treat missing binaries, missing models, missing assistant output, auth/API errors, or smoke text mismatches as blocked evidence. Preserve checker-reported provider/status/message/count fields; use `--include-raw-errors` only when full raw provider payloads are needed for debugging.
- Inspect `goal-config-state.json` for `phase`, `missing_preferences`, `next_command`, and `complete`; do not guess whether config validation is done.
- If the config will be used for `/goal`, pass both the config and passing check report to `goal-preflight` with `--goal-config` and `--goal-config-check`; this is how manifest policies and runtime packet launch attempts are configured.
- Record token counts, character counts, elapsed milliseconds, provider, model, harness, and role separately. Treat token totals as complete only for smoke entries with `token_telemetry.available=true`; otherwise compare character counts and elapsed time.
- Do not record USD/pricing fields. Do not inspect provider credentials or unrelated opencode database tables.
- Do not read or search `skills/*/scripts/*.py` during normal configuration, including with `rg`, `grep`, `cat`, `sed`, or `head`. Inspect Python source only when a script failed and debugging that script is the assigned task.

## Details On Demand

Open `references/configuration-contract.md` only after a phase script or checker reports an ambiguity.
