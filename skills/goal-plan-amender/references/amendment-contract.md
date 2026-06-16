# Amendment Contract

`goal-plan-amender` adapts a live `/goal` manifest by proposal, validation, then apply. It is launched by `goal-main-orchestrator` only after terminal branch results are validated.

## Artifacts

For amendment id `A001`, artifacts live under the bundle `amendments/` directory:

- `A001.decision.json`: main-owned deterministic launch or skip decision recorded after terminal branch validation.
- `A001.packet/`: input manifest, selected prompts/status/review summaries, route, task text, proposal schema/example, launch script, and telemetry.
- `A001.packet/packet.validation.json`: deterministic packet validation proving the decision, route, proposal, and telemetry are consistent before proposal validation/apply.
- `A001.proposal.json`: proposed operations.
- `A001.validation.json`: deterministic validation result.
- `A001.accepted.json`: immutable acceptance record written by the apply script.
- `A001.job.manifest.before.json`: archived copy of the prior live manifest.

`goal-main-orchestrator` must record either a launch or skip decision for each terminal branch checkpoint. Use `scripts/recommend_amendment_decision.py` for deterministic cases such as eligible work remaining, all-pass/no-adaptation, no eligible branch, or non-pass dependencies stalling downstream work; use `scripts/create_amendment_decision.py` for semantic/operator decisions. Launch decisions are valid only for enumerated launch reasons such as `no_eligible_branch`, `blocker_stalls_downstream`, `remaining_work_dod_gap`, `recovery_plausible_before_finalization`, `terminal_blocker_repair`, or `operator_requested`.

When a terminal branch is blocked by missing local files, the main orchestrator may create a deterministic blocker-repair packet with `scripts/create_blocker_repair_packet.py`. That packet uses local status-artifact parsing rather than a model route, writes deterministic telemetry with alias `deterministic-blocker-repair`, and proposes new repair branches. It still applies through the same proposal validation and apply gates.

## Proposal Shape

```json
{
  "schema_version": 1,
  "amendment_id": "A001",
  "job_id": "example-job",
  "rationale": "Terminal branch evidence shows more future work is needed.",
  "operations": [
    {
      "op": "add_branch",
      "branch": {
        "id": "B02",
        "title": "Recovery Follow-up",
        "objective": "Implement a bounded recovery follow-up.",
        "scope": "Only future work; do not edit terminal branch artifacts.",
        "branch_name": "example-job-b02",
        "worktree_path": ".worktrees/example-job-b02",
        "depends_on": [],
        "recovers_from": ["B01"],
        "max_active_worker_packets": 1,
        "worker_serial_reasons": ["Single recovery packet."],
        "work_items": [
          {
            "id": "W01",
            "objective": "Bounded worker objective.",
            "owned_paths": ["src/example.py"],
            "context_files": ["README.md"],
            "verification": ["python3 -m pytest tests/test_example.py -q"],
            "dod": ["Focused verification passes."]
          }
        ],
        "tests": ["python3 -m pytest tests/test_example.py -q"],
        "dod": ["Recovery follow-up is validated."]
      }
    }
  ]
}
```

## Model Routing

Main selects an ordered amender model ladder from manifest `amender_model_policy.allowed_routes`. The packet records that choice in `A001.packet/route.json`, launches bounded read-only attempts, and writes `A001.packet/telemetry.json`. The default ladder is the bridge deepseek routes `ds-pro-max -> ds-flash-max`; bridge routes are delegated read-only through the opencode-worker-bridge `opencode_worker.py`, while any native Codex route runs `codex exec --ephemeral -s read-only`.

`create_adaptation_packet.py` requires a matching `A001.decision.json`; the packet's active and terminal branch ids must match that decision. `validate_amender_packet.py` writes `A001.packet/packet.validation.json` and fails if the decision, route, input-file hashes, proposal envelope, or telemetry aliases drift.

## Immutability

Active and terminal branch ids are protected. A valid proposal must not replace, split, obsolete, add dependencies to, add work items to, or otherwise modify those branches. Applying an accepted proposal must leave their prompts, status paths, review paths, worktrees, dependencies, owned paths, and runtime artifacts untouched.

Blocker repair is modeled as new future work, not mutation of terminal branches. A repair branch must use `recovers_from` to cite the terminal branch evidence. If it needs to touch a path that overlaps prior protected ownership, it must declare a concrete `contention_reason`; the protected branch artifact itself remains immutable.

## Validation

Validation must reject:

- unknown operations;
- missing or unsafe amendment ids, branch ids, branch names, worktree paths, prompt/status/review paths, owned paths, and context paths;
- duplicate branch ids, branch names, worktree paths, status paths, review paths, prompt paths, or work item ids;
- more than five waves or more than four branches per wave;
- more than 20 manifest branches;
- branches with more than four work items;
- invalid dependency ordering;
- changes to active or terminal branch ids;
- owned-path overlap without an explicit dependency or contention reason;
- missing `serial_reasons` when topology underfills branch or worker capacity;
- candidate manifests that fail `goal-preflight/scripts/lint_goal_bundle.py`.

## Apply

Apply is atomic at the manifest level: archive the prior manifest, write the amended manifest, regenerate changed future branch prompts, run lint, then write `A001.accepted.json`. If lint fails after writing, restore the archived manifest and leave a failed validation/apply record.
