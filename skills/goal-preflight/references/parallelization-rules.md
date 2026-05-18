# Parallelization Rules

## Branches

Split by independent outcomes. A good branch can be completed, reviewed, and judged without waiting on another branch except for final integration.

Prefer branches that:

- own mostly disjoint files;
- have a clear user/research outcome;
- can run focused tests independently;
- can be reviewed with local evidence.

Avoid branches that:

- all edit the same central registry without an explicit ordering reason;
- depend on another branch's unmerged code;
- combine implementation, docs, and validation for unrelated outcomes;
- require broad repo exploration by every worker.

## Rolling Branch Scheduling

Use `max_active_branch_agents` as the real concurrency cap and keep slots saturated with eligible branches:

- at most 4 branch orchestrator agents active at once;
- at most 4 branches per scheduling group/wave;
- up to 5 scheduling groups/waves;
- up to 20 branches total;
- main orchestrator closes finished branch agents before launching replacements;
- when one branch finishes, launch the next eligible branch immediately if capacity is available;
- waves are scheduling/order groups, not implicit dependency barriers;
- defer a branch only while one of its explicit manifest `depends_on` branch ids is incomplete.

Single-branch bundles are serialized and must include `serial_reason`. A `max_active_branch_agents` value below 4 must include `parallelization_rationale` or `serial_reason`.

Use branch-level `depends_on` only for true prior-branch dependencies. A dependency must reference an earlier branch id in the manifest order; use it for cases like final audits, integration branches, or branches that need evidence produced by another branch. Do not use waves alone to express dependency.

## Work Items

Work items are inputs for worker packet launchers. Keep them small:

- one objective;
- owned files/modules;
- short read-first list;
- exact commands;
- falsifiable DoD;
- stop conditions.

Use normal `worker` work items for code, config, test, and documentation changes. Use `research-worker` only for outside-information gathering that needs broad read-only information retrieval plus local read-only file context. Research workers may use Codex native search, configured read-only CLI/MCP/connector/browser/search tools, package metadata lookups, remote APIs, shell/network inspection commands, and local read-only inspection. They must not edit files or perform state-changing, destructive, credential, posting, purchasing, or remote mutation actions.

If a work item needs more than roughly 80k-100k tokens of context, split it.

When two work items in the same branch own disjoint files and have independent verification commands, the branch prompt should direct the branch orchestrator to launch them as parallel worker packets in separate child worktrees.

Each branch uses 1 to 4 worker packets total and at most 4 active worker packets. Launch independent worker packets as a rolling saturated pool up to the active cap. When a worker launcher exits, the branch orchestrator collects and integrates its status/diff, frees that active slot, and launches the next eligible worker immediately if capacity is available. Defer a worker only while one of its explicit prior work-item `depends_on` ids is incomplete. If more than 4 worker packets would be needed, split the branch or record why the source material cannot be safely decomposed before generating the bundle. Serial or under-capacity worker execution requires a recorded reason.
