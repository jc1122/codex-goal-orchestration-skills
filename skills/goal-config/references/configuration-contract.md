# Goal Configuration Contract

`goal-config` produces a compact `goal.config.json` that agents can inspect before preflight or runtime orchestration.

## Preference Intake

Before creating `goal.config.json`, run:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/scan_configurables.py" --questions-json
```

If the user has not already supplied preferences, ask the missing categories before creating a config:

- model/harness profile or existing checked profile path;
- effort/aggressiveness for branch and worker caps, timeouts, and token/character pressure;
- validation mode: model check only, smoke, or smoke plus debug telemetry for preflight. Prefer smoke by default and reserve debug for trace analysis.

Use the JSON `interaction.ask_order`. Ask sections in order, and when the user says to continue or wants completion, ask/apply all remaining missing sections in one compact pass:

1. Model profile: explain that this selects harnesses and role-to-model ladders. Show all listed choices, including checked config reuse, current default, the `opencode-deepseek-v4` bridge profile, discovery of available routes, agy generic CLI, and mixed/custom mappings.
2. Effort profile: explain that this controls branch/worker caps, timeouts, and token/character pressure. Show lean, balanced, thorough, and custom.
3. Validation and debug telemetry: explain that this controls fail-closed model checks, harness smoke tests, and whether preflight should collect full debug traces. Show model check only, model check plus smoke, smoke plus debug telemetry, and custom validation. Steer normal users toward smoke mode; debug is for trace analysis workflows.

Do not silently create a default config unless the user explicitly says to use defaults. If the user chooses a custom option, collect the exact required values before creating `goal.config.json`. Write `goal-config-state.json` with create/check/discovery commands so the next step is deterministic.

## Required Shape

- `schema_version`: currently `1`.
- `profile`: human-readable profile id.
- `usage_units`: must describe tokens, characters, and elapsed milliseconds; USD/pricing units are not allowed.
- `aggressiveness`: branch/worker caps and wave count.
- `effort`: timeout and output-size limits for lite and demanding agents.
- `models`: separate entries per model role. Each entry records `harness`, `provider`, `model`, and an alias.
- `model_ladders`: ordered role names, not raw provider strings.
- `harnesses`: named harness definitions including command, smoke invocation, runtime invocation, and readback mode for `opencode-bridge`, `codex`, and `generic-cli`.
- `harness_smokes`: smoke prompt, expected text, timeout, and readback mode for each role.
- `telemetry`: semantic collection groups by model role and harness (`route_decisions`, `token_usage`, `timings`, `scheduler_utilization`, `context_pack_stats`, `validator_runs`, `artifact_hashes`). Detailed token/character/time counter names belong in `usage_units`, not `telemetry.collect`.
- `model_policies`: worker, reviewer, amender, and Lite route policies consumed by `goal-preflight` and runtime packet generation.

Create-time flags are binding. If a user supplies caps, wave count, timeout flags, ladders, role-models, provider/model strings, or harness specs, `create_goal_config.py` must either render those values into `goal.config.json` or fail before writing a misleading config.

Named profiles are first-class flags:

- `create_goal_config.py --effort-profile lean|balanced|thorough`.
- `create_goal_config.py --validation-mode model-check|smoke|debug`.

`--validation-mode debug` must serialize debug telemetry intent in the config, including `telemetry.mode=debug`.

Debug mode is intentionally heavier than smoke and is intended for post-validation trace analysis, not routine throughput checks.

Every configured model role must have a `harness_smokes` entry. The checker fails before launching smoke tests when any selected role lacks smoke configuration.

## Custom Harness Specs

`create_goal_config.py --harness-spec /abs/spec.json` or `--harness-spec '{"name":"..."}'` accepts either a JSON object with `name` or an object keyed by harness name. Harness specs must avoid provider pricing fields and should contain:

- `kind`: one of `opencode-bridge`, `codex`, or `generic-cli`.
- `command`: executable name or absolute command path.
- `smoke_args`: argument template for `check_goal_config.py --smoke`.
- `run_args`: argument template for runtime packet launchers.
- `run_readback`: `bridge_run_dir`, `output_file`, or `stdout`.

Templates may use `{prompt}`, `{prompt_file}`, `{model}`, `{provider}`, `{role}`, `{alias}`, `{packet_id}`, `{worktree}`, `{schema_name}`, and `{output_path}`. The checker decides that a harness is pluggable only after binary/model validation and the requested smoke tests pass.

## Harness Checks

For `opencode-bridge` roles, `check_goal_config.py` must:

- confirm the exact `provider/model` string is a known bridge route (`deepseek/deepseek-v4-flash` for `ds-flash-max`, `deepseek/deepseek-v4-pro` for `ds-pro-max`), accepting nested provider model ids such as `openrouter/deepseek/deepseek-v4-pro`;
- run the bridge offline readiness command (`opencode_worker.py doctor --json`) when `--smoke` is requested; no live deepseek delegate runs at check time;
- for `codex` roles, run `codex exec <prompt>` when `--smoke` is requested;
- for `generic-cli` roles, run the configured harness `command` plus `smoke_args` template with prompt context when `--smoke` is requested;
- report token counts, response character counts, stdout/stderr character counts, elapsed milliseconds, model, provider, harness, and role separately;
- set `token_telemetry.available=false` when a harness does not expose counters, so audits compare character counts and elapsed time instead of treating token totals as complete;
- focus `response_excerpt` on the expected assistant smoke text when possible, so CLI boilerplate does not dominate scan output.

Missing models, missing assistant text, missing harness/binary, auth/API errors, timeout, or smoke mismatch is a failed check. When a harness emits JSON errors, the report should preserve actionable provider/status/message/count fields such as status `401` and message `AuthenticateToken authentication failed`. Full raw provider payloads are intentionally not retained.

For `codex` roles, `--role-model ROLE:HARNESS:PROVIDER/MODEL` records `provider` separately but renders the provider-free `model` for the CLI invocation. Bare provider-implied forms such as `--role-model worker_codex_spark:codex:openai/gpt-5.3-codex-spark` are allowed. For `opencode-bridge` roles, use `--role-model ROLE:opencode-bridge:deepseek/deepseek-v4-flash` (or `deepseek-v4-pro`).

When the user asks to "use all available" models, treat it as discovery, not as a silent default. First create or reuse a seed config, then use `check_goal_config.py --config /abs/seed.goal.config.json --discover-profile mixed-fast --discover-all-candidates --smoke --stdout summary --output /abs/goal-config-discovery.json --state-output /abs/goal-config-state.json` to try a fixed ranking across the configured `opencode-bridge`, Codex, and generic harnesses. The profile stops a provider after the first auth failure. If `--discover-all-candidates` is omitted, the profile may stop early after enough accepted routes and must report unvisited candidates.

Discovery with `--discover-all-candidates` and discovery smoke is for validating discovery path coverage; it is not the default performance/throughput validation workflow.

Discovery emits `candidate_routes`, `checked_roles`, `accepted_routes`, `rejected_routes`, `skipped_routes`, and `unvisited_routes` with reasons. Use accepted routes to create a final explicit config with `create_goal_config.py --from-discovery /abs/goal-config-discovery.json --mapping auto`; do not pass unreviewed discovered routes directly into preflight.
If the accepted route set is unchanged after from-discovery, pass the discovery file into the follow-on smoke check with `--reuse-smoke-report /abs/goal-config-discovery.json` to avoid duplicate harness execution.

When `--output` is present, `check_goal_config.py` defaults stdout to a compact summary. Use `--stdout full` for full JSON or `--stdout none` for quiet file-only runs. Passing smoke evidence is reused by unique `(harness, provider, model)` within a run, and `--reuse-smoke-report /abs/report.json` can reuse prior passing check/discovery evidence.
For large reports, use scoped inspection with `jq` (for example `.summary`, `.accepted_routes | length`, and `.unvisited_routes`) rather than opening full JSON unless required.

Before model availability or smoke validation, run a preflight compatibility gate:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py" \
  --config /abs/goal.config.json \
  --for-preflight \
  --state-output /abs/goal-config-state.json
```

The preflight compatibility pass must validate all of:

- aggressiveness caps are within preflight constraints (`1..4` for `max_active_branch_agents`, `1..4` for `max_active_worker_packets`, `1..5` for `max_waves`);
- validation/check mode compatibility (`validation.mode` must be callable by the requested check mode);
- telemetry schema/version, mode, and semantic collectible group names;
- preflight schema requirements (`usage_units` keys and `model_policies` keys).

`check_goal_config.py` state and report should preserve both:

- `check_mode`: the check invocation mode actually requested in this run (`check`/`smoke`/`discover`),
- `config_validation_mode`: the config intent from `validation.mode`.

When preflight detects a mismatch, it must return `failed` and make the next action explicit in `goal-config-state.json`.

## Preflight And Runtime Consumption

`goal.config.json` is active only after a passing check report is passed to preflight:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo \
  --out-dir /abs/bundle \
  --goal-config /abs/goal.config.json \
  --goal-config-check /abs/goal-config-check.json
```

Preflight embeds `goal_config`, copies the config and check report into the bundle, and replaces manifest model policies with `model_policies`. Branch runtime packet generation reads those policies and emits concrete configured attempts in `launch-config.json`.
