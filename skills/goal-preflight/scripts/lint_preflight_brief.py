#!/usr/bin/env python3
"""Lint a structured preflight brief before bundle generation."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
import contextlib


def _load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load required module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, path.parent.as_posix())
    try:
        spec.loader.exec_module(module)
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(path.parent.as_posix())
    return module


SCRIPT_DIR = Path(__file__).resolve().parent
SHARED_SCRIPTS = SCRIPT_DIR.parents[1] / "_goal_shared" / "scripts"
PATH_RULES = _load_module("goal_shared_path_rules", SHARED_SCRIPTS / "path_rules.py")
CREATE_GOAL_BUNDLE = _load_module("goal_preflight_create_goal_bundle", SCRIPT_DIR / "create_goal_bundle.py")

resolve_absolute_path = PATH_RULES.resolve_absolute_path
is_repo_relative_path = PATH_RULES.is_repo_relative_path

SEVERITY_ORDER = {"minor": 1, "major": 2, "critical": 3}
# The angle-bracket arm matches fill-in placeholders like <your goal> or <JOB_ID>
# while excluding comparison operators ("a < b ... >"), "<=" forms, and emails
# (<x@y.com>): the first inner char may not be whitespace/operator and the body
# may not contain '@'.
PLACEHOLDER_RE = re.compile(r"(<(?!bundle\b)(?![=<>\s/@])[^>\n@]*>|\b(?:TODO|TBD|FIXME|XXX)\b|\?\?\?)", re.IGNORECASE)
WEAK_DOD_RE = re.compile(
    r"^(done|complete|completed|implemented|works|working|tests pass|fix it|ship it|as needed)\.?$", re.IGNORECASE
)
COMMAND_START_RE = re.compile(r"^[A-Za-z0-9_./-]+(?:\s+|$)")
NEGATIVE_STATE_RE = re.compile(
    r"\b(partial|blocked|failed|negative|unresolved|unsupported|probe-only|preserve)\b", re.IGNORECASE
)
EXACT_SOURCE_RE = re.compile(
    r"\b("
    r"exact\s+(?:operation\s+)?(?:list|instance|source|payload|dataset|benchmark|data)|"
    r"provided\s+in\s+this\s+brief|"
    r"matching\s+the\s+exact|"
    r"matches\s+the\s+exact|"
    r"source[-\s]+of[-\s]+truth|"
    r"exact\s+FT\d+"
    r")\b",
    re.IGNORECASE,
)
INLINE_SOURCE_TUPLE_RE = re.compile(r"\(\s*\d+\s*,\s*\d+\s*\)")
RUNTIME_CAP_RE = re.compile(
    r"\b(runtime|time|wall[-\s]*clock)\s+cap\b|\bwithin\s+the\s+(?:chosen\s+)?(?:runtime\s+)?cap\b", re.IGNORECASE
)
PROMOTED_CONTEXT_ATTACHMENT_MIN_BYTES = 8192
PROMOTED_CONTEXT_ATTACHMENT_MIN_USES = 2


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"brief must be a JSON object: {path}")
    return data


def defect(defects: list[dict], path: str, severity: str, message: str) -> None:
    defects.append({"path": path, "severity": severity, "message": message})


def walk_strings(value: object, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child = f"{path}.{key_text}" if path else key_text
            yield from walk_strings(item, child)


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def collected_text(value: object) -> str:
    return "\n".join(text for _, text in walk_strings(value, "$"))


def has_source_attachment(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(item, (str, dict)) for item in value)


def has_inline_source_payload(text: str) -> bool:
    tuple_count = len(INLINE_SOURCE_TUPLE_RE.findall(text))
    if tuple_count >= 10:
        return True
    if len(text) < 400:
        return False
    lowered = text.lower()
    return "ft10 = [" in lowered or ("operation list" in lowered and text.count("[") >= 4 and text.count("]") >= 4)


def promotable_context_sources(brief: dict, repo_root: Path | None) -> list[str]:
    if repo_root is None:
        return []
    counts: dict[str, int] = {}
    branches = brief.get("branches")
    if not isinstance(branches, list):
        return []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        work_items = branch.get("work_items")
        if not isinstance(work_items, list):
            continue
        for item in work_items:
            if not isinstance(item, dict):
                continue
            context_files = item.get("context_files")
            if not isinstance(context_files, list):
                continue
            for value in context_files:
                if isinstance(value, str) and value.strip():
                    counts[value.strip()] = counts.get(value.strip(), 0) + 1
    promotable = []
    for rel_path, count in counts.items():
        if count < PROMOTED_CONTEXT_ATTACHMENT_MIN_USES:
            continue
        target = repo_root / rel_path
        if target.is_file() and target.stat().st_size >= PROMOTED_CONTEXT_ATTACHMENT_MIN_BYTES:
            promotable.append(rel_path)
    return promotable


def repo_is_git(repo_root: Path | None) -> bool:
    if repo_root is None:
        return False
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "rev-parse", "--is-inside-work-tree"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def git_tracks_path(repo_root: Path, rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "ls-files", "--error-unmatch", "--", rel_path],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def concrete_runtime_cap(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and bool(re.search(r"\d", value))
    if isinstance(value, dict):
        if not value:
            return False
        return any(
            isinstance(item, int | float)
            and not isinstance(item, bool)
            and item > 0
            or isinstance(item, str)
            and item.strip()
            and bool(re.search(r"\d", item))
            for item in value.values()
        )
    return False


def lint_placeholders(defects: list[dict], brief: dict) -> None:
    for path, text in walk_strings(brief, "$"):
        if PLACEHOLDER_RE.search(text):
            defect(defects, path, "major", "contains placeholder text")


def lint_policy(defects: list[dict], brief: dict) -> None:
    policy_texts: dict[str, str] = {}
    for field in ["artifact_policy", "cleanup_policy"]:
        text = brief.get(field)
        if not isinstance(text, str) or not text.strip():
            defect(defects, f"$.{field}", "major", "must be supplied explicitly or by deterministic default")
            continue
        policy_texts[field] = text
    if len(policy_texts) != 2:
        return
    combined = "\n".join(policy_texts.values())
    if not NEGATIVE_STATE_RE.search(combined):
        defect(
            defects,
            "$.artifact_policy",
            "major",
            "artifact_policy and cleanup_policy together should preserve partial/blocked/failed or unresolved/negative/probe-only states",
        )


def normalization_defect_path(message: str) -> str:
    if message.startswith("runtime_cap ") or message.startswith("runtime_cap."):
        return "$.runtime_cap"
    if message.startswith("telemetry_policy ") or message.startswith("telemetry_policy."):
        return "$.telemetry_policy"
    if message.startswith("route_policy_degraded_telemetry_waiver ") or message.startswith(
        "route_policy_degraded_telemetry_waiver."
    ):
        return "$.route_policy_degraded_telemetry_waiver"
    return "$"


def lint_goal_surface(defects: list[dict], brief: dict) -> None:
    goal = brief.get("goal")
    if not isinstance(goal, str) or len(goal.split()) < 5 or PLACEHOLDER_RE.search(goal):
        defect(defects, "$.goal", "major", "must be a concrete top-level goal, not a fallback or placeholder")
    source_summary = brief.get("source_summary")
    if not isinstance(source_summary, str) or len(source_summary.split()) < 8 or PLACEHOLDER_RE.search(source_summary):
        defect(defects, "$.source_summary", "major", "must summarize the source report/roadmap/diagnosis concretely")
    for field in ["required_evidence", "final_dod"]:
        values = string_list(brief.get(field))
        if not values:
            defect(defects, f"$.{field}", "major", "must contain at least one falsifiable item")
            continue
        for index, value in enumerate(values):
            if len(value.split()) < 4 or WEAK_DOD_RE.fullmatch(value.strip()) or PLACEHOLDER_RE.search(value):
                defect(defects, f"$.{field}[{index}]", "major", "item is too vague to verify deterministically")


def lint_source_fidelity(defects: list[dict], brief: dict, *, repo_root: Path | None) -> None:
    text = collected_text(brief)
    if not EXACT_SOURCE_RE.search(text):
        return
    if has_source_attachment(brief.get("source_attachments")) or has_inline_source_payload(text):
        return
    if promotable_context_sources(brief, repo_root):
        return
    defect(
        defects,
        "$.source_attachments",
        "critical",
        "exact source/instance/list is referenced but no inline payload or source_attachments entry exists; add a source attachment with SHA or include the exact payload in the brief",
    )


def lint_runtime_cap(defects: list[dict], brief: dict) -> None:
    text = collected_text(brief)
    if not RUNTIME_CAP_RE.search(text):
        return
    if concrete_runtime_cap(brief.get("runtime_cap")):
        return
    defect(
        defects,
        "$.runtime_cap",
        "critical",
        "success criteria reference a runtime cap but no concrete runtime_cap value or CLI flag is declared",
    )


def lint_paths(defects: list[dict], branch: dict, branch_path: str, repo_root: Path | None, *, git_repo: bool) -> None:
    for item_index, item in enumerate(
        branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []
    ):
        if not isinstance(item, dict):
            continue
        item_path = f"{branch_path}.work_items[{item_index}]"
        for key in ["owned_paths", "context_files"]:
            values = item.get(key)
            if values is None:
                continue
            if not isinstance(values, list):
                defect(defects, f"{item_path}.{key}", "critical", "must be an array")
                continue
            for index, value in enumerate(values):
                path = f"{item_path}.{key}[{index}]"
                if not isinstance(value, str) or not value.strip():
                    defect(defects, path, "critical", "must be a non-empty repo-relative path")
                    continue
                if not is_repo_relative_path(value):
                    defect(defects, path, "critical", "must be repo-relative without traversal or backslashes")
                    continue
                if key == "context_files":
                    if repo_root is not None and not (repo_root / value).exists():
                        defect(
                            defects,
                            path,
                            "major",
                            f"context file must already exist under repo root: {value}; put future writable outputs in owned_paths or describe large existing sources in source_attachments",
                        )
                    elif repo_root is not None and git_repo and not git_tracks_path(repo_root, value):
                        defect(
                            defects,
                            path,
                            "major",
                            f"context file exists but is not tracked by git: {value}; add it to the repo or copy it into source_attachments for reproducible runtime context",
                        )
                    continue


def lint_verification_and_dod(defects: list[dict], branch: dict, branch_path: str) -> None:
    work_items = branch.get("work_items")
    if not isinstance(work_items, list) or not work_items:
        defect(defects, f"{branch_path}.work_items", "critical", "must contain at least one work item")
        return
    if len(work_items) > CREATE_GOAL_BUNDLE.MAX_WORKER_PACKETS_PER_BRANCH:
        defect(
            defects,
            f"{branch_path}.work_items",
            "major",
            f"must contain at most {CREATE_GOAL_BUNDLE.MAX_WORKER_PACKETS_PER_BRANCH} work items",
        )
    for item_index, item in enumerate(work_items):
        item_path = f"{branch_path}.work_items[{item_index}]"
        if not isinstance(item, dict):
            defect(defects, item_path, "critical", "must be an object")
            continue
        verification = string_list(item.get("verification"))
        if not verification:
            defect(
                defects, f"{item_path}.verification", "critical", "must include at least one exact verification command"
            )
        for index, command in enumerate(verification):
            if PLACEHOLDER_RE.search(command):
                defect(
                    defects,
                    f"{item_path}.verification[{index}]",
                    "major",
                    "verification command contains placeholder text",
                )
            if not COMMAND_START_RE.search(command):
                defect(
                    defects,
                    f"{item_path}.verification[{index}]",
                    "major",
                    "verification entry should look like an exact shell command",
                )
        dod = string_list(item.get("dod"))
        if not dod:
            defect(defects, f"{item_path}.dod", "critical", "must include at least one falsifiable DoD item")
        for index, item_dod in enumerate(dod):
            if len(item_dod.split()) < 4 or WEAK_DOD_RE.fullmatch(item_dod.strip()):
                defect(
                    defects, f"{item_path}.dod[{index}]", "major", "DoD item is too vague to verify deterministically"
                )
            if PLACEHOLDER_RE.search(item_dod):
                defect(defects, f"{item_path}.dod[{index}]", "major", "DoD item contains placeholder text")
    branch_dod = string_list(branch.get("dod"))
    if branch_dod:
        for index, item_dod in enumerate(branch_dod):
            if len(item_dod.split()) < 4 or WEAK_DOD_RE.fullmatch(item_dod.strip()):
                defect(
                    defects,
                    f"{branch_path}.dod[{index}]",
                    "major",
                    "branch DoD item is too vague to verify deterministically",
                )


def lint_brief(brief: dict, *, repo_root: Path | None) -> list[dict]:
    defects: list[dict] = []
    git_repo = repo_is_git(repo_root)
    normalized: dict | None = None
    try:
        normalized = CREATE_GOAL_BUNDLE.normalize_brief(copy.deepcopy(brief))
    except SystemExit as exc:
        message = str(exc)
        defect(defects, normalization_defect_path(message), "critical", message)
    except Exception as exc:  # noqa: BLE001
        defect(defects, "$", "critical", f"brief normalization failed: {exc}")
    lint_placeholders(defects, brief)
    lint_policy(defects, normalized if normalized is not None else brief)
    lint_goal_surface(defects, brief)
    lint_source_fidelity(defects, brief, repo_root=repo_root)
    lint_runtime_cap(defects, brief)
    branches = brief.get("branches")
    if not isinstance(branches, list):
        defect(defects, "$.branches", "critical", "must be an array")
        return defects
    for branch_index, branch in enumerate(branches):
        branch_path = f"$.branches[{branch_index}]"
        if not isinstance(branch, dict):
            defect(defects, branch_path, "critical", "must be an object")
            continue
        objective = branch.get("objective")
        if not isinstance(objective, str) or len(objective.split()) < 5:
            defect(defects, f"{branch_path}.objective", "major", "branch objective is too vague")
        lint_paths(defects, branch, branch_path, repo_root, git_repo=git_repo)
        lint_verification_and_dod(defects, branch, branch_path)
    return defects


def should_fail(defects: list[dict], fail_on: str) -> bool:
    threshold = SEVERITY_ORDER[fail_on]
    return any(SEVERITY_ORDER.get(str(item.get("severity")), 0) >= threshold for item in defects)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a structured preflight brief before bundle generation.",
        epilog=(
            "For agent-readable brief shape, run --brief-schema-json. "
            "For a valid compact starter brief, run --example-brief."
        ),
    )
    parser.add_argument("--brief")
    parser.add_argument("--repo-root")
    parser.add_argument("--output")
    parser.add_argument("--fail-on", choices=["minor", "major", "critical"], default="major")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--example-brief",
        "--brief-template-json",
        dest="example_brief",
        action="store_true",
        help="Print a valid compact brief JSON template and exit.",
    )
    parser.add_argument(
        "--brief-schema-json",
        action="store_true",
        help="Print an agent-readable brief field guide and exit.",
    )
    args = parser.parse_args()

    if args.example_brief:
        print(json.dumps(CREATE_GOAL_BUNDLE.example_brief(), indent=2, sort_keys=True))
        return 0
    if args.brief_schema_json:
        print(json.dumps(CREATE_GOAL_BUNDLE.brief_schema_summary(), indent=2, sort_keys=True))
        return 0
    if not args.brief:
        parser.print_usage(sys.stderr)
        raise SystemExit("--brief is required unless printing --example-brief or --brief-schema-json")

    brief_path = resolve_absolute_path(args.brief, "--brief", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True) if args.repo_root else None
    defects = lint_brief(read_json(brief_path), repo_root=repo_root)
    status = "failed" if should_fail(defects, args.fail_on) else "pass"
    result = {
        "status": status,
        "brief": brief_path.as_posix(),
        "defect_count": len(defects),
        "defects": defects,
    }
    if args.output:
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={status}")
        for item in defects:
            print(f"- {item['severity']} {item['path']}: {item['message']}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
