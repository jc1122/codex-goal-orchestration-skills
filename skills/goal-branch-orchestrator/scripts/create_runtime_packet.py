#!/usr/bin/env python3
"""Create model-aware worker, research-worker, or reviewer packets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path


def _load_shared_script(module_name: str, script_name: str, label: str):
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / script_name
    if not path.exists():
        raise SystemExit(f"missing {label}: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load {label}: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_shared_script("goal_shared_orchestration_contract", "orchestration_contract.py", "shared orchestration contract")
STATUS_VALIDATION = _load_shared_script("goal_shared_status_validation", "status_validation.py", "shared status validation helpers")
CONTEXT_PACK = _load_shared_script("goal_shared_context_pack", "context_pack.py", "shared context pack helper")
GEMINI_COMMAND = "gemini"
GEMINI_APPROVAL_MODE = "yolo"
GEMINI_PRO_MODEL = "gemini-3.1-pro-preview"
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"
GEMINI_PROBE_TIMEOUT_SECONDS = 20
GEMINI_PROBE_PROMPT = "Return exactly: GEMINI_MODEL_PROBE_OK"
SPARK_MODEL = CONTRACT.CODEX_ROUTE_MODELS["codex-spark"]
MINI_MODEL = CONTRACT.CODEX_ROUTE_MODELS["codex-mini"]
RESEARCH_MODEL = CONTRACT.CODEX_ROUTE_MODELS[CONTRACT.RESEARCH_ALIASES[0]]
RESEARCH_FALLBACK_MODEL = CONTRACT.CODEX_ROUTE_MODELS[CONTRACT.RESEARCH_ALIASES[1]]
RESEARCH_ALIAS = CONTRACT.RESEARCH_ALIASES[0]
RESEARCH_FALLBACK_ALIAS = CONTRACT.RESEARCH_ALIASES[1]
REVIEWER_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.5"]
REVIEWER_FALLBACK_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.4"]
REVIEWER_MINI_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.4-mini"]
WORKER_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.WORKER_ATTEMPT_TIMEOUT_SECONDS
RESEARCH_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.RESEARCH_ATTEMPT_TIMEOUT_SECONDS
REVIEWER_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.REVIEWER_ATTEMPT_TIMEOUT_SECONDS
TIMEOUT_KILL_AFTER_SECONDS = CONTRACT.TIMEOUT_KILL_AFTER_SECONDS
GEMINI_STATUS_BEGIN = "BEGIN_WORKER_STATUS_JSON"
GEMINI_STATUS_END = "END_WORKER_STATUS_JSON"
MAX_CONTEXT_PACK_CHARS = CONTEXT_PACK.DEFAULT_TOTAL_CHARS
MAX_CONTEXT_FILE_CHARS = CONTEXT_PACK.DEFAULT_PER_FILE_CHARS
DEFAULT_WORKER_LADDER = CONTRACT.DEFAULT_WORKER_LADDER
ALLOWED_WORKER_ROUTES = CONTRACT.ALLOWED_WORKER_ROUTES
DEFAULT_WORKER_ROUTE_CLASS = CONTRACT.DEFAULT_WORKER_ROUTE_CLASS
WORKER_ROUTE_CLASSES = CONTRACT.WORKER_ROUTE_CLASSES
WORKER_ROUTE_CLASS_LADDERS = CONTRACT.WORKER_ROUTE_CLASS_LADDERS
WORKER_ROUTE_LABELS = {
    "gemini-pro": "Gemini Pro",
    "gemini-flash": "Gemini Flash",
    "codex-spark": "Codex Spark",
    "codex-mini": "Codex mini",
}
CODEX_LEAN_EXEC_FLAGS_TEXT = " ".join(CONTRACT.CODEX_LEAN_EXEC_FLAGS)
WORKER_ROUTE_COMMANDS = {
    "gemini-pro": f"gemini --model {GEMINI_PRO_MODEL} --approval-mode {GEMINI_APPROVAL_MODE}",
    "gemini-flash": f"gemini --model {GEMINI_FLASH_MODEL} --approval-mode {GEMINI_APPROVAL_MODE}",
    "codex-spark": f"codex exec --ephemeral {CODEX_LEAN_EXEC_FLAGS_TEXT} -m {SPARK_MODEL} -s workspace-write",
    "codex-mini": f"codex exec --ephemeral {CODEX_LEAN_EXEC_FLAGS_TEXT} -m {MINI_MODEL} -s workspace-write",
}
WORKER_ROUTE_EVENT_LABELS = {
    "gemini-pro": "gemini-pro",
    "gemini-flash": "gemini-flash",
    "codex-spark": "spark",
    "codex-mini": "mini",
}
CODEX_WORKER_ROUTES = frozenset({"codex-spark", "codex-mini"})
WORKER_PACKET_PROMPT = "Follow the complete worker packet instructions provided on stdin."
REVIEW_ROUTE_MODELS = {
    alias: CONTRACT.CODEX_ROUTE_MODELS[alias]
    for route in CONTRACT.REVIEW_MODEL_ROUTES.values()
    for alias in route
}


PATH_RULES = _load_shared_script("goal_shared_path_rules", "path_rules.py", "shared path rules")
require_safe_label = PATH_RULES.require_safe_packet_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
safe_branch_name = PATH_RULES.safe_branch_name
shell_quote = CONTRACT.shell_quote


def nonempty_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_owned_paths(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        normalized.append(PATH_RULES.require_relative_path(value, "owned paths"))
    return normalized


def normalize_context_files(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        path = resolve_absolute_path(value, "--context-file", must_exist=True)
        normalized.append(path.as_posix())
    return normalized


def normalize_worker_ladder(
    values: list[str],
    *,
    default_ladder: list[str] | None = None,
    allowed_routes: list[str] | None = None,
) -> list[str]:
    default_ladder = default_ladder or list(DEFAULT_WORKER_LADDER)
    allowed_routes = allowed_routes or list(default_ladder)
    if not values:
        return list(default_ladder)
    flattened = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                flattened.append(item)
    if not flattened:
        raise SystemExit("worker route must contain at least one route alias")
    seen = set()
    positions = []
    for alias in flattened:
        if alias not in allowed_routes:
            raise SystemExit(f"unsupported worker route alias: {alias!r}")
        if alias in seen:
            raise SystemExit(f"worker route alias repeated: {alias!r}")
        seen.add(alias)
        positions.append(default_ladder.index(alias) if alias in default_ladder else len(default_ladder) + allowed_routes.index(alias))
    if positions != sorted(positions):
        raise SystemExit(
            "worker route aliases must preserve standard ladder order: "
            + ", ".join(default_ladder)
        )
    return flattened


def normalize_route_class(value: object, *, allow_custom: bool = True) -> str:
    if value is None:
        return DEFAULT_WORKER_ROUTE_CLASS
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"route class must be one of {', '.join(WORKER_ROUTE_CLASSES)}")
    normalized = value.strip()
    allowed = WORKER_ROUTE_CLASSES if allow_custom else tuple(item for item in WORKER_ROUTE_CLASSES if item != "custom")
    if normalized not in allowed:
        raise SystemExit(f"route class must be one of {', '.join(allowed)}")
    return normalized


def worker_policy_from_manifest(manifest: dict | None) -> dict:
    if isinstance(manifest, dict) and isinstance(manifest.get("worker_model_policy"), dict):
        return manifest["worker_model_policy"]
    return CONTRACT.WORKER_MODEL_POLICY


def goal_config_from_manifest(manifest: dict | None, manifest_path: Path | None = None) -> dict | None:
    if isinstance(manifest, dict) and isinstance(manifest.get("goal_config"), dict):
        return manifest["goal_config"]
    if isinstance(manifest, dict) and manifest_path is not None:
        config_path = manifest.get("goal_config_path")
        if isinstance(config_path, str) and config_path.strip():
            candidate = (manifest_path.parent / config_path).resolve()
            if candidate.is_file():
                return load_json(candidate)
    return None


def policy_default_ladder(policy: dict) -> list[str]:
    ladder = policy.get("default_ladder")
    return list(ladder) if isinstance(ladder, list) and ladder else list(DEFAULT_WORKER_LADDER)


def policy_allowed_routes(policy: dict) -> list[str]:
    routes = policy.get("allowed_routes")
    return list(routes) if isinstance(routes, list) and routes else policy_default_ladder(policy)


def ladder_for_route_class(route_class: str, policy: dict | None = None) -> list[str]:
    if policy is None:
        return list(WORKER_ROUTE_CLASS_LADDERS.get(route_class, WORKER_ROUTE_CLASS_LADDERS[DEFAULT_WORKER_ROUTE_CLASS]))
    route_classes = policy.get("route_classes") if isinstance(policy.get("route_classes"), dict) else {}
    ladder = route_classes.get(route_class)
    if isinstance(ladder, list) and ladder:
        return list(ladder)
    return policy_default_ladder(policy)


def default_selection_reason(route_class: str) -> str:
    return CONTRACT.worker_route_class_reason(route_class)


def validate_route_class_selection(route_class: str, selected_ladder: list[str], selection_reason: str, policy: dict | None = None) -> None:
    if policy is not None:
        allowed = set(ladder_for_route_class(route_class, policy))
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            raise SystemExit(f"route_class {route_class!r} cannot use configured route aliases: " + ", ".join(disallowed))
        return
    if route_class in {"mechanical", "docs", "small-edit", "normal-code"}:
        allowed = set(ladder_for_route_class(route_class))
        disallowed = [alias for alias in selected_ladder if alias not in allowed]
        if disallowed:
            raise SystemExit(
                f"route_class {route_class!r} cannot use premium/full worker route aliases: "
                + ", ".join(disallowed)
            )
    if route_class == "complex-code":
        reason = selection_reason.lower()
        markers = ("complex", "risk", "cross-module", "premium", "architecture", "validator", "scheduler")
        if not any(marker in reason for marker in markers):
            raise SystemExit("--selection-reason for route_class 'complex-code' must include a concrete cost/risk justification")


def model_catalog_rows(path: Path) -> tuple[dict, dict[str, dict]]:
    data = load_json(path)
    if data.get("schema_version") != 1:
        raise SystemExit(f"model catalog schema_version must be 1: {path}")
    if data.get("status") != "pass":
        raise SystemExit(f"model catalog status must be pass before worker packet generation: {path}")
    rows = data.get("route_models")
    if not isinstance(rows, list):
        raise SystemExit(f"model catalog route_models must be a list: {path}")
    by_alias: dict[str, dict] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SystemExit(f"model catalog route_models[{index}] must be an object: {path}")
        alias = row.get("alias")
        if isinstance(alias, str) and alias:
            by_alias[alias] = row
    return data, by_alias


def apply_model_catalog_to_worker_ladder(
    selected_ladder: list[str],
    *,
    catalog_path: Path | None,
    explicit_routes: bool,
) -> tuple[list[str], dict | None]:
    if catalog_path is None:
        return selected_ladder, None

    data, rows = model_catalog_rows(catalog_path)
    retained: list[str] = []
    filtered: list[dict] = []
    defects: list[str] = []
    checked_aliases = [alias for alias in selected_ladder if alias in CODEX_WORKER_ROUTES]
    for alias in selected_ladder:
        if alias not in CODEX_WORKER_ROUTES:
            retained.append(alias)
            continue
        row = rows.get(alias)
        if row is None:
            defects.append(f"{alias}: missing from model catalog route_models")
            continue
        present = row.get("present")
        supported = row.get("supported_in_api")
        if present is True and supported is True:
            retained.append(alias)
            continue
        detail = {
            "alias": alias,
            "model": row.get("model"),
            "present": present,
            "supported_in_api": supported,
            "reason": "not present" if present is not True else "not supported_in_api",
        }
        if explicit_routes:
            defects.append(
                f"{alias}: model={detail['model']} present={present} supported_in_api={supported}"
            )
        else:
            filtered.append(detail)

    if defects:
        raise SystemExit(
            "model catalog rejects selected worker route(s); choose a supported route or omit explicit routes:\n"
            + "\n".join(f"- {item}" for item in defects)
        )
    if not retained:
        raise SystemExit(
            "model catalog removed every selected worker route; choose a supported worker route explicitly"
        )
    metadata = {
        "path": catalog_path.as_posix(),
        "source": data.get("source"),
        "status": data.get("status"),
        "checked_aliases": checked_aliases,
        "filtered_aliases": filtered,
    }
    return retained, metadata


def worker_route_commands(selected_ladder: list[str]) -> list[str]:
    commands = []
    for alias in selected_ladder:
        commands.append(WORKER_ROUTE_COMMANDS[alias])
    return commands


def event_label_for_alias(alias: str) -> str:
    if alias in WORKER_ROUTE_EVENT_LABELS:
        return WORKER_ROUTE_EVENT_LABELS[alias]
    label = re.sub(r"[^a-zA-Z0-9_.-]+", "-", alias).strip("-").lower()
    return label or "configured"


def render_attempt_args(args: object, *, context: dict[str, str]) -> list[str]:
    if not isinstance(args, list):
        return []
    rendered: list[str] = []
    for item in args:
        if isinstance(item, str):
            rendered.append(item.format(**context))
    return rendered


def configured_route_commands(selected_ladder: list[str], goal_config: dict) -> list[str]:
    models = goal_config.get("models", {})
    harnesses = goal_config.get("harnesses", {})
    commands: list[str] = []
    for alias in selected_ladder:
        model = models.get(alias, {})
        harness = harnesses.get(model.get("harness"), {})
        command = harness.get("command", model.get("harness", ""))
        args = harness.get("run_args") or harness.get("smoke_args") or []
        rendered = render_attempt_args(
            args,
            context={
                "alias": alias,
                "model": str(model.get("model", "")),
                "provider": str(model.get("provider", "")),
                "role": alias,
                "packet_id": "<packet_id>",
                "worktree": "<worktree>",
                "prompt": "<prompt>",
                "prompt_file": "<prompt.md>",
                "schema_file": "<schema.json>",
                "output_file": "<output.json>",
                "packet_dir": "<packet_dir>",
            },
        )
        commands.append(" ".join([str(command), *rendered]).strip())
    return commands


def configured_telemetry_attempts(
    selected_ladder: list[str],
    goal_config: dict,
    *,
    timeout_seconds: int,
    sandbox: str,
) -> list[dict]:
    models = goal_config.get("models", {})
    harnesses = goal_config.get("harnesses", {})
    attempts: list[dict] = []
    for alias in selected_ladder:
        model = models.get(alias)
        if not isinstance(model, dict):
            raise SystemExit(f"goal_config missing model role used by route ladder: {alias}")
        harness_name = model.get("harness")
        harness = harnesses.get(harness_name)
        if not isinstance(harness, dict):
            raise SystemExit(f"goal_config model {alias} references unknown harness: {harness_name}")
        kind = harness.get("kind")
        label = event_label_for_alias(alias)
        event_suffix = "jsonl" if kind in {"codex", "opencode"} else "log"
        attempt = {
            "alias": alias,
            "provider": kind,
            "provider_id": model.get("provider"),
            "model": model.get("model"),
            "harness": harness_name,
            "harness_kind": kind,
            "command_binary": harness.get("command"),
            "command": configured_route_commands([alias], goal_config)[0],
            "run_args": harness.get("run_args") or harness.get("smoke_args") or [],
            "run_readback": harness.get("run_readback", "stdout"),
            "effort": "configured",
            "sandbox": sandbox,
            "timeout_seconds": timeout_seconds,
            "event_logs": [f"events-{label}.{event_suffix}"],
            "probe_logs": [],
            "status_markers": {
                "begin": GEMINI_STATUS_BEGIN,
                "end": GEMINI_STATUS_END,
            },
        }
        if kind == "codex":
            attempt["ignore_user_config"] = True
            attempt["ignore_rules"] = True
            attempt["command"] = (
                "codex exec --ephemeral "
                + CODEX_LEAN_EXEC_FLAGS_TEXT
                + f" -m {model.get('model')} -s {sandbox}"
            )
        attempts.append(attempt)
    return attempts


def worker_telemetry_attempts(selected_ladder: list[str], goal_config: dict | None = None) -> list[dict]:
    if goal_config is not None:
        return configured_telemetry_attempts(
            selected_ladder,
            goal_config,
            timeout_seconds=WORKER_ATTEMPT_TIMEOUT_SECONDS,
            sandbox="workspace-write",
        )
    attempts = []
    for alias in selected_ladder:
        label = WORKER_ROUTE_EVENT_LABELS[alias]
        if alias == "gemini-pro":
            attempts.append(
                {
                    "alias": alias,
                    "provider": "gemini",
                    "model": GEMINI_PRO_MODEL,
                    "effort": "",
                    "command": WORKER_ROUTE_COMMANDS[alias],
                    "timeout_seconds": WORKER_ATTEMPT_TIMEOUT_SECONDS,
                    "event_logs": [f"events-{label}.log"],
                    "probe_logs": [f"events-{label}-probe.log"],
                    "probe_model": GEMINI_PRO_MODEL,
                    "probe_timeout_seconds": GEMINI_PROBE_TIMEOUT_SECONDS,
                    "probe_prompt": GEMINI_PROBE_PROMPT,
                    "status_markers": {
                        "begin": GEMINI_STATUS_BEGIN,
                        "end": GEMINI_STATUS_END,
                    },
                }
            )
        elif alias == "gemini-flash":
            attempts.append(
                {
                    "alias": alias,
                    "provider": "gemini",
                    "model": GEMINI_FLASH_MODEL,
                    "effort": "",
                    "command": WORKER_ROUTE_COMMANDS[alias],
                    "timeout_seconds": WORKER_ATTEMPT_TIMEOUT_SECONDS,
                    "event_logs": [f"events-{label}.log"],
                    "probe_logs": [f"events-{label}-probe.log"],
                    "probe_model": GEMINI_FLASH_MODEL,
                    "probe_timeout_seconds": GEMINI_PROBE_TIMEOUT_SECONDS,
                    "probe_prompt": GEMINI_PROBE_PROMPT,
                    "status_markers": {
                        "begin": GEMINI_STATUS_BEGIN,
                        "end": GEMINI_STATUS_END,
                    },
                }
            )
        else:
            model = SPARK_MODEL if alias == "codex-spark" else MINI_MODEL
            attempts.append(
                {
                    "alias": alias,
                    "provider": "codex",
                    "model": model,
                    "effort": "",
                    "command": WORKER_ROUTE_COMMANDS[alias],
                    "timeout_seconds": WORKER_ATTEMPT_TIMEOUT_SECONDS,
                    "event_logs": [f"events-{label}.jsonl"],
                    "probe_logs": [],
                    "ignore_user_config": True,
                    "ignore_rules": True,
                }
            )
    for attempt in attempts:
        attempt.setdefault("sandbox", "workspace-write")
    return attempts


def reviewer_telemetry_attempts(selected_ladder: list[str], goal_config: dict | None = None) -> list[dict]:
    if goal_config is not None:
        return configured_telemetry_attempts(
            selected_ladder,
            goal_config,
            timeout_seconds=REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
            sandbox="read-only",
        )
    return CONTRACT.codex_telemetry_attempts(
        selected_ladder,
        timeout_seconds=REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
        lean=True,
    )


def research_telemetry_attempts() -> list[dict]:
    return CONTRACT.codex_telemetry_attempts(
        [RESEARCH_ALIAS, RESEARCH_FALLBACK_ALIAS],
        timeout_seconds=RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
        sandbox="read-only",
        event_labels=["primary", "fallback"],
        search=True,
    )


def telemetry_function(role: str, packet_id: str, output_name: str, attempts: list[dict]) -> str:
    script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    return CONTRACT.telemetry_shell_function(
        script_path=script,
        packet_dir_expr="$packet_dir",
        packet_id=packet_id,
        role=role,
        output_name=output_name,
        prompt_name="prompt.md",
        attempts=attempts,
    )


def runtime_runner_path() -> Path:
    return Path(__file__).resolve().parent / "runtime_packet_runner.py"


def compact_launch_script() -> str:
    runner = runtime_runner_path()
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
runner={shell_quote(runner.as_posix())}
if [[ ! -f "$runner" ]]; then
  echo "runtime packet runner missing: $runner" >&2
  exit 127
fi
exec python3 "$runner" --packet-dir "$(pwd)"
"""


def exact_string_schema(value: str) -> dict:
    return {"type": "string", "const": value}


def nullable_string_schema() -> dict:
    return {"type": ["string", "null"]}


def strict_schema_defects(schema: object, path: str = "$") -> list[str]:
    defects: list[str] = []
    if not isinstance(schema, dict):
        return defects
    schema_type = schema.get("type")
    is_object = schema_type == "object" or "properties" in schema
    if is_object:
        if schema.get("additionalProperties") is not False:
            defects.append(f"{path}: object schemas must set additionalProperties=false")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            defects.append(f"{path}: object schemas must define properties")
            properties = {}
        required = schema.get("required")
        if not isinstance(required, list):
            defects.append(f"{path}: object schemas must define required")
            required = []
        missing_required = sorted(set(properties) - {item for item in required if isinstance(item, str)})
        if missing_required:
            defects.append(f"{path}: strict schemas must require every property: {', '.join(missing_required)}")
        for name, subschema in properties.items():
            defects.extend(strict_schema_defects(subschema, f"{path}.properties.{name}"))
    if schema_type == "array":
        if "items" not in schema:
            defects.append(f"{path}: array schemas must define items")
        else:
            defects.extend(strict_schema_defects(schema.get("items"), f"{path}.items"))
    return defects


def validate_openai_strict_schema(schema: dict, schema_name: str) -> None:
    defects = strict_schema_defects(schema)
    if defects:
        raise SystemExit(f"{schema_name} is not OpenAI strict-schema compatible:\n" + "\n".join(defects))


def status_schema(packet_id: str, branch: str, worktree: str, selected_ladder: list[str] | None = None) -> dict:
    repo_relative_path = r"^(?!/)(?!.*//)(?!.*\\)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$))(?![ MADRCU?!]{1,2} ).+"
    nonempty_string = {"type": "string", "minLength": 1}
    selected_ladder_schema = (
        {"type": "array", "items": nonempty_string, "const": selected_ladder}
        if selected_ladder
        else {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": list(DEFAULT_WORKER_LADDER)},
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.WORKER_STATUS_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("worker"),
            "status": {"type": "string", "enum": list(CONTRACT.STATUSES)},
            "branch": exact_string_schema(branch),
            "worktree": exact_string_schema(worktree),
            "route_class": {"type": "string", "enum": list(WORKER_ROUTE_CLASSES)},
            "selected_ladder": selected_ladder_schema,
            "selection_reason": nonempty_string,
            "changed_files": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": repo_relative_path}},
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "tests": {"type": "array", "items": nonempty_string},
            "blockers": {"type": "array", "items": nonempty_string},
            "handoff": nonempty_string,
        },
    }


def review_schema(packet_id: str, semantic_hashes: dict[str, str] | None = None, reuse_policy: dict | None = None) -> dict:
    nonempty_string = {"type": "string", "minLength": 1}
    semantic_hashes = semantic_hashes or {}
    semantic_properties = {
        key: {"type": "string", "const": value}
        for key, value in sorted(semantic_hashes.items())
    }
    _reuse_policy = reuse_policy or {
        "mode": "new",
        "accepted": False,
        "semantic_hashes_match": False,
        "source_review_path": None,
        "source_telemetry_path": None,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.REVIEW_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("reviewer"),
            "verdict": {"type": "string", "enum": [item for item in CONTRACT.REVIEW_STATUSES if item != "missing"]},
            "findings": {"type": "array", "items": nonempty_string},
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "verification_gaps": {"type": "array", "items": nonempty_string},
            "residual_risks": {"type": "array", "items": nonempty_string},
            "semantic_input_hashes": {
                "type": "object",
                "additionalProperties": False,
                "required": sorted(semantic_properties),
                "properties": semantic_properties,
            },
            "reuse_policy": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "mode",
                    "accepted",
                    "semantic_hashes_match",
                    "source_review_path",
                    "source_telemetry_path",
                ],
                "properties": {
                    "mode": {"type": "string", "enum": ["new", "reuse"], "const": _reuse_policy.get("mode", "new")},
                    "accepted": {"type": "boolean", "const": bool(_reuse_policy.get("accepted", False))},
                    "semantic_hashes_match": {
                        "type": "boolean",
                        "const": bool(_reuse_policy.get("semantic_hashes_match", False)),
                    },
                    "source_review_path": nullable_string_schema(),
                    "source_telemetry_path": nullable_string_schema(),
                },
            },
            "summary": nonempty_string,
        },
    }


def research_schema(packet_id: str, branch: str, worktree: str) -> dict:
    repo_relative_path = r"^(?!/)(?!.*//)(?!.*\\)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$))(?![ MADRCU?!]{1,2} ).+"
    url = r"^https?://[^ \t\r\n]+$"
    nonempty_string = {"type": "string", "minLength": 1}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CONTRACT.RESEARCH_STATUS_REQUIRED),
        "properties": {
            "packet_id": exact_string_schema(packet_id),
            "role": exact_string_schema("research-worker"),
            "status": {"type": "string", "enum": list(CONTRACT.STATUSES)},
            "branch": exact_string_schema(branch),
            "worktree": exact_string_schema(worktree),
            "search_queries": {"type": "array", "items": nonempty_string},
            "source_urls": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": url}},
            "tools_used": {"type": "array", "items": nonempty_string},
            "local_files_read": {"type": "array", "items": {"type": "string", "minLength": 1, "pattern": repo_relative_path}},
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "findings": {"type": "array", "minItems": 1, "items": nonempty_string},
            "blockers": {"type": "array", "items": nonempty_string},
            "handoff": nonempty_string,
        },
    }


def optional_list(title: str, values: list[str]) -> str:
    if not values:
        return f"{title}: none"
    return title + ":\n" + "\n".join(f"- {value}" for value in values)


def context_section(worktree: str, context_files: list[str], *, include_worktree_excerpts: bool) -> str:
    pack = CONTEXT_PACK.pack_context(
        worktree=Path(worktree).resolve(),
        context_files=[Path(value).resolve() for value in context_files],
        total_chars=MAX_CONTEXT_PACK_CHARS,
        per_file_chars=MAX_CONTEXT_FILE_CHARS,
        include_worktree_excerpts=include_worktree_excerpts,
    )
    return CONTEXT_PACK.markdown_from_pack(pack)


def load_task(path: Path | None) -> str:
    if not path:
        return "- Replace this section with the bounded task objective before launch."
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object at {path}")
    return data


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return ""


def markdown_section(text: str, heading: str, *, max_chars: int = 800) -> str:
    marker = f"## {heading}"
    lines = text.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == marker:
            collecting = True
            continue
        if collecting and stripped.startswith("## "):
            break
        if collecting:
            collected.append(line)
    value = "\n".join(collected).strip()
    if len(value) > max_chars:
        return value[:max_chars].rstrip() + "\n[truncated]"
    return value


def find_manifest_context(context_files: list[str], branch_id: str, packet_id: str) -> tuple[Path, dict, dict, dict] | None:
    for value in context_files:
        path = Path(value)
        if path.name != "job.manifest.json":
            continue
        try:
            manifest = load_json(path)
        except Exception:  # noqa: BLE001
            continue
        branch_data = branch_entry(manifest, branch_id)
        if not branch_data:
            continue
        work_items = branch_data.get("work_items") if isinstance(branch_data.get("work_items"), list) else []
        matches = [
            item
            for item in work_items
            if isinstance(item, dict) and item.get("packet_id") == packet_id
        ]
        if len(matches) != 1:
            continue
        return path, manifest, branch_data, matches[0]
    return None


def compact_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [item for item in values if isinstance(item, str) and item.strip()]


def bullet_list(values: list[str]) -> str:
    if not values:
        return "- none"
    return "\n".join(f"- {value}" for value in values)


def compact_worker_context(
    *,
    branch_id: str,
    packet_id: str,
    task_file: Path | None,
    task_text: str,
    owned_files: list[str],
    context_files: list[str],
) -> tuple[str, list[str], dict] | None:
    found = find_manifest_context(context_files, branch_id, packet_id)
    if found is None:
        return None
    manifest_path, manifest, branch_data, work_item = found
    task_sha = CONTEXT_PACK.sha256_file(task_file) if task_file else None
    manifest_sha = CONTEXT_PACK.sha256_file(manifest_path)
    branch_objective = markdown_section(task_text, "Objective", max_chars=500)
    branch_scope = markdown_section(task_text, "Scope", max_chars=500)
    stop_conditions = markdown_section(task_text, "Stop Conditions", max_chars=500)
    work_owned_paths = compact_list(work_item.get("owned_paths")) or owned_files
    work_context_files = compact_list(work_item.get("context_files"))
    verification = compact_list(work_item.get("verification"))
    dod = compact_list(work_item.get("dod"))
    depends_on = compact_list(work_item.get("depends_on"))
    worker_parallelism = branch_data.get("worker_parallelism") if isinstance(branch_data.get("worker_parallelism"), dict) else {}
    artifact = {
        "schema_version": 1,
        "kind": "compact_worker_context",
        "source": "job.manifest.json branch/work-item slice",
        "manifest_path": manifest_path.as_posix(),
        "manifest_sha256": manifest_sha,
        "task_file": task_file.as_posix() if task_file else None,
        "task_file_sha256": task_sha,
        "job_id": manifest.get("job_id"),
        "base_ref": manifest.get("base_ref"),
        "branch": {
            "id": branch_data.get("id"),
            "branch_name": branch_data.get("branch_name"),
            "worktree_path": branch_data.get("worktree_path"),
            "prompt": branch_data.get("prompt"),
            "status_path": branch_data.get("status_path"),
            "review_path": branch_data.get("review_path"),
            "pre_review_gate_path": branch_data.get("pre_review_gate_path"),
            "owned_paths": compact_list(branch_data.get("owned_paths")),
            "max_active_worker_packets": branch_data.get("max_active_worker_packets"),
            "worker_scheduler_path": worker_parallelism.get("scheduler_path"),
        },
        "work_item": {
            "id": work_item.get("id"),
            "packet_id": work_item.get("packet_id"),
            "worker_type": work_item.get("worker_type", "worker"),
            "route_class": work_item.get("route_class", DEFAULT_WORKER_ROUTE_CLASS),
            "objective": work_item.get("objective"),
            "owned_paths": work_owned_paths,
            "context_files": work_context_files,
            "depends_on": depends_on,
            "verification": verification,
            "dod": dod,
        },
    }
    task_lines = [
        "# Compact Worker Task",
        "",
        "This task was generated deterministically from `packet-context.json`; use the full branch prompt or manifest only if this compact task is insufficient or a validator/launcher fails.",
        "",
        f"Job: {manifest.get('job_id', '')}",
        f"Base ref: {manifest.get('base_ref', '')}",
        f"Branch prompt: {branch_data.get('prompt', '')}",
        f"Manifest: {manifest_path.as_posix()} ({manifest_sha})",
    ]
    heading = first_markdown_heading(task_text)
    if heading:
        task_lines.append(f"Branch heading: {heading}")
    if branch_objective:
        task_lines.extend(["", "Branch objective:", branch_objective])
    if branch_scope:
        task_lines.extend(["", "Branch scope:", branch_scope])
    task_lines.extend(
        [
            "",
            f"Work item: {work_item.get('id', '')} / {packet_id}",
            f"Worker type: {work_item.get('worker_type', 'worker')}",
            f"Route class: {work_item.get('route_class', DEFAULT_WORKER_ROUTE_CLASS)}",
            f"Objective: {work_item.get('objective', '')}",
            "",
            "Owned paths:",
            bullet_list(work_owned_paths),
            "",
            "Context files:",
            bullet_list(work_context_files),
            "",
            "Depends on:",
            bullet_list(depends_on),
            "",
            "Verification commands:",
            bullet_list(verification),
            "",
            "Definition of Done:",
            bullet_list(dod),
        ]
    )
    if stop_conditions:
        task_lines.extend(["", "Stop conditions:", stop_conditions])
    task_lines.extend(
        [
            "",
            "Worker rules:",
            "- Edit only owned paths unless returning `blocked` explains why broader ownership is required.",
            "- Run the listed verification commands or record the concrete blocker.",
            "- Use `git diff --check <base-ref>...HEAD` before claiming readiness when the base ref is available.",
            "- Do not read skill Python source unless a script or validator fails and source-level debugging is required.",
        ]
    )
    filtered_context_files = [
        value
        for value in context_files
        if Path(value).resolve() != manifest_path.resolve()
    ]
    return "\n".join(task_lines).rstrip() + "\n", filtered_context_files, artifact


def archive_existing_packet_dir(packet_dir: Path, *, replace: bool) -> None:
    if not packet_dir.exists():
        return
    if packet_dir.is_dir() and not any(packet_dir.iterdir()):
        return
    if not replace:
        raise SystemExit(f"runtime packet already exists; pass --replace to archive and recreate: {packet_dir}")
    attempts_dir = packet_dir / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    next_index = 1
    for child in sorted(attempts_dir.iterdir()):
        if child.is_dir() and child.name.startswith("attempt-"):
            suffix = child.name.removeprefix("attempt-")
            if suffix.isdigit():
                next_index = max(next_index, int(suffix) + 1)
    archive_dir = attempts_dir / f"attempt-{next_index:03d}"
    archive_dir.mkdir()
    for child in sorted(packet_dir.iterdir()):
        if child.name == "attempts":
            continue
        child.rename(archive_dir / child.name)


def branch_entry(manifest: dict, branch_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        return {}
    matches = [item for item in branches if isinstance(item, dict) and item.get("id") == branch_id]
    return matches[0] if len(matches) == 1 else {}


def bundle_path(bundle_dir: Path, value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    relative = PATH_RULES.require_relative_path(value, field)
    return (bundle_dir / relative).resolve().as_posix()


def reviewer_packet_context(
    *,
    packet_id: str,
    branch_id: str,
    worktree: Path,
    manifest_path: Path,
    manifest: dict,
    gate_path: Path,
    gate: dict,
    review_route: dict,
    review_schema_path: Path,
    review_output_path: Path,
) -> dict:
    bundle_dir = manifest_path.parent
    branch_data = branch_entry(manifest, branch_id)
    worker_artifacts: list[dict] = []
    work_items = branch_data.get("work_items") if isinstance(branch_data.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        worker_packet_id = item.get("packet_id")
        if not isinstance(worker_packet_id, str) or not worker_packet_id.strip():
            item_id = item.get("id")
            worker_packet_id = f"{branch_id}-{item_id}" if isinstance(item_id, str) else ""
        if not worker_packet_id:
            continue
        role = item.get("worker_type", "worker")
        if role == "research":
            role = "research-worker"
        if role == "research-worker":
            packet_dir = bundle_dir / "research" / worker_packet_id
            status_path = packet_dir / "research.json"
        else:
            packet_dir = bundle_dir / "workers" / worker_packet_id
            status_path = packet_dir / "status.json"
        worker_artifacts.append(
            {
                "packet_id": worker_packet_id,
                "role": role if role == "research-worker" else "worker",
                "status_path": status_path.resolve().as_posix(),
                "telemetry_path": (packet_dir / "telemetry.json").resolve().as_posix(),
                "route_path": (packet_dir / "route.json").resolve().as_posix(),
            }
        )
    base_ref = str(manifest.get("base_ref", "main"))
    return {
        "schema_version": 1,
        "kind": "compact_reviewer_context",
        "packet_id": packet_id,
        "role": "reviewer",
        "branch_id": branch_id,
        "branch_name": branch_data.get("branch_name"),
        "worktree": worktree.as_posix(),
        "base_ref": base_ref,
        "read_first": {
            "pre_review_gate": gate_path.as_posix(),
            "branch_status": bundle_path(bundle_dir, branch_data.get("status_path"), "branch status_path"),
            "branch_prompt": bundle_path(bundle_dir, branch_data.get("prompt"), "branch prompt"),
            "manifest": manifest_path.as_posix(),
            "worker_artifacts": worker_artifacts,
            "review_schema": review_schema_path.as_posix(),
        },
        "write_only": {
            "review_output": review_output_path.as_posix(),
        },
        "commands_to_run": [
            "pwd",
            "git status --short --branch",
            f"git diff --check {base_ref}...HEAD",
        ],
        "route": review_route,
        "semantic_input_hashes": gate.get("semantic_input_hashes", {}),
        "reuse_policy": gate.get("reuse_policy", {}),
    }


def branch_entry_for_packet(manifest: dict, branch_value: str, packet_id: str) -> dict:
    branches = manifest.get("branches")
    if not isinstance(branches, list):
        return {}
    for key in ("id", "branch_name"):
        matches = [item for item in branches if isinstance(item, dict) and item.get(key) == branch_value]
        if len(matches) == 1:
            return matches[0]
    packet_prefix = packet_id.split("-", 1)[0] if "-" in packet_id else ""
    if packet_prefix:
        return branch_entry(manifest, packet_prefix)
    return {}


def review_changed_paths(gate: dict, branch: dict) -> list[str]:
    paths: list[str] = []
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    ownership = checks.get("ownership") if isinstance(checks.get("ownership"), dict) else {}
    for source in [
        gate.get("changed_paths"),
        gate.get("changed_files"),
        ownership.get("changed_files"),
        branch.get("owned_paths"),
    ]:
        if not isinstance(source, list):
            continue
        for value in source:
            if isinstance(value, str) and value.strip() and value not in paths:
                paths.append(value)
    return paths


def branch_route_classes(branch: dict) -> list[str]:
    classes: list[str] = []
    work_items = branch.get("work_items") if isinstance(branch.get("work_items"), list) else []
    for item in work_items:
        if not isinstance(item, dict):
            continue
        worker_type = item.get("worker_type", "worker")
        if worker_type in {"research-worker", "research"}:
            route_class = "research-worker"
        else:
            route_class = item.get("route_class", DEFAULT_WORKER_ROUTE_CLASS)
        if isinstance(route_class, str) and route_class.strip() and route_class not in classes:
            classes.append(route_class)
    return classes


def docs_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith((".md", ".markdown", ".txt", ".rst", ".adoc")) or lowered.startswith(("docs/", "doc/", "readme", "changelog", "license", "notice"))


def test_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith(("test/", "tests/", "spec/", "specs/")) or "/tests/" in lowered or lowered.startswith("test_") or "_test." in lowered or ".spec." in lowered


def production_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if not docs_path(path) and not test_path(path)]


def explicit_review_tier(value: object) -> str:
    if isinstance(value, str) and value in CONTRACT.REVIEW_ROUTE_TIERS:
        return value
    return ""


def infer_review_tier(manifest: dict, gate: dict, branch: dict) -> tuple[str, list[str]]:
    explicit = explicit_review_tier(gate.get("review_tier")) or explicit_review_tier(branch.get("review_tier"))
    if explicit:
        explicit_reason = nonempty_text(gate.get("review_tier_reason")) or nonempty_text(branch.get("review_tier_reason"))
        return explicit, [explicit_reason or f"explicit {explicit} review tier"]
    changed_paths = review_changed_paths(gate, branch)
    trigger_hits: list[str] = []
    lower_paths = " ".join(changed_paths).lower()
    for pattern in CONTRACT.REVIEW_HEAVY_TRIGGER_PATTERNS:
        if pattern in lower_paths.replace("-", "_") or pattern in lower_paths:
            trigger_hits.append(pattern)
    diff_stats = gate.get("diff_stats") if isinstance(gate.get("diff_stats"), dict) else {}
    files_changed = diff_stats.get("files_changed")
    lines_changed = diff_stats.get("lines_changed")
    if isinstance(files_changed, int) and not isinstance(files_changed, bool) and files_changed >= 20:
        trigger_hits.append("large-diff")
    if isinstance(lines_changed, int) and not isinstance(lines_changed, bool) and lines_changed >= 800:
        trigger_hits.append("large-diff")
    if gate.get("prior_reviewer_blockers"):
        trigger_hits.append("reviewer-blocker")
    route_classes = branch_route_classes(branch)
    if "complex-code" in route_classes:
        trigger_hits.append("complex-code route class")
    checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
    tests = checks.get("tests") if isinstance(checks.get("tests"), dict) else {}
    if tests.get("status") not in {None, "pass"}:
        trigger_hits.append("incomplete verification")
    if gate.get("status") not in {None, "pass"}:
        trigger_hits.append("incomplete pre-review gate")
    if trigger_hits:
        return "heavy", sorted(set(trigger_hits))
    if route_classes and set(route_classes) <= {"docs", "mechanical"}:
        return "light", ["docs/mechanical route classes with no production behavior signal"]
    if changed_paths and not production_paths(changed_paths) and len(changed_paths) <= 6:
        return "light", ["docs or test-only review surface with no production path changes"]
    if route_classes and set(route_classes) <= {"small-edit", "normal-code"}:
        return "standard", ["normal or small-edit implementation route classes require standard reviewer routing"]
    policy = manifest.get("review_model_policy") if isinstance(manifest.get("review_model_policy"), dict) else {}
    default_tier = policy.get("default_tier") if policy.get("default_tier") in CONTRACT.REVIEW_ROUTE_TIERS else CONTRACT.REVIEW_MODEL_POLICY["default_tier"]
    return str(default_tier), ["default deterministic review tier"]


def select_review_route(manifest: dict, gate: dict, *, branch_id: str, packet_id: str) -> dict:
    branch = branch_entry(manifest, branch_id)
    tier, reasons = infer_review_tier(manifest, gate, branch)
    policy = manifest.get("review_model_policy") if isinstance(manifest.get("review_model_policy"), dict) else {}
    routes = policy.get("routes") if isinstance(policy.get("routes"), dict) else {}
    route = routes.get(tier) if isinstance(routes.get(tier), list) else CONTRACT.review_route_for_tier(tier)
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": "reviewer",
        "tier": tier,
        "selected_ladder": route,
        "selection_reason": "; ".join(reasons),
        "policy_router": policy.get("router", CONTRACT.REVIEW_MODEL_POLICY["router"]),
        "policy_routes": routes or CONTRACT.REVIEW_MODEL_POLICY["routes"],
        "heavy_triggers": reasons if tier == "heavy" else [],
        "route_classes": branch_route_classes(branch),
        "changed_paths": review_changed_paths(gate, branch),
    }


def reviewer_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    context_files: list[str],
    packet_context_path: str,
    include_worktree_context_excerpts: bool,
) -> str:
    context_pointer = (
        f"Packet context to read first:\n- {packet_context_path}"
        if packet_context_path
        else context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)
    )
    return f"""# Branch Reviewer Packet {packet_id}

You are Reviewer {packet_id}. Do not edit files.

Worktree: {worktree}
Branch: {branch}

{context_pointer}

Before reviewing, run:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Review the branch against its prompt, worker status files, bounded diffs, test evidence, and claim-boundary rules. Lead with findings ordered by severity. Ground findings in file/line references or command evidence where possible.

The branch orchestrator must have supplied a passing schema v2 `pre_review_gate.json` before this packet was generated. Read it from the provided context, copy its `semantic_input_hashes` exactly into the final review JSON as `semantic_input_hashes`, and record a `reuse_policy` object. Set reviewer reuse to accepted only when every semantic input hash matches exactly and both the source review and source telemetry are present; otherwise produce a fresh review.

Read the packet-local `compact_reviewer_context` first. It lists the exact branch prompt, branch status, pre-review gate, worker status, worker telemetry, schema, and output paths. Use those paths before searching any bundle directory. Do not read memory, broad bundle directories, full event logs, or unrelated repo files unless a named packet artifact is missing, contradictory, or insufficient to substantiate a concrete finding. Prefer `git diff --stat`, `git diff --name-only`, and targeted file hunks for changed paths over full diffs.

Determine the branch base ref from `compact_reviewer_context`. Before reporting merge readiness, run `git diff --check <base-ref>...HEAD` and record the command result. If the base ref is unavailable, report a verification gap instead of assuming merge readiness.

Do not emit placeholder, draft, or example final-shaped JSON before inspection is complete. Return exactly one final JSON object matching `{schema_name}` only after command inspection and evidence review are finished. `commands_run` must contain exact command strings that were actually run.

If your CLI harness does not write `{schema_name}` directly, print the final review object between these exact marker lines and do not print any other JSON object between them:

{GEMINI_STATUS_BEGIN}
{{"packet_id":"{packet_id}","role":"reviewer","verdict":"blocked","findings":["replace with concrete finding"],"commands_run":["pwd","git status --short --branch"],"verification_gaps":["replace with concrete gap"],"residual_risks":[],"semantic_input_hashes":{{}},"reuse_policy":{{}},"summary":"replace with concise summary"}}
{GEMINI_STATUS_END}
"""


def research_worker_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    include_worktree_context_excerpts: bool,
) -> str:
    example_research = json.dumps(
        {
            "packet_id": packet_id,
            "role": "research-worker",
            "status": "blocked",
            "branch": branch,
            "worktree": worktree,
            "search_queries": [],
            "source_urls": [],
            "tools_used": [],
            "local_files_read": [],
            "commands_run": ["pwd", "git status --short --branch"],
            "findings": ["replace with concrete finding or blocker"],
            "blockers": ["replace with concrete blocker"],
            "handoff": "replace with concise research handoff",
        },
        separators=(",", ":"),
    )
    return f"""# Research Worker Packet {packet_id}

You are Research Worker {packet_id}. Do not edit files.

Worktree: {worktree}
Branch: {branch}

Allowed information sources:

- Native Codex live web search enabled by the launcher.
- Configured read-only CLI tools, MCP servers, connector tools, browser/search tools, package metadata lookups, remote APIs, and shell/network inspection commands when they are relevant to the task.
- Local read-only file and command inspection for the assigned worktree, explicit context files, and configured tool or skill documentation when task-relevant.

Safety boundaries:

- Do not write or modify local files.
- Do not mutate remote services or repositories.
- Do not inspect secrets or unrelated private files.
- Do not post messages, send email, create tickets, buy anything, change calendars/docs/issues, authenticate new accounts, alter credentials, or exfiltrate secrets.
- Use broad tools only for read-only information retrieval and record what you used.

Local read scope:

{optional_list("Relevant local files/modules", owned_files)}

{context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)}

Before researching, run:

```bash
pwd
git status --short --branch
```

Task:

{task_text}

Use the appropriate broad read-only tools for current outside information. Record every search query you rely on in `search_queries`; leave it empty only when you used direct URLs, local files, connectors, or other non-search tools instead. Record every source URL that supports a finding in `source_urls`. Use direct source URLs, not just search-result pages. Record every local file you read in `local_files_read` using repo-relative paths only.
Record every distinct external or local tool family you used in `tools_used`, for example `codex-native-search`, `web-open`, `shell-curl`, `local-rg`, `local-sed`, `mcp-docs`, or `connector-drive`.

Return a research status object matching `{schema_name}`. Allowed `status` values are exactly `pass`, `partial`, `blocked`, or `failed`. Use `pass` only when the research task is complete, source URLs are captured for all online claims, local files read are recorded, and `tools_used` identifies the tool families used. `commands_run` must contain exact local or shell commands that were actually run.

Do not emit placeholder, draft, or example final-shaped JSON before research is complete. Return exactly one final JSON object matching `{schema_name}`.

Example shape only:

```json
{example_research}
```
"""


def worker_prompt(
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    selected_ladder: list[str],
    route_class: str,
    selection_reason: str,
    include_worktree_context_excerpts: bool,
) -> str:
    example_status = json.dumps(
        {
            "packet_id": packet_id,
            "role": "worker",
            "status": "blocked",
            "branch": branch,
            "worktree": worktree,
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "changed_files": [],
            "commands_run": ["pwd", "git status --short --branch"],
            "tests": [],
            "blockers": ["replace with concrete blocker"],
            "handoff": "replace with concise handoff",
        },
        separators=(",", ":"),
    )
    return f"""# Worker Packet {packet_id}

You are Worker {packet_id}.

Worktree: {worktree}
Branch: {branch}

You are not alone in the codebase. Do not revert edits made by others. Own only the files/modules assigned here. If the task needs more than roughly 40k tokens of context, stop and return `blocked` instead of broadening scope.

Selected worker ladder: {", ".join(selected_ladder)}
Route class: {route_class}
Route selection reason: {selection_reason}

Copy `route_class`, `selected_ladder`, and `selection_reason` exactly into the final worker status. Do not change model aliases, model ids, effort levels, or provider order.

{optional_list("Owned files/modules", owned_files)}

{context_section(worktree, context_files, include_worktree_excerpts=include_worktree_context_excerpts)}

Before editing, run:

```bash
pwd
git status --short --branch
```

Task:

{task_text}

Return a worker status object matching `{schema_name}`. Allowed `status` values are exactly `pass`, `partial`, `blocked`, or `failed`. Use `pass` for successful completion; never use `success`. `changed_files` must contain repo-relative file paths only, without git porcelain prefixes such as `M ` or `?? `. `commands_run` and `tests` must contain exact command strings that were actually run.

If your CLI harness does not write `{schema_name}` directly, print the final status object between these exact marker lines and do not print any other JSON object between them:

{GEMINI_STATUS_BEGIN}
{example_status}
{GEMINI_STATUS_END}
"""


def prompt_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    owned_files: list[str],
    context_files: list[str],
    task_text: str,
    selected_ladder: list[str] | None,
    route_class: str,
    selection_reason: str,
    packet_context_path: str = "",
    include_worktree_context_excerpts: bool = False,
) -> str:
    if role == "reviewer":
        return reviewer_prompt(packet_id, branch, worktree, schema_name, context_files, packet_context_path, include_worktree_context_excerpts)
    if role == "research-worker":
        return research_worker_prompt(
            packet_id, branch, worktree, schema_name, owned_files, context_files, task_text, include_worktree_context_excerpts
        )
    if role == "worker":
        return worker_prompt(
            packet_id, branch, worktree, schema_name, owned_files, context_files, task_text,
            selected_ladder or list(DEFAULT_WORKER_LADDER), route_class, selection_reason, include_worktree_context_excerpts,
        )
    raise SystemExit(f"unsupported role for prompt generation: {role}")


def worker_attempt_script(selected_ladder: list[str], output_name: str) -> str:
    run_commands = {
        "gemini-pro": f"run_gemini gemini-pro {shell_quote(GEMINI_PRO_MODEL)}",
        "gemini-flash": f"run_gemini gemini-flash {shell_quote(GEMINI_FLASH_MODEL)}",
        "codex-spark": f"run_codex spark {shell_quote(SPARK_MODEL)}",
        "codex-mini": f"run_codex mini {shell_quote(MINI_MODEL)}",
    }
    lines = []
    for index, alias in enumerate(selected_ladder):
        label = WORKER_ROUTE_LABELS[alias]
        lines.extend(
            [
                f"if {run_commands[alias]}; then",
                "  write_telemetry",
                "  exit 0",
                "fi",
                "",
            ]
        )
        if index < len(selected_ladder) - 1:
            lines.extend(
                [
                    f"guard_clean_for_fallback {shell_quote(label)}",
                    "",
                ]
            )
            continue
        lines.extend(
            [
                "if [ -s \"$output_path\" ]; then",
                "  write_telemetry",
                "  exit 1",
                "fi",
                "",
                "if worktree_dirty; then",
                f"  echo {shell_quote(label + ' failed after leaving dirty worktree; no fallback remains.')} > \"$packet_dir/fallback.blocked.txt\"",
                f"  write_terminal_status blocked {shell_quote(label + ' failed after leaving dirty worktree; no fallback remains.')}",
                "  write_telemetry",
                "  exit 2",
                "fi",
                "",
            ]
        )
    lines.extend(
        [
            f"write_terminal_status blocked {shell_quote(f'All selected worker route attempts failed cleanly without producing {output_name}.')}",
            "write_telemetry",
            "exit 1",
        ]
    )
    return "\n".join(lines)


def launch_for(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    selected_ladder: list[str] | None,
    selection_reason: str,
    review_route: dict | None = None,
    review_semantic_hashes: dict[str, str] | None = None,
    review_reuse_policy: dict | None = None,
) -> str:
    if role in {"research-worker", "reviewer", "worker"}:
        return compact_launch_script()

    raise SystemExit(f"unsupported role for launch script generation: {role}")

def reviewer_ladder_from_route(review_route: dict | None) -> list[str]:
    route = review_route or {
        "selected_ladder": CONTRACT.review_route_for_tier(CONTRACT.REVIEW_MODEL_POLICY["default_tier"]),
        "selection_reason": "Default light reviewer route.",
    }
    selected = [item for item in route.get("selected_ladder", []) if isinstance(item, str) and item]
    return selected or CONTRACT.review_route_for_tier(CONTRACT.REVIEW_MODEL_POLICY["default_tier"])


def launch_config_base(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    sandbox: str,
    attempt_timeout_seconds: int,
) -> dict:
    return {
        "schema_version": 1,
        "role": role,
        "packet_id": packet_id,
        "branch": branch,
        "worktree": worktree,
        "schema_name": schema_name,
        "output_name": output_name,
        "state_artifact": "launcher-state.json",
        "sandbox": sandbox,
        "attempt_timeout_seconds": attempt_timeout_seconds,
        "timeout_kill_after_seconds": TIMEOUT_KILL_AFTER_SECONDS,
    }


def compact_launch_config(
    role: str,
    packet_id: str,
    branch: str,
    worktree: str,
    schema_name: str,
    output_name: str,
    owned_files: list[str] | None = None,
    selected_ladder: list[str] | None = None,
    route_class: str = DEFAULT_WORKER_ROUTE_CLASS,
    selection_reason: str = "",
    model_catalog: dict | None = None,
    review_route: dict | None = None,
    review_semantic_hashes: dict[str, str] | None = None,
    review_reuse_policy: dict | None = None,
    telemetry_debug: bool = False,
    goal_config: dict | None = None,
) -> dict | None:
    telemetry_script = (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix()
    selected_ladder = selected_ladder or list(DEFAULT_WORKER_LADDER)
    debug_config = (
        {
            "telemetry_debug_name": CONTRACT.TELEMETRY_DEBUG_NAME,
            "debug_events_name": CONTRACT.TELEMETRY_DEBUG_EVENTS_NAME,
        }
        if telemetry_debug
        else {}
    )
    if role == "worker":
        return {
            **launch_config_base(
                "worker", packet_id, branch, worktree, schema_name, output_name, "workspace-write", WORKER_ATTEMPT_TIMEOUT_SECONDS
            ),
            **debug_config,
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "owned_files": owned_files or [],
            "worker_prompt": WORKER_PACKET_PROMPT,
            "status_markers": {
                "begin": GEMINI_STATUS_BEGIN,
                "end": GEMINI_STATUS_END,
            },
            "attempts": worker_telemetry_attempts(selected_ladder, goal_config),
            "selected_commands": configured_route_commands(selected_ladder, goal_config) if goal_config else worker_route_commands(selected_ladder),
            "model_catalog": model_catalog or {},
            "telemetry_script": telemetry_script,
            "terminal_message": f"All selected worker route attempts failed cleanly without producing {output_name}.",
            "gemini_probe_timeout_seconds": GEMINI_PROBE_TIMEOUT_SECONDS,
            "gemini_probe_prompt": GEMINI_PROBE_PROMPT,
            "gemini_approval_mode": GEMINI_APPROVAL_MODE,
            "gemini_command": GEMINI_COMMAND,
        }
    if role == "research-worker":
        return {
            **launch_config_base(
                "research-worker", packet_id, branch, worktree, schema_name, output_name, "read-only", RESEARCH_ATTEMPT_TIMEOUT_SECONDS
            ),
            **debug_config,
            "attempts": research_telemetry_attempts(),
            "telemetry_script": telemetry_script,
            "terminal_message": f"Research worker primary and fallback failed without producing {output_name}.",
        }
    if role == "reviewer":
        reviewer_ladder = reviewer_ladder_from_route(review_route)
        terminal_commands = [
            configured_route_commands([alias], goal_config)[0] if goal_config else CONTRACT.codex_command(alias, sandbox="read-only", lean=True)
            for alias in reviewer_ladder
        ]
        return {
            **launch_config_base(
                "reviewer", packet_id, branch, worktree, schema_name, output_name, "read-only", REVIEWER_ATTEMPT_TIMEOUT_SECONDS
            ),
            **debug_config,
            "attempts": reviewer_telemetry_attempts(reviewer_ladder, goal_config),
            "telemetry_script": telemetry_script,
            "semantic_input_hashes": review_semantic_hashes or {},
            "reuse_policy": review_reuse_policy or {
                "mode": "new",
                "accepted": False,
                "semantic_hashes_match": False,
                "source_review_path": None,
                "source_telemetry_path": None,
            },
            "terminal_commands": terminal_commands,
            "terminal_message": f"Reviewer primary and fallback failed without producing {output_name}.",
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["worker", "research-worker", "reviewer"], required=True)
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--manifest",
        help="Absolute path to job.manifest.json. Required for reviewer packets; optional for compact worker packets.",
    )
    parser.add_argument("--pre-review-gate", help="Required for reviewer packets; absolute path to pre_review_gate.json.")
    parser.add_argument("--task-file")
    parser.add_argument("--owned-file", action="append", default=[])
    parser.add_argument("--context-file", action="append", default=[])
    parser.add_argument(
        "--include-worktree-context-excerpts",
        action="store_true",
        help="Embed bounded excerpts for worktree-local --context-file inputs in worker/research prompts; default is path-only.",
    )
    parser.add_argument(
        "--worker-route",
        action="append",
        nargs="+",
        default=[],
        help="Allowed worker route alias. Repeat to choose a non-empty ordered subsequence of the standard ladder.",
    )
    parser.add_argument(
        "--route-class",
        help=(
            "Worker route class. Defaults from the manifest work item when available; otherwise "
            f"{DEFAULT_WORKER_ROUTE_CLASS}. Known classes: {', '.join(WORKER_ROUTE_CLASSES)}."
        ),
    )
    parser.add_argument(
        "--model-catalog",
        help=(
            "Optional fresh check_model_catalog.py --json output. For worker packets, unsupported Codex "
            "route aliases are pruned from the default ladder and rejected when explicitly selected."
        ),
    )
    parser.add_argument("--selection-reason", help="Required when --worker-route is supplied; recorded in route.json and worker status.")
    parser.add_argument("--replace", action="store_true", help="Archive an existing packet directory under attempts/ and recreate it.")
    args = parser.parse_args()

    packet_id = require_safe_label(args.packet_id, "packet-id")
    branch = args.branch
    if not safe_branch_name(branch):
        raise SystemExit(f"branch is not a safe git branch name: {branch!r}")
    manifest_branch_id = branch
    manifest: dict | None = None
    manifest_path: Path | None = None
    telemetry_debug = False
    worktree = resolve_absolute_path(args.worktree, "--worktree", must_exist=True)
    owned_files = normalize_owned_paths(args.owned_file)
    context_files = normalize_context_files(args.context_file)
    if args.manifest and args.role == "worker":
        manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
        manifest = load_json(manifest_path)
        telemetry_debug = CONTRACT.telemetry_debug_enabled(manifest)
        branch_data = branch_entry_for_packet(manifest, branch, packet_id)
        if branch_data:
            branch_id_value = branch_data.get("id")
            branch_name_value = branch_data.get("branch_name")
            if isinstance(branch_id_value, str) and branch_id_value.strip():
                manifest_branch_id = branch_id_value
            if isinstance(branch_name_value, str) and branch_name_value.strip():
                if not safe_branch_name(branch_name_value):
                    raise SystemExit(f"manifest branch_name is not a safe git branch name: {branch_name_value!r}")
                branch = branch_name_value
        manifest_value = manifest_path.as_posix()
        if manifest_value not in context_files:
            context_files.append(manifest_value)
    elif args.manifest and args.role == "research-worker":
        raise SystemExit("--manifest is only valid for worker compact context or reviewer packet generation")
    task_file = (
        resolve_absolute_path(args.task_file, "--task-file", must_exist=True)
        if args.task_file
        else None
    )
    if args.role in {"research-worker", "reviewer"} and (args.worker_route or args.selection_reason):
        raise SystemExit("research-worker and reviewer packets must not set worker route options")
    if args.model_catalog and args.role != "worker":
        raise SystemExit("--model-catalog is only valid for worker packets")
    review_route: dict | None = None
    review_semantic_hashes: dict[str, str] | None = None
    review_reuse_policy: dict | None = None
    if args.role == "reviewer":
        if not args.manifest:
            raise SystemExit("reviewer packets require --manifest")
        if not args.pre_review_gate:
            raise SystemExit("reviewer packets require --pre-review-gate")
        manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
        gate_path = resolve_absolute_path(args.pre_review_gate, "--pre-review-gate", must_exist=True)
        manifest = load_json(manifest_path)
        telemetry_debug = CONTRACT.telemetry_debug_enabled(manifest)
        gate = load_json(gate_path)
        branch_id = packet_id.split("-R", 1)[0] if "-R" in packet_id else ""
        manifest_branch_id = branch_id or manifest_branch_id
        defects: list[str] = []
        STATUS_VALIDATION.validate_pre_review_gate_artifact(
            defects,
            gate_path,
            "pre_review_gate",
            manifest_path=manifest_path,
            branch_id=branch_id,
            review_packet_id=packet_id,
        )
        if defects:
            raise SystemExit("pre-review gate failed; refusing reviewer packet generation:\n" + "\n".join(defects))
        gate_reuse_policy = gate.get("reuse_policy") if isinstance(gate.get("reuse_policy"), dict) else {}
        if gate_reuse_policy.get("accepted") is True and gate_reuse_policy.get("source_telemetry_path"):
            print("pre-review gate accepted reviewer reuse with telemetry; no reviewer model packet generated")
            return 0
        review_route = select_review_route(manifest, gate, branch_id=branch_id, packet_id=packet_id)
        review_semantic_hashes = {
            key: value
            for key, value in gate.get("semantic_input_hashes", {}).items()
            if isinstance(key, str) and isinstance(value, str)
        } if isinstance(gate.get("semantic_input_hashes"), dict) else {}
        review_reuse_policy = {
            "mode": "new",
            "accepted": False,
            "semantic_hashes_match": False,
            "source_review_path": None,
            "source_telemetry_path": None,
        }
        if gate_reuse_policy.get("accepted") is True:
            review_reuse_policy = dict(gate_reuse_policy)
    selected_ladder: list[str] | None = None
    route_class = DEFAULT_WORKER_ROUTE_CLASS
    selection_reason = ""
    model_catalog: dict | None = None
    if args.role == "worker":
        normalized_worker_routes: list[str] = []
        for item in args.worker_route:
            if isinstance(item, str):
                normalized_worker_routes.append(item)
            else:
                normalized_worker_routes.extend(item)
        manifest_work_item: dict | None = None
        manifest_context = find_manifest_context(context_files, manifest_branch_id, packet_id)
        if manifest_context is not None:
            _manifest_path, _manifest, _branch_data, manifest_work_item = manifest_context
            manifest = _manifest
            manifest_path = _manifest_path
            telemetry_debug = telemetry_debug or CONTRACT.telemetry_debug_enabled(_manifest)
        worker_policy = worker_policy_from_manifest(manifest)
        worker_default_ladder = policy_default_ladder(worker_policy)
        worker_allowed_routes = policy_allowed_routes(worker_policy)
        manifest_route_class = manifest_work_item.get("route_class") if isinstance(manifest_work_item, dict) else None
        route_class = normalize_route_class(args.route_class or manifest_route_class or ("custom" if normalized_worker_routes else DEFAULT_WORKER_ROUTE_CLASS))
        selected_ladder = (
            normalize_worker_ladder(
                normalized_worker_routes,
                default_ladder=worker_default_ladder,
                allowed_routes=worker_allowed_routes,
            )
            if normalized_worker_routes
            else ladder_for_route_class(route_class, worker_policy)
        )
        catalog_path = (
            resolve_absolute_path(args.model_catalog, "--model-catalog", must_exist=True)
            if args.model_catalog
            else None
        )
        selected_ladder, model_catalog = apply_model_catalog_to_worker_ladder(
            selected_ladder,
            catalog_path=catalog_path,
            explicit_routes=bool(normalized_worker_routes),
        )
        selection_reason = nonempty_text(args.selection_reason)
        if args.worker_route and not selection_reason:
            raise SystemExit("--selection-reason is required when --worker-route is supplied")
        if not selection_reason:
            goal_config = goal_config_from_manifest(manifest, manifest_path)
            if goal_config:
                selection_reason = (
                    f"{route_class} route class selected from goal_config worker_model_policy: "
                    + ", ".join(selected_ladder)
                )
            else:
                selection_reason = default_selection_reason(route_class)
        if model_catalog and model_catalog.get("filtered_aliases"):
            aliases = ", ".join(str(item.get("alias")) for item in model_catalog["filtered_aliases"])
            selection_reason += f" Model catalog pruned unavailable Codex route(s): {aliases}."
        validate_route_class_selection(
            route_class,
            selected_ladder,
            selection_reason,
            worker_policy if goal_config_from_manifest(manifest, manifest_path) else None,
        )

    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    packet_dir = out_dir / packet_id
    archive_existing_packet_dir(packet_dir, replace=args.replace)
    packet_dir.mkdir(parents=True, exist_ok=True)

    if args.role == "reviewer":
        schema_name = "review.schema.json"
        output_name = "review.json"
        schema = review_schema(packet_id, review_semantic_hashes, review_reuse_policy)
    elif args.role == "research-worker":
        schema_name = "research.schema.json"
        output_name = "research.json"
        schema = research_schema(packet_id, branch, str(worktree))
    else:
        schema_name = "status.schema.json"
        output_name = "status.json"
        schema = status_schema(packet_id, branch, str(worktree), selected_ladder)

    validate_openai_strict_schema(schema, schema_name)
    task_text = load_task(task_file)
    packet_context: dict | None = None
    packet_context_path = ""
    if args.role == "reviewer":
        packet_context_path = (packet_dir / "packet-context.json").resolve().as_posix()
        packet_context = reviewer_packet_context(
            packet_id=packet_id,
            branch_id=manifest_branch_id,
            worktree=worktree,
            manifest_path=manifest_path,
            manifest=manifest,
            gate_path=gate_path,
            gate=gate,
            review_route=review_route or {},
            review_schema_path=packet_dir / schema_name,
            review_output_path=packet_dir / output_name,
        )
    if args.role == "worker":
        compact_context = compact_worker_context(
            branch_id=manifest_branch_id,
            packet_id=packet_id,
            task_file=task_file,
            task_text=task_text,
            owned_files=owned_files,
            context_files=context_files,
        )
        if compact_context is not None:
            task_text, context_files, packet_context = compact_context
            work_item = packet_context.get("work_item") if isinstance(packet_context, dict) else None
            manifest_owned_files = compact_list(work_item.get("owned_paths")) if isinstance(work_item, dict) else []
            if manifest_owned_files:
                owned_files = manifest_owned_files

    write_json(packet_dir / schema_name, schema)
    if packet_context is not None:
        write_json(packet_dir / "packet-context.json", packet_context)
    (packet_dir / "prompt.md").write_text(
        prompt_for(
            args.role,
            packet_id,
            branch,
            str(worktree),
            schema_name,
            owned_files,
            context_files,
            task_text,
            selected_ladder,
            route_class,
            selection_reason,
            packet_context_path,
            args.include_worktree_context_excerpts,
        ),
        encoding="utf-8",
    )
    if args.role == "worker":
        route = {
            "packet_id": packet_id,
            "role": "worker",
            "branch_id": manifest_branch_id,
            "branch": branch,
            "route_class": route_class,
            "selected_ladder": selected_ladder,
            "selection_reason": selection_reason,
            "default_ladder": policy_default_ladder(worker_policy_from_manifest(manifest)),
            "allowed_aliases": policy_allowed_routes(worker_policy_from_manifest(manifest)),
            "model_catalog": model_catalog or {},
        }
        write_json(packet_dir / "route.json", route)
    elif args.role == "reviewer" and review_route is not None:
        write_json(packet_dir / "route.json", review_route)
    launch_config = compact_launch_config(
        args.role,
        packet_id,
        branch,
        str(worktree),
        schema_name,
        output_name,
        owned_files=owned_files,
        selected_ladder=selected_ladder,
        route_class=route_class,
        selection_reason=selection_reason,
        model_catalog=model_catalog,
        review_route=review_route,
        review_semantic_hashes=review_semantic_hashes,
        review_reuse_policy=review_reuse_policy,
        telemetry_debug=telemetry_debug,
        goal_config=goal_config_from_manifest(manifest, manifest_path),
    )
    if launch_config is not None:
        write_json(packet_dir / "launch-config.json", launch_config)
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(
        launch_for(
            args.role,
            packet_id,
            branch,
            str(worktree),
            schema_name,
            output_name,
            selected_ladder,
            selection_reason,
            review_route=review_route,
            review_semantic_hashes=review_semantic_hashes,
            review_reuse_policy=review_reuse_policy,
        ),
        encoding="utf-8",
    )
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
