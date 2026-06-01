#!/usr/bin/env python3
"""Generate a compact agent navigation index from repository structure."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "maintenance" / "agent-context-index.json"
SKILLS_ROOT = ROOT / "skills"
SYNC_SHARED = ROOT / "scripts" / "sync_goal_shared.py"
SKILL_TOKEN_RE = re.compile(r"(?:\$|skills/)?(goal-[a-z0-9-]+)")
FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n", re.DOTALL)
TEXT_SUFFIXES = {".md", ".py", ".js", ".json", ".yml", ".yaml", ".toml", ".txt"}
LARGE_FILE_HOTSPOT_LIMIT = 5
LARGE_FILE_FUNCTION_LIMIT = 4
EXCLUDED_FROM_FINGERPRINT = {
    "maintenance/agent-context-index.json",
    "maintenance/size-budget.json",
}
BOOTSTRAP_SOURCE_PATHS = {
    "AGENTS.md",
    "scripts/generate_agent_context_index.py",
    "scripts/check_model_catalog.py",
}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(f"command failed with {result.returncode}: {' '.join(command)}")
    return result


def repo_paths() -> list[Path]:
    output = run(["git", "ls-files", "-z"]).stdout
    paths = {ROOT / item for item in output.split("\0") if item}
    for item in BOOTSTRAP_SOURCE_PATHS:
        path = ROOT / item
        if path.exists():
            paths.add(path)
    return sorted(paths)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(read_text(path))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {rel(path)}")
    return data


def parse_sync_shared() -> dict[str, Any]:
    module = ast.parse(read_text(SYNC_SHARED), filename=SYNC_SHARED.as_posix())
    values: dict[str, Any] = {}
    for node in module.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in {"SKILLS", "SHARED_SCRIPTS", "SHARED_REFERENCES"}:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (SyntaxError, ValueError):
            continue
        values[target.id] = list(value)
    missing = {"SKILLS", "SHARED_SCRIPTS", "SHARED_REFERENCES"} - set(values)
    if missing:
        raise SystemExit(f"could not parse sync_goal_shared.py values: {', '.join(sorted(missing))}")
    return {
        "public_skills": sorted(values["SKILLS"]),
        "shared_scripts": sorted(values["SHARED_SCRIPTS"]),
        "shared_references": sorted(values["SHARED_REFERENCES"]),
    }


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = read_text(path)
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SystemExit(f"missing skill frontmatter: {rel(path)}")
    data: dict[str, str] = {}
    for raw_line in match.group("body").splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    if not data.get("name"):
        raise SystemExit(f"skill frontmatter missing name: {rel(path)}")
    return data


def is_shared_wrapper(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    try:
        text = read_text(path)
    except UnicodeDecodeError:
        return False
    return "Dispatch to the shared goal orchestration implementation." in text


def skill_files(skill: str, subdir: str, suffixes: tuple[str, ...]) -> list[str]:
    root = SKILLS_ROOT / skill / subdir
    if not root.exists():
        return []
    return [rel(path) for path in sorted(root.iterdir()) if path.is_file() and path.suffix in suffixes]


def first_sentence(text: str, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    sentence = re.split(r"(?<=[.!?])\s+", compact, maxsplit=1)[0]
    if len(sentence) <= limit:
        return sentence
    cut = sentence.rfind(" ", 0, limit - 3)
    if cut < limit // 2:
        cut = limit - 3
    return sentence[:cut].rstrip(" ,;:.") + "..."


def source_owner(path: Path, public_skills: set[str]) -> str:
    parts = rel(path).split("/")
    if len(parts) >= 2 and parts[0] == "skills":
        return parts[1]
    if parts[0] == "scripts":
        return "repo-scripts"
    if parts[0] == "maintenance":
        return "maintenance"
    if parts[0] == ".github":
        return "ci"
    return "repo"


def large_file_guidance(relative: str) -> str:
    if relative.startswith("skills/") and "/scripts/" in relative:
        return "manifest/help/defects before source"
    if relative.startswith("scripts/check_preparedness_fixtures.py"):
        return "failures/function search; table repeats"
    if relative.startswith("scripts/check_golden_smoke.py"):
        return "readable smoke; consolidate repeats"
    if relative.startswith("scripts/"):
        return "--help or JSON before implementation source"
    return "task routes before direct file reads"


def large_file_functions(path: Path, text: str) -> list[str]:
    if path.suffix != ".py":
        return []
    try:
        module = ast.parse(text, filename=rel(path))
    except SyntaxError:
        return []
    functions: list[tuple[int, int, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None:
            continue
        line_count = node.end_lineno - node.lineno + 1
        functions.append((line_count, node.lineno, node.name))
    selected = sorted(functions, key=lambda item: (item[0], -item[1], item[2]), reverse=True)[:LARGE_FILE_FUNCTION_LIMIT]
    return [f"{line}:{name}:{line_count}l" for line_count, line, name in selected]


def build_large_file_hotspots(paths: list[Path], public_skills: set[str]) -> list[dict[str, Any]]:
    hotspots: list[dict[str, Any]] = []
    for path in paths:
        relative = rel(path)
        if relative in EXCLUDED_FROM_FINGERPRINT or path.suffix not in TEXT_SUFFIXES or not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        chars = len(text)
        hotspots.append(
            {
                "path": relative,
                "owner": source_owner(path, public_skills),
                "lines": text.count("\n"),
                "_chars": chars,
                "approx_tokens": round(chars / 4),
                "rule": large_file_guidance(relative),
                "top_functions": large_file_functions(path, text),
            }
        )
    selected = sorted(hotspots, key=lambda item: (item["_chars"], item["lines"], item["path"]), reverse=True)[
        :LARGE_FILE_HOTSPOT_LIMIT
    ]
    for item in selected:
        item.pop("_chars")
    return selected


def scan_edges(paths: list[Path], public_skills: set[str]) -> list[dict[str, Any]]:
    edge_paths: dict[tuple[str, str], set[str]] = {}
    for path in paths:
        relative = rel(path)
        if path.suffix not in TEXT_SUFFIXES or relative in EXCLUDED_FROM_FINGERPRINT:
            continue
        try:
            text = read_text(path)
        except UnicodeDecodeError:
            continue
        owner = source_owner(path, public_skills)
        if owner not in public_skills:
            continue
        for match in SKILL_TOKEN_RE.finditer(text):
            target = match.group(1)
            if target not in public_skills or target == owner:
                continue
            edge_paths.setdefault((owner, target), set()).add(relative)
        if "_goal_shared" in text and owner != "_goal_shared":
            edge_paths.setdefault((owner, "_goal_shared"), set()).add(relative)

    return [
        {
            "source": source,
            "target": target,
            "paths": sorted(paths),
        }
        for (source, target), paths in sorted(edge_paths.items())
    ]


def detect_skill_dependencies(skill: str, public_skills: set[str]) -> list[str]:
    dependencies: set[str] = set()
    root = SKILLS_ROOT / skill
    for path in sorted((root / "scripts").glob("*.py")):
        text = read_text(path)
        if "_goal_shared" in text:
            dependencies.add("_goal_shared")
        for match in SKILL_TOKEN_RE.finditer(text):
            target = match.group(1)
            if target in public_skills and target != skill:
                dependencies.add(target)

    skill_md = root / "SKILL.md"
    if skill_md.exists():
        for line in read_text(skill_md).splitlines():
            if "--require" not in line:
                continue
            for match in SKILL_TOKEN_RE.finditer(line):
                target = match.group(1)
                if target in public_skills and target != skill:
                    dependencies.add(target)

    for path in skill_files(skill, "assets", (".md",)):
        text = read_text(ROOT / path)
        for match in SKILL_TOKEN_RE.finditer(text):
            target = match.group(1)
            if target in public_skills and target != skill:
                dependencies.add(target)
    return sorted(dependencies)


def source_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        relative = rel(path)
        if relative in EXCLUDED_FROM_FINGERPRINT:
            continue
        if path.is_dir():
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def build_entrypoints(package: dict[str, Any]) -> dict[str, Any]:
    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict):
        scripts = {}
    return {
        "agent_start": ["AGENTS.md", "maintenance/agent-context-index.json"],
        "install": ["bin/install-goal-skills.js", "package.json"],
        "ci": [".github/workflows/ci.yml", "package.json"],
        "dependencies": [".github/dependabot.yml", "maintenance/dependency-policy.json", "package.json", "requirements-dev.txt"],
        "maintenance": [
            "maintenance/agent-context-index.json",
            "maintenance/size-budget.json",
            "maintenance/dependency-policy.json",
            "scripts/generate_agent_context_index.py",
            "scripts/check_size_budget.py",
            "scripts/check_dependency_policy.py",
            "scripts/check_model_catalog.py",
        ],
        "release": ["scripts/check_release.py", "package.json", "bin/install-goal-skills.js"],
        "npm_scripts": {name: command for name, command in sorted(scripts.items())},
    }


def build_tasks() -> dict[str, Any]:
    return {
        "agent_navigation": {
            "read": ["AGENTS.md", "maintenance/agent-context-index.json"],
            "write_candidates": ["maintenance/agent-context-index.json", "scripts/generate_agent_context_index.py"],
            "checks": ["npm run check:context"],
        },
        "bundle_schema": {
            "read": [
                "skills/_goal_shared/scripts/orchestration_contract.py",
                "skills/goal-preflight/references/bundle-contract.md",
            ],
            "commands": [
                "python3 skills/goal-preflight/scripts/runtime_phase_manifest.py --markdown",
                "python3 skills/goal-preflight/scripts/create_goal_bundle.py --help",
                "python3 skills/goal-preflight/scripts/lint_goal_bundle.py --help",
            ],
            "open_scripts_only_for": "bundle schema implementation or script debugging",
            "write_candidates": ["skills/goal-preflight/scripts", "skills/_goal_shared/scripts/orchestration_contract.py"],
            "checks": ["npm run check:fixtures", "npm run check:golden"],
        },
        "runtime_scheduling": {
            "read": [
                "skills/goal-main-orchestrator/SKILL.md",
            ],
            "commands": [
                "python3 skills/goal-main-orchestrator/scripts/runtime_phase_manifest.py --markdown",
                "python3 skills/goal-main-orchestrator/scripts/scheduler_tick.py --help",
                "python3 skills/goal-main-orchestrator/scripts/validate_main_status.py --help",
            ],
            "reference_on_demand": [
                "skills/goal-main-orchestrator/references/main-runtime-contract.md",
                "skills/goal-main-orchestrator/references/prompt-audit-contract.md",
            ],
            "open_scripts_only_for": "scheduler/status validator implementation or script debugging",
            "write_candidates": ["skills/_goal_shared/scripts", "skills/goal-main-orchestrator/scripts"],
            "checks": ["npm run check:fixtures", "npm run check:golden"],
        },
        "branch_execution": {
            "read": [
                "skills/goal-branch-orchestrator/SKILL.md",
            ],
            "commands": [
                "python3 skills/goal-branch-orchestrator/scripts/runtime_phase_manifest.py --markdown",
                "python3 skills/goal-branch-orchestrator/scripts/create_runtime_packet.py --help",
                "python3 skills/goal-branch-orchestrator/scripts/validate_branch_status.py --help",
            ],
            "reference_on_demand": ["skills/goal-branch-orchestrator/references/branch-runtime-contract.md"],
            "open_scripts_only_for": "packet/status validator implementation or script debugging",
            "write_candidates": ["skills/goal-branch-orchestrator/scripts"],
            "checks": ["npm run check:fixtures", "npm run check:golden"],
        },
        "amendments": {
            "read": [
                "skills/goal-plan-amender/SKILL.md",
            ],
            "commands": [
                "python3 skills/goal-plan-amender/scripts/runtime_phase_manifest.py --markdown",
                "python3 skills/goal-plan-amender/scripts/recommend_amendment_decision.py --help",
                "python3 skills/goal-plan-amender/scripts/validate_manifest_amendment.py --help",
            ],
            "reference_on_demand": ["skills/goal-plan-amender/references/amendment-contract.md"],
            "open_scripts_only_for": "amendment implementation or script debugging",
            "write_candidates": ["skills/goal-plan-amender/scripts"],
            "checks": ["npm run check:fixtures", "npm run check:golden"],
        },
        "release_install": {
            "read": ["package.json", "bin/install-goal-skills.js", "scripts/check_release.py"],
            "write_candidates": ["package.json", "bin/install-goal-skills.js", "scripts/check_release.py"],
            "checks": ["npm run check:release"],
        },
        "maintenance_guardrails": {
            "read": [
                "maintenance/AGENT_MAINTENANCE.md",
                "scripts/check_size_budget.py",
                "scripts/check_dependency_policy.py",
                "scripts/generate_agent_context_index.py",
                "scripts/check_model_catalog.py",
            ],
            "write_candidates": [
                "maintenance",
                "scripts/check_size_budget.py",
                "scripts/check_dependency_policy.py",
                "scripts/check_model_catalog.py",
            ],
            "checks": ["npm run check:maintenance"],
        },
    }


def build_index() -> dict[str, Any]:
    paths = repo_paths()
    package = read_json(ROOT / "package.json")
    sync = parse_sync_shared()
    public_skills = set(sync["public_skills"])

    skills: dict[str, Any] = {}
    for skill in sync["public_skills"]:
        skill_md = SKILLS_ROOT / skill / "SKILL.md"
        frontmatter = parse_frontmatter(skill_md)
        read_first = [rel(skill_md)]
        references = skill_files(skill, "references", (".md",))
        assets = skill_files(skill, "assets", (".md",))
        scripts = [
            rel(path)
            for path in sorted((SKILLS_ROOT / skill / "scripts").glob("*.py"))
            if not is_shared_wrapper(path)
        ]
        skills[skill] = {
            "role": first_sentence(frontmatter.get("description", "")),
            "read_first": read_first,
            "phase_manifest_command": f"python3 skills/{skill}/scripts/runtime_phase_manifest.py --markdown",
            "reference_on_demand": references,
            "assets_on_demand": assets,
            "core_scripts": scripts,
            "open_core_scripts_only_for": "implementation or debugging of that script surface",
            "depends_on": detect_skill_dependencies(skill, public_skills),
        }

    edges = scan_edges(paths, public_skills)

    shared_scripts = [f"skills/_goal_shared/scripts/{name}" for name in sync["shared_scripts"]]
    shared_references = [f"skills/_goal_shared/references/{name}" for name in sync["shared_references"]]

    return {
        "schema_version": 1,
        "generated_from": {
            "source_fingerprint": source_fingerprint(paths),
        },
        "navigation_rules": [
            "Read this file before broad repository scans.",
            "Prefer tasks.<task>.read for first context.",
            "Use skills.<skill>.read_first for skill-specific work.",
            "Run skills.<skill>.phase_manifest_command for runtime flow before opening detailed references.",
            "Open core_scripts only when implementing or debugging that surface.",
            "During runtime orchestration, use script outputs and validator defects before reading Python source.",
            "Run npm run generate:context after moving, adding, or deleting navigation-relevant files.",
        ],
        "large_file_hotspots": build_large_file_hotspots(paths, public_skills),
        "entrypoints": build_entrypoints(package),
        "skills": skills,
        "shared": {
            "_goal_shared": {
                "read_first": [
                    "skills/_goal_shared/references/lite-advisor-contract.md",
                ],
                "shared_scripts": shared_scripts,
                "shared_references": shared_references,
                "open_shared_scripts_only_for": "implementation or debugging of shared deterministic helpers",
                "wrapped_by_public_skills": sync["public_skills"],
            }
        },
        "edges": edges,
        "tasks": build_tasks(),
    }


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Rewrite maintenance/agent-context-index.json.")
    parser.add_argument("--check", action="store_true", help="Fail when the generated index differs from the committed file.")
    parser.add_argument("--json", action="store_true", help="Print the generated index JSON.")
    args = parser.parse_args()

    if sum(bool(item) for item in (args.write, args.check, args.json)) != 1:
        raise SystemExit("choose exactly one of --write, --check, or --json")

    output = stable_json(build_index())
    if args.json:
        print(output, end="")
        return 0
    if args.write:
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        INDEX.write_text(output, encoding="utf-8")
        print(f"status=pass wrote={rel(INDEX)}")
        return 0
    if not INDEX.exists():
        raise SystemExit(f"missing generated index: {rel(INDEX)}")
    actual = INDEX.read_text(encoding="utf-8")
    if actual != output:
        print(f"status=failed path={rel(INDEX)}")
        print("Run: npm run generate:context")
        return 1
    print(f"status=pass path={rel(INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
