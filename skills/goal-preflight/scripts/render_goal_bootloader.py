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
    return f"""Use $goal-main-orchestrator.

Prepared bundle:
- Bundle root: {bundle_dir}
- Repository root: {repo_root}
- Manifest: {manifest}
- Main prompt: {main_prompt}

Read `job.manifest.json` and `main.prompt.md` first, then run the main skill `runtime_phase_manifest.py --markdown`. Treat script output, JSON artifacts, and validator defects as the working surface; do not read skill Python source unless debugging a failed script.

Use the Bundle root and Repository root above. If either moved, regenerate this bootloader; do not hand-edit paths.

Pass only absolute paths to goal orchestration scripts. Stop if a script path would be relative or contain `..` traversal.

Mandatory bootstrap first: verify skill availability with check_goal_skill_availability.py for goal-main-orchestrator, goal-branch-orchestrator, and goal-plan-amender. Resolve GOAL_SKILLS_ROOT from ${{CODEX_HOME:-$HOME/.codex}}/skills, falling back to $HOME/.agents/skills. Then record the fresh live Codex model catalog:

python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py" --json --require-codex > {model_catalog}

Mandatory second action: create and run prompt audit over job.manifest.json, main.prompt.md, and listed branch prompts. Do not create branch worktrees unless prompt-audit.json says status=pass, can_start=true, and pins the manifest and repository root above.

Parallelism is the default. Respect max_active_branch_agents from job.manifest.json; never exceed {MAX_ACTIVE_BRANCH_AGENTS}. Keep branch orchestrator slots saturated and launch the next eligible branch after closing a finished agent. Defer only unresolved manifest depends_on ids; non-pass dependencies need dependency_failed evidence. Waves are scheduling/order groups, not barriers. Each branch declares 1 to 4 worker packets and branch prompts require a rolling saturated pool up to the worker cap. Record scheduler v2 evidence under schedulers/. Wait for dispatched agents; do not poll active branch, worker, reviewer, process, or status artifacts.

Finish only when main.prompt.md Definition of Done is falsifiably satisfied by status files, review files, packet telemetry, telemetry.summary.json, command evidence, and final git state. If anything is missing or unverifiable, return blocked or partial, not pass.
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
