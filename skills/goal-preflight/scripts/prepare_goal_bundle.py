#!/usr/bin/env python3
"""Run the canonical goal-preflight pipeline and persist handoff artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path


def _load_path_rules():
    path = Path(__file__).resolve().parents[2] / "_goal_shared" / "scripts" / "path_rules.py"
    if not path.exists():
        raise SystemExit(f"missing shared path rules: {path}")
    import importlib.util

    spec = importlib.util.spec_from_file_location("goal_shared_path_rules", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared path rules: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATH_RULES = _load_path_rules()
resolve_absolute_path = PATH_RULES.resolve_absolute_path
SKILLS_ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.name).strip("-")
    return value or "goal-config"


def config_path_from_report(report: dict, config: Path) -> Path | None:
    value = report.get("config_path")
    if not isinstance(value, str) or not value:
        return None
    try:
        return Path(value).resolve()
    except (OSError, ValueError):
        return None


def report_matches_config(report: dict, config: Path, config_sha256: str | None = None) -> bool:
    report_config = config_path_from_report(report, config)
    config_sha = config_sha256
    if config_sha is None:
        config_sha = sha256_file(config)
    if report_config is None or config_sha is None:
        return False
    try:
        if report_config.resolve() == config.resolve():
            return True
    except (OSError, ValueError):
        pass
    report_hash = sha256_file(report_config)
    return report_hash is not None and report_hash == config_sha


def routes_verified(report: dict) -> bool:
    if not isinstance(report, dict) or report.get("status") != "pass":
        return False
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    if summary.get("route_verification_status") == "routes_verified":
        return True
    accepted_route_count = summary.get("accepted_route_count")
    return isinstance(accepted_route_count, int) and not isinstance(accepted_route_count, bool) and accepted_route_count > 0


def find_reusable_route_verified_check(config: Path, explicit: Path | None, check_dir: Path) -> Path | None:
    candidate_paths: list[Path] = []
    if explicit is not None:
        candidate_paths.append(explicit)
    config_sha256 = sha256_file(config)

    preferred = (
        f"{config.name}.smoke.json",
        f"{config.stem}.smoke.json",
        f"{config.stem}.preflight.smoke.json",
        "goal-config-smoke.json",
    )
    for name in preferred:
        path = config.parent / name
        if path.is_file():
            candidate_paths.append(path)

    for path in sorted(config.parent.glob("*smoke*.json")):
        if path.is_file() and path not in candidate_paths:
            candidate_paths.append(path)

    if check_dir.is_dir():
        for path in sorted(check_dir.glob("*.preflight-check.json")):
            if path.is_file() and path not in candidate_paths:
                candidate_paths.append(path)

    seen: set[Path] = set()
    for candidate in candidate_paths:
        if candidate in seen:
            continue
        seen.add(candidate)
        if not candidate.is_file():
            continue
        try:
            report = read_json(candidate)
        except Exception:  # noqa: BLE001
            continue
        if routes_verified(report) and report_matches_config(report, config, config_sha256=config_sha256):
            return candidate
    return None


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def command_hash(command: list[str] | None) -> str | None:
    if command is None:
        return None
    payload = json.dumps(command, separators=(",", ":"), sort_keys=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def artifact_snapshot(root: Path) -> dict[str, dict[str, int | str]]:
    if not root.exists():
        return {}
    snapshot: dict[str, dict[str, int | str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        stat = path.stat()
        snapshot[rel] = {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return snapshot


def artifact_delta(before: dict[str, dict[str, int | str]], after: dict[str, dict[str, int | str]]) -> dict:
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    modified = sorted(path for path in before_keys & after_keys if before[path] != after[path])
    return {
        "added_count": len(added),
        "modified_count": len(modified),
        "removed_count": len(removed),
        "added": added[:20],
        "modified": modified[:20],
        "removed": removed[:20],
        "truncated": len(added) > 20 or len(modified) > 20 or len(removed) > 20,
    }


def phase_record(
    phase: str,
    *,
    command: list[str] | None = None,
    returncode: int = 0,
    output: Path | None = None,
    elapsed_ms: int = 0,
    stdout: str = "",
    stderr: str = "",
    before: dict[str, dict[str, int | str]] | None = None,
    after: dict[str, dict[str, int | str]] | None = None,
) -> dict:
    record = {
        "phase": phase,
        "returncode": returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "command_hash": command_hash(command),
    }
    if output is not None:
        record["output"] = output.as_posix()
    if before is not None and after is not None:
        record["artifact_delta"] = artifact_delta(before, after)
    return record


def top_defects_from_report(path: Path, *, limit: int = 5) -> list[str]:
    if not path.exists():
        return []
    try:
        payload = read_json(path)
    except Exception:  # noqa: BLE001
        return []
    raw_items = payload.get("defects") or payload.get("errors") or payload.get("failures") or []
    if not isinstance(raw_items, list):
        return []
    defects: list[str] = []
    for item in raw_items[:limit]:
        if isinstance(item, dict):
            location = item.get("path") or item.get("file") or item.get("field") or "$"
            severity = item.get("severity")
            message = item.get("message") or item.get("reason") or item.get("error") or item
            prefix = f"{severity} " if isinstance(severity, str) and severity else ""
            defects.append(f"{prefix}{location}: {message}")
        else:
            defects.append(str(item))
    return defects


def top_output_lines(*values: str, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for value in values:
        if not value:
            continue
        for line in value.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
            if len(lines) >= limit:
                return lines
    return lines


def emit_failure_summary(
    *,
    phase: str,
    pipeline_path: Path,
    reports: dict[str, Path] | None = None,
    stdout: str = "",
    stderr: str = "",
) -> None:
    print(f"goal-preflight pipeline failed: phase={phase}", file=sys.stderr)
    print(f"pipeline_result={pipeline_path}", file=sys.stderr)
    report_defects: list[str] = []
    for label, path in (reports or {}).items():
        print(f"{label}={path}", file=sys.stderr)
        report_defects.extend(top_defects_from_report(path))
    if report_defects:
        print("top_defects:", file=sys.stderr)
        for item in report_defects[:5]:
            print(f"- {item}", file=sys.stderr)
        return
    output_lines = top_output_lines(stderr, stdout)
    if output_lines:
        print("top_output:", file=sys.stderr)
        for item in output_lines:
            print(f"- {item}", file=sys.stderr)


def run_tracked_phase(phase: str, command: list[str], out_dir: Path, output: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    before = artifact_snapshot(out_dir)
    start = time.perf_counter()
    result = run(command)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    after = artifact_snapshot(out_dir)
    return result, phase_record(
        phase,
        command=command,
        returncode=result.returncode,
        output=output,
        elapsed_ms=elapsed_ms,
        stdout=result.stdout,
        stderr=result.stderr,
        before=before,
        after=after,
    )


def subprocess_telemetry(command: list[str], result: subprocess.CompletedProcess[str], elapsed_ms: int) -> dict:
    return {
        "command_hash": command_hash(command),
        "returncode": result.returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_bytes": len(result.stdout.encode("utf-8")),
        "stderr_bytes": len(result.stderr.encode("utf-8")),
    }


def _is_git_repo(repo_root: Path) -> bool:
    result = run(["git", "-C", repo_root.as_posix(), "rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def _git_ref_exists(repo_root: Path, ref: str | None) -> bool:
    if not isinstance(ref, str) or not ref.strip():
        return True
    result = run(["git", "-C", repo_root.as_posix(), "rev-parse", "--verify", "--quiet", ref])
    return result.returncode == 0


def runtime_gate_precheck(repo_root: Path, brief: dict) -> dict:
    if not _is_git_repo(repo_root):
        return {
            "status": "blocked",
            "reason": "repository root is not a git work tree; runtime branch/worktree orchestration requires git",
            "repo_root": repo_root.as_posix(),
            "repo_is_git": False,
        }
    base_ref = brief.get("base_ref")
    if isinstance(base_ref, str) and base_ref.strip() and not _git_ref_exists(repo_root, base_ref.strip()):
        return {
            "status": "blocked",
            "reason": f"base_ref does not exist: {base_ref.strip()}",
            "repo_root": repo_root.as_posix(),
            "repo_is_git": True,
            "base_ref": base_ref.strip(),
            "base_ref_status": "missing",
        }
    return {
        "status": "pass",
        "reason": "git work tree and base_ref gate passed",
        "repo_root": repo_root.as_posix(),
        "repo_is_git": True,
        "base_ref": base_ref if isinstance(base_ref, str) else None,
    }


def blocked_gate_next_commands(brief: Path, repo_root: Path, out_dir: Path) -> list[str]:
    return [
        "Correct runtime gate first: use an existing git work tree for --repo-root, initialize this directory as a git work tree, or wait for an explicit supported no-git runtime mode.",
        f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/prepare_goal_bundle.py --brief {brief} --repo-root {repo_root} --out-dir {out_dir} --build-blocked-bundle --allow-blocked-readiness',
    ]


def compact_remediation(remediation: object) -> dict:
    if not isinstance(remediation, dict):
        return {}
    actions = []
    for item in remediation.get("actions", []):
        if not isinstance(item, dict):
            continue
        actions.append(
            {
                "field": item.get("field"),
                "action": item.get("action"),
                "from": item.get("from"),
                "to": item.get("to"),
            }
        )
    status = remediation.get("status")
    if not isinstance(status, str) or not status:
        status = "remediated" if actions else "not_needed"
    result = {
        "status": status,
        "action_count": len(actions),
        "actions": actions,
    }
    for key in ["output_path", "reason"]:
        if remediation.get(key) is not None:
            result[key] = remediation.get(key)
    return result


def route_availability_verified(candidate: dict | None) -> bool:
    if not isinstance(candidate, dict) or not candidate.get("selected_check_path"):
        return False
    path = Path(str(candidate["selected_check_path"]))
    if not path.exists():
        return False
    check = read_json(path)
    summary = check.get("summary") if isinstance(check.get("summary"), dict) else {}
    if summary.get("route_verification_status") == "routes_verified":
        return True
    accepted = summary.get("accepted_route_count")
    return isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0


def selected_candidate_from_selection(selection: dict) -> dict | None:
    candidates = selection.get("candidates") if isinstance(selection.get("candidates"), list) else []
    index = selection.get("selected_index")
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(candidates):
        candidate = candidates[index]
        return candidate if isinstance(candidate, dict) else None
    return None


def selected_config_summary(candidate: dict | None) -> dict:
    if not isinstance(candidate, dict):
        return {}
    config_path = Path(str(candidate.get("selected_config_path"))) if candidate.get("selected_config_path") else None
    check_path = Path(str(candidate.get("selected_check_path"))) if candidate.get("selected_check_path") else None
    return {
        "selected_config_path": config_path.as_posix() if config_path else None,
        "selected_config_sha256": sha256_file(config_path),
        "selected_check_path": check_path.as_posix() if check_path else None,
        "selected_check_sha256": sha256_file(check_path),
        "selection_reason": candidate.get("selection_reason"),
        "selection_kind": "remediated_derivative" if candidate.get("remediated_passed") else "original",
        "eligible": candidate.get("eligible"),
        "remediated_passed": candidate.get("remediated_passed"),
    }


def compact_config_selection(selection: dict) -> dict:
    candidate = selected_candidate_from_selection(selection)
    result = {
        "status": selection.get("status"),
        "selection_path": selection.get("selection_path"),
        "selected_index": selection.get("selected_index"),
        "candidate_count": len(selection.get("candidates") or []),
        "candidate_audit_mode": selection.get("candidate_audit_mode"),
        "route_model_availability_verified": selection.get("route_model_availability_verified"),
        "reason": selection.get("reason"),
    }
    result.update(selected_config_summary(candidate))
    return result


def brief_requests_debug(brief: dict) -> bool:
    if brief.get("debug_telemetry") is True:
        return True
    if brief.get("telemetry_mode") == "debug":
        return True
    policy = brief.get("telemetry_policy")
    return isinstance(policy, dict) and policy.get("mode") == "debug"


def compact_readiness(readiness: dict) -> dict:
    prompt_size = readiness.get("prompt_size_report") if isinstance(readiness.get("prompt_size_report"), dict) else {}
    artifact_size = readiness.get("artifact_size_report") if isinstance(readiness.get("artifact_size_report"), dict) else {}
    return {
        "status": readiness.get("status"),
        "launch_allowed": readiness.get("launch_allowed"),
        "launch_blockers": readiness.get("launch_blockers", []),
        "runtime_gate": readiness.get("runtime_gate", {}),
        "repair_gate": readiness.get("repair_gate", {}),
        "lint_status": readiness.get("lint_status", {}),
        "verified_routes": readiness.get("verified_routes", {}),
        "warnings": readiness.get("warnings", []),
        "cleanup_plan": readiness.get("cleanup_plan", {}),
        "branch_utilization": readiness.get("branch_utilization", {}),
        "prompt_size_summary": {
            "total_chars": prompt_size.get("total_prompt_chars", prompt_size.get("total_chars")),
            "approx_total_tokens": prompt_size.get("approx_total_tokens"),
            "max_single_prompt_chars": prompt_size.get("max_single_prompt_chars"),
            "max_prompt_chars_per_file": prompt_size.get("max_prompt_chars_per_file"),
            "per_file_min_prompt_char_margin": prompt_size.get("per_file_min_prompt_char_margin"),
        },
        "artifact_size_summary": {
            "bundle_surface_chars": artifact_size.get("bundle_surface_chars"),
            "bundle_surface_approx_tokens": artifact_size.get("bundle_surface_approx_tokens"),
            "machine_artifact_chars": artifact_size.get("machine_artifact_chars"),
            "machine_artifact_approx_tokens": artifact_size.get("machine_artifact_approx_tokens"),
            "runtime_contract_chars": artifact_size.get("runtime_contract_chars"),
            "runtime_contract_approx_tokens": artifact_size.get("runtime_contract_approx_tokens"),
        },
    }


def load_bootloader_renderer():
    path = SKILLS_ROOT / "goal-preflight" / "scripts" / "render_goal_bootloader.py"
    spec = importlib.util.spec_from_file_location("goal_preflight_render_goal_bootloader", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load bootloader renderer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update_result_readiness_fields(result: dict, readiness_data: dict, *, verbose: bool) -> None:
    result["readiness_status"] = readiness_data.get("status")
    result["launch_allowed"] = readiness_data.get("launch_allowed")
    result["readiness"] = compact_readiness(readiness_data)
    result["next_commands"] = readiness_data.get("next_commands", [])
    if verbose:
        result["readiness_full"] = readiness_data


def artifact_chars_by_path(readiness_data: dict) -> dict[str, int]:
    report = readiness_data.get("artifact_size_report") if isinstance(readiness_data.get("artifact_size_report"), dict) else {}
    entries = report.get("machine_artifacts") if isinstance(report.get("machine_artifacts"), list) else []
    result: dict[str, int] = {}
    for item in entries:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("chars"), int):
            result[item["path"]] = item["chars"]
    return result


def readiness_size_report_matches_files(readiness_data: dict, bundle_dir: Path) -> bool:
    reported = artifact_chars_by_path(readiness_data)
    checked = False
    for rel_path in ["preflight.pipeline.json", "readiness.json"]:
        path = bundle_dir / rel_path
        if not path.exists():
            continue
        checked = True
        if reported.get(rel_path) != len(path.read_text(encoding="utf-8")):
            return False
    return checked


def refresh_final_readiness_sizes(out_dir: Path, repo_root: Path, output_path: Path, result: dict, *, verbose: bool) -> dict:
    readiness_path = out_dir / "readiness.json"
    if not readiness_path.exists():
        return {"status": "missing"}
    renderer = load_bootloader_renderer()
    readiness_data: dict = read_json(readiness_path)
    for _ in range(6):
        readiness_text = renderer.render_readiness_json(out_dir, repo_root=repo_root)
        readiness_path.write_text(readiness_text, encoding="utf-8")
        readiness_data = json.loads(readiness_text)
        update_result_readiness_fields(result, readiness_data, verbose=verbose)
        write_json(output_path, result)
        if readiness_size_report_matches_files(readiness_data, out_dir):
            return readiness_data
    return readiness_data


def update_preflight_report(out_dir: Path, readiness: dict, lint_path: Path, repair_path: Path, result_kind: str) -> None:
    report_path = out_dir / "PREFLIGHT_REPORT.md"
    if not report_path.exists():
        return
    text = report_path.read_text(encoding="utf-8").rstrip()
    marker = "\nFinal pipeline state:\n"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip()
    lint_payload = read_json(lint_path) if lint_path.exists() else {}
    lint_status = lint_payload.get("schema_lint_status") or lint_payload.get("status") or "missing"
    repair_status = read_json(repair_path).get("status") if repair_path.exists() else "missing"
    readiness_status = readiness.get("status")
    launch_allowed = readiness.get("launch_allowed")
    blockers = readiness.get("launch_blockers") if isinstance(readiness.get("launch_blockers"), list) else []
    final_lines = [
        "",
        "Final pipeline state:",
        f"- Bundle lint: {lint_status}",
        f"- Repair gate: {repair_status}",
        f"- Readiness: {readiness_status}",
        f"- Result kind: {result_kind}",
        f"- Launch allowed: {str(bool(launch_allowed)).lower()}",
    ]
    if blockers:
        final_lines.append(f"- Launch blockers: {'; '.join(str(item) for item in blockers)}")
    report_path.write_text(text + "\n".join(final_lines) + "\n", encoding="utf-8")


def candidate_configs(brief: Path, repo_root: Path, out_dir: Path, explicit: list[str]) -> list[Path]:
    if explicit:
        return [resolve_absolute_path(value, "--goal-config", must_exist=True) for value in explicit]
    roots = []
    for root in [brief.parent, repo_root, out_dir.parent]:
        if root not in roots:
            roots.append(root)
    names = ("goal.preflight.config.json", "goal.config.json")
    paths: list[Path] = []
    for root in roots:
        for name in names:
            path = root / name
            if path.is_file() and path not in paths:
                paths.append(path)
    return paths


def check_config_candidate(config: Path, check_dir: Path, explicit_check: Path | None = None) -> dict:
    check_script = SKILLS_ROOT / "goal-config" / "scripts" / "check_goal_config.py"
    stem = safe_name(config)
    original_report = check_dir / f"{stem}.preflight-check.json"
    remediated_config = check_dir / f"{stem}.remediated.json"
    reusable_check = find_reusable_route_verified_check(config, explicit_check, check_dir)
    command = [
        sys.executable,
        check_script.as_posix(),
        "--config",
        config.as_posix(),
        "--for-preflight",
        "--smoke",
        "--stdout",
        "none",
        "--output",
        original_report.as_posix(),
        "--remediated-output",
        remediated_config.as_posix(),
    ]
    start = time.perf_counter()
    result = run(command)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    failure_output = "\n".join(item for item in [result.stdout, result.stderr] if item)
    original = read_json(original_report) if original_report.exists() else {"status": "failed", "failures": [failure_output]}
    candidate = {
        "config_path": config.as_posix(),
        "original_check_path": original_report.as_posix(),
        "original_status": original.get("status"),
        "original_failures": original.get("failures", []),
        "remediation": compact_remediation(original.get("remediation", {})),
        "eligible": False,
        "remediated_passed": False,
        "selected": False,
        "selected_config_path": None,
        "selected_check_path": None,
        "selection_reason": None,
        "original_check_telemetry": subprocess_telemetry(command, result, elapsed_ms),
    }
    if result.returncode == 0 and original.get("status") == "pass":
        selected_check_path = original_report
        selection_reason = "original config passed preflight compatibility"
        if reusable_check is not None:
            selected_check_path = reusable_check
            selection_reason = (
                "original config passed preflight compatibility "
                "with route verification reused from explicit/colocated smoke evidence"
            )
        candidate.update(
            {
                "selected": True,
                "eligible": True,
                "selected_config_path": config.as_posix(),
                "selected_check_path": selected_check_path.as_posix(),
                "selection_reason": selection_reason,
            }
        )
        return candidate

    if remediated_config.exists() and original.get("remediation", {}).get("actions"):
        remediated_report = check_dir / f"{stem}.remediated.preflight-check.json"
        remediated_command = [
            sys.executable,
            check_script.as_posix(),
            "--config",
            remediated_config.as_posix(),
            "--for-preflight",
            "--smoke",
            "--stdout",
            "none",
            "--output",
            remediated_report.as_posix(),
        ]
        start = time.perf_counter()
        remediated_result = run(remediated_command)
        remediated_elapsed_ms = int((time.perf_counter() - start) * 1000)
        remediated_failure_output = "\n".join(item for item in [remediated_result.stdout, remediated_result.stderr] if item)
        remediated = read_json(remediated_report) if remediated_report.exists() else {"status": "failed", "failures": [remediated_failure_output]}
        candidate["remediated_config_path"] = remediated_config.as_posix()
        candidate["remediated_check_path"] = remediated_report.as_posix()
        candidate["remediated_status"] = remediated.get("status")
        candidate["remediated_failures"] = remediated.get("failures", [])
        candidate["remediated_check_telemetry"] = subprocess_telemetry(remediated_command, remediated_result, remediated_elapsed_ms)
        if remediated_result.returncode == 0 and remediated.get("status") == "pass":
            candidate.update(
                {
                    "selected": True,
                    "eligible": True,
                    "remediated_passed": True,
                    "selected_config_path": remediated_config.as_posix(),
                    "selected_check_path": remediated_report.as_posix(),
                    "selection_reason": "remediated config passed preflight compatibility",
                }
            )
    return candidate


def select_config(
    brief: Path,
    repo_root: Path,
    out_dir: Path,
    explicit: list[str],
    skip: bool,
    *,
    audit_all: bool = False,
    explicit_check: Path | None = None,
) -> dict:
    selection_path = out_dir / "goal-config-selection.json"
    if skip:
        selection = {
            "status": "skipped",
            "selected_index": None,
            "candidates": [],
            "candidate_audit_mode": "skipped",
            "reason": "--no-goal-config",
            "selection_path": selection_path.as_posix(),
        }
        write_json(selection_path, selection)
        return selection
    check_dir = out_dir / "config-checks"
    candidates = []
    for path in candidate_configs(brief, repo_root, out_dir, explicit):
        candidate = check_config_candidate(path, check_dir, explicit_check=explicit_check)
        candidates.append(candidate)
        if candidate.get("eligible") and not audit_all:
            break
    selected_index = next((index for index, item in enumerate(candidates) if item.get("eligible")), None)
    for index, item in enumerate(candidates):
        item["selected"] = selected_index == index
    selected = candidates[selected_index] if selected_index is not None else None
    selected_summary = selected_config_summary(selected)
    selection = {
        "status": "pass" if selected else "not_selected",
        "selected_index": selected_index,
        **selected_summary,
        "candidates": candidates,
        "candidate_audit_mode": "full" if audit_all else "first_compatible",
        "selection_path": selection_path.as_posix(),
        "route_model_availability_verified": route_availability_verified(selected),
        "reason": "no compatible config candidates found" if not selected else selected.get("selection_reason"),
    }
    write_json(selection_path, selection)
    return selection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--goal-config", action="append", default=[], help="Candidate config path. Repeat to control selection order.")
    parser.add_argument(
        "--goal-config-check",
        help="Optional passing check report to use when a supplied goal-config has a route-verified smoke/discovery file.",
    )
    parser.add_argument("--no-goal-config", action="store_true", help="Do not auto-detect or embed a goal config.")
    parser.add_argument("--allow-blocked-readiness", action="store_true", help="Return zero even when readiness is blocked.")
    parser.add_argument(
        "--build-blocked-bundle",
        action="store_true",
        help="Continue past an early runtime gate blocker and build an inspection-only bundle.",
    )
    parser.add_argument("--json", action="store_true", help="Print pipeline JSON to stdout.")
    parser.add_argument("--verbose", action="store_true", help="Embed full config-selection and readiness payloads in the pipeline result.")
    parser.add_argument("--output", help="Write pipeline result JSON. Defaults to <bundle>/preflight.pipeline.json.")
    args = parser.parse_args()

    brief = resolve_absolute_path(args.brief, "--brief", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    out_dir.mkdir(parents=True, exist_ok=True)

    commands: list[dict] = []

    brief_lint = out_dir / "preflight.brief.lint.json"
    brief_lint_command = [
        sys.executable,
        (SKILLS_ROOT / "goal-preflight" / "scripts" / "lint_preflight_brief.py").as_posix(),
        "--brief",
        brief.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--json",
        "--output",
        brief_lint.as_posix(),
    ]
    brief_lint_result, record = run_tracked_phase("brief_lint", brief_lint_command, out_dir, brief_lint)
    commands.append(record)
    if brief_lint_result.returncode != 0:
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else out_dir / "preflight.pipeline.json"
        result = {
            "status": "failed",
            "phase": "brief_lint",
            "bundle_dir": out_dir.as_posix(),
            "brief_lint_path": brief_lint.as_posix(),
            "top_defects": top_defects_from_report(brief_lint),
            "commands": commands,
        }
        write_json(output_path, result)
        emit_failure_summary(
            phase="brief_lint",
            pipeline_path=output_path,
            reports={"brief_lint_path": brief_lint},
            stdout=brief_lint_result.stdout,
            stderr=brief_lint_result.stderr,
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    brief_data = read_json(brief)
    before = artifact_snapshot(out_dir)
    start = time.perf_counter()
    runtime_gate = runtime_gate_precheck(repo_root, brief_data)
    commands.append(
        phase_record(
            "runtime_gate",
            returncode=0 if runtime_gate["status"] == "pass" else 2,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            before=before,
            after=artifact_snapshot(out_dir),
        )
    )
    if runtime_gate["status"] != "pass" and not args.build_blocked_bundle:
        result = {
            "schema_version": 1,
            "status": "blocked",
            "result_kind": "blocked_runtime_gate_preflight",
            "usable_bundle": False,
            "blocked_reason": runtime_gate.get("reason"),
            "bundle_dir": out_dir.as_posix(),
            "brief_lint_path": brief_lint.as_posix(),
            "runtime_gate": runtime_gate,
            "launch_allowed": False,
            "next_commands": blocked_gate_next_commands(brief, repo_root, out_dir),
            "commands": commands,
        }
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else out_dir / "preflight.pipeline.json"
        write_json(output_path, result)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(output_path)
        return 0 if args.allow_blocked_readiness else 1

    before = artifact_snapshot(out_dir)
    start = time.perf_counter()
    goal_config_check = (
        resolve_absolute_path(args.goal_config_check, "--goal-config-check", must_exist=True)
        if args.goal_config_check
        else None
    )
    if goal_config_check is not None and not args.goal_config:
        raise SystemExit("--goal-config-check requires --goal-config")
    config_selection = select_config(
        brief,
        repo_root,
        out_dir,
        args.goal_config,
        args.no_goal_config,
        audit_all=args.verbose or brief_requests_debug(brief_data),
        explicit_check=goal_config_check,
    )
    commands.append(
        phase_record(
            "config_selection",
            returncode=0 if config_selection.get("status") in {"pass", "skipped"} else 1,
            output=out_dir / "goal-config-selection.json",
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            before=before,
            after=artifact_snapshot(out_dir),
        )
    )
    selected = selected_candidate_from_selection(config_selection)

    create_command = [
        sys.executable,
        (SKILLS_ROOT / "goal-preflight" / "scripts" / "create_goal_bundle.py").as_posix(),
        "--brief",
        brief.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--out-dir",
        out_dir.as_posix(),
        "--json",
        "--output",
        (out_dir / "create-bundle-result.json").as_posix(),
    ]
    if selected:
        create_command.extend(["--goal-config", str(selected["selected_config_path"]), "--goal-config-check", str(selected["selected_check_path"])])
    create_result, record = run_tracked_phase("create_bundle", create_command, out_dir, out_dir / "create-bundle-result.json")
    commands.append(record)
    if create_result.returncode != 0:
        output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else out_dir / "preflight.pipeline.json"
        result = {
            "status": "failed",
            "phase": "create_bundle",
            "bundle_dir": out_dir.as_posix(),
            "config_selection": compact_config_selection(config_selection),
            "stdout": create_result.stdout,
            "stderr": create_result.stderr,
            "top_output": top_output_lines(create_result.stderr, create_result.stdout),
            "commands": commands,
        }
        if args.verbose:
            result["config_selection_full"] = config_selection
        write_json(output_path, result)
        emit_failure_summary(
            phase="create_bundle",
            pipeline_path=output_path,
            reports={"create_result_path": out_dir / "create-bundle-result.json"},
            stdout=create_result.stdout,
            stderr=create_result.stderr,
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    bundle_lint = out_dir / "preflight.lint.json"
    lint_command = [
        sys.executable,
        (SKILLS_ROOT / "goal-preflight" / "scripts" / "lint_goal_bundle.py").as_posix(),
        "--bundle-dir",
        out_dir.as_posix(),
        "--json",
        "--output",
        bundle_lint.as_posix(),
    ]
    lint_result, record = run_tracked_phase("bundle_lint", lint_command, out_dir, bundle_lint)
    commands.append(record)

    repair_gate = out_dir / "repair-gate.json"
    repair_command = [
        sys.executable,
        (SKILLS_ROOT / "_goal_shared" / "scripts" / "script_only_repair_gate.py").as_posix(),
        "--manifest",
        (out_dir / "job.manifest.json").as_posix(),
        "--bundle-dir",
        out_dir.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--scope",
        "preflight",
        "--bundle-lint-report",
        bundle_lint.as_posix(),
        "--json",
        "--output",
        repair_gate.as_posix(),
    ]
    repair_result, record = run_tracked_phase("repair_gate", repair_command, out_dir, repair_gate)
    commands.append(record)

    readiness = out_dir / "readiness.json"
    readiness_command = [
        sys.executable,
        (SKILLS_ROOT / "goal-preflight" / "scripts" / "render_goal_bootloader.py").as_posix(),
        "--bundle-dir",
        out_dir.as_posix(),
        "--repo-root",
        repo_root.as_posix(),
        "--readiness",
        "--json",
        "--output",
        readiness.as_posix(),
    ]
    readiness_result, record = run_tracked_phase("readiness", readiness_command, out_dir, readiness)
    commands.append(record)
    readiness_data = read_json(readiness) if readiness.exists() else {"status": "missing"}

    status = "pass"
    result_kind = "pass"
    blocked_reason = None
    if lint_result.returncode != 0 or repair_result.returncode != 0:
        status = "blocked"
        result_kind = "blocked_artifact_gate"
    elif readiness_data.get("status") != "pass":
        status = "blocked"
        result_kind = "blocked_readiness_usable_bundle"
        runtime_gate = readiness_data.get("runtime_gate") if isinstance(readiness_data.get("runtime_gate"), dict) else {}
        blocked_reason = runtime_gate.get("reason") or "readiness status is not pass"
    update_preflight_report(out_dir, readiness_data, bundle_lint, repair_gate, result_kind)
    result = {
        "schema_version": 1,
        "status": status,
        "result_kind": result_kind,
        "usable_bundle": status == "pass" or result_kind == "blocked_readiness_usable_bundle",
        "blocked_reason": blocked_reason,
        "bundle_dir": out_dir.as_posix(),
        "bootloader_path": (out_dir / "goal-bootloader.md").as_posix(),
        "config_selection_path": (out_dir / "goal-config-selection.json").as_posix(),
        "config_selection": compact_config_selection(config_selection),
        "brief_lint_path": brief_lint.as_posix(),
        "bundle_lint_path": bundle_lint.as_posix(),
        "repair_gate_path": repair_gate.as_posix(),
        "readiness_path": readiness.as_posix(),
        "readiness_status": readiness_data.get("status"),
        "launch_allowed": readiness_data.get("launch_allowed"),
        "readiness": compact_readiness(readiness_data),
        "next_commands": readiness_data.get("next_commands", []),
        "commands": commands,
    }
    if args.verbose:
        result["config_selection_full"] = config_selection
        result["readiness_full"] = readiness_data
    output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else out_dir / "preflight.pipeline.json"
    write_json(output_path, result)
    if output_path == out_dir / "preflight.pipeline.json":
        readiness_data = refresh_final_readiness_sizes(out_dir, repo_root, output_path, result, verbose=args.verbose)
        update_preflight_report(out_dir, readiness_data, bundle_lint, repair_gate, result_kind)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(output_path)
    return 0 if status == "pass" or args.allow_blocked_readiness else 1


if __name__ == "__main__":
    raise SystemExit(main())
