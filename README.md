# Codex Goal Orchestration Skills

Install three Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`

These are packaged in one repository and reference each other by skill name, not by separate repository URLs:

- `goal-preflight` creates linted, path-safe job bundles and a location-bound `/goal` bootloader for `$goal-main-orchestrator`.
- `goal-main-orchestrator` runs bootstrap and prompt audit, creates validated branch worktrees, and dispatches `$goal-branch-orchestrator` sessions within the hard active-agent limit.
- `goal-branch-orchestrator` creates path-safe worker/reviewer packets, dispatches Spark-first Codex CLI workers, integrates results, and sends read-only reviewer packets.

## Install

```bash
npx github:jc1122/codex-goal-orchestration-skills
```

The installer copies bundled skills to `$CODEX_HOME/skills` when `CODEX_HOME` is set, otherwise to `~/.codex/skills`.

Use a custom destination:

```bash
npx github:jc1122/codex-goal-orchestration-skills -- --dest /path/to/skills
```

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

The runtime enforces skill availability bootstrap, reproducible manifest paths, prompt audit before branch work, branch waves, and a hard limit of 5 active branch orchestrator agents. Bundle prompt/status/review paths are relative to the bundle root; worktree paths are relative to the repository root.

Generated `goal-bootloader.md` files are location-bound because they embed absolute bundle and repository roots. If a bundle or repository checkout is moved, rerun `goal-preflight` or `render_goal_bootloader.py`; do not hand-edit the bootloader paths.
