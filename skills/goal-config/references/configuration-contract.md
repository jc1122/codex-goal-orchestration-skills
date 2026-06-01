# Goal Configuration Contract

`goal-config` produces a compact `goal.config.json` that agents can inspect before preflight or runtime orchestration.

## Required Shape

- `schema_version`: currently `1`.
- `profile`: human-readable profile id.
- `usage_units`: must describe tokens, characters, and elapsed milliseconds; USD/pricing units are not allowed.
- `aggressiveness`: branch/worker caps and wave count.
- `effort`: timeout and output-size limits for lite and demanding agents.
- `models`: separate entries per model role. Each entry records `harness`, `provider`, `model`, and an alias.
- `model_ladders`: ordered role names, not raw provider strings.
- `harnesses`: named harness definitions including command, smoke invocation, runtime invocation, and readback mode for `opencode`, `codex`, `gemini`, and `generic-cli`.
- `harness_smokes`: smoke prompt, expected text, timeout, and readback mode for each role.
- `telemetry`: fields to collect by model role and harness.
- `model_policies`: worker, reviewer, amender, and Lite route policies consumed by `goal-preflight` and runtime packet generation.

## Custom Harness Specs

`create_goal_config.py --harness-spec /abs/spec.json` accepts either a JSON object with `name` or an object keyed by harness name. Harness specs must avoid provider pricing fields and should contain:

- `kind`: one of `opencode`, `codex`, `gemini`, or `generic-cli`.
- `command`: executable name or absolute command path.
- `smoke_args`: argument template for `check_goal_config.py --smoke`.
- `run_args`: argument template for runtime packet launchers.
- `run_readback`: `opencode_session_db`, `output_file`, or `stdout`.

Templates may use `{prompt}`, `{prompt_file}`, `{model}`, `{provider}`, `{role}`, `{alias}`, `{packet_id}`, `{worktree}`, `{schema_name}`, and `{output_path}`. The checker decides that a harness is pluggable only after binary/model validation and the requested smoke tests pass.

## Opencode Checks

For opencode-backed roles, `check_goal_config.py` must:

- find the `opencode` binary unless a fixture model list is supplied;
- confirm the exact `provider/model` string appears in `opencode models <provider>`;
- run `opencode run --pure --format json --model <provider/model>` when `--smoke` is requested;
- for `codex` roles, run `codex exec <prompt>` when `--smoke` is requested;
- for `gemini` roles, run `gemini <prompt>` when `--smoke` is requested;
- for `generic-cli` roles, run the configured harness `command` plus `smoke_args` template with prompt context when `--smoke` is requested;
- read assistant text and token counters for the captured session id from the local opencode session database;
- report token counts, response character counts, stdout/stderr character counts, elapsed milliseconds, model, provider, harness, and role separately.

Missing models, missing assistant text, missing harness/binary, timeout, or smoke mismatch is a failed check.

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
