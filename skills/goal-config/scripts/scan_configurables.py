#!/usr/bin/env python3
"""Inventory configurable goal orchestration parameters."""

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


def build_inventory(contract: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "pass",
        "usage_units": {
            "tokens": ["input", "output", "reasoning", "cache_read", "cache_write"],
            "characters": ["prompt", "response", "stdout", "stderr"],
            "time": ["elapsed_ms", "timeout_seconds"],
        },
        "categories": {
            "aggressiveness": {
                "description": "Controls parallel goal scheduling pressure.",
                "parameters": {
                    "max_active_branch_agents": contract.MAX_ACTIVE_BRANCH_AGENTS,
                    "max_active_worker_packets": contract.MAX_WORKER_PACKETS_PER_BRANCH,
                    "max_waves": contract.MAX_WAVES,
                    "default_total_branch_cap": contract.DEFAULT_TOTAL_BRANCH_CAP,
                },
            },
            "timeouts": {
                "description": "Per-attempt timeout settings for agent and deterministic phases.",
                "parameters": {
                    "audit_attempt_timeout_seconds": contract.AUDIT_ATTEMPT_TIMEOUT_SECONDS,
                    "worker_attempt_timeout_seconds": contract.WORKER_ATTEMPT_TIMEOUT_SECONDS,
                    "research_attempt_timeout_seconds": contract.RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
                    "reviewer_attempt_timeout_seconds": contract.REVIEWER_ATTEMPT_TIMEOUT_SECONDS,
                    "amender_attempt_timeout_seconds": contract.AMENDER_ATTEMPT_TIMEOUT_SECONDS,
                    "lite_attempt_timeout_seconds": contract.LITE_ATTEMPT_TIMEOUT_SECONDS,
                    "timeout_kill_after_seconds": contract.TIMEOUT_KILL_AFTER_SECONDS,
                },
            },
            "worker_routes": {
                "description": "Default worker ladders and route classes.",
                "parameters": {
                    "default_worker_ladder": list(contract.DEFAULT_WORKER_LADDER),
                    "allowed_worker_routes": sorted(contract.ALLOWED_WORKER_ROUTES),
                    "default_worker_route_class": contract.DEFAULT_WORKER_ROUTE_CLASS,
                    "worker_route_classes": list(contract.WORKER_ROUTE_CLASSES),
                    "worker_route_class_ladders": {
                        key: list(value) for key, value in contract.WORKER_ROUTE_CLASS_LADDERS.items()
                    },
                },
            },
            "lite": {
                "description": "Low-effort advisory route.",
                "parameters": {
                    "default_lite_ladder": list(contract.DEFAULT_LITE_LADDER),
                    "allowed_lite_routes": list(contract.ALLOWED_LITE_ROUTES),
                    "lite_model": contract.LITE_MODEL,
                    "lite_approval_mode": contract.LITE_APPROVAL_MODE,
                    "lite_model_policy": contract.LITE_MODEL_POLICY,
                },
            },
            "review": {
                "description": "Reviewer route tiers and heavy-review triggers.",
                "parameters": {
                    "review_route_tiers": list(contract.REVIEW_ROUTE_TIERS),
                    "review_model_routes": {key: list(value) for key, value in contract.REVIEW_MODEL_ROUTES.items()},
                    "review_heavy_trigger_patterns": list(contract.REVIEW_HEAVY_TRIGGER_PATTERNS),
                },
            },
            "amender": {
                "description": "Plan amender ladder and deterministic repair aliases.",
                "parameters": {
                    "default_amender_ladder": list(contract.DEFAULT_AMENDER_LADDER),
                    "allowed_amender_routes": list(contract.ALLOWED_AMENDER_ROUTES),
                    "deterministic_amender_alias": contract.DETERMINISTIC_AMENDER_ALIAS,
                },
            },
            "research": {
                "description": "Research worker aliases and timeout.",
                "parameters": {
                    "research_aliases": list(contract.RESEARCH_ALIASES),
                    "research_attempt_timeout_seconds": contract.RESEARCH_ATTEMPT_TIMEOUT_SECONDS,
                    "research_worker_policy": contract.RESEARCH_WORKER_POLICY,
                },
            },
            "watchdog": {
                "description": "No-completion wait limits and stale-agent handling.",
                "parameters": contract.ORCHESTRATION_WATCHDOG,
            },
            "telemetry": {
                "description": "Standard/debug telemetry modes and safe collection fields.",
                "parameters": {
                    "telemetry_policy_modes": list(contract.TELEMETRY_POLICY_MODES),
                    "telemetry_collect_items": list(contract.TELEMETRY_COLLECT_ITEMS),
                    "telemetry_policy_default": contract.TELEMETRY_POLICY_DEFAULT,
                    "telemetry_debug_name": contract.TELEMETRY_DEBUG_NAME,
                    "telemetry_debug_events_name": contract.TELEMETRY_DEBUG_EVENTS_NAME,
                },
            },
            "harnesses": {
                "description": "CLI harnesses supported by goal-config and checker validation.",
                "parameters": {
                    "supported_kinds": [
                        "opencode",
                        "codex",
                        "gemini",
                        "generic-cli",
                    ],
                    "default_harnesses": [
                        "opencode",
                        "codex",
                        "gemini",
                        "antigravity",
                    ],
                    "opencode_model_availability_command": "opencode models <provider>",
                    "opencode_smoke_shape": "run --pure --format json --model <provider/model> <prompt>",
                    "codex_smoke_shape": "codex exec <prompt>",
                    "gemini_smoke_shape": "gemini <prompt>",
                    "generic_smoke_shape": "custom command template with {prompt}, {model}, {provider}, {role}, {alias}",
                    "smoke_reports": "per-role harness_smokes with prompt/expect/timeout_seconds",
                },
            },
        },
        "source_files": [
            "skills/_goal_shared/scripts/orchestration_contract.py",
            "skills/goal-config/scripts/create_goal_config.py",
            "skills/goal-config/scripts/check_goal_config.py",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the inventory as JSON.")
    args = parser.parse_args()

    inventory = build_inventory(load_contract())
    if args.json:
        print(json.dumps(inventory, indent=2, sort_keys=True))
        return 0

    print("status=pass")
    for name, category in inventory["categories"].items():
        print(f"- {name}: {category['description']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
