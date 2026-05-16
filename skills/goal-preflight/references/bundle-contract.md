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

```json
{
  "job_id": "phaseX",
  "main_prompt": "main.prompt.md",
  "base_ref": "main",
  "max_active_branch_agents": 5,
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
- `main.prompt.md` says no more than 5 branch orchestrator agents may be active.
- `main.prompt.md` says finished branch orchestrator agents must be closed before replacements launch.
- Branch prompts define objective, scope, work items, reviewer requirement, stop conditions, and falsifiable DoD.
