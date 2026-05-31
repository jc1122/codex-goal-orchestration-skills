# Agent Start

Read `maintenance/agent-context-index.json` before broad repository scans.

Use `tasks.<task>.read` for first context, then run `skills.<skill>.phase_manifest_command` for runtime flow before expanding to references or `core_scripts`. If the index is stale after file moves/additions/deletions, run:

During runtime orchestration, prefer generated JSON artifacts, script `--help`, and validator output. Do not read `skills/*/scripts/*.py` unless implementing or debugging those scripts.

```bash
npm run generate:context
npm run check:context
```

Use `npm run check:maintenance` for navigation, model-catalog, dependency, and size-budget drift. Use `npm run check` before release-oriented changes.
