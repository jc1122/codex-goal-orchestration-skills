#!/usr/bin/env python3
"""Report dependency-policy drift for maintainer and agent workflows."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "maintenance" / "dependency-policy.json"
PACKAGE_JSON = ROOT / "package.json"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
RUNTIME_DEPENDENCY_SECTIONS = (
    "dependencies",
    "optionalDependencies",
    "peerDependencies",
    "bundledDependencies",
    "bundleDependencies",
)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid {label} JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{label} must be a JSON object")
    return data


def parse_dependabot(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    entries: set[tuple[str, str]] = set()
    ecosystem: str | None = None
    directory: str | None = None
    key_re = re.compile(r"^\s*(?:-\s*)?(package-ecosystem|directory):\s*[\"']?([^\"'#]+)[\"']?\s*(?:#.*)?$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = key_re.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "package-ecosystem":
            if ecosystem and directory:
                entries.add((ecosystem, directory))
            ecosystem = value
            directory = None
        elif key == "directory":
            directory = value
            if ecosystem:
                entries.add((ecosystem, directory))
    if ecosystem and directory:
        entries.add((ecosystem, directory))
    return entries


def dependency_names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(str(name) for name in value)
    if isinstance(value, list):
        return sorted(str(name) for name in value)
    return []


def collect_policy_report(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("schema_version") != 1:
        raise SystemExit("dependency policy schema_version must be 1")
    package = load_json(PACKAGE_JSON, "package.json")
    allowlist_raw = policy.get("runtime_npm_dependency_allowlist", [])
    if not isinstance(allowlist_raw, list):
        raise SystemExit("runtime_npm_dependency_allowlist must be an array")
    allowlist = {str(item) for item in allowlist_raw}

    warnings: list[dict[str, Any]] = []
    runtime_dependencies: dict[str, list[str]] = {}
    for section in RUNTIME_DEPENDENCY_SECTIONS:
        names = dependency_names(package.get(section))
        runtime_dependencies[section] = names
        unapproved = [name for name in names if name not in allowlist]
        if unapproved:
            warnings.append(
                {
                    "severity": "high",
                    "rule": "runtime-npm-dependencies",
                    "message": f"{section} contains unapproved runtime dependencies: {', '.join(unapproved)}",
                    "section": section,
                    "dependencies": unapproved,
                }
            )

    dev_dependencies = dependency_names(package.get("devDependencies"))
    if dev_dependencies and not (ROOT / "package-lock.json").exists():
        warnings.append(
            {
                "severity": "high",
                "rule": "npm-lockfile",
                "message": "package.json has devDependencies but package-lock.json is missing",
            }
        )

    required_manifests = policy.get("required_manifests", [])
    if not isinstance(required_manifests, list):
        raise SystemExit("required_manifests must be an array")
    for rel in required_manifests:
        if not (ROOT / str(rel)).exists():
            warnings.append(
                {
                    "severity": "info",
                    "rule": "required-manifest",
                    "message": f"required dependency manifest is missing: {rel}",
                    "path": str(rel),
                }
            )

    dependabot_entries = parse_dependabot(DEPENDABOT)
    required_updates = policy.get("required_dependabot_updates", [])
    if not isinstance(required_updates, list):
        raise SystemExit("required_dependabot_updates must be an array")
    for item in required_updates:
        if not isinstance(item, dict):
            raise SystemExit("required_dependabot_updates entries must be objects")
        ecosystem = str(item.get("package_ecosystem", ""))
        directory = str(item.get("directory", ""))
        if not ecosystem or not directory:
            raise SystemExit("required_dependabot_updates entries require package_ecosystem and directory")
        if (ecosystem, directory) not in dependabot_entries:
            warnings.append(
                {
                    "severity": "high",
                    "rule": "dependabot-coverage",
                    "message": f"Dependabot is missing {ecosystem} updates for {directory}",
                    "package_ecosystem": ecosystem,
                    "directory": directory,
                }
            )

    return {
        "status": "warn" if warnings else "pass",
        "warnings": warnings,
        "runtime_dependencies": runtime_dependencies,
        "runtime_npm_dependency_allowlist": sorted(allowlist),
        "dev_dependencies": dev_dependencies,
        "dependabot_entries": [
            {"package_ecosystem": ecosystem, "directory": directory}
            for ecosystem, directory in sorted(dependabot_entries)
        ],
        "recommended_actions": recommended_actions(warnings),
    }


def recommended_actions(warnings: list[dict[str, Any]]) -> list[str]:
    if not warnings:
        return ["No dependency-policy action needed."]
    actions = []
    if any(item.get("rule") == "runtime-npm-dependencies" for item in warnings):
        actions.append(
            "Avoid runtime dependencies; if one is necessary, add it to the policy allowlist with a rationale."
        )
    if any(item.get("rule") == "dependabot-coverage" for item in warnings):
        actions.append("Add missing ecosystems to .github/dependabot.yml before relying on automated dependency PRs.")
    if any(item.get("rule") == "npm-lockfile" for item in warnings):
        actions.append("Regenerate package-lock.json after changing npm devDependencies.")
    return actions


def print_human(report: dict[str, Any]) -> None:
    print(f"status={report['status']}")
    print(f"dev_dependencies={len(report['dev_dependencies'])}")
    runtime_count = sum(len(items) for items in report["runtime_dependencies"].values())
    print(f"runtime_dependencies={runtime_count}")
    print(f"dependabot_entries={len(report['dependabot_entries'])}")
    if report["warnings"]:
        print("warnings:")
        for item in report["warnings"]:
            print(f"- [{item['severity']}] {item['message']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="Path to dependency policy JSON.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--fail-on-warnings", action="store_true", help="Return nonzero when warnings are present.")
    args = parser.parse_args()

    policy = load_json(args.policy, "dependency policy")
    report = collect_policy_report(policy)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if args.fail_on_warnings and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
