#!/usr/bin/env python3
"""Deterministically lint a goal preflight bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shlex
import re
import subprocess
from pathlib import Path
from typing import NamedTuple


PREFLIGHT_LITE_PURPOSES = {"preflight-decomposition", "lint-repair"}
LITE_STATUSES = {"ok", "partial", "blocked"}
LITE_DISPOSITIONS = {"unused", "used", "ignored"}
LITE_VALIDATION_STATUSES = {"pass", "failed"}


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
SAFE_ID_RE = PATH_RULES.SAFE_ID_RE
SAFE_LABEL_RE = PATH_RULES.SAFE_LABEL_RE
is_strict_int = PATH_RULES.is_strict_int
resolve_absolute_path = PATH_RULES.resolve_absolute_path
resolve = PATH_RULES.resolve
relative_path_defect = PATH_RULES.relative_path_defect
safe_branch_name = PATH_RULES.safe_branch_name
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS
MAX_WORKER_PACKETS_PER_BRANCH = CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH
MAX_WAVES = CONTRACT.MAX_WAVES
DEFAULT_TOTAL_BRANCH_CAP = CONTRACT.DEFAULT_TOTAL_BRANCH_CAP
DEFAULT_WORKER_LADDER = CONTRACT.worker_ladder_list()
MANIFEST_WORKER_ROUTE_CLASSES = CONTRACT.MANIFEST_WORKER_ROUTE_CLASSES
WORKER_MODEL_POLICY = CONTRACT.WORKER_MODEL_POLICY
AMENDER_MODEL_POLICY = CONTRACT.AMENDER_MODEL_POLICY
LITE_MODEL_POLICY = CONTRACT.LITE_MODEL_POLICY
LITE_ADVISOR_POLICY = CONTRACT.LITE_ADVISOR_POLICY
RESEARCH_WORKER_TYPE = CONTRACT.RESEARCH_WORKER_TYPE
REVIEW_MODEL_POLICY = CONTRACT.REVIEW_MODEL_POLICY
ORCHESTRATION_WATCHDOG = CONTRACT.ORCHESTRATION_WATCHDOG
TELEMETRY_POLICY_SCHEMA_VERSION = CONTRACT.TELEMETRY_POLICY_SCHEMA_VERSION
TELEMETRY_POLICY_MODES = CONTRACT.TELEMETRY_POLICY_MODES
TELEMETRY_COLLECT_ITEMS = CONTRACT.TELEMETRY_COLLECT_ITEMS
PREMIUM_ROUTE_MARKERS = ("demanding", "heavy", "premium", "pro", "gpt-5.5", "gpt-5.4")
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
PATH_TOKEN_EXTENSIONS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".txt",
    ".csv",
    ".sh",
)
PYTHON_BINS = {"python", "python3", "py"}


def cheaper_worker_ladder(default_ladder: list[str]) -> list[str]:
    cheap = [
        alias for alias in default_ladder if not any(marker in str(alias).lower() for marker in PREMIUM_ROUTE_MARKERS)
    ]
    if cheap:
        return cheap[-2:]
    return default_ladder[-1:]


def normalized_worker_policy(policy: dict) -> dict:
    if not isinstance(policy, dict):
        policy = WORKER_MODEL_POLICY
    result = dict(policy)
    default_ladder = result.get("default_ladder")
    if not isinstance(default_ladder, list) or not default_ladder:
        default_ladder = DEFAULT_WORKER_LADDER
    default_ladder = [str(alias) for alias in default_ladder if isinstance(alias, str) and alias]
    allowed_routes = result.get("allowed_routes")
    if not isinstance(allowed_routes, list) or not allowed_routes:
        allowed_routes = default_ladder
    allowed_routes = [str(alias) for alias in allowed_routes if isinstance(alias, str) and alias]
    route_classes = result.get("route_classes")
    normalized_route_classes: dict[str, list[str]] | None = None
    if isinstance(route_classes, dict) and default_ladder and allowed_routes:
        allowed_set = set(allowed_routes)
        candidate: dict[str, list[str]] = {}
        for route_class in MANIFEST_WORKER_ROUTE_CLASSES:
            ladder = route_classes.get(route_class)
            if not isinstance(ladder, list) or not ladder:
                break
            aliases = [alias for alias in ladder if isinstance(alias, str) and alias]
            if len(aliases) != len(ladder):
                break
            if any(alias not in allowed_set for alias in aliases):
                break
            if [alias for alias in default_ladder if alias in aliases] != aliases:
                break
            candidate[route_class] = aliases
        else:
            normalized_route_classes = candidate
    if normalized_route_classes is not None:
        result["route_classes"] = normalized_route_classes
        result["route_class_ladder_source"] = result.get(
            "route_class_ladder_source",
            "goal_config.model_policies.worker_model_policy.route_classes",
        )
    else:
        cheap_ladder = cheaper_worker_ladder(default_ladder)
        cheapest = [cheap_ladder[-1]] if cheap_ladder else [default_ladder[-1]]
        result["route_classes"] = {
            "mechanical": cheapest,
            "docs": cheapest,
            "small-edit": cheap_ladder,
            "normal-code": cheap_ladder,
            "complex-code": default_ladder,
            "custom": default_ladder,
        }
        result["route_class_ladder_source"] = "preflight_deterministic_cheap_subsequences"
    return result


def _git_repo_status(bundle_dir: Path) -> dict:
    result = subprocess.run(
        ["git", "-C", bundle_dir.as_posix(), "rev-parse", "--is-inside-work-tree"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        return {"status": "not_in_repo"}
    branch_result = subprocess.run(
        ["git", "-C", bundle_dir.as_posix(), "branch", "--show-current"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    branch = branch_result.stdout.strip()
    if not branch:
        return {"status": "git_repo_detached", "branch": None}
    return {"status": "pass", "branch": branch}


def _manifest_git_repo_status(manifest: dict, fallback: dict) -> dict:
    status = manifest.get("repo_status")
    if not isinstance(status, dict):
        return fallback
    result = dict(status)
    if "status" not in result:
        if result.get("repo_is_git") is False:
            result["status"] = "not_in_repo"
        elif result.get("base_ref_status") == "missing":
            result["status"] = "base_ref_missing"
        elif result.get("repo_is_git") is True:
            result["status"] = "pass"
        else:
            result["status"] = "unknown"
    return result


def git_tracks_manifest_path(repo_status: dict, rel_path: str) -> bool | None:
    if repo_status.get("repo_is_git") is not True:
        return None
    root = repo_status.get("repo_root")
    if not isinstance(root, str) or not root:
        return None
    result = subprocess.run(
        ["git", "-C", root, "ls-files", "--error-unmatch", "--", rel_path],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def bundle_inside_git_unignored(repo_status: dict, bundle_dir: Path) -> str | None:
    if repo_status.get("repo_is_git") is not True:
        return None
    root = repo_status.get("repo_root")
    if not isinstance(root, str) or not root:
        return None
    try:
        relative = bundle_dir.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return None
    result = subprocess.run(
        ["git", "-C", root, "check-ignore", "-q", relative.as_posix()],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return None if result.returncode == 0 else relative.as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_text(value).encode("utf-8")).hexdigest()


def load_bundle_json(defect, bundle_dir: Path, relative_path: str, label: str) -> dict | None:
    path = bundle_dir / relative_path
    if not path.is_file():
        defect("job.manifest.json", "critical", f"{label} artifact is missing: {relative_path}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        defect(relative_path, "critical", f"{label} must be valid JSON: {exc}")
        return None
    if not isinstance(data, dict):
        defect(relative_path, "critical", f"{label} must be a JSON object")
        return None
    return data


VALIDATOR_COMMAND_STATUS_HINTS = {
    "validate_branch_status.py": "--status /absolute/path/to/bundle/branches/Bxx.status.json",
    "validate_main_status.py": "--status /absolute/path/to/bundle/main.status.json",
}
VALIDATOR_COMMAND_RE = re.compile(r"(?P<script>validate_(?:branch|main)_status\.py)\b(?P<tail>[^`\n]*)")
STATUS_TARGET_PREFIXES = (
    "/absolute/path/to/bundle/",
    "/absolute/path/to/",
    "/abs/bundle/",
    "/abs/",
)
REQUIRED_BUNDLE_DIRS = ("branches",)
MANIFEST_REQUIRED_KEYS = (
    "job_id",
    "title",
    "goal",
    "source_summary",
    "required_evidence",
    "final_dod",
    "main_prompt",
    "runtime_rules_path",
    "runtime_rules_sha256",
    "runtime_index_path",
    "runtime_index_sha256",
    "base_ref",
    "artifact_policy",
    "cleanup_policy",
    "branches",
    "waves",
    "max_active_branch_agents",
    "parallelization",
    "adaptation_policy",
    "worker_model_policy",
    "amender_model_policy",
    "lite_model_policy",
    "lite_advisor_policy",
    "review_model_policy",
    "route_contract",
    "route_contract_sha256",
    "execution_strategy",
    "ownership_feasibility",
    "orchestration_watchdog",
    "preflight_lite_advice",
    "preflight_input_precedence",
)
MAIN_PROMPT_REQUIRED_PHRASES = (
    "manifest paths",
    "repository root",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "skill availability bootstrap",
    "prompt audit",
    "prompt-audit.json",
    "pins this manifest",
    "max_active_branch_agents",
    CONTRACT.MAIN_SCHEDULER_PATH,
    "branch_parallelism.scheduler_path",
    "Parallelism is the default",
    "never exceed 4",
    "Saturate branch orchestrator slots",
    "Launch the next eligible branch",
    "depends_on",
    "waves as scheduling/order groups",
    "do not poll active branch",
    "git diff --check",
    "Cleanup Policy",
    "Artifact Policy",
    "close finished branch orchestrator agents",
    "rolling saturated pool",
    "scheduler ledger",
    "orchestration_watchdog.main_no_completion_wait_limit",
    "validate_branch_status.py --manifest",
    "summarize_telemetry.py --bundle-dir",
    "telemetry.summary.json",
    "validate_main_status.py --manifest",
    "Optional Lite advisors",
)
BOOTLOADER_REQUIRED_PHRASES = (
    "$goal-main-orchestrator",
    "Bundle root",
    "Repository root",
    "job.manifest.json",
    "main.prompt.md",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "skill availability",
    "check_goal_skill_availability.py",
    "absolute paths",
    "pins the manifest",
    "Parallelism is the default",
    "never exceed 4",
    "branch orchestrator slots saturated",
    "depends_on",
    "Waves are dependency-aware scheduling/order groups",
    "1 to 4 worker packets",
    "rolling saturated pool",
)
BRANCH_REQUIRED_KEYS = (
    "id",
    "objective",
    "scope",
    "wave",
    "prompt",
    "branch_name",
    "worktree_path",
    "status_path",
    "review_path",
    "pre_review_gate_path",
    "depends_on",
    "owned_paths",
    "work_items",
    "execution_strategy_ref",
    "route_contract_sha256",
    "max_active_worker_packets",
    "worker_parallelism",
)
BRANCH_PROMPT_PHRASES_BEFORE_SCHEDULER = (
    "Objective",
    "Scope",
    "Depends on branches",
    "Work Items",
    "Runtime rules appendix",
    "Runtime rules sha256",
    "Runtime index",
    "Runtime index sha256",
    "Route contract sha256",
    "Execution strategy",
    "Execution setup commands",
    "Execution validation env",
    "Ownership feasibility",
    "Branch Runtime Parameters",
    "Max active worker packets",
    "Effective worker launch cap",
    "Declared worker packets",
    "Configured package max worker packets per branch",
    "Never exceed",
    "active worker packets",
    "Branch scheduler serial/under-capacity reasons",
    "Worker scheduler serial/under-capacity reasons",
)
BRANCH_PROMPT_PHRASES_AFTER_SCHEDULER = (
    "worker_parallelism.scheduler_path",
    "Worker parallelization rationale",
    "Pre-review gate path",
    "Default worker ladder",
    "Allowed worker route aliases",
    "Route-class ladders",
    "Bootstrap Command",
    "Additional Validators",
    "Worker packet id",
    "Route class reason code",
    "Recommended ladder",
    "Source attachment refs",
    "telemetry.json",
    "validate_branch_status.py --manifest",
    "Stop Conditions",
    "git diff --check",
    "semantic_input_hashes",
)
RUNTIME_RULES_REQUIRED_PHRASES = (
    "Shared Branch Runtime Rules",
    "Worker Parallelism",
    "runtime_phase_manifest.py --markdown",
    "do not read skill Python source",
    "script_only_repair_gate.py --scope branch",
    "rolling saturated pool",
    "render_worker_schedule.py",
    "scheduler_tick.py --blocked/--close",
    "Worker Model Routing",
    "Selected worker ladders",
    "Lite Advisors",
    "Reviewer Requirement",
    "pre_review_gate.json",
    "semantic_input_hashes",
    "Bootstrap Requirement",
    "orchestration_watchdog.branch_no_completion_wait_limit",
    "do not poll active",
    "Validation DoD",
)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def has_dod(text: str) -> bool:
    lowered = text.lower()
    if "definition of done" not in lowered:
        return False
    after = lowered.split("definition of done", 1)[1]
    return "- " in after


def canonical_status_target(value: str) -> str:
    target = value.strip().strip("`'\"").rstrip(".,;)")
    for prefix in STATUS_TARGET_PREFIXES:
        if target.startswith(prefix):
            target = target[len(prefix) :]
            break
    if target.startswith("branches/"):
        return target
    if "/branches/" in target:
        return "branches/" + target.rsplit("/branches/", 1)[1]
    if target.endswith("/main.status.json"):
        return "main.status.json"
    return target


def status_target_from_line(script_name: str, line: str) -> str | None:
    for match in VALIDATOR_COMMAND_RE.finditer(line):
        if match.group("script") != script_name:
            continue
        snippet = script_name + match.group("tail")
        try:
            tokens = shlex.split(snippet)
        except ValueError:
            return None
        for index, token in enumerate(tokens):
            if token == "--status" and index + 1 < len(tokens):
                return canonical_status_target(tokens[index + 1])
    return None


def lint_validator_command_snippets(defect, path: str, text: str, expected_status_targets: dict[str, str]) -> None:
    for lineno, line in enumerate(text.splitlines(), start=1):
        for script_name, expected_target in expected_status_targets.items():
            status_hint = VALIDATOR_COMMAND_STATUS_HINTS[script_name]
            if f"{script_name} --manifest" not in line:
                continue
            actual_target = status_target_from_line(script_name, line)
            if actual_target != expected_target:
                action = f"use {expected_target} as status target" if actual_target else f"include {status_hint}"
                defect(
                    path,
                    "major",
                    f"line {lineno}: {script_name} command snippet must {action} on same line",
                )
                continue


def require_text_phrases(
    defect,
    path: str,
    text: str,
    phrases: tuple[str, ...],
    *,
    severity: str,
    message_prefix: str,
    case_sensitive: bool = False,
) -> None:
    haystack = text if case_sensitive else text.lower()
    for phrase in phrases:
        needle = phrase if case_sensitive else phrase.lower()
        if needle not in haystack:
            defect(path, severity, f"{message_prefix}: {phrase}")


def lint_generated_prompt_text(defect, path: str, text: str, *, is_branch_prompt: bool = False) -> None:
    if "$GOAL_SKILLS_ROOT/" in text or "${GOAL_SKILLS_ROOT}/" in text:
        defect(path, "major", 'GOAL_SKILLS_ROOT script paths must be quoted as "$GOAL_SKILLS_ROOT"/...')
    if not is_branch_prompt:
        return
    if "## Tests And Validators" in text:
        defect(path, "major", "branch prompt must use Additional Validators instead of Tests And Validators")
    if "small-edit/normal-code -> Codex Spark then Codex mini" in text:
        defect(path, "major", "branch prompt contains stale hard-coded worker route aliases")
    dod_tail = text.split("## Definition of Done", 1)[1] if "## Definition of Done" in text else ""
    if "\n- none" in dod_tail:
        defect(path, "major", "branch prompt Definition of Done must not include stray '- none'")


def branch_prompt_required_phrases(branch_id: object) -> tuple[str, ...]:
    scheduler_path = (
        CONTRACT.worker_scheduler_path(str(branch_id)) if isinstance(branch_id, str) else "worker scheduler"
    )
    return (
        *BRANCH_PROMPT_PHRASES_BEFORE_SCHEDULER,
        scheduler_path,
        *BRANCH_PROMPT_PHRASES_AFTER_SCHEDULER,
    )


def load_lite_validator() -> object | None:
    path = Path(__file__).resolve().parent / "validate_lite_advice.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("goal_preflight_validate_lite_advice", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json_artifact(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def lite_validation_command(advice_path: Path, inputs_path: Path) -> str:
    validator_path = Path(__file__).resolve().parent / "validate_lite_advice.py"
    return shlex.join(
        [
            "python3",
            validator_path.as_posix(),
            "--advice",
            advice_path.as_posix(),
            "--inputs",
            inputs_path.as_posix(),
        ]
    )


def paths_overlap(left: str, right: str) -> bool:
    left_norm = left.rstrip("/")
    right_norm = right.rstrip("/")
    return left_norm == right_norm or left_norm.startswith(right_norm + "/") or right_norm.startswith(left_norm + "/")


def collect_manifest_text(manifest: dict) -> str:
    parts: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    for key in ("goal", "source_summary", "required_evidence", "final_dod", "runtime_cap"):
        walk(manifest.get(key))
    for branch in manifest.get("branches", []) if isinstance(manifest.get("branches"), list) else []:
        if not isinstance(branch, dict):
            continue
        for key in ("objective", "scope", "dod"):
            walk(branch.get(key))
        for item in branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []:
            if isinstance(item, dict):
                for key in ("objective", "verification", "dod"):
                    walk(item.get(key))
    return "\n".join(parts)


def has_inline_source_payload(text: str) -> bool:
    tuple_count = len(INLINE_SOURCE_TUPLE_RE.findall(text))
    if tuple_count >= 10:
        return True
    if len(text) < 400:
        return False
    lowered = text.lower()
    return "ft10 = [" in lowered or ("operation list" in lowered and text.count("[") >= 4 and text.count("]") >= 4)


def has_source_attachment(manifest: dict) -> bool:
    attachments = manifest.get("source_attachments")
    return isinstance(attachments, list) and any(isinstance(item, dict) for item in attachments)


def concrete_runtime_cap(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and bool(re.search(r"\d", value))
    if isinstance(value, dict):
        if not value:
            return False
        for item in value.values():
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)) and item > 0:
                return True
            if isinstance(item, str) and item.strip() and re.search(r"\d", item):
                return True
        return False
    return False


def dependency_closure(branch_id: str, branch_deps: dict[str, list[str]]) -> set[str]:
    remaining = list(branch_deps.get(branch_id, []))
    seen: set[str] = set()
    while remaining:
        current = remaining.pop()
        if current in seen:
            continue
        seen.add(current)
        remaining.extend(dep for dep in branch_deps.get(current, []) if dep not in seen)
    return seen


def owner_for_path(path: str, branch_owned_paths: dict[str, list[str]]) -> tuple[str, str] | None:
    for branch_id, owned_paths in branch_owned_paths.items():
        for owned in owned_paths:
            if paths_overlap(path, owned):
                return branch_id, owned
    return None


def command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def strip_command_path_token(token: str) -> str:
    value = token.strip().strip("\"'`").rstrip(".,;)")
    if "::" in value:
        value = value.split("::", 1)[0]
    return value


def looks_like_repo_path(token: str) -> bool:
    if not token or token.startswith("-") or token.startswith("$") or token.startswith("/"):
        return False
    if "\\" in token or "://" in token:
        return False
    value = strip_command_path_token(token)
    if not value or value in {".", ".."}:
        return False
    if "/" in value:
        return not value.startswith("../") and "/../" not in value and not value.endswith("/..")
    return value.endswith(PATH_TOKEN_EXTENSIONS)


def python_module_candidates(module: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module):
        return []
    path = module.replace(".", "/")
    return [
        f"{path}.py",
        f"{path}/__init__.py",
        f"src/{path}.py",
        f"src/{path}/__init__.py",
    ]


def command_references(command: str) -> list[dict[str, object]]:
    tokens = command_tokens(command)
    refs: list[dict[str, object]] = []
    for index, token in enumerate(tokens):
        if (
            token in {"-m", "--module"}
            and index > 0
            and index + 1 < len(tokens)
            and Path(tokens[index - 1]).name in PYTHON_BINS
        ):
            module = tokens[index + 1]
            if module != "pytest":
                refs.append({"kind": "python_module", "value": module, "candidates": python_module_candidates(module)})
        candidate = strip_command_path_token(token)
        if looks_like_repo_path(candidate):
            refs.append({"kind": "path", "value": candidate, "candidates": [candidate]})
    return refs


def has_contention_reason(*values: object) -> bool:
    for value in values:
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value):
            return True
    return False


def ready_branch_count(branches: list[dict]) -> int:
    return len([branch for branch in branches if isinstance(branch, dict) and not branch.get("depends_on")])


def longest_branch_chain(branches: list[dict]) -> int:
    lengths: dict[str, int] = {}
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        deps = branch.get("depends_on", []) if isinstance(branch.get("depends_on"), list) else []
        lengths[branch["id"]] = 1 + max([lengths.get(dep, 1) for dep in deps if isinstance(dep, str)] or [0])
    return max(lengths.values(), default=0)


def branch_dependency_levels(branches: list[dict]) -> dict[str, int]:
    levels: dict[str, int] = {}
    remaining = {
        str(branch.get("id")): branch
        for branch in branches
        if isinstance(branch, dict) and isinstance(branch.get("id"), str)
    }
    while remaining:
        progressed = False
        for branch_id, branch in list(remaining.items()):
            deps = [dep for dep in branch.get("depends_on", []) if isinstance(dep, str)]
            if any(dep in remaining for dep in deps):
                continue
            dep_levels = [levels.get(dep, 0) for dep in deps]
            levels[branch_id] = 1 + (max(dep_levels) if dep_levels else 0)
            remaining.pop(branch_id)
            progressed = True
        if not progressed:
            for branch_id in list(remaining):
                levels[branch_id] = 1
                remaining.pop(branch_id)
    return levels


def max_branch_ready_width(branches: list[dict]) -> int:
    widths: dict[int, int] = {}
    for level in branch_dependency_levels(branches).values():
        widths[level] = widths.get(level, 0) + 1
    return max(widths.values(), default=0)


def ready_worker_count(work_items: list[dict]) -> int:
    return len([item for item in work_items if isinstance(item, dict) and not item.get("depends_on")])


def longest_worker_chain(work_items: list[dict]) -> int:
    lengths: dict[str, int] = {}
    for item in work_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        deps = item.get("depends_on", []) if isinstance(item.get("depends_on"), list) else []
        lengths[item["id"]] = 1 + max([lengths.get(dep, 1) for dep in deps if isinstance(dep, str)] or [0])
    return max(lengths.values(), default=0)


def validate_preflight_lite_records(defect, bundle_dir: Path, manifest: dict) -> None:
    records = manifest.get("preflight_lite_advice")
    if not isinstance(records, list):
        defect("job.manifest.json", "critical", "preflight_lite_advice must be present as an array")
        return
    validator = load_lite_validator()
    if validator is None:
        defect("job.manifest.json", "critical", "could not load validate_lite_advice.py for preflight Lite provenance")
        return
    reported_ids: set[str] = set()
    for index, record in enumerate(records):
        path = f"preflight_lite_advice[{index}]"
        if not isinstance(record, dict):
            defect("job.manifest.json", "critical", f"{path} must be an object")
            continue
        required = [
            "packet_id",
            "purpose",
            "avoids_action",
            "expected_savings_reason",
            "status",
            "disposition",
            "advice_path",
            "inputs_path",
            "source_files",
            "validation_command",
            "validation_status",
            "validation_defects",
            "reason",
        ]
        for key in required:
            if key not in record:
                defect("job.manifest.json", "critical", f"{path} missing key: {key}")
        packet_id = record.get("packet_id")
        if not isinstance(packet_id, str) or not SAFE_LABEL_RE.fullmatch(packet_id):
            defect("job.manifest.json", "critical", f"{path}.packet_id must match {SAFE_LABEL_RE.pattern}")
            continue
        if packet_id in reported_ids:
            defect("job.manifest.json", "critical", f"{path}.packet_id duplicates {packet_id}")
        reported_ids.add(packet_id)
        purpose, validation_status, validation_defects = _lite_record_static_fields(defect, record, path, packet_id)
        _lite_record_artifacts(
            defect,
            validator,
            bundle_dir,
            record,
            path,
            packet_id,
            purpose,
            validation_status,
            validation_defects,
        )

    _lint_lite_orphan_packets(defect, bundle_dir, reported_ids)


def _lite_record_static_fields(defect, record: dict, path: str, packet_id: str) -> tuple[object, str | None, list]:
    """Validate the non-artifact fields of one Lite record.

    Returns (purpose, validation_status, validation_defects) for downstream
    cross-checks against the on-disk Lite artifacts.
    """
    purpose = record.get("purpose")
    if purpose not in PREFLIGHT_LITE_PURPOSES:
        defect("job.manifest.json", "critical", f"{path}.purpose must be one of {sorted(PREFLIGHT_LITE_PURPOSES)}")
    if not isinstance(record.get("avoids_action"), str) or not record.get("avoids_action", "").strip():
        defect("job.manifest.json", "critical", f"{path}.avoids_action must be a non-empty string")
    if (
        not isinstance(record.get("expected_savings_reason"), str)
        or not record.get("expected_savings_reason", "").strip()
    ):
        defect("job.manifest.json", "critical", f"{path}.expected_savings_reason must be a non-empty string")
    if record.get("status") not in LITE_STATUSES:
        defect("job.manifest.json", "critical", f"{path}.status must be one of {sorted(LITE_STATUSES)}")
    if record.get("disposition") not in LITE_DISPOSITIONS:
        defect("job.manifest.json", "critical", f"{path}.disposition must be one of {sorted(LITE_DISPOSITIONS)}")
    if record.get("disposition") == "used" and record.get("status") != "ok":
        defect("job.manifest.json", "critical", f"{path}.disposition may be used only when Lite status is ok")
    expected_advice = f"lite/{packet_id}/advice.json"
    expected_inputs = f"lite/{packet_id}/input-files.json"
    if record.get("advice_path") != expected_advice:
        defect("job.manifest.json", "critical", f"{path}.advice_path must be {expected_advice!r}")
    if record.get("inputs_path") != expected_inputs:
        defect("job.manifest.json", "critical", f"{path}.inputs_path must be {expected_inputs!r}")
    validation_status = record.get("validation_status")
    validation_defects = record.get("validation_defects")
    if validation_status not in LITE_VALIDATION_STATUSES:
        defect(
            "job.manifest.json",
            "critical",
            f"{path}.validation_status must be one of {sorted(LITE_VALIDATION_STATUSES)}",
        )
    if not isinstance(validation_defects, list) or any(
        not isinstance(item, str) or not item.strip() for item in validation_defects
    ):
        defect("job.manifest.json", "critical", f"{path}.validation_defects must be an array of non-empty strings")
        validation_defects = []
    if validation_status == "pass" and validation_defects:
        defect(
            "job.manifest.json",
            "critical",
            f"{path}.validation_defects must be empty when validation_status is pass",
        )
    if validation_status == "failed" and not validation_defects:
        defect("job.manifest.json", "critical", f"{path}.validation_defects must explain failed Lite validation")
    if not isinstance(record.get("reason"), str) or not record.get("reason", "").strip():
        defect("job.manifest.json", "critical", f"{path}.reason must be a non-empty string")
    return purpose, validation_status, validation_defects


def _lite_record_artifacts(
    defect,
    validator,
    bundle_dir: Path,
    record: dict,
    path: str,
    packet_id: str,
    purpose: object,
    validation_status: str | None,
    validation_defects: list,
) -> None:
    """Cross-check one Lite record against its on-disk advice/input artifacts."""
    expected_advice = f"lite/{packet_id}/advice.json"
    expected_inputs = f"lite/{packet_id}/input-files.json"
    validation_command = record.get("validation_command")
    advice_path = bundle_dir / expected_advice
    inputs_path = bundle_dir / expected_inputs
    expected_command = lite_validation_command(advice_path, inputs_path)
    if not isinstance(validation_command, str) or validation_command != expected_command:
        defect("job.manifest.json", "critical", f"{path}.validation_command must be exactly: {expected_command}")
    if not advice_path.exists():
        defect("job.manifest.json", "critical", f"{path}.advice_path artifact does not exist: {advice_path}")
        return
    if not inputs_path.exists():
        defect("job.manifest.json", "critical", f"{path}.inputs_path artifact does not exist: {inputs_path}")
        return
    try:
        advice_data = load_json_artifact(advice_path)
        inputs_data = load_json_artifact(inputs_path)
    except Exception as exc:  # noqa: BLE001
        defect("job.manifest.json", "critical", f"{path} Lite artifacts must be readable JSON: {exc}")
        return
    expected_sources = (
        inputs_data.get("source_files")
        if isinstance(inputs_data, dict) and isinstance(inputs_data.get("source_files"), list)
        else []
    )
    expected_min = [
        {
            "path": source.get("path"),
            "sha256": source.get("sha256"),
            "size_bytes": source.get("size_bytes"),
            "reason": source.get("reason"),
        }
        for source in expected_sources
        if isinstance(source, dict)
    ]
    if record.get("source_files") != expected_min:
        defect(
            "job.manifest.json",
            "critical",
            f"{path}.source_files must match input-files.json source metadata exactly",
        )
    if record.get("avoids_action") != inputs_data.get("avoids_action"):
        defect("job.manifest.json", "critical", f"{path}.avoids_action must match input-files.json")
    if record.get("expected_savings_reason") != inputs_data.get("expected_savings_reason"):
        defect("job.manifest.json", "critical", f"{path}.expected_savings_reason must match input-files.json")
    lite_defects = validator.validate(
        advice_data,
        packet_id=packet_id,
        purpose=str(purpose) if isinstance(purpose, str) else None,
        expected_sources=expected_sources,
        inputs=inputs_data if isinstance(inputs_data, dict) else None,
        inputs_path=inputs_path,
    )
    actual_validation_status = "pass" if not lite_defects else "failed"
    if validation_status in LITE_VALIDATION_STATUSES and validation_status != actual_validation_status:
        defect(
            "job.manifest.json",
            "critical",
            f"{path}.validation_status must match actual Lite validation status {actual_validation_status!r}",
        )
    if validation_status == "failed" and validation_defects != lite_defects:
        defect(
            "job.manifest.json",
            "critical",
            f"{path}.validation_defects must match actual Lite validation defects exactly",
        )
    if record.get("disposition") == "used" and lite_defects:
        defect("job.manifest.json", "critical", f"{path} used Lite advice must pass validation")


def _lint_lite_orphan_packets(defect, bundle_dir: Path, reported_ids: set[str]) -> None:
    """Flag preflight Lite packet dirs on disk that no manifest record references."""
    lite_root = bundle_dir / "lite"
    if not lite_root.is_dir():
        return
    for packet_dir in sorted(item for item in lite_root.iterdir() if item.is_dir()):
        inputs_path = packet_dir / "input-files.json"
        advice_path = packet_dir / "advice.json"
        inputs_data: object = {}
        if inputs_path.exists():
            try:
                inputs_data = load_json_artifact(inputs_path)
            except Exception as exc:  # noqa: BLE001
                defect(
                    "job.manifest.json",
                    "critical",
                    f"lite/{packet_dir.name}/input-files.json must be readable JSON: {exc}",
                )
                continue
        elif advice_path.exists() and packet_dir.name.startswith("P"):
            defect(
                "job.manifest.json",
                "critical",
                f"unrecorded malformed preflight Lite packet without input-files.json: {packet_dir}",
            )
            continue
        if not isinstance(inputs_data, dict):
            continue
        purpose = inputs_data.get("purpose")
        skill = inputs_data.get("skill")
        input_packet_id = inputs_data.get("packet_id")
        packet_id = input_packet_id if isinstance(input_packet_id, str) and input_packet_id.strip() else packet_dir.name
        relevant = purpose in PREFLIGHT_LITE_PURPOSES or skill == "goal-preflight" or packet_dir.name.startswith("P")
        if relevant and packet_id not in reported_ids:
            defect(
                "job.manifest.json",
                "critical",
                f"unrecorded manifest-owned preflight Lite packet: {packet_id} at {packet_dir}",
            )


def validate_telemetry_policy(defect, manifest: dict) -> None:
    policy = manifest.get("telemetry_policy")
    if policy is None:
        defect(
            "job.manifest.json",
            "warning",
            'telemetry_policy is missing; assuming {"schema_version": 1, "mode": "standard", "raw_text": false, "collect": []}',
        )
        return
    if not isinstance(policy, dict):
        defect("job.manifest.json", "critical", "telemetry_policy must be an object")
        return

    schema_version = policy.get("schema_version", TELEMETRY_POLICY_SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version != TELEMETRY_POLICY_SCHEMA_VERSION:
        defect(
            "job.manifest.json",
            "critical",
            f"telemetry_policy.schema_version must be {TELEMETRY_POLICY_SCHEMA_VERSION}",
        )

    mode = policy.get("mode")
    if mode not in TELEMETRY_POLICY_MODES:
        mode_display = ", ".join(TELEMETRY_POLICY_MODES)
        defect("job.manifest.json", "critical", f"telemetry_policy.mode must be one of [{mode_display}]")

    raw_text = policy.get("raw_text")
    if raw_text is not False:
        defect("job.manifest.json", "critical", "telemetry_policy.raw_text must be false")

    collect = policy.get("collect", [])
    if collect is None:
        collect = []
    elif isinstance(collect, str):
        collect = [collect]
    elif not isinstance(collect, list):
        defect("job.manifest.json", "critical", "telemetry_policy.collect must be a list")
        collect = []

    unsupported = []
    for index, item in enumerate(collect):
        if not isinstance(item, str) or not item.strip():
            defect("job.manifest.json", "critical", f"telemetry_policy.collect[{index}] must be a non-empty string")
            continue
        if item not in TELEMETRY_COLLECT_ITEMS:
            unsupported.append(item)
    if unsupported:
        defect(
            "job.manifest.json",
            "critical",
            f"telemetry_policy.collect has unsupported names: {', '.join(sorted(unsupported))}",
        )

    for key in policy:
        lowered = str(key).lower()
        if "usd" in lowered or "pricing" in lowered:
            defect("job.manifest.json", "critical", f"telemetry_policy contains unsupported billing field: {key}")

    allowed_keys = {"schema_version", "mode", "raw_text", "collect"}
    unknown = sorted(set(policy.keys()) - allowed_keys)
    if unknown:
        defect("job.manifest.json", "critical", f"telemetry_policy contains unsupported keys: {', '.join(unknown)}")


def validate_goal_config_manifest(defect, bundle_dir: Path, manifest: dict) -> tuple[dict | None, str]:
    config_path = manifest.get("goal_config_path")
    check_path = manifest.get("goal_config_check_path")
    if config_path is None and check_path is None and "goal_config_summary" not in manifest:
        return None, "not_configured"
    status = "pass"
    if "goal_config" in manifest:
        defect(
            "job.manifest.json",
            "critical",
            "goal_config must not embed full config; use goal_config_path plus summary/hash",
        )
        status = "failed"
    if "goal_config_check" in manifest:
        defect(
            "job.manifest.json",
            "critical",
            "goal_config_check must not embed full check report; use goal_config_check_path plus summary/hash",
        )
        status = "failed"

    if config_path != "goal.config.json":
        defect(
            "job.manifest.json", "critical", "goal_config_path must be 'goal.config.json' when goal_config is present"
        )
        return None, "failed"
    config = load_bundle_json(defect, bundle_dir, "goal.config.json", "goal_config")
    if config is None:
        return None, "failed"
    config_hash = manifest.get("goal_config_sha256")
    if not isinstance(config_hash, str) or not config_hash:
        defect("job.manifest.json", "critical", "goal_config_sha256 must be present when goal_config_path is present")
        status = "failed"
    elif sha256_file(bundle_dir / "goal.config.json") != config_hash:
        defect("job.manifest.json", "critical", "goal_config_sha256 does not match goal.config.json")
        status = "failed"
    if not isinstance(manifest.get("goal_config_summary"), dict):
        defect(
            "job.manifest.json", "critical", "goal_config_summary must be an object when goal_config_path is present"
        )
        status = "failed"

    serialized = json.dumps(config, sort_keys=True).lower()
    for forbidden in ("usd", "dollar", "pricing", "price"):
        if forbidden in serialized:
            defect(
                "job.manifest.json", "critical", f"goal_config contains unsupported billing field or unit: {forbidden}"
            )
    telemetry = config.get("telemetry") if isinstance(config.get("telemetry"), dict) else {}
    if telemetry.get("raw_text") is not False:
        defect(
            "goal.config.json",
            "critical",
            "bundled goal.config.json telemetry.raw_text must be false; runtime config copies must be sanitized",
        )
    if config.get("schema_version") != 1:
        defect("job.manifest.json", "critical", "goal_config.schema_version must be 1")
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        defect("job.manifest.json", "critical", "goal_config.models must be a non-empty object")
        models = {}
    harnesses = config.get("harnesses")
    if not isinstance(harnesses, dict) or not harnesses:
        defect("job.manifest.json", "critical", "goal_config.harnesses must be a non-empty object")
    ladders = config.get("model_ladders")
    if not isinstance(ladders, dict):
        defect("job.manifest.json", "critical", "goal_config.model_ladders must be an object")
        ladders = {}
    for ladder_name, ladder in ladders.items():
        if not isinstance(ladder, list) or not ladder:
            defect(
                "job.manifest.json", "critical", f"goal_config.model_ladders.{ladder_name} must be a non-empty array"
            )
            continue
        for role in ladder:
            if role not in models:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"goal_config.model_ladders.{ladder_name} references unknown role: {role}",
                )
    policies = config.get("model_policies")
    if not isinstance(policies, dict):
        defect("job.manifest.json", "critical", "goal_config.model_policies must be present")
        policies = {}
    for key in ("worker_model_policy", "review_model_policy", "amender_model_policy", "lite_model_policy"):
        if not isinstance(policies.get(key), dict):
            defect("job.manifest.json", "critical", f"goal_config.model_policies.{key} must be an object")

    if not isinstance(check_path, str) or check_path != "goal-config.check.json":
        defect(
            "job.manifest.json",
            "critical",
            "goal_config_check_path must be 'goal-config.check.json' when goal_config_path is present",
        )
        return config, "failed"
    check = load_bundle_json(defect, bundle_dir, "goal-config.check.json", "goal_config_check")
    if check is None:
        return config, "failed"
    check_hash = manifest.get("goal_config_check_sha256")
    if not isinstance(check_hash, str) or not check_hash:
        defect(
            "job.manifest.json",
            "critical",
            "goal_config_check_sha256 must be present when goal_config_check_path is present",
        )
        status = "failed"
    elif sha256_file(bundle_dir / "goal-config.check.json") != check_hash:
        defect("job.manifest.json", "critical", "goal_config_check_sha256 does not match goal-config.check.json")
        status = "failed"
    if not isinstance(manifest.get("goal_config_check_summary"), dict):
        defect(
            "job.manifest.json",
            "critical",
            "goal_config_check_summary must be an object when goal_config_check_path is present",
        )
        status = "failed"
    if check.get("status") != "pass":
        defect("job.manifest.json", "critical", "goal_config_check.status must be pass")
        status = "failed"
    return config, status


def _validate_route_contract(defect, manifest: dict) -> None:
    """Validate manifest route_contract object, hash, and flag fields."""
    route_contract = manifest.get("route_contract")
    if not isinstance(route_contract, dict):
        defect("job.manifest.json", "critical", "route_contract must be an object")
        route_contract = {}
    elif route_contract.get("schema_version") != 1:
        defect("job.manifest.json", "critical", "route_contract.schema_version must be 1")
    route_hash = manifest.get("route_contract_sha256")
    if not isinstance(route_hash, str) or not route_hash:
        defect("job.manifest.json", "critical", "route_contract_sha256 must be present")
    elif sha256_json(route_contract) != route_hash:
        defect("job.manifest.json", "critical", "route_contract_sha256 does not match route_contract")
    if not isinstance(route_contract.get("catalog_refresh_required"), bool):
        defect("job.manifest.json", "critical", "route_contract.catalog_refresh_required must be boolean")
    if not isinstance(route_contract.get("route_recommendations_enabled"), bool):
        defect("job.manifest.json", "critical", "route_contract.route_recommendations_enabled must be boolean")
    if (
        route_contract.get("goal_config_check_path") is not None
        and route_contract.get("goal_config_check_path") != "goal-config.check.json"
    ):
        defect(
            "job.manifest.json",
            "critical",
            "route_contract.goal_config_check_path must be goal-config.check.json when present",
        )
    route_check_hash = route_contract.get("goal_config_check_sha256")
    if route_check_hash is not None and route_check_hash != manifest.get("goal_config_check_sha256"):
        defect(
            "job.manifest.json",
            "critical",
            "route_contract.goal_config_check_sha256 must match manifest goal_config_check_sha256",
        )


def _validate_execution_strategy(defect, manifest: dict) -> dict:
    """Validate manifest execution_strategy; return the (possibly coerced) object."""
    execution_strategy = manifest.get("execution_strategy")
    if not isinstance(execution_strategy, dict):
        defect("job.manifest.json", "critical", "execution_strategy must be an object")
        execution_strategy = {}
    elif execution_strategy.get("schema_version") != 1:
        defect("job.manifest.json", "critical", "execution_strategy.schema_version must be 1")
    for key in ("id", "strategy", "reason"):
        if not isinstance(execution_strategy.get(key), str) or not execution_strategy.get(key, "").strip():
            defect("job.manifest.json", "critical", f"execution_strategy.{key} must be a non-empty string")
    setup_commands = execution_strategy.get("setup_commands")
    if not isinstance(setup_commands, list) or any(
        not isinstance(item, str) or not item.strip() for item in setup_commands
    ):
        defect("job.manifest.json", "critical", "execution_strategy.setup_commands must be an array of strings")
    validation_env = execution_strategy.get("validation_env")
    if not isinstance(validation_env, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in validation_env.items()
    ):
        defect("job.manifest.json", "critical", "execution_strategy.validation_env must be an object of string values")
    return execution_strategy


def _validate_ownership_feasibility(defect, manifest: dict) -> dict:
    """Validate manifest ownership_feasibility; return the (possibly coerced) object."""
    ownership = manifest.get("ownership_feasibility")
    if not isinstance(ownership, dict):
        defect("job.manifest.json", "critical", "ownership_feasibility must be an object")
        ownership = {}
    elif ownership.get("schema_version") != 1:
        defect("job.manifest.json", "critical", "ownership_feasibility.schema_version must be 1")
    if ownership.get("status") not in {"pass", "needs_review"}:
        defect("job.manifest.json", "critical", "ownership_feasibility.status must be pass or needs_review")
    commands = ownership.get("commands")
    if not isinstance(commands, list):
        defect("job.manifest.json", "critical", "ownership_feasibility.commands must be an array")
        commands = []
    for index, record in enumerate(commands):
        path = f"ownership_feasibility.commands[{index}]"
        if not isinstance(record, dict):
            defect("job.manifest.json", "critical", f"{path} must be an object")
            continue
        if record.get("status") not in {"pass", "needs_review"}:
            defect("job.manifest.json", "critical", f"{path}.status must be pass or needs_review")
        for key in ("branch_id", "work_item_id", "command", "recommended_action"):
            if not isinstance(record.get(key), str) or not record.get(key, "").strip():
                defect("job.manifest.json", "critical", f"{path}.{key} must be a non-empty string")
        for key in (
            "required_paths",
            "branch_owned_paths",
            "dependency_covered_paths",
            "uncovered_paths",
            "missing_owned_paths",
        ):
            values = record.get(key)
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                defect("job.manifest.json", "critical", f"{path}.{key} must be an array of strings")
    needs_review_count = ownership.get("needs_review_count")
    if not is_strict_int(needs_review_count) or needs_review_count < 0:
        defect(
            "job.manifest.json", "critical", "ownership_feasibility.needs_review_count must be a non-negative integer"
        )
    return ownership


def _validate_runtime_index_cross_refs(
    defect, manifest: dict, runtime_index: dict, execution_strategy: dict, ownership: dict
) -> None:
    """Cross-check the loaded runtime index against the manifest's derived metadata."""
    if runtime_index.get("schema_version") != 1:
        defect("runtime.index.json", "critical", "runtime index schema_version must be 1")
    if runtime_index.get("kind") != "goal-runtime-index":
        defect("runtime.index.json", "critical", "runtime index kind must be goal-runtime-index")
    if runtime_index.get("manifest_path") != "job.manifest.json":
        defect("runtime.index.json", "critical", "runtime index manifest_path must be job.manifest.json")
    runtime_rules = runtime_index.get("runtime_rules") if isinstance(runtime_index.get("runtime_rules"), dict) else {}
    if runtime_rules.get("path") != manifest.get("runtime_rules_path") or runtime_rules.get("sha256") != manifest.get(
        "runtime_rules_sha256"
    ):
        defect(
            "runtime.index.json", "critical", "runtime index runtime_rules must match manifest runtime rules path/hash"
        )
    index_route_contract = (
        runtime_index.get("route_contract") if isinstance(runtime_index.get("route_contract"), dict) else {}
    )
    if index_route_contract.get("sha256") != manifest.get("route_contract_sha256"):
        defect("runtime.index.json", "critical", "runtime index route_contract.sha256 must match manifest")
    index_execution = (
        runtime_index.get("execution_strategy") if isinstance(runtime_index.get("execution_strategy"), dict) else {}
    )
    if index_execution.get("id") != execution_strategy.get("id"):
        defect("runtime.index.json", "critical", "runtime index execution_strategy.id must match manifest")
    index_ownership = (
        runtime_index.get("ownership_feasibility")
        if isinstance(runtime_index.get("ownership_feasibility"), dict)
        else {}
    )
    if index_ownership.get("status") != ownership.get("status"):
        defect("runtime.index.json", "critical", "runtime index ownership_feasibility.status must match manifest")
    index_counts = runtime_index.get("counts") if isinstance(runtime_index.get("counts"), dict) else {}
    branch_count = len(manifest.get("branches", [])) if isinstance(manifest.get("branches"), list) else 0
    if index_counts.get("branch_count") != branch_count:
        defect(
            "runtime.index.json",
            "warning",
            "runtime index branch_count differs from current manifest; regenerate after topology amendments",
        )


def validate_runtime_metadata(defect, bundle_dir: Path, manifest: dict) -> dict | None:
    _validate_route_contract(defect, manifest)
    execution_strategy = _validate_execution_strategy(defect, manifest)
    ownership = _validate_ownership_feasibility(defect, manifest)

    runtime_index_value = manifest.get("runtime_index_path")
    runtime_index_error = relative_path_defect(runtime_index_value, "runtime_index_path")
    runtime_index = None
    if runtime_index_error:
        defect("job.manifest.json", "critical", runtime_index_error)
    elif runtime_index_value != "runtime.index.json":
        defect("job.manifest.json", "critical", "runtime_index_path must be 'runtime.index.json'")
    else:
        runtime_index = load_bundle_json(defect, bundle_dir, "runtime.index.json", "runtime_index")
    index_hash = manifest.get("runtime_index_sha256")
    if not isinstance(index_hash, str) or not index_hash:
        defect("job.manifest.json", "critical", "runtime_index_sha256 must be present")
    elif (bundle_dir / "runtime.index.json").is_file() and sha256_file(bundle_dir / "runtime.index.json") != index_hash:
        defect("job.manifest.json", "critical", "runtime_index_sha256 does not match runtime.index.json")
    if runtime_index is None:
        return None
    _validate_runtime_index_cross_refs(defect, manifest, runtime_index, execution_strategy, ownership)
    return runtime_index


def _lint_source_attachments(defect, manifest: dict) -> tuple[set[str], set[str]]:
    """Validate manifest source_attachments and return (labels, paths) sets.

    Exact behavior-preserving extraction of the lint() source-attachments block.
    """
    source_attachment_labels: set[str] = set()
    source_attachment_paths: set[str] = set()
    attachments = manifest.get("source_attachments")
    if attachments is not None:
        if not isinstance(attachments, list):
            defect("job.manifest.json", "critical", "source_attachments must be an array when present")
        else:
            for index, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    defect("job.manifest.json", "critical", f"source_attachments[{index}] must be an object")
                    continue
                label = attachment.get("label")
                path = attachment.get("path")
                if not isinstance(label, str) or not label.strip():
                    defect("job.manifest.json", "critical", f"source_attachments[{index}].label must be non-empty")
                elif label in source_attachment_labels:
                    defect("job.manifest.json", "critical", f"source_attachments label is duplicated: {label}")
                else:
                    source_attachment_labels.add(label)
                if not isinstance(path, str) or relative_path_defect(path, f"source_attachments[{index}].path"):
                    defect("job.manifest.json", "critical", f"source_attachments[{index}].path must be repo-relative")
                else:
                    source_attachment_paths.add(path)
                if attachment.get("promoted_from_context_files") is True and attachment.get("bytes", 0) < 8192:
                    defect(
                        "job.manifest.json",
                        "major",
                        f"source_attachments[{index}] was promoted from context_files but is below the large-source threshold",
                    )
    return source_attachment_labels, source_attachment_paths


def _lint_parallelization(defect, manifest: dict) -> tuple[dict, list, bool]:
    """Validate manifest-level parallelization; return (parallelization, serial_reasons, has_serial_reason)."""
    parallelization = manifest.get("parallelization", {})
    if not isinstance(parallelization, dict):
        defect("job.manifest.json", "critical", "parallelization must be an object")
        parallelization = {}
    if parallelization.get("parallelism_default") is not True:
        defect("job.manifest.json", "critical", "parallelization.parallelism_default must be true")
    if parallelization.get("max_branches_per_wave") != MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "parallelization.max_branches_per_wave must be 4")
    if parallelization.get("max_waves") != MAX_WAVES:
        defect("job.manifest.json", "critical", "parallelization.max_waves must be 5")
    if parallelization.get("scheduling_mode") != "rolling":
        defect("job.manifest.json", "critical", "parallelization.scheduling_mode must be rolling")
    if parallelization.get("scheduler_path") != CONTRACT.MAIN_SCHEDULER_PATH:
        defect(
            "job.manifest.json", "critical", f"parallelization.scheduler_path must be {CONTRACT.MAIN_SCHEDULER_PATH!r}"
        )
    if (
        not isinstance(parallelization.get("dependency_policy"), str)
        or not parallelization.get("dependency_policy", "").strip()
    ):
        defect("job.manifest.json", "critical", "parallelization.dependency_policy must be non-empty")
    wave_execution = parallelization.get("wave_execution", "")
    if (
        not isinstance(wave_execution, str)
        or "saturat" not in wave_execution.lower()
        or "depends_on" not in wave_execution
    ):
        defect(
            "job.manifest.json",
            "critical",
            "parallelization.wave_execution must describe rolling saturation and depends_on deferral",
        )
    if "serial_reason" in parallelization:
        defect("job.manifest.json", "critical", "parallelization.serial_reason is obsolete; use serial_reasons")
    serial_reasons = parallelization.get("serial_reasons", [])
    if not isinstance(serial_reasons, list) or any(
        not isinstance(item, str) or not item.strip() for item in serial_reasons
    ):
        defect("job.manifest.json", "critical", "parallelization.serial_reasons must be an array of non-empty strings")
        serial_reasons = []
    has_serial_reason = bool(serial_reasons)
    return parallelization, serial_reasons, has_serial_reason


def _lint_branch_concurrency(
    defect,
    parallelization: dict,
    branches: list,
    max_active: object,
    serial_reasons: list,
    has_serial_reason: bool,
) -> None:
    """Validate branch-count, serial-reason, and DAG-width concurrency rules."""
    if not branches:
        defect("job.manifest.json", "critical", "branches must be non-empty")
    if len(branches) > DEFAULT_TOTAL_BRANCH_CAP:
        defect("job.manifest.json", "critical", "more than 20 branches exceeds 5 waves of 4")
    if len(branches) == 1 and not serial_reasons:
        defect("job.manifest.json", "critical", "single-branch bundles require parallelization.serial_reasons")
    if is_strict_int(max_active) and max_active < MAX_ACTIVE_BRANCH_AGENTS and not has_serial_reason:
        defect(
            "job.manifest.json", "critical", "max_active_branch_agents below 4 requires parallelization.serial_reasons"
        )
    if (
        is_strict_int(max_active)
        and isinstance(branches, list)
        and ready_branch_count(branches) < min(max_active, len(branches))
        and not has_serial_reason
    ):
        defect(
            "job.manifest.json",
            "critical",
            "too few initially eligible branches under max_active_branch_agents requires parallelization.serial_reasons",
        )
    if is_strict_int(max_active) and isinstance(branches, list) and branches:
        usable_cap = min(max_active, len(branches))
        max_ready_width = max_branch_ready_width(branches)
        if usable_cap > 1 and max_ready_width < usable_cap:
            defect(
                "job.manifest.json",
                "warning",
                f"branch dependency DAG reaches max ready width {max_ready_width} under usable cap {usable_cap}; branch agents may be underutilized",
            )
        rationale = parallelization.get("parallelization_rationale")
        if (
            isinstance(rationale, str)
            and max_ready_width <= 1
            and len(branches) > 1
            and any(marker in rationale.lower() for marker in ("concurrent", "parallel", "saturat"))
        ):
            defect(
                "job.manifest.json",
                "warning",
                "parallelization_rationale describes concurrent/saturated execution, but the branch dependency DAG exposes at most one ready branch at a time",
            )


def _lint_model_policies(defect, manifest: dict, goal_config: dict | None, compatibility_status: str) -> str:
    """Validate worker/review/amender/lite/advisor/watchdog/research policies.

    Returns the (possibly updated) compatibility_status. Behavior-preserving
    extraction of the lint() model-policies block.
    """
    worker_model_policy = manifest.get("worker_model_policy", {})
    if not isinstance(worker_model_policy, dict):
        defect("job.manifest.json", "critical", "worker_model_policy must be an object")
        worker_model_policy = {}
    if goal_config is not None:
        expected_policies = (
            goal_config.get("model_policies", {}) if isinstance(goal_config.get("model_policies"), dict) else {}
        )
        if worker_model_policy != normalized_worker_policy(expected_policies.get("worker_model_policy")):
            defect(
                "job.manifest.json",
                "critical",
                "worker_model_policy must match deterministic preflight-normalized goal_config.model_policies.worker_model_policy",
            )
            compatibility_status = "failed"
    else:
        if worker_model_policy.get("default_ladder") != DEFAULT_WORKER_LADDER:
            defect(
                "job.manifest.json", "critical", f"worker_model_policy.default_ladder must be {DEFAULT_WORKER_LADDER!r}"
            )
        if worker_model_policy.get("allowed_routes") != DEFAULT_WORKER_LADDER:
            defect(
                "job.manifest.json", "critical", f"worker_model_policy.allowed_routes must be {DEFAULT_WORKER_LADDER!r}"
            )
        if worker_model_policy.get("branch_may_select_worker_route") is not True:
            defect("job.manifest.json", "critical", "worker_model_policy.branch_may_select_worker_route must be true")
        if worker_model_policy.get("selection_reason_required") is not True:
            defect("job.manifest.json", "critical", "worker_model_policy.selection_reason_required must be true")
        if (
            not isinstance(worker_model_policy.get("ordering_rule"), str)
            or not worker_model_policy.get("ordering_rule", "").strip()
        ):
            defect("job.manifest.json", "critical", "worker_model_policy.ordering_rule must be non-empty")

    review_model_policy = manifest.get("review_model_policy", {})
    if goal_config is not None:
        expected_policies = (
            goal_config.get("model_policies", {}) if isinstance(goal_config.get("model_policies"), dict) else {}
        )
        if review_model_policy != expected_policies.get("review_model_policy"):
            defect(
                "job.manifest.json",
                "critical",
                "review_model_policy must match goal_config.model_policies.review_model_policy",
            )
            compatibility_status = "failed"
    elif review_model_policy != REVIEW_MODEL_POLICY:
        defect(
            "job.manifest.json",
            "critical",
            "review_model_policy must match the shared deterministic review router policy",
        )

    amender_model_policy = manifest.get("amender_model_policy", {})
    if goal_config is not None:
        expected_policies = (
            goal_config.get("model_policies", {}) if isinstance(goal_config.get("model_policies"), dict) else {}
        )
        if amender_model_policy != expected_policies.get("amender_model_policy"):
            defect(
                "job.manifest.json",
                "critical",
                "amender_model_policy must match goal_config.model_policies.amender_model_policy",
            )
            compatibility_status = "failed"
    elif amender_model_policy != AMENDER_MODEL_POLICY:
        defect(
            "job.manifest.json",
            "critical",
            "amender_model_policy must match the shared deterministic plan-amender router policy",
        )

    lite_model_policy = manifest.get("lite_model_policy", {})
    if goal_config is not None:
        expected_policies = (
            goal_config.get("model_policies", {}) if isinstance(goal_config.get("model_policies"), dict) else {}
        )
        if lite_model_policy != expected_policies.get("lite_model_policy"):
            defect(
                "job.manifest.json",
                "critical",
                "lite_model_policy must match goal_config.model_policies.lite_model_policy",
            )
            compatibility_status = "failed"
    elif lite_model_policy != LITE_MODEL_POLICY:
        defect(
            "job.manifest.json", "critical", "lite_model_policy must match the shared deterministic Lite model policy"
        )

    lite_advisor_policy = manifest.get("lite_advisor_policy", {})
    if lite_advisor_policy != LITE_ADVISOR_POLICY:
        defect(
            "job.manifest.json",
            "critical",
            "lite_advisor_policy must match the shared deterministic Lite advisor policy",
        )

    watchdog = manifest.get("orchestration_watchdog", {})
    if watchdog != ORCHESTRATION_WATCHDOG:
        defect("job.manifest.json", "critical", "orchestration_watchdog must match shared watchdog defaults")
    return compatibility_status


def _lint_research_worker_policy(defect, manifest: dict) -> object:
    """Validate research_worker_policy and return the (possibly coerced) value."""
    research_worker_policy = manifest.get("research_worker_policy")
    if research_worker_policy is not None:
        if not isinstance(research_worker_policy, dict):
            defect("job.manifest.json", "critical", "research_worker_policy must be an object when present")
            research_worker_policy = {}
        if research_worker_policy.get("enabled") is not True:
            defect("job.manifest.json", "critical", "research_worker_policy.enabled must be true when present")
        if research_worker_policy.get("worker_type") != RESEARCH_WORKER_TYPE:
            defect("job.manifest.json", "critical", "research_worker_policy.worker_type must be 'research-worker'")
        for key in ["launcher", "network_scope", "local_access"]:
            if not isinstance(research_worker_policy.get(key), str) or not research_worker_policy.get(key, "").strip():
                defect("job.manifest.json", "critical", f"research_worker_policy.{key} must be non-empty")
        rejected_phrases, required_phrases = CONTRACT.research_policy_defects(research_worker_policy)
        for phrase in rejected_phrases:
            defect(
                "job.manifest.json",
                "critical",
                f"research_worker_policy contains obsolete narrow-access phrase: {phrase}",
            )
        for phrase in required_phrases:
            defect("job.manifest.json", "critical", f"research_worker_policy must mention {phrase}")
    return research_worker_policy


def _lint_branch_identity(defect, branches: list) -> list:
    """Validate branch id/name/worktree uniqueness; return the branch ids list."""
    ids = [branch.get("id") for branch in branches]
    names = [branch.get("branch_name") for branch in branches]
    worktree_paths = [branch.get("worktree_path") for branch in branches]
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
    if len(worktree_paths) != len(set(worktree_paths)):
        defect("job.manifest.json", "critical", "branch worktree_path values must be unique")
    return ids


def _lint_reserved_bundle_paths(defect, branches: list) -> None:
    """Validate branch path fields against reserved/duplicate bundle file names."""
    reserved_bundle_paths = {
        "job.manifest.json",
        "main.prompt.md",
        "runtime-rules.md",
        "goal-bootloader.md",
        "PREFLIGHT_REPORT.md",
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "readiness.json",
        "goal-config-selection.json",
        "preflight.pipeline.json",
    }
    branch_bundle_paths: dict[str, str] = {}
    for branch in branches:
        for key in ["prompt", "status_path", "review_path", "pre_review_gate_path"]:
            value = branch.get(key)
            if not isinstance(value, str):
                continue
            label = f"branch {branch.get('id')} {key}"
            if value in reserved_bundle_paths:
                defect("job.manifest.json", "critical", f"{label} collides with reserved bundle file: {value}")
            owner = branch_bundle_paths.get(value)
            if owner is not None:
                defect("job.manifest.json", "critical", f"{label} duplicates {owner}: {value}")
            else:
                branch_bundle_paths[value] = label


def _lint_waves(defect, manifest: dict, branches: list, ids: list, has_serial_reason: bool) -> None:
    """Validate waves coverage/uniqueness and long serial branch chains."""
    waves = manifest.get("waves", [])
    wave_branch_ids = []
    wave_ids = []
    if len(waves) > MAX_WAVES:
        defect("job.manifest.json", "critical", "more than 5 waves is not allowed")
    for wave in waves:
        wid = wave.get("id")
        wave_ids.append(wid)
        if not isinstance(wid, str) or not SAFE_LABEL_RE.fullmatch(wid):
            defect("job.manifest.json", "critical", f"wave id is not safe: {wid!r}")
        branch_ids = wave.get("branches", [])
        if not isinstance(branch_ids, list) or not branch_ids:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} must list at least one branch")
            branch_ids = []
        if len(branch_ids) > MAX_ACTIVE_BRANCH_AGENTS:
            defect("job.manifest.json", "critical", f"wave {wave.get('id')} has more than 4 branches")
        wave_branch_ids.extend(branch_ids)
    if len(wave_ids) != len(set(wave_ids)):
        defect("job.manifest.json", "critical", "wave ids must be unique")
    if len(wave_branch_ids) != len(set(wave_branch_ids)):
        defect("job.manifest.json", "critical", "branch ids must not appear in more than one wave")
    if set(wave_branch_ids) != set(ids):
        defect("job.manifest.json", "critical", "waves must cover exactly the manifest branch ids")
    if isinstance(branches, list) and not has_serial_reason:
        chain = longest_branch_chain(branches)
        if len(branches) > 2 and chain >= len(branches) - 1:
            defect(
                "job.manifest.json",
                "critical",
                "long serial branch dependency chains require parallelization.serial_reasons",
            )


def _lint_main_prompt(defect, bundle_dir: Path, manifest: dict) -> None:
    """Validate the main prompt artifact and its required content."""
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
        lint_generated_prompt_text(defect, str(main_path), main_text)
        lint_validator_command_snippets(
            defect,
            str(main_path),
            main_text,
            {
                "validate_branch_status.py": "branches/Bxx.status.json",
                "validate_main_status.py": "main.status.json",
            },
        )
        require_text_phrases(
            defect,
            str(main_path),
            main_text,
            MAIN_PROMPT_REQUIRED_PHRASES,
            severity="critical",
            message_prefix="main prompt missing required phrase",
        )
        if not has_dod(main_text):
            defect(str(main_path), "critical", "main prompt lacks a falsifiable Definition of Done")


def _lint_runtime_rules(defect, bundle_dir: Path, manifest: dict) -> None:
    """Validate the runtime-rules appendix artifact, content, and hash."""
    runtime_rules_value = manifest.get("runtime_rules_path")
    runtime_rules_error = relative_path_defect(runtime_rules_value, "runtime_rules_path")
    if runtime_rules_error:
        defect("job.manifest.json", "critical", runtime_rules_error)
        runtime_rules_path = None
    elif runtime_rules_value != "runtime-rules.md":
        defect("job.manifest.json", "critical", "runtime_rules_path must be 'runtime-rules.md'")
        runtime_rules_path = resolve(bundle_dir, runtime_rules_value) if isinstance(runtime_rules_value, str) else None
    else:
        runtime_rules_path = resolve(bundle_dir, runtime_rules_value)
    if runtime_rules_path is None or not runtime_rules_path.exists():
        defect("runtime-rules.md", "critical", "runtime rules appendix is missing")
    else:
        runtime_rules_text = runtime_rules_path.read_text(encoding="utf-8")
        lint_generated_prompt_text(defect, "runtime-rules.md", runtime_rules_text)
        require_text_phrases(
            defect,
            "runtime-rules.md",
            runtime_rules_text,
            RUNTIME_RULES_REQUIRED_PHRASES,
            severity="critical",
            message_prefix="runtime rules missing required phrase",
        )
        runtime_rules_hash = manifest.get("runtime_rules_sha256")
        if not isinstance(runtime_rules_hash, str) or not runtime_rules_hash:
            defect("job.manifest.json", "critical", "runtime_rules_sha256 must be present")
        elif sha256_file(runtime_rules_path) != runtime_rules_hash:
            defect("job.manifest.json", "critical", "runtime_rules_sha256 does not match runtime-rules.md")


def _lint_bootloader_and_report(defect, bundle_dir: Path, manifest: dict) -> None:
    """Validate the bootloader and PREFLIGHT_REPORT readiness messaging."""
    bootloader_path = bundle_dir / "goal-bootloader.md"
    if not bootloader_path.exists():
        defect("goal-bootloader.md", "critical", "bootloader is missing")
    else:
        bootloader = bootloader_path.read_text(encoding="utf-8")
        lint_generated_prompt_text(defect, "goal-bootloader.md", bootloader)
        if len(bootloader) > 4000:
            defect("goal-bootloader.md", "critical", "bootloader exceeds 4000 characters")
        repo_status = manifest.get("repo_status") if isinstance(manifest.get("repo_status"), dict) else {}
        runtime_blocked = repo_status.get("repo_is_git") is False or repo_status.get("base_ref_status") == "missing"
        if runtime_blocked:
            if "BLOCKED READINESS" not in bootloader:
                defect(
                    "goal-bootloader.md",
                    "critical",
                    "runtime blocker requires a hard blocked-readiness bootloader warning",
                )
            if "Use $goal-main-orchestrator" in bootloader or "run_prompt_audit_phase.py" in bootloader:
                defect(
                    "goal-bootloader.md",
                    "critical",
                    "blocked-readiness bootloader must not render launch-looking main-orchestrator handoff commands",
                )
        else:
            require_text_phrases(
                defect,
                "goal-bootloader.md",
                bootloader,
                BOOTLOADER_REQUIRED_PHRASES,
                severity="critical",
                message_prefix="bootloader missing phrase",
                case_sensitive=True,
            )
        report_path = bundle_dir / "PREFLIGHT_REPORT.md"
        if report_path.exists():
            report_text = report_path.read_text(encoding="utf-8")
            if repo_status.get("repo_is_git") is False and "Runtime readiness gate: blocked" not in report_text:
                defect(
                    "PREFLIGHT_REPORT.md",
                    "major",
                    "preflight report must surface the non-git runtime readiness blocker",
                )
            if "harness check status is pass" in report_text:
                defect(
                    "PREFLIGHT_REPORT.md",
                    "major",
                    "preflight report must not label config compatibility as harness/route availability pass",
                )


def _lint_goal_config_selection(defect, bundle_dir: Path) -> None:
    """Validate the optional goal-config-selection.json artifact."""
    selection_path = bundle_dir / "goal-config-selection.json"
    if selection_path.exists():
        try:
            selection = load_json(selection_path)
        except Exception as exc:  # noqa: BLE001
            defect("goal-config-selection.json", "critical", f"goal-config-selection.json is not valid JSON: {exc}")
            selection = {}
        candidates = selection.get("candidates") if isinstance(selection, dict) else []
        if isinstance(candidates, list) and candidates:
            selected_candidates = [
                item for item in candidates if isinstance(item, dict) and item.get("selected") is True
            ]
            if selection.get("status") == "pass" and len(selected_candidates) != 1:
                defect("goal-config-selection.json", "critical", "exactly one candidate may have selected=true")
            if "selected" in selection:
                defect(
                    "goal-config-selection.json",
                    "major",
                    "top-level selected candidate must not be duplicated; use selected_index plus selected path/hash fields",
                )
            selected_index = selection.get("selected_index")
            if selection.get("status") == "pass":
                if (
                    not isinstance(selected_index, int)
                    or isinstance(selected_index, bool)
                    or not (0 <= selected_index < len(candidates))
                ):
                    defect(
                        "goal-config-selection.json", "critical", "selected_index must point to the selected candidate"
                    )
                elif selected_candidates and candidates[selected_index] is not selected_candidates[0]:
                    defect(
                        "goal-config-selection.json",
                        "critical",
                        "selected_index must match the candidate with selected=true",
                    )
            for index, item in enumerate(candidates):
                if not isinstance(item, dict):
                    continue
                if item.get("selected") is True and item.get("eligible") is not True:
                    defect(
                        "goal-config-selection.json",
                        "critical",
                        f"candidates[{index}] selected=true requires eligible=true",
                    )
                if (
                    "remediation" in item
                    and isinstance(item.get("remediation"), dict)
                    and "remediated_config" in item["remediation"]
                ):
                    defect(
                        "goal-config-selection.json",
                        "major",
                        f"candidates[{index}] remediation must be compact; full remediated config payload is not allowed",
                    )


class _BranchRouteTally(NamedTuple):
    """Aggregate counters produced while linting per-branch work items."""

    has_research_work_item: bool
    worker_route_class_count: int
    default_normal_route_count: int


def _lint_work_item_fields(
    defect, branch: dict, item: dict, item_path: str, source_attachment_labels: set[str], git_status: dict
) -> None:
    """Validate a single work item's id/packet_id/objective/worker_type/route fields and list fields."""
    item_id = item.get("id")
    packet_id = item.get("packet_id")
    expected_packet_id = (
        f"{branch.get('id')}-{item_id}" if isinstance(branch.get("id"), str) and isinstance(item_id, str) else ""
    )
    if not isinstance(packet_id, str) or not SAFE_LABEL_RE.fullmatch(packet_id):
        defect("job.manifest.json", "critical", f"{item_path}.packet_id must match {SAFE_LABEL_RE.pattern}")
    elif expected_packet_id and packet_id != expected_packet_id:
        defect("job.manifest.json", "critical", f"{item_path}.packet_id must be {expected_packet_id!r}")
    if not isinstance(item.get("objective"), str) or not item.get("objective", "").strip():
        defect("job.manifest.json", "critical", f"{item_path}.objective must be non-empty")
    for key, min_items in [
        ("owned_paths", 1),
        ("verification", 1),
        ("dod", 1),
        ("context_files", 0),
        ("source_attachment_refs", 0),
        ("depends_on", 0),
    ]:
        values = item.get(key, [])
        if key in {"owned_paths", "verification", "dod"} and key not in item:
            defect("job.manifest.json", "critical", f"{item_path}.{key} is required")
            continue
        if not isinstance(values, list) or len(values) < min_items:
            defect(
                "job.manifest.json",
                "critical",
                f"{item_path}.{key} must contain at least {min_items} item(s)",
            )
            continue
        for value_index, value in enumerate(values):
            if not isinstance(value, str) or not value.strip():
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.{key}[{value_index}] must be a non-empty string",
                )
            elif key in {"owned_paths", "context_files"}:
                message = relative_path_defect(value, f"{item_path}.{key}[{value_index}]")
                if message:
                    defect("job.manifest.json", "critical", message)
                elif key == "context_files":
                    tracked = git_tracks_manifest_path(git_status, value)
                    if tracked is False:
                        defect(
                            "job.manifest.json",
                            "major",
                            f"{item_path}.context_files[{value_index}] exists in manifest but is not tracked by git: {value}",
                        )
            elif key == "source_attachment_refs" and value not in source_attachment_labels:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.source_attachment_refs[{value_index}] references unknown source attachment label: {value}",
                )


def _lint_work_items(
    defect,
    branch: dict,
    work_items: list,
    branch_owned_paths_value: list,
    source_attachment_labels: set[str],
    git_status: dict,
    tally: _BranchRouteTally,
) -> _BranchRouteTally:
    """Validate the work_items list of one branch; return the updated route tally."""
    has_research_work_item = tally.has_research_work_item
    worker_route_class_count = tally.worker_route_class_count
    default_normal_route_count = tally.default_normal_route_count
    seen_work_item_ids = set()
    for index, item in enumerate(work_items):
        item_path = f"branch {branch.get('id')} work_items[{index}]"
        item_id = item.get("id")
        if not isinstance(item_id, str) or not SAFE_LABEL_RE.fullmatch(item_id):
            defect("job.manifest.json", "critical", f"{item_path}.id must match {SAFE_LABEL_RE.pattern}")
        elif item_id in seen_work_item_ids:
            defect("job.manifest.json", "critical", f"{item_path}.id duplicates {item_id}")
        else:
            seen_work_item_ids.add(item_id)
        worker_type = item.get("worker_type", "worker")
        if worker_type not in {"worker", RESEARCH_WORKER_TYPE}:
            defect(
                "job.manifest.json",
                "critical",
                f"{item_path}.worker_type must be 'worker' or 'research-worker'",
            )
        if worker_type == RESEARCH_WORKER_TYPE:
            has_research_work_item = True
            if "route_class" in item:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.route_class must be omitted for research-worker items",
                )
        else:
            worker_route_class_count += 1
            route_class = item.get("route_class")
            if route_class not in MANIFEST_WORKER_ROUTE_CLASSES:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.route_class must be one of {', '.join(MANIFEST_WORKER_ROUTE_CLASSES)}",
                )
            route_reason = item.get("route_class_reason")
            if not isinstance(route_reason, str) or not route_reason.strip():
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.route_class_reason must be a non-empty string",
                )
            elif route_class == "normal-code" and route_reason.strip() == "inferred_normal_code_default":
                default_normal_route_count += 1
        if worker_type == RESEARCH_WORKER_TYPE:
            route_reason = item.get("route_class_reason")
            if not isinstance(route_reason, str) or not route_reason.strip():
                defect(
                    "job.manifest.json",
                    "critical",
                    f"{item_path}.route_class_reason must explain research-worker routing",
                )
        _lint_work_item_fields(defect, branch, item, item_path, source_attachment_labels, git_status)
    _lint_work_item_cross_checks(defect, branch, work_items, branch_owned_paths_value)
    return _BranchRouteTally(has_research_work_item, worker_route_class_count, default_normal_route_count)


def _lint_work_item_cross_checks(defect, branch: dict, work_items: list, branch_owned_paths_value: list) -> None:
    """Validate cross-item depends_on ordering, owned_paths overlap, and derived branch ownership."""
    known_work_item_ids = {item.get("id") for item in work_items if isinstance(item, dict)}
    work_item_order = {
        item.get("id"): index
        for index, item in enumerate(work_items)
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for index, item in enumerate(work_items):
        if not isinstance(item, dict):
            continue
        for dep in item.get("depends_on", []):
            if dep not in known_work_item_ids:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch {branch.get('id')} work_items[{index}] depends on unknown work item: {dep}",
                )
            elif work_item_order.get(dep, index) >= index:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch {branch.get('id')} work_items[{index}] depends_on must reference only prior work item ids: {dep}",
                )
    for left_index, left in enumerate(work_items):
        if not isinstance(left, dict):
            continue
        for right_index in range(left_index + 1, len(work_items)):
            right = work_items[right_index]
            if not isinstance(right, dict):
                continue
            left_paths = left.get("owned_paths", []) if isinstance(left.get("owned_paths"), list) else []
            right_paths = right.get("owned_paths", []) if isinstance(right.get("owned_paths"), list) else []
            overlaps = [
                (left_path, right_path)
                for left_path in left_paths
                for right_path in right_paths
                if isinstance(left_path, str) and isinstance(right_path, str) and paths_overlap(left_path, right_path)
            ]
            if not overlaps:
                continue
            left_id = left.get("id")
            right_id = right.get("id")
            dependency_serialized = (
                isinstance(left_id, str)
                and isinstance(right.get("depends_on"), list)
                and left_id in right.get("depends_on", [])
            ) or (
                isinstance(right_id, str)
                and isinstance(left.get("depends_on"), list)
                and right_id in left.get("depends_on", [])
            )
            if not dependency_serialized and not has_contention_reason(
                left.get("contention_reason"),
                right.get("contention_reason"),
                branch.get("worker_contention_reason"),
            ):
                overlap_text = ", ".join(f"{left_path} vs {right_path}" for left_path, right_path in overlaps)
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch {branch.get('id')} work item owned_paths overlap without dependency or contention_reason: {left_id} and {right_id}: {overlap_text}",
                )
    derived_branch_owned = []
    for item in work_items:
        for value in item.get("owned_paths", []):
            if isinstance(value, str) and value not in derived_branch_owned:
                derived_branch_owned.append(value)
    if branch_owned_paths_value != derived_branch_owned:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} owned_paths must equal the ordered union of work item owned_paths",
        )


def _lint_branch_worker_parallelism(
    defect, manifest: dict, branch: dict, work_items: list, max_workers: object
) -> None:
    """Validate one branch's worker_parallelism block and contract refs."""
    worker_parallelism = branch.get("worker_parallelism", {})
    if not isinstance(worker_parallelism, dict):
        defect("job.manifest.json", "critical", f"branch {branch.get('id')} worker_parallelism must be an object")
        worker_parallelism = {}
    if worker_parallelism.get("parallelism_default") is not True:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.parallelism_default must be true",
        )
    if worker_parallelism.get("scheduling_mode") != "rolling":
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.scheduling_mode must be rolling",
        )
    expected_scheduler_path = (
        CONTRACT.worker_scheduler_path(str(branch.get("id", ""))) if isinstance(branch.get("id"), str) else ""
    )
    if worker_parallelism.get("scheduler_path") != expected_scheduler_path:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.scheduler_path must be {expected_scheduler_path!r}",
        )
    if worker_parallelism.get("max_active_worker_packets") != max_workers:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.max_active_worker_packets must match branch max_active_worker_packets",
        )
    if worker_parallelism.get("max_worker_packets_per_branch") != MAX_WORKER_PACKETS_PER_BRANCH:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.max_worker_packets_per_branch must be 4",
        )
    if "serial_reason" in worker_parallelism:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.serial_reason is obsolete; use serial_reasons",
        )
    worker_serial_reasons = worker_parallelism.get("serial_reasons", [])
    if not isinstance(worker_serial_reasons, list) or any(
        not isinstance(item, str) or not item.strip() for item in worker_serial_reasons
    ):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.serial_reasons must be an array of non-empty strings",
        )
        worker_serial_reasons = []
    if (
        not isinstance(worker_parallelism.get("parallelization_rationale"), str)
        or not worker_parallelism.get("parallelization_rationale", "").strip()
    ):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.parallelization_rationale must be non-empty",
        )
    manifest_execution_strategy = (
        manifest.get("execution_strategy") if isinstance(manifest.get("execution_strategy"), dict) else {}
    )
    if branch.get("execution_strategy_ref") != manifest_execution_strategy.get("id"):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} execution_strategy_ref must match manifest execution_strategy.id",
        )
    if branch.get("route_contract_sha256") != manifest.get("route_contract_sha256"):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} route_contract_sha256 must match manifest route_contract_sha256",
        )
    if is_strict_int(max_workers) and max_workers < MAX_WORKER_PACKETS_PER_BRANCH and not worker_serial_reasons:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} max_active_worker_packets below 4 requires worker_parallelism.serial_reasons",
        )
    if (
        is_strict_int(max_workers)
        and isinstance(work_items, list)
        and len(work_items) == 1
        and max_workers > 1
        and not worker_serial_reasons
    ):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} has one worker while max_active_worker_packets > 1 without worker_parallelism.serial_reasons",
        )
    if (
        is_strict_int(max_workers)
        and isinstance(work_items, list)
        and ready_worker_count(work_items) < min(max_workers, len(work_items))
        and not worker_serial_reasons
    ):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} has too few initially eligible worker items under max_active_worker_packets without worker_parallelism.serial_reasons",
        )
    if (
        isinstance(work_items, list)
        and len(work_items) > 2
        and longest_worker_chain(work_items) >= len(work_items) - 1
        and not worker_serial_reasons
    ):
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker dependency chain serializes most work without worker_parallelism.serial_reasons",
        )
    dependency_policy = worker_parallelism.get("dependency_policy", "")
    if not isinstance(dependency_policy, str) or "depends_on" not in dependency_policy:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.dependency_policy must mention depends_on",
        )
    slot_refill = worker_parallelism.get("slot_refill", "")
    if not isinstance(slot_refill, str) or "launch" not in slot_refill.lower():
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} worker_parallelism.slot_refill must describe launching replacements",
        )


def _lint_branch_paths_and_prompt(
    defect, bundle_dir: Path, manifest: dict, branch: dict, runtime_index: dict | None
) -> None:
    """Validate one branch's path fields and its rendered branch prompt artifact."""
    for key in ["prompt", "status_path", "review_path"]:
        message = relative_path_defect(branch.get(key), key)
        if message:
            defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
    expected_gate_path = (
        CONTRACT.pre_review_gate_path(str(branch.get("id", ""))) if isinstance(branch.get("id"), str) else ""
    )
    if branch.get("pre_review_gate_path") != expected_gate_path:
        defect(
            "job.manifest.json",
            "critical",
            f"branch {branch.get('id')} pre_review_gate_path must be {expected_gate_path!r}",
        )
    message = relative_path_defect(branch.get("pre_review_gate_path"), "pre_review_gate_path")
    if message:
        defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
    message = relative_path_defect(branch.get("worktree_path"), "worktree_path")
    if message:
        defect("job.manifest.json", "critical", f"branch {branch.get('id')}: {message}")
    prompt_value = branch.get("prompt", "")
    if relative_path_defect(prompt_value, "prompt"):
        return
    prompt_path = resolve(bundle_dir, prompt_value)
    if not prompt_path.exists():
        defect(str(prompt_path), "critical", f"branch prompt missing for {branch.get('id')}")
        return
    text = prompt_path.read_text(encoding="utf-8")
    lint_generated_prompt_text(defect, str(prompt_path), text, is_branch_prompt=True)
    expected_status_path = (
        branch.get("status_path") if isinstance(branch.get("status_path"), str) else "branches/Bxx.status.json"
    )
    lint_validator_command_snippets(
        defect,
        str(prompt_path),
        text,
        {"validate_branch_status.py": expected_status_path},
    )
    require_text_phrases(
        defect,
        str(prompt_path),
        text,
        branch_prompt_required_phrases(branch.get("id")),
        severity="major",
        message_prefix="branch prompt missing section",
    )
    if not has_dod(text):
        defect(str(prompt_path), "critical", f"branch {branch.get('id')} lacks a falsifiable Definition of Done")
    if runtime_index is not None and manifest.get("runtime_index_path") not in text:
        defect(str(prompt_path), "major", f"branch {branch.get('id')} prompt must reference runtime_index_path")
    route_hash = manifest.get("route_contract_sha256")
    if isinstance(route_hash, str) and route_hash and route_hash not in text:
        defect(str(prompt_path), "major", f"branch {branch.get('id')} prompt must embed route_contract_sha256")
    strategy_id = (
        manifest.get("execution_strategy", {}).get("id")
        if isinstance(manifest.get("execution_strategy"), dict)
        else None
    )
    if isinstance(strategy_id, str) and strategy_id and strategy_id not in text:
        defect(str(prompt_path), "major", f"branch {branch.get('id')} prompt must embed execution strategy id")


def _lint_branches(
    defect,
    bundle_dir: Path,
    manifest: dict,
    branches: list,
    ids: list,
    source_attachment_labels: set[str],
    git_status: dict,
    runtime_index: dict | None,
) -> _BranchRouteTally:
    """Validate every branch (keys, depends_on, work_items, parallelism, prompt) in manifest order.

    Returns the aggregated route tally consumed by downstream lint() checks.
    """
    tally = _BranchRouteTally(False, 0, 0)
    for branch in branches:
        for key in BRANCH_REQUIRED_KEYS:
            if key not in branch:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} missing key: {key}")
        for key in ["objective", "scope"]:
            if not isinstance(branch.get(key), str) or not branch.get(key, "").strip():
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} {key} must be a non-empty string")
        depends_on = branch.get("depends_on", [])
        if not isinstance(depends_on, list):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on must be a list")
            depends_on = []
        seen_branch_deps = set()
        branch_index = ids.index(branch.get("id")) if branch.get("id") in ids else -1
        for dep_index, dep in enumerate(depends_on):
            if not isinstance(dep, str) or not SAFE_ID_RE.fullmatch(dep):
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch {branch.get('id')} depends_on[{dep_index}] is not a safe branch id",
                )
                continue
            if dep in seen_branch_deps:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends_on repeats branch {dep}")
            seen_branch_deps.add(dep)
            if dep not in ids:
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} depends on unknown branch {dep}")
            elif dep == branch.get("id"):
                defect("job.manifest.json", "critical", f"branch {branch.get('id')} cannot depend on itself")
            elif ids.index(dep) >= branch_index:
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch {branch.get('id')} depends_on must reference only prior branch ids; invalid dependency: {dep}",
                )
        max_workers = branch.get("max_active_worker_packets")
        if not is_strict_int(max_workers) or max_workers < 1 or max_workers > MAX_WORKER_PACKETS_PER_BRANCH:
            defect(
                "job.manifest.json",
                "critical",
                f"branch {branch.get('id')} max_active_worker_packets must be an integer from 1 to 4",
            )
        branch_owned_paths_value = branch.get("owned_paths", [])
        if not isinstance(branch_owned_paths_value, list) or any(
            not isinstance(item, str) or not item.strip() for item in branch_owned_paths_value
        ):
            defect(
                "job.manifest.json",
                "critical",
                f"branch {branch.get('id')} owned_paths must be a derived array of non-empty strings",
            )
            branch_owned_paths_value = []
        work_items = branch.get("work_items", [])
        if not isinstance(work_items, list) or len(work_items) < 1 or len(work_items) > MAX_WORKER_PACKETS_PER_BRANCH:
            defect(
                "job.manifest.json",
                "critical",
                f"branch {branch.get('id')} work_items must contain 1 to 4 worker packets",
            )
        elif any(not isinstance(item, dict) for item in work_items):
            defect("job.manifest.json", "critical", f"branch {branch.get('id')} work_items entries must be objects")
        else:
            tally = _lint_work_items(
                defect, branch, work_items, branch_owned_paths_value, source_attachment_labels, git_status, tally
            )
        _lint_branch_worker_parallelism(defect, manifest, branch, work_items, max_workers)
        _lint_branch_paths_and_prompt(defect, bundle_dir, manifest, branch, runtime_index)
    return tally


def _lint_cross_branch_ownership(defect, branches: list) -> None:
    """Validate cross-branch context/ownership, verification command ownership, and branch overlap."""
    branch_owned_paths: dict[str, list[str]] = {}
    branch_deps: dict[str, list[str]] = {}
    branch_contention: dict[str, object] = {}
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        bid = branch["id"]
        branch_deps[bid] = branch.get("depends_on", []) if isinstance(branch.get("depends_on"), list) else []
        branch_contention[bid] = branch.get("contention_reason")
        owned = (
            [path for path in branch.get("owned_paths", []) if isinstance(path, str)]
            if isinstance(branch.get("owned_paths"), list)
            else []
        )
        branch_owned_paths[bid] = owned
    for branch in branches:
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        bid = branch["id"]
        work_items = branch.get("work_items", []) if isinstance(branch.get("work_items"), list) else []
        has_context = any(
            isinstance(item, dict)
            and (
                isinstance(item.get("context_files"), list)
                and bool(item.get("context_files"))
                or isinstance(item.get("source_attachment_refs"), list)
                and bool(item.get("source_attachment_refs"))
            )
            for item in work_items
        )
        if branch_deps.get(bid) and not has_context:
            reason = branch.get("dependency_context_reason")
            if not isinstance(reason, str) or not reason.strip():
                defect(
                    "job.manifest.json",
                    "major",
                    f"branch {bid} depends_on prior branches but declares no work item context_files and no dependency_context_reason",
                )
        for owned_path in branch_owned_paths.get(bid, []):
            if owned_path == ".gitkeep" or owned_path.endswith("/.gitkeep"):
                defect(
                    "job.manifest.json",
                    "major",
                    f"branch {bid} owns placeholder file {owned_path}; own the containing directory or explicit artifact files instead",
                )
        allowed_branch_ids = {bid, *dependency_closure(bid, branch_deps)}
        _lint_branch_verification_ownership(defect, bid, work_items, branch_owned_paths, allowed_branch_ids)
    _lint_branch_overlap(defect, branch_owned_paths, branch_deps, branch_contention)


def _lint_branch_verification_ownership(
    defect, bid: str, work_items: list, branch_owned_paths: dict[str, list[str]], allowed_branch_ids: set
) -> None:
    """Validate that each verification command of a branch only touches owned/depended paths."""
    for item_index, item in enumerate(work_items):
        if not isinstance(item, dict):
            continue
        verification = item.get("verification", [])
        if not isinstance(verification, list):
            continue
        for command_index, command in enumerate(verification):
            if not isinstance(command, str) or not command.strip():
                continue
            for ref in command_references(command):
                owners: list[tuple[str, str, str]] = []
                for candidate in ref.get("candidates", []):
                    if not isinstance(candidate, str):
                        continue
                    owner = owner_for_path(candidate, branch_owned_paths)
                    if owner is not None:
                        owners.append((owner[0], owner[1], candidate))
                if owners:
                    owner_id, owned_path, matched_path = owners[0]
                    if owner_id not in allowed_branch_ids:
                        ref_label = (
                            f"python module {ref.get('value')}"
                            if ref.get("kind") == "python_module"
                            else f"path {ref.get('value')}"
                        )
                        defect(
                            "job.manifest.json",
                            "critical",
                            f"branch {bid} work_items[{item_index}].verification[{command_index}] references {ref_label} mapped to {matched_path} owned by {owner_id}, but {bid} does not depend on {owner_id}",
                        )
                    continue
                value = ref.get("value")
                if ref.get("kind") == "path" and isinstance(value, str) and re.search(r"(^|/)tests?/", value):
                    defect(
                        "job.manifest.json",
                        "critical",
                        f"branch {bid} work_items[{item_index}].verification[{command_index}] references unowned test path {value}; tests must be owned by this branch or a completed dependency",
                    )


def _lint_branch_overlap(
    defect,
    branch_owned_paths: dict[str, list[str]],
    branch_deps: dict[str, list[str]],
    branch_contention: dict[str, object],
) -> None:
    """Validate cross-branch owned_paths overlaps require a dependency or contention reason."""
    branch_ids_for_overlap = list(branch_owned_paths)
    for left_index, left_id in enumerate(branch_ids_for_overlap):
        for right_id in branch_ids_for_overlap[left_index + 1 :]:
            overlaps = [
                (left_path, right_path)
                for left_path in branch_owned_paths[left_id]
                for right_path in branch_owned_paths[right_id]
                if paths_overlap(left_path, right_path)
            ]
            if not overlaps:
                continue
            dependency_serialized = left_id in branch_deps.get(right_id, []) or right_id in branch_deps.get(left_id, [])
            # Only a genuine per-pair signal waives a cross-branch owned_paths overlap: an actual
            # dependency relationship (above) or a real contention_reason on one of the two
            # branches. Manifest-level parallelization.serial_reasons (scheduling-capacity notes,
            # auto-populated for single-branch / low-cap / narrow-DAG bundles) must NOT waive it,
            # or two concurrently-running branch worktrees could write the same file undetected.
            if not dependency_serialized and not has_contention_reason(
                branch_contention.get(left_id),
                branch_contention.get(right_id),
            ):
                overlap_text = ", ".join(f"{left_path} vs {right_path}" for left_path, right_path in overlaps)
                defect(
                    "job.manifest.json",
                    "critical",
                    f"branch owned_paths overlap without dependency or contention_reason: {left_id} and {right_id}: {overlap_text}",
                )


def _lint_manifest_core_fields(defect, manifest: dict, config_check_status: str) -> tuple[str, set[str]]:
    """Validate top-level manifest scalar/array fields and source attachments.

    Returns (compatibility_status, source_attachment_labels). Behavior-preserving
    extraction of the lint() top-level-field block; source_attachment_paths is
    computed and discarded exactly as the original did.
    """
    check_summary = (
        manifest.get("goal_config_check_summary") if isinstance(manifest.get("goal_config_check_summary"), dict) else {}
    )
    if check_summary.get("status") == "pass":
        accepted_routes = check_summary.get("accepted_route_count")
        if not (isinstance(accepted_routes, int) and not isinstance(accepted_routes, bool) and accepted_routes > 0):
            defect(
                "job.manifest.json",
                "warning",
                "goal_config_check_summary.status is pass but route availability is unverified; treat this as config_schema_pass_routes_unverified, not accepted-route availability",
            )
    compatibility_status = "not_applicable"
    if config_check_status not in {"not_configured"}:
        compatibility_status = "pass" if config_check_status == "pass" else "failed"
    if not safe_branch_name(manifest.get("base_ref")):
        defect("job.manifest.json", "critical", f"base_ref is not safe: {manifest.get('base_ref')!r}")
    for key in ["artifact_policy", "cleanup_policy"]:
        if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip():
            defect("job.manifest.json", "critical", f"{key} must be non-empty")
    for key in ["title", "goal", "source_summary"]:
        if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip():
            defect("job.manifest.json", "critical", f"{key} must be a non-empty string")
    for key in ["required_evidence", "final_dod"]:
        values = manifest.get(key)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(item, str) or not item.strip() for item in values)
        ):
            defect("job.manifest.json", "critical", f"{key} must be a non-empty array of strings")
    if not isinstance(manifest.get("preflight_input_precedence"), dict):
        defect("job.manifest.json", "critical", "preflight_input_precedence must be an object")
    manifest_text = collect_manifest_text(manifest)
    if (
        EXACT_SOURCE_RE.search(manifest_text)
        and not has_source_attachment(manifest)
        and not has_inline_source_payload(manifest_text)
    ):
        defect(
            "job.manifest.json",
            "critical",
            "exact source/instance/list is referenced but no inline payload or source_attachments entry exists",
        )
    source_attachment_labels, source_attachment_paths = _lint_source_attachments(defect, manifest)
    if RUNTIME_CAP_RE.search(manifest_text) and not concrete_runtime_cap(manifest.get("runtime_cap")):
        defect(
            "job.manifest.json",
            "critical",
            "success criteria reference a runtime cap but job.manifest.json lacks a concrete runtime_cap value or CLI flag",
        )
    return compatibility_status, source_attachment_labels


def lint(bundle_dir: Path) -> dict:
    git_status = _git_repo_status(bundle_dir)
    defects: list[dict] = []

    def defect(file: str, severity: str, message: str) -> None:
        defects.append({"file": file, "severity": severity, "message": message})

    manifest_path = bundle_dir / "job.manifest.json"
    if not manifest_path.exists():
        defect("job.manifest.json", "critical", "manifest is missing")
        return result(
            defects,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            branch_count=0,
            config_check_status="not_available",
            compatibility_status="not_applicable",
            git_repo_status=git_status,
        )

    try:
        manifest = load_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        defect("job.manifest.json", "critical", f"manifest is not valid JSON: {exc}")
        return result(
            defects,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            branch_count=0,
            config_check_status="not_available",
            compatibility_status="not_applicable",
            git_repo_status=git_status,
        )
    git_status = _manifest_git_repo_status(manifest, git_status)
    unignored_bundle_path = bundle_inside_git_unignored(git_status, bundle_dir)
    if unignored_bundle_path:
        defect(
            "job.manifest.json",
            "warning",
            f"bundle path {unignored_bundle_path} is inside the git work tree and is not ignored; generated artifacts may dirty the repository",
        )

    for dirname in REQUIRED_BUNDLE_DIRS:
        if not (bundle_dir / dirname).is_dir():
            defect(dirname + "/", "critical", f"required bundle directory is missing: {dirname}/")

    for key in MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            defect("job.manifest.json", "critical", f"missing key: {key}")
    validate_preflight_lite_records(defect, bundle_dir, manifest)
    validate_telemetry_policy(defect, manifest)
    goal_config, config_check_status = validate_goal_config_manifest(defect, bundle_dir, manifest)
    runtime_index = validate_runtime_metadata(defect, bundle_dir, manifest)

    compatibility_status, source_attachment_labels = _lint_manifest_core_fields(defect, manifest, config_check_status)

    max_active = manifest.get("max_active_branch_agents")
    if not is_strict_int(max_active) or max_active < 1 or max_active > MAX_ACTIVE_BRANCH_AGENTS:
        defect("job.manifest.json", "critical", "max_active_branch_agents must be an integer from 1 to 4")

    parallelization, serial_reasons, has_serial_reason = _lint_parallelization(defect, manifest)

    if manifest.get("adaptation_policy") != CONTRACT.ADAPTATION_POLICY:
        defect("job.manifest.json", "critical", "adaptation_policy must match the shared amendment proposal policy")

    branches = manifest.get("branches", [])
    _lint_branch_concurrency(defect, parallelization, branches, max_active, serial_reasons, has_serial_reason)

    compatibility_status = _lint_model_policies(defect, manifest, goal_config, compatibility_status)
    research_worker_policy = _lint_research_worker_policy(defect, manifest)

    ids = _lint_branch_identity(defect, branches)
    _lint_reserved_bundle_paths(defect, branches)
    _lint_waves(defect, manifest, branches, ids, has_serial_reason)
    _lint_main_prompt(defect, bundle_dir, manifest)
    _lint_runtime_rules(defect, bundle_dir, manifest)
    _lint_bootloader_and_report(defect, bundle_dir, manifest)
    _lint_goal_config_selection(defect, bundle_dir)

    tally = _lint_branches(
        defect, bundle_dir, manifest, branches, ids, source_attachment_labels, git_status, runtime_index
    )

    if tally.worker_route_class_count and tally.default_normal_route_count == tally.worker_route_class_count:
        defect(
            "job.manifest.json",
            "warning",
            "all worker route classes fell back to default normal-code; check whether docs, mechanical, small-edit, complex-code, or research-worker routing should apply",
        )

    if tally.has_research_work_item and research_worker_policy is None:
        defect(
            "job.manifest.json",
            "critical",
            "research_worker_policy is required when any work item uses worker_type='research-worker'",
        )

    _lint_cross_branch_ownership(defect, branches)

    return result(
        defects,
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        branch_count=len(manifest.get("branches", [])) if isinstance(manifest, dict) else 0,
        config_check_status=config_check_status,
        compatibility_status=compatibility_status,
        git_repo_status=git_status,
    )


def result(
    defects: list[dict],
    *,
    bundle_dir: Path,
    manifest_path: Path,
    branch_count: int,
    config_check_status: str,
    compatibility_status: str,
    git_repo_status: dict,
) -> dict:
    schema_status = "pass" if not any(item["severity"] in {"critical", "major"} for item in defects) else "failed"
    severity_counts = {
        severity: len([item for item in defects if item.get("severity") == severity])
        for severity in ("critical", "major", "warning")
    }
    runtime_launch_blocked_reason = None
    runtime_launch_status = "pass"
    if git_repo_status.get("repo_is_git") is False or git_repo_status.get("status") == "not_in_repo":
        runtime_launch_status = "blocked"
        runtime_launch_blocked_reason = (
            "repository root is not a git work tree; runtime branch/worktree orchestration is blocked"
        )
    elif git_repo_status.get("base_ref_status") == "missing":
        runtime_launch_status = "blocked"
        runtime_launch_blocked_reason = f"base_ref does not exist: {git_repo_status.get('base_ref')}"
    reported_status = schema_status
    if schema_status == "pass" and runtime_launch_status != "pass":
        reported_status = "launch_blocked"
    return {
        "status": reported_status,
        "status_kind": "schema_and_launch_readiness",
        "status_meaning": "status is launch-oriented; schema/artifact lint is reported separately in schema_lint_status",
        "schema_lint_status": schema_status,
        "launch_status": {
            "status": runtime_launch_status,
            "allowed": schema_status == "pass" and runtime_launch_status == "pass",
            "blocked_reason": runtime_launch_blocked_reason,
        },
        "runtime_launch_status": runtime_launch_status,
        "runtime_launch_allowed": schema_status == "pass" and runtime_launch_status == "pass",
        "runtime_launch_blocked_reason": runtime_launch_blocked_reason,
        "bundle_path": bundle_dir.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "branch_count": branch_count,
        "config_check_status": config_check_status,
        "git_repo_status": git_repo_status,
        "compatibility_status": compatibility_status,
        "defect_count": len(defects),
        "severity_counts": severity_counts,
        "defects": defects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--output")
    parser.add_argument(
        "--no-write", action="store_true", help="Print lint JSON to stdout without mutating preflight.lint.json."
    )
    parser.add_argument("--json", action="store_true", help="Print lint JSON to stdout instead of the output path.")
    args = parser.parse_args()

    bundle_dir = resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
    data = lint(bundle_dir)
    if args.no_write:
        if args.output:
            raise SystemExit("--no-write cannot be combined with --output")
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0 if data["schema_lint_status"] == "pass" else 1
    canonical_path = bundle_dir / "preflight.lint.json"
    output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else canonical_path
    rendered = json.dumps(data, indent=2, sort_keys=True) + "\n"
    output_path.write_text(rendered, encoding="utf-8")
    if output_path.resolve() != canonical_path.resolve():
        canonical_path.write_text(rendered, encoding="utf-8")
    if args.json or args.no_write:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(output_path)
    return 0 if data["schema_lint_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
