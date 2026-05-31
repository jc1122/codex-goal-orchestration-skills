# Actionability Rubric

Ask the user for clarification before writing final files if any of these are missing and cannot be inferred safely:

- `job_id`
- base ref
- merge policy
- cleanup policy
- top-level DoD
- branch objective or branch scope
- 1 to 4 bounded work items per branch
- serial reason when the job cannot be split into multiple branches
- required tests/validators
- acceptance evidence
- whether unresolved/negative/probe-only labels must be preserved

Do not ask when a conservative default is clear:

- base ref defaults to the current git branch, falling back to `main`;
- max active branch agents defaults to 4;
- total branch cap defaults to 20;
- branch worker packet cap defaults to 4;
- normal jobs should use 3-4 branches;
- parallelism is the default;
- runtime branch creation belongs to `goal-main-orchestrator`;
- prompt audit is mandatory and fail-closed.

Every DoD item should be falsifiable by a file, JSON status, command result, review verdict, or git state.
