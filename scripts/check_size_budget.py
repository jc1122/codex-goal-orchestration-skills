#!/usr/bin/env python3
"""Report tracked repository size against a committed maintenance budget."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = ROOT / "maintenance" / "size-budget.json"
EXECUTABLE_SUFFIXES = {".js", ".py"}
SIZE_METRICS = ("files", "lines", "chars")
FILE_SIZE_METRICS = ("chars",)
DEFAULT_THRESHOLDS = {
    "growth_warn_ratio": 0.0,
    "growth_high_ratio": 0.02,
    "growth_review_ratio": 0.05,
}
WARNING_PRINT_LIMIT = 12
WARNING_SEVERITY_ORDER = {"review": 0, "high": 1, "info": 2}
WARNING_GROUP_ORDER = {"largest_files": 0, "scopes": 1, "per_skill": 2}
GENERATED_LARGEST_FILE_EXCLUDES = {
    "maintenance/agent-context-index.json",
}


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(f"command failed with {result.returncode}: {' '.join(command)}")
    return result


def git_paths(*args: str) -> list[Path]:
    output = run(["git", "ls-files", "-z", *args]).stdout
    return [ROOT / item for item in output.split("\0") if item]


def tracked_paths(*, include_untracked: bool = False) -> list[Path]:
    paths = git_paths()
    if include_untracked:
        paths.extend(git_paths("--others", "--exclude-standard"))
    return sorted(set(paths))


def count_file(path: Path) -> dict[str, int]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return {
        "files": 1,
        "lines": text.count("\n"),
        "chars": len(text),
        "bytes": len(raw),
    }


def empty_stats() -> dict[str, int]:
    return {"files": 0, "lines": 0, "chars": 0, "bytes": 0}


def add_stats(total: dict[str, int], item: dict[str, int]) -> None:
    for key in ("files", "lines", "chars", "bytes"):
        total[key] += int(item[key])


def finalize_stats(stats: dict[str, int]) -> dict[str, int]:
    finalized = dict(stats)
    finalized["approx_tokens"] = round(finalized["chars"] / 4)
    return finalized


def collect_current(*, include_untracked: bool = False) -> dict[str, Any]:
    paths = tracked_paths(include_untracked=include_untracked)
    file_stats: dict[str, dict[str, int]] = {}
    scopes = {
        "tracked": empty_stats(),
        "skills": empty_stats(),
        "executable": empty_stats(),
        "skill_python": empty_stats(),
    }
    per_skill: dict[str, dict[str, int]] = {}
    skill_docs: dict[str, dict[str, int]] = {}

    for path in paths:
        rel = path.relative_to(ROOT).as_posix()
        stats = count_file(path)
        file_stats[rel] = stats
        add_stats(scopes["tracked"], stats)
        if rel.startswith("skills/"):
            add_stats(scopes["skills"], stats)
            parts = rel.split("/")
            if len(parts) >= 2:
                per_skill.setdefault(parts[1], empty_stats())
                add_stats(per_skill[parts[1]], stats)
                if len(parts) == 3 and parts[2] == "SKILL.md":
                    skill_docs[parts[1]] = finalize_stats(stats)
            if path.suffix == ".py":
                add_stats(scopes["skill_python"], stats)
        if path.suffix in EXECUTABLE_SUFFIXES:
            add_stats(scopes["executable"], stats)

    largest_files = sorted(
        (
            {"path": rel, **finalize_stats(stats)}
            for rel, stats in file_stats.items()
            if rel not in GENERATED_LARGEST_FILE_EXCLUDES
        ),
        key=lambda item: (item["chars"], item["lines"], item["path"]),
        reverse=True,
    )[:15]

    return {
        "scopes": {name: finalize_stats(stats) for name, stats in scopes.items()},
        "per_skill": {name: finalize_stats(stats) for name, stats in sorted(per_skill.items())},
        "skill_docs": dict(sorted(skill_docs.items())),
        "largest_files": largest_files,
    }


def load_budget(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing size budget: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid size budget JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("size budget must be a JSON object")
    if data.get("schema_version") != 1:
        raise SystemExit("size budget schema_version must be 1")
    if not isinstance(data.get("scopes"), dict):
        raise SystemExit("size budget missing scopes object")
    if not isinstance(data.get("per_skill"), dict):
        raise SystemExit("size budget missing per_skill object")
    if not isinstance(data.get("largest_files"), list):
        raise SystemExit("size budget missing largest_files list")
    return data


def write_budget(path: Path, current: dict[str, Any]) -> None:
    payload = {
        "schema_version": 1,
        "mode": "warn",
        "thresholds": DEFAULT_THRESHOLDS,
        "notes": [
            "Budget is compared against git-tracked files only.",
            "approx_tokens is a rough chars/4 estimate for planning, not tokenizer-exact accounting.",
            "Use scripts/check_size_budget.py --update only when growth is intentional.",
        ],
        **current,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def severity_for_ratio(ratio: float, thresholds: dict[str, float]) -> str:
    if ratio >= thresholds["growth_review_ratio"]:
        return "review"
    if ratio >= thresholds["growth_high_ratio"]:
        return "high"
    return "info"


def compare_group(
    *,
    group: str,
    current: dict[str, dict[str, int]],
    baseline: dict[str, Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for name, stats in sorted(current.items()):
        expected = baseline.get(name)
        if not isinstance(expected, dict):
            warnings.append(
                {
                    "severity": "high",
                    "group": group,
                    "name": name,
                    "message": f"{group}.{name} is not present in the size budget",
                }
            )
            continue
        for metric in SIZE_METRICS:
            actual = int(stats[metric])
            budgeted = int(expected.get(metric, 0))
            delta = actual - budgeted
            if delta <= 0:
                continue
            ratio = delta / max(budgeted, 1)
            if ratio >= thresholds["growth_warn_ratio"]:
                warnings.append(
                    {
                        "severity": severity_for_ratio(ratio, thresholds),
                        "group": group,
                        "name": name,
                        "metric": metric,
                        "budget": budgeted,
                        "actual": actual,
                        "delta": delta,
                        "growth_ratio": round(ratio, 6),
                        "message": f"{group}.{name}.{metric} grew by {delta} ({ratio:.2%})",
                    }
                )
    return warnings


def compare_largest_files(
    *,
    current: list[dict[str, Any]],
    baseline: list[Any],
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    baseline_by_path = {
        str(item.get("path")): (rank, item)
        for rank, item in enumerate(baseline, start=1)
        if isinstance(item, dict) and item.get("path")
    }
    for current_rank, stats in enumerate(current, start=1):
        path = str(stats.get("path", ""))
        baseline_item = baseline_by_path.get(path)
        if baseline_item is None:
            warnings.append(
                {
                    "severity": "review",
                    "group": "largest_files",
                    "name": path,
                    "current_rank": current_rank,
                    "message": f"largest_files.{path} entered the current top-file set at rank {current_rank}",
                }
            )
            continue
        budgeted_rank, expected = baseline_item
        for metric in FILE_SIZE_METRICS:
            actual = int(stats.get(metric, 0))
            budgeted = int(expected.get(metric, 0))
            delta = actual - budgeted
            if delta <= 0:
                continue
            ratio = delta / max(budgeted, 1)
            if ratio >= thresholds["growth_warn_ratio"]:
                warnings.append(
                    {
                        "severity": severity_for_ratio(ratio, thresholds),
                        "group": "largest_files",
                        "name": path,
                        "metric": metric,
                        "budget": budgeted,
                        "actual": actual,
                        "delta": delta,
                        "growth_ratio": round(ratio, 6),
                        "budgeted_rank": budgeted_rank,
                        "current_rank": current_rank,
                        "message": f"largest_files.{path}.{metric} grew by {delta} ({ratio:.2%})",
                    }
                )
    return warnings


def make_report(current: dict[str, Any], budget: dict[str, Any]) -> dict[str, Any]:
    thresholds_raw = budget.get("thresholds", DEFAULT_THRESHOLDS)
    if not isinstance(thresholds_raw, dict):
        raise SystemExit("size budget thresholds must be an object")
    thresholds = {
        "growth_warn_ratio": float(thresholds_raw.get("growth_warn_ratio", DEFAULT_THRESHOLDS["growth_warn_ratio"])),
        "growth_high_ratio": float(thresholds_raw.get("growth_high_ratio", DEFAULT_THRESHOLDS["growth_high_ratio"])),
        "growth_review_ratio": float(
            thresholds_raw.get("growth_review_ratio", DEFAULT_THRESHOLDS["growth_review_ratio"])
        ),
    }
    warnings = []
    warnings.extend(
        compare_group(group="scopes", current=current["scopes"], baseline=budget["scopes"], thresholds=thresholds)
    )
    warnings.extend(
        compare_group(
            group="per_skill", current=current["per_skill"], baseline=budget["per_skill"], thresholds=thresholds
        )
    )
    warnings.extend(
        compare_largest_files(
            current=current["largest_files"],
            baseline=budget["largest_files"],
            thresholds=thresholds,
        )
    )
    return {
        "status": "warn" if warnings else "pass",
        "mode": budget.get("mode", "warn"),
        "budget_path": DEFAULT_BUDGET.relative_to(ROOT).as_posix(),
        "thresholds": thresholds,
        "warnings": warnings,
        **current,
        "recommended_actions": recommended_actions(warnings),
    }


def recommended_actions(warnings: list[dict[str, Any]]) -> list[str]:
    if not warnings:
        return ["No size-budget action needed."]
    actions = [
        "Review largest_files before adding more prompt or validator surface.",
        "Prefer moving repeated policy text into shared references or deterministic validators.",
    ]
    if any(item.get("group") == "largest_files" for item in warnings):
        actions.append("Named top-file growth is present; reduce or justify those files before refreshing the budget.")
    if any(item.get("severity") == "review" for item in warnings):
        actions.append("Growth exceeded the review threshold; update the budget only with an intentional rationale.")
    return actions


def warning_priority(item: dict[str, Any]) -> tuple[int, int, float, str]:
    return (
        WARNING_SEVERITY_ORDER.get(str(item.get("severity")), 99),
        WARNING_GROUP_ORDER.get(str(item.get("group")), 99),
        -float(item.get("growth_ratio", 0.0)),
        str(item.get("message", "")),
    )


def warning_counts(warnings: list[dict[str, Any]], key: str) -> str:
    counts: dict[str, int] = {}
    for item in warnings:
        value = str(item.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return " ".join(f"{name}={counts[name]}" for name in sorted(counts))


def print_human(report: dict[str, Any], *, verbose: bool = False) -> None:
    print(f"status={report['status']}")
    for name, stats in report["scopes"].items():
        print(
            f"{name}: files={stats['files']} lines={stats['lines']} "
            f"chars={stats['chars']} approx_tokens={stats['approx_tokens']}"
        )
    warnings = report["warnings"]
    if warnings:
        print("warnings:")
        print(f"- total={len(warnings)} severity: {warning_counts(warnings, 'severity')}")
        print(f"- groups: {warning_counts(warnings, 'group')}")
        shown = sorted(warnings, key=warning_priority) if not verbose else warnings
        if not verbose:
            shown = shown[:WARNING_PRINT_LIMIT]
        for item in shown:
            print(f"- [{item['severity']}] {item['message']}")
        hidden = len(warnings) - len(shown)
        if hidden > 0:
            print(f"- ... {hidden} more warnings hidden; use --json or --verbose for full detail")
    print("largest_files:")
    for item in report["largest_files"][:5]:
        print(f"- {item['path']}: lines={item['lines']} chars={item['chars']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET, help="Path to the committed size budget.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--update", action="store_true", help="Rewrite the budget to the current tracked-file baseline."
    )
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Include untracked non-ignored files; intended for creating a baseline before the first guardrail commit.",
    )
    parser.add_argument(
        "--fail-on-warnings", action="store_true", help="Return nonzero when growth warnings are present."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print every human-readable warning; --json always includes all."
    )
    args = parser.parse_args()

    current = collect_current(include_untracked=args.include_untracked)
    if args.update:
        write_budget(args.budget, current)
        budget = load_budget(args.budget)
    else:
        budget = load_budget(args.budget)
    report = make_report(current, budget)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report, verbose=args.verbose)
    if args.fail_on_warnings and report["warnings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
