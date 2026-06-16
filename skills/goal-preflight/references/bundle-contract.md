# Goal Preflight Bundle Contract

## Required Bundle

```text
plans/orchestration/<job-id>/
  job.manifest.json
  main.prompt.md
  goal-bootloader.md
  preflight.brief.lint.json
  preflight.lint.json
  repair-gate.json
  readiness.json
  goal-config-selection.json
  preflight.pipeline.json
  PREFLIGHT_REPORT.md
  config-checks/
  telemetry.summary.json        # runtime-created before final pass
  telemetry.debug.summary.json  # debug mode runtime-created summary
  run.trace.jsonl               # debug mode structured full run trace
  schedulers/                     # runtime-created on first scheduler write
    main.scheduler.json         # runtime-created branch scheduler ledger
    B01.worker.scheduler.json   # runtime-created worker scheduler ledger
  branches/
    B01.prompt.md
    B01.pre_review_gate.json    # runtime-created before reviewer launch
  workers/                      # runtime-created on first worker packet
  research/                     # runtime-created on first research-worker packet
  reviewers/                    # runtime-created on reviewer dispatch
  audit/                        # runtime-created during prompt audit
  lite/                         # runtime-created only when Lite packets exist
  amendments/                   # runtime-created when amendment packets/proposals exist
```

## Manifest

Manifest-owned paths are reproducible POSIX-relative paths only. `main_prompt`, branch `prompt`, `status_path`, `review_path`, `pre_review_gate_path`, and scheduler paths are relative to the manifest directory. `worktree_path` is relative to the repository root. Branch `owned_paths` are derived from work item ownership; work item `owned_paths` and `context_files` are repo-relative paths. Numeric limits such as `max_active_branch_agents` and `max_active_worker_packets` must be JSON integers, not booleans or strings. Absolute paths, backslashes, empty path segments, `.`, and `..` are invalid. Branch `prompt`, `status_path`, `review_path`, and `pre_review_gate_path` values must be collision-free across all branches and must not reuse reserved bundle files such as `job.manifest.json`, `main.prompt.md`, `goal-bootloader.md`, `PREFLIGHT_REPORT.md`, or `preflight.lint.json`; branch `worktree_path` values must also be unique. Runtime normal worker artifacts must resolve to manifest-owned `workers/<packet_id>/status.json` paths. Runtime research-worker artifacts must resolve to manifest-owned `research/<packet_id>/research.json` paths. Reviewer packet ids must belong to the reviewed branch, and reviewer telemetry must match manifest-owned `reviewers/<packet_id>/route.json` unless accepted reuse points to existing source review and source telemetry.

Preflight script entry paths are absolute only: `prepare_goal_bundle.py --brief/--repo-root/--out-dir`, `lint_preflight_brief.py --brief/--repo-root/--output`, `create_goal_bundle.py --brief/--repo-root/--out-dir`, bundle lint `--bundle-dir/--output`, and bootloader render `--bundle-dir/--repo-root` must not depend on the caller's current working directory. The guided `prepare_goal_bundle.py` command writes canonical `preflight.brief.lint.json`, `goal-config-selection.json`, `preflight.lint.json`, `repair-gate.json`, `readiness.json`, and compact `preflight.pipeline.json` artifacts in the bundle; use `--verbose` only for full embedded selection/readiness payloads. The brief linter rejects missing concrete top-level `goal`, `source_summary`, `required_evidence`, and `final_dod`, placeholders, unsafe or missing context paths, vague DoD, missing verification commands, exact-source claims without inline payload or `source_attachments`, runtime-cap success criteria without concrete `runtime_cap`, and policies that do not preserve partial/blocked/failed or unresolved/negative/probe-only states before bundle generation. Omitted artifact/cleanup policies are linted after deterministic defaults are applied.

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
  "adaptation_policy": {
    "enabled": true,
    "mode": "amendment_proposals",
    "launcher": "goal-main-orchestrator",
    "may_modify_active_or_terminal_branches": false,
    "allowed_operations": ["add_branch", "split_unstarted_branch", "replace_unstarted_branch", "add_dependency_to_unstarted_branch", "add_work_item_to_unstarted_branch", "mark_unstarted_branch_obsolete"]
  },
  "amender_model_policy": {
    "default_ladder": ["ds-pro-max", "ds-flash-max"],
    "allowed_routes": ["ds-pro-max", "ds-flash-max"],
    "launcher": "goal-main-orchestrator",
    "selection_reason_required": true,
    "ordering_rule": "Selected amender routes must be a non-empty ordered subsequence of allowed_routes.",
    "sandbox": "read-only",
    "timeout_seconds": 1200
  },
  "worker_model_policy": {
    "default_ladder": ["ds-pro-max", "ds-flash-max", "codex-spark", "codex-mini"],
    "allowed_routes": ["ds-pro-max", "ds-flash-max", "codex-spark", "codex-mini"],
    "default_route_class": "normal-code",
    "route_classes": {
      "mechanical": ["ds-flash-max"],
      "docs": ["ds-flash-max"],
      "small-edit": ["ds-flash-max", "codex-mini"],
      "normal-code": ["ds-flash-max", "codex-spark"],
      "complex-code": ["ds-pro-max", "codex-spark"],
      "custom": ["ds-pro-max", "ds-flash-max", "codex-spark", "codex-mini"]
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
  "lite_model_policy": {
    "default_ladder": ["ds-flash-max"],
    "allowed_routes": ["ds-flash-max"],
    "model_map": {"ds-flash-max": "deepseek-v4-flash"},
    "launcher": "create_lite_advice_packet.py",
    "selection_reason_required": false,
    "ordering_rule": "Lite advisors use the fixed ds-flash-max bridge route; no runtime route broadening is allowed.",
    "approval_mode": "plan",
    "timeout_seconds": 600
  },
  "lite_advisor_policy": {
    "enabled": true,
    "role": "lite_advisor",
    "model_policy_ref": "lite_model_policy",
    "purpose": "context routing only",
    "launcher": "create_lite_advice_packet.py",
    "input_scope": "explicit packet input files only; no full repository dumps, full event logs, or unrelated result histories",
    "must_validate_with": "validate_lite_advice.py",
    "artifact_paths": "lite/<packet_id>/advice.json and lite/<packet_id>/input-files.json",
    "telemetry_required": true
  },
  "review_model_policy": {
    "router": "deterministic-v1",
    "default_tier": "standard",
    "routes": {
      "light": ["ds-flash-max"],
      "standard": ["ds-pro-max"],
      "heavy": ["ds-pro-max", "gpt-5.5"]
    }
  },
  "orchestration_watchdog": {
    "main_no_completion_wait_limit": 3,
    "branch_no_completion_wait_limit": 3
  },
  "preflight_lite_advice": [],
  "telemetry_policy": {
    "schema_version": 1,
    "mode": "standard",
    "raw_text": false,
    "collect": []
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
      "owned_paths": ["src/example.py"],
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

- `goal-bootloader.md` invokes `$goal-main-orchestrator` only when readiness launch is allowed; blocked-readiness bootloaders are corrective-only and show fix plus readiness recheck commands.
- `goal-bootloader.md` points to the bundle root, repository root, `job.manifest.json`, and `main.prompt.md`.
- `goal-bootloader.md` is under 4000 characters.
- Generated prompts and bootloaders stay compact: they point runtime agents at `runtime_phase_manifest.py --markdown`, `job.manifest.json`, script output, and validators instead of copying full policy text.
- `main.prompt.md` says prompt audit is first and branches cannot be created until audit passes.
- `main.prompt.md` says no more than 4 branch orchestrator agents may be active.
- `main.prompt.md` says parallelism is the default, branch orchestrator slots should stay saturated up to `max_active_branch_agents`, and waves are dependency-aware scheduling/order groups rather than implicit dependency barriers.
- `PREFLIGHT_REPORT.md` states the same: waves are dependency-aware scheduling/order groups, while branch dependencies are explicit `depends_on` constraints.
- Branch manifest `depends_on` entries reference only prior branch ids; a branch is deferred only while one of those explicit dependencies is incomplete.
- `main.prompt.md` says finished branch orchestrator agents must be closed before replacements launch.
- `main.prompt.md` requires manifest-bound `validate_branch_status.py --manifest ... --status ...` for branch outputs and manifest-bound `validate_main_status.py --manifest ... --status ...` for final output.
- `job.manifest.json` contains `adaptation_policy` and `amender_model_policy`; main may invoke `goal-plan-amender` only after terminal branch validation and only for future unstarted work.
- Plan-amender packets live under `amendments/Axxx.packet/`, select an allowed amender model ladder in `route.json`, write `telemetry.json`, and write proposals to the sibling `amendments/Axxx.proposal.json`.
- `main.prompt.md` requires `summarize_telemetry.py --bundle-dir <bundle>` before final validation and requires `telemetry.summary.json` for pass.
- `main.prompt.md` says optional Lite advisors are context routers only and cannot satisfy audit, review, mergeability, or DoD evidence.
- `job.manifest.json` contains a `telemetry_policy` object. `schema_version` is 1 and mode is `standard` by default; debug mode is passive and must not change route selection, polling, or scheduling strategy. Debug summary generation writes `run.trace.jsonl` as a structured trace of scheduler events, packet debug events, launcher states, model attempts, packet telemetry, and terminal artifacts without duplicating raw prompts, outputs, or full logs.
- Preflight brief shorthand `telemetry_mode: "debug"` or `debug_telemetry: true` expands deterministically to the full safe debug `telemetry_policy`.
- Preflight brief `source_attachments` may list repo-relative files or `{path,label,kind}` objects for large benchmark/spec inputs. Bundle creation stores file size and SHA-256 in the manifest and prompt instead of forcing all source text into the top-level goal. When the brief claims an exact instance/list/source is provided, either the exact payload must be inline or a source attachment must be declared.
- Preflight brief `runtime_cap` records concrete time/runtime caps mentioned by the success criteria, preferably including the CLI flag to enforce the cap. Bundle lint fails if cap wording appears without a concrete manifest value.
- Bundle lint checks verification commands for common repo path references and `python -m package.module` references. A branch may verify files/modules it owns or that completed dependency branches own; future-owned or unrelated owned paths are critical defects.
- Dependent branches with no direct work-item `context_files` must include `dependency_context_reason`; bundle creation supplies a default reason that tells runtime to inspect completed dependency branch status/review artifacts before launching the dependent branch.
- Readiness treats `repo_status.repo_is_git=false` as a blocked runtime gate for branch/worktree orchestration. Directory mode is not runtime-supported until a future no-git execution mode is explicit in the manifest and bootloader.
- `job.manifest.json` contains `worker_model_policy` with the fixed `ds-pro-max -> ds-flash-max -> codex-spark -> codex-mini` ladder (bridge deepseek leading, native Codex fallback); branch-selected worker routes must be non-empty ordered subsequences with recorded reasons.
- `job.manifest.json` contains `research_worker_policy` defining `research-worker` packets as broad read-only information retrieval through Codex native search plus configured CLI/MCP/connector/browser/search tools, shell/network inspection commands, remote APIs, package metadata lookups, and read-only local access. It must not suppress user config, and it must prohibit file edits and state-changing actions.
- `job.manifest.json` contains `lite_model_policy` and `lite_advisor_policy`; Lite advisors are fixed-route context routers, validate through `validate_lite_advice.py`, write telemetry, and cannot satisfy audit/review/mergeability/DoD evidence.
- `job.manifest.json` contains `preflight_lite_advice` as an array. It is empty when preflight Lite was not used; otherwise every preflight Lite packet under `lite/` is recorded with relative `lite/<packet_id>/advice.json` and `lite/<packet_id>/input-files.json` paths plus validation status/defects.
- Branch prompt/status/review paths are unique and cannot overwrite one another.
- `main.prompt.md` includes explicit cleanup and artifact policies so partial or blocked runs do not rely on runtime judgment.
- Branch prompts define objective, scope, work items, reviewer requirement, stop conditions, and falsifiable DoD.
- Branch prompts include base ref and require base-range whitespace validation before review or merge readiness.
- Branch prompts require final branch status validation with concrete bundle-root paths, equivalent to `validate_branch_status.py --manifest <bundle>/job.manifest.json --status <bundle>/branches/Bxx.status.json`.
- Branch prompts require worker, research-worker, reviewer, and Lite packet `telemetry.json` artifacts for pass.
- Branch prompts say optional Lite advisors may guide targeted context only after required checks and never while worker/research-worker/reviewer launchers are active.
- Branch prompts define Worker Model Routing and require `selected_ladder` plus `selection_reason` in every worker status and branch rollup.
- Branch manifest work items include deterministic `packet_id` values in `<branch_id>-<work_item_id>` form, optional `worker_type` values of `worker` or `research-worker`, and branch prompts list those packet ids.
- Work-item `depends_on` entries reference only prior work item ids and are the only reason to defer an otherwise eligible worker.
- Branch manifest entries and prompts include 1 to 4 worker packets per branch, a hard `max_active_worker_packets` cap of 1-4/default 4, and require independent worker packets to launch as a rolling saturated pool up to that active cap.
- Single-branch, under-capacity, and dependency-serialized bundles include `parallelization.serial_reasons`; `create_goal_bundle.py` supplies deterministic reasons when the brief omits them.
- Generated prompts name `schedulers/main.scheduler.json`, `schedulers/<branch-id>.worker.scheduler.json`, and branch-local `pre_review_gate.json`; runtime validators require current ledgers/gates before pass claims.
