# Codex Goal Orchestration Skills

Install three Codex skills for file-backed `/goal` orchestration:

- `goal-preflight`
- `goal-main-orchestrator`
- `goal-branch-orchestrator`

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
4. The main orchestrator launches branch sessions that use `goal-branch-orchestrator`.

The runtime enforces prompt audit first, branch waves, and a hard limit of 5 active branch orchestrator agents.
