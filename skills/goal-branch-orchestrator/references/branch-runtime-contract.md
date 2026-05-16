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

Resolve all bundle-owned paths from the manifest directory before passing them to worker/reviewer packet scripts. Worker/reviewer packet directories, worktrees, task files, and context files must be absolute paths; the packet generator rejects relative paths and `..` traversal. Worker-owned files should stay repo-relative and must not contain absolute paths or `..` traversal.

## Worker Model Policy

Use this exact worker preference:

1. Gemini CLI with `gemini-3.1-pro-preview`
2. Gemini CLI with `gemini-3-flash-preview`
3. `gpt-5.3-codex-spark`
4. `gpt-5.4-mini`

Fallback is allowed only when:

- the current worker attempt did not produce a valid status file;
- the worker worktree is clean.

No Gemini model other than `gemini-3.1-pro-preview` and `gemini-3-flash-preview` may be used. Runtime packet generation must not accept model or approval-mode overrides. Worker prompts must render worktree-local context files as relative paths and embed out-of-worktree context snapshots so Gemini never needs to read bundle paths outside the worker worktree. Before each full Gemini worker attempt, run a 20-second headless Gemini probe with the same model to catch renamed, retired, unauthorized, or quota-blocked model IDs before the worktree can be dirtied. Gemini is best-effort because quota limits may be tight: missing Gemini CLI, quota errors, invalid JSON, unavailable models, or other clean failures should fall through to the next worker attempt. If Gemini returns marked worker JSON with `status: "success"`, normalize it to canonical `pass` before schema validation. If Gemini Pro, Gemini Flash, Spark, or mini fails after dirty edits and no valid `status.json` exists, stop and report `blocked`; do not continue in the same worktree. If every attempt fails cleanly, write a terminal blocked worker `status.json`.

## Reviewer Model Policy

Use this reviewer/auditor preference:

1. `gpt-5.5`
2. `gpt-5.4`

Reviewers are read-only. They produce findings, verification gaps, residual risks, and mergeability verdicts.

## Branch Status

Return/write status with these fields:

```json
{
  "branch_id": "B01",
  "status": "pass|partial|blocked|failed",
  "branch": "phaseX-B01",
  "worktree": "/absolute/path",
  "worker_statuses": [],
  "review_status": "mergeable|mergeable_after_fixes|blocked|reject|missing",
  "changed_files": [],
  "commands_run": [],
  "tests": [],
  "dod_checklist": [],
  "blockers": [],
  "handoff": ""
}
```

## Context Conservation

Read high-signal artifacts first:

1. branch prompt;
2. prompt audit JSON;
3. worker status JSON files;
4. `git diff --name-only`;
5. `git diff --check`;
6. focused test output;
7. review JSON.

Do not read full worker event logs unless a worker status is missing, failed, or inconsistent with the worktree diff.

While worker or reviewer launchers are active, wait rather than poll. A quiet launcher is not evidence of a stall. Do not inspect active launcher event logs, process tables, worker worktrees, status files, or review files while waiting. Inspect those artifacts only after the launcher exits, the generated status/review artifact is missing or failed, or the user explicitly switches to debug mode.

## Integration Rules

- Verify the active checkout with `pwd` and `git status --short --branch` before edits or merges.
- Keep worker ownership disjoint.
- Prefer one child worktree per worker when workers write.
- Launch independent worker packets concurrently when owned files and verification commands do not conflict.
- Record the reason in branch status if worker execution is serialized.
- Wait for active worker/reviewer launchers instead of polling their event logs, process tables, worktrees, status files, or review files.
- Inspect diffs before accepting worker summaries.
- Run both working-tree whitespace checks and base-range whitespace checks, for example `git diff --check <base-ref>...HEAD`, before review or merge readiness.
- Run branch-level validators after integrating workers.
- Preserve negative and unresolved scientific labels.
- Return blocked rather than guessing when prompt DoD is ambiguous.
