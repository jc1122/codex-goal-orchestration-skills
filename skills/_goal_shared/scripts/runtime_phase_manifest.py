#!/usr/bin/env python3
"""Print compact runtime phase manifests for goal orchestration skills."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def current_skill_name() -> str:
    override = globals().get("SKILL_NAME_OVERRIDE")
    if isinstance(override, str) and override:
        return override
    return Path(__file__).resolve().parents[1].name


COMMON_RULES = [
    "Use script outputs, JSON artifacts, and validator defects before opening prose references.",
    "Do not read or search skills/*/scripts/*.py with cat/sed/head/rg/grep during runtime unless a script failed and script debugging is assigned.",
    "Record exact commands run and preserve blocked/partial evidence.",
    "Parallelism is default; under-capacity requires structured scheduler evidence.",
]


PHASES: dict[str, dict[str, Any]] = {
    "goal-config": {
        "role": "Configure and verify model/provider profiles; do not launch goal runtime work.",
        "first_artifacts": ["user harness/provider/model preference", "optional existing goal.config.json"],
        "phases": [
            {
                "id": "scan",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/scan_configurables.py --json > /abs/goal-config-inventory.json",
                "agent_does": "inspect the inventory categories instead of broad source scans before proposing knob changes",
            },
            {
                "id": "preference_intake",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/scan_configurables.py --questions-json > /abs/goal-config-questions.json",
                "agent_does": "if preferences are missing, follow goal-config-questions.json interaction.ask_order; ask missing sections in order with all listed options and short descriptions; when the user says continue or wants completion, ask/apply all remaining missing sections in one compact pass",
                "pass": "preferences are captured, an existing checked profile is selected, or the user explicitly says to use defaults",
                "on_fail": "do not create goal.config.json silently from defaults",
            },
            {
                "id": "route_discovery",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py --config /abs/seed.goal.config.json --discover-profile mixed-fast --discover-all-candidates --smoke --stdout summary --output /abs/goal-config-discovery.json --state-output /abs/goal-config-state.json",
                "agent_does": "only when the user chooses discovery/use-all-available; inspect the summary first, then candidate_routes, checked_roles, accepted_routes, rejected_routes, skipped_routes, unvisited_routes, and goal-config-state.json; create a final explicit goal.config.json from accepted routes with --from-discovery",
                "pass": "at least one accepted route and every rejected route has reasons",
                "on_fail": "do not pass unreviewed discovered routes to preflight",
            },
            {
                "id": "create",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/create_goal_config.py --preset opencode-deepseek-v4 --effort-profile balanced --validation-mode smoke --role-model lite_agent:opencode:provider/model --role-model demanding_agent:opencode:provider/model --harness-spec /abs/custom-harness.json --from-discovery /abs/goal-config-discovery.json --mapping auto --output /abs/goal.config.json --state-output /abs/goal-config-state.json",
                "artifacts": ["goal.config.json"],
                "agent_does": "translate captured preferences into explicit flags; omit --role-model or --harness-spec entries that the user did not request; keep user-supplied harness, provider, and model strings explicit; verify requested caps/timeouts/ladders are rendered and every role has harness_smokes",
            },
            {
                "id": "preflight_compatibility",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py --config /abs/goal.config.json --for-preflight --stdout summary --output /abs/goal-config-preflight.json --state-output /abs/goal-config-state.json",
                "pass": "status=pass with config_validation_mode and check_mode recorded",
                "on_fail": "repair cap, telemetry, validation-mode, or preflight schema defects before model availability checks",
            },
            {
                "id": "model_check",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py --config /abs/goal.config.json --require-models --stdout summary --output /abs/goal-config-check.json --state-output /abs/goal-config-state.json",
                "pass": "status=pass and every selected provider/model is listed by its harness",
                "on_fail": "return blocked with goal-config-check.json failures",
            },
            {
                "id": "harness_smoke",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-config/scripts/check_goal_config.py --config /abs/goal.config.json --require-models --smoke --harness lite --harness demanding --stdout summary --output /abs/goal-config-smoke.json --state-output /abs/goal-config-state.json",
                "pass": "status=pass with assistant smoke text, token counts, character counts, and elapsed milliseconds for each role",
                "on_fail": "return blocked with checker failures and opencode status/message fields; do not silently fall back to another provider/model",
            },
            {
                "id": "preflight_integration",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py --brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle --goal-config /abs/goal.config.json --goal-config-check /abs/goal-config-check.json",
                "pass": "bundle manifest references hashed goal_config artifacts, embeds derived model_policies, and lint_goal_bundle.py passes",
                "agent_does": "use the smoke report as --goal-config-check when the user requested smoke-validated harnesses",
            },
        ],
        "details": [
            "references/configuration-contract.md only when the config schema or checker report needs interpretation",
        ],
    },
    "goal-preflight": {
        "role": "Prepare a bundle only; do not launch runtime agents.",
        "first_artifacts": ["source brief/report", "repo root"],
        "phases": [
            {
                "id": "bootstrap",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/check_goal_skill_availability.py --skills-root $GOAL_SKILLS_ROOT --require goal-preflight --require goal-main-orchestrator --require goal-branch-orchestrator --require goal-plan-amender",
                "pass": "status=pass",
                "on_fail": "stop before writing prompts",
            },
            {
                "id": "brief",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py --brief-schema-json && python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py --example-brief",
                "agent_does": "write one structured brief JSON with concrete branches/work_items/DoD; prefer 3-4 independent branches when safe; if the user asks for debug mode, set telemetry_mode=debug",
                "avoid": "do not inspect Python source or runtime contracts for brief shape; use --brief-schema-json, --example-brief, and lint defects",
            },
            {
                "id": "guided_pipeline",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/prepare_goal_bundle.py --brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle --json",
                "artifacts": [
                    "preflight.brief.lint.json",
                    "goal-config-selection.json",
                    "job.manifest.json",
                    "preflight.lint.json",
                    "repair-gate.json",
                    "readiness.json",
                    "goal-bootloader.md",
                    "preflight.pipeline.json",
                ],
                "agent_does": "prefer this one-shot path for normal preflight; it auto-detects candidate configs, remediates preflight caps/telemetry when possible, persists canonical artifacts, and blocks non-git runtime handoff",
                "pass": "pipeline status=pass and readiness status=pass",
                "on_fail": "inspect the pipeline JSON, canonical lint/check artifacts, and readiness runtime_gate before running individual stages",
            },
            {
                "id": "brief_lint",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_preflight_brief.py --brief /abs/brief.json --repo-root /abs/repo --json --output /abs/bundle/preflight.brief.lint.json",
                "pass": "status=pass",
                "on_fail": "manual fallback only; repair only reported defects",
            },
            {
                "id": "bundle",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py --brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle",
                "artifacts": ["job.manifest.json", "main.prompt.md", "branches/*.prompt.md", "goal-bootloader.md"],
                "agent_does": "manual fallback only when guided_pipeline needs stage isolation",
            },
            {
                "id": "bundle_lint",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_goal_bundle.py --bundle-dir /abs/bundle --json --output /abs/bundle/preflight.lint.json",
                "pass": "status=pass",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/_goal_shared/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope preflight --json --output /abs/bundle/repair-gate.json",
                "pass": "decision=pass_no_actions or direct script actions completed and gate rerun clean",
                "agent_does": "run deterministic script suggestions before any optional Lite or semantic repair pass; rerun until decision=pass_no_actions",
            },
            {
                "id": "readiness",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir /abs/bundle --readiness --json --output /abs/bundle/readiness.json",
                "agent_does": "capture compact readiness snapshot: config compatibility, bundle lint, cap settings, route policy, telemetry mode, branch DAG, git status, repair gate, runtime gate, and next command",
                "pass": "readiness status=pass",
                "on_fail": "do not continue into /goal if readiness is blocked or lint/report defects remain",
            },
            {
                "id": "handoff",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir /abs/bundle",
                "agent_does": "return the exact bootloader text",
            },
        ],
        "details": [
            "references/actionability-rubric.md only for vague source material",
            "references/bundle-contract.md only when bundle lint reports schema defects",
            "share only compact readiness output and next command for handoff when user asks for compact prompt mode",
        ],
    },
    "goal-main-orchestrator": {
        "role": "Run a prepared audited bundle; do not implement branch work.",
        "first_artifacts": ["job.manifest.json", "main.prompt.md", "goal-bootloader roots"],
        "phases": [
            {
                "id": "bootstrap",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_goal_skill_availability.py --skills-root $GOAL_SKILLS_ROOT --require goal-main-orchestrator --require goal-branch-orchestrator --require goal-plan-amender --require-codex-cli",
                "pass": "status=pass",
                "on_fail": "return blocked",
            },
            {
                "id": "model_catalog",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/check_model_catalog.py --json --require-codex > /abs/bundle/model-catalog.json",
                "pass": "status pass/source live preferred",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope main --json",
                "pass": "decision=pass_no_actions before launching prompt audit or branch orchestrators",
                "agent_does": "complete script_actions_needed commands first; launch a model only after the gate returns pass_no_actions",
            },
            {
                "id": "prompt_audit",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/run_prompt_audit_phase.py --manifest /abs/bundle/job.manifest.json --repo-root /abs/repo --audit-dir /abs/bundle/audit --deterministic --require-pass",
                "pass": "prompt-audit.json status=pass and can_start=true",
                "on_fail": "do not create branches; read audit/prompt-audit-phase.json before event logs",
            },
            {
                "id": "resume_reconcile",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/reconcile_goal_run.py --manifest /abs/bundle/job.manifest.json --repo-root /abs/repo --write",
                "agent_does": "inspect resume.report.json before launching or relaunching branches; reuse only validated terminal artifacts and follow listed next_commands for stale or missing evidence",
                "pass": "orchestration.state.json and resume.report.json exist with explicit safe_to_reuse and next_commands",
            },
            {
                "id": "branch_schedule",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/render_branch_worktree_commands.py --manifest /abs/bundle/job.manifest.json --repo-root /abs/repo --audit /abs/bundle/audit/prompt-audit.json --list-ready --limit 4",
                "agent_does": "launch eligible branch orchestrators as a saturated pool up to manifest cap",
            },
            {
                "id": "scheduler",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope main --runtime-ref goal-main-orchestrator --init --record-ready",
                "agent_does": "record launch/finish/close/refill/defer/blocked events as branches complete",
            },
            {
                "id": "watchdog",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope main --runtime-ref goal-main-orchestrator --init --record-ready",
                "agent_does": "after orchestration_watchdog.main_no_completion_wait_limit consecutive no-completion waits, inspect only native agent/process state; close unreachable or stale active branches with scheduler_tick.py --blocked/--close and --reason-code stale_active|native_agent_unreachable|timeout, then refill eligible capacity",
                "pass": "active work completes normally or has terminal scheduler evidence before replacement launch",
            },
            {
                "id": "validate_collect",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/validate_branch_status.py --manifest /abs/bundle/job.manifest.json --status /abs/bundle/branches/Bxx.status.json",
                "agent_does": "accept only validated terminal branch artifacts",
            },
            {
                "id": "finalize",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope main --runtime-ref goal-main-orchestrator --init --record-ready --close-from-artifacts --validate-final && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py --bundle-dir /abs/bundle && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/assemble_main_status.py --manifest /abs/bundle/job.manifest.json --out /abs/bundle/main.status.json --replace && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/validate_main_status.py --manifest /abs/bundle/job.manifest.json --status /abs/bundle/main.status.json && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/reconcile_goal_run.py --manifest /abs/bundle/job.manifest.json --write",
                "pass": "main.status.json validates and DoD evidence is complete",
            },
        ],
        "details": [
            "references/prompt-audit-contract.md only when audit schema/meaning is unclear",
            "references/main-runtime-contract.md only when validate_main_status defects need interpretation",
        ],
    },
    "goal-branch-orchestrator": {
        "role": "Run one audited branch worktree; delegate work to worker packets.",
        "first_artifacts": ["branch prompt", "job.manifest.json", "prompt-audit.json", "branch worktree"],
        "phases": [
            {
                "id": "bootstrap",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/check_goal_skill_availability.py --skills-root $GOAL_SKILLS_ROOT --require goal-branch-orchestrator --require-codex-cli && python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/check_model_catalog.py --json --require-codex > /abs/bundle/branches/Bxx.model-catalog.json",
                "pass": "skill availability and model-catalog.json status=pass/source live preferred",
            },
            {
                "id": "scheduler_init",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope worker --branch-id Bxx --runtime-ref goal-branch-orchestrator --init --record-ready",
                "pass": "worker scheduler ledger exists and records initially ready work before branch script gate",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope branch --branch-id Bxx --json",
                "pass": "decision=pass_no_actions before worker packet launch",
                "agent_does": "complete script_actions_needed commands first; launch workers only after the gate allows semantic work",
            },
            {
                "id": "ready_workers",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/render_worker_schedule.py --manifest /abs/bundle/job.manifest.json --branch-id Bxx --list-ready --limit 4",
                "agent_does": "after scheduler_init and script_repair_gate pass, launch independent ready workers as a saturated pool up to max_active_worker_packets",
            },
            {
                "id": "context_pack",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/context_pack.py --worktree /abs/worktree --context-file /abs/context --markdown --output /abs/bundle/branches/Bxx.context-pack.md",
                "agent_does": "use the written bounded context pack; default is path-only for worktree files, add --include-worktree-excerpts only when bounded source excerpts are needed",
            },
            {
                "id": "worker_packets",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py --role worker --packet-id Bxx-Wyy --branch Bxx --worktree /abs/worker-worktree --out-dir /abs/bundle/workers --manifest /abs/bundle/job.manifest.json --model-catalog /abs/bundle/branches/Bxx.model-catalog.json --task-file /abs/bundle/branches/Bxx.prompt.md --owned-file repo/path --context-file /abs/context --selection-reason 'bounded route choice'",
                "agent_does": "run packet launch.sh; do not inspect active logs while running; include worktree context excerpts only when a bounded source excerpt is required",
            },
            {
                "id": "watchdog",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope worker --branch-id Bxx --runtime-ref goal-branch-orchestrator --record-ready",
                "agent_does": "after orchestration_watchdog.branch_no_completion_wait_limit consecutive no-completion waits, inspect only native agent/process state; close unreachable or stale active worker/reviewer packets with scheduler_tick.py --blocked/--close and --reason-code stale_active|native_agent_unreachable|timeout, then refill eligible capacity",
                "pass": "active worker/reviewer work completes normally or has terminal scheduler evidence before replacement launch",
            },
            {
                "id": "integrate_workers",
                "agent_does": "after launcher exit, inspect status/diff/tests; let scheduler_tick record launch/finish/close/refill from artifacts",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope worker --branch-id Bxx --runtime-ref goal-branch-orchestrator --init --record-ready --close-from-artifacts --validate-final",
            },
            {
                "id": "assemble_for_review",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/assemble_branch_status.py --manifest /abs/bundle/job.manifest.json --branch-id Bxx --worktree /abs/branch-worktree --replace",
                "pass": "branch status validates as partial/pass with integrated worker evidence",
            },
            {
                "id": "pre_review_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_pre_review_gate.py --manifest /abs/bundle/job.manifest.json --branch-id Bxx --worktree /abs/branch-worktree",
                "pass": "pre_review_gate.json status=pass",
            },
            {
                "id": "pre_reviewer_script_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope branch --branch-id Bxx --status /abs/bundle/branches/Bxx.status.json --json",
                "pass": "decision=pass_no_actions or accepted reviewer reuse with telemetry",
                "agent_does": "prefer deterministic repair/reuse evidence over reviewer model launch",
            },
            {
                "id": "reviewer",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/create_runtime_packet.py --role reviewer --packet-id Bxx-R01 --branch Bxx --worktree /abs/branch-worktree --manifest /abs/bundle/job.manifest.json --pre-review-gate /abs/gate --out-dir /abs/bundle/reviewers",
                "agent_does": "run read-only reviewer packet only after gate passes",
            },
            {
                "id": "assemble_validate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/assemble_branch_status.py --manifest /abs/bundle/job.manifest.json --branch-id Bxx --worktree /abs/branch-worktree --allow-pass --replace && python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/validate_branch_status.py --manifest /abs/bundle/job.manifest.json --status /abs/bundle/branches/Bxx.status.json",
                "pass": "validated branch status",
            },
        ],
        "details": [
            "references/branch-runtime-contract.md only when validate_branch_status defects need interpretation",
        ],
    },
    "goal-plan-amender": {
        "role": "Create and validate future-work-only manifest amendments.",
        "first_artifacts": ["terminal branch status", "job.manifest.json"],
        "phases": [
            {
                "id": "decision",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-plan-amender/scripts/recommend_amendment_decision.py --manifest /abs/bundle/job.manifest.json --amendment-id A001",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-plan-amender/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --scope amender --status /abs/bundle/branches/Bxx.status.json --json",
                "pass": "decision=pass_no_actions before semantic amender packet launch",
                "agent_does": "use amendment decision and blocker repair commands before launching an amender model",
            },
            {
                "id": "packet",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-plan-amender/scripts/create_adaptation_packet.py --manifest /abs/bundle/job.manifest.json --amendment-id A001 --amender-route gpt-5.4 --selection-reason 'bounded recovery planning'",
            },
            {
                "id": "validate_apply",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-plan-amender/scripts/validate_amender_packet.py --manifest /abs/bundle/job.manifest.json --amendment-id A001 && python3 $GOAL_SKILLS_ROOT/goal-plan-amender/scripts/validate_manifest_amendment.py --manifest /abs/bundle/job.manifest.json --proposal /abs/proposal.json",
            },
        ],
        "details": ["references/amendment-contract.md only when amendment validation defects need interpretation"],
    },
}


def manifest_for(skill: str) -> dict[str, Any]:
    if skill not in PHASES:
        raise SystemExit(f"unsupported goal skill for runtime phase manifest: {skill}")
    return {
        "schema_version": 1,
        "skill": skill,
        "token_rules": COMMON_RULES,
        **PHASES[skill],
    }


def markdown(data: dict[str, Any], *, compact: bool = False) -> str:
    if compact:
        lines = [
            f"# {data['skill']} phases",
            data["role"],
            "Rules: " + "; ".join(data["token_rules"]),
            "Read first: " + ", ".join(data["first_artifacts"]),
            "Phases:",
        ]
        for phase in data["phases"]:
            parts = [str(phase["id"])]
            for key in ["run", "agent_does", "artifacts", "pass", "on_fail", "avoid"]:
                if key not in phase:
                    continue
                value = phase[key]
                if isinstance(value, list):
                    value = ", ".join(str(item) for item in value)
                parts.append(f"{key}={value}")
            lines.append("- " + " | ".join(parts))
        if data.get("details"):
            lines.append("Details on demand: " + "; ".join(str(item) for item in data["details"]))
        return "\n".join(lines) + "\n"

    lines = [
        f"# Runtime Phase Manifest: {data['skill']}",
        "",
        data["role"],
        "",
        "Token rules:",
    ]
    lines.extend(f"- {item}" for item in data["token_rules"])
    lines.extend(["", "Read first:"])
    lines.extend(f"- {item}" for item in data["first_artifacts"])
    lines.extend(["", "Phases:"])
    for phase in data["phases"]:
        lines.append(f"- {phase['id']}")
        for key in ["run", "agent_does", "artifacts", "pass", "on_fail", "avoid"]:
            if key not in phase:
                continue
            value = phase[key]
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            lines.append(f"  {key}: {value}")
    if data.get("details"):
        lines.extend(["", "Open detailed references only when needed:"])
        lines.extend(f"- {item}" for item in data["details"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", default=current_skill_name(), help="Skill name; defaults to current installed skill wrapper.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Accepted for agent ergonomics; phase manifests are compact by default.",
    )
    args = parser.parse_args()

    if bool(args.json) == bool(args.markdown):
        raise SystemExit("choose exactly one of --json or --markdown")
    data = manifest_for(args.skill)
    if args.json:
        if args.compact:
            print(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n", end="")
        else:
            print(json.dumps(data, indent=2, sort_keys=True) + "\n", end="")
    else:
        print(markdown(data, compact=args.compact), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
