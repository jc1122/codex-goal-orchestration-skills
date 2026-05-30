# Agent Maintenance Guide

Use deterministic reports before proposing changes. Agents may summarize, triage, and prepare pull requests, but the scripts in this repository are the source of truth for drift.

## Required Local Checks

```bash
npm run check
npm run check:maintenance
```

Use the focused checks when investigating a narrow issue:

```bash
npm run check:maintenance:size
npm run check:maintenance:deps
python3 scripts/check_size_budget.py --json
python3 scripts/check_dependency_policy.py --json
```

Optional lint/type reports require the pinned dev tools:

```bash
npm ci
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
npm run check:quality
```

## Size Budget Rules

- The size budget uses `git ls-files`; ignored local caches and untracked scratch files do not count.
- `approx_tokens` is `chars / 4`, intended for planning and context budgeting only.
- Growth is warning-only for now. Do not update `maintenance/size-budget.json` just to make a warning disappear.
- Prefer reducing duplication, moving repeated policy into shared references, or enforcing behavior in validators before adding more prose.
- Refresh the budget only when growth is intentional:

```bash
python3 scripts/check_size_budget.py --update
```

## Dependency Rules

- Runtime npm dependencies are forbidden unless explicitly added to `maintenance/dependency-policy.json`.
- Development tools belong in `devDependencies` or `requirements-dev.txt`.
- Dependabot must cover every dependency manifest listed in the policy.
- Dependency update PRs should run the full deterministic check suite before merge.

## Agent Workflow

1. Run the maintenance checks and read the JSON output.
2. Identify the largest files and highest-severity warnings.
3. Prefer targeted cleanup or consolidation over broad rewrites.
4. Open changes on a branch or PR; never edit `main` directly.
5. Include whether `maintenance/size-budget.json` was intentionally refreshed.
