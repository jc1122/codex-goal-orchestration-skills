#!/usr/bin/env python3
"""Create the mandatory prompt-audit packet for a prepared job bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


def _load_contract():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "orchestration_contract.py"
    if not path.exists():
        raise SystemExit(f"missing shared orchestration contract: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()
shell_quote = CONTRACT.shell_quote
AUDIT_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.5"]
AUDIT_FALLBACK_MODEL = CONTRACT.CODEX_ROUTE_MODELS["gpt-5.4"]
AUDIT_ATTEMPT_TIMEOUT_SECONDS = CONTRACT.AUDIT_ATTEMPT_TIMEOUT_SECONDS
TIMEOUT_KILL_AFTER_SECONDS = CONTRACT.TIMEOUT_KILL_AFTER_SECONDS
RUNNER_PATH = (Path(__file__).resolve().parent / "runtime_prompt_audit_runner.py").resolve()


def audit_telemetry_attempts(repo_root: Path, *, timeout_seconds: int = AUDIT_ATTEMPT_TIMEOUT_SECONDS) -> list[dict]:
    attempts = CONTRACT.codex_telemetry_attempts(
        ["gpt-5.5", "gpt-5.4"],
        timeout_seconds=timeout_seconds,
        sandbox="read-only",
        event_labels=["primary", "fallback"],
    )
    for attempt in attempts:
        model = str(attempt.get("model", ""))
        attempt["command"] = (
            f"codex exec --ephemeral -m {model} -C {repo_root.as_posix()} "
            "-s read-only --json --output-schema prompt-audit.schema.json -o prompt-audit.json"
        )
    return attempts


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
resolve_absolute_path = PATH_RULES.resolve_absolute_path
resolve = PATH_RULES.resolve
require_relative_path = PATH_RULES.require_relative_path


def resolve_bundle_path(base: Path, value: object, field: str) -> Path:
    return resolve(base, require_relative_path(value, field))


def resolve_repo_path(repo_root: Path, value: object, field: str) -> Path:
    return resolve(repo_root, require_relative_path(value, field))


def load_manifest(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"manifest must be a JSON object: {path}")
    return data


def archive_existing_packet_dir(packet_dir: Path, *, replace: bool) -> None:
    if not packet_dir.exists():
        return
    if packet_dir.is_dir() and not any(packet_dir.iterdir()):
        return
    if not replace:
        raise SystemExit(f"audit packet already exists; pass --replace to archive and recreate: {packet_dir}")
    attempts_dir = packet_dir / "attempts"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    next_index = 1
    for child in sorted(attempts_dir.iterdir()):
        if child.is_dir() and child.name.startswith("attempt-"):
            suffix = child.name.removeprefix("attempt-")
            if suffix.isdigit():
                next_index = max(next_index, int(suffix) + 1)
    archive_dir = attempts_dir / f"attempt-{next_index:03d}"
    archive_dir.mkdir()
    for child in sorted(packet_dir.iterdir()):
        if child.name == "attempts":
            continue
        child.rename(archive_dir / child.name)


def exact_string_schema(value: str) -> dict:
    return {"type": "string", "const": value}


def audit_schema(manifest_path: Path, repo_root: Path) -> dict:
    nonempty_string = {"type": "string", "minLength": 1}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "manifest",
            "repo_root",
            "status",
            "can_start",
            "checked_files",
            "defects",
            "missing_dod_items",
            "actionability_verdict",
            "commands_run",
            "summary",
        ],
        "properties": {
            "manifest": exact_string_schema(manifest_path.as_posix()),
            "repo_root": exact_string_schema(repo_root.as_posix()),
            "status": {"type": "string", "enum": ["pass", "failed", "blocked"]},
            "can_start": {"type": "boolean"},
            "checked_files": {"type": "array", "items": nonempty_string},
            "defects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["file", "severity", "message"],
                    "properties": {
                        "file": nonempty_string,
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "message": nonempty_string,
                    },
                },
            },
            "missing_dod_items": {"type": "array", "items": nonempty_string},
            "actionability_verdict": nonempty_string,
            "commands_run": {"type": "array", "minItems": 1, "items": nonempty_string},
            "summary": nonempty_string,
        },
    }


def render_prompt(manifest_path: Path, repo_root: Path, manifest: dict) -> str:
    base = manifest_path.parent
    main_prompt = resolve_bundle_path(base, manifest["main_prompt"], "main_prompt")
    branches = manifest.get("branches", [])
    max_active = manifest.get("max_active_branch_agents", "missing")
    waves = manifest.get("waves", [])
    branch_lines = []
    for branch in branches:
        work_items = branch.get("work_items", [])
        if isinstance(work_items, list):
            worker_packet_ids = ",".join(
                str(item.get("packet_id", "missing")) if isinstance(item, dict) else "invalid" for item in work_items
            )
            worker_types = ",".join(
                str(item.get("worker_type", "worker")) if isinstance(item, dict) else "invalid" for item in work_items
            )
        else:
            worker_packet_ids = "invalid"
            worker_types = "invalid"
        branch_lines.append(
            "- {id}: prompt={prompt}, branch={branch_name}, worktree={worktree}, expected_status_output={status}, expected_review_output={review}, depends_on={depends_on}, max_active_worker_packets={max_workers}, worker_packets={worker_packets}, worker_packet_ids={worker_packet_ids}, worker_types={worker_types}".format(
                id=branch.get("id", ""),
                prompt=resolve_bundle_path(base, branch.get("prompt", ""), "prompt").as_posix(),
                branch_name=branch.get("branch_name", ""),
                worktree=resolve_repo_path(repo_root, branch.get("worktree_path", ""), "worktree_path").as_posix(),
                status=resolve_bundle_path(base, branch.get("status_path", ""), "status_path").as_posix(),
                review=resolve_bundle_path(base, branch.get("review_path", ""), "review_path").as_posix(),
                depends_on=",".join(branch.get("depends_on", []))
                if isinstance(branch.get("depends_on", []), list)
                else "invalid",
                max_workers=branch.get("max_active_worker_packets", "missing"),
                worker_packets=len(work_items) if isinstance(work_items, list) else "invalid",
                worker_packet_ids=worker_packet_ids,
                worker_types=worker_types,
            )
        )

    return f"""# Prompt Audit Packet

You are a read-only prompt auditor. Do not edit files. Do not create branches or worktrees.

Repository root: {repo_root}
Manifest: {manifest_path}
Main prompt: {main_prompt}

Branch prompt entries:
{os.linesep.join(branch_lines) if branch_lines else "- none"}

Max active branch agents: {max_active}
Waves:
{json.dumps(waves, indent=2, sort_keys=True)}

Run only non-mutating inspection commands as needed, starting with:

```bash
pwd
git status --short --branch
git diff --check HEAD
```

Audit `job.manifest.json`, `main.prompt.md`, and every listed branch prompt before any runtime orchestration starts.

Do not emit the final JSON until after the inspection commands have completed and you have read the
manifest, main prompt, and listed branch prompts. If an intermediate thought or tool issue tempts you
to return early, continue the read-only inspection first; the last assistant message must be the only
final JSON object.

Required checks:

- `job.manifest.json`, `main.prompt.md`, and every branch prompt exist and are readable.
- Status, review, and worktree paths are output targets; they do not need to exist before audit.
- Manifest branch ids, branch names, worktree paths, status paths, review paths, prompt paths, and pre-review-gate paths are present, unique, relative where required, and collision-free.
- `max_active_branch_agents <= 4`; branch and worker parallelism are default rolling mode; waves cover each branch exactly once and are scheduling/order groups, not dependency barriers.
- Branch `depends_on` entries reference only prior branch ids; worker `depends_on` entries reference only prior work item ids.
- Every branch declares 1 to 4 work items, deterministic `<branch_id>-<work_item_id>` packet ids, and `max_active_worker_packets` from 1 to 4.
- Compact prompts are valid when they list job-specific objectives/scope/work items/DoD and point runtime procedure to the skill phase manifests; full repeated policy may live in the manifest and deterministic scripts.
- `main.prompt.md` requires skill availability bootstrap, prompt audit, model-catalog capture, branch slot saturation, closing finished branch orchestrators before replacements, manifest-bound branch/main validators, telemetry summary, and no active artifact polling.
- Branch prompts require branch bootstrap, worker slot saturation, manifest-owned packet artifacts, pre-review gate before reviewer launch, base-range `git diff --check`, telemetry, and manifest-bound branch validation.
- Research-worker boundaries, review routes, Lite advisor limits, amendment limits, artifact policy, and cleanup policy are present in the manifest and not contradicted by prompts.
- Prompts are actionable without chat history, do not require branch creation before audit, define falsifiable Definitions of Done, and preserve unsupported/unresolved/negative/probe-only labels.
- A `pass` audit must have `can_start=true`, no `critical` or `major` defects, and no missing DoD items.

Return only JSON matching `prompt-audit.schema.json`. The JSON must include `manifest` and `repo_root`
exactly as specified by the schema.
"""


def render_launch() -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
runner={shell_quote(RUNNER_PATH.as_posix())}
if [[ ! -f "$runner" ]]; then
  echo "prompt audit runner missing: $runner" >&2
  exit 127
fi
exec python3 "$runner" --packet-dir "$(pwd)"
"""


def audit_launch_config(
    manifest_path: Path,
    repo_root: Path,
    *,
    manifest: dict | None = None,
    attempt_timeout_seconds: int = AUDIT_ATTEMPT_TIMEOUT_SECONDS,
) -> dict:
    attempts = audit_telemetry_attempts(repo_root, timeout_seconds=attempt_timeout_seconds)
    debug_config = CONTRACT.telemetry_debug_config(manifest)
    return {
        "schema_version": 1,
        "role": "prompt-auditor",
        "packet_id": "prompt-audit",
        "repo_root": repo_root.as_posix(),
        "manifest_path": manifest_path.as_posix(),
        "prompt_name": "prompt.md",
        "schema_name": "prompt-audit.schema.json",
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        **debug_config,
        "attempt_timeout_seconds": attempt_timeout_seconds,
        "timeout_kill_after_seconds": TIMEOUT_KILL_AFTER_SECONDS,
        "telemetry_script": (Path(__file__).resolve().parent / "extract_telemetry.py").as_posix(),
        "validation_script": (Path(__file__).resolve().parent / "validate_prompt_audit.py").as_posix(),
        "attempts": attempts,
        "commands_run": [str(attempt.get("command", "")) for attempt in attempts],
        "terminal_messages": {
            "git_invalid": "Repository root is not a valid git worktree; prompt audit cannot run.",
            "missing_runtime_file": "Prompt audit runtime input file is missing.",
            "command_failed": "Prompt audit command failed before producing a valid prompt-audit.json.",
            "invalid_output": "Prompt audit did not produce a valid prompt-audit.json artifact.",
            "interrupted": "Prompt audit runner was interrupted before producing a valid prompt-audit.json.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--replace", action="store_true", help="Archive an existing audit packet under attempts/ and recreate it."
    )
    parser.add_argument(
        "--attempt-timeout-seconds",
        type=int,
        default=AUDIT_ATTEMPT_TIMEOUT_SECONDS,
        help="Per-model prompt-audit attempt timeout; default preserves runtime policy.",
    )
    args = parser.parse_args()
    if args.attempt_timeout_seconds <= 0:
        raise SystemExit("--attempt-timeout-seconds must be positive")

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    manifest = load_manifest(manifest_path)

    archive_existing_packet_dir(out_dir, replace=args.replace)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prompt-audit.schema.json").write_text(
        json.dumps(audit_schema(manifest_path, repo_root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "prompt.md").write_text(
        render_prompt(manifest_path, repo_root, manifest),
        encoding="utf-8",
    )
    (out_dir / "launch-config.json").write_text(
        json.dumps(
            audit_launch_config(
                manifest_path, repo_root, manifest=manifest, attempt_timeout_seconds=args.attempt_timeout_seconds
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    launch = out_dir / "launch.sh"
    launch.write_text(render_launch(), encoding="utf-8")
    os.chmod(launch, 0o755)
    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
