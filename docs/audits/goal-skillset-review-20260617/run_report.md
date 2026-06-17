# Run report — goal skillset review + audit-package remediation

- **Repo:** `codex-goal-orchestration-skills`  **Branch:** `opencode-worker-migration`
- **Run id:** `goal-skillset-review-20260617`  **Window:** 2026-06-17
- **Orchestrator:** repo-audit-refactor-optimize v0.12.1, driven via Opus 4.8 subagents (11 review agents across 3 rounds)
- **Scope:** the goal skillset — `skills/{_goal_shared, goal-preflight, goal-config, goal-main-orchestrator, goal-branch-orchestrator, goal-plan-amender}` (~48k LOC across 108 scripts)
- **Definition of Done (user):** *no more actionable issues found by reviewer* — a review-driven loop, stronger than deterministic-wave convergence.

## Outcome
- **DoD reached.** Round 3's two dedicated reviewers both returned CLEAN verdicts; the sole parenthetical note was traced and determined benign.
- **Deterministic side:** forced-full wave (all 6 lanes, `--source-prefix skills`) = **0 actionable** (1910 documented residuals in `.repo-audit/accept.json`).
- **Reviewer side:** **13 real correctness findings fixed** + **1 gate-integrity fix**. All P1/P2; no P0.
- `npm run check` (full deterministic gate) **green**; my changes introduced **zero** new lint errors.

## What the deterministic wave alone would have missed
The branch arrived "converged" (a prior run-report committed today claimed *"npm run check pass"* and *"forced-full wave actionable = 0"*). Yet on a clean tree the gate was **red**: the run-report commit (`3b0ad98`) changed a fingerprinted file without regenerating `maintenance/agent-context-index.json`. **Gate fix #0** regenerated it. This is exactly why a review-driven DoD beats a linter-only one.

## Findings fixed

### Round 1 — 6 parallel reviewers (one per subsystem)
| Sev | File | Issue |
|-----|------|-------|
| P1 | `_goal_shared/extract_telemetry.py` | `blocked` lite_advisor mislabeled **accepted** — substring guard missed the real `"...bridge delegate failed..."` / `"...capacity limit reached..."` blockers (propagated to all 5 skills) |
| P2 | `goal-preflight/prepare_goal_bundle.py` | unguarded `.get()` on non-dict `remediation` → crash |
| P2 | `goal-config/check_goal_config.py` | non-dict `telemetry` → uncaught `AttributeError` |
| P2 | `goal-config/check_goal_config.py` | non-dict `effort` → uncaught `AttributeError` |
| P2 | `goal-config/create_goal_config.py` | doubled provider prefix `openai/anthropic/claude` (non-implied harness + qualified model) |
| P2 | `goal-main-orchestrator/run_prompt_audit_phase.py` | default-mode prompt audit **exits 0 on `blocked`** while the sibling reuse path was already hardened |
| P2 | `goal-main-orchestrator/assemble_main_status.py` | `active_branch_ids` unfiltered vs validator's non-empty-string rule → self-reject |
| P2 | `goal-branch-orchestrator/validate_branch_status.py` | latent `ValueError` in `validate_worker_ladder` |
| P2 | `goal-branch-orchestrator/promote_worker_repair_evidence.py` | empty `owned_paths` ⇒ "owns everything" (fail-open) |
| P2 | `goal-plan-amender/amendment_lib.py` | unguarded `branch["id"]` → crash on malformed manifest |

### Round 2 — diff-verifier + 2 fresh re-reviewers
| Sev | File | Issue |
|-----|------|-------|
| P2 | `goal-config/create_goal_config.py` | Round-1 #4 **incomplete**: same unguarded `config.get("effort", {})` at `build_model_policies` (2 sites) → guarded `effort_cfg` local |
| P2 | `goal-branch-orchestrator/assemble_branch_status.py` | `changed_files_from_git` silently returned `[]` on git failure → now records a blocker (fail-closed, matches siblings) |
| P2 | `goal-branch-orchestrator/runtime_packet_runner.py` | `worker_ownership_violations` bypassed when `owned_files` empty → fail-closed for `role=="worker"` (other roles unchanged) |

### Round 3 — convergence
`ROUND3-VERIFY: CLEAN` + `ROUND3-CORE: CLEAN`. A dedicated re-scan of the recurring **fail-open class** (empty-collection-permissive gates; silent git/subprocess swallows; validators passing on error paths) found every instance already fixed or independently guarded. The `extract_changed_files` swallow noted in passing was traced to a best-effort evidence/fingerprint helper that is downstream-compensated by the authoritative pre-review gate — **not actionable**.

## Verification (fresh)
- `python3 -m py_compile` all skill scripts — **pass**
- `npm run check` (shared, config, fixtures, golden, release, maintenance) — **pass**
- `ruff format --check` on changed files — **all formatted**; `git diff --check` — **clean**
- Forced-full wave (`code-health,security,hygiene,docs,dependency,hotspot`, scoped to `skills/`) — **0 actionable**
- Targeted behavior smokes (telemetry-not-accepted, no-doubled-prefix, worker-empty-owned-fail-closed) — **pass**; worker preparedness fixtures confirm the ownership fix did not break legitimate worker flows.

## Notes / out of scope
- **Pre-existing, not fixed:** 9 `ruff` F-errors in `scripts/check_goal_config_fixtures.py` and `scripts/check_golden_smoke.py` — the repo's CI harness, **outside the reviewed `skills/` surface**, identical on clean HEAD, and CI's ruff step is advisory. Flagged for the maintainer.
- **No commit made.** Changes are in the working tree on `opencode-worker-migration` pending the user's decision (12 files; +55/−31).
- The 1910 accept.json residuals were spot-checked as legitimate by-design/idiom-floor/FP residuals, not concealed fixable bugs.
