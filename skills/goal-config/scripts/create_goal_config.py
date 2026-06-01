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


def load_contract() -> Any:
    shared_path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    spec = importlib.util.spec_from_file_location("_goal_shared_orchestration_contract", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared contract: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_role_model(spec: str, default_provider: str | None = None) -> dict[str, str]:
    parts = spec.split(":")
    if len(parts) < 3:
        raise SystemExit(
            "role-model spec must be ROLE:HARNESS:PROVIDER/MODEL or ROLE:HARNESS:MODEL with --provider"
        )

    role, harness, provider_model = parts[:3]
    aliases = parts[3:]
    if "/" not in provider_model:
        if default_provider:
            provider_model = f"{default_provider}/{provider_model}"
        else:
            raise SystemExit(
                f"model for role {role!r} must include provider/model or use --provider: {provider_model!r}"
            )
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
            "command": "antigravity",
            "smoke_args": ["{prompt}"],
            "run_args": ["{prompt_file}"],
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


def load_harness_spec(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"harness spec must be a JSON object: {path}")
    if "name" in data:
        name = data.get("name")
        if not isinstance(name, str) or not name:
            raise SystemExit(f"harness spec name must be non-empty: {path}")
        spec = {key: value for key, value in data.items() if key != "name"}
        return {name: spec}
    return data


def apply_harness_specs(config: dict[str, Any], paths: list[Path]) -> None:
    harnesses = config.setdefault("harnesses", default_harnesses())
    for path in paths:
        for name, spec in load_harness_spec(path).items():
            if not isinstance(name, str) or not name:
                raise SystemExit(f"harness spec has invalid name in {path}")
            if not isinstance(spec, dict):
                raise SystemExit(f"harness spec for {name!r} must be an object: {path}")
            harnesses[name] = spec


def positive_int(value: int, field: str) -> int:
    if value <= 0:
        raise SystemExit(f"{field} must be a positive integer")
    return value


def apply_numeric_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    aggressiveness = config.setdefault("aggressiveness", {})
    effort = config.setdefault("effort", {})
    for arg_name, key in {
        "max_active_branch_agents": "max_active_branch_agents",
        "max_active_worker_packets": "max_active_worker_packets",
        "max_waves": "max_waves",
    }.items():
        value = getattr(args, arg_name)
        if value is not None:
            aggressiveness[key] = positive_int(value, f"--{arg_name.replace('_', '-')}")

    for arg_name, key in {
        "lite_timeout_seconds": "lite_timeout_seconds",
        "demanding_timeout_seconds": "demanding_timeout_seconds",
    }.items():
        value = getattr(args, arg_name)
        if value is not None:
            effort[key] = positive_int(value, f"--{arg_name.replace('_', '-')}")

    if "max_active_branch_agents" in aggressiveness and "max_waves" in aggressiveness:
        aggressiveness["total_branch_cap"] = int(aggressiveness["max_active_branch_agents"]) * int(
            aggressiveness["max_waves"]
        )


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
    apply_role_overrides(config["models"], args.role_model, args.provider)
    apply_harness_specs(config, args.harness_spec)
    apply_numeric_overrides(config, args)
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
            "collect": [
                "prompt_chars",
                "response_chars",
                "stdout_chars",
                "stderr_chars",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "elapsed_ms",
                "returncode",
            ],
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
        type=Path,
        default=[],
        help="JSON harness spec object or {name, ...spec}. Repeat to add/override CLI harnesses.",
    )
    parser.add_argument(
        "--role-model",
        action="append",
        default=[],
        help=(
            "Override role mapping as ROLE:HARNESS:PROVIDER/MODEL[:ALIAS[:PURPOSE]]. "
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
