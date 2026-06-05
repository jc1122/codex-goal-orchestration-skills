# Main Runtime Contract

## Inputs

The main runtime consumes artifacts prepared before `/goal` starts:

- `job.manifest.json`
- `main.prompt.md`
- all branch prompt files listed in the manifest

The main runtime may create execution artifacts:

- `prompt-audit.json`
- audit `telemetry.json`
- `main.status.json`
- `telemetry.summary.json`
- optional Lite advisory artifacts under `lite/`
- optional route-bound amendment artifacts and telemetry under `amendments/`
- branch integration branches/worktrees
- branch worker, research-worker, status, and review artifacts produced by branch orchestrators

It must not create or rewrite the bootloader or main prompt. It may invoke `goal-plan-amender` after validated terminal branch results to update only future unstarted manifest work and changed future branch prompts through accepted amendment artifacts.

## Manifest Shape

Manifest-owned paths are reproducible POSIX-relative paths only. `main_prompt`, branch `prompt`, `status_path`, and `review_path` are relative to the manifest directory. `worktree_path` is relative to the repository root. Absolute paths, backslashes, empty path segments, `.`, and `..` are invalid. Runtime script arguments for manifest, repo root, audit files, and output directories must be absolute paths with no `..` traversal.

```json
{
  "job_id": "phaseX",
  "main_prompt": "main.prompt.md",
  "base_ref": "main",
  "artifact_policy": "Preserve the full orchestration bundle under plans/orchestration/<job-id>; commit generated preflight prompts only when the user explicitly asks, and commit runtime status/review/audit artifacts only when the main prompt or user explicitly requires them.",
  "cleanup_policy": "On pass, report mergeability and leave branch/worktree removal to explicit user authorization. On partial, blocked, or failed runs, preserve branch worktrees, branches, packets, and logs for inspection unless the user explicitly authorizes cleanup.",
  "max_active_branch_agents": 4,
  "parallelization": {
    "parallelism_default": true,
    "max_active_branch_agents": 4,
    "max_branches_per_wave": 4,
    "max_waves": 5,
    "scheduling_mode": "rolling",
    "scheduler_path": "schedulers/main.scheduler.json",
    "serial_reasons": [],
    "parallelization_rationale": "Keep up to 4 branch orchestrators active; defer only branches whose depends_on branch ids are not complete.",
    "wave_execution": "Use waves as scheduling/order groups only. Keep branch orchestrator slots saturated up to max_active_branch_agents; when a branch finishes and capacity is freed, launch the next eligible branch whose depends_on branch ids are complete.",
    "dependency_policy": "Branch depends_on entries are explicit prior-branch dependencies; branches without unresolved depends_on entries are eligible whenever capacity is available."
  },
  "adaptation_policy": {
    "enabled": true,
    "mode": "amendment_proposals",
    "launcher": "goal-main-orchestrator",
    "may_modify_active_or_terminal_branches": false,
    "allowed_operations": ["add_branch", "split_unstarted_branch", "replace_unstarted_branch", "add_dependency_to_unstarted_branch", "add_work_item_to_unstarted_branch", "mark_unstarted_branch_obsolete"]
  },
  "amender_model_policy": {
    "default_ladder": ["gpt-5.4", "gpt-5.4-mini"],
    "allowed_routes": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"],
    "launcher": "goal-main-orchestrator",
    "selection_reason_required": true,
    "ordering_rule": "Selected amender routes must be a non-empty ordered subsequence of allowed_routes.",
    "sandbox": "read-only",
    "timeout_seconds": 1200
  },
  "worker_model_policy": {
    "default_ladder": ["gemini-pro", "gemini-flash", "codex-spark", "codex-mini"],
    "allowed_routes": ["gemini-pro", "gemini-flash", "codex-spark", "codex-mini"],
    "default_route_class": "normal-code",
    "route_classes": {
      "mechanical": ["codex-mini"],
      "docs": ["codex-mini"],
      "small-edit": ["codex-spark", "codex-mini"],
      "normal-code": ["codex-spark", "codex-mini"],
      "complex-code": ["gemini-pro", "gemini-flash", "codex-spark", "codex-mini"],
      "custom": ["gemini-pro", "gemini-flash", "codex-spark", "codex-mini"]
    },
    "branch_may_select_worker_route": true,
    "selection_reason_required": true,
    "ordering_rule": "Selected worker routes must be a non-empty ordered subsequence of default_ladder."
  },
  "research_worker_policy": {
    "enabled": true,
    "worker_type": "research-worker",
    "launcher": "codex --search exec --ephemeral -s read-only",
    "network_scope": "Broad read-only information retrieval is allowed through Codex native web search, configured CLI tools, MCP servers, connector tools, browser/search tools, package metadata lookups, remote APIs, and shell/network inspection commands. State-changing, destructive, credential, posting, purchasing, and file-editing actions are prohibited.",
    "local_access": "Read-only local file and command inspection for the assigned worktree, explicit context files, and configured tool or skill documentation when task-relevant; no writes, no secrets or unrelated private files."
  },
  "branches": [
    {
      "id": "B01",
      "wave": "wave-01",
      "prompt": "branches/B01.prompt.md",
      "branch_name": "phaseX-B01",
      "worktree_path": ".worktrees/phaseX-B01",
      "status_path": "branches/B01.status.json",
      "review_path": "branches/B01.review.json",
      "pre_review_gate_path": "branches/B01.pre_review_gate.json",
      "depends_on": [],
      "max_active_worker_packets": 4,
      "work_items": [
        {
          "id": "W01",
          "packet_id": "B01-W01",
          "worker_type": "worker",
          "objective": "Bounded worker objective.",
          "owned_paths": ["src/example.py"],
          "verification": ["python3 -m pytest tests/test_example.py -q"],
          "dod": ["Focused validator passes."]
        }
      ],
      "worker_parallelism": {
        "parallelism_default": true,
        "scheduling_mode": "rolling",
        "scheduler_path": "schedulers/B01.worker.scheduler.json",
        "max_active_worker_packets": 4,
        "max_worker_packets_per_branch": 4,
        "serial_reasons": [],
        "parallelization_rationale": "Launch independent worker packets as a rolling saturated pool up to 4 active worker packets.",
        "wave_execution": "Use work items as an ordered ready queue. Keep worker slots saturated up to max_active_worker_packets; when a worker finishes and capacity is freed, launch the next eligible worker whose depends_on work item ids are complete.",
        "dependency_policy": "Work item depends_on entries are explicit prior-worker dependencies; workers without unresolved depends_on entries are eligible whenever capacity is available.",
        "slot_refill": "After a worker launcher exits, collect and integrate its status/diff, remove it from the active set, then launch the next eligible worker immediately if capacity is available."
      }
    }
  ],
  "waves": [
    {
      "id": "wave-01",
      "branches": ["B01"]
    }
  ]
}
```

## Main Status

Return/write status with these fields:

```json
{
  "job_id": "phaseX",
  "status": "pass|partial|blocked|failed",
  "audit_status": "pass|failed|blocked|missing",
  "branch_parallelism": {
    "scheduler_path": "schedulers/main.scheduler.json",
    "launched_ids": ["B01"],
    "finished_ids": ["B01"],
    "active_ids": [],
    "blocked_ids": [],
    "deferred_ids": [],
    "max_observed_active": 1
  },
	  "branch_statuses": [
    {
      "branch_id": "B01",
      "status": "pass|partial|blocked|failed",
      "status_path": "branches/B01.status.json",
      "review_path": "branches/B01.review.json",
	      "review_status": "mergeable|mergeable_after_fixes|blocked|reject|missing"
	    }
	  ],
	  "amendment_decisions": [
	    {
	      "amendment_id": "A001",
	      "decision": "launch|skip",
	      "decision_path": "amendments/A001.decision.json",
	      "packet_validation_path": "amendments/A001.packet/packet.validation.json"
	    }
	  ],
	  "lite_advice": [
	    {
	      "packet_id": "M01-L01",
	      "purpose": "main-summary",
	      "status": "ok|partial|blocked",
	      "disposition": "used|ignored|unused",
	      "advice_path": "/absolute/path/to/lite/M01-L01/advice.json",
	      "inputs_path": "/absolute/path/to/lite/M01-L01/input-files.json",
	      "source_files": [
	        {
	          "path": "plans/orchestration/phaseX/branches/B01.status.json",
	          "sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
	          "size_bytes": 123,
	          "reason": "explicit Lite input"
	        }
	      ],
	      "validation_command": "python3 /absolute/path/to/goal-main-orchestrator/scripts/validate_lite_advice.py --advice /absolute/path/to/lite/M01-L01/advice.json --inputs /absolute/path/to/lite/M01-L01/input-files.json",
	      "validation_status": "pass|failed",
	      "validation_defects": [],
	      "reason": "used only to choose targeted original reads"
	    }
	  ],
	  "commands_run": ["python3 scripts/check_goal_skill_availability.py ...", "python3 scripts/validate_main_status.py --manifest ... --status ..."],
  "dod_checklist": ["prompt audit passed", "all branch statuses validated"],
  "blockers": [],
  "summary": "concise main handoff"
}
```

`lite_advice` must be present, even when empty. Any recorded main Lite packet must point to existing manifest-owned `lite/<packet_id>/advice.json` and `lite/<packet_id>/input-files.json`, match source hashes exactly, and have exact validation command plus `validation_status`/`validation_defects` matching actual `validate_lite_advice.py` output. Any relevant main Lite packet directory under manifest-owned `lite/` must be recorded, so an empty `lite_advice` array is valid only when no main Lite packet exists.

Run `scripts/scheduler_tick.py --scope main --close-from-artifacts --validate-final`, then `scripts/summarize_telemetry.py --bundle-dir /absolute/path/to/bundle`, then `scripts/assemble_main_status.py --manifest /absolute/path/to/job.manifest.json --out /absolute/path/to/main.status.json --replace`, before final validation so scheduler evidence, `telemetry.summary.json`, and `main.status.json` are generated from manifest-owned artifacts instead of hand-authored. `telemetry.summary.json` lists discovered artifacts in `telemetry_files`, exposes `telemetry_count`, and includes warning-only `token_pressure.warnings` when known child-session input tokens greatly exceed packet prompt estimates; inspect those fields before opening raw event logs. Write the schema v2 main scheduler ledger at `schedulers/main.scheduler.json` with `scripts/scheduler_tick.py` for normal ready/launch/finish/close/refill bookkeeping; it must include `ready`, `launch`, `finish`, `close`, `refill`, `defer`, `under_capacity`, and `blocked` events as applicable, plus the current manifest sha256, ordered `seq`, `timestamp`, `runtime_ref`, and enum `reason_code` for defer/under_capacity/blocked events. Dependencies unlock downstream branches only when they finish with `pass`; non-pass dependencies require `dependency_failed` evidence. Validate every completed branch status in archived mode with `goal-branch-orchestrator/scripts/validate_branch_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/bundle/branches/Bxx.status.json --allow-archived-manifest-hashes` before accepting it; branch agents are responsible for live `--worktree` validation before producing terminal status. Validate the final main status with `scripts/validate_main_status.py --manifest /absolute/path/to/job.manifest.json --status /absolute/path/to/main.status.json` before reporting `pass`; this validator opens every listed manifest-referenced branch status artifact, validates it against recorded terminal snapshots, and fails if it is missing, invalid, or inconsistent with `main.status.json`. It also validates prompt-audit telemetry, opens review artifacts whenever `review_status` is not `missing`, requires every recorded Lite packet to use manifest-owned `lite/<packet_id>/` paths, validates every Lite advice artifact, live input/task/prompt hashes, and Lite telemetry, checks the captured Gemini path/version/binary sha for non-blocked Lite advice, requires recorded validation command/status/defects to match actual validation, scans manifest-owned `lite/` for unrecorded main Lite packets, validates amendment launch/skip decision artifacts for every terminal branch summary, requires every discovered amender packet to have `packet.validation.json`, validates branch scheduler reconstruction in `branch_parallelism`, rejects duplicate branch launches, missing finishes/closes, active counts above cap, non-pass dependency launches, missing refill events, vague reason text, and eligible-idle gaps without structured reasons, and for `pass` requires every normal worker artifact to live at the manifest-owned `workers/<packet_id>/status.json`, every research-worker artifact to live at the manifest-owned `research/<packet_id>/research.json`, every worker/research-worker/reviewer packet to have same-packet telemetry or accepted reviewer source telemetry reuse, every plan-amender packet to have route-bound telemetry when amendments were attempted, every review artifact to use a same-branch reviewer packet id, contain exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, and have no verification gaps when `mergeable`. Main `pass` requires `audit_status: "pass"`, exactly the manifest branch summary set with manifest-matching status/review paths, every branch summary status `pass`, every passing branch summary review status `mergeable`, recorded `amendment_decisions` covering every terminal branch summary, a current `telemetry.summary.json` file, a current `schedulers/main.scheduler.json` ledger, a `lite_advice` array, a non-empty command list, a non-empty DoD checklist, and no blockers. Main `partial` may omit unlaunched branch artifacts only when scheduler v2 explains every omitted id with terminal structured evidence. Non-pass main status must include at least one blocker.

If a plan-amender route includes `gpt-5.5`, the summary records it under `premium_usage.amender_gpt_5_5`.

## Amendment Policy

Main records an amendment decision after every terminal branch result has passed branch status validation and the branch orchestrator is closed. The decision is `skip` when no adaptation is needed and `launch` only when no manifest branch is eligible, a non-pass dependency would stall downstream work, remaining unstarted work no longer covers the main DoD, finalization cannot pass but recovery work is plausible, or the operator explicitly requests recovery planning.

The amender writes `amendments/Axxx.decision.json`, `Axxx.packet/route.json`, `Axxx.packet/telemetry.json`, `Axxx.packet/packet.validation.json`, `Axxx.proposal.json`, `Axxx.validation.json`, optional `Axxx.accepted.json`, and prior-manifest archives. Main selects an ordered amender model ladder from `amender_model_policy.allowed_routes` and records a non-empty selection reason; the packet uses read-only Codex attempts and a bounded timeout. It may add, split, replace, or obsolete unstarted branches, add dependencies to unstarted branches, or add work items to unstarted branches. It must not mutate active or terminal branch ids, prompts, worktrees, status paths, review paths, dependencies, owned paths, worker sets, scheduler ledgers, or runtime artifacts. It must not inspect active branch internals. Accepted amendments update the live manifest and changed future branch prompts through preflight helpers, rerun lint, and preserve prompt-audit/main DoD boundaries. Invalid amendments leave the live manifest unchanged and become blocker evidence if no existing work can continue.

Every prompt-audit, worker, research-worker, reviewer, plan-amender, and Lite telemetry attempt must include a positive `timeout_seconds`. Default full-attempt limits are 1200 seconds for prompt audit, 3600 seconds for normal worker route attempts, 1200 seconds for research workers, 1800 seconds for reviewers, 1200 seconds for plan-amenders, and 600 seconds for Lite advisors, each with a 30-second kill-after window. Timeout is a failed attempt and never authorizes polling active branch or worker artifacts.

## Context Conservation

Read high-signal artifacts first:

1. `job.manifest.json`
2. `main.prompt.md`
3. `prompt-audit.json`
4. `branches/*.status.json`
5. `branches/*.review.json`
6. `git status`, `git worktree list`, `git diff --check`

Do not read full worker logs unless a branch status is missing, failed, or inconsistent with its diff.

Lite advice, when present, is a context router. Read validated Lite `advice.json` first to choose targeted original files, then open only cited originals needed for verification. Do not read Lite summaries and all originals by default. Lite cannot satisfy audit, branch, review, merge, cleanup, or DoD evidence requirements.

Do not read `goal-branch-orchestrator/SKILL.md` in the main orchestrator context. Main verifies branch-skill availability and dispatches branch sessions; the branch session is responsible for loading and following the branch skill.

Prefer native branch-agent delegation whenever the runtime surface provides it. Before launching a selected branch, render and preserve a delegation report, for example `render_branch_worktree_commands.py --branch Bxx --delegation-report /abs/bundle/branches/Bxx.delegation.json`. The report records `preferred_delegation=native_agent`, the selected native or CLI mode, native availability provenance, the CLI branch-control Codex model, and any CLI fallback reason. Use CLI worktree commands only as an explicit fallback when native delegation is unavailable or the user/runtime explicitly requests CLI mode; the default CLI branch-control model is `gpt-5.4-mini`.

While branch orchestrator agents are active, main must wait rather than poll. Use the native agent wait mechanism with the longest practical timeout. A no-completion wait result is not evidence that a branch is stalled. Main must not inspect worker packets, research-worker packets, reviewer packets, branch worktrees, process tables, or branch status files during active-branch waiting, and must not send status-check nudges. Inspect branch artifacts only after a branch agent completes, explicitly reports `blocked`/`failed`/`partial`, or the user explicitly switches to debug mode.

## Lite Advisor Policy

Main may create CLI-only Lite packets only after prompt audit has completed:

- `audit-defect-summary` after failed or blocked audit;
- `main-summary` after branch status/review artifacts are complete.

Main must not launch Lite before prompt audit to pre-screen prompts. Lite launchers run Gemini Flash Lite in read-only `plan` mode using the absolute Gemini path, version, and binary sha256 captured at packet creation and write `advice.json` plus `telemetry.json`. The launcher and validator rehash every input, `task.md`, `prompt.md`, and the Gemini binary; stale inputs, prompt/task drift, Gemini binary drift, or missing telemetry make the advice invalid or blocked. Validate advice with `scripts/validate_lite_advice.py` before using it. If Lite is unavailable, quota-limited, blocked, invalid, stale, or contradicted by branch artifacts, ignore it and continue with the normal status validation path unless the user explicitly required Lite. Record every used or ignored Lite packet in `main.status.json`; record `lite_advice: []` only when no relevant main Lite packet exists.

## Active Agent Limit

`max_active_branch_agents` is a hard runtime limit and must be <= 4. Launch branches as a rolling saturated pool. Parallelism is the default: keep up to `max_active_branch_agents` branch orchestrators active whenever eligible branches remain. `render_branch_worktree_commands.py --list-ready --limit 4` clamps to remaining capacity, so agents do not need to compute slot counts before calling it. Do not wait for a whole wave to finish. Waves are scheduling/order groups, not implicit dependency barriers. Defer a branch only while one of its manifest `depends_on` branch ids is incomplete. Keep at most `max_active_branch_agents` branch orchestrator agents active at once.

When a branch finishes:

1. collect its branch status and review artifacts;
2. record the result;
3. close or turn off the finished branch orchestrator agent;
4. launch the next eligible branch immediately after capacity is freed.

If an agent cannot be closed and capacity cannot be freed, return `blocked` rather than exceeding the limit.

## Fail-Closed Rules

Return `blocked` if:

- audit did not pass;
- `prompt-audit.json` does not pin the exact manifest and repo root for this run;
- prompt-audit telemetry is missing or inconsistent;
- manifest branch metadata is missing;
- manifest cleanup or artifact policy is missing or contradicted by `main.prompt.md`;
- manifest adaptation policy is missing or contradicted by an attempted amendment;
- `max_active_branch_agents` is missing, non-numeric, or greater than 4;
- the manifest is missing the fixed `worker_model_policy`;
- the manifest contains a `research-worker` work item but is missing a research-worker policy requiring `codex --search exec --ephemeral -s read-only` without user-config suppression, broad read-only information retrieval through configured CLI/MCP/connector/browser/search tools and shell/network inspection commands, and no file edits or state-changing actions;
- a branch is missing `max_active_worker_packets` or `worker_parallelism`;
- a branch does not have 1 to 4 worker packets or `max_active_worker_packets` greater than 4;
- a branch `worker_parallelism.scheduling_mode` is not `rolling`;
- a manifest contains more than 5 waves or more than 4 branches in any wave;
- a branch `depends_on` entry references an unknown, same, or later branch id;
- a work-item `depends_on` entry references an unknown, same, or later work item id;
- a single-branch or otherwise serialized manifest lacks `serial_reasons` or `parallelization_rationale`;
- a branch worktree target already exists without an explicit reuse policy;
- branch status/review files are missing;
- worker, research-worker, reviewer, Lite, or final summary telemetry required for a `pass` run is missing;
- branch status or main status validation fails;
- merge-ready branch status/review artifacts do not record base-range whitespace validation;
- main polled active branch agents' worker packets, research-worker packets, reviewer packets, worktrees, process tables, or status files instead of waiting;
- main treated Lite advice as audit, branch, review, mergeability, cleanup, or DoD evidence;
- DoD evidence is ambiguous or not falsifiable;
- the main prompt does not authorize a requested merge/cleanup operation.
