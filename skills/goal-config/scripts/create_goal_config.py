#!/usr/bin/env python3
"""Create deterministic goal orchestration configuration profiles."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def load_contract() -> Any:
    shared_path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    spec = importlib.util.spec_from_file_location("_goal_shared_orchestration_contract", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared contract: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def split_provider(model: str, provider: str | None = None) -> str:
    if provider:
        return provider
    if "/" not in model:
        raise SystemExit(f"model must be provider/model when provider is omitted: {model}")
    return model.split("/", 1)[0]


def model_entry(*, alias: str, role: str, harness: str, provider: str, model: str, purpose: str) -> dict[str, Any]:
    return {
        "alias": alias,
        "role": role,
        "harness": harness,
        "provider": provider,
        "model": model,
        "purpose": purpose,
    }


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
                harness="gemini-cli",
                provider="gemini",
                model=contract.LITE_MODEL,
                purpose="low-token advisory summaries and routing hints",
            ),
            "worker_primary": model_entry(
                alias="codex-spark",
                role="worker_primary",
                harness="codex-cli",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["codex-spark"],
                purpose="ordinary bounded implementation work",
            ),
            "worker_fallback": model_entry(
                alias="codex-mini",
                role="worker_fallback",
                harness="codex-cli",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["codex-mini"],
                purpose="cheap fallback and mechanical work",
            ),
            "demanding_agent": model_entry(
                alias="gpt-5.4",
                role="demanding_agent",
                harness="codex-cli",
                provider="openai",
                model=contract.CODEX_ROUTE_MODELS["gpt-5.4"],
                purpose="review, planning, and higher-risk reasoning",
            ),
        },
        "model_ladders": {
            "lite": ["lite_agent"],
            "worker": ["worker_primary", "worker_fallback"],
            "demanding": ["demanding_agent", "worker_primary"],
        },
        "harness_smokes": {},
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
    lite_model = args.lite_model
    demanding_model = args.demanding_model
    lite_provider = split_provider(lite_model, args.provider)
    demanding_provider = split_provider(demanding_model, args.provider)
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
    config["harness_smokes"] = {
        "lite_agent": {
            "prompt": "Reply with GOAL_CONFIG_LITE_SMOKE_OK and nothing else.",
            "expect": "GOAL_CONFIG_LITE_SMOKE_OK",
            "timeout_seconds": args.lite_timeout_seconds,
            "readback": "opencode_session_db",
        },
        "demanding_agent": {
            "prompt": "Reply with GOAL_CONFIG_DEMANDING_SMOKE_OK and nothing else.",
            "expect": "GOAL_CONFIG_DEMANDING_SMOKE_OK",
            "timeout_seconds": args.demanding_timeout_seconds,
            "readback": "opencode_session_db",
        },
    }
    config["effort"]["lite_timeout_seconds"] = args.lite_timeout_seconds
    config["effort"]["demanding_timeout_seconds"] = args.demanding_timeout_seconds
    config["aggressiveness"]["max_active_branch_agents"] = args.max_active_branch_agents
    config["aggressiveness"]["max_active_worker_packets"] = args.max_active_worker_packets
    config["aggressiveness"]["max_waves"] = args.max_waves
    config["aggressiveness"]["total_branch_cap"] = args.max_active_branch_agents * args.max_waves
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
    parser.add_argument("--max-active-branch-agents", type=int, default=4)
    parser.add_argument("--max-active-worker-packets", type=int, default=4)
    parser.add_argument("--max-waves", type=int, default=5)
    parser.add_argument("--lite-timeout-seconds", type=int, default=600)
    parser.add_argument("--demanding-timeout-seconds", type=int, default=1200)
    args = parser.parse_args()

    contract = load_contract()
    if args.preset == "opencode-deepseek-v4":
        config = opencode_deepseek_v4_config(contract, args)
    else:
        config = base_config(contract)
    write_json(config, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
