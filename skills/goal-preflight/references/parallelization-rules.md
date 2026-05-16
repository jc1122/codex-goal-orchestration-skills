# Parallelization Rules

## Branches

Split by independent outcomes. A good branch can be completed, reviewed, and judged without waiting on another branch except for final integration.

Prefer branches that:

- own mostly disjoint files;
- have a clear user/research outcome;
- can run focused tests independently;
- can be reviewed with local evidence.

Avoid branches that:

- all edit the same central registry in the same wave;
- depend on another branch's unmerged code;
- combine implementation, docs, and validation for unrelated outcomes;
- require broad repo exploration by every worker.

## Waves

Use waves to maximize safe parallel execution while respecting the hard active-agent limit:

- at most 4 branch orchestrator agents active at once;
- at most 4 branches per wave;
- up to 5 waves;
- up to 20 branches total;
- main orchestrator closes finished branch agents before launching replacements.
- launch all branches in the current wave concurrently up to `max_active_branch_agents`.

Single-branch bundles are serialized and must include `serial_reason`. Underfilled non-final waves or a `max_active_branch_agents` value below 4 must include `parallelization_rationale` or `serial_reason`.

## Work Items

Work items are inputs for Spark workers. Keep them small:

- one objective;
- owned files/modules;
- short read-first list;
- exact commands;
- falsifiable DoD;
- stop conditions.

If a work item needs more than roughly 80k-100k tokens of context, split it.

When two work items in the same branch own disjoint files and have independent verification commands, the branch prompt should direct the branch orchestrator to launch them as parallel worker packets in separate child worktrees.
