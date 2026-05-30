#!/usr/bin/env python3
"""Shared constants and rendering helpers for goal orchestration scripts."""

from __future__ import annotations

import json


MAX_ACTIVE_BRANCH_AGENTS = 4
MAX_WORKER_PACKETS_PER_BRANCH = 4
MAX_WAVES = 5
DEFAULT_TOTAL_BRANCH_CAP = MAX_ACTIVE_BRANCH_AGENTS * MAX_WAVES
MAIN_SCHEDULER_PATH = "schedulers/main.scheduler.json"
WORKER_SCHEDULER_PATH_TEMPLATE = "schedulers/{branch_id}.worker.scheduler.json"
PRE_REVIEW_GATE_PATH_TEMPLATE = "branches/{branch_id}.pre_review_gate.json"
PRE_REVIEW_GATE_SCHEMA_VERSION = 2
SCHEDULER_SCHEMA_VERSION = 2
SCHEDULER_EVENTS = (
    "ready",
    "launch",
    "finish",
    "close",
    "refill",
    "defer",
    "under_capacity",
    "blocked",
)
SCHEDULER_TERMINAL_STATUSES = ("pass", "partial", "blocked", "failed")
SCHEDULER_REASON_CODES = (
    "artifact_invalid",
    "capacity_limit",
    "contention",
    "dependency_failed",
    "dependency_pending",
    "launcher_failed",
    "native_agent_unreachable",
    "no_ready_work",
    "operator_requested",
    "process_exited_blocked",
    "stale_active",
    "timeout",
)
SCHEDULER_REASON_REQUIRED_EVENTS = ("defer", "under_capacity", "blocked")

WORKER_ROLE = "worker"
RESEARCH_WORKER_TYPE = "research-worker"
WORK_ITEM_ROLES = (WORKER_ROLE, RESEARCH_WORKER_TYPE)

STATUSES = ("pass", "partial", "blocked", "failed")
REVIEW_STATUSES = ("mergeable", "mergeable_after_fixes", "blocked", "reject", "missing")
REVIEW_ROUTE_TIERS = ("light", "standard", "heavy")
AUDIT_ATTEMPT_TIMEOUT_SECONDS = 1200
WORKER_ATTEMPT_TIMEOUT_SECONDS = 3600
RESEARCH_ATTEMPT_TIMEOUT_SECONDS = 1200
REVIEWER_ATTEMPT_TIMEOUT_SECONDS = 1800
AMENDER_ATTEMPT_TIMEOUT_SECONDS = 1200
TIMEOUT_KILL_AFTER_SECONDS = 30
CODEX_ROUTE_MODELS = {
    "gpt-5.5": "gpt-5.5",
    "gpt-5.4": "gpt-5.4",
    "gpt-5.4-mini": "gpt-5.4-mini",
    "codex-spark": "gpt-5.3-codex-spark",
    "codex-mini": "gpt-5.4-mini",
    "codex-research": "gpt-5.4",
    "codex-research-mini": "gpt-5.4-mini",
}
CODEX_ROUTE_EVENT_LABELS = {
    "gpt-5.5": "gpt-5-5",
    "gpt-5.4": "gpt-5-4",
    "gpt-5.4-mini": "gpt-5-4-mini",
    "codex-spark": "spark",
    "codex-mini": "mini",
    "codex-research": "primary",
    "codex-research-mini": "fallback",
}
REVIEW_MODEL_ROUTES = {
    "light": ("gpt-5.4-mini", "gpt-5.4"),
    "standard": ("gpt-5.4", "gpt-5.5"),
    "heavy": ("gpt-5.5", "gpt-5.4"),
}
REVIEW_HEAVY_TRIGGER_PATTERNS = (
    "api",
    "public_api",
    "scheduler",
    "validator",
    "validation",
    "security",
    "auth",
    "credential",
    "migration",
    "schema",
    "scientific",
    "claim",
    "large-diff",
    "reviewer-blocker",
)
REVIEW_MODEL_POLICY = {
    "router": "deterministic-v1",
    "default_tier": "standard",
    "routes": {tier: list(route) for tier, route in REVIEW_MODEL_ROUTES.items()},
    "heavy_triggers": [
        "public API changes",
        "scheduler or validator contract changes",
        "security-sensitive changes",
        "data migration or schema migration work",
        "scientific or claim-boundary work",
        "large diffs",
        "prior reviewer blockers",
    ],
}
ORCHESTRATION_WATCHDOG = {
    "main_no_completion_wait_limit": 3,
    "branch_no_completion_wait_limit": 3,
    "after_limit": "inspect only native agent/process state, then close unreachable or stale work as structured blocked evidence and refill capacity",
}
ADAPTATION_ALLOWED_OPERATIONS = (
    "add_branch",
    "split_unstarted_branch",
    "replace_unstarted_branch",
    "add_dependency_to_unstarted_branch",
    "add_work_item_to_unstarted_branch",
    "mark_unstarted_branch_obsolete",
)
AMENDMENT_DECISIONS = ("launch", "skip")
AMENDMENT_DECISION_REASON_CODES = (
    "blocker_stalls_downstream",
    "eligible_work_remains",
    "finalization_still_plausible",
    "no_adaptation_needed",
    "no_eligible_branch",
    "operator_declined",
    "operator_requested",
    "recovery_plausible_before_finalization",
    "remaining_work_covers_dod",
    "remaining_work_dod_gap",
    "terminal_blocker_repair",
)
AMENDMENT_LAUNCH_REASON_CODES = (
    "blocker_stalls_downstream",
    "no_eligible_branch",
    "operator_requested",
    "recovery_plausible_before_finalization",
    "remaining_work_dod_gap",
    "terminal_blocker_repair",
)
ADAPTATION_POLICY = {
    "enabled": True,
    "mode": "amendment_proposals",
    "launcher": "goal-main-orchestrator",
    "may_modify_active_or_terminal_branches": False,
    "allowed_operations": list(ADAPTATION_ALLOWED_OPERATIONS),
}

DEFAULT_WORKER_LADDER = (
    "gemini-pro",
    "gemini-flash",
    "codex-spark",
    "copilot-gpt-5.4",
    "codex-mini",
)
ALLOWED_WORKER_ROUTES = frozenset(DEFAULT_WORKER_LADDER)
RESEARCH_ALIASES = ("codex-research", "codex-research-mini")
AMENDER_ROLE = "plan_amender"
DEFAULT_AMENDER_LADDER = (
    "gpt-5.4",
    "gpt-5.4-mini",
)
ALLOWED_AMENDER_ROUTES = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
)
DETERMINISTIC_AMENDER_ALIAS = "deterministic-blocker-repair"
ALLOWED_AMENDER_TELEMETRY_ALIASES = ALLOWED_AMENDER_ROUTES + (DETERMINISTIC_AMENDER_ALIAS,)

WORKER_MODEL_POLICY = {
    "default_ladder": list(DEFAULT_WORKER_LADDER),
    "allowed_routes": list(DEFAULT_WORKER_LADDER),
    "branch_may_select_worker_route": True,
    "selection_reason_required": True,
    "ordering_rule": "Selected worker routes must be a non-empty ordered subsequence of default_ladder.",
}
AMENDER_MODEL_POLICY = {
    "default_ladder": list(DEFAULT_AMENDER_LADDER),
    "allowed_routes": list(ALLOWED_AMENDER_ROUTES),
    "launcher": "goal-main-orchestrator",
    "selection_reason_required": True,
    "ordering_rule": "Selected amender routes must be a non-empty ordered subsequence of allowed_routes.",
    "sandbox": "read-only",
    "timeout_seconds": AMENDER_ATTEMPT_TIMEOUT_SECONDS,
}
RESEARCH_WORKER_POLICY = {
    "enabled": True,
    "worker_type": RESEARCH_WORKER_TYPE,
    "launcher": "codex --search exec --ephemeral -s read-only",
    "network_scope": "Broad read-only information retrieval is allowed through Codex native web search, configured CLI tools, MCP servers, connector tools, browser/search tools, package metadata lookups, remote APIs, and shell/network inspection commands. State-changing, destructive, credential, posting, purchasing, and file-editing actions are prohibited.",
    "local_access": "Read-only local file and command inspection for the assigned worktree, explicit context files, and configured tool or skill documentation when task-relevant; no writes, no secrets or unrelated private files.",
}
RESEARCH_POLICY_REJECTED_PHRASES = (
    "--ignore-user-config",
    "general web search only",
    "local file access only",
    "mcp/connector",
    "connector tools are unavailable",
    "shell-network tools prohibited",
)
RESEARCH_POLICY_REQUIRED_PHRASES = (
    "--search",
    "read-only",
    "broad read-only information retrieval",
    "configured",
    "mcp",
    "connector",
    "shell/network",
    "state-changing",
    "file-editing",
)

WORKER_STATUS_REQUIRED = (
    "packet_id",
    "role",
    "status",
    "branch",
    "worktree",
    "selected_ladder",
    "selection_reason",
    "changed_files",
    "commands_run",
    "tests",
    "blockers",
    "handoff",
)
WORKER_ROLLUP_REQUIRED = (
    "packet_id",
    "status",
    "status_path",
    "worktree",
    "selected_ladder",
    "selection_reason",
    "changed_files",
    "commands_run",
    "tests",
    "blockers",
    "handoff",
)
RESEARCH_STATUS_REQUIRED = (
    "packet_id",
    "role",
    "status",
    "branch",
    "worktree",
    "search_queries",
    "source_urls",
    "tools_used",
    "local_files_read",
    "commands_run",
    "findings",
    "blockers",
    "handoff",
)
RESEARCH_ROLLUP_REQUIRED = (
    "packet_id",
    "role",
    "status",
    "status_path",
    "worktree",
    "search_queries",
    "source_urls",
    "tools_used",
    "local_files_read",
    "commands_run",
    "findings",
    "blockers",
    "handoff",
)
REVIEW_REQUIRED = (
    "packet_id",
    "role",
    "verdict",
    "findings",
    "commands_run",
    "verification_gaps",
    "residual_risks",
    "semantic_input_hashes",
    "reuse_policy",
    "summary",
)
BRANCH_SUMMARY_REQUIRED = ("branch_id", "status", "status_path", "review_path", "review_status")
MAIN_STATUS_REQUIRED = (
    "job_id",
    "status",
    "audit_status",
    "branch_parallelism",
    "branch_statuses",
    "amendment_decisions",
    "lite_advice",
    "commands_run",
    "dod_checklist",
    "blockers",
    "summary",
)


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def worker_ladder_list() -> list[str]:
    return list(DEFAULT_WORKER_LADDER)


def worker_scheduler_path(branch_id: str) -> str:
    return WORKER_SCHEDULER_PATH_TEMPLATE.format(branch_id=branch_id)


def pre_review_gate_path(branch_id: str) -> str:
    return PRE_REVIEW_GATE_PATH_TEMPLATE.format(branch_id=branch_id)


def format_worker_ladder(values: list[str] | tuple[str, ...] = DEFAULT_WORKER_LADDER) -> str:
    return " -> ".join(values)


def review_route_for_tier(tier: str) -> list[str]:
    return list(REVIEW_MODEL_ROUTES.get(tier, REVIEW_MODEL_ROUTES["standard"]))


def normalize_route_ladder(
    values: list[str],
    *,
    default_ladder: tuple[str, ...],
    allowed_routes: tuple[str, ...] | frozenset[str],
    route_name: str,
) -> list[str]:
    if not values:
        return list(default_ladder)
    flattened = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                flattened.append(item)
    if not flattened:
        raise ValueError(f"{route_name} route must contain at least one route alias")
    allowed = tuple(allowed_routes)
    seen = set()
    positions = []
    for alias in flattened:
        if alias not in allowed_routes:
            raise ValueError(f"unsupported {route_name} route alias: {alias!r}")
        if alias in seen:
            raise ValueError(f"{route_name} route alias repeated: {alias!r}")
        seen.add(alias)
        positions.append(allowed.index(alias))
    if positions != sorted(positions):
        raise ValueError(
            f"{route_name} route aliases must preserve standard ladder order: "
            + ", ".join(allowed)
        )
    return flattened


def codex_model(alias: str) -> str:
    try:
        return CODEX_ROUTE_MODELS[alias]
    except KeyError as exc:
        raise ValueError(f"unsupported codex route alias: {alias!r}") from exc


def codex_event_label(alias: str) -> str:
    return CODEX_ROUTE_EVENT_LABELS.get(alias, alias.replace(".", "-"))


def codex_command(alias: str, *, sandbox: str, search: bool = False) -> str:
    prefix = "codex --search exec" if search else "codex exec"
    return f"{prefix} --ephemeral -m {codex_model(alias)} -s {sandbox}"


def codex_telemetry_attempts(
    selected_ladder: list[str],
    *,
    timeout_seconds: int,
    sandbox: str,
    event_labels: list[str] | None = None,
    search: bool = False,
) -> list[dict]:
    attempts = []
    for index, alias in enumerate(selected_ladder):
        label = event_labels[index] if event_labels and index < len(event_labels) else codex_event_label(alias)
        attempts.append(
            {
                "alias": alias,
                "provider": "codex",
                "model": codex_model(alias),
                "effort": "",
                "command": codex_command(alias, sandbox=sandbox, search=search),
                "timeout_seconds": timeout_seconds,
                "event_logs": [f"events-{label}.jsonl"],
                "probe_logs": [],
            }
        )
    return attempts


def research_policy_text(policy: dict) -> str:
    return " ".join(str(policy.get(key, "")) for key in ["launcher", "network_scope", "local_access"]).lower()


def research_policy_defects(policy: dict) -> tuple[list[str], list[str]]:
    text = research_policy_text(policy)
    rejected = [phrase for phrase in RESEARCH_POLICY_REJECTED_PHRASES if phrase in text]
    missing = [phrase for phrase in RESEARCH_POLICY_REQUIRED_PHRASES if phrase not in text]
    return rejected, missing


def telemetry_attempt_args(attempts: list[dict]) -> str:
    lines = []
    for item in attempts:
        lines.append("    --attempt-json " + shell_quote(json.dumps(item, separators=(",", ":"))) + " \\")
    if lines:
        lines[-1] = lines[-1].removesuffix(" \\")
    return "\n".join(lines)


def telemetry_shell_function(
    *,
    script_path: str,
    packet_dir_expr: str,
    packet_id: str,
    role: str,
    output_name: str,
    prompt_name: str,
    attempts: list[dict],
) -> str:
    return f"""write_telemetry() {{
  python3 {shell_quote(script_path)} \\
    --packet-dir "{packet_dir_expr}" \\
    --packet-id {shell_quote(packet_id)} \\
    --role {shell_quote(role)} \\
    --output-name {shell_quote(output_name)} \\
    --prompt-name {shell_quote(prompt_name)} \\
{telemetry_attempt_args(attempts)}
}}
"""
