# Codex Goal Orchestration Skills

This repository packages five Codex skills for file-backed `/goal` orchestration:

- `goal-config`
- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`
- `goal-plan-amender`

The package is intentionally script-and-artifact driven. Agents should use this README, `AGENTS.md`, `maintenance/agent-context-index.json`, each skill's `runtime_phase_manifest.py --markdown`, script `--help`, JSON artifacts, and validator defects before opening implementation source.

## Install

Install the latest repository version:

```bash
npx github:jc1122/codex-goal-orchestration-skills
```

Install a pinned release:

```bash
npx github:jc1122/codex-goal-orchestration-skills#v0.2.65
```

Install to a custom absolute skills root:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --dest /absolute/path/to/skills --force
```

Useful installer commands:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --list
npx github:jc1122/codex-goal-orchestration-skills -- --version
npx github:jc1122/codex-goal-orchestration-skills -- --dry-run
```

From a local checkout:

```bash
node bin/install-goal-skills.js --dest /absolute/path/to/skills --force
```

The default destination is `$CODEX_HOME/skills` when `CODEX_HOME` is set, otherwise `~/.codex/skills`. The installer copies all five public skills, `_goal_shared`, root `AGENTS.md`, and `maintenance/agent-context-index.json`.

Resolve the installed skills root in runtime instructions:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-preflight" ] && [ -d "$HOME/.agents/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

Runtime goal-config instructions live at `"$GOAL_SKILLS_ROOT/goal-config/SKILL.md"`. `.agents/skills/.system` is for system-level wrappers/metadata, not user-facing config docs.

## Agent Start

1. Read `AGENTS.md`, then `maintenance/agent-context-index.json`.
2. Read the relevant skill `SKILL.md`.
3. Print and follow the skill phase manifest:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/runtime_phase_manifest.py" --markdown
```

Do not read or search `skills/*/scripts/*.py` during normal runtime. Use script outputs, `--help`, generated artifacts, and validators. Open Python source only when implementing or debugging that script surface.

## Architecture

| Layer | Skill | Responsibility |
| --- | --- | --- |
| Configuration | `goal-config` | Scan configurable model/provider, aggressiveness, token/character/time effort, telemetry, and harness knobs; write `goal.config.json`; fail-closed model availability and harness smoke reports. |
| Preflight | `goal-preflight` | Turn a report, roadmap, diagnosis, or rough brief into a linted bundle with `job.manifest.json`, `main.prompt.md`, branch prompts, and a location-bound `goal-bootloader.md`. |
| Main runtime | `goal-main-orchestrator` | Consume an existing bundle, bootstrap skills/model catalog, fail-closed prompt audit, create branch worktrees, schedule branch agents, summarize telemetry, assemble and validate final status. |
| Branch runtime | `goal-branch-orchestrator` | Run one audited branch worktree, create worker/research/reviewer packets, keep worker slots saturated, integrate results, gate review, assemble and validate branch status. |
| Amendment runtime | `goal-plan-amender` | After validated terminal branch evidence, propose, validate, and optionally apply future-work-only manifest amendments or deterministic blocker-repair branches. |

Shared `_goal_shared` support includes skill availability checks, model catalog checks, path rules, runtime phase manifests, Lite packet creation/validation, telemetry extraction, context packing, scheduler ledgers, status validation helpers, and script-only repair gates.

The bundle is the data plane. Runtime agents exchange manifest-owned files rather than hidden chat state. Prompt audit, branch statuses, reviews, scheduler ledgers, telemetry, Lite advice, and amendments must be validated from disk before pass claims.

## End-To-End Flow

1. Optionally run `goal-config` to write and verify a model/provider/harness profile before preflight or runtime work.
2. `goal-preflight` writes a structured brief, then normally runs `prepare_goal_bundle.py` to auto-select a preflight-compatible config, lint the brief, create the bundle, lint the bundle, run the repair gate, write readiness, and return the exact `goal-bootloader.md` text.
3. Use `render_goal_bootloader.py --readiness` (or `--readiness --json`) to confirm config compatibility, lint status, caps/route policy/telemetry mode, branch DAG, git runtime gate, repair gate, and next command.
4. The user launches `/goal` with the bootloader. The bootloader points to absolute bundle and repository roots, so it must be regenerated if either path moves.
5. `goal-main-orchestrator` runs bootstrap, live model catalog, script-only repair gate, deterministic or model prompt audit, branch scheduling, and main scheduler ledger updates.
6. Main launches eligible branch orchestrator sessions as a rolling saturated pool up to `max_active_branch_agents` and waits for terminal artifacts rather than polling active branch internals.
7. `goal-branch-orchestrator` creates worker or research-worker packets, runs launchers, integrates diffs or research findings, updates worker scheduler evidence, creates a pre-review gate, launches or reuses reviewer evidence, and validates branch status.
8. After each validated terminal branch result, main records an amendment launch-or-skip decision. `goal-plan-amender` may add or adjust only future unstarted work through validated amendments.
9. Main closes by running scheduler finalization, `summarize_telemetry.py`, `assemble_main_status.py`, and `validate_main_status.py`.

Status semantics:

- `pass`: every manifest branch and worker/research item launched, validated, reviewed where required, and satisfies DoD.
- `partial`: some work completed, with structured unlaunched or blocked remainder.
- `blocked`: progress is stopped by a concrete blocker with preserved evidence.
- `failed`: attempted work produced a terminal negative result.

## Goal Configuration

Use `goal-config` when the user asks for a model/provider profile, different harnesses, lower token usage, branch/worker aggressiveness changes, or a provider smoke before orchestration.

Inventory configurable knobs:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/scan_configurables.py" --json
```

Ask for preferences before creating a profile when the user has not already supplied them:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/scan_configurables.py" --questions-json
```

Use the generated `interaction.ask_order` so the user is not overwhelmed:

1. Ask the model/harness profile first and show every listed option with its short explanation.
2. Ask the effort/aggressiveness profile second.
3. Ask the validation/smoke/debug telemetry mode third.

For normal runs, prefer smoke mode; request debug only when a user asks for traceability or stall analysis.

Ask only missing sections in that order. If the user says to continue or wants completion, ask/apply all remaining missing sections in one compact pass. Do not silently create a default config unless the user says to use defaults or selects an existing checked profile.

Create the opencode DeepSeek v4 profile requested for Lite and demanding agents:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py" \
  --preset opencode-deepseek-v4 \
  --effort-profile balanced \
  --validation-mode smoke \
  --state-output /abs/goal-config-state.json \
  --output /abs/goal.config.json
```

The preset lists `deepseek/deepseek-v4-flash` separately as `lite_agent` and `deepseek/deepseek-v4-pro` separately as `demanding_agent`. It records effort in tokens, characters, and elapsed time only.

Create flags are binding. If the user supplies caps, wave count, timeout flags, ladders, role-models, provider/model strings, or harness specs, the rendered `goal.config.json` must apply those values or the command must fail.

Effort profiles are exact create inputs:

- `--effort-profile lean`: lower branch/worker caps, fewer waves, shorter Lite and demanding-agent timeouts.
- `--effort-profile balanced`: default compact profile.
- `--effort-profile thorough`: higher caps and longer timeouts for harder goals.

Validation modes are exact create inputs:

- `--validation-mode model-check`: require model availability checks.
- `--validation-mode smoke`: require model checks and harness smoke.
- `--validation-mode debug`: require smoke and serialize `telemetry.mode=debug` plus debug preflight intent. This is heavier and intended for trace analysis.

To use user-supplied models, keep roles explicit:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py" \
  --preset opencode-deepseek-v4 \
  --role-model lite_agent:opencode:deepseek/deepseek-v4-flash \
  --role-model demanding_agent:opencode:deepseek/deepseek-v4-pro \
  --worker-ladder demanding_agent,lite_agent \
  --reviewer-ladder demanding_agent \
  --output /abs/goal.config.json
```

For `codex` and `gemini`, `ROLE:HARNESS:PROVIDER/MODEL` records `provider` separately and renders the provider-free model id for the CLI, such as `gpt-5.4` or `gemini-3-flash-preview`.
Bare provider-implied forms also work for those harnesses, for example `--role-model lite_agent:gemini:gemini-3-flash-preview`.

To plug in another CLI harness, provide a JSON harness spec path or inline JSON and map roles to it. Built-in harness kinds are `opencode`, `codex`, `gemini`, and `generic-cli`; `generic-cli` is for harnesses such as antigravity that can be represented as a command plus prompt/model templates.

```json
{
  "name": "antigravity",
  "kind": "generic-cli",
  "command": "agy",
  "smoke_args": ["--print", "{prompt}"],
  "run_args": ["--print", "{prompt}"],
  "run_readback": "stdout"
}
```

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py" \
  --harness-spec /abs/antigravity-harness.json \
  --role-model lite_agent:antigravity:provider/model-lite \
  --role-model demanding_agent:antigravity:provider/model-pro \
  --output /abs/goal.config.json
```

Fail closed on missing opencode models:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py" \
  --config /abs/goal.config.json \
  --require-models \
  --stdout summary \
  --output /abs/goal-config-check.json \
  --state-output /abs/goal-config-state.json
```

Smoke-test every configured role before passing the report to preflight:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py" \
  --config /abs/goal.config.json \
  --require-models \
  --smoke \
  --stdout summary \
  --output /abs/goal-config-smoke.json \
  --state-output /abs/goal-config-state.json
```

When `--output` is supplied, checker stdout defaults to `summary`: status, accepted routes, rejection counts, and output path. Use `--stdout full` to print the full JSON report, or `--stdout none` for quiet file-only output.

The smoke report records role, harness, provider, model, exact model availability, return code, elapsed milliseconds, stdout/stderr character counts, assistant response character counts, and token counters when the harness exposes them, such as opencode session database readback. Each smoke entry includes `token_telemetry.available`; when it is false, compare character counts and elapsed time instead of pretending token totals are complete. CLI response excerpts are focused on the expected assistant smoke text when possible to keep boilerplate out of the scan path. The checker does not read provider credentials or report provider prices. Passing smoke evidence is reused for duplicate `(harness, provider, model)` routes in the same run; `--reuse-smoke-report /abs/previous.json` can reuse a prior passing discovery/check report.

For large reports, prefer scoped reads:

```bash
jq '.accepted_routes | length' /abs/goal-config-smoke.json
jq '.unvisited_routes' /abs/goal-config-smoke.json
```

Generated configs include `harness_smokes` for every configured model role. If a selected role lacks a smoke definition, the checker fails before running route smokes. The canonical `/goal` smoke report should omit `--harness` so it covers worker, reviewer, amender, Lite, and demanding aliases that runtime packets may use. To isolate a failing route after the full report fails, pass repeated or comma-separated `--harness` values, for example `--harness worker_opencode`.

The opencode checker accepts nested model ids such as `openrouter/deepseek/deepseek-v4-pro` and normalizes JSON/API errors into provider, status, short message, and count fields. Full raw provider error payloads are emitted only with `--include-raw-errors`. If the user asks to use all available models, treat that as discovery: list candidates, smoke selected routes, and report `accepted_routes` and `rejected_routes` with reasons before preflight consumes the config.

Discovery mode checks provider-listed candidates without manually writing every role first:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py" \
  --config /abs/goal.config.json \
  --discover-profile mixed-fast \
  --discover-all-candidates \
  --discover-model-filter 'deepseek|gpt-5.4|gemini' \
  --smoke \
  --stdout summary \
  --output /abs/goal-config-discovery.json \
  --state-output /abs/goal-config-state.json
```

`mixed-fast` tries a fixed ranking across configured opencode, Codex, Gemini, and generic antigravity harnesses. By default it may stop early after enough accepted routes; `--discover-all-candidates` disables that early accept stop and adds explicit `skipped_routes` and `unvisited_routes` evidence. Provider-specific opencode listing remains available with repeated `--discover-provider PROVIDER`.
This route-discovery flow is for discovery-path validation (discover all candidates and smoke traversal), not the default performance validation loop.

The discovery report includes `candidate_routes`, `accepted_routes`, and `rejected_routes`. Use the accepted route list to create the final explicit `goal.config.json`; do not pass unreviewed discovered routes directly into preflight.

If final accepted routes are unchanged between discovery and final config creation, reuse discovery smoke evidence to avoid duplicate smoke calls:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py" \
  --config /abs/goal.config.json \
  --require-models \
  --smoke \
  --reuse-smoke-report /abs/goal-config-discovery.json \
  --output /abs/goal-config-smoke.json \
  --state-output /abs/goal-config-state.json
```

Use this final accepted list to create the explicit goal config:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py" \
  --from-discovery /abs/goal-config-discovery.json \
  --mapping auto \
  --effort-profile balanced \
  --validation-mode smoke \
  --output /abs/goal.config.json \
  --state-output /abs/goal-config-state.json
```

`goal-config-state.json` records `phase`, `missing_preferences`, `next_command`, and `complete`. Use it to answer "is the config done?" deterministically.

After the check passes, pass both artifacts into preflight. This is the integration point: `create_goal_bundle.py` embeds `goal_config`, copies `goal.config.json` and `goal-config.check.json`, replaces manifest model policies with the configured ladders, and runtime packet generation turns those policies into concrete harness launch attempts.

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo \
  --out-dir /abs/bundle \
  --goal-config /abs/goal.config.json \
  --goal-config-check /abs/goal-config-check.json
```

## Preflight Briefs

Ask for clarification only when branch boundaries, DoD, merge policy, cleanup policy, required evidence, or runtime safety cannot be inferred safely. Conservative defaults include current branch as `base_ref` with fallback to `main`, `max_active_branch_agents: 4`, branch worker cap `4`, parallelism by default, and prompt audit as mandatory/fail-closed.

Use deterministic schema output instead of reading source:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" --brief-schema-json
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" --example-brief
```

Guided preflight command for the normal path:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/prepare_goal_bundle.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo \
  --out-dir /abs/bundle \
  --json
```

The guided command auto-detects candidate `goal.preflight.config.json` or `goal.config.json`, runs preflight compatibility with mechanical remediation when possible, writes `goal-config-selection.json`, persists canonical lint/repair/readiness artifacts in the bundle, and exits blocked rather than handing off a non-git directory-mode bundle as branch/worktree-ready. `preflight.pipeline.json` is compact by default; add `--verbose` only when you need the full config-selection and readiness payloads embedded in the pipeline result.

Manual preflight commands for debugging individual stages:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_preflight_brief.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo

python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" \
  --brief /abs/brief.json \
  --repo-root /abs/repo \
  --out-dir /abs/bundle

python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_goal_bundle.py" \
  --bundle-dir /abs/bundle

python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py" \
  --bundle-dir /abs/bundle

python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py" \
  --bundle-dir /abs/bundle --readiness

python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py" \
  --bundle-dir /abs/bundle --readiness --json
```

## Bundle Layout

```text
plans/orchestration/<job-id>/
  job.manifest.json
  main.prompt.md
  goal-bootloader.md
  orchestration.state.json
  resume.report.json
  PREFLIGHT_REPORT.md
  preflight.brief.lint.json
  preflight.lint.json
  repair-gate.json
  readiness.json
  goal-config-selection.json
  preflight.pipeline.json
  config-checks/                     # selected/remediated config checks when a config is supplied
  telemetry.summary.json              # runtime-created before final pass
  telemetry.debug.summary.json        # debug mode only
  run.trace.jsonl                     # debug mode structured full run trace
  audit/
  branches/
  workers/
  research/
  reviewers/
  schedulers/
  lite/
  amendments/
```

All manifest-owned paths are POSIX-relative to the bundle unless explicitly documented as repo-relative worktree or owned-file paths. Absolute paths, backslashes, `.` segments, `..`, path collisions, and unsafe packet ids are rejected. Worker artifacts live under `workers/<packet_id>/`; research artifacts under `research/<packet_id>/`; reviewers under `reviewers/<packet_id>/`; Lite packets under `lite/<packet_id>/`; amendments under `amendments/`.

Important manifest policies:

- `artifact_policy` and `cleanup_policy` preserve pass, partial, blocked, failed, unresolved, negative, and probe-only evidence unless the user authorizes cleanup.
- `parallelization` and branch `worker_parallelism` define rolling saturated scheduling.
- `worker_model_policy`, `research_worker_policy`, `review_model_policy`, `amender_model_policy`, and `lite_model_policy` define allowed model routes.
- `adaptation_policy` allows only future-work manifest amendments.
- `telemetry_policy` controls standard or debug telemetry.

## Resume and Debug Entrypoints

- For any non-terminal run, `orchestration.state.json` is the primary state snapshot and `resume.report.json` is the canonical resume/readiness view.
- These artifacts describe safe reuse, blocked/recoverable work, and the exact next command before relaunching work.
- Main runtime should run reconciliation before relaunching work and again during finalization, then require validator status contracts before reporting final runtime status.
- Packet-level compact debug surfaces are `packet.summary.json` files (route, attempts, outputs, changed files, and next action) plus `telemetry.json`.
- Debug-first flow should read `telemetry.debug.summary.json`, `run.trace.jsonl`, `orchestration.state.json`, then `resume.report.json` before opening packet-level logs.

## Scheduling

Branch and worker parallelism are rolling saturated pools:

- `max_active_branch_agents` is hard capped at 4.
- each branch has 1 to 4 work items and `max_active_worker_packets` capped at 4.
- waves are scheduling/order groups, not dependency barriers.
- `depends_on` is the only dependency mechanism and must reference prior ids.
- downstream work unlocks only after dependencies finish with `pass`.
- non-pass dependencies require `dependency_failed` scheduler evidence.
- under-capacity, defer, blocked, finish, close, and refill events belong in schema v2 scheduler ledgers.

Use scheduler helpers rather than hand-editing ledgers:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/render_branch_worktree_commands.py" --help
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/render_worker_schedule.py" --help
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py" --help
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/append_scheduler_event.py" --help
```

## Model Routing

Prompt audit is read-only and runs before branch creation. Its route is `gpt-5.5 -> gpt-5.4`, with deterministic audit available through `run_prompt_audit_phase.py --deterministic`.

Normal worker aliases, in default order:

1. `gemini-pro` (`gemini-3.1-pro-preview`)
2. `gemini-flash` (`gemini-3-flash-preview`)
3. `codex-spark` (`gpt-5.3-codex-spark`)
4. `codex-mini` (`gpt-5.4-mini`)

Worker route classes:

- `mechanical` and `docs`: `codex-mini`
- `small-edit` and `normal-code`: `codex-spark -> codex-mini`
- `complex-code` and `custom`: full ordered ladder when justified

The branch orchestrator may choose a non-empty ordered subsequence with a concrete `selection_reason`. Passing the fresh `model-catalog.json` lets packet generation prune unsupported Codex aliases and reject unavailable explicit selections.

Research workers use `codex --search exec --ephemeral -s read-only` with user config loaded. They may use Codex native search, configured read-only CLI/MCP/connector/browser/search tools, remote APIs, package metadata, shell/network inspection, and local read-only files. They must not edit files, inspect secrets or unrelated private files, or perform state-changing actions.

Reviewer routes are selected from `review_model_policy`:

- `light`: `gpt-5.4-mini -> gpt-5.4`
- `standard`: `gpt-5.4 -> gpt-5.5`
- `heavy`: `gpt-5.5 -> gpt-5.4`

Reviewers are read-only and require a passing `pre_review_gate.json`. Reviewer reuse is valid only when semantic hashes match and both source review and source telemetry exist.

Plan-amender default route is `gpt-5.4 -> gpt-5.4-mini`; `gpt-5.5` is allowed only with a concrete reason. Deterministic blocker-repair packets use local status-artifact parsing and alias `deterministic-blocker-repair` instead of a model call.

Check the local Codex model catalog:

```bash
npm run check:models
npm run models:catalog
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py" --json --require-codex
```

## Telemetry

Every prompt-audit, worker, research-worker, reviewer, plan-amender, and Lite launcher writes packet-local `telemetry.json`. Telemetry records model aliases, provider/model ids, declared/called/accepted attempts, prompt/output/log character and byte counts, best-effort token usage when provider logs expose it, timeout seconds, and terminal attempt status.

Costs are measured as text and time, not USD. Do not add pricing, dollar amounts, or USD budget fields.

Summarize standard telemetry before final status validation:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py" \
  --bundle-dir /abs/bundle
```

Enable debug telemetry in the preflight brief. There is no runtime flag; `job.manifest.json.telemetry_policy` owns the mode. The lean user-facing shorthand is:

```json
"telemetry_mode": "debug"
```

`debug_telemetry: true` is also accepted for compatibility. Both shorthands expand to:

```json
"telemetry_policy": {
  "schema_version": 1,
  "mode": "debug",
  "raw_text": false,
  "collect": [
    "route_decisions",
    "token_usage",
    "timings",
    "scheduler_utilization",
    "context_pack_stats",
    "validator_runs",
    "artifact_hashes"
  ]
}
```

If a user says "use goal-preflight in debug mode," the preflight agent should set `telemetry_mode: "debug"` in the structured brief. Debug mode is passive: it must not change route selection, polling cadence, scheduling, watchdog thresholds, or validation outcomes. It adds packet-level `telemetry.debug.json`, append-only `debug.events.jsonl`, bundle-level `telemetry.debug.summary.json`, and root `run.trace.jsonl`. Raw prompts, raw model outputs, full logs, secrets, and USD/pricing fields remain prohibited; `raw_text` must be `false`.

`run.trace.jsonl` is the investigation artifact for efficiency and stall analysis. It indexes scheduler events, packet debug start/end events, launcher state transitions, model attempts, packet telemetry, and terminal artifacts by path, status, hashes, counts, timings, and token usage where available. It does not embed raw log lines, raw prompt text, or raw model output.

Generate or refresh the debug summary and trace:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py" \
  --bundle-dir /abs/bundle \
  --debug
```

Inspect a debug run:

```bash
jq '.model_usage, .text_metrics, .time_metrics, .trace' /abs/bundle/telemetry.debug.summary.json
jq -c 'select(.event_type=="scheduler_event" or .event_type=="launcher_state" or .event_type=="model_attempt")' /abs/bundle/run.trace.jsonl
```

## Lite Advisors

Lite advisors are optional Gemini Flash Lite packets for context routing only. They cannot satisfy prompt audit, worker pass, reviewer pass, mergeability, scientific claim support, or DoD evidence. Always validate Lite output before use and then open only cited original files or spans needed for verification.

Allowed purposes:

| Skill | Purposes |
| --- | --- |
| `goal-preflight` | `preflight-decomposition`, `lint-repair` |
| `goal-main-orchestrator` | `audit-defect-summary`, `main-summary` |
| `goal-branch-orchestrator` | `branch-packet-planning`, `context-pack`, `worker-summary`, `blocked-triage` |
| `goal-plan-amender` | `amendment-summary`, `amendment-defect-summary` |

Create and validate Lite packets through the skill wrapper:

```bash
python3 "$GOAL_SKILLS_ROOT/<skill-name>/scripts/create_lite_advice_packet.py" --help
python3 "$GOAL_SKILLS_ROOT/<skill-name>/scripts/validate_lite_advice.py" --help
```

Lite packets capture the Gemini binary path, version, binary sha256, input hashes, task hash, prompt hash, advice, and telemetry. Runtime status files must record every used or ignored relevant Lite packet; validators scan `lite/` for unrecorded packets.

## Validation Gates

| Gate | Required command family |
| --- | --- |
| Goal configuration | `scan_configurables.py`, `create_goal_config.py`, `check_goal_config.py` |
| Skill bootstrap | `check_goal_skill_availability.py` |
| Brief lint | `lint_preflight_brief.py` |
| Bundle lint | `lint_goal_bundle.py` |
| Prompt audit | `run_prompt_audit_phase.py`, `validate_prompt_audit.py` |
| Branch scheduling | `render_branch_worktree_commands.py`, `scheduler_tick.py` |
| Worker scheduling | `render_worker_schedule.py`, `scheduler_tick.py` |
| Worker/research/reviewer packets | `create_runtime_packet.py`, packet `launch.sh`, `runtime_packet_runner.py` |
| Branch assembly | `assemble_branch_status.py`, `validate_branch_status.py` |
| Pre-review gate | `create_pre_review_gate.py` |
| Main assembly | `summarize_telemetry.py`, `assemble_main_status.py`, `validate_main_status.py` |
| Amendments | `recommend_amendment_decision.py`, `create_amendment_decision.py`, `create_adaptation_packet.py`, `validate_amender_packet.py`, `validate_manifest_amendment.py`, `apply_manifest_amendment.py` |

Use exact absolute paths for script arguments when the phase manifest requires them. Validate artifacts before reporting `pass`. Do not hand-author final branch or main status when assemblers can derive it from manifest-owned artifacts.

## CLI Surface

Goal config:

- `scan_configurables.py`: inventory configurable aggressiveness, model route, harness, timeout, and telemetry knobs.
- `create_goal_config.py`: render a deterministic `goal.config.json` profile, including the `opencode-deepseek-v4` preset, role-model overrides, model ladders, and custom harness specs.
- `check_goal_config.py`: validate provider/model availability and optionally smoke-test configured harness roles; a passing report is required before preflight consumes the config.

Preflight:

- `lint_preflight_brief.py`: validate a structured brief before bundle generation.
- `create_goal_bundle.py`: print schema/examples or generate `job.manifest.json`, prompts, bootloader, and report.
- `lint_goal_bundle.py`: validate generated bundle structure and policy.
- `render_goal_bootloader.py`: render or rewrite location-bound bootloader paths.

Main:

- `create_audit_packet.py`, `runtime_prompt_audit_runner.py`, `deterministic_prompt_audit.py`, `run_prompt_audit_phase.py`, `validate_prompt_audit.py`.
- `render_branch_worktree_commands.py`: list eligible branches after audit and render a native-first branch delegation plan with an explicit CLI worktree fallback reason.
- `assemble_main_status.py`, `validate_main_status.py`.
- `summarize_telemetry.py`: write standard or debug telemetry summaries.

Branch:

- `render_worker_schedule.py`: list ready workers under the branch cap.
- `context_pack.py`: create bounded context packs.
- `create_runtime_packet.py`, `runtime_packet_runner.py`: create/run worker, research-worker, and reviewer packets.
- `create_pre_review_gate.py`: bind review to current branch evidence and semantic hashes.
- `assemble_branch_status.py`, `validate_branch_status.py`.

Plan amendment:

- `recommend_amendment_decision.py`, `create_amendment_decision.py`.
- `create_blocker_repair_packet.py`: deterministic repair proposal from terminal blocker evidence.
- `create_adaptation_packet.py`, `validate_amender_packet.py`.
- `validate_manifest_amendment.py`, `apply_manifest_amendment.py`.

Shared wrappers available under each installed skill:

- `runtime_phase_manifest.py`
- `check_goal_skill_availability.py`
- `check_model_catalog.py`
- `scheduler_tick.py`
- `append_scheduler_event.py`
- `script_only_repair_gate.py`
- `create_lite_advice_packet.py`
- `validate_lite_advice.py`
- `runtime_lite_runner.py`
- `extract_telemetry.py`
- `context_pack.py`

Internal support modules such as `amendment_lib.py`, `orchestration_contract.py`, `path_rules.py`, and `status_validation.py` define shared contracts and helpers. They are not normal runtime entrypoints; prefer phase manifests, CLI `--help`, and validator output unless implementing or debugging those modules.

## Maintainer Checks

Run the full deterministic gate before release-oriented changes:

```bash
npm run check
git diff --check
```

Focused checks:

```bash
npm run check:shared
npm run check:config
npm run check:fixtures
npm run check:golden
npm run check:release
npm run check:maintenance
npm run check:models
npm run check:context
```

Machine-readable maintenance reports:

```bash
python3 scripts/generate_agent_context_index.py --json
python3 scripts/check_model_catalog.py --json
python3 scripts/check_size_budget.py --json
python3 scripts/check_dependency_policy.py --json
```

`check:config` validates goal-config fixtures without launching live model CLIs. `check:fixtures` validates deterministic fixtures without launching live model CLIs. `check:golden` installs into a temporary skills root, creates an offline smoke bundle, generates audit/scheduler/worker/research/reviewer/Lite/amendment/telemetry artifacts, and validates them. `check:release` validates package metadata, installer behavior, temp install parity, and `npm pack --dry-run`.

`check:maintenance` runs generated context index checks, size-budget warnings, dependency policy, and local model catalog compatibility. Size-budget growth is warning-first and uses `git ls-files`; update `maintenance/size-budget.json` only for intentional growth:

```bash
python3 scripts/check_size_budget.py --update
```

Optional quality tools:

```bash
npm ci
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm run check:quality
```

Runtime npm dependencies are forbidden unless explicitly allowlisted in `maintenance/dependency-policy.json`. Development tooling belongs in `devDependencies` or `requirements-dev.txt`, and Dependabot must cover every dependency manifest.

## Release

Before tagging:

```bash
npm run check
npm run check:release -- --require-clean
git diff --check
```

Update the `package.json` version, `package-lock.json` version, and all public skill `SKILL.md` frontmatter versions together. Regenerate `maintenance/agent-context-index.json` after tracked file additions, removals, or navigation-relevant edits:

```bash
npm run generate:context
npm run check:context
```

Tag from a clean tree:

```bash
git tag -a v<package.json version> -m "Release v<package.json version>"
git push origin main v<package.json version>
```

CI has two jobs: a deterministic gate that compiles scripts, runs `npm run check`, verifies generated files/package contents/temp install parity, and checks whitespace; and a maintenance report job that uploads size, dependency, model-catalog, Ruff, and Pyright reports.
