# Goal Preflight Bundle Contract

## Required Bundle

```text
plans/orchestration/<job-id>/
  job.manifest.json
  main.prompt.md
  goal-bootloader.md
  preflight.lint.json
  PREFLIGHT_REPORT.md
  telemetry.summary.json        # runtime-created before final pass
  schedulers/
    main.scheduler.json         # runtime-created branch scheduler ledger
    B01.worker.scheduler.json   # runtime-created worker scheduler ledger
  branches/
    B01.prompt.md
    B01.pre_review_gate.json    # runtime-created before reviewer launch
  workers/
  research/
  reviewers/
  audit/
  lite/
```

## Manifest

Manifest-owned paths are reproducible POSIX-relative paths only. `main_prompt`, branch `prompt`, `status_path`, `review_path`, `pre_review_gate_path`, and scheduler paths are relative to the manifest directory. `worktree_path` is relative to the repository root. Work item `owned_paths` and `context_files` are repo-relative paths. Numeric limits such as `max_active_branch_agents` and `max_active_worker_packets` must be JSON integers, not booleans or strings. Absolute paths, backslashes, empty path segments, `.`, and `..` are invalid. Branch `prompt`, `status_path`, `review_path`, and `pre_review_gate_path` values must be collision-free across all branches and must not reuse reserved bundle files such as `job.manifest.json`, `main.prompt.md`, `goal-bootloader.md`, `PREFLIGHT_REPORT.md`, or `preflight.lint.json`; branch `worktree_path` values must also be unique. Runtime normal worker artifacts must resolve to manifest-owned `workers/<packet_id>/status.json` paths. Runtime research-worker artifacts must resolve to manifest-owned `research/<packet_id>/research.json` paths. Reviewer packet ids must belong to the reviewed branch.

Preflight script entry paths are absolute only: `--brief`, `--repo-root`, optional `--out-dir`, lint `--bundle-dir`, lint `--output`, and bootloader render `--bundle-dir`/`--repo-root` must not depend on the caller's current working directory.

`goal-bootloader.md` is location-bound because it embeds absolute bundle and repository roots. If the bundle or repository checkout moves, regenerate the bootloader from the preflight skill or with `render_goal_bootloader.py --repo-root /absolute/path/to/repo --write` instead of editing those paths manually.

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
  "worker_model_policy": {
    "default_ladder": ["gemini-pro", "gemini-flash", "codex-spark", "copilot-gpt-5.4", "codex-mini"],
    "allowed_routes": ["gemini-pro", "gemini-flash", "codex-spark", "copilot-gpt-5.4", "codex-mini"],
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
  "preflight_lite_advice": [],
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

## Compatibility Rules

- `goal-bootloader.md` invokes `$goal-main-orchestrator`.
- `goal-bootloader.md` points to the bundle root, repository root, `job.manifest.json`, and `main.prompt.md`.
- `goal-bootloader.md` is under 4000 characters.
- `main.prompt.md` says prompt audit is first and branches cannot be created until audit passes.
- `main.prompt.md` says no more than 4 branch orchestrator agents may be active.
- `main.prompt.md` says parallelism is the default, branch orchestrator slots should stay saturated up to `max_active_branch_agents`, and waves are scheduling/order groups rather than implicit dependency barriers.
- Branch manifest `depends_on` entries reference only prior branch ids; a branch is deferred only while one of those explicit dependencies is incomplete.
- `main.prompt.md` says finished branch orchestrator agents must be closed before replacements launch.
- `main.prompt.md` requires manifest-bound `validate_branch_status.py` for branch outputs and manifest-bound `validate_main_status.py` for final output.
- `main.prompt.md` requires `summarize_telemetry.py --bundle-dir <bundle>` before final validation and requires `telemetry.summary.json` for pass.
- `main.prompt.md` says optional Lite advisors are context routers only and cannot satisfy audit, review, mergeability, or DoD evidence.
- `job.manifest.json` contains `worker_model_policy` with the fixed Gemini Pro -> Gemini Flash -> Codex Spark -> GitHub Copilot `gpt-5.4` -> Codex mini ladder; branch-selected worker routes must be non-empty ordered subsequences with recorded reasons.
- `job.manifest.json` contains `research_worker_policy` defining `research-worker` packets as broad read-only information retrieval through Codex native search plus configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and read-only local access. It must not suppress user config, and it must prohibit file edits and state-changing actions.
- `job.manifest.json` contains `preflight_lite_advice` as an array. It is empty when preflight Lite was not used; otherwise every preflight Lite packet under `lite/` is recorded with relative `lite/<packet_id>/advice.json` and `lite/<packet_id>/input-files.json` paths plus validation status/defects.
- Branch prompt/status/review paths are unique and cannot overwrite one another.
- `main.prompt.md` includes explicit cleanup and artifact policies so partial or blocked runs do not rely on runtime judgment.
- Branch prompts define objective, scope, work items, reviewer requirement, stop conditions, and falsifiable DoD.
- Branch prompts include base ref and require base-range whitespace validation before review or merge readiness.
- Branch prompts require final branch status validation with `validate_branch_status.py --manifest /absolute/path/to/job.manifest.json`.
- Branch prompts require worker, research-worker, reviewer, and Lite packet `telemetry.json` artifacts for pass.
- Branch prompts say optional Lite advisors may guide targeted context only after required checks and never while worker/research-worker/reviewer launchers are active.
- Branch prompts define Worker Model Routing and require `selected_ladder` plus `selection_reason` in every worker status and branch rollup.
- Branch manifest work items include deterministic `packet_id` values in `<branch_id>-<work_item_id>` form, optional `worker_type` values of `worker` or `research-worker`, and branch prompts list those packet ids.
- Work-item `depends_on` entries reference only prior work item ids and are the only reason to defer an otherwise eligible worker.
- Branch manifest entries and prompts include 1 to 4 worker packets per branch, a hard `max_active_worker_packets` cap of 1-4/default 4, and require independent worker packets to launch as a rolling saturated pool up to that active cap.
- Single-branch bundles include `parallelization.serial_reasons`.
- Generated prompts name `schedulers/main.scheduler.json`, `schedulers/<branch-id>.worker.scheduler.json`, and branch-local `pre_review_gate.json`; runtime validators require current ledgers/gates before pass claims.
