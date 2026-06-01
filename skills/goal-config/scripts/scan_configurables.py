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


def build_preference_questions() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "pass",
        "purpose": "Ask before creating goal.config.json when the user has not already supplied these preferences.",
        "interaction": {
            "style": "ordered_options",
            "ask_order": ["model_profile", "effort_profile", "validation_mode"],
            "max_sections_per_turn": 1,
            "instructions": [
                "Start with the first missing question in ask_order.",
                "For the current question, show the explanation and every option with its short description.",
                "Ask the next missing question only after the previous preference is answered unless the user asks for all questions at once.",
                "For custom options, collect the requested exact harness/provider/model or numeric values before creating goal.config.json.",
            ],
        },
        "do_not_create_until": [
            "do not create goal.config.json until model_profile is answered or the user explicitly says to use defaults",
            "do not create goal.config.json until effort_profile is answered or the user explicitly says to use defaults",
            "do not create goal.config.json until validation_mode is answered or the user explicitly says to use defaults",
        ],
        "ask_only_missing": True,
        "questions": [
            {
                "id": "model_profile",
                "order": 1,
                "title": "Model profile",
                "ask_when_missing": ["preset", "role-model", "existing checked profile path"],
                "explain_to_user": "This chooses the CLI harnesses and role-to-model ladder. It affects quality, latency, token/character use, and whether configured models can be verified automatically.",
                "question": "Choose the model/harness profile for this goal.",
                "recommended_default": "reuse an existing checked profile when available; otherwise ask before using opencode-deepseek-v4",
                "options": [
                    {
                        "id": "reuse_checked",
                        "label": "Reuse checked config",
                        "description": "Use an existing goal.config.json plus its passing check report. Fastest when the profile was already validated.",
                        "needs": ["path to goal.config.json", "path to goal-config check or smoke report"],
                        "maps_to": ["--goal-config", "--goal-config-check"],
                    },
                    {
                        "id": "current_default",
                        "label": "Current default",
                        "description": "Use the bundled default Codex/Gemini route policy and then validate the configured models.",
                        "maps_to": ["create_goal_config.py --preset current-default"],
                    },
                    {
                        "id": "opencode_deepseek_v4",
                        "label": "Opencode DeepSeek v4",
                        "description": "Use opencode with DeepSeek v4 Flash for Lite work and DeepSeek v4 Pro for demanding work.",
                        "maps_to": ["create_goal_config.py --preset opencode-deepseek-v4"],
                    },
                    {
                        "id": "discover_available",
                        "label": "Discover available",
                        "description": "List candidate models from configured CLIs, smoke selected candidates, then report accepted_routes and rejected_routes. Slower but useful when the user says to use all available models.",
                        "needs": ["providers or harnesses to scan", "roles each accepted route may serve"],
                        "maps_to": ["check_goal_config.py --discover-provider PROVIDER --smoke", "accepted_routes", "rejected_routes"],
                    },
                    {
                        "id": "gemini",
                        "label": "Gemini models",
                        "description": "Use the gemini CLI for selected roles. Ask for the exact Gemini model names if the user has not supplied them.",
                        "needs": ["role-to-model choices or explicit Gemini model names"],
                        "maps_to": ["--role-model ROLE:gemini:gemini/MODEL"],
                    },
                    {
                        "id": "agy_generic_cli",
                        "label": "Antigravity/agy generic CLI",
                        "description": "Use agy through a generic CLI harness. Model selection is only configurable if that CLI exposes it, so smoke validation is required.",
                        "needs": ["harness spec", "smoke command", "runtime command"],
                        "maps_to": ["--harness-spec /abs/custom-harness.json", "--role-model ROLE:agy:provider/model"],
                    },
                    {
                        "id": "custom_mixed",
                        "label": "Mixed/custom",
                        "description": "Use explicit role mappings across opencode, codex, gemini, or generic-cli harnesses.",
                        "needs": ["role-model mapping for each changed role", "optional custom harness specs"],
                        "maps_to": ["--role-model", "--harness-spec", "--lite-ladder", "--worker-ladder", "--reviewer-ladder", "--amender-ladder"],
                    },
                ],
                "maps_to": ["--preset", "--role-model", "--harness-spec", "--lite-ladder", "--worker-ladder", "--reviewer-ladder", "--amender-ladder"],
            },
            {
                "id": "effort_profile",
                "order": 2,
                "title": "Effort profile",
                "ask_when_missing": ["aggressiveness", "timeouts", "branch/worker caps"],
                "explain_to_user": "This controls parallelism, timeouts, and output pressure. It changes elapsed time and token/character use, not USD cost.",
                "question": "Choose the effort profile for this goal.",
                "recommended_default": "balanced: configured default caps and timeouts",
                "options": [
                    {
                        "id": "lean",
                        "label": "Lean",
                        "description": "Fewer active branches/workers, shorter timeouts, and tighter output limits for low token/character use.",
                        "maps_to": ["lower --max-active-branch-agents", "lower --max-active-worker-packets", "shorter --lite-timeout-seconds", "shorter --demanding-timeout-seconds"],
                    },
                    {
                        "id": "balanced",
                        "label": "Balanced",
                        "description": "Use default caps and timeouts. Good default when the user has no strong cost or latency preference.",
                        "maps_to": ["configured default caps and timeouts"],
                    },
                    {
                        "id": "thorough",
                        "label": "Thorough",
                        "description": "Use higher allowed parallelism within hard caps and longer timeouts for harder goals.",
                        "maps_to": ["higher --max-active-branch-agents within caps", "higher --max-active-worker-packets within caps", "longer timeout flags"],
                    },
                    {
                        "id": "custom",
                        "label": "Custom",
                        "description": "Ask for exact branch cap, worker cap, wave cap, Lite timeout, and demanding-agent timeout.",
                        "needs": ["max active branches", "max active worker packets", "max waves", "Lite timeout seconds", "demanding timeout seconds"],
                        "maps_to": ["--max-active-branch-agents", "--max-active-worker-packets", "--max-waves", "--lite-timeout-seconds", "--demanding-timeout-seconds"],
                    },
                ],
                "maps_to": ["--max-active-branch-agents", "--max-active-worker-packets", "--lite-timeout-seconds", "--demanding-timeout-seconds"],
            },
            {
                "id": "validation_mode",
                "order": 3,
                "title": "Validation and debug telemetry",
                "ask_when_missing": ["model check", "smoke requirement", "debug telemetry intent"],
                "explain_to_user": "This decides how hard the harness is tested before orchestration and whether preflight should request full debug telemetry for later efficiency analysis.",
                "question": "Choose the validation and telemetry mode.",
                "recommended_default": "fail-closed model check plus smoke for new or changed harnesses",
                "options": [
                    {
                        "id": "model_check_only",
                        "label": "Model check only",
                        "description": "Verify configured models are available. Use this only for a recently checked profile or a quick config refresh.",
                        "maps_to": ["check_goal_config.py --require-models"],
                    },
                    {
                        "id": "model_check_plus_smoke",
                        "label": "Model check plus smoke",
                        "description": "Verify model availability and run assistant smoke tests for selected roles before preflight consumes the config.",
                        "maps_to": ["check_goal_config.py --require-models --smoke"],
                    },
                    {
                        "id": "full_debug_trace",
                        "label": "Smoke plus debug telemetry",
                        "description": "Run model checks and smoke tests, then request debug telemetry in the preflight brief for full trace analysis.",
                        "maps_to": ["check_goal_config.py --require-models --smoke", "brief telemetry_mode=debug"],
                    },
                    {
                        "id": "custom_validation",
                        "label": "Custom validation",
                        "description": "Ask which roles need smoke tests and whether debug telemetry should be enabled.",
                        "needs": ["roles to smoke", "debug telemetry yes/no"],
                        "maps_to": ["check_goal_config.py --harness ROLE", "optional brief telemetry_mode=debug"],
                    },
                ],
                "maps_to": ["check_goal_config.py --require-models", "check_goal_config.py --smoke", "brief telemetry_mode=debug"],
            },
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print the inventory as JSON.")
    parser.add_argument("--questions-json", action="store_true", help="Print preference-intake questions as JSON.")
    args = parser.parse_args()

    if args.questions_json:
        print(json.dumps(build_preference_questions(), indent=2, sort_keys=True))
        return 0

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
