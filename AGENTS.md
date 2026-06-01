# Agent Start

Read `README.md`, then `maintenance/agent-context-index.json`, before broad repository scans. `README.md` is the compact map for install, architecture, runtime flow, model routing, telemetry, Lite advisors, validation gates, and maintainer checks.

Use `tasks.<task>.read` for first context, then run `skills.<skill>.phase_manifest_command` for runtime flow before expanding to references or `core_scripts`.

During runtime orchestration, prefer generated JSON artifacts, script `--help`, and validator output. Do not read or search `skills/*/scripts/*.py` with `cat`, `sed`, `head`, `rg`, `grep`, or similar commands unless implementing or debugging those scripts.

For generated prompt-audit, runtime, and Lite advisory packets, inspect packet-local `prompt.md`, `packet-context.json`, `route.json`, `launch-config.json`, status/review/research/advice/audit outputs, and `telemetry.json` before expanding to implementation files.

Debug telemetry is manifest-owned through `job.manifest.json.telemetry_policy`; it is not a runtime flag. For goal-preflight, "debug mode" means the structured brief should set `telemetry_mode: "debug"` so bundle creation expands the full safe policy. Debug mode is passive, `raw_text` must remain false, and USD/pricing fields are prohibited.

For main prompt audit, use `run_prompt_audit_phase.py` with the phase-manifest flags; inspect `audit/prompt-audit-phase.json` before raw event logs.

If the index is stale after tracked file moves/additions/deletions, run:

```bash
npm run generate:context
npm run check:context
```

Use `npm run check:maintenance` for navigation, model-catalog, dependency, and size-budget drift. Use `npm run check` before release-oriented changes.
