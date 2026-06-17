# Goal Skillset — Repo Audit Refactor Optimize (2026-06-17)

**Run id:** `goal-skillset-audit-20260617` · **Branch:** `main` · **Orchestrator:** repo-audit-refactor-optimize v0.12.1

**Scope:** Forced-full diagnosis wave (all six lanes) over the whole goal skillset + supporting
scripts, plus a parallel per-skill correctness review (one subagent per skill).

**DoD:** Forced-full wave → 0 actionable + 0 stale; full `npm run check` green; every genuine
correctness finding either fixed (with a regression test) or triaged with a documented rationale.

## Result

| Signal | Before | After |
|---|---|---|
| Forced-full wave actionable | 31 | **0** |
| Wave stale accept entries | 2 | **0** |
| Wave accepted residuals (documented) | 2279 | 2310 |
| `npm run check` | green | **green** |
| Regression tests | 15 | **22** |

## Stage 0–3 — bootstrap, discovery, diagnosis

All audit leaves present in all three skills roots. `performance` lane `blocked` (no benchmark
surface — structural to a CLI-script repo, not a tooling gap). Forced-full wave (code-health,
security, hygiene, docs, dependency, hotspot) raw counts: code-health 1681, security 194, hotspot
428, dependency 7, docs/hygiene 0 → **31 actionable** after the existing rule-kind acceptances.

## Stage 4 — deterministic remediation (31 → 0)

- **7 real ruff-quality findings** in `scripts/` tooling fixed, behavior-preserving: `UP022`×3
  (`capture_output=True`), `UP035` (`collections.abc.Callable`), `SIM102` (collapsed nested `if`),
  `B904` (`raise … from None`), `B018` (dropped a `run([...]).stdout` useless attribute access —
  the result was already read from a file on the next line). Note the repo gate lints only `F`
  (`select=["F"]`), so these were real but outside the gate's chosen scope.
- **24 bandit `B101` (assert_used)** accepted via two path globs: `tests/**` (pytest assertions)
  and `maintenance/reports/**` (an archived git-worktree snapshot the repo already ruff-excludes).
- **2 stale accept entries pruned** (`is_worktree_dirty`, `clear_git_stdout_cache`) — confirmed
  referenced by `check_preparedness_fixtures.py`, so the dead-code lane no longer flags them →
  `.repo-audit/accept.json` now has 0 stale entries.

## Correctness review — 6 parallel subagents (one per skill)

21 findings surfaced. Highlights: **two were the exact fail-open classes fixed last session but in
siblings that were missed.**

### Fixed (3 unambiguous P1 fail-opens, each with a regression test)

1. **`_goal_shared/.../extract_telemetry.py` — worker `accepted_alias` status-blind.** The worker
   branch keyed acceptance off a 4-item substring-marker list, not `output["status"]`; a blocked/
   failed worker whose blockers fell outside the markers was marked **accepted** in telemetry (the
   same bug fixed for `lite_advisor` last session). Now returns `None` for `status in {blocked,
   failed}`. Synced to all 5 dispatch wrappers.
2. **`goal-branch-orchestrator/.../validate_branch_status.py` — empty `owned_paths` ownership
   bypass.** `validate_worker_changed_files` only flagged out-of-scope changes when `owned_paths`
   was truthy, so a passing worker that declared **no** owned paths validated clean while changing
   arbitrary files. Dropped the `owned_paths and` short-circuit → fails closed, consistent with the
   runner's `worker_ownership_violations`.
3. **`goal-plan-amender/.../create_blocker_repair_packet.py` — duplicate recovery.** `generate_
   proposal` re-proposed an `add_branch` (with `recovers_from`/`supersedes`) for a terminal already
   recovered/superseded by an existing branch (e.g. on a second run), and the apply-operations
   validator does not reject duplicates. Now skips already-recovered terminals (idempotent).

### Triaged — not a bug

- **`assemble_main_status.py:605` `main()` returns 0 on blocked/failed.** By design: `assemble_*`
  is an artifact writer (exit = "assembly succeeded"; the status lives in `main.status.json`, and a
  separate `validate_main_status` / the orchestrator gates on the field). Fixtures
  10017/10064/10172/10255 deliberately assert exit 0 on blocked/invalid/partial assemblies; this
  matches `assemble_branch_status`'s contract.

### Deferred — documented, recommend a deliberate decision

- **P1 `runtime_packet_runner.py` silent git failure → vacuous ownership pass** — real fail-open but
  invasive (`worktree_status_lines` has many tolerant callers) and low-probability; needs a
  git-failure sentinel threaded through the helper chain.
- **P1 `script_only_repair_gate.py`** — `status='pass'`/exit 0 while `launch_allowed=False` when only
  the runtime gate blocks. The blocked signal is authoritatively in the `launch_allowed` field
  (checked by consumers/fixtures), so impact is bounded; changing status/exit risks the artifact
  contract + repair-gate fixtures.
- **P2** non-dict guards (assemble_main_status 104/160/263, create_goal_bundle load_json/_resolve_
  waves, check_goal_config validate_config_shape, create_goal_config load_harness_spec) — currently
  fail-closed crash on malformed JSON, no wrong-success; amendment_lib uniqueness defect + forward-
  only dependency rewrite; promote_worker_repair_evidence branch-scope (degenerate manifest);
  aggregate_review_status secondary signal; scheduler_tick interim status; lint_goal_bundle
  multi-owner masking.

## Stage 5 — verification

- `npm run check` → **GATE_EXIT=0** (ruff lint+format, `sync_goal_shared` no-drift, config/fixtures/
  golden/release/maintenance/context/models, **22 pytest**).
- `ruff check --select UP022,B018,B904,UP035,SIM102 .` → All checks passed.
- `validate_accept` → pass. Forced-full wave (5 runs) → **0 actionable / 0 stale / 2310 accepted**.
- All deterministic lint fixes behavior-preserving; the 3 correctness fixes only alter wrong-result/
  fail-open paths (passing/normal paths unchanged, verified by regression tests + green fixtures).

## Round 2 — fresh-angle review (concurrency, atomicity, path-safety, contract drift, determinism)

A second pass dispatched 6 more per-skill subagents with new angles and an exclusion list of everything already fixed/triaged. It surfaced **21 findings incl. a P0**. Fixed (each verified):

| Sev | Fix |
|---|---|
| **P0** | `render_goal_bootloader.py` — authoritative readiness stopped deferring absent bundle-lint/repair-gate reports (`defer_missing_reports` flag; `False` on the readiness path, `True` only for the pre-lint bootloader render). A never-linted/repair-gated bundle now reports `launch_allowed=false`. End-to-end test. |
| P1 | `assemble_main_status.scheduler_rollup` — pass `allowed_manifest_sha256s` like the validator does, so a post-amendment pass isn't downgraded to `partial` by a spurious manifest-sha blocker. |
| P1 | `lint_goal_bundle._lint_branch_overlap` — stop letting `parallelization.serial_reasons` (a near-ubiquitous scheduling note) waive cross-branch owned-path overlaps; two parallel branches owning the same file are flagged again. |
| P1 | `orchestration_contract.normalize_route_ladder` — order the ladder check against `default_ladder` for set/frozenset inputs instead of `tuple(frozenset)` hash order (deterministic across hash seeds). |
| P1 | `scheduler_tick.close_from_artifacts` — emit `dependency_failed` blocked events for unlaunched items stuck behind a failed/blocked upstream, so `--close-from-artifacts --validate-final` certifies a normal "upstream failed → downstream blocked" run instead of failing un-self-repairably. |
| P2 | `assemble_main_status.aggregate_review_status` — a `reject` verdict rolls up as `blocked`, not `missing`. |

**Triaged / deferred:** `create_goal_config` `--from-discovery` premium-on-mechanical (the surgical fix is a no-op because premium detection is alias-name-based and discovered aliases are generic — needs model-level cost awareness; **the no-op was reverted, not shipped**); plus ~11 P2s (silent git-failure, repair-gate exit semantics, amender uniqueness/forward-dep, ledger append-race, etc.) — see `run_report.json` `round2_batch.deferred_with_rationale`.

Round 2 added 7 regression tests (`tests/test_readiness_gate.py`, `tests/test_round2_fixes.py`, `tests/test_scheduler_dependency_failed.py`, + worker/owned-paths/blocker-repair from round 1). **29 pytest total** (was 15), `npm run check` GREEN, wave **0 actionable / 0 stale / 2327 accepted**.

## Notes

- Local checkout is on `main`; changes are in the working tree (not committed) pending review.
- `package.json` still `0.2.110` (no release cut).
