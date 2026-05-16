# Prompt Audit Contract

The prompt auditor is a read-only heavy-model agent launched before branch creation. The launcher uses exactly `gpt-5.5`, then `gpt-5.4`; do not pass model overrides.

## Files To Check

- `job.manifest.json`
- `main.prompt.md`
- every branch prompt listed in the manifest

## Required Checks

- every listed file exists and is readable;
- manifest branch ids, branch names, worktree paths, status paths, and review paths are present;
- branch prompt paths, status paths, review paths, and worktree paths are unique and collision-free;
- every branch declares 1 to 4 work items, deterministic worker `packet_id` values in `<branch_id>-<work_item_id>` form, `max_active_worker_packets` from 1 to 4, and `worker_parallelism.parallelism_default=true`;
- `max_active_branch_agents` is present and <= 4;
- manifest artifact and cleanup policies are present, non-empty, and are repeated or honored by `main.prompt.md`;
- manifest waves, when present, cover every branch exactly once, no wave exceeds `max_active_branch_agents`, no wave has more than 4 branches, and there are no more than 5 waves;
- parallelism is the default and single-branch or otherwise serialized plans include a serial reason or parallelization rationale;
- `main.prompt.md` defines a falsifiable top-level DoD;
- every branch prompt defines a bounded branch scope and falsifiable DoD;
- prompts require manifest-bound branch status validation and manifest-bound main status validation before pass;
- branch prompts are actionable without chat history;
- prompt files do not require branch creation before audit;
- merge/cleanup behavior is explicit when expected;
- `main.prompt.md` requires closing finished branch orchestrators before launching replacements;
- unsupported, unresolved, negative, or probe-only claim labels are not erased by pass/fail language.

## Audit Status

`pass` means orchestration may start. A pass audit must have `can_start=true`, non-empty `checked_files`, non-empty `commands_run`, no critical or major defects, and no missing DoD items. Any missing, ambiguous, or non-actionable contract is `failed` or `blocked`, and main must not create branches.

The auditor returns only JSON matching `prompt-audit.schema.json`. The schema pins the exact absolute `manifest` and `repo_root` values, and downstream branch worktree rendering must reject an audit artifact whose identity does not match the current command inputs.

If both audit model attempts fail without a valid audit artifact, the launcher writes a terminal blocked `prompt-audit.json` with `can_start=false`.
