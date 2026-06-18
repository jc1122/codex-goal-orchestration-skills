# Goal-skillset audit + remediation run report

- Run ID: `2026-06-18-goal-skillset-audit`
- Date: 2026-06-18
- Repo: `codex-goal-orchestration-skills` (goal-config, goal-preflight, goal-main-orchestrator, goal-branch-orchestrator, goal-plan-amender, _goal_shared)
- Scope: full review pass (maintainability, code quality, performance, gaps, bugs, inconsistencies) + repo-audit-refactor-optimize pass with all lanes opted in, executed with subagents, iterated audit→fix→audit to convergence.
- Pipeline stages run: 0 Bootstrap · 1 Discovery · 2 Diagnosis · 3 Synthesis · 4 Execution · 5 Verification · 6 Run report.

## Outcome

CONVERGED. 22 genuine defects fixed across all 6 skills, each with a regression test. Final gate `npm run check` GREEN at **190 tests** (157 baseline → 190, +33). Deterministic diagnosis wave: **0 active findings** after acceptance.

## Stage 2 — Diagnosis (deterministic wave, all lanes)

Lanes: code-health (complexity, dead-code, dependency, docs-consistency, duplication, quality, repo-hygiene, structure), security (bandit), hygiene, docs, dependency, hotspot.

- Raw findings: code-health 1689, security 388, hotspot 362, dependency 7 — **0 active after `.repo-audit/accept.json`**. All raw findings fall in the curated by-design residual categories (E501 long strings, complexity floor, hotspot temporal-coupling, duplication standalone-script idiom, bandit FPs, doc base-path FPs). No dead-code, no structure, no over-acceptance of genuine bugs. The deterministic side was already converged by prior passes; the value of this run was the semantic review.

## Findings + fixes (semantic review, executed with subagents)

Dominant defect class (as in prior passes): **unguarded set/frozenset/dict membership over a `.get()`-tainted, possibly-non-string value** — `x in {set}` hashes the LHS, so a list/dict-valued artifact field raised `TypeError` instead of failing closed. The first detector/sweep had false negatives; this run closed them.

### Round 1 — file-disjoint review subagents (per skill + cross-cutting)
- **goal-main-orchestrator** (one batch): C1 `validate_main_status` review_status (~100); C2 verdict (~940); C3 amendment-path `status` membership (~417/659); C4 `validate_prompt_audit` status (~95); C5 `assemble_main_status` status/review_status (~187/190); C6 `assemble_main_status` manifest_sha256 (~314); C7 `runtime_prompt_audit_runner.failure_summary` event-log-name fallback divergence (~350 vs 474).
- **_goal_shared**: A1 `extract_telemetry.accepted_alias` marked a *blocked* reviewer/research-worker as accepted (fail-open provenance) — now keys off verdict/status `"blocked"`; A2 `check_model_catalog` reported hardcoded config paths instead of the manifest-resolved ones; A3 `check_goal_skill_availability.declared_skill_name` crashed on a non-UTF-8 SKILL.md.
- **goal-branch-orchestrator**: B1 `runtime_packet_runner._review_finalize_no_success` used `parse_messages` before assignment (worker sibling nests it correctly); B2 `render_worker_schedule.validate_work_items` rejected the legacy `worker_type:"research"` alias every sibling accepts (+ hardened the set-membership).
- **goal-preflight**: D1 `create_goal_bundle.assign_package_skeleton_context_files` auto-injected untracked `__init__.py` skeletons that its own bundle linter then rejected as a `major` defect (un-launchable bundle) — now only injects git-tracked skeletons; D2 `_resolve_waves` never checked supplied-wave dependency ordering and omitted `dependency_level` — now both; D3 `_lint_branches` skipped per-item path-safety validation when `work_items` count was out of range; D4 `lint_preflight_brief` emitted spurious policy-default defects when brief normalization failed.
- **goal-plan-amender**: E1 `validate_manifest_amendment.main()` crashed (and wrote no artifact) on a malformed `amendment_id` instead of emitting the failed validation (+ dead `Path.stem` fallback fixed); E3 `amender_model_policy` returned the shared mutable `CONTRACT.AMENDER_MODEL_POLICY`; E4 split duplicate-id guard inspected `defects[-1]` (fragile) — now tracks the defect count.
- **goal-config**: F2 `find_route` consumed a JSON boolean selector as an int route index (bool is an int subclass) — now rejected.
- **cross-cutting**: G2 `aggregate_review_status` left the valid `mergeable_after_fixes` review status falling through to "missing" — now rolled into the non-mergeable set; G3 three inline scheduler enums (`SCHEDULER_EVENT_NAMES`, `SCHEDULER_REASON_EVENTS`, `_event_reason_code` allowed) re-encoded CONTRACT tuples — now derived from the contract (single source).

### Round 2 — AST membership scanner across all skills (closed first-pass false negatives)
- **goal-branch-orchestrator** `validate_branch_status`: verdict (~2230), source_verdict (~2332), review_status (~2724), worker_type (~1460) all unguarded set-membership; `validate_branch_status_header` now normalizes a non-string status to "" so all downstream `status in {…}` cannot crash, and `validate_branch_worker_statuses_shape`/`_trailer` are independently guarded.
- **goal-main-orchestrator** `render_branch_worktree_commands._validate_work_item_fields`: worker_type (~270) unguarded + missing "research" normalization.
- **goal-preflight** `lint_goal_bundle._lite_record_artifacts`: validation_status (~981) unguarded set-membership over an external manifest field.

### Round 3 — verification subagents
- **goal-main-orchestrator** `validate_prompt_audit`: pass-branch defect `severity` membership (~119) unguarded (item dict-guarded but severity not str-guarded).
- **goal-plan-amender** `validate_manifest_amendment`: the Round-1 `except SystemExit` was INCOMPLETE — `ensure_amendment_id("")` raises `ValueError`; now catches `(ValueError, SystemExit)`.

### Round 4 — convergence confirmation
- Fresh adversarial pass over the prompt-audit pipeline + the two Round-3 files: **CONVERGED — no remaining genuine defects.** Every membership/`.get()` site reproduced-or-disproved; remainder verified safe (str-normalized, require_string-detainted, isinstance-guarded, tuple/list RHS, or int-typed).

## Triaged NOT fixed (with rationale)
- **F1 — `normalize_role_model_for_harness` "drops" the provider prefix for codex/opencode-bridge**: REVERTED after `check:config` failed. This is BY DESIGN — implied-provider harnesses carry the provider in the model entry's own `provider` field and emit the bare model id; exercised by the `opencode-deepseek-v4` preset fixture. Agent finding was a false positive on the core behavior.
- **E2 — `replace_dependency` only rewrites branches after the split/replace target**: DEFERRED with rationale. The depends-only-on-prior-branch-ids invariant means a backward dependency is already invalid and fail-closes downstream via preflight normalization; rewriting `replace_dependency` to scan the full list risks altering replacement-branch self-references for marginal benefit.
- Deterministic-wave residual categories: accepted via `.repo-audit/accept.json` (unchanged) — curated by-design floor.

## Stage 5 — Verification (evidence)
- `npm run check` (lint+format, shared-sync, harness-contract, config, fixtures, golden, release, maintenance incl. context-index/size-budget/dependency-policy/model-catalog, tests): **PASS**.
- pytest: **190 passed** (+33 regression tests pinning every fix; per-fix repros confirmed crashes pre-fix).
- Deterministic diagnosis wave re-run on final tree: **0 active findings**.
- AST set-membership scanner across all skills: all genuine sites guarded; remainder verified safe.
- `ruff format --check .`: clean. `maintenance/agent-context-index.json` regenerated after the edits.

## Files changed
14 source files (skills/_goal_shared ×4, goal-branch-orchestrator ×3, goal-config ×1, goal-main-orchestrator ×4, goal-plan-amender ×2, goal-preflight ×3) + 6 regression-test files + the regenerated context index. +747 / −97 lines.
