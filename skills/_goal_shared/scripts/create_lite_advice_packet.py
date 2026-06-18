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
from pathlib import Path


BRANCH_LITE_PACKET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*-L[A-Za-z0-9_.-]+$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# Lite route alias on the bridge deepseek ladder; resolved to the deepseek model
# id and variant through the shared contract (no hardcoded model strings).
LITE_ROUTE_ALIAS = "ds-flash-max"
LITE_ATTEMPT_TIMEOUT_SECONDS = 600
TIMEOUT_KILL_AFTER_SECONDS = 30
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


def _load_lite_prompt():
    # Always resolved next to this shared module (Path(__file__) points at the
    # _goal_shared copy even when invoked through a generated per-skill wrapper),
    # so create and validate import the SAME prompt builder and cannot drift.
    path = Path(__file__).resolve().parent / "lite_prompt.py"
    if not path.exists():
        raise SystemExit(f"missing shared lite prompt builder: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_lite_prompt", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared lite prompt builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
PATH_RULES = _load_path_rules()
LITE_PROMPT = _load_lite_prompt()
require_safe_label = PATH_RULES.require_safe_packet_label
resolve_absolute_path = PATH_RULES.resolve_absolute_path
repo_relative_path = PATH_RULES.repo_relative_path
shell_quote = CONTRACT.shell_quote
build_lite_prompt = LITE_PROMPT.build_lite_prompt
bridge_advice_command = LITE_PROMPT.bridge_advice_command
LITE_STATUS_BEGIN = LITE_PROMPT.LITE_STATUS_BEGIN
LITE_STATUS_END = LITE_PROMPT.LITE_STATUS_END
# Bridge/deepseek route descriptors consumed from the shared contract.
BRIDGE_PROVIDER_ID = CONTRACT.BRIDGE_PROVIDER_ID
BRIDGE_HARNESS_KIND = CONTRACT.BRIDGE_HARNESS_KIND
LITE_MODEL = CONTRACT.bridge_model(LITE_ROUTE_ALIAS)
LITE_VARIANT = CONTRACT.bridge_variant(LITE_ROUTE_ALIAS)
LITE_APPROVAL_MODE = CONTRACT.LITE_APPROVAL_MODE
LITE_PERMISSION_PROFILE = "read-only"
LITE_EVENT_LABEL = CONTRACT.bridge_event_label(LITE_ROUTE_ALIAS)


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


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_bridge_root() -> Path | None:
    """Resolve the opencode-worker-bridge skill root that holds the control script.

    Order mirrors the B4 runtime pattern: env override -> source checkout under
    CWD -> $CODEX_HOME skills -> $HOME/.agents skills.
    """
    env_root = os.environ.get("OPENCODE_WORKER_BRIDGE_ROOT")
    candidates: list[Path] = []
    if env_root and env_root.strip():
        candidates.append(Path(env_root).expanduser())
    candidates.append(Path.cwd() / "skills" / "opencode-worker-bridge")
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    candidates.append(Path(codex_home).expanduser() / "skills" / "opencode-worker-bridge")
    candidates.append(Path(os.path.expanduser("~")) / ".agents" / "skills" / "opencode-worker-bridge")
    for candidate in candidates:
        if (candidate / "scripts" / "opencode_worker.py").exists():
            return candidate
    return None


def offline_bridge_metadata_from_env() -> tuple[str, str] | None:
    """Offline capture of the bridge control-script path/version for fixtures.

    Returns (control_script_path, control_version) or None when not in offline
    mode. The live deepseek delegate is never invoked at packet creation.
    """
    if os.environ.get("GOAL_LITE_OFFLINE_BRIDGE_METADATA") != "1":
        return None
    control_script = os.environ.get("GOAL_LITE_BRIDGE_CONTROL_SCRIPT", "").strip()
    control_version = os.environ.get("GOAL_LITE_BRIDGE_CONTROL_VERSION", "").strip()
    missing = [
        name
        for name, value in (
            ("GOAL_LITE_BRIDGE_CONTROL_SCRIPT", control_script),
            ("GOAL_LITE_BRIDGE_CONTROL_VERSION", control_version),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"offline bridge metadata mode missing: {', '.join(missing)}")
    path = Path(control_script)
    if "\\" in control_script or not path.is_absolute() or ".." in path.parts:
        raise SystemExit("GOAL_LITE_BRIDGE_CONTROL_SCRIPT must be an absolute path without traversal")
    if control_version == "unavailable":
        raise SystemExit("GOAL_LITE_BRIDGE_CONTROL_VERSION must be a captured fixture version")
    return control_script, control_version


def bridge_control_version(control_path: Path) -> str:
    """Best-effort deterministic control-script version (schema version).

    Reports the bridge contracts SCHEMA_VERSION when discoverable, else the
    control-script sha256. No live delegate call is made.
    """
    contracts_path = control_path.parent / "contracts.py"
    if contracts_path.exists():
        try:
            text = contracts_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            text = ""
        match = re.search(r"SCHEMA_VERSION\s*=\s*(\d+)", text)
        if match:
            return f"schema_version:{match.group(1)}"
    try:
        return sha256_file(control_path)
    except Exception as exc:  # noqa: BLE001
        return f"version-unavailable: {exc}"


def resolve_bridge_control() -> tuple[str, str]:
    """Resolve the bridge control-script path + version for the Lite envelope.

    Returns (control_script_path, control_version). Both are "unavailable" when
    the bridge control script cannot be located, which produces a blocked Lite
    packet at runtime (the route is unusable, never silently degraded).
    """
    offline_metadata = offline_bridge_metadata_from_env()
    if offline_metadata is not None:
        return offline_metadata
    bridge_root = resolve_bridge_root()
    if bridge_root is None:
        return "", "unavailable"
    control_path = (bridge_root / "scripts" / "opencode_worker.py").resolve()
    return control_path.as_posix(), bridge_control_version(control_path)


def source_metadata(path: Path, base_dir: Path) -> dict:
    return {
        "path": repo_relative_path(path, base_dir, "--input-file"),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "reason": "explicit Lite input",
    }


def advice_command(control_script: str) -> str:
    return bridge_advice_command(
        control_script=control_script,
        provider=BRIDGE_PROVIDER_ID,
        model=LITE_MODEL,
        variant=LITE_VARIANT,
        permission_profile=LITE_PERMISSION_PROFILE,
    )


def lite_telemetry_attempts(control_script: str) -> list[dict]:
    return [
        {
            "alias": LITE_ROUTE_ALIAS,
            "provider": BRIDGE_HARNESS_KIND,
            "provider_id": BRIDGE_PROVIDER_ID,
            "model": LITE_MODEL,
            "variant": LITE_VARIANT,
            "harness": BRIDGE_HARNESS_KIND,
            "harness_kind": BRIDGE_HARNESS_KIND,
            "effort": "",
            "command": advice_command(control_script),
            "timeout_seconds": LITE_ATTEMPT_TIMEOUT_SECONDS,
            "event_logs": [f"events-{LITE_EVENT_LABEL}.jsonl"],
            "probe_logs": [],
            "bridge": {
                "provider": BRIDGE_PROVIDER_ID,
                "model": LITE_MODEL,
                "variant": LITE_VARIANT,
                "permission_profile": LITE_PERMISSION_PROFILE,
                "run_dir": f"bridge/{LITE_EVENT_LABEL}",
                "pool_dir": "bridge/pool",
                "prompt_file": "prompt.md",
                "supervisor": False,
            },
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


def load_optional_manifest(base_dir: Path) -> dict | None:
    manifest_path = base_dir / "job.manifest.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def lite_launch_config(
    packet_id: str,
    purpose: str,
    base_dir: Path,
    control_script: str,
    control_version: str,
    *,
    avoids_action: str,
    expected_savings_reason: str,
    manifest: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "role": "lite_advisor",
        "packet_id": packet_id,
        "purpose": purpose,
        "avoids_action": avoids_action,
        "expected_savings_reason": expected_savings_reason,
        "base_dir": base_dir.as_posix(),
        "alias": LITE_ROUTE_ALIAS,
        "provider": BRIDGE_PROVIDER_ID,
        "model": LITE_MODEL,
        "variant": LITE_VARIANT,
        "permission_profile": LITE_PERMISSION_PROFILE,
        "approval_mode": LITE_APPROVAL_MODE,
        "bridge_control_script": control_script,
        "bridge_control_version": control_version,
        "event_label": LITE_EVENT_LABEL,
        "attempt_timeout_seconds": LITE_ATTEMPT_TIMEOUT_SECONDS,
        "timeout_kill_after_seconds": TIMEOUT_KILL_AFTER_SECONDS,
        "inputs_name": "input-files.json",
        "prompt_name": "prompt.md",
        "task_name": "task.md",
        "output_name": "advice.json",
        "raw_name": "advice.raw.txt",
        "telemetry_name": "telemetry.json",
        **CONTRACT.telemetry_debug_config(manifest),
        "status_begin": LITE_STATUS_BEGIN,
        "status_end": LITE_STATUS_END,
        "runner_prompt": "Follow the complete Lite advisory packet instructions provided on stdin.",
        "validation_script": (current_script_dir() / "validate_lite_advice.py").as_posix(),
        "telemetry_script": (current_script_dir() / "extract_telemetry.py").as_posix(),
        "attempts": lite_telemetry_attempts(control_script),
        "terminal_messages": {
            "bridge_unavailable": f"opencode-worker-bridge control script unavailable at packet creation path: {control_script}",
            "inputs_stale": "Lite advisor input files changed or became unavailable after packet creation.",
            "prompt_stale": "Lite advisor prompt.md changed or became unavailable after packet creation.",
            "task_stale": "Lite advisor task.md changed or became unavailable after packet creation.",
            "bridge_stale": "opencode-worker-bridge control script changed or could not be verified after packet creation.",
            "command_failed": "Lite advisor bridge delegate failed. Inspect the bridge run-dir artifacts for transport, model, permission, or validation errors.",
            "invalid_output": "Lite advisor did not produce valid advice JSON.",
        },
    }


def prompt_for(
    packet_id: str,
    purpose: str,
    base_dir,
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
    # Thin pass-through to the single shared builder so create and validate
    # cannot drift; see skills/_goal_shared/scripts/lite_prompt.py.
    return build_lite_prompt(
        packet_id,
        purpose,
        base_dir,
        sources,
        extra,
        skill=skill,
        model=model,
        provider=provider,
        variant=variant,
        control_script=control_script,
        control_version=control_version,
        permission_profile=permission_profile,
        task_sha256=task_sha256,
        avoids_action=avoids_action,
        expected_savings_reason=expected_savings_reason,
    )


def launch_for(packet_id: str, purpose: str, base_dir: Path, control_script: str) -> str:
    return compact_launch_script()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-id", required=True)
    parser.add_argument("--purpose", choices=sorted(allowed_purposes()), required=True)
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--input-file", action="append", default=[])
    parser.add_argument("--task-file")
    parser.add_argument(
        "--avoids-action",
        help="Expensive action this Lite packet is expected to avoid; defaults by purpose when known.",
    )
    parser.add_argument(
        "--expected-savings-reason",
        help="Concrete reason this Lite packet reduces a heavier read or model call; defaults by purpose when known.",
    )
    parser.add_argument(
        "--replace", action="store_true", help="Replace an existing packet directory after removing it first."
    )
    args = parser.parse_args()

    packet_id = require_safe_label(args.packet_id, "packet-id")
    skill = current_skill_name()
    if skill == "goal-branch-orchestrator" and not BRANCH_LITE_PACKET_RE.fullmatch(packet_id):
        raise SystemExit("branch Lite packet-id must be scoped as <branch-id>-L<suffix>")
    base_dir = resolve_absolute_path(args.base_dir, "--base-dir", must_exist=True)
    if not base_dir.is_dir():
        raise SystemExit(f"--base-dir must be a directory: {base_dir}")
    manifest = load_optional_manifest(base_dir)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    task_file = resolve_absolute_path(args.task_file, "--task-file", must_exist=True) if args.task_file else None
    input_files = [resolve_absolute_path(value, "--input-file", must_exist=True) for value in args.input_file]
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
    # errors="replace": match the validator side (validate_lite_advice reads the persisted
    # task.md with errors="replace") so a non-UTF-8 --task-file does not crash create and the
    # create/validate hashes stay consistent.
    extra = task_file.read_text(encoding="utf-8", errors="replace") if task_file else ""
    task_sha256 = sha256_text(extra)
    control_script, control_version = resolve_bridge_control()
    prompt_text = prompt_for(
        packet_id,
        args.purpose,
        base_dir,
        sources,
        extra,
        skill=skill,
        model=LITE_MODEL,
        provider=BRIDGE_PROVIDER_ID,
        variant=LITE_VARIANT,
        control_script=control_script,
        control_version=control_version,
        permission_profile=LITE_PERMISSION_PROFILE,
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
        "alias": LITE_ROUTE_ALIAS,
        "provider": BRIDGE_PROVIDER_ID,
        "harness_kind": BRIDGE_HARNESS_KIND,
        "model": LITE_MODEL,
        "variant": LITE_VARIANT,
        "permission_profile": LITE_PERMISSION_PROFILE,
        "bridge_control_script": control_script,
        "bridge_control_version": control_version,
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
                control_script,
                control_version,
                avoids_action=avoids_action,
                expected_savings_reason=expected_savings_reason,
                manifest=manifest,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch_for(packet_id, args.purpose, base_dir, control_script), encoding="utf-8")
    os.chmod(launch_path, 0o755)
    print(packet_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
