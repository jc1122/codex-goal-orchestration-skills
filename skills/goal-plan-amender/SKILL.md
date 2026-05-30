---
name: goal-plan-amender
description: "Runtime-only plan amender for audited /goal bundles. Use only when goal-main-orchestrator has validated a terminal branch result and needs amendment proposals for future unstarted manifest work; create file-backed adaptation packets, validate safe manifest amendments, and apply accepted amendments without changing active or terminal branch evidence."
---

# Goal Plan Amender

## Role Boundary

Act only as an amendment proposer and applier for a prepared `/goal` bundle that is already being run by `goal-main-orchestrator`.

Do not launch branches, create worktrees, dispatch workers or reviewers, edit scheduler ledgers, inspect active branch internals, decide prompt-audit status, decide branch pass/fail, or mark the whole run `pass`.

The main orchestrator owns scheduling. The branch orchestrator owns branch execution. This skill may only propose and apply safe changes to future unstarted work in `job.manifest.json` and regenerate prompts for changed future branches.

## Start Conditions

Use this skill only after `goal-main-orchestrator` has:

- completed prompt audit;
- received a terminal branch result;
- validated that branch with `goal-branch-orchestrator/scripts/validate_branch_status.py`;
- closed or otherwise removed that branch from the active set.

Never read active branch worktrees, worker packets, research packets, reviewer packets, event logs, or process state. Active and terminal branch prompt paths, dependencies, owned paths, worktrees, status paths, review paths, and runtime artifacts are immutable.

## Workflow

1. Create an explicit launch decision after main validates the terminal branch result. For deterministic launch/skip cases, main should use the recommender:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/recommend_amendment_decision.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --amendment-id A001 \
  --write-decision
```

For semantic/operator decisions, main may write the decision directly:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/create_amendment_decision.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --amendment-id A001 \
  --decision launch \
  --reason-code remaining_work_dod_gap \
  --reason "Remaining unstarted work no longer covers the main DoD." \
  --terminal-branch B01
```

2. Create an adaptation packet:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/create_adaptation_packet.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --main-prompt /absolute/path/to/main.prompt.md \
  --repo-root /absolute/path/to/repo \
  --amendment-id A001 \
  --amender-route gpt-5.4 \
  --selection-reason "Default recovery-planning route"
```

The packet writes `route.json`, `input-files.json`, `proposal.schema.json`, `proposal.example.json`, `prompt.md`, `launch.sh`, and later `telemetry.json`. If no route is supplied, the script uses `amender_model_policy.default_ladder`.
3. Run `amendments/A001.packet/launch.sh` to write `amendments/A001.proposal.json`. The proposal may use only operations listed in manifest `adaptation_policy.allowed_operations`. The launcher is bounded, read-only, route-bound, and records plan-amender telemetry.
4. Validate the route-bound packet evidence:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/validate_amender_packet.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --amendment-id A001
```

5. Validate the proposal:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/validate_manifest_amendment.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --proposal /absolute/path/to/amendments/A001.proposal.json \
  --output /absolute/path/to/amendments/A001.validation.json \
  --terminal-branch B01
```

6. Apply only when validation says `status: "pass"`:

```bash
python3 "$GOAL_SKILLS_ROOT/goal-plan-amender/scripts/apply_manifest_amendment.py" \
  --manifest /absolute/path/to/job.manifest.json \
  --proposal /absolute/path/to/amendments/A001.proposal.json \
  --validation /absolute/path/to/amendments/A001.validation.json
```

The apply script archives the previous manifest under `amendments/`, writes `amendments/A001.accepted.json`, updates the live manifest, regenerates changed future branch prompts through the preflight helpers, and reruns `lint_goal_bundle.py`.

## Model Routing

Use only aliases allowed by manifest `amender_model_policy.allowed_routes`. The default ladder is `gpt-5.4 -> gpt-5.4-mini`; `gpt-5.5` is allowed for harder recovery planning when main records a concrete selection reason. The selected aliases must preserve policy order and are copied into `route.json` and `telemetry.json`.

## Allowed Amendment Operations

- `add_branch`
- `split_unstarted_branch`
- `replace_unstarted_branch`
- `add_dependency_to_unstarted_branch`
- `add_work_item_to_unstarted_branch`
- `mark_unstarted_branch_obsolete`

Every proposed branch or work item must be worker-sized, path-safe, and compatible with the same preflight lint rules as the original bundle. Recovery branches should cite terminal non-pass evidence with `recovers_from` and must not use `depends_on` to wait for a branch that finished `partial`, `blocked`, or `failed`.

## Fail Closed

If validation fails, leave `job.manifest.json`, branch prompts, scheduler ledgers, and runtime artifacts unchanged. The main orchestrator may continue with any still-eligible existing work. If no work remains, main returns `partial` or `blocked` with the amendment validation defects as evidence.

Read `references/amendment-contract.md` before writing or accepting a proposal.
