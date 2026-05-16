#!/usr/bin/env python3
"""Deterministically lint a goal preflight bundle."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath


MAX_ACTIVE_BRANCH_AGENTS = 5
DEFAULT_TOTAL_BRANCH_CAP = 25
SAFE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
SAFE_LABEL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")
INVALID_BRANCH_CHARS = set(" ~^:?*[\\")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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


def resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def relative_path_defect(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{field} must be a non-empty relative path"
    if "\\" in value:
        return f"{field} must use POSIX '/' separators, not backslashes"
    if "//" in value:
        return f"{field} must not contain empty path segments"
    if value.startswith("./") or "/./" in value or value.endswith("/."):
        return f"{field} must not contain '.' path segments"
    path = PurePosixPath(value)
    if path.is_absolute():
        return f"{field} must be relative, not absolute"
    if any(part in {"", ".", ".."} for part in path.parts):
        return f"{field} must not contain empty, '.', or '..' segments"
    return None


def safe_branch_name(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return not (
        any(char in INVALID_BRANCH_CHARS for char in value)
        or any(char.isspace() for char in value)
        or value.startswith(("/", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
    )


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
    if not isinstance(max_active, int) or max_active < 1 or max_active > MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "max_active_branch_agents must be an integer from 1 to 5")

    branches = manifest.get("branches", [])
    if not branches:
        defect("job.manifest.json", "critical", "branches must be non-empty")
    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP and not manifest.get("allow_more_than_25_branches"):
        defect("job.manifest.json", "critical", "more than 25 branches requires explicit override")

    ids = [branch.get("id") for branch in branches]
    names = [branch.get("branch_name") for branch in branches]
    for bid in ids:
        if not isinstance(bid, str) or not SAFE_ID_RE.fullmatch(bid):
            defect("job.manifest.json", "critical", f"branch id is not safe: {bid!r}")
    if len(ids) != len(set(ids)):
        defect("job.manifest.json", "critical", "branch ids must be unique")
    for name in names:
        if not safe_branch_name(name):
            defect("job.manifest.json", "critical", f"branch name is not safe: {name!r}")
    if len(names) != len(set(names)):
        defect("job.manifest.json", "critical", "branch names must be unique")

    waves = manifest.get("waves", [])
    wave_branch_ids = []
    wave_ids = []
    for wave in waves:
        wid = wave.get("id")
        wave_ids.append(wid)
        if not isinstance(wid, str) or not SAFE_LABEL_RE.fullmatch(wid):
            defect("job.manifest.json", "critical", f"wave id is not safe: {wid!r}")
        branch_ids = wave.get("branches", [])
        if len(branch_ids) > MAX_ACTIVE_BRANCH_AGENTS:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} has more than 5 branches")
        wave_branch_ids.extend(branch_ids)
    if len(wave_ids) != len(set(wave_ids)):
        defect("job.manifest.json", "critical", "wave ids must be unique")
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        defect("job.manifest.json", "critical", "branch ids must not appear in more than one wave")
    if set(wave_branch_ids) != set(ids):
        defect("job.manifest.json", "critical", "waves must cover exactly the manifest branch ids")

    main_prompt_value = manifest.get("main_prompt", "main.prompt.md")
    main_path_error = relative_path_defect(main_prompt_value, "main_prompt")
    if main_path_error:
        defect("job.manifest.json", "critical", main_path_error)
        main_path = None
    else:
        main_path = resolve(bundle_dir, main_prompt_value)
    if main_path is not None and not main_path.exists():
        defect(str(main_path), "critical", "main prompt is missing")
    elif main_path is not None:
        main_text = main_path.read_text(encoding="utf-8")
        for phrase in [
            "manifest paths",
            "repository root",
            "skill availability bootstrap",
            "prompt audit",
            "prompt-audit.json",
            "pins this manifest",
            "max_active_branch_agents",
            "never exceed 5",
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
            "Bundle root",
            "Repository root",
            "job.manifest.json",
            "main.prompt.md",
            "skill availability",
            "check_goal_skill_availability.py",
            "absolute paths",
            "pins the manifest",
        ]:
            if phrase not in bootloader:
                defect("goal-bootloader.md", "critical", f"bootloader missing phrase: {phrase}")

    required_branch_keys = ["id", "wave", "prompt", "branch_name", "worktree_path", "status_path", "review_path"]
    for branch in branches:
        for key in required_branch_keys:
            if key not in branch:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} missing key: {key}")
        for key in ["prompt", "status_path", "review_path"]:
            message = relative_path_defect(branch.get(key), key)
            if message:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
        message = relative_path_defect(branch.get("worktree_path"), "worktree_path")
        if message:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
        prompt_value = branch.get("prompt", "")
        if relative_path_defect(prompt_value, "prompt"):
            continue
        prompt_path = resolve(bundle_dir, prompt_value)
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

    bundle_dir = resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
    data = lint(bundle_dir)
    output_path = (
        resolve_absolute_path(args.output, "--output", must_exist=False)
        if args.output
        else bundle_dir / "preflight.lint.json"
    )
    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(output_path)
    return 0 if data["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
