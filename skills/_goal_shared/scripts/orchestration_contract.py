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
SCHEDULER_SCHEMA_VERSION = 1
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

WORKER_ROLE = "worker"
RESEARCH_WORKER_TYPE = "research-worker"
WORK_ITEM_ROLES = (WORKER_ROLE, RESEARCH_WORKER_TYPE)

STATUSES = ("pass", "partial", "blocked", "failed")
REVIEW_STATUSES = ("mergeable", "mergeable_after_fixes", "blocked", "reject", "missing")

DEFAULT_WORKER_LADDER = (
    "gemini-pro",
    "gemini-flash",
    "codex-spark",
    "copilot-gpt-5.4",
    "codex-mini",
)
ALLOWED_WORKER_ROUTES = frozenset(DEFAULT_WORKER_LADDER)
RESEARCH_ALIASES = ("codex-research", "codex-research-mini")

WORKER_MODEL_POLICY = {
    "default_ladder": list(DEFAULT_WORKER_LADDER),
    "allowed_routes": list(DEFAULT_WORKER_LADDER),
    "branch_may_select_worker_route": True,
    "selection_reason_required": True,
    "ordering_rule": "Selected worker routes must be a non-empty ordered subsequence of default_ladder.",
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
    "input_hashes",
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
