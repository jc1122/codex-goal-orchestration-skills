#!/usr/bin/env python3
"""Check that goal orchestration skills are installed and runnable."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path


REQUIRED_FILES = {
    "goal-preflight": [
        "SKILL.md",
        "scripts/create_goal_bundle.py",
        "scripts/create_lite_advice_packet.py",
        "scripts/lint_goal_bundle.py",
        "scripts/render_goal_bootloader.py",
        "scripts/validate_lite_advice.py",
        "scripts/check_goal_skill_availability.py",
    ],
    "goal-main-orchestrator": [
        "SKILL.md",
        "scripts/create_audit_packet.py",
        "scripts/create_lite_advice_packet.py",
        "scripts/render_branch_worktree_commands.py",
        "scripts/validate_main_status.py",
        "scripts/validate_lite_advice.py",
        "scripts/check_goal_skill_availability.py",
    ],
    "goal-branch-orchestrator": [
        "SKILL.md",
        "scripts/create_runtime_packet.py",
        "scripts/create_lite_advice_packet.py",
        "scripts/validate_branch_status.py",
        "scripts/validate_lite_advice.py",
        "scripts/check_goal_skill_availability.py",
    ],
}


def normalize_absolute_root(value: str | Path, field: str, *, fail_on_relative: bool) -> Path | None:
    text = str(value)
    if "\\" in text:
        if fail_on_relative:
            raise SystemExit(f"{field} must use POSIX '/' separators: {text!r}")
        return None
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        if fail_on_relative:
            raise SystemExit(f"{field} must be an absolute path: {text!r}")
        return None
    if ".." in expanded.parts:
        if fail_on_relative:
            raise SystemExit(f"{field} must not contain '..' traversal: {text!r}")
        return None
    return expanded.resolve(strict=False)


def add_unique(paths: list[Path], path: Path | None) -> None:
    if path is None:
        return
    if path not in paths:
        paths.append(path)


def candidate_roots(cli_roots: list[str], allow_fallback_roots: bool) -> list[Path]:
    roots: list[Path] = []
    for root in cli_roots:
        add_unique(roots, normalize_absolute_root(root, "--skills-root", fail_on_relative=True))
    if cli_roots and not allow_fallback_roots:
        return roots
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        add_unique(roots, normalize_absolute_root(Path(codex_home) / "skills", "CODEX_HOME/skills", fail_on_relative=False))
    add_unique(roots, normalize_absolute_root(Path.home() / ".codex" / "skills", "~/.codex/skills", fail_on_relative=False))
    add_unique(roots, normalize_absolute_root(Path.home() / ".agents" / "skills", "~/.agents/skills", fail_on_relative=False))
    try:
        add_unique(roots, normalize_absolute_root(Path(__file__).resolve().parents[2], "script skill root", fail_on_relative=False))
    except IndexError:
        pass
    return roots


def declared_skill_name(skill_md: Path) -> str | None:
    if not skill_md.exists():
        return None
    for line in skill_md.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*name:\s*['\"]?([^'\"\s]+)", line)
        if match:
            return match.group(1)
    return None


def inspect_skill(root: Path, skill: str) -> dict:
    skill_dir = root / skill
    missing = []
    if not skill_dir.is_dir():
        return {
            "skill": skill,
            "root": str(root),
            "available": False,
            "declared_name": None,
            "missing": ["skill directory"],
        }
    for rel_path in REQUIRED_FILES[skill]:
        if not (skill_dir / rel_path).exists():
            missing.append(rel_path)
    declared_name = declared_skill_name(skill_dir / "SKILL.md")
    if declared_name != skill:
        missing.append(f"SKILL.md name mismatch: {declared_name!r}")
    return {
        "skill": skill,
        "root": str(root),
        "available": not missing,
        "declared_name": declared_name,
        "missing": missing,
    }


def find_skill(roots: list[Path], skill: str) -> dict:
    attempts = [inspect_skill(root, skill) for root in roots]
    for attempt in attempts:
        if attempt["available"]:
            return {"status": "pass", "selected": attempt, "attempts": attempts}
    return {"status": "missing", "selected": None, "attempts": attempts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skills-root", action="append", default=[])
    parser.add_argument("--require", action="append", choices=sorted(REQUIRED_FILES), default=[])
    parser.add_argument("--require-codex-cli", action="store_true")
    parser.add_argument("--allow-fallback-roots", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    required = args.require or sorted(REQUIRED_FILES)
    roots = candidate_roots(args.skills_root, args.allow_fallback_roots)
    skills = {skill: find_skill(roots, skill) for skill in required}
    codex_cli = shutil.which("codex") if args.require_codex_cli else None
    blockers = [skill for skill, result in skills.items() if result["status"] != "pass"]
    selected_roots = {
        result["selected"]["root"]
        for result in skills.values()
        if result.get("selected")
    }
    if len(selected_roots) > 1:
        blockers.append("mixed-skill-roots")
    if args.require_codex_cli and not codex_cli:
        blockers.append("codex-cli")

    result = {
        "status": "pass" if not blockers else "blocked",
        "required": required,
        "roots_checked": [str(root) for root in roots],
        "skills": skills,
        "codex_cli": codex_cli,
        "blockers": blockers,
        "selected_roots": sorted(selected_roots),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"status={result['status']}")
        for skill, data in skills.items():
            selected = data["selected"]
            if selected:
                print(f"{skill}: pass at {selected['root']}")
            else:
                print(f"{skill}: missing")
        if len(selected_roots) > 1:
            print(f"selected roots: {', '.join(sorted(selected_roots))}")
        if args.require_codex_cli:
            print(f"codex-cli: {'pass at ' + codex_cli if codex_cli else 'missing'}")
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
