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

Read the manifest and main prompt first. Treat main.prompt.md as the runtime contract. Do not infer paths from the current working directory; use the bundle root and repository root above.

If the bundle root or repository root above is wrong because files moved, stop and regenerate the bootloader with goal-preflight. Do not hand-edit these paths.

Pass only absolute paths to goal orchestration scripts. If a script entry path would be relative or would contain `..` traversal, stop and regenerate the bundle or bootloader.

Mandatory bootstrap first: verify runtime skill availability before prompt audit. Resolve GOAL_SKILLS_ROOT from ${{CODEX_HOME:-$HOME/.codex}}/skills, falling back to $HOME/.agents/skills, then run check_goal_skill_availability.py for goal-main-orchestrator, goal-branch-orchestrator, and goal-plan-amender. If any skill or required script is unavailable, return blocked and ask the user to install the skills package. Then record the fresh live Codex model catalog before selecting model routes:

python3 "$GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py" --json --require-codex > {model_catalog}

Mandatory second action: create and run the prompt-audit packet over job.manifest.json, main.prompt.md, and every listed branch prompt. Do not create branch worktrees or launch branch orchestrators unless bootstrap passed and prompt-audit.json says status=pass, can_start=true, and pins the manifest and repository root above.

Parallelism is the default. Respect max_active_branch_agents from job.manifest.json; never exceed {MAX_ACTIVE_BRANCH_AGENTS}. Keep branch orchestrator slots saturated up to that cap: when a branch finishes with pass and capacity is freed, launch the next eligible branch immediately. Partial, blocked, or failed manifest depends_on branch ids do not unlock downstream work; record dependency_failed evidence instead. After a terminal branch result is validated and the branch agent is closed, goal-plan-amender may propose amendments only for future unstarted work through route-bound packets with route.json and telemetry.json; accepted amendments must archive the prior manifest, regenerate changed future branch prompts, rerun lint, and never mutate active or terminal branch evidence. Waves are scheduling/order groups, not implicit dependency barriers. Record schema v2 scheduler events under schedulers/ with seq, timestamp, runtime_ref, and reason_code for defer/under_capacity/blocked. Each branch entry must declare 1 to 4 worker packets and branch prompts require independent worker packets to launch as a rolling saturated pool up to the branch worker cap. After dispatch, wait for branch agents; do not poll active branch worktrees, worker packets, reviewer packets, process tables, or status files. After 3 no-completion waits, inspect only native agent/process state, close unreachable/stale work as structured blocked evidence, and refill capacity. Collect finished branch status/review artifacts and close finished branch orchestrator agents before launching replacements. A single-branch or otherwise serialized plan is valid only when job.manifest.json records serial_reasons.

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
