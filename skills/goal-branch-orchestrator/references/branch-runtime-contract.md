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

Each manifest branch must declare `max_active_worker_packets` and `worker_parallelism`. A branch uses 1 to 4 prepared worker packets total. `max_active_worker_packets` is a hard per-branch active cap from 1 to 4. Parallel worker dispatch is the default: launch independent worker packets concurrently up to that cap. If more than 4 worker packets would be needed, stop and split the branch instead of inventing extra packets. If a branch runs serially or below capacity, record the reason in `worker_parallelism.serial_reasons`.

## Worker Model Policy

Use this exact worker preference:

1. Gemini CLI with `gemini-3.1-pro-preview`
2. Gemini CLI with `gemini-3-flash-preview`
3. GitHub Copilot CLI with `gpt-5.4` and `--effort high`
4. `gpt-5.3-codex-spark`
5. `gpt-5.4-mini`

Fallback is allowed only when:

- the current worker attempt did not produce a valid status file;
- the worker worktree is clean.

No Gemini model other than `gemini-3.1-pro-preview` and `gemini-3-flash-preview` may be used. Runtime packet generation must not accept model, effort, approval-mode, or permission overrides. Worker prompts must render worktree-local context files as relative paths and embed out-of-worktree context snapshots so workspace-restricted CLIs never need to read bundle paths outside the worker worktree. All worker providers receive the same generated prompt and must satisfy the same worker status schema. CLI permission controls are provider-specific, so a provider status is only evidence after schema validation, clean fallback boundaries, branch diff inspection, and branch-level tests. Before each full Gemini worker attempt, run a 20-second headless probe with the same Gemini model. Before the full Copilot worker attempt, run a 20-second no-tool probe with `gpt-5-mini` and `--effort low` to verify Copilot CLI/auth/routing without spending a `gpt-5.4` call. Gemini and Copilot are best-effort because quota limits may be tight: missing CLIs, quota errors, invalid JSON, unavailable models, or other clean failures should fall through to the next worker attempt. Copilot must run real worker packets in programmatic mode with `--model gpt-5.4`, `--effort high`, `--no-ask-user`, minimal tool permissions, JSONL output, and a Markdown session share. Do not use `/fleet` for packet execution; the branch orchestrator owns worker parallelism externally. If Gemini or Copilot returns marked worker JSON with `status: "success"`, normalize it to canonical `pass` before schema validation. If Gemini Pro, Gemini Flash, Copilot, Spark, or mini fails after dirty edits and no valid `status.json` exists, stop and report `blocked`; do not continue in the same worktree. If every attempt fails cleanly, write a terminal blocked worker `status.json`.

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
  "worker_statuses": [
    {
      "packet_id": "B01-W01",
      "status": "pass|partial|blocked|failed",
      "status_path": "/absolute/path/to/workers/B01-W01/status.json",
      "worktree": "/absolute/path/to/.worktrees/phaseX-B01-W01",
      "changed_files": ["src/example.py"],
      "commands_run": ["python3 -m pytest tests/test_example.py -q"],
      "tests": ["python3 -m pytest tests/test_example.py -q"],
      "blockers": [],
      "handoff": "concise worker handoff"
    }
  ],
  "worker_parallelism": {
    "max_worker_packets_per_branch": 4,
    "max_active_worker_packets": 4,
    "max_observed_active_worker_packets": 4,
    "concurrent_launch_default": true,
    "serialized_workers": [],
    "serial_reasons": []
  },
  "review_status": "mergeable|mergeable_after_fixes|blocked|reject|missing",
  "changed_files": ["src/example.py", "tests/test_example.py"],
  "commands_run": ["python3 -m pytest tests/test_example.py -q", "git diff --check main...HEAD"],
  "tests": ["python3 -m pytest tests/test_example.py -q"],
  "dod_checklist": ["focused validator passed", "reviewer verdict mergeable"],
  "blockers": [],
  "handoff": "concise branch handoff"
}
```

Validate the final branch status with `scripts/validate_branch_status.py` before reporting `pass`. Worker and branch `changed_files` entries must be repo-relative file paths without git porcelain prefixes; command and test evidence must be exact command strings. `pass` requires `review_status: "mergeable"`, a non-empty command list, a non-empty DoD checklist, and no blockers. Non-pass worker or branch statuses must include at least one blocker.

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
- Use 1 to 4 worker packets for one branch.
- Launch independent worker packets concurrently when owned files and verification commands do not conflict, up to `max_active_worker_packets`.
- Never exceed 4 active worker packets in one branch.
- Record the reason in `worker_parallelism.serial_reasons` if worker execution is serialized.
- Wait for active worker/reviewer launchers instead of polling their event logs, process tables, worktrees, status files, or review files.
- Inspect diffs before accepting worker summaries.
- Run both working-tree whitespace checks and base-range whitespace checks, for example `git diff --check <base-ref>...HEAD`, before review or merge readiness.
- Run branch-level validators after integrating workers.
- Preserve negative and unresolved scientific labels.
- Return blocked rather than guessing when prompt DoD is ambiguous.
