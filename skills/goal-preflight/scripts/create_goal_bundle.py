#!/usr/bin/env python3
"""Create a /goal orchestration bundle from a structured preflight brief."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath


MAX_ACTIVE_BRANCH_AGENTS = 4
MAX_WORKER_PACKETS_PER_BRANCH = 4
MAX_WAVES = 5
DEFAULT_TOTAL_BRANCH_CAP = MAX_ACTIVE_BRANCH_AGENTS * MAX_WAVES
SAFE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")


def slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "goal-job"


def require_safe_id(value: str, field: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_ID_RE.pattern}: {value!r}")
    return value


def require_safe_label(value: str, field: str) -> str:
    if not SAFE_LABEL_RE.fullmatch(value):
        raise SystemExit(f"{field} must match {SAFE_LABEL_RE.pattern}: {value!r}")
    return value


def resolve_absolute_path(value: str, field: str, *, must_exist: bool) -> Path:
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators: {value!r}")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise SystemExit(f"{field} must be an absolute path: {value!r}")
    if ".." in expanded.parts:
        raise SystemExit(f"{field} must not contain '..' traversal: {value!r}")
    if must_exist and not expanded.exists():
        raise SystemExit(f"{field} does not exist: {expanded}")
    return expanded.resolve(strict=must_exist)


def require_branch_name(value: str, field: str = "branch_name") -> str:
    if (
        not value
        or any(char in INVALID_BRANCH_CHARS for char in value)
        or any(char.isspace() for char in value)
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
    ):
        raise SystemExit(f"{field} is not a safe git branch name: {value!r}")
    return value


def require_relative_path(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise SystemExit(f"{field} must be a non-empty relative path")
    if "\\" in value:
        raise SystemExit(f"{field} must use POSIX '/' separators, not backslashes: {value!r}")
    if "//" in value:
        raise SystemExit(f"{field} must not contain empty path segments: {value!r}")
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        raise SystemExit(f"{field} must not contain '.' path segments: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise SystemExit(f"{field} must be relative, not absolute: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"{field} must not contain empty, '.', or '..' segments: {value!r}")
    return path.as_posix()


def require_agent_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 4") from exc
    if limit < 1 or limit > MAX_ACTIVE_BRANCH_AGENTS:
        raise SystemExit("max_active_branch_agents must be an integer from 1 to 4")
    return limit


def require_worker_limit(value: object) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4") from exc
    if limit < 1 or limit > MAX_WORKER_PACKETS_PER_BRANCH:
        raise SystemExit("max_active_worker_packets must be an integer from 1 to 4")
    return limit


def nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


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
        return "- No work items supplied; preflight should ask for or synthesize worker-sized items."
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
                    "Depends on:",
                    bullets(item.get("depends_on", [])),
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


def chunk_waves(branches: list[dict], wave_size: int) -> list[dict]:
    waves = []
    for offset in range(0, len(branches), wave_size):
        wave_branches = branches[offset : offset + wave_size]
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
    base_ref = require_branch_name(str(brief.get("base_ref", "main")), "base_ref")
    max_active = require_agent_limit(brief.get("max_active_branch_agents", MAX_ACTIVE_BRANCH_AGENTS))
    serial_reason = nonempty_text(brief.get("serial_reason"))
    parallelization_rationale = nonempty_text(brief.get("parallelization_rationale"))
    branches = []
    for idx, original in enumerate(brief["branches"], start=1):
        bid = original.get("id") or branch_id(idx)
        bid = require_safe_id(str(bid).upper(), "branch id")
        branch_name = require_branch_name(original.get("branch_name") or f"{job_id}-{bid.lower()}")
        worktree_path = require_relative_path(original.get("worktree_path") or f".worktrees/{branch_name}", "worktree_path")
        max_workers = require_worker_limit(original.get("max_active_worker_packets", MAX_WORKER_PACKETS_PER_BRANCH))
        worker_serial_reason = nonempty_text(original.get("worker_serial_reason"))
        worker_parallelization_rationale = nonempty_text(original.get("worker_parallelization_rationale"))
        work_items = original.get("work_items", [])
        if not isinstance(work_items, list):
            raise SystemExit(f"branch {bid} work_items must be a list")
        if len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
            raise SystemExit(f"branch {bid} must have 1 to {MAX_WORKER_PACKETS_PER_BRANCH} worker packets; split or synthesize work items")
        if any(not isinstance(item, dict) for item in work_items):
            raise SystemExit(f"branch {bid} work_items entries must be objects")
        branch = {
            **original,
            "work_items": work_items,
            "id": bid,
            "branch_name": branch_name,
            "worktree_path": worktree_path,
            "prompt": require_relative_path(original.get("prompt") or f"branches/{bid}.prompt.md", "prompt"),
            "status_path": require_relative_path(original.get("status_path") or f"branches/{bid}.status.json", "status_path"),
            "review_path": require_relative_path(original.get("review_path") or f"branches/{bid}.review.json", "review_path"),
            "max_active_worker_packets": max_workers,
            "worker_parallelism": {
                "parallelism_default": True,
                "max_active_worker_packets": max_workers,
                "max_worker_packets_per_branch": MAX_WORKER_PACKETS_PER_BRANCH,
                "serial_reason": worker_serial_reason,
                "parallelization_rationale": worker_parallelization_rationale
                or f"Launch independent worker packets concurrently up to {max_workers} active worker packets.",
                "wave_execution": "Launch independent worker packets concurrently up to max_active_worker_packets; collect finished worker status before launching replacements.",
            },
        }
        branches.append(branch)

    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        raise SystemExit(f"brief has more than {DEFAULT_TOTAL_BRANCH_CAP} branches; max is {MAX_WAVES} waves of {MAX_ACTIVE_BRANCH_AGENTS}")
    if len(branches) == 1 and not serial_reason:
        raise SystemExit("single-branch bundles are serialized and require brief.serial_reason")
    if max_active < MAX_ACTIVE_BRANCH_AGENTS and not (serial_reason or parallelization_rationale):
        raise SystemExit("max_active_branch_agents below 4 requires serial_reason or parallelization_rationale")

    waves = brief.get("waves") or chunk_waves(branches, max_active)
    if len(waves) > MAX_WAVES:
        raise SystemExit(f"waves must not exceed {MAX_WAVES}")
    branch_ids = {branch["id"] for branch in branches}
    seen_wave_ids = set()
    seen_wave_branches = []
    wave_by_branch = {}
    for idx, wave in enumerate(waves):
        wid = require_safe_label(str(wave["id"]), "wave id")
        if wid in seen_wave_ids:
            raise SystemExit(f"duplicate wave id: {wid}")
        seen_wave_ids.add(wid)
        wave["id"] = wid
        wave_branches = wave.get("branches")
        if not isinstance(wave_branches, list) or not wave_branches:
            raise SystemExit(f"wave {wid} must list at least one branch")
        if len(wave_branches) > max_active:
            raise SystemExit(f"wave {wid} has more than max_active_branch_agents={max_active} branches")
        if idx < len(waves) - 1 and len(wave_branches) < max_active and not (serial_reason or parallelization_rationale):
            raise SystemExit(f"underfilled non-final wave {wid} requires serial_reason or parallelization_rationale")
        for bid in wave_branches:
            if bid not in branch_ids:
                raise SystemExit(f"wave {wid} references unknown branch id: {bid}")
            if bid in wave_by_branch:
                raise SystemExit(f"branch {bid} appears in more than one wave")
            wave_by_branch[bid] = wave["id"]
            seen_wave_branches.append(bid)
    if set(seen_wave_branches) != branch_ids:
        raise SystemExit("waves must cover every branch exactly once")
    for branch in branches:
        branch["wave"] = wave_by_branch[branch["id"]]

    return {
        **brief,
        "job_id": job_id,
        "base_ref": base_ref,
        "artifact_policy": nonempty_text(brief.get("artifact_policy"))
        or "Preserve the full orchestration bundle under plans/orchestration/<job-id>; commit generated preflight prompts only when the user explicitly asks, and commit runtime status/review/audit artifacts only when the main prompt or user explicitly requires them.",
        "cleanup_policy": nonempty_text(brief.get("cleanup_policy"))
        or "On pass, report mergeability and leave branch/worktree removal to explicit user authorization. On partial, blocked, or failed runs, preserve branch worktrees, branches, packets, and logs for inspection unless the user explicitly authorizes cleanup.",
        "max_active_branch_agents": max_active,
        "parallelization": {
            "parallelism_default": True,
            "max_active_branch_agents": max_active,
            "max_branches_per_wave": MAX_ACTIVE_BRANCH_AGENTS,
            "max_waves": MAX_WAVES,
            "serial_reason": serial_reason,
            "parallelization_rationale": parallelization_rationale
            or f"Branches are grouped into waves of up to {max_active} independent branch agents.",
            "wave_execution": "Launch every branch in the current wave concurrently, then close finished branch orchestrators before launching the next wave.",
        },
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


def render_bootloader(bundle_dir: Path, repo_root: Path) -> str:
    manifest = bundle_dir / "job.manifest.json"
    main_prompt = bundle_dir / "main.prompt.md"
    return f"""Use $goal-main-orchestrator.

Prepared bundle:
- Bundle root: {bundle_dir}
- Repository root: {repo_root}
- Manifest: {manifest}
- Main prompt: {main_prompt}

Read the manifest and main prompt first. Treat main.prompt.md as the runtime contract. Do not infer paths from the current working directory; use the bundle root and repository root above.

If the bundle root or repository root above is wrong because files moved, stop and regenerate the bootloader with goal-preflight. Do not hand-edit these paths.

Pass only absolute paths to goal orchestration scripts. If a script entry path would be relative or would contain `..` traversal, stop and regenerate the bundle or bootloader.

Mandatory bootstrap first: verify runtime skill availability before prompt audit. Resolve GOAL_SKILLS_ROOT from ${{CODEX_HOME:-$HOME/.codex}}/skills, falling back to $HOME/.agents/skills, then run check_goal_skill_availability.py for goal-main-orchestrator and goal-branch-orchestrator. If either skill or required script is unavailable, return blocked and ask the user to install the skills package.

Mandatory second action: create and run the prompt-audit packet over job.manifest.json, main.prompt.md, and every listed branch prompt. Do not create branch worktrees or launch branch orchestrators unless bootstrap passed and prompt-audit.json says status=pass, can_start=true, and pins the manifest and repository root above.

Parallelism is the default. Respect max_active_branch_agents from job.manifest.json; never exceed {MAX_ACTIVE_BRANCH_AGENTS}. Launch every branch in the current wave concurrently up to that limit. Run branch waves sequentially. Each branch entry must declare 1 to 4 worker packets and branch prompts require independent worker packets to launch concurrently up to the branch worker cap. After dispatch, wait for branch agents; do not poll active branch worktrees, worker packets, reviewer packets, process tables, or status files. Collect finished branch status/review artifacts and close finished branch orchestrator agents before launching replacements. A single-branch or otherwise serialized plan is valid only when job.manifest.json records a serial_reason or parallelization_rationale.

Finish only when main.prompt.md Definition of Done is falsifiably satisfied by status files, review files, command evidence, and final git state. If anything is missing or unverifiable, return blocked or partial, not pass.
"""


def create_bundle(brief: dict, repo_root: Path, out_dir: Path | None) -> Path:
    brief = normalize_brief(brief)

    bundle_dir = out_dir or repo_root / "plans" / "orchestration" / brief["job_id"]
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for dirname in ["branches", "workers", "reviewers", "audit"]:
        (bundle_dir / dirname).mkdir(exist_ok=True)

    manifest = {
        "job_id": brief["job_id"],
        "main_prompt": "main.prompt.md",
        "base_ref": brief["base_ref"],
        "artifact_policy": brief["artifact_policy"],
        "cleanup_policy": brief["cleanup_policy"],
        "max_active_branch_agents": brief["max_active_branch_agents"],
        "parallelization": brief["parallelization"],
        "branches": [
            {
                "id": branch["id"],
                "wave": branch["wave"],
                "prompt": branch["prompt"],
                "branch_name": branch["branch_name"],
                "worktree_path": branch["worktree_path"],
                "status_path": branch["status_path"],
                "review_path": branch["review_path"],
                "work_items": branch["work_items"],
                "max_active_worker_packets": branch["max_active_worker_packets"],
                "worker_parallelism": branch["worker_parallelism"],
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
            max_active_branch_agents=brief["max_active_branch_agents"],
            parallelization_rationale=brief["parallelization"]["parallelization_rationale"],
            merge_policy=brief.get("merge_policy", "Report mergeability only unless explicitly authorized to merge."),
            cleanup_policy=brief["cleanup_policy"],
            artifact_policy=brief["artifact_policy"],
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
                base_ref=brief["base_ref"],
                branch_name=branch["branch_name"],
                worktree_path=branch["worktree_path"],
                wave=branch["wave"],
                max_active_worker_packets=branch["max_active_worker_packets"],
                worker_parallelization_rationale=branch["worker_parallelism"]["parallelization_rationale"],
                objective=branch.get("objective", "Objective not supplied."),
                scope=branch.get("scope", "Scope not supplied."),
                owned_paths=bullets(branch.get("owned_paths", [])),
                work_items=format_work_items(branch.get("work_items", [])),
                tests=bullets(branch.get("tests", [])),
                stop_conditions=bullets(branch.get("stop_conditions", [])),
                dod=bullets(branch.get("dod", [])),
            ),
        )

    bootloader = render_bootloader(bundle_dir.resolve(), repo_root.resolve())
    write(bundle_dir / "goal-bootloader.md", bootloader)
    report = "\n".join(
        [
            f"# Preflight Report: {brief['job_id']}",
            "",
            f"Bundle: {bundle_dir.resolve()}",
            f"Branches: {len(brief['branches'])}",
            f"Waves: {len(brief['waves'])}",
            f"Max active branch agents: {brief['max_active_branch_agents']}",
            f"Parallelization: {brief['parallelization']['parallelization_rationale']}",
            f"Artifact policy: {brief['artifact_policy']}",
            f"Cleanup policy: {brief['cleanup_policy']}",
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
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir")
    args = parser.parse_args()

    brief_path = resolve_absolute_path(args.brief, "--brief", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False) if args.out_dir else None
    bundle_dir = create_bundle(load_json(brief_path), repo_root, out_dir)
    print(bundle_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
