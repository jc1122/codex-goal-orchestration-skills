# Codex Goal Orchestration Skills

Install three Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`

These are packaged in one repository and reference each other by skill name, not by separate repository URLs:

- `goal-preflight` creates linted, path-safe job bundles and a location-bound `/goal` bootloader for `$goal-main-orchestrator`; it can optionally use CLI-only Lite advisory packets for source digestion or lint-repair advice.
- `goal-main-orchestrator` runs bootstrap and prompt audit, can optionally use Lite only after audit or completed branch artifacts for summaries, creates validated branch worktrees, and dispatches `$goal-branch-orchestrator` sessions as a rolling saturated pool within the hard active-agent limit without loading the branch skill body into main context.
- `goal-branch-orchestrator` can optionally use Lite for packet planning, context packing, completed-worker summaries, or blocked triage; it creates path-safe worker, research-worker, and reviewer packets, dispatches normal workers through an allowed Gemini Pro -> Gemini Flash -> Codex Spark -> GitHub Copilot `gpt-5.4` high-effort -> Codex mini ladder, dispatches research workers through broad read-only information retrieval with Codex native search, configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and local read-only file access, waits on active packet launchers without polling their logs, integrates results, and sends read-only reviewer packets.

## Install

```bash
npx github:jc1122/codex-goal-orchestration-skills
```

Install a pinned release tag:

```bash
npx github:jc1122/codex-goal-orchestration-skills#v0.2.0
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
npm run check:shared
npm run check:fixtures
npm run check:golden
npm run check:release
```

`check:fixtures` validates the preparedness fixture bundle without launching live model CLIs. It covers timeout-wrapped launcher generation, valid broad-access research-worker artifacts, rejection of obsolete narrow research policy text, and rejection of unsafe research-worker command evidence.

`check:golden` installs the skills into a temporary skills root, creates a temporary git repository, builds a complete offline smoke bundle from that installed copy, generates audit, worker, research-worker, reviewer, Lite, branch, main, and telemetry-summary artifacts, then validates them with the installed runtime validators.

`check:release` validates release metadata, installer `--list`/`--version`, temp install parity, and `npm pack --dry-run --json` package contents.

## Release

Before creating a production candidate tag:

```bash
npm run check:shared
npm run check:fixtures
npm run check:golden
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

The runtime enforces skill availability bootstrap, absolute CLI entry paths, reproducible and collision-free manifest paths, fixed prompt-audit and allowed worker model fallback chains, prompt audit before branch work, rolling branch orchestration, rolling worker orchestration, wait-not-poll branch supervision, a hard limit of 4 active branch orchestrator agents, deterministic worker packet ids, artifact-backed manifest-bound branch and main status validation, deterministic packet telemetry, bounded launcher attempt timeouts, research-worker security checks, and a hard limit of 4 worker packets per branch. `max_active_branch_agents` is the real branch concurrency cap: main keeps slots saturated up to that cap, launches the next eligible branch when one finishes, closes finished branch orchestrator agents before replacements, and defers only branches with incomplete explicit manifest `depends_on` branch ids. Waves are scheduling/order groups, not implicit dependency barriers. `max_active_worker_packets` is the real branch-local worker concurrency cap: branch keeps worker launcher slots saturated up to that cap, launches the next eligible worker when one finishes and is integrated, and defers only workers with incomplete explicit manifest work-item `depends_on` ids. Default normal-worker fallback order is Gemini Pro, Gemini Flash, Codex Spark, GitHub Copilot `gpt-5.4` high effort, then Codex mini; branch orchestrators may choose a non-empty ordered subsequence per normal worker packet with a recorded `selection_reason`, and validators reject route drift from manifest-owned `workers/<packet_id>/route.json`. Research-worker packets are declared with `worker_type: "research-worker"` and run `codex --search exec --ephemeral -s read-only` with user config loaded; they may use broad read-only information retrieval through Codex native search, configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and local read-only file inspection, and their artifacts must live under `research/<packet_id>/research.json` with search queries when used, source URLs, tools used, local files read, command evidence, and telemetry. Every audit, worker, research-worker, reviewer, and Lite launcher writes packet-local `telemetry.json` with declared/called/accepted model aliases, provider/model ids, prompt/output/log character and byte counts, attempt timeout seconds, and best-effort token usage parsed from provider logs when exposed; main writes `telemetry.summary.json` with bundle-level totals before final validation. Lite advisory packets, when used, run Gemini Flash Lite through the CLI in read-only plan mode, capture the absolute Gemini binary, version, and binary sha256 at packet creation, rehash source inputs, `task.md`, `prompt.md`, and the Gemini binary before launch and validation, regenerate prompts from `input-files.json` plus `task.md`, enforce skill-specific purpose allowlists, reject silent packet overwrites, and write validated `advice.json`; Lite is a context router only and cannot satisfy audit, review, mergeability, scientific claim, or DoD evidence. Preflight manifests require `preflight_lite_advice` provenance, and preflight/main/branch validators scan manifest-owned `lite/` for relevant unrecorded Lite packet directories. Branch and main status validators require manifest-owned `lite_advice` audit records, empty only when no relevant runtime Lite packet exists, and reject obvious state-changing or secret-inspection research commands. Preflight defaults to parallel decomposition, allows at most 5 scheduling groups of 4 branches, requires a serial reason for single-branch bundles, and writes explicit cleanup/artifact policies so partial or blocked runs preserve inspection evidence unless the user authorizes cleanup. Bundle prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root; normal worker status artifacts must live at manifest-owned `workers/<packet_id>/status.json` paths and research-worker status artifacts must live at manifest-owned `research/<packet_id>/research.json` paths.

Generated `goal-bootloader.md` files are location-bound because they embed absolute bundle and repository roots. If a bundle or repository checkout is moved, rerun `goal-preflight` or run `render_goal_bootloader.py --repo-root /absolute/path/to/repo --write`; do not hand-edit the bootloader paths.
