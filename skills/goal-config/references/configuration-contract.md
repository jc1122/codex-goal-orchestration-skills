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
- `harness_smokes`: smoke prompt, expected text, timeout, and readback mode for each role.
- `telemetry`: fields to collect by model role and harness.

## Opencode Checks

For opencode-backed roles, `check_goal_config.py` must:

- find the `opencode` binary unless a fixture model list is supplied;
- confirm the exact `provider/model` string appears in `opencode models <provider>`;
- run `opencode run --pure --format json --model <provider/model>` when `--smoke` is requested;
- read assistant text and token counters for the captured session id from the local opencode session database;
- report token counts, response character counts, stdout/stderr character counts, elapsed milliseconds, model, provider, harness, and role separately.

Missing models, missing assistant text, timeout, or smoke mismatch is a failed check.
