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
            "token_counts": ["input", "output", "reasoning", "cache_read", "cache_write"],
            "text_counts": ["prompt_chars", "response_chars", "stdout_chars", "stderr_chars"],
            "time_counts": ["elapsed_ms", "timeout_seconds"],
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
                        "opencode-bridge",
                        "codex",
                        "generic-cli",
                    ],
                    "default_harnesses": [
                        "opencode-bridge",
                        "codex",
                    ],
                    "bridge_harness_kind": contract.BRIDGE_HARNESS_KIND,
                    "bridge_provider_id": contract.BRIDGE_PROVIDER_ID,
                    "bridge_route_aliases": list(contract.BRIDGE_ROUTE_ALIASES),
                    "bridge_route_models": dict(contract.BRIDGE_ROUTE_MODELS),
                    "bridge_route_variants": dict(contract.BRIDGE_ROUTE_VARIANTS),
                    "bridge_control_script": "opencode_worker.py (opencode-worker-bridge skill)",
                    "bridge_offline_readiness_command": "opencode_worker.py doctor --json",
                    "bridge_runtime_shape": "opencode_worker.py supervisor/delegate --provider deepseek --model <deepseek-v4-{pro,flash}> --variant max",
                    "codex_smoke_shape": "codex exec <prompt>",
                    "codex_research_shape": "codex --search exec -s read-only (native research, retained)",
                    "codex_prompt_audit_shape": "codex exec --json --output-schema prompt-audit.schema.json (native gpt-5.x, retained)",
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
            "max_sections_per_turn": 3,
            "instructions": [
                "Start with the first missing question in ask_order and keep that order.",
                "Show every option for each missing section with its short description.",
                "Ask all missing sections in one compact pass when the user asks to continue or wants the config completed.",
                "Use goal-config-state.json after create/check/discovery so the next step is deterministic.",
                "For preflight compatibility, always run check_goal_config.py --for-preflight before heavy model checks.",
                "For custom options, collect the requested exact harness/provider/model or numeric values before creating goal.config.json.",
                "Prefer smoke checks for normal validation; reserve debug mode for trace-analysis workflows.",
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
                "recommended_default": "reuse an existing checked profile when available; otherwise the opencode-deepseek-v4 bridge profile is the default",
                "options": [
                    {
                        "id": "reuse_checked",
                        "label": "Reuse checked config",
                        "description": "Use an existing goal.config.json plus its passing check report. Fastest when the profile was already validated.",
                        "needs": ["path to goal.config.json", "path to goal-config check or smoke report"],
                        "maps_to": ["--goal-config", "--goal-config-check"],
                    },
                    {
                        "id": "opencode_deepseek_v4",
                        "label": "Opencode DeepSeek v4 (default)",
                        "description": "Default profile. Route deepseek through the opencode-worker-bridge: ds-flash-max (deepseek-v4-flash --variant max) for Lite work and ds-pro-max (deepseek-v4-pro --variant max) for demanding/review work, with native Codex routes (codex-spark/codex-mini) kept as provider-diversity fallback, native codex --search research, and native gpt-5.x prompt-audit.",
                        "maps_to": [
                            "create_goal_config.py --preset opencode-deepseek-v4 --effort-profile PROFILE --validation-mode MODE --state-output /abs/goal-config-state.json",
                        ],
                    },
                    {
                        "id": "current_default",
                        "label": "Current default (bridge + native Codex)",
                        "description": "Use the bundled default route policy: bridge ds-flash-max/ds-pro-max plus native Codex Spark/mini fallback rungs, then validate the configured models.",
                        "maps_to": [
                            "create_goal_config.py --preset current-default --effort-profile PROFILE --validation-mode MODE --state-output /abs/goal-config-state.json",
                        ],
                    },
                    {
                        "id": "discover_available",
                        "label": "Discover all candidates",
                        "description": "Create or reuse a seed config, list configured candidate models from CLIs, smoke every candidate unless provider auth fails, then report accepted_routes, rejected_routes, skipped_routes, and unvisited_routes. Use this for discovery-path coverage checks.",
                        "needs": [
                            "optional provider/model filter",
                            "roles each accepted route may serve when auto mapping is not enough",
                        ],
                        "maps_to": [
                            "create_goal_config.py --preset current-default --output /abs/seed.goal.config.json --state-output /abs/goal-config-state.json",
                            "check_goal_config.py --config /abs/seed.goal.config.json --discover-profile mixed-fast --discover-all-candidates --smoke --stdout summary --output /abs/goal-config-discovery.json --state-output /abs/goal-config-state.json",
                            "create_goal_config.py --from-discovery /abs/goal-config-discovery.json --mapping auto --effort-profile PROFILE --validation-mode MODE --output /abs/goal.config.json --state-output /abs/goal-config-state.json",
                            "check_goal_config.py --config /abs/goal.config.json --require-models --smoke --reuse-smoke-report /abs/goal-config-discovery.json --output /abs/goal-config-smoke.json --state-output /abs/goal-config-state.json",
                            "accepted_routes",
                            "rejected_routes",
                            "skipped_routes",
                            "unvisited_routes",
                        ],
                    },
                    {
                        "id": "deepseek_bridge",
                        "label": "DeepSeek via opencode-worker-bridge",
                        "description": "Bind selected roles to the bridge deepseek routes ds-flash-max (deepseek-v4-flash) or ds-pro-max (deepseek-v4-pro), always launched with --variant max through opencode_worker.py.",
                        "needs": ["role-to-route choices among ds-flash-max / ds-pro-max"],
                        "maps_to": [
                            "--role-model ROLE:opencode-bridge:deepseek-v4-pro",
                            "--role-model ROLE:opencode-bridge:deepseek-v4-flash",
                        ],
                    },
                    {
                        "id": "native_codex",
                        "label": "Native Codex / gpt models",
                        "description": "Use the codex CLI for selected roles: codex-spark/codex-mini workers, codex --search read-only research, and gpt-5.5/gpt-5.4 prompt-audit. Ask for the exact route if the user has not supplied it.",
                        "needs": [
                            "role-to-model choices among codex-spark / codex-mini / gpt-5.5 / gpt-5.4 / codex-research"
                        ],
                        "maps_to": ["--role-model ROLE:codex:MODEL"],
                    },
                    {
                        "id": "custom_mixed",
                        "label": "Mixed/custom",
                        "description": "Use explicit role mappings across the opencode-bridge, codex, or generic-cli harnesses.",
                        "needs": ["role-model mapping for each changed role", "optional custom harness specs"],
                        "maps_to": [
                            "--role-model",
                            "--harness-spec",
                            "--lite-ladder",
                            "--worker-ladder",
                            "--reviewer-ladder",
                            "--amender-ladder",
                        ],
                    },
                ],
                "maps_to": [
                    "--preset",
                    "--role-model",
                    "--harness-spec",
                    "--lite-ladder",
                    "--worker-ladder",
                    "--reviewer-ladder",
                    "--amender-ladder",
                ],
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
                        "maps_to": ["create_goal_config.py --effort-profile lean"],
                        "values": {
                            "max_active_branch_agents": 2,
                            "max_active_worker_packets": 2,
                            "max_waves": 3,
                            "lite_timeout_seconds": 300,
                            "demanding_timeout_seconds": 900,
                        },
                    },
                    {
                        "id": "balanced",
                        "label": "Balanced",
                        "description": "Use default caps and timeouts. Good default when the user has no strong cost or latency preference.",
                        "maps_to": ["create_goal_config.py --effort-profile balanced"],
                        "values": {
                            "max_active_branch_agents": 4,
                            "max_active_worker_packets": 4,
                            "max_waves": 5,
                            "lite_timeout_seconds": 600,
                            "demanding_timeout_seconds": 1200,
                        },
                    },
                    {
                        "id": "thorough",
                        "label": "Thorough",
                        "description": "Use higher requested parallelism and longer timeouts for harder goals. If values exceed hard preflight caps, compatibility capping is recorded under compatibility.aggressiveness_adjustments.",
                        "maps_to": ["create_goal_config.py --effort-profile thorough"],
                        "values": {
                            "max_active_branch_agents": 6,
                            "max_active_worker_packets": 6,
                            "max_waves": 8,
                            "lite_timeout_seconds": 900,
                            "demanding_timeout_seconds": 2400,
                        },
                    },
                    {
                        "id": "custom",
                        "label": "Custom",
                        "description": "Ask for exact branch cap, worker cap, wave cap, Lite timeout, and demanding-agent timeout.",
                        "needs": [
                            "max active branches",
                            "max active worker packets",
                            "max waves",
                            "Lite timeout seconds",
                            "demanding timeout seconds",
                        ],
                        "maps_to": [
                            "--max-active-branch-agents",
                            "--max-active-worker-packets",
                            "--max-waves",
                            "--lite-timeout-seconds",
                            "--demanding-timeout-seconds",
                        ],
                    },
                ],
                "maps_to": [
                    "--effort-profile",
                    "--max-active-branch-agents",
                    "--max-active-worker-packets",
                    "--max-waves",
                    "--lite-timeout-seconds",
                    "--demanding-timeout-seconds",
                ],
            },
            {
                "id": "validation_mode",
                "order": 3,
                "title": "Validation and debug telemetry",
                "ask_when_missing": ["model check", "smoke requirement", "debug telemetry intent"],
                "explain_to_user": "This decides how hard the harness is tested before orchestration and whether preflight should request debug telemetry for trace analysis. Smoke is the normal path.",
                "question": "Choose the validation and telemetry mode.",
                "recommended_default": "smoke is the normal default validation; debug only for trace analysis",
                "options": [
                    {
                        "id": "model_check_only",
                        "label": "Model check only",
                        "description": "Verify configured models are available. Use this only for a recently checked profile or a quick config refresh.",
                        "maps_to": [
                            "create_goal_config.py --validation-mode model-check",
                            "check_goal_config.py --require-models --stdout summary --output /abs/goal-config-check.json --state-output /abs/goal-config-state.json",
                        ],
                    },
                    {
                        "id": "model_check_plus_smoke",
                        "label": "Model check plus smoke",
                        "description": "Lean verification: verify model availability and run assistant smoke tests for selected roles before preflight consumes the config.",
                        "maps_to": [
                            "create_goal_config.py --validation-mode smoke",
                            "check_goal_config.py --require-models --smoke --stdout summary --output /abs/goal-config-smoke.json --state-output /abs/goal-config-state.json",
                        ],
                    },
                    {
                        "id": "full_debug_trace",
                        "label": "Smoke plus debug telemetry",
                        "description": "Run model checks and smoke tests, then request debug telemetry in the preflight brief for trace analysis. This is heavier and should be chosen only when trace-level diagnosis is requested.",
                        "maps_to": [
                            "create_goal_config.py --validation-mode debug",
                            "check_goal_config.py --require-models --smoke --stdout summary --output /abs/goal-config-smoke.json --state-output /abs/goal-config-state.json",
                            "goal.config.json telemetry.mode=debug",
                        ],
                    },
                    {
                        "id": "custom_validation",
                        "label": "Custom validation",
                        "description": "Ask which roles need smoke tests and whether debug telemetry should be enabled for trace analysis.",
                        "needs": ["roles to smoke", "debug telemetry yes/no"],
                        "maps_to": ["check_goal_config.py --harness ROLE", "optional brief telemetry_mode=debug"],
                    },
                ],
                "maps_to": [
                    "check_goal_config.py --require-models",
                    "check_goal_config.py --smoke",
                    "brief telemetry_mode=debug",
                ],
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
