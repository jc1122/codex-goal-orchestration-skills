# Goal Skillset Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock in the 13 verified review fixes and harden the goal skillset against the recurring bug classes the review exposed (fail-open defaults, malformed-input crashes, stale generated artifacts, advisory-lint drift).

**Architecture:** Five sequential commits on branch `opencode-worker-migration`. First commit lands the already-verified review fixes. Then four hardening commits, each independently green under `npm run check`: (1) clear pre-existing ruff debt, (2) add a pytest regression suite + CI job pinning the bug-class behaviors, (3) a fail-closed creation-time `owned_paths` assertion for worker packets, (4) promote ruff from advisory to a blocking gate + a pre-commit hook that prevents the stale-generated-index class.

**Tech Stack:** Python 3.11 (stdlib + importlib-loaded standalone scripts), pytest 9.0.2, ruff 0.8.6, Node-driven `npm run check` deterministic gate, GitHub Actions, pre-commit.

**Repo:** `/home/jakub/projects/codex-goal-orchestration-skills`. CI = `npm run check` (deterministic gate scripts). Skill scripts are standalone CLIs loaded by path (importlib bootstraps), not an installed package.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `(12 review-fix files)` + `docs/audits/goal-skillset-review-20260617/` | The verified fixes + run report | Commit (Task 0) |
| `scripts/check_golden_smoke.py` | remove 1 unused import (F401) | Modify (Task 1) |
| `scripts/check_goal_config_fixtures.py` | remove 8 unused locals (F841) | Modify (Task 1) |
| `scripts/check_size_budget.py`, `scripts/fixture_support.py`, `scripts/generate_agent_context_index.py`, + 5 more | `ruff format` the 8 drifted harness files | Modify (Task 1) |
| `requirements-dev.txt` | pin `pytest` | Modify (Task 2) |
| `.gitignore` | ignore `.pytest_cache/` | Modify (Task 2) |
| `tests/conftest.py` | importlib loader for standalone skill scripts; suppress bytecode | Create (Task 2) |
| `tests/test_bug_classes.py` | regression tests pinning the fixed bug-class behaviors | Create (Task 2) |
| `package.json` | add `check:tests` + `check:lint`; wire into `check` | Modify (Task 2, Task 4) |
| `.github/workflows/ci.yml` | add a `unit-tests` job | Modify (Task 2) |
| `skills/goal-branch-orchestrator/scripts/create_runtime_packet.py` | fail-closed if a worker packet resolves to empty owned paths | Modify (Task 3) |
| `tests/test_owned_paths_guard.py` | test the creation-time guard | Create (Task 3) |
| `.pre-commit-config.yaml` | ruff + ruff-format + context-index-freshness local hooks | Create (Task 4) |
| `README.md` (or `AGENTS.md`) | document `pre-commit install` + the new gate | Modify (Task 4) |

**Branch note:** all work stays on `opencode-worker-migration`. Do NOT touch `main`.

---

### Task 0: Land the verified review fixes (commit 1)

**Files:**
- Modify (already in working tree): the 12 review-fix files + `docs/audits/goal-skillset-review-20260617/`

- [ ] **Step 1: Confirm the gate is green right now**

Run: `npm run check`
Expected: final line ends with a `status=pass`-style summary, exit 0.

- [ ] **Step 2: Confirm scope is exactly the review fixes + report**

Run: `git status --porcelain`
Expected: 12 ` M` skill/index files + `?? docs/audits/goal-skillset-review-20260617/` (and this plan file under `docs/superpowers/plans/`). No other modifications.

- [ ] **Step 3: Stage and commit**

```bash
git add maintenance/agent-context-index.json skills/ docs/audits/goal-skillset-review-20260617/
git commit -m "fix(skills): correctness review remediation (13 findings) + run report

Review-driven audit of the goal skillset (3 rounds, parallel reviewers).
Fixes 13 P1/P2 correctness bugs the deterministic wave cannot see:
- extract_telemetry: blocked lite_advisor mislabeled accepted (real bridge-fail/capacity blocker text)
- create_goal_config: doubled provider prefix; guarded non-dict effort
- run_prompt_audit_phase: default-mode exit-0-on-blocked -> fail closed
- assemble_branch_status: silent git-failure -> blocker; assemble_main_status active_ids filter
- runtime_packet_runner: worker ownership bypass on empty owned_files -> fail closed
- promote_worker_repair_evidence: empty owned -> fail closed
- non-dict guards (telemetry/effort/remediation/branch-id); validate_worker_ladder ValueError guard
Also regenerates stale agent-context-index.json (gate was red at tip).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4: Verify commit is clean**

Run: `git status --porcelain | grep -vE 'docs/superpowers/plans/' ; echo "exit=$?"`
Expected: no skill/index files remain uncommitted.

---

### Task 1: Clear pre-existing ruff debt (commit 2)

**Why:** Task 4 promotes ruff to a blocking gate. The repo currently has 9 `F` errors and 8 format-drifted files, all in `scripts/` (the CI harness, not `skills/`). They must be clean before gating.

**Files:**
- Modify: `scripts/check_golden_smoke.py` (F401), `scripts/check_goal_config_fixtures.py` (8× F841), + 8 format-drifted `scripts/*.py`

- [ ] **Step 1: See the exact lint debt**

Run: `ruff check . --output-format=concise`
Expected: 9 errors — `scripts/check_golden_smoke.py:23 F401` and 8× `scripts/check_goal_config_fixtures.py F841`.

- [ ] **Step 2: Auto-fix the safe F401**

Run: `ruff check scripts/check_golden_smoke.py --fix`
Expected: removes the unused `fixture_support.assert_lean_codex_attempts` import. Re-run `ruff check scripts/check_golden_smoke.py` → "All checks passed".

- [ ] **Step 3: Manually remove the 8 F841 unused locals**

For each `scripts/check_goal_config_fixtures.py` line flagged (`discovery_reuse_report_path`, `discover_list_script`, `normal_cache_count_path`, `baseline_smoke_count_path`, `discover_count_path`, `profile_discover_count_path`, `discover_db_path`, `baseline_smoke_db_path`): read the assignment. If the RHS is a pure path expression (no side effect), delete the whole assignment line. If the RHS is a function call with a side effect, keep the call but drop the binding (`foo()` instead of `x = foo()`).
Verify each is dead first:

```bash
for v in discovery_reuse_report_path discover_list_script normal_cache_count_path baseline_smoke_count_path discover_count_path profile_discover_count_path discover_db_path baseline_smoke_db_path; do echo "== $v =="; grep -n "$v" scripts/check_goal_config_fixtures.py; done
```
Expected: each name appears on exactly ONE line (its assignment) → safe to delete that line.

- [ ] **Step 4: Format the 8 drifted harness files**

Run: `ruff format scripts/`
Expected: "N files reformatted" (the 8 drifted ones). This is format-only; no logic change.

- [ ] **Step 5: Verify lint + format + gate all clean**

Run: `ruff check . && ruff format --check . && npm run check`
Expected: `ruff check .` → "All checks passed!"; `ruff format --check .` → "NN files already formatted"; `npm run check` → pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/
git commit -m "style(scripts): clear pre-existing ruff debt (9 F-errors + 8 format-drift)

Harness-only cleanup ahead of promoting ruff to a blocking gate.
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pytest regression suite + CI job (commit 3)

**Why:** All 13 bugs lived in logic the deterministic gate-scripts/fixtures never exercised (error paths, malformed input, edge cases). A unit suite over these pure functions pins the behaviors so they cannot silently regress.

**Files:**
- Modify: `requirements-dev.txt`, `.gitignore`, `package.json`
- Create: `tests/conftest.py`, `tests/test_bug_classes.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add the importlib loader (conftest)**

Create `tests/conftest.py`:

```python
"""Pytest support: load standalone skill scripts by path.

The goal skills are standalone CLIs (not an installed package); they bootstrap
their own siblings via importlib at import time. We load them the same way and
suppress bytecode so test runs never pollute skills/ with __pycache__ (CI asserts
no bytecode under skills/bin/scripts).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]


def load_module(relpath: str, name: str | None = None):
    path = REPO / relpath
    modname = name or f"goalskill_{path.stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module
```

- [ ] **Step 2: Write the failing regression suite**

Create `tests/test_bug_classes.py`:

```python
"""Regression tests pinning the behaviors fixed in the 2026-06-17 review.

Each test would FAIL on the pre-fix code; verify red-green per the plan.
"""

import pytest

from conftest import load_module

et = load_module("skills/_goal_shared/scripts/extract_telemetry.py")
cg = load_module("skills/goal-config/scripts/create_goal_config.py")
rpr = load_module("skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py")


# --- extract_telemetry: a blocked lite_advisor must never be marked accepted ---
@pytest.mark.parametrize(
    "blocker",
    [
        "Lite advisor bridge delegate failed. Inspect the bridge run-dir artifacts "
        "for transport, model, permission, or validation errors.",
        "bridge pool capacity limit reached; scheduler should refill later",
        "Lite advisor did not produce valid advice JSON.",
    ],
)
def test_blocked_lite_never_accepted(blocker):
    attempts = [{"alias": "ds-flash-max", "called": True}]
    out = {"status": "blocked", "blockers": [blocker]}
    assert et.accepted_alias("lite_advisor", out, attempts) is None


def test_passing_lite_is_accepted():
    attempts = [{"alias": "ds-flash-max", "called": True}]
    out = {"status": "pass", "blockers": []}
    assert et.accepted_alias("lite_advisor", out, attempts) == "ds-flash-max"


# --- create_goal_config: no doubled provider prefix on a non-implied harness ---
def test_qualified_model_keeps_listed_provider():
    assert cg.normalize_role_model_for_harness("anthropic/claude", "generic-cli", "openai") == (
        "anthropic",
        "anthropic/claude",
    )


def test_bare_model_gets_default_provider_prefix():
    assert cg.normalize_role_model_for_harness("gpt-5", "generic-cli", "openai") == ("openai", "openai/gpt-5")


def test_implied_harness_returns_bare_model():
    # opencode-bridge is an implied-provider harness -> bare model suffix
    assert cg.normalize_role_model_for_harness("deepseek/deepseek-v4-flash", "opencode-bridge", None) == (
        "deepseek",
        "deepseek-v4-flash",
    )


# --- runtime_packet_runner: worker ownership fail-closed on empty owned_files ---
def test_worker_empty_owned_flags_all_changes():
    changed = ["a/b.py", "c/d.py"]
    assert rpr.worker_ownership_violations({"role": "worker"}, changed) == changed


def test_reviewer_empty_owned_flags_nothing():
    assert rpr.worker_ownership_violations({"role": "reviewer"}, ["a/b.py"]) == []


def test_worker_with_owned_flags_only_unowned():
    assert rpr.worker_ownership_violations(
        {"role": "worker", "owned_files": ["a/"]}, ["a/b.py", "c/d.py"]
    ) == ["c/d.py"]
```

- [ ] **Step 3: Run the suite — expect PASS on current (fixed) code**

Run: `python3 -B -m pytest tests/test_bug_classes.py -v`
Expected: all tests PASS (the fixes are already in the tree).

- [ ] **Step 4: Prove the tests are real (red-green on one bug)**

```bash
git stash --include-untracked -- skills/_goal_shared/scripts/extract_telemetry.py 2>/dev/null || true
# temporarily restore the OLD substring guard to prove the test catches it:
python3 -B -m pytest tests/test_bug_classes.py -k blocked_lite -v
```
If stash is impractical, instead manually revert the `extract_telemetry.py` lite block to `if "command failed" in blocker_text ...: return None` and run `pytest -k blocked_lite` → expected FAIL on the bridge-fail/capacity cases. Then restore the fix and re-run → PASS. Document the observed red→green.

- [ ] **Step 5: Pin pytest + ignore its cache**

Edit `requirements-dev.txt` to:

```
ruff==0.8.6
pytest==9.0.2
```

Edit `.gitignore`, add a line:

```
.pytest_cache/
```

- [ ] **Step 6: Wire tests into `npm run check`**

In `package.json`, add to `scripts`:

```json
"check:tests": "python3 -B -m pytest tests -q"
```

And append `&& npm run check:tests` to the existing `check` script value (after `check:maintenance`).

- [ ] **Step 7: Run the full gate**

Run: `npm run check`
Expected: pass, now including `check:tests` (e.g. `N passed`).

- [ ] **Step 8: Add a CI job**

In `.github/workflows/ci.yml`, add a third job (sibling of `deterministic-gate` / `maintenance-report`):

```yaml
  unit-tests:
    name: Unit Tests
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dev tools
        run: python3 -m pip install -r requirements-dev.txt
      - name: Run unit tests
        run: python3 -B -m pytest tests -q
      - name: Assert no bytecode under skills/bin/scripts
        run: |
          if find skills bin scripts -type d -name __pycache__ -print -quit | grep -q .; then
            find skills bin scripts -type d -name __pycache__ -print
            exit 1
          fi
```

- [ ] **Step 9: Confirm tests leave no bytecode under skills/**

Run: `python3 -B -m pytest tests -q && find skills bin scripts -type d -name __pycache__ -print | head`
Expected: tests pass; no `__pycache__` printed.

- [ ] **Step 10: Commit**

```bash
git add tests/ requirements-dev.txt .gitignore package.json .github/workflows/ci.yml
git commit -m "test(skills): add pytest regression suite for review bug-classes + CI job

Pins telemetry accepted/blocked, provider-prefix, and worker-ownership behaviors.
Wired into npm run check (check:tests) and a new unit-tests CI job.
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Fail-closed `owned_paths` at worker-packet creation (commit 4)

**Why:** Worker work-items are *contractually* required to declare ≥1 `owned_path` (manifest lint `lint_goal_bundle.py:1948-1958`), and the runtime now fails closed on empty ownership (review fix #12). This adds the missing creation-time assertion so a malformed worker packet is rejected up front instead of relying on the downstream gate — and proves reviewer/research packets are unaffected.

**Files:**
- Create: `tests/test_owned_paths_guard.py`
- Modify: `skills/goal-branch-orchestrator/scripts/create_runtime_packet.py` (`compact_worker_context`, around line 1265)

- [ ] **Step 1: Confirm the resolution point and that it is worker-specific**

Run: `sed -n '1247,1266p' skills/goal-branch-orchestrator/scripts/create_runtime_packet.py`
Expected: `def compact_worker_context(...)` with `work_owned_paths = compact_list(work_item.get("owned_paths")) or owned_files` at ~1265. Then confirm the function is only called on the worker path:
Run: `grep -n "compact_worker_context" skills/goal-branch-orchestrator/scripts/create_runtime_packet.py`
Expected: a single call site, in the worker-packet builder (not reviewer/research). If it is NOT worker-only, move the guard in Step 3 to a `role == "worker"`-gated location instead and adjust the test.

- [ ] **Step 2: Write the failing test**

Create `tests/test_owned_paths_guard.py`:

```python
"""A worker packet that resolves to no owned paths must be rejected at creation."""

import pytest

from conftest import load_module

crp = load_module("skills/goal-branch-orchestrator/scripts/create_runtime_packet.py", "crp_owned")


def _found(work_item):
    # (manifest_path, manifest, branch_data, work_item) shape returned by find_manifest_context
    from pathlib import Path

    return (Path("job.manifest.json"), {}, {"id": "B01"}, work_item)


def test_worker_no_owned_paths_rejected(monkeypatch):
    monkeypatch.setattr(crp, "find_manifest_context", lambda *a, **k: _found({}))
    with pytest.raises(SystemExit) as exc:
        crp.compact_worker_context(
            branch_id="B01",
            packet_id="B01-W01",
            task_file=None,
            task_text="Objective\nScope\nStop Conditions",
            owned_files=[],
            context_files=["job.manifest.json"],
        )
    assert "owned" in str(exc.value).lower()


def test_worker_with_owned_paths_ok(monkeypatch):
    monkeypatch.setattr(
        crp, "find_manifest_context", lambda *a, **k: _found({"owned_paths": ["src/x.py"]})
    )
    result = crp.compact_worker_context(
        branch_id="B01",
        packet_id="B01-W01",
        task_file=None,
        task_text="Objective\nScope\nStop Conditions",
        owned_files=[],
        context_files=["job.manifest.json"],
    )
    assert result is not None
```

- [ ] **Step 3: Run the test — expect FAIL (no guard yet)**

Run: `python3 -B -m pytest tests/test_owned_paths_guard.py -v`
Expected: `test_worker_no_owned_paths_rejected` FAILS (no SystemExit raised). If `test_worker_with_owned_paths_ok` errors on unrelated missing fields, simplify it to assert the call does not raise SystemExit, or adjust the `work_item` to include the minimum fields `compact_worker_context` reads (objective/scope/stop already supplied via `task_text`).

- [ ] **Step 4: Add the guard**

In `skills/goal-branch-orchestrator/scripts/create_runtime_packet.py`, immediately after the `work_owned_paths = ...` line (~1265):

```python
    work_owned_paths = compact_list(work_item.get("owned_paths")) or owned_files
    if not work_owned_paths:
        raise SystemExit(
            f"worker packet {packet_id} (branch {branch_id}) declares no owned_paths; "
            "a worker must own at least one path"
        )
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `python3 -B -m pytest tests/test_owned_paths_guard.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Full gate (ensure no fixture/worker flow broke)**

Run: `npm run check`
Expected: pass — in particular `check:fixtures` (worker preparedness fixtures) stays green, proving legitimate worker packets still build.

- [ ] **Step 7: Commit**

```bash
git add skills/goal-branch-orchestrator/scripts/create_runtime_packet.py tests/test_owned_paths_guard.py
git commit -m "fix(branch): fail closed when a worker packet resolves to no owned_paths

Defense-in-depth at creation, mirroring the runtime ownership fix.
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Promote ruff to a gate + pre-commit hook (commit 5)

**Why:** ruff is advisory today (`continue-on-error`), which is how 9 errors + format-drift accumulated; and the gate was once red from a stale generated index. Make lint/format blocking, and add a pre-commit hook that regenerates/validates the context index so "green" cannot be faked locally.

**Files:**
- Modify: `package.json` (add `check:lint`, wire into `check`)
- Create: `.pre-commit-config.yaml`
- Modify: `README.md` (document `pre-commit install`)

- [ ] **Step 1: Add a blocking lint script**

In `package.json` `scripts`, add:

```json
"check:lint": "ruff check . && ruff format --check ."
```

Append `&& npm run check:lint` to the `check` script value (place it FIRST in the chain so style fails fast; i.e. `"check": "npm run check:lint && npm run check:shared && ... && npm run check:tests"`).

- [ ] **Step 2: Verify the gate now blocks on lint**

Run: `npm run check`
Expected: pass (Task 1 cleared all debt). Then prove it bites: append an unused import to any `skills/**/*.py`, run `npm run check` → expected FAIL at `check:lint`; revert the planted import; re-run → pass.

- [ ] **Step 3: Add the pre-commit config**

Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.6
    hooks:
      - id: ruff
      - id: ruff-format
        args: [--check]
  - repo: local
    hooks:
      - id: context-index-fresh
        name: agent-context-index is fresh
        entry: python3 scripts/generate_agent_context_index.py --check
        language: system
        pass_filenames: false
        always_run: true
```

- [ ] **Step 4: Verify the hooks run**

Run: `pre-commit run --all-files` (if `pre-commit` is unavailable, run the equivalents: `ruff check . && ruff format --check . && python3 scripts/generate_agent_context_index.py --check`)
Expected: all hooks pass. Then prove the index hook bites: edit any fingerprinted source (e.g. add a comment to `AGENTS.md`), run the hook → expected FAIL ("Run: npm run generate:context"); revert; re-run → pass.

- [ ] **Step 5: Document it**

In `README.md`, under the contributor/dev section, add a short block:

```markdown
## Local checks

- `npm run check` runs the full deterministic gate (lint, format, fixtures, tests).
- Enable pre-commit hooks once: `pip install pre-commit && pre-commit install`.
  Hooks run ruff + ruff-format and assert `maintenance/agent-context-index.json`
  is regenerated (`npm run generate:context`) before every commit.
```

- [ ] **Step 6: Final full gate**

Run: `npm run check && python3 -B -m pytest tests -q`
Expected: both pass.

- [ ] **Step 7: Commit**

```bash
git add package.json .pre-commit-config.yaml README.md
git commit -m "ci: promote ruff to a blocking gate + pre-commit hooks

ruff check/format now run inside npm run check; pre-commit asserts
context-index freshness so a stale generated artifact can't pass locally.
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** (1) clean 9 ruff errors → Task 1 ✓; (2) pytest suite + CI job → Task 2 ✓; (3) creation-time `owned_paths` assertion → Task 3 ✓; (4) ruff-as-gate + pre-commit hook → Task 4 ✓; commit-fixes-first → Task 0 ✓.

**Placeholder scan:** every code/test step contains complete code; every command has an expected result. Task 3 Step 1 includes a branch-condition (if `compact_worker_context` is not worker-only, relocate the guard) — this is a verification gate, not a placeholder.

**Type consistency:** `load_module(relpath, name)` defined in `conftest.py` and used consistently in both test files. `worker_ownership_violations(config, changed_files)`, `accepted_alias(role, output, attempts)`, `normalize_role_model_for_harness(provider_model, harness, default_provider)`, `compact_worker_context(*, branch_id, packet_id, task_file, task_text, owned_files, context_files)` all match the real signatures verified in the codebase.

**Risk notes for the executor:**
- Task 2 Step 4 (red-green) must show an actual failure on reverted code — do not skip.
- Task 3: if `compact_worker_context` turns out to be reachable by non-worker roles, gate the new `raise` behind the worker role (and update the test). The full gate (`check:fixtures`) is the safety net.
- Keep each commit independently `npm run check`-green.
