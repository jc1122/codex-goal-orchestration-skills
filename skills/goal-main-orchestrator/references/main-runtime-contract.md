# Main Runtime Contract

## Inputs

The main runtime consumes artifacts prepared before `/goal` starts:

- `job.manifest.json`
- `main.prompt.md`
- all branch prompt files listed in the manifest

The main runtime may create execution artifacts:

- `prompt-audit.json`
- `main.status.json`
- optional Lite advisory artifacts under `lite/`
- branch integration branches/worktrees
- branch status/review artifacts produced by branch orchestrators

It must not create or rewrite the bootloader, main prompt, branch prompts, or manifest.

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
    "serial_reason": "",
    "parallelization_rationale": "Branches are grouped into waves of up to 4 independent branch agents.",
    "wave_execution": "Launch every branch in the current wave concurrently, then close finished branch orchestrators before launching the next wave."
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
      "max_active_worker_packets": 4,
      "work_items": [
        {
          "id": "W01",
          "packet_id": "B01-W01",
          "objective": "Bounded worker objective.",
          "owned_paths": ["src/example.py"],
          "verification": ["python3 -m pytest tests/test_example.py -q"],
          "dod": ["Focused validator passes."]
        }
      ],
      "worker_parallelism": {
        "parallelism_default": true,
        "max_active_worker_packets": 4,
        "max_worker_packets_per_branch": 4,
        "serial_reason": "",
        "parallelization_rationale": "Launch independent worker packets concurrently up to 4 active worker packets.",
        "wave_execution": "Launch independent worker packets concurrently up to max_active_worker_packets; collect finished worker status before launching replacements."
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
	  "branch_statuses": [
    {
      "branch_id": "B01",
      "status": "pass|partial|blocked|failed",
      "status_path": "branches/B01.status.json",
      "review_path": "branches/B01.review.json",
	      "review_status": "mergeable|mergeable_after_fixes|blocked|reject|missing"
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
	  "commands_run": ["python3 scripts/check_goal_skill_availability.py ...", "python3 scripts/validate_main_status.py --manifest ..."],
  "dod_checklist": ["prompt audit passed", "all branch statuses validated"],
  "blockers": [],
  "summary": "concise main handoff"
}
```

Validate every branch status with `goal-branch-orchestrator/scripts/validate_branch_status.py --manifest /absolute/path/to/job.manifest.json` before accepting it. Validate the final main status with `scripts/validate_main_status.py --manifest /absolute/path/to/job.manifest.json` before reporting `pass`; this validator opens every listed manifest-referenced branch status artifact, validates it, and fails if it is missing, invalid, or inconsistent with `main.status.json`. It also opens review artifacts whenever `review_status` is not `missing`, requires every recorded Lite packet to use manifest-owned `lite/<packet_id>/` paths, validates every Lite advice artifact and live input/prompt hashes, requires recorded `validation_status` and `validation_defects` to match actual validation, and for `pass` requires every worker artifact to live at the manifest-owned `workers/<packet_id>/status.json`, every review artifact to use a same-branch reviewer packet id, contain exact base-range whitespace command evidence from `git diff --check <base-ref>...HEAD`, and have no verification gaps when `mergeable`. Main `pass` requires `audit_status: "pass"`, exactly the manifest branch summary set with manifest-matching status/review paths, every branch summary status `pass`, every passing branch summary review status `mergeable`, a `lite_advice` array, a non-empty command list, a non-empty DoD checklist, and no blockers. Non-pass main status must include at least one blocker.

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

Do not read `goal-branch-orchestrator/SKILL.md` in the main orchestrator context. Main verifies branch-skill availability, creates branch worktrees, and dispatches branch sessions; the branch session is responsible for loading and following the branch skill.

While branch orchestrator agents are active, main must wait rather than poll. Use the native agent wait mechanism with the longest practical timeout. A no-completion wait result is not evidence that a branch is stalled. Main must not inspect worker packets, reviewer packets, branch worktrees, process tables, or branch status files during active-branch waiting, and must not send status-check nudges. Inspect branch artifacts only after a branch agent completes, explicitly reports `blocked`/`failed`/`partial`, or the user explicitly switches to debug mode.

## Lite Advisor Policy

Main may create CLI-only Lite packets only after prompt audit has completed:

- `audit-defect-summary` after failed or blocked audit;
- `main-summary` after branch status/review artifacts are complete.

Main must not launch Lite before prompt audit to pre-screen prompts. Lite launchers run Gemini Flash Lite in read-only `plan` mode using the absolute Gemini path and version captured at packet creation and write `advice.json`. The launcher and validator rehash every input and `prompt.md`; stale inputs or prompt drift make the advice invalid. Validate advice with `scripts/validate_lite_advice.py` before using it. If Lite is unavailable, quota-limited, blocked, invalid, stale, or contradicted by branch artifacts, ignore it and continue with the normal status validation path unless the user explicitly required Lite. Record every used or ignored Lite packet in `main.status.json`; record `lite_advice: []` when no Lite packet was used.

## Active Agent Limit

`max_active_branch_agents` is a hard runtime limit and must be <= 4. Launch branches by wave when `waves` is present. Parallelism is the default: launch every branch in the current wave concurrently up to the limit, then wait for the wave to finish before launching the next wave. Keep at most that many branch orchestrator agents active at once.

When a branch finishes:

1. collect its branch status and review artifacts;
2. record the result;
3. close or turn off the finished branch orchestrator agent;
4. launch a replacement only after capacity is freed.

If an agent cannot be closed and capacity cannot be freed, return `blocked` rather than exceeding the limit.

## Fail-Closed Rules

Return `blocked` if:

- audit did not pass;
- `prompt-audit.json` does not pin the exact manifest and repo root for this run;
- manifest branch metadata is missing;
- manifest cleanup or artifact policy is missing or contradicted by `main.prompt.md`;
- `max_active_branch_agents` is missing, non-numeric, or greater than 4;
- a branch is missing `max_active_worker_packets` or `worker_parallelism`;
- a branch does not have 1 to 4 worker packets or `max_active_worker_packets` greater than 4;
- a wave contains more branches than `max_active_branch_agents`;
- a manifest contains more than 5 waves or more than 4 branches in any wave;
- a single-branch or otherwise serialized manifest lacks `serial_reason` or `parallelization_rationale`;
- a branch worktree target already exists without an explicit reuse policy;
- branch status/review files are missing;
- branch status or main status validation fails;
- merge-ready branch status/review artifacts do not record base-range whitespace validation;
- main polled active branch agents' worker packets, reviewer packets, worktrees, process tables, or status files instead of waiting;
- main treated Lite advice as audit, branch, review, mergeability, cleanup, or DoD evidence;
- DoD evidence is ambiguous or not falsifiable;
- the main prompt does not authorize a requested merge/cleanup operation.
