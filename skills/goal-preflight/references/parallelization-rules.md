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

Use waves to respect the hard active-agent limit:

- at most 5 branch orchestrator agents active at once;
- at most 5 branches per wave;
- up to 25 branches total by default;
- main orchestrator closes finished branch agents before launching replacements.

## Work Items

Work items are inputs for Spark workers. Keep them small:

- one objective;
- owned files/modules;
- short read-first list;
- exact commands;
- falsifiable DoD;
- stop conditions.

If a work item needs more than roughly 80k-100k tokens of context, split it.
