# Main Runtime Contract

## Inputs

The main runtime consumes artifacts prepared before `/goal` starts:

- `job.manifest.json`
- `main.prompt.md`
- all branch prompt files listed in the manifest

The main runtime may create execution artifacts:

- `prompt-audit.json`
- `main.status.json`
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

## Main Status

Return/write status with these fields:

```json
{
  "job_id": "phaseX",
  "status": "pass|partial|blocked|failed",
  "audit_status": "pass|failed|blocked|missing",
  "branch_statuses": [],
  "commands_run": [],
  "dod_checklist": [],
  "blockers": [],
  "summary": ""
}
```

## Context Conservation

Read high-signal artifacts first:

1. `job.manifest.json`
2. `main.prompt.md`
3. `prompt-audit.json`
4. `branches/*.status.json`
5. `branches/*.review.json`
6. `git status`, `git worktree list`, `git diff --check`

Do not read full worker logs unless a branch status is missing, failed, or inconsistent with its diff.

## Active Agent Limit

`max_active_branch_agents` is a hard runtime limit and must be <= 5. Launch branches by wave when `waves` is present. Keep at most that many branch orchestrator agents active at once.

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
- `max_active_branch_agents` is missing, non-numeric, or greater than 5;
- a wave contains more branches than `max_active_branch_agents`;
- a branch worktree target already exists without an explicit reuse policy;
- branch status/review files are missing;
- DoD evidence is ambiguous or not falsifiable;
- the main prompt does not authorize a requested merge/cleanup operation.
