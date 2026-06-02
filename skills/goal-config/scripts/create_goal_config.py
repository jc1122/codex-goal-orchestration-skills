#!/usr/bin/env python3
"""Create deterministic goal orchestration configuration profiles."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


IMPLIED_PROVIDER_BY_HARNESS = {
    "codex": "openai",
    "gemini": "gemini",
}

EFFORT_PROFILES: dict[str, dict[str, int]] = {
    "lean": {
        "max_active_branch_agents": 2,
        "max_active_worker_packets": 2,
        "max_waves": 3,
        "lite_timeout_seconds": 300,
        "demanding_timeout_seconds": 900,
    },
    "balanced": {
        "max_active_branch_agents": 4,
        "max_active_worker_packets": 4,
        "max_waves": 5,
        "lite_timeout_seconds": 600,
        "demanding_timeout_seconds": 1200,
    },
    "thorough": {
        "max_active_branch_agents": 6,
        "max_active_worker_packets": 6,
        "max_waves": 8,
        "lite_timeout_seconds": 900,
        "demanding_timeout_seconds": 2400,
    },
}

VALIDATION_MODES = {"model-check", "smoke", "debug"}


def load_contract() -> Any:
    shared_path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    spec = importlib.util.spec_from_file_location("_goal_shared_orchestration_contract", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared contract: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def normalize_aggressiveness_with_preflight_caps(
    config: dict[str, Any],
    requested: dict[str, int],
    *,
    source: str,
    contract: Any,
) -> None:
    caps = {
        "max_active_branch_agents": (1, int(contract.MAX_ACTIVE_BRANCH_AGENTS)),
        "max_active_worker_packets": (1, int(contract.MAX_WORKER_PACKETS_PER_BRANCH)),
        "max_waves": (1, int(contract.MAX_WAVES)),
    }

    adjusted: dict[str, int] = {}
    adjustments: list[str] = []
    for key, (minimum, maximum) in caps.items():
        value = int(requested[key])
        normalized = max(minimum, min(value, maximum))
        adjusted[key] = normalized
        if normalized != value:
            adjustments.append(f"{key} {value} -> {normalized}")
        config["aggressiveness"][key] = normalized

    config["aggressiveness"]["total_branch_cap"] = adjusted["max_active_branch_agents"] * adjusted["max_waves"]

    if not adjustments:
        return

    compatibility = config.setdefault("compatibility", {})
    history = compatibility.setdefault("aggressiveness_adjustments", [])
    if not isinstance(history, list):
        history = []
        compatibility["aggressiveness_adjustments"] = history
    history.append(
        {
            "source": source,
            "requested": {
                "max_active_branch_agents": requested["max_active_branch_agents"],
                "max_active_worker_packets": requested["max_active_worker_packets"],
                "max_waves": requested["max_waves"],
            },
            "applied": {
                "max_active_branch_agents": adjusted["max_active_branch_agents"],
                "max_active_worker_packets": adjusted["max_active_worker_packets"],
                "max_waves": adjusted["max_waves"],
            },
            "adjustments": adjustments,
            "total_branch_cap": config["aggressiveness"]["total_branch_cap"],
        }
    )


def parse_role_model(spec: str, default_provider: str | None = None) -> dict[str, str]:
    parts = spec.split(":")
    if len(parts) < 3:
        raise SystemExit(
            "role-model spec must be ROLE:HARNESS:PROVIDER/MODEL, "
            "ROLE:HARNESS:MODEL with an implied provider, or ROLE:HARNESS:MODEL with --provider"
        )

    role, harness, provider_model = parts[:3]
    aliases = parts[3:]
    if len(aliases) > 2:
        raise SystemExit(f"invalid role-model spec with too many suffixes: {spec}")

    alias = aliases[0] if len(aliases) >= 1 and aliases[0] else role
    purpose = aliases[1] if len(aliases) >= 2 and aliases[1] else f"{role} model"
    provider, model = normalize_role_model_for_harness(provider_model, harness, default_provider)
    return {
        "role": role,
        "harness": harness,
        "provider": provider,
        "model": model,
        "alias": alias,
        "purpose": purpose,
    }


def normalize_role_model_for_harness(
    provider_model: str,
    harness: str,
    default_provider: str | None = None,
) -> tuple[str, str]:
    implied_provider = IMPLIED_PROVIDER_BY_HARNESS.get(harness)
    provider = default_provider or implied_provider
    if "/" in provider_model:
        listed_provider, model_suffix = provider_model.split("/", 1)
        provider = provider or listed_provider
    elif provider:
        model_suffix = provider_model
    else:
        raise SystemExit(
            f"model for role using harness {harness!r} must include provider/model, use --provider, "
            "or use a harness with an implied provider"
        )

    if harness in IMPLIED_PROVIDER_BY_HARNESS:
        return provider, model_suffix

    if default_provider:
        if provider_model.startswith(f"{default_provider}/"):
            return provider, provider_model
        return provider, f"{default_provider}/{provider_model}"

    if "/" in provider_model:
        return provider, provider_model
    return provider, f"{provider}/{provider_model}"


def model_entry(*, alias: str, role: str, harness: str, provider: str, model: str, purpose: str) -> dict[str, Any]:
    return {
        "alias": alias,
        "role": role,
        "harness": harness,
        "provider": provider,
        "model": model,
        "purpose": purpose,
    }


def default_harnesses() -> dict[str, Any]:
    return {
        "opencode": {
            "kind": "opencode",
            "command": "opencode",
            "model_list_args": ["models", "{provider}"],
            "smoke_args": [
                "run",
                "--pure",
                "--format",
                "json",
                "--model",
                "{model}",
                "{prompt}",
            ],
            "run_args": [
                "run",
                "--pure",
                "--format",
                "json",
                "--model",
                "{model}",
                "--dir",
                "{worktree}",
                "--title",
                "{packet_id}-{alias}",
                "{prompt}",
            ],
            "run_readback": "opencode_session_db",
        },
        "codex": {
            "kind": "codex",
            "command": "codex",
            "smoke_args": [
                "exec",
                "--ephemeral",
                "-m",
                "{model}",
                "-s",
                "read-only",
                "{prompt}",
            ],
            "run_readback": "output_file",
        },
        "gemini": {
            "kind": "gemini",
            "command": "gemini",
            "approval_mode": "yolo",
            "smoke_args": ["--model", "{model}", "--approval-mode", "yolo", "-p", "{prompt}"],
            "run_args": ["--model", "{model}", "--approval-mode", "yolo", "-p", "{prompt}"],
            "run_readback": "stdout",
        },
        "antigravity": {
            "kind": "generic-cli",
            "command": "agy",
            "smoke_args": ["--print", "{prompt}"],
            "run_args": ["--print", "{prompt}"],
            "run_readback": "stdout",
        },
    }


def apply_role_overrides(models: dict[str, Any], role_models: list[str], default_provider: str | None) -> None:
    for spec in role_models:
        mapping = parse_role_model(spec, default_provider)
        models[mapping["role"]] = model_entry(
            alias=mapping["alias"],
            role=mapping["role"],
            harness=mapping["harness"],
            provider=mapping["provider"],
            model=mapping["model"],
            purpose=mapping["purpose"],
        )


def parse_ladder(value: str | None) -> list[str] | None:
    if value is None:
        return None
    ladder = [item.strip() for item in value.split(",") if item.strip()]
    if not ladder:
        raise SystemExit("ladder override must contain at least one role")
    return ladder


def load_harness_spec(value: str) -> dict[str, Any]:
    source = value.strip()
    if source.startswith("{"):
        data = json.loads(source)
        source_name = "inline --harness-spec"
    else:
        path = Path(value)
        data = json.loads(path.read_text(encoding="utf-8"))
        source_name = path.as_posix()
    if not isinstance(data, dict):
        raise SystemExit(f"harness spec must be a JSON object: {source_name}")
    if "name" in data:
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise SystemExit(f"harness spec name must be non-empty: {source_name}")
        spec = {key: value for key, value in data.items() if key != "name"}
        return {name: spec}
    return data


def apply_harness_specs(config: dict[str, Any], specs: list[str]) -> None:
    harnesses = config.setdefault("harnesses", default_harnesses())
    for value in specs:
        for name, spec in load_harness_spec(value).items():
            if not isinstance(name, str) or not name:
                raise SystemExit(f"harness spec has invalid name in {value}")
            if not isinstance(spec, dict):
                raise SystemExit(f"harness spec for {name!r} must be an object: {value}")
            harnesses[name] = spec


def apply_effort_profile(config: dict[str, Any], profile: str | None, contract: Any) -> None:
    if profile is None:
        return
    values = EFFORT_PROFILES.get(profile)
    if values is None:
        raise SystemExit(f"unknown effort profile: {profile}")
    aggressiveness = config.setdefault("aggressiveness", {})
    effort = config.setdefault("effort", {})
    requested = {
        "max_active_branch_agents": int(values["max_active_branch_agents"]),
        "max_active_worker_packets": int(values["max_active_worker_packets"]),
        "max_waves": int(values["max_waves"]),
    }
    for key in ("max_active_branch_agents", "max_active_worker_packets", "max_waves"):
        aggressiveness[key] = requested[key]
    normalize_aggressiveness_with_preflight_caps(
        config,
        requested,
        source=f"effort-profile:{profile}",
        contract=contract,
    )
    for key in ("lite_timeout_seconds", "demanding_timeout_seconds"):
        effort[key] = values[key]
    config["effort_profile"] = profile


def apply_validation_mode(config: dict[str, Any], mode: str) -> None:
    if mode not in VALIDATION_MODES:
        raise SystemExit(f"unknown validation mode: {mode}")
    config["validation"] = {
        "mode": mode,
        "require_models": True,
        "smoke": mode in {"smoke", "debug"},
        "debug_telemetry": mode == "debug",
    }
    telemetry = config.setdefault("telemetry", {})
    if mode == "debug":
        telemetry["mode"] = "debug"
        telemetry["raw_text"] = True
        config["preflight_intent"] = {"telemetry_mode": "debug"}
    else:
        telemetry.setdefault("mode", "standard")
        config.setdefault("preflight_intent", {})["telemetry_mode"] = telemetry["mode"]


def positive_int(value: int, field: str) -> int:
    if value <= 0:
        raise SystemExit(f"{field} must be a positive integer")
    return value


def apply_numeric_overrides(config: dict[str, Any], args: argparse.Namespace, contract: Any) -> None:
    aggressiveness = config.setdefault("aggressiveness", {})
    effort = config.setdefault("effort", {})
    requested: dict[str, int] = {}
    has_cap_override = False
    for arg_name, key in {
        "max_active_branch_agents": "max_active_branch_agents",
        "max_active_worker_packets": "max_active_worker_packets",
        "max_waves": "max_waves",
    }.items():
        value = getattr(args, arg_name)
        if value is not None:
            aggressiveness[key] = positive_int(value, f"--{arg_name.replace('_', '-')}")
            has_cap_override = True
        current = aggressiveness.get(key)
        if isinstance(current, int) and not isinstance(current, bool):
            requested[key] = int(current)

    for arg_name, key in {
        "lite_timeout_seconds": "lite_timeout_seconds",
        "demanding_timeout_seconds": "demanding_timeout_seconds",
    }.items():
        value = getattr(args, arg_name)
        if value is not None:
            effort[key] = positive_int(value, f"--{arg_name.replace('_', '-')}")

    if has_cap_override and len(requested) == 3:
        normalize_aggressiveness_with_preflight_caps(
            config,
            requested,
            source="numeric overrides",
            contract=contract,
        )

    if "max_active_branch_agents" in aggressiveness and "max_waves" in aggressiveness:
        aggressiveness["total_branch_cap"] = int(aggressiveness["max_active_branch_agents"]) * int(
            aggressiveness["max_waves"]
        )
    if any(getattr(args, name) is not None for name in (
        "max_active_branch_agents",
        "max_active_worker_packets",
        "max_waves",
        "lite_timeout_seconds",
        "demanding_timeout_seconds",
    )):
        config["effort_profile"] = "custom"


def smoke_token(role: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", role).strip("_").upper()
    return f"GOAL_CONFIG_{normalized or 'ROLE'}_SMOKE_OK"


def role_smoke_timeout(role: str, config: dict[str, Any]) -> int:
    ladders = config.get("model_ladders") if isinstance(config.get("model_ladders"), dict) else {}
    effort = config.get("effort") if isinstance(config.get("effort"), dict) else {}
    if role in set(ladders.get("lite") or []):
        return int(effort.get("lite_timeout_seconds") or 600)
    if role in set(ladders.get("worker") or []):
        return int(effort.get("worker_timeout_seconds") or effort.get("demanding_timeout_seconds") or 1200)
    return int(effort.get("demanding_timeout_seconds") or 1200)


def role_smoke_readback(role: str, config: dict[str, Any]) -> str:
    model = config.get("models", {}).get(role, {})
    harness_name = model.get("harness") if isinstance(model, dict) else None
    harness = config.get("harnesses", {}).get(harness_name, {}) if isinstance(config.get("harnesses"), dict) else {}
    kind = harness.get("kind") if isinstance(harness, dict) else None
    if kind == "opencode":
        return "opencode_session_db"
    return "stdout"


def ensure_harness_smokes(config: dict[str, Any]) -> None:
    smokes = config.setdefault("harness_smokes", {})
    if not isinstance(smokes, dict):
        raise SystemExit("harness_smokes must be an object")
    models = config.get("models")
    if not isinstance(models, dict):
        raise SystemExit("models must be an object before smoke generation")
    for role in sorted(models):
        if isinstance(smokes.get(role), dict):
            smoke = smokes[role]
            smoke.setdefault("timeout_seconds", role_smoke_timeout(role, config))
            smoke.setdefault("readback", role_smoke_readback(role, config))
            continue
        token = smoke_token(role)
        smokes[role] = {
            "prompt": f"Reply with {token} and nothing else.",
            "expect": token,
            "timeout_seconds": role_smoke_timeout(role, config),
            "readback": role_smoke_readback(role, config),
        }


def unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def route_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return normalized or "route"


def load_discovery_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"discovery report must be a JSON object: {path}")
    routes = data.get("accepted_routes")
    if not isinstance(routes, list) or not routes:
        raise SystemExit(f"discovery report has no accepted_routes: {path}")
    return data


def route_selector_text(route: dict[str, Any]) -> str:
    return " ".join(
        str(route.get(key, ""))
        for key in ("role", "alias", "harness", "provider", "model")
    ).lower()


def route_score(route: dict[str, Any], *, purpose: str) -> tuple[int, str]:
    text = route_selector_text(route)
    if purpose == "lite":
        score = 0
        for token, weight in {
            "flash": -30,
            "lite": -25,
            "mini": -20,
            "fast": -15,
            "pro": 20,
            "heavy": 25,
        }.items():
            if token in text:
                score += weight
    else:
        score = 0
        for token, weight in {
            "pro": -30,
            "heavy": -25,
            "gpt-5.4": -20,
            "latest": -10,
            "flash": 20,
            "lite": 25,
            "mini": 20,
        }.items():
            if token in text:
                score += weight
    return score, text


def select_route(routes: list[dict[str, Any]], *, purpose: str) -> dict[str, Any]:
    return sorted(routes, key=lambda route: route_score(route, purpose=purpose))[0]


def route_model_entry(role: str, route: dict[str, Any], *, alias: str | None = None, purpose: str | None = None) -> dict[str, Any]:
    for key in ("harness", "provider", "model"):
        if not isinstance(route.get(key), str) or not route.get(key):
            raise SystemExit(f"accepted route missing {key}: {route}")
    return model_entry(
        alias=alias or str(route.get("alias") or role),
        role=role,
        harness=str(route["harness"]),
        provider=str(route["provider"]),
        model=str(route["model"]),
        purpose=purpose or f"{role} from discovery",
    )


def find_route(routes: list[dict[str, Any]], selector: Any) -> dict[str, Any]:
    if isinstance(selector, int):
        index = selector
        if index < 0 or index >= len(routes):
            raise SystemExit(f"discovery mapping index out of range: {selector}")
        return routes[index]
    if isinstance(selector, str):
        for route in routes:
            if selector in {route.get("role"), route.get("alias"), route.get("model")}:
                return route
        raise SystemExit(f"discovery mapping selector did not match accepted route: {selector}")
    if isinstance(selector, dict):
        for route in routes:
            if all(route.get(key) == value for key, value in selector.items()):
                return route
        if all(isinstance(selector.get(key), str) and selector.get(key) for key in ("harness", "provider", "model")):
            return selector
    raise SystemExit(f"unsupported discovery mapping selector: {selector!r}")


def apply_discovery_mapping(config: dict[str, Any], discovery_path: Path | None, mapping: str | None) -> None:
    if discovery_path is None:
        return
    report = load_discovery_report(discovery_path)
    routes = [route for route in report["accepted_routes"] if isinstance(route, dict)]
    if not routes:
        raise SystemExit(f"discovery report has no object accepted_routes: {discovery_path}")

    mapping = mapping or "auto"
    if mapping == "auto":
        lite_route = select_route(routes, purpose="lite")
        demanding_route = select_route(routes, purpose="demanding")
        models = {
            "lite_agent": route_model_entry(
                "lite_agent",
                lite_route,
                alias="discovered-lite",
                purpose="auto-selected Lite route from discovery",
            ),
            "demanding_agent": route_model_entry(
                "demanding_agent",
                demanding_route,
                alias="discovered-demanding",
                purpose="auto-selected demanding route from discovery",
            ),
        }
        extra_roles: list[str] = []
        seen_route_keys = {
            (models["lite_agent"]["harness"], models["lite_agent"]["provider"], models["lite_agent"]["model"]),
            (models["demanding_agent"]["harness"], models["demanding_agent"]["provider"], models["demanding_agent"]["model"]),
        }
        for route in routes:
            key = (route.get("harness"), route.get("provider"), route.get("model"))
            if key in seen_route_keys:
                continue
            role = f"discovered_{len(extra_roles) + 1}_{route_id(str(route.get('model', 'route')))}"
            models[role] = route_model_entry(role, route)
            extra_roles.append(role)
            seen_route_keys.add(key)
    else:
        mapping_path = Path(mapping)
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise SystemExit(f"discovery mapping must be a non-empty JSON object: {mapping_path}")
        models = {
            role: route_model_entry(role, find_route(routes, selector), alias=role)
            for role, selector in data.items()
        }
        extra_roles = [role for role in models if role not in {"lite_agent", "demanding_agent"}]

    if "lite_agent" not in models:
        models["lite_agent"] = route_model_entry("lite_agent", routes[0], alias="discovered-lite")
    if "demanding_agent" not in models:
        models["demanding_agent"] = route_model_entry("demanding_agent", routes[0], alias="discovered-demanding")

    worker = unique(["demanding_agent", "lite_agent", *extra_roles])
    config["profile"] = "from-discovery"
    config["source_discovery"] = {
        "path": discovery_path.as_posix(),
        "mapping": mapping,
        "accepted_route_count": len(routes),
    }
    config["models"] = models
    config["model_ladders"] = {
        "lite": ["lite_agent"],
        "worker": worker,
        "reviewer": ["demanding_agent"],
        "amender": ["demanding_agent", "lite_agent"],
        "demanding": ["demanding_agent"],
    }
    config["harness_smokes"] = {}


def require_roles(config: dict[str, Any], ladder_name: str, ladder: list[str]) -> None:
    models = config.get("models", {})
    for role in ladder:
        if role not in models:
            raise SystemExit(f"model_ladders.{ladder_name} references unknown model role: {role}")


def build_model_policies(config: dict[str, Any], contract: Any) -> dict[str, Any]:
    ladders = config.get("model_ladders")
    if not isinstance(ladders, dict):
        raise SystemExit("model_ladders must be an object")
    worker = list(ladders.get("worker") or [])
    reviewer = list(ladders.get("reviewer") or ladders.get("demanding") or worker)
    amender = list(ladders.get("amender") or reviewer)
    lite = list(ladders.get("lite") or worker[-1:])
    for name, ladder in {
        "worker": worker,
        "reviewer": reviewer,
        "amender": amender,
        "lite": lite,
    }.items():
        if not ladder:
            raise SystemExit(f"model_ladders.{name} must contain at least one role")
        require_roles(config, name, ladder)

    worker_allowed = unique(worker)
    worker_route_classes = {
        "mechanical": [worker[-1]],
        "docs": [worker[-1]],
        "small-edit": worker,
        "normal-code": worker,
        "complex-code": worker,
        "custom": worker,
    }
    return {
        "worker_model_policy": {
            "source": "goal_config",
            "default_ladder": worker,
            "allowed_routes": worker_allowed,
            "default_route_class": contract.DEFAULT_WORKER_ROUTE_CLASS,
            "route_classes": worker_route_classes,
            "branch_may_select_worker_route": True,
            "selection_reason_required": True,
            "ordering_rule": "Selected worker routes must be a non-empty ordered subsequence of default_ladder.",
        },
        "review_model_policy": {
            "source": "goal_config",
            "router": "goal-config-v1",
            "default_tier": "standard",
            "routes": {
                "light": reviewer,
                "standard": reviewer,
                "heavy": reviewer,
            },
            "heavy_triggers": list(contract.REVIEW_MODEL_POLICY.get("heavy_triggers", [])),
        },
        "amender_model_policy": {
            "source": "goal_config",
            "default_ladder": amender,
            "allowed_routes": unique(amender),
            "launcher": "goal-main-orchestrator",
            "selection_reason_required": True,
            "ordering_rule": "Selected amender routes must be a non-empty ordered subsequence of allowed_routes.",
            "sandbox": "read-only",
            "timeout_seconds": int(config.get("effort", {}).get("amender_timeout_seconds") or contract.AMENDER_ATTEMPT_TIMEOUT_SECONDS),
        },
        "lite_model_policy": {
            "source": "goal_config",
            "default_ladder": lite,
            "allowed_routes": unique(lite),
            "model_map": {
                role: config["models"][role]["model"]
                for role in lite
            },
            "launcher": "create_lite_advice_packet.py",
            "selection_reason_required": False,
            "ordering_rule": "Lite advisors use configured goal_config routes only.",
            "timeout_seconds": int(config.get("effort", {}).get("lite_timeout_seconds") or contract.LITE_ATTEMPT_TIMEOUT_SECONDS),
        },
    }


def finalize_config(config: dict[str, Any], contract: Any, args: argparse.Namespace) -> dict[str, Any]:
    apply_harness_specs(config, args.harness_spec)
    apply_discovery_mapping(config, args.from_discovery, args.mapping)
    apply_role_overrides(config["models"], args.role_model, args.provider)
    apply_effort_profile(config, args.effort_profile, contract)
    apply_numeric_overrides(config, args, contract)
    apply_validation_mode(config, args.validation_mode)
    ladders = config.setdefault("model_ladders", {})
    for name, value in {
        "lite": args.lite_ladder,
        "worker": args.worker_ladder,
        "reviewer": args.reviewer_ladder,
        "amender": args.amender_ladder,
    }.items():
        parsed = parse_ladder(value)
        if parsed is not None:
            ladders[name] = parsed
    if "reviewer" not in ladders and "demanding" in ladders:
        ladders["reviewer"] = list(ladders["demanding"])
    if "amender" not in ladders and "reviewer" in ladders:
        ladders["amender"] = list(ladders["reviewer"])
    ensure_harness_smokes(config)
    config["model_policies"] = build_model_policies(config, contract)
    return config


def base_config(contract: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "goal-orchestration-config",
        "profile": "current-default",
        "usage_units": {
            "token_counts": ["input", "output", "reasoning", "cache_read", "cache_write"],
            "text_counts": ["prompt_chars", "response_chars", "stdout_chars", "stderr_chars"],
            "time_counts": ["elapsed_ms", "timeout_seconds"],
        },
        "aggressiveness": {
            "max_active_branch_agents": contract.MAX_ACTIVE_BRANCH_AGENTS,
            "max_active_worker_packets": contract.MAX_WORKER_PACKETS_PER_BRANCH,
            "max_waves": contract.MAX_WAVES,
            "total_branch_cap": contract.DEFAULT_TOTAL_BRANCH_CAP,
        },
        "effort": {
            "lite_timeout_seconds": contract.LITE_ATTEMPT_TIMEOUT_SECONDS,
            "demanding_timeout_seconds": contract.REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
            "worker_timeout_seconds": contract.WORKER_ATTEMPT_TIMEOUT_SECONDS,
            "research_timeout_seconds": contract.RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
            "amender_timeout_seconds": contract.AMENDER_ATTEMPT_TIMEOUT_SECONDS,
            "timeout_kill_after_seconds": contract.TIMEOUT_KILL_AFTER_SECONDS,
            "max_prompt_chars": 24000,
            "max_response_chars": 8000,
        },
        "models": {
            "lite_agent": model_entry(
                alias="gemini-lite",
                role="lite_agent",
                harness="gemini",
                provider="gemini",
                model=contract.LITE_MODEL,
                purpose="low-token advisory summaries and routing hints",
            ),
            "worker_primary": model_entry(
                alias="codex-spark",
                role="worker_primary",
                harness="codex",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["codex-spark"],
                purpose="ordinary bounded implementation work",
            ),
            "worker_fallback": model_entry(
                alias="codex-mini",
                role="worker_fallback",
                harness="codex",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["codex-mini"],
                purpose="cheap fallback and mechanical work",
            ),
            "demanding_agent": model_entry(
                alias="gpt-5.4",
                role="demanding_agent",
                harness="codex",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["gpt-5.4"],
                purpose="review, planning, and higher-risk reasoning",
            ),
        },
        "model_ladders": {
            "lite": ["lite_agent"],
            "worker": ["worker_primary", "worker_fallback"],
            "reviewer": ["demanding_agent"],
            "amender": ["demanding_agent", "worker_primary"],
            "demanding": ["demanding_agent", "worker_primary"],
        },
        "harness_smokes": {},
        "harnesses": default_harnesses(),
        "telemetry": {
            "mode": "standard",
            "group_by": ["role", "harness", "provider", "model"],
            "collect": list(contract.TELEMETRY_COLLECT_ITEMS),
            "raw_text": False,
        },
    }


def opencode_deepseek_v4_config(contract: Any, args: argparse.Namespace) -> dict[str, Any]:
    config = base_config(contract)
    lite_provider, lite_model = normalize_role_model_for_harness(args.lite_model, "opencode", args.provider)
    demanding_provider, demanding_model = normalize_role_model_for_harness(
        args.demanding_model,
        "opencode",
        args.provider,
    )
    config["profile"] = "opencode-deepseek-v4"
    config["models"] = {
        "lite_agent": model_entry(
            alias="opencode-deepseek-v4-flash",
            role="lite_agent",
            harness="opencode",
            provider=lite_provider,
            model=lite_model,
            purpose="low-latency advisory and small deterministic checks",
        ),
        "demanding_agent": model_entry(
            alias="opencode-deepseek-v4-pro",
            role="demanding_agent",
            harness="opencode",
            provider=demanding_provider,
            model=demanding_model,
            purpose="higher-effort planning, review, and complex coding checks",
        ),
    }
    config["model_ladders"] = {
        "lite": ["lite_agent"],
        "worker": ["demanding_agent", "lite_agent"],
        "reviewer": ["demanding_agent"],
        "amender": ["demanding_agent", "lite_agent"],
    }
    config["effort"]["lite_timeout_seconds"] = 600
    config["effort"]["demanding_timeout_seconds"] = 1200
    return config


def write_json(data: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if output is None or output.as_posix() == "-":
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def write_state(config: dict[str, Any], output: Path | None, state_output: Path | None) -> None:
    if state_output is None:
        return
    config_path = output.resolve().as_posix() if output is not None and output.as_posix() != "-" else None
    validation = config.get("validation") if isinstance(config.get("validation"), dict) else {}
    smoke = bool(validation.get("smoke"))
    next_command = None
    if config_path:
        next_command = (
            "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py "
            f"--config {config_path} --require-models"
        )
        if smoke:
            next_command += " --smoke"
        check_report = "/abs/goal-config-smoke.json" if smoke else "/abs/goal-config-check.json"
        next_command += f" --output {check_report}"
    state = {
        "schema_version": 1,
        "phase": "config_created",
        "complete": False,
        "missing_preferences": [],
        "config_path": config_path,
        "validation_mode": validation.get("mode"),
        "next_command": next_command,
    }
    state_output.parent.mkdir(parents=True, exist_ok=True)
    state_output.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        choices=("current-default", "opencode-deepseek-v4"),
        default="current-default",
        help="Configuration preset to render.",
    )
    parser.add_argument("--output", type=Path, help="Write goal.config.json to this path; use - for stdout.")
    parser.add_argument("--provider", help="Provider id override for provider/model strings.")
    parser.add_argument("--from-discovery", type=Path, help="Build an explicit config from a goal-config discovery report.")
    parser.add_argument("--mapping", help="Discovery mapping mode: auto or path to role-selector JSON.")
    parser.add_argument(
        "--effort-profile",
        choices=tuple(EFFORT_PROFILES),
        default="balanced",
        help="Apply deterministic effort/aggressiveness defaults before numeric overrides.",
    )
    parser.add_argument(
        "--validation-mode",
        choices=tuple(sorted(VALIDATION_MODES)),
        default="model-check",
        help="Serialize model-check, smoke, or debug validation intent into the config.",
    )
    parser.add_argument("--state-output", type=Path, help="Write goal-config-state.json for UX/state handoff.")
    parser.add_argument("--lite-model", default="deepseek/deepseek-v4-flash", help="Lite opencode provider/model.")
    parser.add_argument("--demanding-model", default="deepseek/deepseek-v4-pro", help="Demanding opencode provider/model.")
    parser.add_argument("--max-active-branch-agents", type=int)
    parser.add_argument("--max-active-worker-packets", type=int)
    parser.add_argument("--max-waves", type=int)
    parser.add_argument("--lite-timeout-seconds", type=int)
    parser.add_argument("--demanding-timeout-seconds", type=int)
    parser.add_argument("--lite-ladder", help="Comma-separated model roles for Lite routing.")
    parser.add_argument("--worker-ladder", help="Comma-separated model roles for worker routing.")
    parser.add_argument("--reviewer-ladder", help="Comma-separated model roles for reviewer routing.")
    parser.add_argument("--amender-ladder", help="Comma-separated model roles for plan-amender routing.")
    parser.add_argument(
        "--harness-spec",
        action="append",
        default=[],
        help="Path to a JSON harness spec, or an inline JSON object. Repeat to add/override CLI harnesses.",
    )
    parser.add_argument(
        "--role-model",
        action="append",
        default=[],
        help=(
            "Override role mapping as ROLE:HARNESS:PROVIDER/MODEL[:ALIAS[:PURPOSE]] "
            "or ROLE:HARNESS:MODEL for harnesses with implied providers. "
            "Repeat for each role you want to change."
        ),
    )
    args = parser.parse_args()

    contract = load_contract()
    if args.preset == "opencode-deepseek-v4":
        config = opencode_deepseek_v4_config(contract, args)
    else:
        config = base_config(contract)

    finalize_config(config, contract, args)

    write_json(config, args.output)
    write_state(config, args.output, args.state_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
