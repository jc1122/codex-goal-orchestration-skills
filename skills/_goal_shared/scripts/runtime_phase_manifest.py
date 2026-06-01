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
                "id": "brief_lint",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_preflight_brief.py --brief /abs/brief.json --repo-root /abs/repo",
                "pass": "status=pass",
                "on_fail": "repair only reported defects",
            },
            {
                "id": "bundle",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/create_goal_bundle.py --brief /abs/brief.json --repo-root /abs/repo --out-dir /abs/bundle",
                "artifacts": ["job.manifest.json", "main.prompt.md", "branches/*.prompt.md", "goal-bootloader.md"],
            },
            {
                "id": "bundle_lint",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/lint_goal_bundle.py --bundle-dir /abs/bundle",
                "pass": "status=pass",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-preflight/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope preflight --json",
                "pass": "decision=needs_semantic_decision or direct script actions completed and gate rerun clean",
                "agent_does": "run deterministic script suggestions before any optional Lite or semantic repair pass",
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
                "pass": "decision=needs_semantic_decision before launching prompt audit or branch orchestrators",
                "agent_does": "complete script_action_available commands first; launch a model only after the gate returns needs_semantic_decision",
            },
            {
                "id": "prompt_audit",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/run_prompt_audit_phase.py --manifest /abs/bundle/job.manifest.json --repo-root /abs/repo --audit-dir /abs/bundle/audit --deterministic --require-pass",
                "pass": "prompt-audit.json status=pass and can_start=true",
                "on_fail": "do not create branches; read audit/prompt-audit-phase.json before event logs",
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
                "run": "python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope main --runtime-ref goal-main-orchestrator --init --record-ready --close-from-artifacts --validate-final && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/summarize_telemetry.py --bundle-dir /abs/bundle && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/assemble_main_status.py --manifest /abs/bundle/job.manifest.json --out /abs/bundle/main.status.json --replace && python3 $GOAL_SKILLS_ROOT/goal-main-orchestrator/scripts/validate_main_status.py --manifest /abs/bundle/job.manifest.json --status /abs/bundle/main.status.json",
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
                "id": "ready_workers",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/render_worker_schedule.py --manifest /abs/bundle/job.manifest.json --branch-id Bxx --list-ready --limit 4",
                "agent_does": "launch independent ready workers as a saturated pool up to max_active_worker_packets",
            },
            {
                "id": "script_repair_gate",
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/script_only_repair_gate.py --manifest /abs/bundle/job.manifest.json --bundle-dir /abs/bundle --repo-root /abs/repo --scope branch --branch-id Bxx --json",
                "pass": "decision=needs_semantic_decision before worker packet launch",
                "agent_does": "complete script_action_available commands first; launch workers only after the gate allows semantic work",
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
                "run": "python3 $GOAL_SKILLS_ROOT/goal-branch-orchestrator/scripts/scheduler_tick.py --manifest /abs/bundle/job.manifest.json --scope worker --branch-id Bxx --runtime-ref goal-branch-orchestrator --init --record-ready",
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
                "pass": "decision=needs_semantic_decision or accepted reviewer reuse with telemetry",
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
                "pass": "decision=needs_semantic_decision before semantic amender packet launch",
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
