#!/usr/bin/env python3
"""Deterministically lint a goal preflight bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MAX_ACTIVE_BRANCH_AGENTS = 5
DEFAULT_TOTAL_BRANCH_CAP = 25


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def has_dod(text: str) -> bool:
    lowered = text.lower()
    if "definition of done" not in lowered:
        return False
    after = lowered.split("definition of done", 1)[1]
    return "- " in after


def lint(bundle_dir: Path) -> dict:
    defects: list[dict] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append({"file": file, "severity": severity, "message": message})

    manifest_path = bundle_dir / "job.manifest.json"
    if not manifest_path.exists():
        defect("job.manifest.json", "critical", "manifest is missing")
        return result(defects)

    try:
        manifest = load_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        defect("job.manifest.json", "critical", f"manifest is not valid JSON: {exc}")
        return result(defects)

    for key in ["job_id", "main_prompt", "base_ref", "branches", "waves", "max_active_branch_agents"]:
        if key not in manifest:
            defect("job.manifest.json", "critical", f"missing key: {key}")

    max_active = manifest.get("max_active_branch_agents")
    if not isinstance(max_active, int) or max_active > MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "max_active_branch_agents must be an integer <= 5")

    branches = manifest.get("branches", [])
    if not branches:
        defect("job.manifest.json", "critical", "branches must be non-empty")
    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP and not manifest.get("allow_more_than_25_branches"):
        defect("job.manifest.json", "critical", "more than 25 branches requires explicit override")

    ids = [branch.get("id") for branch in branches]
    names = [branch.get("branch_name") for branch in branches]
    if len(ids) != len(set(ids)):
        defect("job.manifest.json", "critical", "branch ids must be unique")
    if len(names) != len(set(names)):
        defect("job.manifest.json", "critical", "branch names must be unique")

    waves = manifest.get("waves", [])
    wave_branch_ids = []
    for wave in waves:
        branch_ids = wave.get("branches", [])
        if len(branch_ids) > MAX_ACTIVE_BRANCH_AGENTS:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} has more than 5 branches")
        wave_branch_ids.extend(branch_ids)
    if set(wave_branch_ids) != set(ids):
        defect("job.manifest.json", "critical", "waves must cover exactly the manifest branch ids")

    main_path = resolve(bundle_dir, manifest.get("main_prompt", "main.prompt.md"))
    if not main_path.exists():
        defect(str(main_path), "critical", "main prompt is missing")
    else:
        main_text = main_path.read_text(encoding="utf-8")
        for phrase in [
            "skill availability bootstrap",
            "prompt audit",
            "max_active_branch_agents=5",
            "close finished branch orchestrator agents",
        ]:
            if phrase.lower() not in main_text.lower():
                defect(str(main_path), "critical", f"main prompt missing required phrase: {phrase}")
        if not has_dod(main_text):
            defect(str(main_path), "critical", "main prompt lacks a falsifiable Definition of Done")

    bootloader_path = bundle_dir / "goal-bootloader.md"
    if not bootloader_path.exists():
        defect("goal-bootloader.md", "critical", "bootloader is missing")
    else:
        bootloader = bootloader_path.read_text(encoding="utf-8")
        if len(bootloader) > 4000:
            defect("goal-bootloader.md", "critical", "bootloader exceeds 4000 characters")
        for phrase in [
            "$goal-main-orchestrator",
            "job.manifest.json",
            "main.prompt.md",
            "skill availability",
            "check_goal_skill_availability.py",
        ]:
            if phrase not in bootloader:
                defect("goal-bootloader.md", "critical", f"bootloader missing phrase: {phrase}")

    required_branch_keys = ["id", "wave", "prompt", "branch_name", "worktree_path", "status_path", "review_path"]
    for branch in branches:
        for key in required_branch_keys:
            if key not in branch:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} missing key: {key}")
        prompt_path = resolve(bundle_dir, branch.get("prompt", ""))
        if not prompt_path.exists():
            defect(str(prompt_path), "critical", f"branch prompt missing for {branch.get('id')}")
            continue
        text = prompt_path.read_text(encoding="utf-8")
        for phrase in [
            "Objective",
            "Scope",
            "Work Items",
            "Reviewer Requirement",
            "Bootstrap Requirement",
            "Stop Conditions",
        ]:
            if phrase.lower() not in text.lower():
                defect(str(prompt_path), "major", f"branch prompt missing section: {phrase}")
        if not has_dod(text):
            defect(str(prompt_path), "critical", f"branch {branch.get('id')} lacks a falsifiable Definition of Done")

    return result(defects)


def result(defects: list[dict]) -> dict:
    status = "pass" if not any(item["severity"] in {"critical", "major"} for item in defects) else "failed"
    return {"status": status, "defects": defects}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    bundle_dir = Path(args.bundle_dir).expanduser().resolve()
    data = lint(bundle_dir)
    output_path = Path(args.output).expanduser().resolve() if args.output else bundle_dir / "preflight.lint.json"
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(output_path)
    return 0 if data["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
