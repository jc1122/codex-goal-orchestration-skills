# Debug Telemetry Goal

## Goal

Add an opt-in debug telemetry mode to `codex-goal-orchestration-skills` that records enough structured evidence to improve future route selection, token/text consumption, runtime speed, and deterministic behavior, without tracking USD cost and without changing normal orchestration behavior.

The debug mode should measure time and text, while reporting models separately by alias, provider, model id, role, and attempt outcome.

## Non-Goals

- Do not estimate USD cost.
- Do not change route selection during debug collection.
- Do not store full raw prompts, full model outputs, or full logs in new debug artifacts.
- Do not weaken active-agent polling rules.

## Core Artifacts

- `telemetry_policy` in `job.manifest.json`
- packet-level `telemetry.debug.json`
- append-only `debug.events.jsonl`
- bundle-level `telemetry.debug.summary.json`
- optional `debug/model-usage.json` for model-only aggregation

## Metric Groups

Text consumption:

```json
{
  "prompt_chars": 0,
  "prompt_bytes": 0,
  "context_chars": 0,
  "context_bytes": 0,
  "output_chars": 0,
  "output_bytes": 0,
  "event_log_chars": 0,
  "event_log_bytes": 0,
  "known_input_tokens": null,
  "known_cached_input_tokens": null,
  "known_output_tokens": null,
  "estimated_prompt_tokens": 0,
  "token_estimate_method": "chars_div_4",
  "usage_source": "event_log | provider_metadata | estimate_only"
}
```

Model usage, reported separately from text cost:

```json
{
  "role": "worker",
  "packet_id": "B01-W02",
  "provider": "codex",
  "route_alias": "codex-mini",
  "model": "gpt-5.4-mini",
  "effort": "low | medium | high | null",
  "attempt_index": 1,
  "called": true,
  "accepted": true,
  "fallback_from": null,
  "timeout_seconds": 3600,
  "selection_reason": "bounded small edit"
}
```

Time metrics:

```json
{
  "phase": "worker_packet",
  "started_at": "...",
  "ended_at": "...",
  "elapsed_ms": 0,
  "queue_wait_ms": 0,
  "active_runtime_ms": 0,
  "timeout": false,
  "exit_status": 0
}
```

Scheduler efficiency:

```json
{
  "scope": "worker",
  "capacity": 4,
  "ready_count": 3,
  "active_count": 2,
  "eligible_idle_ms": 0,
  "under_capacity_events": 0,
  "refill_latency_ms": 0,
  "blocked_count": 0,
  "reason_code": "none"
}
```

Determinism and reproducibility:

```json
{
  "manifest_sha256": "...",
  "prompt_sha256": "...",
  "context_pack_sha256": "...",
  "model_catalog_sha256": "...",
  "route_policy_sha256": "...",
  "skill_version": "0.2.x",
  "script_version_hashes": {},
  "artifact_paths_are_manifest_owned": true
}
```

Outcome metrics:

```json
{
  "status": "pass | partial | blocked | failed",
  "validator": "validate_branch_status.py",
  "validation_elapsed_ms": 0,
  "defect_count": 0,
  "defect_severity_counts": {
    "critical": 0,
    "major": 0,
    "minor": 0
  },
  "retry_count": 0,
  "fallback_count": 0
}
```

## Definition Of Done

- Debug mode is opt-in through manifest policy and defaults to off.
- Standard telemetry remains backward-compatible.
- Every prompt-audit, worker, research-worker, reviewer, Lite, and amender packet can emit debug telemetry.
- Bundle summary separates text/time metrics from model usage.
- No USD fields or price tables exist.
- Validators require debug artifacts only when debug mode is enabled.
- Debug artifacts use hashes, counts, timings, and paths rather than duplicating raw prompt/output/log text.
- Toy bundle smoke proves both standard mode and debug mode.
- Checks pass: `npm run check:fixtures`, `npm run check:golden`, `npm run check:maintenance`, and then `npm run check`.

## Success Metrics For The Feature

- `model_usage_count` by role, provider, alias, and model.
- `known_token_coverage_ratio`: attempts with real token data divided by total called attempts.
- `text_pressure_ratio`: known or estimated input tokens divided by packet prompt estimate.
- `scheduler_utilization_ratio`: active slot time divided by available slot time.
- `eligible_idle_ms`: time where work was ready but capacity was unused.
- `fallback_rate`: fallback attempts divided by called attempts.
- `timeout_rate`: timed-out attempts divided by called attempts.
- `debug_overhead_chars`: extra debug artifact text produced.
- `debug_overhead_ms`: time spent writing or summarizing debug artifacts.
- `determinism_drift_count`: mismatched hashes, stale catalogs, changed prompts, or changed inputs.
