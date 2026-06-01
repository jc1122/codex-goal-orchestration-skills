# Codex Goal Orchestration Skills

This repository packages four Codex skills for file-backed `/goal` orchestration:

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
npx github:jc1122/codex-goal-orchestration-skills#v0.2.53
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

The default destination is `$CODEX_HOME/skills` when `CODEX_HOME` is set, otherwise `~/.codex/skills`. The installer copies all four public skills, `_goal_shared`, root `AGENTS.md`, and `maintenance/agent-context-index.json`.

Resolve the installed skills root in runtime instructions:

```bash
GOAL_SKILLS_ROOT="${CODEX_HOME:-$HOME/.codex}/skills"
if [ ! -d "$GOAL_SKILLS_ROOT/goal-preflight" ] && [ -d "$HOME/.agents/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
fi
```

## Agent Start

1. Read `AGENTS.md`, then `maintenance/agent-context-index.json`.
2. Read the relevant skill `SKILL.md`.
3. Print and follow the skill phase manifest:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/runtime_phase_manifest.py" --markdown
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/runtime_phase_manifest.py" --markdown
```

Do not read or search `skills/*/scripts/*.py` during normal runtime. Use script outputs, `--help`, generated artifacts, and validators. Open Python source only when implementing or debugging that script surface.

## Architecture

| Layer | Skill | Responsibility |
| --- | --- | --- |
| Preflight | `goal-preflight` | Turn a report, roadmap, diagnosis, or rough brief into a linted bundle with `job.manifest.json`, `main.prompt.md`, branch prompts, and a location-bound `goal-bootloader.md`. |
| Main runtime | `goal-main-orchestrator` | Consume an existing bundle, bootstrap skills/model catalog, fail-closed prompt audit, create branch worktrees, schedule branch agents, summarize telemetry, assemble and validate final status. |
| Branch runtime | `goal-branch-orchestrator` | Run one audited branch worktree, create worker/research/reviewer packets, keep worker slots saturated, integrate results, gate review, assemble and validate branch status. |
| Amendment runtime | `goal-plan-amender` | After validated terminal branch evidence, propose, validate, and optionally apply future-work-only manifest amendments or deterministic blocker-repair branches. |

Shared `_goal_shared` support includes skill availability checks, model catalog checks, path rules, runtime phase manifests, Lite packet creation/validation, telemetry extraction, context packing, scheduler ledgers, status validation helpers, and script-only repair gates.

The bundle is the data plane. Runtime agents exchange manifest-owned files rather than hidden chat state. Prompt audit, branch statuses, reviews, scheduler ledgers, telemetry, Lite advice, and amendments must be validated from disk before pass claims.

## End-To-End Flow

1. `goal-preflight` writes a structured brief, lints it, creates the bundle, lints the bundle, and returns the exact `goal-bootloader.md` text.
2. The user launches `/goal` with the bootloader. The bootloader points to absolute bundle and repository roots, so it must be regenerated if either path moves.
3. `goal-main-orchestrator` runs bootstrap, live model catalog, script-only repair gate, deterministic or model prompt audit, branch scheduling, and main scheduler ledger updates.
4. Main launches eligible branch orchestrator sessions as a rolling saturated pool up to `max_active_branch_agents` and waits for terminal artifacts rather than polling active branch internals.
5. `goal-branch-orchestrator` creates worker or research-worker packets, runs launchers, integrates diffs or research findings, updates worker scheduler evidence, creates a pre-review gate, launches or reuses reviewer evidence, and validates branch status.
6. After each validated terminal branch result, main records an amendment launch-or-skip decision. `goal-plan-amender` may add or adjust only future unstarted work through validated amendments.
7. Main closes by running scheduler finalization, `summarize_telemetry.py`, `assemble_main_status.py`, and `validate_main_status.py`.

Status semantics:

- `pass`: every manifest branch and worker/research item launched, validated, reviewed where required, and satisfies DoD.
- `partial`: some work completed, with structured unlaunched or blocked remainder.
- `blocked`: progress is stopped by a concrete blocker with preserved evidence.
- `failed`: attempted work produced a terminal negative result.

## Preflight Briefs

Ask for clarification only when branch boundaries, DoD, merge policy, cleanup policy, required evidence, or runtime safety cannot be inferred safely. Conservative defaults include current branch as `base_ref` with fallback to `main`, `max_active_branch_agents: 4`, branch worker cap `4`, parallelism by default, and prompt audit as mandatory/fail-closed.

Use deterministic schema output instead of reading source:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" --brief-schema-json
python3 "$GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py" --example-brief
```

Core preflight commands:

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
```

## Bundle Layout

```text
plans/orchestration/<job-id>/
  job.manifest.json
  main.prompt.md
  goal-bootloader.md
  PREFLIGHT_REPORT.md
  preflight.lint.json
  telemetry.summary.json              # runtime-created before final pass
  telemetry.debug.summary.json        # debug mode only
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
4. `copilot-gpt-5.4` (`gpt-5.4`, high effort)
5. `codex-mini` (`gpt-5.4-mini`)

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

If a user says "use goal-preflight in debug mode," the preflight agent should set `telemetry_mode: "debug"` in the structured brief. Debug mode is passive: it must not change route selection, polling cadence, scheduling, watchdog thresholds, or validation outcomes. It adds packet-level `telemetry.debug.json`, append-only `debug.events.jsonl`, and bundle-level `telemetry.debug.summary.json`. Raw prompts, raw model outputs, full logs, secrets, and USD/pricing fields remain prohibited; `raw_text` must be `false`.

Generate or refresh the debug summary:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py" \
  --bundle-dir /abs/bundle \
  --debug
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

Preflight:

- `lint_preflight_brief.py`: validate a structured brief before bundle generation.
- `create_goal_bundle.py`: print schema/examples or generate `job.manifest.json`, prompts, bootloader, and report.
- `lint_goal_bundle.py`: validate generated bundle structure and policy.
- `render_goal_bootloader.py`: render or rewrite location-bound bootloader paths.

Main:

- `create_audit_packet.py`, `runtime_prompt_audit_runner.py`, `deterministic_prompt_audit.py`, `run_prompt_audit_phase.py`, `validate_prompt_audit.py`.
- `render_branch_worktree_commands.py`: list/create eligible branch worktrees after audit.
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

`check:fixtures` validates deterministic fixtures without launching live model CLIs. `check:golden` installs into a temporary skills root, creates an offline smoke bundle, generates audit/scheduler/worker/research/reviewer/Lite/amendment/telemetry artifacts, and validates them. `check:release` validates package metadata, installer behavior, temp install parity, and `npm pack --dry-run`.

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
