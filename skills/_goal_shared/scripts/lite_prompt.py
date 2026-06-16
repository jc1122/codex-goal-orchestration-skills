#!/usr/bin/env python3
"""Single source of truth for the Lite advisory prompt template.

`create_lite_advice_packet.py` writes `prompt.md` from `build_lite_prompt` and
records its sha256 in `input-files.json`. `validate_lite_advice.py` regenerates
the prompt from the recorded envelope with the SAME builder and compares hashes
to prove the packet is deterministic. Both modules MUST import this builder so
the two cannot drift (the previous physical duplication silently broke that
determinism guarantee when the copies diverged).

The Lite route delegates a deepseek launch through the opencode-worker-bridge
control script under permission-profile `read-only`; the builder records that
bridge/deepseek invocation envelope (control-script path/version, provider,
model, variant) rather than a local CLI binary path/sha.
"""

from __future__ import annotations

import json


LITE_STATUS_BEGIN = "BEGIN_LITE_ADVICE_JSON"
LITE_STATUS_END = "END_LITE_ADVICE_JSON"


def bridge_advice_command(
    *,
    control_script: str,
    provider: str,
    model: str,
    variant: str,
    permission_profile: str,
) -> str:
    """Render the deterministic human-readable bridge delegate command line.

    Mirrors the runner's actual `opencode_worker.py delegate` invocation. No USD
    or price fields are involved; this is the route descriptor recorded in the
    prompt envelope and echoed in advice `commands_run`.
    """
    control = control_script if control_script else "opencode_worker.py"
    return (
        f"python3 {control} delegate "
        f"--provider {provider} --model {model} --variant {variant} "
        f"--permission-profile {permission_profile}"
    )


def build_lite_prompt(
    packet_id: str,
    purpose: str,
    base_dir: str,
    sources: list[dict],
    extra: str,
    *,
    skill: str,
    model: str,
    provider: str,
    variant: str,
    control_script: str,
    control_version: str,
    permission_profile: str,
    task_sha256: str,
    avoids_action: str,
    expected_savings_reason: str,
) -> str:
    """Render the Lite advisory prompt. The only template; never duplicate it.

    `base_dir` is accepted as a string so create (Path) and validate (str) feed
    identical text into the f-string (a prior cosmetic Path-vs-str drift between
    the two copies is exactly the class of bug this consolidation prevents).
    """
    base_dir = str(base_dir)
    source_lines = "\n".join(f"- {item['path']} ({item['sha256']}, {item['size_bytes']} bytes)" for item in sources)
    example_sources = json.dumps(sources, indent=2, sort_keys=True)
    command = bridge_advice_command(
        control_script=control_script,
        provider=provider,
        model=model,
        variant=variant,
        permission_profile=permission_profile,
    )
    return f"""# Lite Advisory Packet {packet_id}

You are a CLI-only Lite advisor. Do not edit files, create branches, create worktrees, run tests, or decide pass/fail. Your job is to route context cheaply for heavier agents.

Purpose: {purpose}
Avoids action: {avoids_action}
Expected savings reason: {expected_savings_reason}
Base directory: {base_dir}

Deterministic envelope:
- Skill: {skill}
- Provider: {provider}
- Model: {model}
- Variant: {variant}
- Permission profile: {permission_profile}
- Bridge control script: {control_script if control_script else "unavailable"}
- Bridge control version: {control_version}
- Task guidance sha256: {task_sha256}

Read only these explicit input files:
{source_lines if source_lines else "- none"}

Policy:
- Lite output is advisory only.
- If you cannot actually reduce the declared avoided action, return `status: "blocked"` and explain why in blockers.
- Do not decide mergeability, prompt-audit pass/fail, scientific claim support, or Definition-of-Done satisfaction.
- Preserve labels exactly when present: `unsupported`, `unresolved`, `negative`, `weakened`, `probe-only`, `blocked`.
- Recommend targeted original reads with path, anchor, and reason. Do not tell heavy agents to reread every source file by default.
- For any purpose other than `preflight-decomposition`, `recommended_reads` may cite only the explicit input files listed above.
- Use focused context. Do not broaden beyond the listed files unless the purpose is `preflight-decomposition`; even then, only recommend additional paths rather than reading the whole repository.
- If an input file is missing, unreadable, stale, or insufficient, return `status: "blocked"` or `status: "partial"` with blockers.

Additional task guidance:
{extra.strip() if extra.strip() else "- No extra guidance."}

Return exactly one JSON object between these marker lines. Do not print any other JSON object between them. The `source_files` array must echo this exact metadata for every listed input file:

{LITE_STATUS_BEGIN}
{{
  "packet_id": "{packet_id}",
  "role": "lite_advisor",
  "purpose": "{purpose}",
  "avoids_action": {json.dumps(avoids_action)},
  "expected_savings_reason": {json.dumps(expected_savings_reason)},
  "status": "ok",
  "source_files": {example_sources},
  "recommended_reads": [],
  "risk_flags": [],
  "advice": {{}},
  "summary": "replace with concise advisory summary",
  "blockers": [],
  "commands_run": [{json.dumps(command)}]
}}
{LITE_STATUS_END}
"""
