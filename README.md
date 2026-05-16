# Codex Goal Orchestration Skills

Install three Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`

These are packaged in one repository and reference each other by skill name, not by separate repository URLs:

- `goal-preflight` creates linted, path-safe job bundles and a location-bound `/goal` bootloader for `$goal-main-orchestrator`.
- `goal-main-orchestrator` runs bootstrap and prompt audit, creates validated branch worktrees, and dispatches `$goal-branch-orchestrator` sessions within the hard active-agent limit without loading the branch skill body into main context.
- `goal-branch-orchestrator` creates path-safe worker/reviewer packets, dispatches Gemini Pro/Flash-first workers with Codex Spark and mini fallbacks, integrates results, and sends read-only reviewer packets.

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

The runtime enforces skill availability bootstrap, absolute CLI entry paths, reproducible manifest paths, fixed prompt-audit and worker model fallback chains, prompt audit before branch work, parallel branch waves, wait-not-poll branch supervision, and a hard limit of 4 active branch orchestrator agents. Preflight defaults to parallel decomposition, allows at most 5 waves of 4 branches, requires a serial reason for single-branch bundles, and writes explicit cleanup/artifact policies so partial or blocked runs preserve inspection evidence unless the user authorizes cleanup. Bundle prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root.

Generated `goal-bootloader.md` files are location-bound because they embed absolute bundle and repository roots. If a bundle or repository checkout is moved, rerun `goal-preflight` or `render_goal_bootloader.py`; do not hand-edit the bootloader paths.
