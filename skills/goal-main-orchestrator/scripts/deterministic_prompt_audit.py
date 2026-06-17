#!/usr/bin/env python3
"""Write a prompt-audit artifact from deterministic bundle checks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
GOAL_PREFLIGHT_LINTER = SCRIPT_DIR.parents[1] / "goal-preflight" / "scripts" / "lint_goal_bundle.py"
ALIAS = "deterministic-prompt-audit"


def load_module(name: str, path: Path):
    if not path.exists():
        raise SystemExit(f"missing required module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load required module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"JSON must be an object: {path}")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


def file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": path.name, "exists": False, "bytes": 0, "chars": 0, "usage": None}
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return {"path": path.name, "exists": True, "bytes": len(raw), "chars": len(text), "usage": None}


def resolve_path(value: str, label: str, *, must_exist: bool) -> Path:
    path = Path(value).expanduser().resolve()
    if must_exist and not path.exists():
        raise SystemExit(f"{label} does not exist: {path}")
    return path


def normalize_severity(value: object) -> str:
    severity = str(value or "").strip().lower()
    if severity in {"critical", "major", "minor"}:
        return severity
    if severity in {"warning", "warn", "info", "review"}:
        return "minor"
    return "major"


def normalize_display_severity(value: object) -> str:
    severity = str(value or "").strip().lower()
    if severity in {"critical", "major", "minor", "warning", "warn", "info", "review"}:
        return severity
    return "info"


def add_defect(defects: list[dict[str, str]], file: str, severity: object, message: str) -> None:
    normalized = normalize_severity(severity)
    display = normalize_display_severity(severity)
    entry: dict[str, str] = {"file": file, "severity": normalized, "message": message}
    if display != normalized:
        entry["display_severity"] = display
    defects.append(entry)


def parse_git_status(raw: str) -> dict[str, list[str]]:
    tracked: list[str] = []
    untracked: list[str] = []
    for line in raw.splitlines():
        status_line = line.rstrip()
        if len(status_line) < 3:
            continue
        status = status_line[:2]
        if status == "##":
            continue
        if status == "!!":
            continue
        path = status_line[3:].strip()
        if not path:
            continue
        if "->" in path:
            path = path.split("->", 1)[0].strip()
        if status == "??":
            untracked.append(path)
        else:
            tracked.append(path)
    return {"tracked": tracked, "untracked": untracked}


def root_worktree_state_from_manifest(
    manifest: dict[str, Any], status_result: subprocess.CompletedProcess[str]
) -> dict[str, object]:
    repo_status = manifest.get("repo_status")
    if isinstance(repo_status, dict):
        state = repo_status.get("root_worktree_state")
        if isinstance(state, dict):
            return dict(state)

    parsed = parse_git_status(status_result.stdout)
    branch_paths = []
    manifest_branches = manifest.get("branches")
    for branch in manifest_branches if isinstance(manifest_branches, list) else []:
        if not isinstance(branch, dict):
            continue
        worktree_path = branch.get("worktree_path")
        if isinstance(worktree_path, str) and worktree_path.strip():
            branch_paths.append(worktree_path.strip())

    used_for_runtime = True
    if branch_paths:
        used_for_runtime = any(path in {"", ".", "./"} for path in branch_paths)

    dirty = bool(parsed["tracked"] or parsed["untracked"])
    if dirty:
        decision = "warning" if used_for_runtime else "ignored_because_isolated_worktrees"
    else:
        decision = "clean"

    return {
        "dirty": dirty,
        "tracked_changes": parsed["tracked"],
        "untracked_changes": parsed["untracked"],
        "used_for_runtime": used_for_runtime,
        "decision": decision,
    }


def one_char_bullet_run(text: str) -> bool:
    run_length = 0
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) == 3 and stripped.startswith("- "):
            run_length += 1
            if run_length >= 8:
                return True
        elif stripped:
            run_length = 0
    return False


def checked_prompt_files(bundle_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = [bundle_dir / "job.manifest.json"]
    main_prompt = manifest.get("main_prompt")
    if isinstance(main_prompt, str):
        paths.append(bundle_dir / main_prompt)
    paths.append(bundle_dir / "goal-bootloader.md")
    manifest_branches = manifest.get("branches")
    for branch in manifest_branches if isinstance(manifest_branches, list) else []:
        if isinstance(branch, dict) and isinstance(branch.get("prompt"), str):
            paths.append(bundle_dir / branch["prompt"])
    return paths


def lint_bundle(bundle_dir: Path) -> dict[str, Any]:
    module = load_module("goal_preflight_lint_goal_bundle", GOAL_PREFLIGHT_LINTER)
    data = module.lint(bundle_dir)
    if not isinstance(data, dict):
        raise SystemExit("lint_goal_bundle.py did not return an object")
    return data


def write_telemetry(packet_dir: Path, output_name: str, prompt_name: str, command: str, timeout_seconds: int) -> None:
    log = file_stats(packet_dir / "deterministic-audit.log")
    prompt = file_stats(packet_dir / prompt_name)
    output = file_stats(packet_dir / output_name)
    telemetry = {
        "schema_version": 1,
        "packet_id": "prompt-audit",
        "role": "prompt-auditor",
        "output_artifact": output_name,
        "prompt_artifact": prompt_name,
        "prompt_chars": prompt["chars"],
        "prompt_bytes": prompt["bytes"],
        "output_chars": output["chars"],
        "output_bytes": output["bytes"],
        "event_log_chars": log["chars"],
        "event_log_bytes": log["bytes"],
        "accepted_alias": ALIAS,
        "attempts": [
            {
                "alias": ALIAS,
                "provider": "local",
                "model": ALIAS,
                "effort": None,
                "command": command,
                "timeout_seconds": timeout_seconds,
                "called": True,
                "accepted": True,
                "event_logs": [log],
                "probe_logs": [],
                "usage": None,
            }
        ],
        "totals": {
            "attempts_declared": 1,
            "attempts_called": 1,
            "event_log_chars": log["chars"],
            "event_log_bytes": log["bytes"],
            "known_usage": None,
        },
    }
    write_json(packet_dir / "telemetry.json", telemetry)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--audit-dir", required=True)
    parser.add_argument("--output-name", default="prompt-audit.json")
    parser.add_argument("--prompt-name", default="prompt.md")
    parser.add_argument("--timeout-seconds", type=int, default=1)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")

    manifest_path = resolve_path(args.manifest, "--manifest", must_exist=True)
    repo_root = resolve_path(args.repo_root, "--repo-root", must_exist=True)
    audit_dir = resolve_path(args.audit_dir, "--audit-dir", must_exist=False)
    audit_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = manifest_path.parent
    manifest = read_json(manifest_path)
    status_result = run(["git", "-C", repo_root.as_posix(), "status", "--short", "--branch"])
    output_path = audit_dir / args.output_name
    command = " ".join(sys.argv)
    root_state = root_worktree_state_from_manifest(manifest, status_result)

    defects: list[dict[str, str]] = []
    commands_run = [
        f"python3 {GOAL_PREFLIGHT_LINTER.as_posix()} --bundle-dir {bundle_dir.as_posix()} --no-write",
        f"git -C {repo_root.as_posix()} status --short --branch",
        f"git -C {repo_root.as_posix()} diff --check HEAD",
        command,
    ]

    lint = lint_bundle(bundle_dir)
    for item in lint.get("defects", []):
        if not isinstance(item, dict):
            continue
        add_defect(
            defects,
            str(item.get("file", "bundle")),
            str(item.get("severity", "critical")),
            str(item.get("message", "bundle lint defect")),
        )

    diff_result = run(["git", "-C", repo_root.as_posix(), "diff", "--check", "HEAD"])
    if status_result.returncode != 0:
        add_defect(defects, "repository", "critical", "git status failed during deterministic prompt audit")
    if diff_result.returncode != 0:
        add_defect(defects, "repository", "critical", "git diff --check HEAD failed during deterministic prompt audit")

    checked_files = []
    for path in checked_prompt_files(bundle_dir, manifest):
        rel = path.relative_to(bundle_dir).as_posix() if path.is_relative_to(bundle_dir) else path.as_posix()
        if path.exists():
            checked_files.append(rel)
            text = path.read_text(encoding="utf-8", errors="replace")
            if one_char_bullet_run(text):
                add_defect(defects, rel, "major", "prompt contains a run of one-character bullets")
        else:
            add_defect(defects, rel, "critical", "required prompt-audit input file is missing")

    blocking = [item for item in defects if item["severity"] in {"critical", "major"}]
    audit_status = "pass" if not blocking else "failed"
    missing_dod_items = (
        [] if audit_status == "pass" else ["Resolve deterministic prompt-audit defects before branch scheduling."]
    )
    summary = (
        "Deterministic prompt audit passed script-provable bundle, prompt, path, telemetry, and git whitespace checks."
        if audit_status == "pass"
        else f"Deterministic prompt audit found {len(blocking)} blocking defect(s)."
    )
    audit = {
        "manifest": manifest_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "root_worktree_state": root_state,
        "status": audit_status,
        "can_start": audit_status == "pass",
        "checked_files": checked_files,
        "defects": defects,
        "missing_dod_items": missing_dod_items,
        "actionability_verdict": "pass" if audit_status == "pass" else "failed",
        "commands_run": commands_run,
        "summary": summary,
    }
    write_json(output_path, audit)
    write_json(
        audit_dir / "deterministic-audit.log",
        {
            "lint": lint,
            "defects": defects,
            "root_worktree_state": root_state,
            "git_status": {
                "returncode": status_result.returncode,
                "output": status_result.stdout,
            },
            "git_diff_check": {
                "returncode": diff_result.returncode,
                "output": diff_result.stdout,
            },
            "audit": {
                "status": audit_status,
                "checked_files": checked_files,
                "blocking_defects": blocking,
            },
        },
    )
    write_telemetry(audit_dir, args.output_name, args.prompt_name, command, args.timeout_seconds)
    print(output_path)
    return 0 if audit_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
