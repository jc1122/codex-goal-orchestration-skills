# Goal Preflight Bundle Contract

## Required Bundle

```text
plans/orchestration/<job-id>/
  job.manifest.json
  main.prompt.md
  goal-bootloader.md
  preflight.lint.json
  PREFLIGHT_REPORT.md
  branches/
    B01.prompt.md
  workers/
  reviewers/
  audit/
```

## Manifest

Manifest-owned paths are reproducible POSIX-relative paths only. `main_prompt`, branch `prompt`, `status_path`, and `review_path` are relative to the manifest directory. `worktree_path` is relative to the repository root. Absolute paths, backslashes, empty path segments, `.`, and `..` are invalid.

Preflight script entry paths are absolute only: `--brief`, `--repo-root`, optional `--out-dir`, lint `--bundle-dir`, lint `--output`, and bootloader render `--bundle-dir` must not depend on the caller's current working directory.

`goal-bootloader.md` is location-bound because it embeds absolute bundle and repository roots. If the bundle or repository checkout moves, regenerate the bootloader from the preflight skill instead of editing those paths manually.

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
      "review_path": "branches/B01.review.json"
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
- `main.prompt.md` says parallelism is the default and branches in a wave should launch concurrently.
- `main.prompt.md` says finished branch orchestrator agents must be closed before replacements launch.
- `main.prompt.md` includes explicit cleanup and artifact policies so partial or blocked runs do not rely on runtime judgment.
- Branch prompts define objective, scope, work items, reviewer requirement, stop conditions, and falsifiable DoD.
- Branch prompts include base ref and require base-range whitespace validation before review or merge readiness.
- Single-branch bundles include `parallelization.serial_reason`.
