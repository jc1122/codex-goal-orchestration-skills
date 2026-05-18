# Codex Goal Orchestration Skills

Install three Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`

These are packaged in one repository and reference each other by skill name, not by separate repository URLs:

- `goal-preflight` creates linted, path-safe job bundles and a location-bound `/goal` bootloader for `$goal-main-orchestrator`; it can optionally use CLI-only Lite advisory packets for source digestion or lint-repair advice.
- `goal-main-orchestrator` runs bootstrap and prompt audit, can optionally use Lite only after audit or completed branch artifacts for summaries, creates validated branch worktrees, and dispatches `$goal-branch-orchestrator` sessions as a rolling saturated pool within the hard active-agent limit without loading the branch skill body into main context.
- `goal-branch-orchestrator` can optionally use Lite for packet planning, context packing, completed-worker summaries, or blocked triage; it creates path-safe worker/reviewer packets, dispatches workers through an allowed Gemini Pro -> Gemini Flash -> Codex Spark -> GitHub Copilot `gpt-5.4` high-effort -> Codex mini ladder as a rolling saturated worker pool, waits on active packet launchers without polling their logs, integrates results, and sends read-only reviewer packets.

## Install

```bash
npx github:jc1122/codex-goal-orchestration-skills
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

## Workflow

1. Use `goal-preflight` to turn a roadmap, diagnosis, report, or rough brief into a linted goal bundle.
2. Paste the generated `goal-bootloader.md` text into Copilot `/goal`.
3. The `/goal` runtime uses `goal-main-orchestrator`.
4. Runtime bootstrap checks that required skills and scripts are available before prompt audit.
5. The main orchestrator launches branch sessions that use `goal-branch-orchestrator`.

The runtime enforces skill availability bootstrap, absolute CLI entry paths, reproducible and collision-free manifest paths, fixed prompt-audit and allowed worker model fallback chains, prompt audit before branch work, rolling branch orchestration, rolling worker orchestration, wait-not-poll branch supervision, a hard limit of 4 active branch orchestrator agents, deterministic worker packet ids, artifact-backed manifest-bound branch and main status validation, deterministic packet telemetry, and a hard limit of 4 worker packets per branch. `max_active_branch_agents` is the real branch concurrency cap: main keeps slots saturated up to that cap, launches the next eligible branch when one finishes, closes finished branch orchestrator agents before replacements, and defers only branches with incomplete explicit manifest `depends_on` branch ids. Waves are scheduling/order groups, not implicit dependency barriers. `max_active_worker_packets` is the real branch-local worker concurrency cap: branch keeps worker launcher slots saturated up to that cap, launches the next eligible worker when one finishes and is integrated, and defers only workers with incomplete explicit manifest work-item `depends_on` ids. Default worker fallback order is Gemini Pro, Gemini Flash, Codex Spark, GitHub Copilot `gpt-5.4` high effort, then Codex mini; branch orchestrators may choose a non-empty ordered subsequence per worker packet with a recorded `selection_reason`, and validators reject route drift from manifest-owned `workers/<packet_id>/route.json`. Every audit, worker, reviewer, and Lite launcher writes packet-local `telemetry.json` with declared/called/accepted model aliases, provider/model ids, prompt/output/log character and byte counts, and best-effort token usage parsed from provider logs when exposed; main writes `telemetry.summary.json` with bundle-level totals before final validation. Lite advisory packets, when used, run Gemini Flash Lite through the CLI in read-only plan mode, capture the absolute Gemini binary, version, and binary sha256 at packet creation, rehash source inputs, `task.md`, `prompt.md`, and the Gemini binary before launch and validation, regenerate prompts from `input-files.json` plus `task.md`, enforce skill-specific purpose allowlists, reject silent packet overwrites, and write validated `advice.json`; Lite is a context router only and cannot satisfy audit, review, mergeability, scientific claim, or DoD evidence. Preflight manifests require `preflight_lite_advice` provenance, and preflight/main/branch validators scan manifest-owned `lite/` for relevant unrecorded Lite packet directories. Branch and main status validators require manifest-owned `lite_advice` audit records, empty only when no relevant runtime Lite packet exists. Preflight defaults to parallel decomposition, allows at most 5 scheduling groups of 4 branches, requires a serial reason for single-branch bundles, and writes explicit cleanup/artifact policies so partial or blocked runs preserve inspection evidence unless the user authorizes cleanup. Bundle prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root; worker status artifacts must live at manifest-owned `workers/<packet_id>/status.json` paths.

Generated `goal-bootloader.md` files are location-bound because they embed absolute bundle and repository roots. If a bundle or repository checkout is moved, rerun `goal-preflight` or run `render_goal_bootloader.py --repo-root /absolute/path/to/repo --write`; do not hand-edit the bootloader paths.
