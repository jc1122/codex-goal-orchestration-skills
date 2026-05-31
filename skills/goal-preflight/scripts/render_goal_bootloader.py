#!/usr/bin/env python3
"""Print or regenerate the final /goal bootloader text from a preflight bundle."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
resolve_absolute_path = PATH_RULES.resolve_absolute_path
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS


def render_bootloader(bundle_dir: Path, repo_root: Path) -> str:
    manifest = bundle_dir / "job.manifest.json"
    main_prompt = bundle_dir / "main.prompt.md"
    model_catalog = bundle_dir / "model-catalog.json"
    return f"""Use $goal-main-orchestrator with the generated bundle context below.

Prepared bundle:
- Bundle root: {bundle_dir}
- Repository root: {repo_root}
- Manifest: {manifest}
- Main prompt: {main_prompt}

Read `job.manifest.json` and `main.prompt.md` first, then run in order:

```bash
if [ -d "${{CODEX_HOME:-$HOME/.codex}}/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="${{CODEX_HOME:-$HOME/.codex}}/skills"
elif [ -d "$HOME/.agents/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
else
  echo "missing installed skill root for goal-main-orchestrator (checked $CODEX_HOME/.codex and $HOME/.agents/skills)" >&2
  exit 1
fi

python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/runtime_phase_manifest.py --markdown
python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_goal_skill_availability.py --skills-root $GOAL_SKILLS_ROOT --require goal-main-orchestrator --require goal-branch-orchestrator --require goal-plan-amender
python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py --json --require-codex > {model_catalog}
python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/run_prompt_audit_phase.py --manifest {manifest} --repo-root {repo_root} --audit-dir {bundle_dir}/audit --deterministic --require-pass
```

Treat script output, JSON artifacts, and validator defects as the working surface; do not read skill Python source unless debugging a failed script.
Use absolute paths only for goal scripts; regenerate this bootloader if bundle or repo paths change.

Mandatory skill availability bootstrap:
1. `runtime_phase_manifest.py --markdown`
2. `check_goal_skill_availability.py --require goal-main-orchestrator --require goal-branch-orchestrator --require goal-plan-amender`
3. `check_model_catalog.py --json --require-codex`

Do not start branches unless prompt-audit says `status=pass`, `can_start=true`, and it pins the manifest and repository above.

Respect max_active_branch_agents from job.manifest.json; never exceed {MAX_ACTIVE_BRANCH_AGENTS}. Keep branch slots saturated, record scheduler-v2 evidence under `schedulers/`, and avoid polling active branch, worker, reviewer, process, or status artifacts.

Parallelism is the default. Keep branch orchestrator slots saturated and use a rolling saturated pool. Defer only unresolved `depends_on` entries. Waves are scheduling/order groups, not barriers. Branches may each declare 1 to 4 worker packets in-band.

Finish only when the Definition of Done in `main.prompt.md` is satisfied and all evidence is present:
- Branch `status` and `review` files required by DoD are complete.
- Packet telemetry and `telemetry.summary.json` are present and coherent.
- Command evidence (stdout/stderr + exit code) exists for `run_prompt_audit_phase.py --deterministic --require-pass` and final `validate_main_status.py`.
- Final `python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/validate_main_status.py` succeeds.
- Git state is clean/explicit in status-review artifacts.
Otherwise return blocked/partial.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--repo-root", help="Regenerate bootloader text with this repository root before printing.")
    parser.add_argument("--write", action="store_true", help="With --repo-root, rewrite goal-bootloader.md before printing.")
    args = parser.parse_args()

    bundle_dir = resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
    path = bundle_dir / "goal-bootloader.md"
    if args.repo_root:
        repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
        if not (bundle_dir / "job.manifest.json").exists():
            raise SystemExit(f"missing manifest: {bundle_dir / 'job.manifest.json'}")
        if not (bundle_dir / "main.prompt.md").exists():
            raise SystemExit(f"missing main prompt: {bundle_dir / 'main.prompt.md'}")
        text = render_bootloader(bundle_dir, repo_root)
        if args.write:
            path.write_text(text, encoding="utf-8")
        print(text, end="" if text.endswith("\n") else "\n")
        return 0
    if args.write:
        raise SystemExit("--write requires --repo-root")
    if not path.exists():
        raise SystemExit(f"missing bootloader: {path}")
    text = path.read_text(encoding="utf-8")
    print(text, end="" if text.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
