#!/usr/bin/env python3
"""Create a CLI-only Lite advisory packet for goal orchestration."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


BRANCH_LITE_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*-L[A-Za-z0-9_.-]+$")
LITE_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_COMMAND = "gemini"
GEMINI_APPROVAL_MODE = "plan"
LITE_ATTEMPT_TIMEOUT_SECONDS = 600
TIMEOUT_KILL_AFTER_SECONDS = 30
LITE_STATUS_BEGIN = "BEGIN_LITE_ADVICE_JSON"
LITE_STATUS_END = "END_LITE_ADVICE_JSON"
SKILL_NAME_OVERRIDE: str | None = None
SCRIPT_DIR_OVERRIDE: Path | None = None
SKILL_PURPOSES = {
    "goal-preflight": {"preflight-decomposition", "lint-repair"},
    "goal-main-orchestrator": {"audit-defect-summary", "main-summary"},
    "goal-branch-orchestrator": {
        "branch-packet-planning",
        "context-pack",
        "worker-summary",
        "blocked-triage",
    },
    "goal-plan-amender": {
        "amendment-summary",
        "amendment-defect-summary",
    },
}


def _load_contract():
    path = Path(__file__).resolve().parent / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_path_rules():
    path = Path(__file__).resolve().parent / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
PATH_RULES = _load_path_rules()
require_safe_label = PATH_RULES.require_safe_packet_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
repo_relative_path = PATH_RULES.repo_relative_path
shell_quote = CONTRACT.shell_quote


def current_skill_name() -> str:
    if SKILL_NAME_OVERRIDE is not None:
        return SKILL_NAME_OVERRIDE
    try:
        return Path(__file__).resolve().parents[1].name
    except IndexError:
        return ""


def current_script_dir() -> Path:
    if SCRIPT_DIR_OVERRIDE is not None:
        return SCRIPT_DIR_OVERRIDE
    return Path(__file__).resolve().parent


def allowed_purposes() -> set[str]:
    skill = current_skill_name()
    if skill not in SKILL_PURPOSES:
        raise SystemExit("Lite advice scripts must be run through a goal skill wrapper, not _goal_shared directly.")
    return SKILL_PURPOSES[skill]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_gemini() -> tuple[str, str, str]:
    executable = shutil.which(GEMINI_COMMAND)
    if executable is None:
        return "", "unavailable", "unavailable"
    path = Path(executable).resolve()
    try:
        gemini_sha256 = sha256_file(path)
    except Exception as exc:  # noqa: BLE001
        gemini_sha256 = f"sha256-unavailable: {exc}"
    try:
        completed = subprocess.run(
            [path.as_posix(), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        return path.as_posix(), f"version-unavailable: {exc}", gemini_sha256
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return path.as_posix(), version[0] if version else "version-unavailable", gemini_sha256


def source_metadata(path: Path, base_dir: Path) -> dict:
    return {
        "path": repo_relative_path(path, base_dir, "--input-file"),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "reason": "explicit Lite input",
    }


def advice_command(gemini_path: str) -> str:
    command = gemini_path if gemini_path else GEMINI_COMMAND
    return f"{command} --model {LITE_MODEL} --approval-mode {GEMINI_APPROVAL_MODE} --skip-trust --output-format text"


def lite_telemetry_attempts(gemini_path: str) -> list[dict]:
    return [
        {
            "alias": "gemini-lite",
            "provider": "gemini",
            "model": LITE_MODEL,
            "effort": "",
            "command": advice_command(gemini_path),
            "timeout_seconds": LITE_ATTEMPT_TIMEOUT_SECONDS,
            "event_logs": ["advice.raw.txt"],
            "probe_logs": [],
        }
    ]


def runtime_runner_path() -> Path:
    return current_script_dir() / "runtime_lite_runner.py"


def compact_launch_script() -> str:
    runner = runtime_runner_path()
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
runner={shell_quote(runner.as_posix())}
if [[ ! -f "$runner" ]]; then
  echo "Lite runtime runner missing: $runner" >&2
  exit 127
fi
exec python3 "$runner" --packet-dir "$(pwd)"
"""


def lite_usefulness(purpose: str, avoids_action: str | None, expected_savings_reason: str | None) -> tuple[str, str]:
    defaults = CONTRACT.lite_avoided_action_defaults(purpose)
    action = (avoids_action or defaults.get("avoids_action") or "").strip()
    reason = (expected_savings_reason or defaults.get("expected_savings_reason") or "").strip()
    if not action:
        raise SystemExit("--avoids-action is required for this Lite purpose")
    if not reason:
        raise SystemExit("--expected-savings-reason is required for this Lite purpose")
    return action, reason


def lite_launch_config(packet_id: str, purpose: str, base_dir: Path, gemini_path: str, *, avoids_action: str, expected_savings_reason: str) -> dict:
    return {
        "schema_version": 1,
        "role": "lite_advisor",
        "packet_id": packet_id,
        "purpose": purpose,
        "avoids_action": avoids_action,
        "expected_savings_reason": expected_savings_reason,
        "base_dir": base_dir.as_posix(),
        "model": LITE_MODEL,
        "approval_mode": GEMINI_APPROVAL_MODE,
        "attempt_timeout_seconds": LITE_ATTEMPT_TIMEOUT_SECONDS,
        "timeout_kill_after_seconds": TIMEOUT_KILL_AFTER_SECONDS,
        "inputs_name": "input-files.json",
        "prompt_name": "prompt.md",
        "task_name": "task.md",
        "output_name": "advice.json",
        "raw_name": "advice.raw.txt",
        "telemetry_name": "telemetry.json",
        "status_begin": LITE_STATUS_BEGIN,
        "status_end": LITE_STATUS_END,
        "runner_prompt": "Follow the complete Lite advisory packet instructions provided on stdin.",
        "validation_script": (current_script_dir() / "validate_lite_advice.py").as_posix(),
        "telemetry_script": (current_script_dir() / "extract_telemetry.py").as_posix(),
        "attempts": lite_telemetry_attempts(gemini_path),
        "terminal_messages": {
            "gemini_unavailable": f"Gemini CLI command unavailable at packet creation path: {gemini_path}",
            "inputs_stale": "Lite advisor input files changed or became unavailable after packet creation.",
            "prompt_stale": "Lite advisor prompt.md changed or became unavailable after packet creation.",
            "task_stale": "Lite advisor task.md changed or became unavailable after packet creation.",
            "gemini_stale": "Gemini CLI binary or version changed or could not be verified after packet creation.",
            "command_failed": "Lite advisor command failed. Inspect advice.raw.txt for CLI, quota, auth, or model errors.",
            "invalid_output": "Lite advisor did not produce valid advice JSON.",
        },
    }


def prompt_for(
    packet_id: str,
    purpose: str,
    base_dir: Path,
    sources: list[dict],
    extra: str,
    *,
    skill: str,
    model: str,
    gemini_path: str,
    gemini_version: str,
    gemini_sha256: str,
    task_sha256: str,
    avoids_action: str,
    expected_savings_reason: str,
) -> str:
    source_lines = "\n".join(
        f"- {item['path']} ({item['sha256']}, {item['size_bytes']} bytes)"
        for item in sources
    )
    example_sources = json.dumps(sources, indent=2, sort_keys=True)
    command = advice_command(gemini_path)
    return f"""# Lite Advisory Packet {packet_id}

You are a CLI-only Lite advisor. Do not edit files, create branches, create worktrees, run tests, or decide pass/fail. Your job is to route context cheaply for heavier agents.

Purpose: {purpose}
Avoids action: {avoids_action}
Expected savings reason: {expected_savings_reason}
Base directory: {base_dir}

Deterministic envelope:
- Skill: {skill}
- Model: {model}
- Gemini path: {gemini_path if gemini_path else "unavailable"}
- Gemini version: {gemini_version}
- Gemini sha256: {gemini_sha256}
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


def launch_for(packet_id: str, purpose: str, base_dir: Path, gemini_path: str) -> str:
    return compact_launch_script()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--purpose", choices=sorted(allowed_purposes()), required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-file", action="append", default=[])
    parser.add_argument("--task-file")
    parser.add_argument("--avoids-action", help="Expensive action this Lite packet is expected to avoid; defaults by purpose when known.")
    parser.add_argument("--expected-savings-reason", help="Concrete reason this Lite packet reduces a heavier read or model call; defaults by purpose when known.")
    parser.add_argument("--replace", action="store_true", help="Replace an existing packet directory after removing it first.")
    args = parser.parse_args()

    packet_id = require_safe_label(args.packet_id, "packet-id")
    skill = current_skill_name()
    if skill == "goal-branch-orchestrator" and not BRANCH_LITE_PACKET_RE.fullmatch(packet_id):
        raise SystemExit("branch Lite packet-id must be scoped as <branch-id>-L<suffix>")
    base_dir = resolve_absolute_path(args.base_dir, "--base-dir", must_exist=True)
    if not base_dir.is_dir():
        raise SystemExit(f"--base-dir must be a directory: {base_dir}")
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    task_file = (
        resolve_absolute_path(args.task_file, "--task-file", must_exist=True)
        if args.task_file
        else None
    )
    input_files = [
        resolve_absolute_path(value, "--input-file", must_exist=True)
        for value in args.input_file
    ]
    if not input_files:
        raise SystemExit("at least one --input-file is required")
    sources = [source_metadata(path, base_dir) for path in input_files]
    avoids_action, expected_savings_reason = lite_usefulness(
        args.purpose,
        args.avoids_action,
        args.expected_savings_reason,
    )

    packet_dir = out_dir / packet_id
    if packet_dir.exists():
        if not args.replace:
            raise SystemExit(f"Lite packet already exists; pass --replace to recreate deterministically: {packet_dir}")
        shutil.rmtree(packet_dir)
    packet_dir.mkdir(parents=True, exist_ok=True)
    extra = task_file.read_text(encoding="utf-8") if task_file else ""
    task_sha256 = sha256_text(extra)
    gemini_path, gemini_version, gemini_sha256 = resolve_gemini()
    prompt_text = prompt_for(
        packet_id,
        args.purpose,
        base_dir,
        sources,
        extra,
        skill=skill,
        model=LITE_MODEL,
        gemini_path=gemini_path,
        gemini_version=gemini_version,
        gemini_sha256=gemini_sha256,
        task_sha256=task_sha256,
        avoids_action=avoids_action,
        expected_savings_reason=expected_savings_reason,
    )
    inputs = {
        "packet_id": packet_id,
        "purpose": args.purpose,
        "avoids_action": avoids_action,
        "expected_savings_reason": expected_savings_reason,
        "skill": skill,
        "base_dir": base_dir.as_posix(),
        "model": LITE_MODEL,
        "gemini_path": gemini_path,
        "gemini_version": gemini_version,
        "gemini_sha256": gemini_sha256,
        "task_sha256": task_sha256,
        "prompt_sha256": sha256_text(prompt_text),
        "source_files": sources,
    }

    (packet_dir / "input-files.json").write_text(json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (packet_dir / "prompt.md").write_text(
        prompt_text,
        encoding="utf-8",
    )
    (packet_dir / "task.md").write_text(extra, encoding="utf-8")
    (packet_dir / "launch-config.json").write_text(
        json.dumps(
            lite_launch_config(
                packet_id,
                args.purpose,
                base_dir,
                gemini_path,
                avoids_action=avoids_action,
                expected_savings_reason=expected_savings_reason,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch_for(packet_id, args.purpose, base_dir, gemini_path), encoding="utf-8")
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
