# {branch_id}: {title}

Branch id: {branch_id}
Base ref: {base_ref}
Branch name: {branch_name}
Worktree path: {worktree_path}
Wave: {wave}

## Objective

{objective}

## Scope

{scope}

## Owned Paths

{owned_paths}

## Work Items

{work_items}

## Worker Parallelism

Parallel worker packets are the default for independent work items. Launch independent workers in separate child worktrees whenever their owned paths and verification commands do not conflict. If a work item has a `Depends on` entry, do not launch it until the dependency's output is integrated and available as context. If this branch is executed serially, record the reason in the branch status blockers or summary.

After worker dispatch, wait for active worker launchers; do not poll active worker worktrees, event logs, process tables, or status files unless the user explicitly enters debug mode or a launcher exits without a valid status.

## Tests And Validators

{tests}

## Reviewer Requirement

Dispatch a read-only heavy-model reviewer after worker integration. The branch may return pass only if the reviewer verdict is mergeable or the branch DoD explicitly permits a weaker verdict.

After reviewer dispatch, wait for the reviewer launcher; do not poll active reviewer event logs, process tables, or review files unless the user explicitly enters debug mode or the launcher exits without a valid review.

## Bootstrap Requirement

Run the branch skill and Codex CLI availability bootstrap before worker dispatch. Return blocked if the bootstrap fails.

## Stop Conditions

{stop_conditions}

## Definition of Done

- Branch skill and Codex CLI availability bootstrap passed before worker dispatch.
- `git diff --check {base_ref}...HEAD` passed before review or merge readiness was reported.
- Active worker/reviewer launchers were waited on rather than polled.
{dod}
