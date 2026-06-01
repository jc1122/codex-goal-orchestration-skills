#!/usr/bin/env python3
"""Validate release readiness for the goal orchestration skills package."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
README = ROOT / "README.md"
SKILLS_DIR = ROOT / "skills"
INSTALLER = ROOT / "bin" / "install-goal-skills.js"
SKILL_VERSION_RE = re.compile(r"(?m)^version:\s*(?P<version>\S+)\s*$")
EXPECTED_SKILLS = [
    "goal-branch-orchestrator",
    "goal-config",
    "goal-main-orchestrator",
    "goal-plan-amender",
    "goal-preflight",
]
EXPECTED_SUPPORT_DIRS = ["_goal_shared"]
EXPECTED_METADATA_FILES = ["AGENTS.md", "maintenance/agent-context-index.json"]
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REQUIRED_PACKAGE_FILES = {
    "AGENTS.md",
    "README.md",
    "package.json",
    "bin/install-goal-skills.js",
    "fixtures/preparedness/research-worker-brief.json",
    "maintenance/AGENT_MAINTENANCE.md",
    "maintenance/agent-context-index.json",
    "maintenance/dependency-policy.json",
    "maintenance/size-budget.json",
    "scripts/check_dependency_policy.py",
    "scripts/check_goal_config_fixtures.py",
    "scripts/check_golden_smoke.py",
    "scripts/check_model_catalog.py",
    "scripts/fixture_support.py",
    "scripts/check_preparedness_fixtures.py",
    "scripts/check_release.py",
    "scripts/check_size_budget.py",
    "scripts/generate_agent_context_index.py",
    "scripts/sync_goal_shared.py",
    "skills/_goal_shared/scripts/check_model_catalog.py",
    "skills/_goal_shared/scripts/context_pack.py",
    "skills/_goal_shared/scripts/orchestration_contract.py",
    "skills/_goal_shared/scripts/runtime_lite_runner.py",
    "skills/_goal_shared/scripts/runtime_phase_manifest.py",
    "skills/_goal_shared/scripts/scheduler_tick.py",
    "skills/_goal_shared/scripts/status_validation.py",
    "skills/goal-branch-orchestrator/SKILL.md",
    "skills/goal-branch-orchestrator/scripts/assemble_branch_status.py",
    "skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py",
    "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
    "skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py",
    "skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py",
    "skills/goal-config/SKILL.md",
    "skills/goal-config/references/configuration-contract.md",
    "skills/goal-config/scripts/check_goal_config.py",
    "skills/goal-config/scripts/create_goal_config.py",
    "skills/goal-config/scripts/runtime_phase_manifest.py",
    "skills/goal-config/scripts/scan_configurables.py",
    "skills/goal-main-orchestrator/SKILL.md",
    "skills/goal-main-orchestrator/scripts/assemble_main_status.py",
    "skills/goal-main-orchestrator/scripts/deterministic_prompt_audit.py",
    "skills/goal-main-orchestrator/scripts/runtime_phase_manifest.py",
    "skills/goal-main-orchestrator/scripts/validate_prompt_audit.py",
    "skills/goal-plan-amender/SKILL.md",
    "skills/goal-plan-amender/scripts/amendment_lib.py",
    "skills/goal-plan-amender/scripts/apply_manifest_amendment.py",
    "skills/goal-plan-amender/scripts/create_amendment_decision.py",
    "skills/goal-plan-amender/scripts/create_adaptation_packet.py",
    "skills/goal-plan-amender/scripts/create_blocker_repair_packet.py",
    "skills/goal-plan-amender/scripts/recommend_amendment_decision.py",
    "skills/goal-plan-amender/scripts/runtime_phase_manifest.py",
    "skills/goal-plan-amender/scripts/validate_amender_packet.py",
    "skills/goal-plan-amender/scripts/validate_manifest_amendment.py",
    "skills/goal-preflight/SKILL.md",
    "skills/goal-preflight/scripts/lint_preflight_brief.py",
    "skills/goal-preflight/scripts/runtime_phase_manifest.py",
}
REQUIRED_PACKAGE_FILES_ENTRIES = {
    "AGENTS.md",
    "bin/",
    "fixtures/",
    "maintenance/AGENT_MAINTENANCE.md",
    "maintenance/agent-context-index.json",
    "maintenance/dependency-policy.json",
    "maintenance/size-budget.json",
    "scripts/check_dependency_policy.py",
    "scripts/check_goal_config_fixtures.py",
    "scripts/check_golden_smoke.py",
    "scripts/check_model_catalog.py",
    "scripts/fixture_support.py",
    "scripts/generate_agent_context_index.py",
    "scripts/check_preparedness_fixtures.py",
    "scripts/check_release.py",
    "scripts/check_size_budget.py",
    "scripts/sync_goal_shared.py",
    "skills/",
    "README.md",
}


def run(command: list[str], *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    npm_cache = str(Path(tempfile.gettempdir()) / "codex-goal-npm-cache")
    env["npm_config_cache"] = npm_cache
    env["NPM_CONFIG_CACHE"] = npm_cache
    env["npm_config_update_notifier"] = "false"
    env["NPM_CONFIG_UPDATE_NOTIFIER"] = "false"
    env["NO_UPDATE_NOTIFIER"] = "1"
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=env,
    )
    if result.returncode != expect:
        print(f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(1)
    return result


def load_package() -> dict:
    data = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("package.json must be a JSON object")
    return data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def check_metadata(package: dict) -> str:
    require(package.get("name") == "codex-goal-orchestration-skills", "package.json name is wrong")
    version = package.get("version")
    require(isinstance(version, str) and SEMVER_RE.fullmatch(version) is not None, "package.json version must be valid semver")
    require(isinstance(package.get("description"), str) and package["description"].strip(), "package.json description is required")
    require(isinstance(package.get("license"), str) and package["license"].strip(), "package.json license is required")
    repository = package.get("repository")
    require(isinstance(repository, dict) and repository.get("type") == "git", "package.json repository.type must be git")
    require(
        isinstance(repository, dict)
        and isinstance(repository.get("url"), str)
        and repository["url"].startswith("git+https://github.com/"),
        "package.json repository.url must be a git+https GitHub URL",
    )
    engines = package.get("engines")
    require(isinstance(engines, dict) and engines.get("node") == ">=18", "package.json engines.node must be >=18")
    bins = package.get("bin")
    require(isinstance(bins, dict), "package.json bin must be an object")
    for name in ("codex-goal-orchestration-skills", "install-codex-goal-skills"):
        require(bins.get(name) == "./bin/install-goal-skills.js", f"package.json bin.{name} must point to installer")
    files = package.get("files")
    require(isinstance(files, list), "package.json files must be an array")
    missing_file_entries = sorted(REQUIRED_PACKAGE_FILES_ENTRIES - set(files))
    require(not missing_file_entries, f"package.json files is missing entries: {', '.join(missing_file_entries)}")
    scripts = package.get("scripts")
    require(isinstance(scripts, dict), "package.json scripts must be an object")
    for script in (
        "check",
        "check:config",
        "check:shared",
        "check:fixtures",
        "check:golden",
        "check:release",
        "check:maintenance",
        "check:models",
        "check:context",
        "generate:context",
        "context:index",
    ):
        require(script in scripts, f"package.json scripts missing {script}")
    return version


def check_readme(version: str) -> None:
    text = README.read_text(encoding="utf-8")
    for phrase in [
        "npm run check:shared",
        "npm run check:fixtures",
        "npm run check:golden",
        "npm run check:release",
        "npm run check:maintenance",
        "npm run check:models",
        "npm run check:context",
        "npm run generate:context",
        "Release",
        "package.json` version",
    ]:
        require(phrase in text, f"README.md missing release/check phrase: {phrase}")
    require(version in load_package().get("version", ""), "internal version readback failed")


def check_skill_versions(version: str) -> None:
    for name in EXPECTED_SKILLS:
        skill_md = SKILLS_DIR / name / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        require(text.startswith("---"), f"{name}/SKILL.md must start with YAML frontmatter")
        parts = text.split("---", 2)
        require(len(parts) >= 3, f"{name}/SKILL.md frontmatter is malformed")
        frontmatter = parts[1]
        match = SKILL_VERSION_RE.search(frontmatter)
        require(match is not None, f"{name}/SKILL.md frontmatter is missing a version field")
        skill_version = match.group("version").strip().strip("\"'")
        require(
            skill_version == version,
            f"{name}/SKILL.md version {skill_version!r} must match package version {version!r}",
        )


def check_installer(version: str) -> None:
    listed = run(["node", INSTALLER.as_posix(), "--list"]).stdout.strip().splitlines()
    require(listed == EXPECTED_SKILLS, f"installer --list mismatch: {listed!r}")
    reported_version = run(["node", INSTALLER.as_posix(), "--version"]).stdout.strip()
    require(reported_version == version, f"installer --version must be {version!r}, got {reported_version!r}")
    dry_run = run(["node", INSTALLER.as_posix(), "--dest", "/tmp/codex-goal-release-check", "--dry-run"]).stdout
    for name in EXPECTED_SKILLS + EXPECTED_SUPPORT_DIRS:
        require(name in dry_run, f"installer --dry-run output missing {name}")
    for name in EXPECTED_METADATA_FILES:
        require(name in dry_run, f"installer --dry-run output missing metadata file {name}")

    with tempfile.TemporaryDirectory(prefix="goal-release-install-") as tmp:
        dest = Path(tmp) / "skills"
        run(["node", INSTALLER.as_posix(), "--dest", dest.as_posix(), "--force"])
        for name in EXPECTED_SKILLS + EXPECTED_SUPPORT_DIRS:
            require((dest / name).is_dir(), f"installer did not create {name}")
        for name in EXPECTED_METADATA_FILES:
            require((dest / name).is_file(), f"installer did not create metadata file {name}")
        for name in EXPECTED_SKILLS + EXPECTED_SUPPORT_DIRS:
            run(["diff", "-qr", (ROOT / "skills" / name).as_posix(), (dest / name).as_posix()])
        for name in EXPECTED_METADATA_FILES:
            run(["diff", "-q", (ROOT / name).as_posix(), (dest / name).as_posix()])


def check_pack(version: str) -> None:
    result = run(["npm", "pack", "--dry-run", "--json"])
    try:
        packed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(result.stdout, file=sys.stderr)
        raise SystemExit(f"npm pack --dry-run --json did not return JSON: {exc}") from exc
    require(isinstance(packed, list) and len(packed) == 1 and isinstance(packed[0], dict), "npm pack JSON must contain one package object")
    package = packed[0]
    require(package.get("name") == "codex-goal-orchestration-skills", "packed package name mismatch")
    require(package.get("version") == version, "packed package version mismatch")
    require(package.get("filename") == f"codex-goal-orchestration-skills-{version}.tgz", "packed filename mismatch")
    files = package.get("files")
    require(isinstance(files, list) and files, "packed package must contain files")
    paths = {item.get("path") for item in files if isinstance(item, dict)}
    missing = sorted(REQUIRED_PACKAGE_FILES - paths)
    require(not missing, f"packed package is missing required files: {', '.join(missing)}")
    forbidden = sorted(
        path
        for path in paths
        if isinstance(path, str)
        and ("__pycache__" in path or path.endswith((".pyc", ".pyo")) or path.startswith(".github/"))
    )
    require(not forbidden, f"packed package contains forbidden generated/private files: {', '.join(forbidden)}")
    require(package.get("entryCount") == len(files), "packed entryCount must match files length")


def check_git_clean() -> None:
    result = run(["git", "status", "--porcelain", "--untracked-files=all"])
    if result.stdout.strip():
        print(result.stdout, file=sys.stderr)
        raise SystemExit("release mode requires a clean git tree")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-clean", action="store_true", help="Fail unless git status is clean; use before tagging.")
    args = parser.parse_args()

    require(shutil.which("node") is not None, "node must be available")
    require(shutil.which("npm") is not None, "npm must be available")
    package = load_package()
    version = check_metadata(package)
    check_readme(version)
    check_skill_versions(version)
    check_installer(version)
    check_pack(version)
    if args.require_clean:
        check_git_clean()
    print(f"status=pass version={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
