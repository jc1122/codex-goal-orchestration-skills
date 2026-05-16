#!/usr/bin/env python3
"""Create a /goal orchestration bundle from a structured preflight brief."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


MAX_ACTIVE_BRANCH_AGENTS = 5
DEFAULT_TOTAL_BRANCH_CAP = 25


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "goal-job"


def branch_id(index: int) -> str:
    return f"B{index:02d}"


def wave_id(index: int) -> str:
    return f"wave-{index:02d}"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bullets(values: list[str], fallback: str = "- none") -> str:
    if not values:
        return fallback
    return "\n".join(f"- {value}" for value in values)


def format_work_items(items: list[dict]) -> str:
    if not items:
        return "- No work items supplied; preflight should ask for or synthesize Spark-sized items."
    chunks = []
    for idx, item in enumerate(items, start=1):
        item_id = item.get("id") or f"W{idx:02d}"
        chunks.append(
            "\n".join(
                [
                    f"### {item_id}: {item.get('title') or item.get('objective') or 'Work item'}",
                    "",
                    item.get("objective", "Objective not supplied."),
                    "",
                    "Owned files/modules:",
                    bullets(item.get("owned_paths", [])),
                    "",
                    "Context files:",
                    bullets(item.get("context_files", [])),
                    "",
                    "Verification commands:",
                    bullets(item.get("verification", [])),
                    "",
                    "Definition of Done:",
                    bullets(item.get("dod", [])),
                ]
            )
        )
    return "\n\n".join(chunks)


def chunk_waves(branches: list[dict]) -> list[dict]:
    waves = []
    for offset in range(0, len(branches), MAX_ACTIVE_BRANCH_AGENTS):
        wave_branches = branches[offset : offset + MAX_ACTIVE_BRANCH_AGENTS]
        waves.append(
            {
                "id": wave_id(len(waves) + 1),
                "branches": [branch["id"] for branch in wave_branches],
            }
        )
    return waves


def normalize_brief(brief: dict) -> dict:
    if "job_id" not in brief:
        raise SystemExit("brief must include job_id")
    if not brief.get("branches"):
        raise SystemExit("brief must include synthesized branches before bundle generation")

    job_id = slug(brief["job_id"])
    branches = []
    for idx, original in enumerate(brief["branches"], start=1):
        bid = original.get("id") or branch_id(idx)
        bid = bid.upper()
        branch_name = original.get("branch_name") or f"{job_id}-{bid.lower()}"
        worktree_path = original.get("worktree_path") or f".worktrees/{branch_name}"
        branch = {
            **original,
            "id": bid,
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "prompt": original.get("prompt") or f"branches/{bid}.prompt.md",
            "status_path": original.get("status_path") or f"branches/{bid}.status.json",
            "review_path": original.get("review_path") or f"branches/{bid}.review.json",
        }
        branches.append(branch)

    waves = brief.get("waves") or chunk_waves(branches)
    wave_by_branch = {}
    for wave in waves:
        for bid in wave["branches"]:
            wave_by_branch[bid] = wave["id"]
    for branch in branches:
        branch["wave"] = branch.get("wave") or wave_by_branch.get(branch["id"], wave_id(1))

    return {
        **brief,
        "job_id": job_id,
        "base_ref": brief.get("base_ref", "main"),
        "max_active_branch_agents": int(brief.get("max_active_branch_agents", MAX_ACTIVE_BRANCH_AGENTS)),
        "branches": branches,
        "waves": waves,
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def render_branch_waves(waves: list[dict]) -> str:
    lines = []
    for wave in waves:
        lines.append(f"- {wave['id']}: {', '.join(wave['branches'])}")
    return "\n".join(lines)


def render_bootloader(bundle_dir: Path) -> str:
    manifest = bundle_dir / "job.manifest.json"
    main_prompt = bundle_dir / "main.prompt.md"
    return f"""Use $goal-main-orchestrator.

Prepared bundle:
- Manifest: {manifest}
- Main prompt: {main_prompt}

Read the manifest and main prompt first. Treat main.prompt.md as the runtime contract.

Mandatory bootstrap first: verify runtime skill availability before prompt audit. Resolve GOAL_SKILLS_ROOT from ${{CODEX_HOME:-$HOME/.codex}}/skills, falling back to $HOME/.agents/skills, then run check_goal_skill_availability.py for goal-main-orchestrator and goal-branch-orchestrator. If either skill or required script is unavailable, return blocked and ask the user to install the skills package.

Mandatory second action: create and run the prompt-audit packet over job.manifest.json, main.prompt.md, and every listed branch prompt. Do not create branch worktrees or launch branch orchestrators unless bootstrap passed and prompt-audit.json says status=pass and can_start=true.

Respect max_active_branch_agents=5. Run branch waves sequentially. Keep at most 5 branch orchestrator agents active. Collect finished branch status/review artifacts and close finished branch orchestrator agents before launching replacements.

Finish only when main.prompt.md Definition of Done is falsifiably satisfied by status files, review files, command evidence, and final git state. If anything is missing or unverifiable, return blocked or partial, not pass.
"""


def create_bundle(brief: dict, repo_root: Path, out_dir: Path | None) -> Path:
    brief = normalize_brief(brief)
    if len(brief["branches"]) > DEFAULT_TOTAL_BRANCH_CAP and not brief.get("allow_more_than_25_branches"):
        raise SystemExit("brief has more than 25 branches; explicit override required")

    bundle_dir = out_dir or repo_root / "plans" / "orchestration" / brief["job_id"]
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ["branches", "workers", "reviewers", "audit"]:
        (bundle_dir / dirname).mkdir(exist_ok=True)

    manifest = {
        "job_id": brief["job_id"],
        "main_prompt": "main.prompt.md",
        "base_ref": brief["base_ref"],
        "max_active_branch_agents": brief["max_active_branch_agents"],
        "branches": [
            {
                "id": branch["id"],
                "wave": branch["wave"],
                "prompt": branch["prompt"],
                "branch_name": branch["branch_name"],
                "worktree_path": branch["worktree_path"],
                "status_path": branch["status_path"],
                "review_path": branch["review_path"],
            }
            for branch in brief["branches"]
        ],
        "waves": brief["waves"],
    }
    write(bundle_dir / "job.manifest.json", json.dumps(manifest, indent=2) + "\n")

    main_prompt = (Path(__file__).resolve().parents[1] / "assets" / "main.prompt.template.md").read_text(encoding="utf-8")
    write(
        bundle_dir / "main.prompt.md",
        main_prompt.format(
            title=brief.get("title", brief["job_id"]),
            job_id=brief["job_id"],
            base_ref=brief["base_ref"],
            goal=brief.get("goal", "Goal not supplied."),
            source_summary=brief.get("source_summary", "Source summary not supplied."),
            branch_waves=render_branch_waves(brief["waves"]),
            merge_policy=brief.get("merge_policy", "Report mergeability only unless explicitly authorized to merge."),
            cleanup_policy=brief.get("cleanup_policy", "Do not remove branches or worktrees unless explicitly authorized."),
            required_evidence=bullets(brief.get("required_evidence", [])),
            final_dod=bullets(brief.get("final_dod", [])),
        ),
    )

    branch_template = (Path(__file__).resolve().parents[1] / "assets" / "branch.prompt.template.md").read_text(encoding="utf-8")
    for branch in brief["branches"]:
        write(
            bundle_dir / branch["prompt"],
            branch_template.format(
                branch_id=branch["id"],
                title=branch.get("title", branch.get("objective", branch["id"])),
                branch_name=branch["branch_name"],
                worktree_path=branch["worktree_path"],
                wave=branch["wave"],
                objective=branch.get("objective", "Objective not supplied."),
                scope=branch.get("scope", "Scope not supplied."),
                owned_paths=bullets(branch.get("owned_paths", [])),
                work_items=format_work_items(branch.get("work_items", [])),
                tests=bullets(branch.get("tests", [])),
                stop_conditions=bullets(branch.get("stop_conditions", [])),
                dod=bullets(branch.get("dod", [])),
            ),
        )

    bootloader = render_bootloader(bundle_dir.resolve())
    write(bundle_dir / "goal-bootloader.md", bootloader)
    report = "\n".join(
        [
            f"# Preflight Report: {brief['job_id']}",
            "",
            f"Bundle: {bundle_dir.resolve()}",
            f"Branches: {len(brief['branches'])}",
            f"Waves: {len(brief['waves'])}",
            "Max active branch agents: 5",
            "",
            "Bootstrap: generated bootloaders require runtime skill availability checks before prompt audit.",
            "Run `lint_goal_bundle.py` before launching `/goal`.",
            "",
        ]
    )
    write(bundle_dir / "PREFLIGHT_REPORT.md", report)
    return bundle_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--out-dir")
    args = parser.parse_args()

    brief_path = Path(args.brief).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    bundle_dir = create_bundle(load_json(brief_path), repo_root, out_dir)
    print(bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
