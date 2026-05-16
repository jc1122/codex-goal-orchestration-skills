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

Resolve all bundle-owned paths from the manifest directory before passing them to worker/reviewer packet scripts. Worker/reviewer packet directories must be absolute paths under the prepared bundle's `workers/` or `reviewers/` directory. Worker-owned files should stay repo-relative and must not contain absolute paths or `..` traversal.

## Worker Model Policy

Use this exact worker preference:

1. Gemini CLI with `gemini-3.1-pro`
2. Gemini CLI with `gemini-3.1-flash`
3. `gpt-5.3-codex-spark`
4. `gpt-5.4-mini`

Fallback is allowed only when:

- the current worker attempt did not produce a valid status file;
- the worker worktree is clean; or
- the fallback is launched from a fresh child worktree created from the same baseline.

No Gemini model other than `gemini-3.1-pro` and `gemini-3.1-flash` may be used. Gemini is best-effort because quota limits may be tight: missing Gemini CLI, quota errors, invalid JSON, or other clean failures should fall through to the next worker attempt. If Gemini Pro, Gemini Flash, or Spark fails after dirty edits and no valid `status.json` exists, stop and report `blocked`; do not continue in the same worktree.

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

## Integration Rules

- Verify the active checkout with `pwd` and `git status --short --branch` before edits or merges.
- Keep worker ownership disjoint.
- Prefer one child worktree per worker when workers write.
- Inspect diffs before accepting worker summaries.
- Run branch-level validators after integrating workers.
- Preserve negative and unresolved scientific labels.
- Return blocked rather than guessing when prompt DoD is ambiguous.
