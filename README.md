# Codex Goal Orchestration Skills

Install four Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`
- `goal-plan-amender`

These are packaged in one repository and reference each other by skill name, not by separate repository URLs:

- `goal-preflight` creates linted, path-safe job bundles and a location-bound `/goal` bootloader for `$goal-main-orchestrator`; it can optionally use CLI-only Lite advisory packets for source digestion or lint-repair advice.
- `goal-main-orchestrator` runs bootstrap and prompt audit, can optionally use Lite only after audit or completed branch artifacts for summaries, creates validated branch worktrees, dispatches `$goal-branch-orchestrator` sessions as a rolling saturated pool within the hard active-agent limit, launches route-bound `$goal-plan-amender` packets only after validated terminal branch results when future-work adaptation is needed, and records branch scheduler evidence without loading the branch skill body into main context.
- `goal-branch-orchestrator` can optionally use Lite for packet planning, context packing, completed-worker summaries, or blocked triage; it creates path-safe worker, research-worker, deterministic route-gated reviewer packets, dispatches normal workers through an allowed Gemini Pro -> Gemini Flash -> Codex Spark -> GitHub Copilot `gpt-5.4` high-effort -> Codex mini ladder, dispatches research workers through broad read-only information retrieval with Codex native search, configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and local read-only file access, waits on active packet launchers without polling their logs, integrates results, sends read-only reviewer packets only after deterministic pre-review gates pass, and assembles branch status from manifest-owned artifacts.
- `goal-plan-amender` creates file-backed adaptation packets with selected amender model ladders and telemetry, can create deterministic local-script blocker-repair packets from terminal status artifacts, validates amendment proposals against the live manifest and terminal/active branch immutability rules, applies accepted changes only as new unstarted work, archives prior manifests, regenerates changed future branch prompts through preflight helpers, and reruns bundle lint.

## Install

```bash
npx github:jc1122/codex-goal-orchestration-skills
```

Install a pinned release tag:

```bash
npx github:jc1122/codex-goal-orchestration-skills#v0.2.19
```

The installer copies bundled skills to `$CODEX_HOME/skills` when `CODEX_HOME` is set, otherwise to `~/.codex/skills`. The destination must resolve to an absolute path.

Use a custom destination:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --dest /path/to/skills
```

Custom destinations must be absolute and must not contain `..` traversal.

List bundled skills:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --list
```

Dry run:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --dry-run
```

## Maintainer Checks

```bash
npm run check
npm run check:shared
npm run check:fixtures
npm run check:golden
npm run check:release
npm run check:maintenance
npm run check:models
npm run check:context
```

`check:fixtures` validates the preparedness fixture bundle without launching live model CLIs. It covers timeout-wrapped launcher generation, valid broad-access research-worker artifacts, preflight brief linting, schema v2 scheduler refill/under-capacity/stuck-worker/stuck-branch/dependency-failed/watchdog closeout fixtures, deterministic pre-review gate creation, conservative branch status assembly, review router tiers including premium escalation, failed pre-review gate blocking reviewer launch, topology lint failures, amendment validation/apply fixtures, rejection of old self-reported saturation without a ledger, rejection of obsolete narrow research policy text, and rejection of unsafe research-worker command evidence.

`check:golden` installs the skills into a temporary skills root, creates a temporary git repository, builds a complete offline smoke bundle from that installed copy, generates audit, scheduler, worker, research-worker, pre-review gate, reviewer route, reviewer, Lite, branch, main, amendment, and telemetry-summary artifacts, then validates them with the installed runtime validators. It also checks negative stale-telemetry, reviewer route/telemetry mismatch, reviewer semantic-hash mismatch, accepted reviewer reuse without a fresh model call, missing reuse-source telemetry, and partial-subset fixtures.

`check:release` validates release metadata, installer `--list`/`--version`, temp install parity, and `npm pack --dry-run --json` package contents.

`check:maintenance` runs warning-first repository guardrails. It reports tracked file counts, lines, characters, approximate tokens, per-skill size, runtime dependency policy, Dependabot coverage, and local Codex model catalog compatibility. The model catalog check uses `codex debug models` when available, then falls back to `codex debug models --bundled`; absence of the Codex CLI is reported as skipped so CI remains portable. The size budget is stored in `maintenance/size-budget.json` and uses `git ls-files`, so ignored caches and untracked scratch files do not count. Refresh the budget only for intentional growth:

```bash
python3 scripts/check_size_budget.py --update
```

Machine-readable maintenance reports are available with:

```bash
python3 scripts/generate_agent_context_index.py --json
python3 scripts/check_model_catalog.py --json
python3 scripts/check_size_budget.py --json
python3 scripts/check_dependency_policy.py --json
```

### Codex Model Catalog

Model route aliases are defined in `skills/_goal_shared/scripts/orchestration_contract.py` and are checked against the local Codex catalog:

```bash
npm run check:models
npm run models:catalog
```

`scripts/check_model_catalog.py` prefers `codex debug models`, which returns the refreshed account-visible catalog. It falls back to `codex debug models --bundled` only when the live catalog is unavailable, because the bundled catalog is shipped with the CLI binary and can lag behind models available to the current account. A model with `supported_in_api=false` may still be usable through `codex exec`; this is expected for route aliases such as Codex Spark.

The same checker is installed into each skill as `scripts/check_model_catalog.py`, so runtime bootstraps can record a fresh `model-catalog.json` before prompt audit, branch scheduling, worker route selection, or reviewer route selection.

Do not update packet validators just because the local model bundle changed. Validators should preserve alias and telemetry consistency for already-created artifacts. Update route aliases only when `npm run check:models` shows the live catalog no longer contains a configured model or when intentionally adopting a new route.

Agent navigation is generated for token-efficient repo entry. Agents should read `AGENTS.md`, then `maintenance/agent-context-index.json`, before broad scans. Regenerate it after moving, adding, or deleting navigation-relevant files:

```bash
npm run generate:context
npm run check:context
```

Runtime skill entrypoints are intentionally small wrappers. Each installed skill exposes a compact phase table:

```bash
python3 "$CODEX_HOME/skills/goal-main-orchestrator/scripts/runtime_phase_manifest.py" --markdown
python3 "$CODEX_HOME/skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py" --markdown
```

Runtime agents should follow those phase tables, script `--help` output, JSON artifacts, and validator defects before opening long references. Python script source is an implementation/debug surface, not normal runtime context; do not search it with `rg` or `grep` during ordinary runs.

Preflight brief shape is available from deterministic script output, so agents do not need to inspect `create_goal_bundle.py`:

```bash
python3 "$CODEX_HOME/skills/goal-preflight/scripts/create_goal_bundle.py" --brief-schema-json
python3 "$CODEX_HOME/skills/goal-preflight/scripts/create_goal_bundle.py" --example-brief
```

Generated `main.prompt.md`, branch prompts, prompt-audit packets, and `goal-bootloader.md` are intentionally compact. They carry job-specific data and point runtime agents at `job.manifest.json`, phase manifests, script outputs, and validators instead of repeating long orchestration policy in every prompt. Bundle lint now checks that generated prompts point agents at `runtime_phase_manifest.py --markdown` and explicitly discourage reading skill Python source during normal runtime.

Generated worker packets are compact too: when `create_runtime_packet.py` receives `job.manifest.json`, it writes a deterministic `packet-context.json` branch/work-item slice and removes the full manifest excerpt from `prompt.md`. Normal worker `launch.sh` files are tiny wrappers; provider attempts, probes, selected route commands, timeout policy, and terminal blocked metadata live in packet-local `launch-config.json`. Gemini worker attempts still pass the full prompt on stdin so process inspection does not expose or re-tokenize the worker prompt.

Generated research-worker and reviewer packets use the same wrapper pattern. Runtime attempt policy, telemetry inputs, semantic hashes, and terminal blocked metadata live in packet-local `launch-config.json`, and `runtime_packet_runner.py` performs deterministic execution. Agents should inspect `launch-config.json` and generated artifacts instead of opening launcher implementation source.

Lite advisory launchers use the same stdin pattern for Gemini prompts and keep the full prompt out of process command lines.

Optional quality tooling is pinned separately from runtime code:

```bash
npm ci
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm run check:quality
```

Agent-assisted maintenance should follow `maintenance/AGENT_MAINTENANCE.md`: read deterministic reports first, prefer consolidation over new prose, and leave size-budget updates explicit.

## Release

Before creating a production candidate tag:

```bash
npm run check
npm run check:release -- --require-clean
git tag v<package.json version>
git push origin main v<package.json version>
```

Update `package.json` version before tagging. Do not tag from a dirty tree.

## Workflow

1. Use `goal-preflight` to turn a roadmap, diagnosis, report, or rough brief into a linted goal bundle.
2. Paste the generated `goal-bootloader.md` text into Copilot `/goal`.
3. The `/goal` runtime uses `goal-main-orchestrator`.
4. Runtime bootstrap checks that required skills and scripts are available before prompt audit.
5. The main orchestrator launches branch sessions that use `goal-branch-orchestrator`.
6. After each validated terminal branch result, main records an amender launch-or-skip decision; launch decisions run `goal-plan-amender` to adapt future work or add deterministic blocker-repair branches through accepted amendment artifacts.

The runtime enforces skill availability bootstrap, absolute CLI entry paths, reproducible and collision-free manifest paths, fixed prompt-audit and allowed worker model fallback chains, prompt audit before branch work, rolling branch orchestration, rolling worker orchestration, explicit amendment launch-or-skip decisions for every terminal branch checkpoint, route-bound amendment proposals for future unstarted work plus deterministic blocker-repair packets that add new repair branches, wait-not-poll branch supervision, a hard limit of 4 active branch orchestrator agents, deterministic worker packet ids, artifact-backed manifest-bound branch and main status validation, deterministic schema v2 scheduler ledgers, deterministic packet telemetry, bounded launcher attempt timeouts, research-worker security checks, deterministic pre-review gates, deterministic branch status assembly, route-bound reviewer and plan-amender telemetry, semantic-hash-bound reviewer reuse, and a hard limit of 4 worker packets per branch. `max_active_branch_agents` is the real branch concurrency cap: main keeps slots saturated up to that cap, launches the next eligible branch when one finishes with `pass`, closes finished branch orchestrator agents before replacements, records `schedulers/main.scheduler.json`, and defers only branches with incomplete explicit manifest `depends_on` branch ids or a structured reason. `partial`, `blocked`, and `failed` dependencies do not unlock downstream work; downstream ids must be blocked or deferred with `reason_code: "dependency_failed"` evidence unless an accepted amendment adds a valid recovery path as future work. Waves are scheduling/order groups, not implicit dependency barriers. Accepted amendments write `amendments/Axxx.decision.json`, `amendments/Axxx.packet/route.json`, `amendments/Axxx.packet/telemetry.json`, `amendments/Axxx.packet/packet.validation.json`, `amendments/Axxx.proposal.json`, `Axxx.validation.json`, `Axxx.accepted.json`, archive the prior manifest, regenerate changed future branch prompts, rerun lint, and must not mutate active or terminal branch evidence. `max_active_worker_packets` is the real branch-local worker concurrency cap: branch keeps worker launcher slots saturated up to that cap, launches the next eligible worker when one finishes with `pass` and is integrated, records `schedulers/<branch-id>.worker.scheduler.json`, and defers only workers with incomplete explicit manifest work-item `depends_on` ids or a structured reason. Scheduler v2 events require ordered `seq`, `timestamp`, `runtime_ref`, and enum `reason_code` for `defer`, `under_capacity`, and `blocked`; `scheduler_tick.py` handles normal ready/launch/finish/close/refill bookkeeping and `append_scheduler_event.py` remains available for explicit unusual events. A manifest worker id may be relaunched after a non-pass attempt is finished and closed, which enables reviewer-feedback repairs with `create_runtime_packet.py --replace` while preserving the old packet under `attempts/`; undeclared repair worker ids remain invalid. Validators reconstruct active counts from scheduler events and reject duplicate active launches, missing finishes/closes, cap overflow, missing refill events, stale manifest hashes, non-pass dependency launches, vague reason text, and eligible-idle gaps without structured evidence. Default normal-worker fallback order is Gemini Pro, Gemini Flash, Codex Spark, GitHub Copilot `gpt-5.4` high effort, then Codex mini; branch orchestrators may choose a non-empty ordered subsequence per normal worker packet with a recorded `selection_reason`, and validators reject route drift from manifest-owned `workers/<packet_id>/route.json`. Plan-amender packets default to `gpt-5.4 -> gpt-5.4-mini`, may select allowed `gpt-5.5` routes with a recorded reason, and use read-only bounded Codex attempts; deterministic blocker-repair packets use local status-artifact parsing with alias `deterministic-blocker-repair` and do not call a model. Research-worker packets are declared with `worker_type: "research-worker"` and run `codex --search exec --ephemeral -s read-only` with user config loaded; they may use broad read-only information retrieval through Codex native search, configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and local read-only file inspection, and their artifacts must live under `research/<packet_id>/research.json` with search queries when used, source URLs, tools used, local files read, command evidence, and telemetry. Every audit, worker, research-worker, reviewer, plan-amender, and Lite launcher writes packet-local `telemetry.json` with declared/called/accepted model aliases, provider/model ids, prompt/output/log character and byte counts, attempt timeout seconds, and best-effort token usage parsed from provider logs when exposed; main writes a current `telemetry.summary.json` with bundle-level totals and separate premium `gpt-5.5` audit/reviewer attempt accounting before final validation. Reviewer packet generation requires a passing schema v2 branch-local `pre_review_gate.json`; the generated `reviewers/<packet_id>/route.json` selects `light` (`gpt-5.4-mini -> gpt-5.4`), `standard` (`gpt-5.4 -> gpt-5.5`), or `heavy` (`gpt-5.5 -> gpt-5.4`) from `review_model_policy`, and reviewer telemetry aliases must match that route exactly. Reviewer outputs must copy gate `semantic_input_hashes` exactly. Reuse is accepted only when semantic hashes match and both source review and source telemetry exist; no fresh reviewer model call is required for accepted reuse. Lite advisory packets, when used, run Gemini Flash Lite through the CLI in read-only plan mode, capture the absolute Gemini binary, version, and binary sha256 at packet creation, rehash source inputs, `task.md`, `prompt.md`, and the Gemini binary before launch and validation, regenerate prompts from `input-files.json` plus `task.md`, enforce skill-specific purpose allowlists, reject silent packet overwrites, and write validated `advice.json`; Lite is a context router only and cannot satisfy audit, review, mergeability, scientific claim, or DoD evidence. Preflight manifests require `adaptation_policy`, `amender_model_policy`, `review_model_policy`, `orchestration_watchdog`, branch `owned_paths`, and `preflight_lite_advice` provenance; preflight/main/branch validators scan manifest-owned `lite/` for relevant unrecorded Lite packet directories. Branch and main status validators require manifest-owned `lite_advice` audit records, empty only when no relevant runtime Lite packet exists, and reject obvious state-changing or secret-inspection research commands. Preflight defaults to parallel decomposition, allows at most 5 scheduling groups of 4 branches, requires explicit `serial_reasons` when branch or worker topology underfills capacity, and writes explicit cleanup/artifact policies so partial or blocked runs preserve inspection evidence unless the user authorizes cleanup. `partial` means some work completed with structured unlaunched or blocked remainder; `pass` requires all manifest branches and workers launched and validated. Bundle prompt/status/review/pre-review-gate/scheduler/amendment paths are relative to the bundle root; worktree paths are relative to the repository root; normal worker status artifacts must live at manifest-owned `workers/<packet_id>/status.json` paths and research-worker status artifacts must live at manifest-owned `research/<packet_id>/research.json` paths.

Generated `goal-bootloader.md` files are location-bound because they embed absolute bundle and repository roots. If a bundle or repository checkout is moved, rerun `goal-preflight` or run `render_goal_bootloader.py --repo-root /absolute/path/to/repo --write`; do not hand-edit the bootloader paths.

When a plan-amender packet selects `gpt-5.5`, `telemetry.summary.json` records it in a dedicated `premium_usage.amender_gpt_5_5` bucket alongside audit and reviewer premium buckets.
