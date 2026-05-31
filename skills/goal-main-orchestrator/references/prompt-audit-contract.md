# Prompt Audit Contract

The prompt auditor is a read-only heavy-model agent launched before branch creation. The compact `launch.sh` delegates to `runtime_prompt_audit_runner.py`; packet-local `launch-config.json` records the exact `gpt-5.5`, then `gpt-5.4` route, timeout policy, event logs, validator paths, terminal blocked metadata, and telemetry inputs. Do not pass model overrides. The runner writes `telemetry.json` next to `prompt-audit.json` on every terminal path. Lite advisors must not be used before prompt audit to pre-screen or soften prompt defects. After a failed or blocked audit, main may use a Lite `audit-defect-summary` packet only to summarize defects for handoff.

## Files To Check

- `job.manifest.json`
- `main.prompt.md`
- every branch prompt listed in the manifest

## Required Checks

- every listed file exists and is readable;
- manifest branch ids, branch names, worktree paths, status paths, and review paths are present;
- branch prompt paths, status paths, review paths, and worktree paths are unique and collision-free;
- every branch declares 1 to 4 work items, deterministic worker `packet_id` values in `<branch_id>-<work_item_id>` form, `max_active_worker_packets` from 1 to 4, `worker_parallelism.parallelism_default=true`, and `worker_parallelism.scheduling_mode=rolling`;
- `max_active_branch_agents` is present and <= 4;
- manifest artifact and cleanup policies are present, non-empty, and are honored by `main.prompt.md`;
- manifest waves, when present, cover every branch exactly once, no wave has more than 4 branches, and there are no more than 5 waves;
- `parallelization.scheduling_mode` is `rolling`, waves are scheduling/order groups rather than dependency barriers, and `main.prompt.md` requires saturating branch orchestrator slots up to `max_active_branch_agents`;
- branch `depends_on` entries reference only prior branch ids and are the only reason to defer an otherwise eligible branch;
- work-item `depends_on` entries reference only prior work item ids and are the only reason to defer an otherwise eligible worker;
- parallelism is the default and single-branch or reduced-cap plans include a serial reason or parallelization rationale;
- compact prompts are valid when they carry job-specific data and point runtime procedure to `runtime_phase_manifest.py --markdown`, while repeated policy lives in `job.manifest.json` and deterministic scripts;
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

The auditor returns only JSON matching `prompt-audit.schema.json`. The schema pins the exact absolute `manifest` and `repo_root` values. Validate it with `scripts/validate_prompt_audit.py --require-pass` before branch scheduling; downstream branch worktree rendering must reject an audit artifact whose identity does not match the current command inputs.

If both audit model attempts fail without a valid audit artifact, the launcher writes a terminal blocked `prompt-audit.json` with `can_start=false` and telemetry that records the failed declared/called audit attempts. Passing audit telemetry must identify exactly one accepted attempt and preserve prompt/output/log character and byte counts plus any token usage exposed in event logs.
