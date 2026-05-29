# Branch Runtime Contract

## Inputs

The branch runtime receives:

- manifest path;
- prompt audit path;
- branch id;
- branch prompt path;
- branch integration branch name;
- branch integration worktree path;
- status output path;
- review output path.

The main orchestrator already created the integration worktree. The branch orchestrator may create worker child worktrees from that branch.

Resolve all bundle-owned paths from the manifest directory before passing them to worker/research-worker/reviewer/Lite packet scripts. Worker/research-worker/reviewer/Lite packet directories, worktrees, task files, and context files must be absolute paths; the packet generators reject relative paths and `..` traversal. Worker-owned files should stay repo-relative and must not contain absolute paths or `..` traversal.

Each manifest branch must declare `max_active_worker_packets`, `worker_parallelism.scheduler_path`, `pre_review_gate_path`, and 1 to 4 `work_items` with deterministic `packet_id` values in `<branch_id>-<work_item_id>` form. Work items may declare `worker_type: "worker"` for normal code/config/test/doc work or `worker_type: "research-worker"` for outside-information gathering. `max_active_worker_packets` is a hard per-branch active cap from 1 to 4. Parallel worker dispatch is the default: launch independent worker packets as a rolling saturated pool up to that cap. Work-item `depends_on` entries must reference prior work item ids and are the only reason to defer an otherwise eligible worker. When a worker launcher exits, collect and integrate its status/diff or research findings, remove it from the active set, and launch the next eligible worker immediately if capacity is available. If more than 4 worker packets would be needed, stop and split the branch instead of inventing extra packets. If a branch runs serially or below capacity, record the reason in `worker_parallelism.serial_reasons`, and write explicit scheduler `defer`, `under_capacity`, or `blocked` events.

## Worker Model Policy

Use this exact worker preference:

1. Gemini CLI with `gemini-3.1-pro-preview`
2. Gemini CLI with `gemini-3-flash-preview`
3. `gpt-5.3-codex-spark`
4. GitHub Copilot CLI with `gpt-5.4` and `--effort high`
5. `gpt-5.4-mini`

Worker route aliases are exactly:

- `gemini-pro`
- `gemini-flash`
- `codex-spark`
- `copilot-gpt-5.4`
- `codex-mini`

The branch orchestrator may choose a non-empty ordered subsequence of the default ladder per worker packet when task hardness, context size, quota pressure, or provider availability justifies it. The selected route must preserve the default order; Spark is preferred immediately after Gemini Pro and Gemini Flash, and Copilot comes only after Spark. Runtime packet generation accepts repeated `--worker-route <alias>` values plus a required `--selection-reason` for non-default routes. It writes `route.json`; worker status JSON and branch rollups must copy `selected_ladder` and `selection_reason` exactly.

Fallback is allowed only when:

- the current worker attempt did not produce a valid status file;
- the worker worktree is clean.

No Gemini model other than `gemini-3.1-pro-preview` and `gemini-3-flash-preview` may be used. Runtime packet generation must not accept model, effort, approval-mode, or permission overrides. Worker prompts must render worktree-local context files as relative paths and embed out-of-worktree context snapshots so workspace-restricted CLIs never need to read bundle paths outside the worker worktree. All worker providers receive the same generated prompt and must satisfy the same worker status schema, including route fields. CLI permission controls are provider-specific, so a provider status is only evidence after schema validation, clean fallback boundaries, branch diff inspection, and branch-level tests. Before each full Gemini worker attempt, run a 20-second headless probe with the same Gemini model. Before the full Copilot worker attempt, run a 20-second no-tool probe with `gpt-5-mini` and `--effort low` to verify Copilot CLI/auth/routing without spending a `gpt-5.4` call. Gemini and Copilot are best-effort because quota limits may be tight: missing CLIs, quota errors, invalid JSON, unavailable models, or other clean failures should fall through to the next selected worker attempt. Copilot must run real worker packets in programmatic mode with `--model gpt-5.4`, `--effort high`, `--no-ask-user`, minimal tool permissions, JSONL output, and a Markdown session share. Do not use `/fleet` for packet execution; the branch orchestrator owns worker parallelism externally. Every launcher terminal path writes same-packet `telemetry.json` with declared/called/accepted route aliases, provider/model ids, prompt/output/log character and byte counts, and any token counts exposed in provider logs. If Gemini or Copilot returns marked worker JSON with `status: "success"`, normalize it to canonical `pass` before schema validation, without adding command evidence. If Gemini Pro, Gemini Flash, Spark, Copilot, or mini fails after dirty edits and no valid `status.json` exists, stop and report `blocked`; do not continue in the same worktree. If every selected attempt fails cleanly, write a terminal blocked worker `status.json` and `telemetry.json`.

## Research Worker Policy

Use `--role research-worker` only for research-only packets. Research workers run `codex --search exec --ephemeral -s read-only` without user-config suppression, which provides Codex native web search plus configured read-only CLI/MCP/connector/browser/search tools, package metadata lookups, remote APIs, shell/network inspection commands, local read-only file access, and configured tool/skill documentation when relevant. Research workers must not edit files, inspect secrets or unrelated private files, or perform state-changing, destructive, credential, posting, purchasing, or remote mutation actions. They write `research.json` under manifest-owned `research/<packet_id>/`, and a passing research status must include `search_queries` when search was used, direct `source_urls`, `tools_used`, repo-relative `local_files_read`, exact `commands_run`, findings, empty blockers, and same-packet `telemetry.json`. Branch validation rejects obvious state-changing research commands, package/system mutation, file writes, shell redirection to files, environment dumps, and secret/credential path reads.

## Timeout And Retry Policy

All full model/CLI attempts run through `timeout --foreground --kill-after=30s`; if `timeout` is unavailable, the launcher refuses to run an unbounded attempt and records a blocked artifact. Default attempt limits are:

- normal worker route attempt: 3600 seconds;
- research-worker attempt: 1200 seconds;
- reviewer attempt: 1800 seconds;
- prompt-audit attempt: 1200 seconds;
- Lite advisor attempt: 600 seconds.

A timeout is a failed attempt. Do not poll active logs or status files while waiting for a timeout. Fallback is allowed only when the attempt did not produce a valid artifact and, for write-capable workers, the worker worktree is clean. There is no same-alias retry loop; retry means moving to the next declared fallback alias. Every telemetry attempt must record a positive `timeout_seconds`.

## Reviewer Model Policy

Use this reviewer/auditor preference:

1. `gpt-5.5`
2. `gpt-5.4`

Reviewers are read-only. They produce findings, verification gaps, residual risks, mergeability verdicts, and same-packet `telemetry.json`.

Before launching a reviewer, write branch-local `pre_review_gate.json` at the manifest `pre_review_gate_path`. The gate must pass manifest/status validation, configured tests/checks or explicit skip authorization, `git diff --check <base-ref>...HEAD`, worker/research artifact freshness through current hashes, ownership checks, and non-empty DoD evidence. Reviewer packets require `--manifest` and `--pre-review-gate`; packet generation fails when the gate status is not `pass`. Reviewer JSON must copy the gate `input_hashes` exactly and include `reuse_policy`. Reuse is accepted only when every recorded input hash matches exactly.

## Lite Advisor Policy

Lite advisors are optional context routers. They are not workers, reviewers, or authorities. Branch may create Lite packets only after required start checks pass and never while worker or reviewer launchers are active.

Allowed branch Lite purposes:

- `branch-packet-planning`: branch prompt, manifest branch entry, and prompt audit to worker packet advice.
- `context-pack`: selected branch prompt/read-first files to focused worker context advice.
- `worker-summary`: completed worker statuses, diff names, and test evidence to summary advice.
- `blocked-triage`: blocked/failed worker status plus relevant failure excerpt to next repair-packet advice.

Validate Lite `advice.json` with `scripts/validate_lite_advice.py` before using it. Lite launchers use the absolute Gemini path, version, and binary sha256 captured at packet creation, rehash every input, rehash `task.md`, rehash/regenerate `prompt.md`, rehash the Gemini binary, and write same-packet `telemetry.json` before acceptance. Read validated Lite output first, then open only cited original files or spans needed for verification. Ignore Lite when it is unavailable, quota-limited, blocked, invalid, stale, missing telemetry, or contradicted by worker artifacts or original files. Lite cannot satisfy worker pass, review pass, mergeability, scientific claim support, or DoD requirements. Branch Lite packet ids must be scoped as `<branch-id>-L<suffix>`. Record every used or ignored Lite packet in branch status; record `lite_advice: []` only when no relevant branch Lite packet exists. The branch status validator scans manifest-owned `lite/` for relevant branch Lite packet directories and fails if any are unrecorded, branch-purpose packets are not branch-scoped, or validation commands are non-canonical.

## Branch Status

Return/write status with these fields:

```json
{
  "branch_id": "B01",
  "status": "pass|partial|blocked|failed",
  "branch": "phaseX-B01",
  "worktree": "/absolute/path",
  "worker_statuses": [
    {
      "packet_id": "B01-W01",
      "status": "pass|partial|blocked|failed",
      "status_path": "/absolute/path/to/workers/B01-W01/status.json",
      "worktree": "/absolute/path/to/.worktrees/phaseX-B01-W01",
      "selected_ladder": ["codex-spark", "copilot-gpt-5.4"],
      "selection_reason": "Bounded implementation packet; preserve Gemini quota and prefer Spark before Copilot.",
      "changed_files": ["src/example.py"],
      "commands_run": ["python3 -m pytest tests/test_example.py -q"],
      "tests": ["python3 -m pytest tests/test_example.py -q"],
      "blockers": [],
      "handoff": "concise worker handoff"
    },
    {
      "packet_id": "B01-W02",
      "role": "research-worker",
      "status": "pass|partial|blocked|failed",
      "status_path": "/absolute/path/to/research/B01-W02/research.json",
      "worktree": "/absolute/path/to/.worktrees/phaseX-B01-W02",
      "search_queries": ["current external fact query"],
      "source_urls": ["https://example.com/source"],
      "tools_used": ["codex-native-search", "local-sed"],
      "local_files_read": ["plans/source-brief.md"],
      "commands_run": ["pwd", "git status --short --branch", "sed -n '1,120p' plans/source-brief.md"],
      "findings": ["source-backed finding"],
      "blockers": [],
      "handoff": "concise research handoff"
    }
  ],
	  "worker_parallelism": {
	    "scheduler_path": "schedulers/B01.worker.scheduler.json",
	    "max_worker_packets_per_branch": 4,
	    "max_active_worker_packets": 4,
	    "max_observed_active_worker_packets": 4,
	    "max_observed_active": 4,
	    "concurrent_launch_default": true,
	    "rolling_refill_default": true,
	    "scheduling_mode": "rolling",
	    "launched_ids": ["B01-W01", "B01-W02"],
	    "finished_ids": ["B01-W01", "B01-W02"],
	    "active_ids": [],
	    "blocked_ids": [],
	    "deferred_ids": [],
	    "serialized_workers": [],
	    "deferred_workers": [],
	    "serial_reasons": [],
	    "refill_events": []
	  },
	  "lite_advice": [
	    {
	      "packet_id": "B01-L01",
	      "purpose": "context-pack",
	      "status": "ok|partial|blocked",
	      "disposition": "used|ignored|unused",
	      "advice_path": "/absolute/path/to/lite/B01-L01/advice.json",
	      "inputs_path": "/absolute/path/to/lite/B01-L01/input-files.json",
	      "source_files": [
	        {
	          "path": "plans/orchestration/phaseX/branches/B01.prompt.md",
	          "sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
	          "size_bytes": 123,
	          "reason": "explicit Lite input"
	        }
	      ],
	      "validation_command": "python3 /absolute/path/to/goal-branch-orchestrator/scripts/validate_lite_advice.py --advice /absolute/path/to/lite/B01-L01/advice.json --inputs /absolute/path/to/lite/B01-L01/input-files.json",
	      "validation_status": "pass|failed",
	      "validation_defects": [],
	      "reason": "used only to choose targeted original reads"
	    }
	  ],
	  "review_status": "mergeable|mergeable_after_fixes|blocked|reject|missing",
  "changed_files": ["src/example.py", "tests/test_example.py"],
  "commands_run": ["python3 -m pytest tests/test_example.py -q", "git diff --check main...HEAD"],
  "tests": ["python3 -m pytest tests/test_example.py -q"],
  "dod_checklist": ["focused validator passed", "reviewer verdict mergeable"],
  "blockers": [],
  "handoff": "concise branch handoff"
}
```

Validate the final branch status with `scripts/validate_branch_status.py --manifest /absolute/path/to/job.manifest.json` before reporting `pass`. A `pass` or `partial` branch status must include exactly one worker status for every manifest work item `packet_id` and no extra worker packet ids. Worker and branch `changed_files` entries must be repo-relative file paths without git porcelain prefixes; command and test evidence must be exact command strings. Normal worker `status_path` values must resolve to manifest-owned `workers/<packet_id>/status.json`; research-worker `status_path` values must resolve to manifest-owned `research/<packet_id>/research.json`; copied or external artifacts are invalid. Worker route fields must be present in normal worker branch rollups and worker artifacts, must use allowed aliases in standard order, and must match manifest-owned `workers/<packet_id>/route.json`. Same-packet worker/research-worker `telemetry.json` must exist. Research-worker telemetry aliases must be `codex-research` or `codex-research-mini`, and passing research-worker statuses must record direct source URLs plus `tools_used`. `worker_parallelism.scheduler_path` must point to `schedulers/<branch-id>.worker.scheduler.json`, whose ledger must match the current manifest hash and reconstruct active worker counts without duplicate launches, missing finishes/closes, cap overflow, missing refill events, or eligible-idle gaps without structured reasons. `lite_advice` must be present, even when empty; any recorded Lite packet must point to existing manifest-owned `lite/<packet_id>/advice.json` and `lite/<packet_id>/input-files.json`, match source hashes exactly, have same-packet telemetry, and have exact validation command plus `validation_status`/`validation_defects` matching actual `validate_lite_advice.py` output. Any relevant branch Lite packet directory under manifest-owned `lite/` must be recorded, so an empty `lite_advice` array is valid only when no branch Lite packet exists. Any `disposition: "used"` Lite packet must validate with `validation_status: "pass"`. Reviewer packet ids must be safe ids for the same branch, such as `B01-R01`, and reviewer `telemetry.json` must exist. `pass` requires every worker and research-worker status to be `pass` and backed by its manifest-owned artifact, passing `pre_review_gate.json`, `review_status: "mergeable"` backed by the manifest review artifact, reviewer `input_hashes` exactly matching the pre-review gate, reviewer reuse accepted only for exact hash matches, empty reviewer verification gaps, exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, a non-empty DoD checklist, and no blockers. Non-pass worker or branch statuses must include at least one blocker.

## Context Conservation

Read high-signal artifacts first:

1. branch prompt;
2. prompt audit JSON;
3. worker and research-worker status JSON files;
4. worker/research-worker/reviewer/Lite telemetry JSON files;
5. `git diff --name-only`;
6. `git diff --check`;
7. focused test output;
8. review JSON.

Do not read full worker event logs unless a worker status is missing, failed, or inconsistent with the worktree diff.

If validated Lite advice exists for the current purpose, read it before opening larger originals. Do not read both Lite summaries and all original files by default; use Lite to choose targeted original reads.

While worker, research-worker, or reviewer launchers are active, wait rather than poll. A quiet launcher is not evidence of a stall. Do not inspect active launcher event logs, process tables, worker worktrees, status files, research files, or review files while waiting. Inspect those artifacts only after the launcher exits, the generated status/research/review artifact is missing or failed, or the user explicitly switches to debug mode.

## Integration Rules

- Verify the active checkout with `pwd` and `git status --short --branch` before edits or merges.
- Keep worker ownership disjoint.
- Prefer one child worktree per worker when workers write.
- Use 1 to 4 worker packets for one branch.
- Launch independent worker packets as a rolling saturated pool when owned files and verification commands do not conflict, up to `max_active_worker_packets`.
- After a worker launcher exits, integrate its status/diff, free its active slot, and launch the next eligible worker immediately if capacity is available.
- Never exceed 4 active worker packets in one branch.
- Record the reason in `worker_parallelism.serial_reasons` if worker execution is serialized.
- Wait for active worker/research-worker/reviewer launchers instead of polling their event logs, process tables, worktrees, status files, research files, or review files.
- Inspect diffs before accepting worker summaries.
- Run both working-tree whitespace checks and base-range whitespace checks, for example `git diff --check <base-ref>...HEAD`, before review or merge readiness.
- Run branch-level validators after integrating workers.
- Preserve negative and unresolved scientific labels.
- Treat Lite advice only as advisory context routing, never as worker/research-worker/reviewer/DoD evidence.
- Return blocked rather than guessing when prompt DoD is ambiguous.
