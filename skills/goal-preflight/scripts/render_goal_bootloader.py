#!/usr/bin/env python3
"""Print or regenerate the final /goal bootloader text from a preflight bundle."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
from pathlib import Path


def _read_json(path: Path, label: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{label} must be an object: {path}")
    return data


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_manifest(bundle_dir: Path) -> dict:
    return _read_json(bundle_dir / "job.manifest.json", "manifest")


def _read_bootloader(bundle_dir: Path) -> str:
    path = bundle_dir / "goal-bootloader.md"
    if not path.exists():
        return ""
    return _read_text(path)


def _wave_lookup(manifest: dict) -> dict[str, dict[str, object]]:
    lookup: dict[str, dict[str, object]] = {}
    for wave in manifest.get("waves", []):
        if not isinstance(wave, dict):
            continue
        for branch_id in wave.get("branches", []):
            if isinstance(branch_id, str):
                lookup[branch_id] = wave
    return lookup


def _collect_bundle_dag(manifest: dict) -> list[dict[str, list[str] | str | int]]:
    wave_lookup = _wave_lookup(manifest)
    branches = []
    for branch in manifest.get("branches", []):
        if not isinstance(branch, dict):
            continue
        wave = wave_lookup.get(str(branch.get("id", "")), {})
        branches.append(
            {
                "id": str(branch.get("id", "<missing>")),
                "wave": str(branch.get("wave", "")),
                "dependency_level": wave.get("dependency_level", ""),
                "depends_on": sorted([str(dep) for dep in branch.get("depends_on", []) if isinstance(dep, str)]),
                "worker_cap": branch.get("max_active_worker_packets", ""),
            }
        )
    return branches


def _branch_dependency_levels(branches: list[dict]) -> dict[str, int]:
    levels: dict[str, int] = {}
    remaining = {str(branch.get("id")): branch for branch in branches if isinstance(branch, dict)}
    while remaining:
        progressed = False
        for branch_id, branch in list(remaining.items()):
            deps = [str(dep) for dep in branch.get("depends_on", []) if isinstance(dep, str)]
            if any(dep in remaining for dep in deps):
                continue
            dep_levels = [levels.get(dep, 0) for dep in deps]
            levels[branch_id] = 1 + (max(dep_levels) if dep_levels else 0)
            remaining.pop(branch_id)
            progressed = True
        if not progressed:
            for branch_id in list(remaining):
                levels[branch_id] = 1
                remaining.pop(branch_id)
    return levels


def _branch_utilization_summary(manifest: dict) -> dict[str, object]:
    branches = [branch for branch in manifest.get("branches", []) if isinstance(branch, dict)]
    max_active = manifest.get("max_active_branch_agents")
    if not isinstance(max_active, int) or isinstance(max_active, bool):
        max_active = 0
    levels = _branch_dependency_levels(branches)
    widths: dict[int, int] = {}
    for level in levels.values():
        widths[level] = widths.get(level, 0) + 1
    max_ready_width = max(widths.values(), default=0)
    initial_ready = len([branch for branch in branches if not branch.get("depends_on")])
    usable_cap = min(max_active, len(branches)) if max_active else len(branches)
    utilization_ratio = None if usable_cap <= 0 else max_ready_width / usable_cap
    return {
        "branch_count": len(branches),
        "max_active_branch_agents": max_active,
        "initial_ready_count": initial_ready,
        "max_ready_width": max_ready_width,
        "usable_branch_cap": usable_cap,
        "utilization_ratio": utilization_ratio,
        "dependency_level_widths": {str(key): value for key, value in sorted(widths.items())},
        "serial_reasons": manifest.get("parallelization", {}).get("serial_reasons", [])
        if isinstance(manifest.get("parallelization"), dict)
        else [],
    }


def _route_policy_summary(manifest: dict) -> dict[str, object]:
    routes: dict[str, object] = {}
    verified_routes = _verified_routes_summary(manifest)
    recommendations_suppressed = verified_routes.get("route_model_availability_verified") is not True
    worker_policy = manifest.get("worker_model_policy", {})
    routes["worker_recommendations_suppressed"] = recommendations_suppressed
    if recommendations_suppressed:
        routes["worker"] = []
        routes["unverified_config_aliases"] = {
            "worker": worker_policy.get("default_ladder", []),
        }
        routes["worker_recommendation_reason"] = (
            "route availability was not verified; prompts defer concrete worker alias selection until a fresh model catalog or accepted-route smoke check"
        )
    else:
        routes["worker"] = worker_policy.get("default_ladder", [])
    amender_policy = manifest.get("amender_model_policy", {})
    routes["amender"] = amender_policy.get("default_ladder", [])
    lite_policy = manifest.get("lite_model_policy", {})
    routes["lite"] = lite_policy.get("allowed_routes", [])
    review_policy = manifest.get("review_model_policy", {})
    routes["reviewer"] = review_policy.get("routes", {})
    parallelization = manifest.get("parallelization", {})
    routes["dependency_parallel_policy"] = {
        "max_active_branch_agents": parallelization.get(
            "max_active_branch_agents", manifest.get("max_active_branch_agents")
        ),
        "max_waves": parallelization.get("max_waves", 0),
        "max_branches_per_wave": parallelization.get("max_branches_per_wave", 0),
    }
    return routes


def _caps_summary(manifest: dict) -> dict[str, object]:
    parallelization = manifest.get("parallelization", {})
    branch_caps = {
        str(branch.get("id")): branch.get("max_active_worker_packets")
        for branch in manifest.get("branches", [])
        if isinstance(branch, dict) and isinstance(branch.get("id"), str)
    }
    numeric_caps = [value for value in branch_caps.values() if isinstance(value, int) and not isinstance(value, bool)]
    uniform_worker_cap = (
        numeric_caps[0]
        if numeric_caps and len(set(numeric_caps)) == 1 and len(numeric_caps) == len(branch_caps)
        else None
    )
    return {
        "max_active_branch_agents": manifest.get("max_active_branch_agents"),
        "max_active_worker_packets_default": uniform_worker_cap,
        "max_active_worker_packets_by_branch": branch_caps,
        "max_active_worker_packets_max": max(numeric_caps, default=None),
        "max_branches_per_wave": parallelization.get("max_branches_per_wave", 0),
        "max_waves": parallelization.get("max_waves", 0),
        "waves": len(manifest.get("waves", [])) if isinstance(manifest.get("waves"), list) else 0,
    }


def _lint_status(bundle_dir: Path, label: str) -> dict[str, object]:
    filename = "preflight.brief.lint.json" if label == "brief" else "preflight.lint.json"
    path = bundle_dir / filename
    if not path.exists():
        return {"label": label, "status": "missing", "path": str(path)}
    payload = _read_json(path, f"{label} lint report")
    if not isinstance(payload, dict):
        return {"label": label, "status": "invalid", "path": str(path)}
    schema_status = payload.get("schema_lint_status") or payload.get("status", "unknown")
    return {
        "label": label,
        "status": schema_status,
        "reported_status": payload.get("status", "unknown"),
        "status_kind": payload.get("status_kind"),
        "defect_count": payload.get("defect_count", len(payload.get("defects", payload.get("errors", [])) or [])),
        "defects": payload.get("defects", payload.get("errors", [])),
        "path": str(path),
    }


def _repair_gate_status(bundle_dir: Path) -> dict[str, object]:
    path = bundle_dir / "repair-gate.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    payload = _read_json(path, "repair gate report")
    status = payload.get("status")
    actions = payload.get("actions", [])
    if status not in {"pass", "blocked", "failed"}:
        status = "pass" if payload.get("decision") == "pass_no_actions" else "blocked"
    return {
        "status": status,
        "decision": payload.get("decision"),
        "actions": actions if isinstance(actions, list) else [],
        "path": str(path),
        "model_launch_allowed": payload.get("model_launch_allowed"),
        "script_repair_model_launch_allowed": payload.get("script_repair_model_launch_allowed"),
        "runtime_launch_allowed": payload.get("runtime_launch_allowed"),
        "launch_allowed": payload.get("launch_allowed"),
        "action_count": payload.get("action_count", len(payload.get("actions", []) or [])),
    }


def _resolve_repo_root(bundle_dir: Path, repo_root: Path | None, bootloader_text: str) -> Path | None:
    if repo_root is not None and repo_root.exists():
        return repo_root
    marker = "- Repository root: "
    for line in bootloader_text.splitlines():
        if line.startswith(marker):
            candidate = Path(line.removeprefix(marker).strip())
            if candidate.exists():
                return candidate
    candidate = bundle_dir.parent
    if candidate.exists() and (candidate / ".git").exists():
        return candidate
    return None


def _git_status(repo_root: Path | None) -> list[str]:
    if repo_root is None:
        return ["unavailable: cannot locate repository root"]
    try:
        process = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--short", "--branch"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        return ["unavailable: git not installed"]
    return [line for line in process.stdout.strip().splitlines()]


def _config_compatibility(manifest: dict) -> str:
    has_config = bool(manifest.get("goal_config_path") or manifest.get("goal_config_summary"))
    check_report = manifest.get("goal_config_check_summary", {})
    route_contract = manifest.get("route_contract") if isinstance(manifest.get("route_contract"), dict) else {}
    missing_worker_roles = route_contract.get("missing_worker_roles")
    if not has_config and not check_report:
        return "no goal config supplied"
    if not isinstance(check_report, dict):
        return "goal config check summary malformed"
    if check_report.get("status") == "pass":
        if isinstance(missing_worker_roles, list) and missing_worker_roles:
            return "config_schema_pass_routes_unverified"
        accepted = check_report.get("accepted_route_count")
        if isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0:
            return "goal config check pass"
        return "config_schema_pass_routes_unverified"
    return f"goal config check {check_report.get('status', 'missing')}"


def _verified_routes_summary(manifest: dict) -> dict[str, object]:
    check_report = manifest.get("goal_config_check_summary", {})
    if not isinstance(check_report, dict) or not check_report:
        return {"status": "missing", "route_model_availability_verified": False}
    summary = dict(check_report)
    accepted = summary.get("accepted_route_count")
    route_verified = isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0
    route_contract = manifest.get("route_contract") if isinstance(manifest.get("route_contract"), dict) else {}
    missing_worker_roles = route_contract.get("missing_worker_roles")
    if isinstance(missing_worker_roles, list) and missing_worker_roles:
        route_verified = False
        summary["missing_worker_roles"] = missing_worker_roles
        summary["route_contract_sha256"] = manifest.get("route_contract_sha256")
    summary["route_model_availability_verified"] = route_verified
    token_telemetry = summary.get("token_telemetry") if isinstance(summary.get("token_telemetry"), dict) else {}
    unavailable = token_telemetry.get("unavailable_routes")
    available = token_telemetry.get("available_routes")
    if route_verified and isinstance(unavailable, int) and not isinstance(unavailable, bool):
        summary["telemetry_capability_status"] = "partial" if unavailable > 0 else "complete"
    elif route_verified:
        summary["telemetry_capability_status"] = "unknown"
    else:
        summary["telemetry_capability_status"] = "not_verified"
    if (
        isinstance(available, int)
        and isinstance(unavailable, int)
        and not isinstance(available, bool)
        and not isinstance(unavailable, bool)
    ):
        total = available + unavailable
        summary["token_telemetry_coverage_ratio"] = round(available / total, 6) if total > 0 else None
    if route_verified:
        summary["route_verification_status"] = summary.get("route_verification_status") or "routes_verified"
        return summary
    if summary.get("status") == "pass":
        summary["schema_status"] = "pass"
        summary["status"] = "schema_pass_routes_not_checked"
    summary["route_verification_status"] = summary.get("route_verification_status") or summary.get("status", "missing")
    return summary


def _prompt_entry(bundle_dir: Path, relative_path: str) -> dict[str, object]:
    path = bundle_dir / relative_path
    if not path.exists():
        return {
            "path": relative_path,
            "exists": False,
            "chars": 0,
            "approx_tokens": 0,
            "line_count": 0,
        }
    text = _read_text(path)
    chars = len(text)
    return {
        "path": relative_path,
        "exists": True,
        "chars": chars,
        "approx_tokens": (chars + 3) // 4,
        "line_count": text.count("\n") + (0 if text.endswith("\n") else 1),
    }


def _prompt_char_budget(manifest: dict) -> int | None:
    for container in [
        manifest.get("goal_config_summary"),
        manifest.get("preflight_compatibility"),
    ]:
        if not isinstance(container, dict):
            continue
        effort = container.get("effort") if isinstance(container.get("effort"), dict) else {}
        value = effort.get("max_prompt_chars")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _prompt_size_report(bundle_dir: Path, manifest: dict) -> dict[str, object]:
    entries = [_prompt_entry(bundle_dir, str(manifest.get("main_prompt") or "main.prompt.md"))]
    if isinstance(manifest.get("runtime_rules_path"), str):
        entries.append(_prompt_entry(bundle_dir, str(manifest["runtime_rules_path"])))
    for branch in manifest.get("branches", []):
        if isinstance(branch, dict) and isinstance(branch.get("prompt"), str):
            entries.append(_prompt_entry(bundle_dir, branch["prompt"]))
    total_chars = sum(int(item["chars"]) for item in entries)
    budget = _prompt_char_budget(manifest)
    if budget is not None:
        for entry in entries:
            entry["max_prompt_chars"] = budget
            entry["prompt_char_margin"] = budget - int(entry["chars"])
    max_single_prompt_chars = max((int(item["chars"]) for item in entries), default=0)
    per_file_min_margin = (
        None if budget is None else min((budget - int(item["chars"]) for item in entries), default=budget)
    )
    branch_prompt_entries = [item for item in entries if str(item.get("path", "")).startswith("branches/")]
    runtime_rules_present = (
        isinstance(manifest.get("runtime_rules_path"), str)
        and (bundle_dir / str(manifest["runtime_rules_path"])).exists()
    )
    repeated_sections = {
        "branch_prompt_count": len(branch_prompt_entries),
        "shared_runtime_rules_extracted": runtime_rules_present,
        "shared_runtime_boilerplate_sections": 0 if runtime_rules_present else len(branch_prompt_entries) * 5,
        "counted_sections": [
            "Worker Parallelism",
            "Worker Model Routing",
            "Lite Advisors",
            "Reviewer Requirement",
            "Bootstrap Requirement",
        ],
    }
    return {
        "files": entries,
        "total_chars": total_chars,
        "total_prompt_chars": total_chars,
        "approx_total_tokens": (total_chars + 3) // 4,
        "max_single_prompt_chars": max_single_prompt_chars,
        "max_prompt_chars_per_file": budget,
        "per_file_min_prompt_char_margin": per_file_min_margin,
        "prompt_char_margin_basis": "minimum per-file margin against max_prompt_chars_per_file; total prompt chars are reported separately",
        "duplicated_section_counts": repeated_sections,
    }


def _artifact_entry(bundle_dir: Path, relative_path: str) -> dict[str, object]:
    path = bundle_dir / relative_path
    if not path.exists():
        return {
            "path": relative_path,
            "exists": False,
            "chars": 0,
            "approx_tokens": 0,
            "line_count": 0,
        }
    text = _read_text(path)
    chars = len(text)
    return {
        "path": relative_path,
        "exists": True,
        "chars": chars,
        "approx_tokens": (chars + 3) // 4,
        "line_count": text.count("\n") + (0 if text.endswith("\n") else 1),
    }


def _artifact_size_report(bundle_dir: Path, manifest: dict, prompt_report: dict[str, object]) -> dict[str, object]:
    prompt_paths = [
        str(item.get("path")) for item in prompt_report.get("files", []) if isinstance(item, dict) and item.get("path")
    ]
    machine_paths = [
        "job.manifest.json",
        "goal-config-selection.json",
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "create-bundle-result.json",
    ]
    if manifest.get("goal_config_path"):
        machine_paths.extend(["goal.config.json", "goal-config.check.json"])
    optional_machine_paths = ["preflight.pipeline.json", "readiness.json"]
    present_optional_machine_paths = [path for path in optional_machine_paths if (bundle_dir / path).exists()]
    machine_entries = [_artifact_entry(bundle_dir, path) for path in machine_paths]
    machine_entries.extend(_artifact_entry(bundle_dir, path) for path in present_optional_machine_paths)
    prompt_entries = [_artifact_entry(bundle_dir, path) for path in prompt_paths]
    machine_chars = sum(int(item["chars"]) for item in machine_entries)
    prompt_chars = sum(int(item["chars"]) for item in prompt_entries)
    runtime_contract_paths = set(prompt_paths + ["job.manifest.json"])
    runtime_contract_chars = sum(int(_artifact_entry(bundle_dir, path)["chars"]) for path in runtime_contract_paths)
    return {
        "prompt_files": prompt_entries,
        "machine_artifacts": machine_entries,
        "prompt_file_chars": prompt_chars,
        "prompt_file_approx_tokens": (prompt_chars + 3) // 4,
        "machine_artifact_chars": machine_chars,
        "machine_artifact_approx_tokens": (machine_chars + 3) // 4,
        "runtime_contract_chars": runtime_contract_chars,
        "runtime_contract_approx_tokens": (runtime_contract_chars + 3) // 4,
        "bundle_surface_chars": prompt_chars + machine_chars,
        "bundle_surface_approx_tokens": (prompt_chars + machine_chars + 3) // 4,
        "optional_machine_artifacts": {
            "counted_when_present": optional_machine_paths,
            "present": present_optional_machine_paths,
            "absent_not_counted": [
                path for path in optional_machine_paths if path not in present_optional_machine_paths
            ],
        },
        "note": "prompt_size_report covers prompt files only; artifact_size_report includes selected machine artifacts and the manifest runtime contract",
    }


def _config_status_blocks_launch(goal_config_status: str) -> bool:
    return goal_config_status not in {
        "no goal config supplied",
        "goal config check pass",
    }


def _repo_runtime_gate(manifest: dict) -> dict[str, object]:
    repo_status = manifest.get("repo_status") if isinstance(manifest.get("repo_status"), dict) else {}
    if repo_status.get("repo_is_git") is False:
        return {
            "status": "blocked",
            "reason": "directory mode is unsupported for runtime branch/worktree orchestration; use a git work tree or add an explicit supported no-git runtime mode",
        }
    if repo_status.get("base_ref_status") == "missing":
        return {"status": "blocked", "reason": f"base_ref does not exist: {repo_status.get('base_ref')}"}
    return {"status": "pass", "reason": "git worktree/base_ref gate passed or was not required"}


def _launch_blockers(
    *,
    goal_config_status: str,
    manifest: dict,
    verified_routes: dict[str, object],
    lint_brief: dict[str, object],
    lint_bundle: dict[str, object],
    repair_gate: dict[str, object],
    runtime_gate: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    if _config_status_blocks_launch(goal_config_status):
        blockers.append(goal_config_status)
    token_telemetry = (
        verified_routes.get("token_telemetry") if isinstance(verified_routes.get("token_telemetry"), dict) else {}
    )
    unavailable = token_telemetry.get("unavailable_routes")
    waiver = manifest.get("route_policy_degraded_telemetry_waiver")
    waiver_accepted = (
        isinstance(waiver, dict)
        and waiver.get("accepted") is True
        and isinstance(waiver.get("reason"), str)
        and waiver.get("reason", "").strip()
    )
    if (
        verified_routes.get("route_model_availability_verified") is True
        and isinstance(unavailable, int)
        and not isinstance(unavailable, bool)
        and unavailable > 0
        and not waiver_accepted
    ):
        blockers.append("route_token_telemetry_degraded_without_waiver")
    if lint_brief["status"] not in {"pass", "missing"}:
        blockers.append(f"brief lint {lint_brief['status']}")
    if lint_bundle["status"] != "pass":
        blockers.append(f"bundle lint {lint_bundle['status']}")
    if repair_gate["status"] != "pass":
        blockers.append(f"repair gate {repair_gate['status']}")
    if runtime_gate["status"] != "pass":
        blockers.append(f"runtime gate blocked: {runtime_gate.get('reason')}")
    return blockers


def _readiness_warnings(
    manifest: dict,
    *,
    bundle_dir: Path,
    repo_root: Path | None,
    goal_config_status: str,
    verified_routes: dict[str, object],
    utilization: dict[str, object],
) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    for warning in manifest.get("preflight_warnings", []):
        if isinstance(warning, dict) and _manifest_warning_still_applies(
            warning, bundle_dir=bundle_dir, repo_root=repo_root
        ):
            warnings.append(warning)
    if verified_routes.get("route_model_availability_verified") is not True:
        warnings.append(
            {
                "code": "route_availability_unverified",
                "severity": "warning",
                "message": f"{goal_config_status}; worker route alias recommendations are deferred until accepted routes are verified.",
            }
        )
    token_telemetry = (
        verified_routes.get("token_telemetry") if isinstance(verified_routes.get("token_telemetry"), dict) else {}
    )
    unavailable = token_telemetry.get("unavailable_routes")
    if (
        verified_routes.get("route_model_availability_verified") is True
        and isinstance(unavailable, int)
        and not isinstance(unavailable, bool)
        and unavailable > 0
    ):
        warnings.append(
            {
                "code": "route_token_telemetry_degraded",
                "severity": "warning",
                "message": f"{unavailable} verified route(s) lack token telemetry; runtime launch requires route_policy_degraded_telemetry_waiver.accepted=true or telemetry-capable route pruning.",
            }
        )
    ratio = utilization.get("utilization_ratio")
    usable_cap = utilization.get("usable_branch_cap")
    max_ready = utilization.get("max_ready_width")
    if isinstance(ratio, (int, float)) and ratio < 1 and isinstance(usable_cap, int) and usable_cap > 1:
        warnings.append(
            {
                "code": "low_branch_parallelism_utilization",
                "severity": "warning",
                "message": f"Branch dependency DAG reaches max ready width {max_ready} under usable cap {usable_cap}; branch agents may be underutilized.",
                "utilization_ratio": ratio,
            }
        )
    rationale = ""
    parallelization = manifest.get("parallelization")
    if isinstance(parallelization, dict) and isinstance(parallelization.get("parallelization_rationale"), str):
        rationale = parallelization["parallelization_rationale"].lower()
    if (
        isinstance(max_ready, int)
        and max_ready <= 1
        and any(marker in rationale for marker in ("concurrent", "parallel", "saturat"))
        and len(manifest.get("branches", [])) > 1
    ):
        warnings.append(
            {
                "code": "parallelization_rationale_dag_mismatch",
                "severity": "warning",
                "message": "Parallelization rationale describes concurrent/saturated execution, but the branch dependency DAG exposes at most one ready branch at a time.",
            }
        )
    return warnings


def _manifest_warning_still_applies(warning: dict[str, object], *, bundle_dir: Path, repo_root: Path | None) -> bool:
    if warning.get("code") != "bundle_inside_git_worktree_not_ignored":
        return True
    if repo_root is None:
        return True
    warning_path = warning.get("path")
    candidate = str(warning_path).strip() if isinstance(warning_path, str) else ""
    try:
        relative = Path(candidate) if candidate else bundle_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return True
    return not (
        _git_path_is_ignored(repo_root, relative) or _git_path_is_ignored(repo_root, relative / "job.manifest.json")
    )


def _git_path_is_ignored(repo_root: Path, relative_path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_root.as_posix(), "check-ignore", "-q", "--", relative_path.as_posix()],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _readiness_next_commands(
    bundle_dir: Path,
    manifest_path: Path,
    repo_root: Path | None,
    *,
    lint_bundle_status: str,
    repair_gate_status: str,
    runtime_gate: dict[str, object],
    launch_blockers: list[str],
) -> list[str]:
    commands: list[str] = []
    if lint_bundle_status != "pass":
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/lint_goal_bundle.py --bundle-dir {bundle_dir}'
        )
    if repair_gate_status != "pass":
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/_goal_shared/scripts/script_only_repair_gate.py --manifest {manifest_path} --bundle-dir {bundle_dir} --repo-root {repo_root or "<repo-root>"} --scope preflight --json --output {bundle_dir / "repair-gate.json"}'
        )
    if runtime_gate.get("status") != "pass":
        commands.append(
            "Correct runtime gate: use an existing git work tree for --repo-root, run git init before preflight/runtime, or wait for an explicit supported no-git runtime mode; do not launch /goal from this bundle while readiness is blocked."
        )
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir} --repo-root {repo_root or "<repo-root>"} --readiness --json'
        )
        return commands
    if "bootloader launch handoff stale" in launch_blockers:
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir} --repo-root {repo_root or "<repo-root>"} --write'
        )
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir} --repo-root {repo_root or "<repo-root>"} --readiness --json'
        )
        return commands
    if launch_blockers:
        blockers = ", ".join(sorted(set(launch_blockers)))
        commands.append(f"Launch blockers remain: {blockers}. Do not launch /goal until launch blockers are cleared.")
        commands.append(
            f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir} --repo-root {repo_root or "<repo-root>"} --readiness --json'
        )
        return commands
    commands.append(
        f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir}'
    )
    if lint_bundle_status == "pass" and repair_gate_status == "pass" and runtime_gate.get("status") == "pass":
        commands.append("/goal")
    return commands


def _cleanup_plan(bundle_dir: Path, repo_root: Path | None, warnings: list[dict[str, object]]) -> dict[str, object]:
    warning_paths = [
        str(warning.get("path"))
        for warning in warnings
        if isinstance(warning, dict)
        and warning.get("code") == "bundle_inside_git_worktree_not_ignored"
        and isinstance(warning.get("path"), str)
        and str(warning.get("path")).strip()
    ]
    bundle_rel = warning_paths[0] if warning_paths else None
    runtime_dirs = ["audit", "workers", "research", "reviewers", "lite", "schedulers", "amendments", "branches"]
    generated_artifacts = [
        "job.manifest.json",
        "main.prompt.md",
        "goal-bootloader.md",
        "runtime-rules.md",
        "preflight.brief.lint.json",
        "preflight.lint.json",
        "repair-gate.json",
        "readiness.json",
        "preflight.pipeline.json",
        "PREFLIGHT_REPORT.md",
        "telemetry.summary.json",
        "orchestration.state.json",
        "resume.report.json",
        "goal-config-selection.json",
    ]
    config_artifacts = ["goal.config.json", "goal-config.check.json"]
    preserve_config = [name for name in config_artifacts if (bundle_dir / name).exists()]
    commands: list[str] = []
    if bundle_rel:
        commands.append(f"printf '%s\\n' {shlex.quote(bundle_rel + '/')} >> .git/info/exclude")
    if preserve_config:
        # Remove disposable artifacts individually so the preserved config
        # artifacts stay in place; a blanket `rm -rf` of the bundle dir would
        # delete the very files the plan says it preserves.
        for dirname in runtime_dirs:
            commands.append(f"rm -rf {shlex.quote((bundle_dir / dirname).as_posix())}")
        for artifact in generated_artifacts:
            commands.append(f"rm -f {shlex.quote((bundle_dir / artifact).as_posix())}")
        note = (
            "Generated bundle/runtime artifacts are removed individually so the "
            "preserved config artifacts stay in place; delete the bundle directory "
            "manually once you no longer need them."
        )
    else:
        commands.append(f"rm -rf {shlex.quote(bundle_dir.as_posix())}")
        note = (
            "Generated bundle/runtime artifacts are disposable after the run report "
            "is captured; no config artifacts are present to preserve."
        )
    return {
        "status": "needs_ignore_or_cleanup" if bundle_rel else "ok",
        "bundle_inside_git_worktree_not_ignored": bool(bundle_rel),
        "bundle_repo_relative_path": bundle_rel,
        "repository_root": str(repo_root) if repo_root else None,
        "generated_artifact_roots": runtime_dirs,
        "generated_artifacts": generated_artifacts,
        "config_artifacts": config_artifacts,
        "preserve_config_artifacts": preserve_config,
        "suggested_ignore_patterns": [bundle_rel + "/"] if bundle_rel else [],
        "cleanup_commands": commands,
        "note": note,
    }


def render_readiness(bundle_dir: Path, repo_root: Path | None = None) -> str:
    manifest = _load_manifest(bundle_dir)
    bootloader_text = _read_bootloader(bundle_dir)
    bootloader_path = bundle_dir / "goal-bootloader.md"
    resolved_repo_root = _resolve_repo_root(bundle_dir, repo_root, bootloader_text)
    telemetry_mode = manifest.get("telemetry_policy", {}).get("mode", "standard")
    status = "pass"
    goal_config_status = _config_compatibility(manifest)
    lint_brief = _lint_status(bundle_dir, "brief")
    lint_bundle = _lint_status(bundle_dir, "bundle")
    repair_gate = _repair_gate_status(bundle_dir)
    runtime_gate = _repo_runtime_gate(manifest)
    verified_routes = _verified_routes_summary(manifest)
    launch_blockers = _bootloader_launch_blockers(
        _launch_blockers(
            goal_config_status=goal_config_status,
            manifest=manifest,
            verified_routes=verified_routes,
            lint_brief=lint_brief,
            lint_bundle=lint_bundle,
            repair_gate=repair_gate,
            runtime_gate=runtime_gate,
        ),
        lint_bundle=lint_bundle,
        repair_gate=repair_gate,
        defer_missing_reports=False,
    )
    if not launch_blockers and _bootloader_launch_handoff_is_stale(bootloader_text, lint_bundle, repair_gate):
        launch_blockers = ["bootloader launch handoff stale"]
    if launch_blockers:
        status = "blocked"
    launch_allowed = status == "pass" and not launch_blockers

    branch_dag = _collect_bundle_dag(manifest)
    route_policy = _route_policy_summary(manifest)
    caps = _caps_summary(manifest)
    utilization = _branch_utilization_summary(manifest)
    warnings = _readiness_warnings(
        manifest,
        bundle_dir=bundle_dir,
        repo_root=resolved_repo_root,
        goal_config_status=goal_config_status,
        verified_routes=verified_routes,
        utilization=utilization,
    )
    cleanup_plan = _cleanup_plan(bundle_dir, resolved_repo_root, warnings)
    prompt_size = _prompt_size_report(bundle_dir, manifest)
    artifact_size = _artifact_size_report(bundle_dir, manifest, prompt_size)
    manifest_path = bundle_dir / "job.manifest.json"
    next_command = _readiness_next_commands(
        bundle_dir,
        manifest_path,
        resolved_repo_root,
        lint_bundle_status=str(lint_bundle["status"]),
        repair_gate_status=str(repair_gate["status"]),
        runtime_gate=runtime_gate,
        launch_blockers=launch_blockers,
    )

    lines = [
        "Compact readiness summary:",
        f"status={status}",
        f"launch_allowed={str(launch_allowed).lower()}",
        f"launch_blockers={json.dumps(launch_blockers, sort_keys=True)}",
        f"bundle={bundle_dir}",
        f"bootloader={bootloader_path}",
        f"bootloader_exists={bootloader_path.exists()}",
        f"config_compatibility={goal_config_status}",
        f"telemetry_mode={telemetry_mode}",
        f"caps: max_active_branch_agents={caps['max_active_branch_agents']}, max_waves={caps['max_waves']}, max_branches_per_wave={caps['max_branches_per_wave']}, waves={caps['waves']}",
        f"branch_utilization={json.dumps(utilization, sort_keys=True)}",
        f"route_policy={json.dumps(route_policy, sort_keys=True)}",
        f"verified_routes={json.dumps(verified_routes, sort_keys=True)}",
        f"warnings={json.dumps(warnings, sort_keys=True)}",
        f"cleanup_plan={json.dumps(cleanup_plan, sort_keys=True)}",
        f"prompt_size_report={json.dumps(prompt_size, sort_keys=True)}",
        f"artifact_size_report={json.dumps(artifact_size, sort_keys=True)}",
        f"runtime_gate={json.dumps(runtime_gate, sort_keys=True)}",
        f"repair_gate={json.dumps(repair_gate, sort_keys=True)}",
        "branch_dag:",
        *[
            f"  {branch['id']}: wave={branch['wave']} dependency_level={branch['dependency_level']} depends_on={branch['depends_on']} worker_cap={branch['worker_cap']}"
            for branch in branch_dag
        ],
        f"lint_status={json.dumps({'brief_lint': lint_brief, 'bundle_lint': lint_bundle}, sort_keys=True)}",
        f"git_status={resolved_repo_root or '<unknown repo>'}",
        *[f"  {line}" for line in _git_status(resolved_repo_root)],
        "next_command=",
        *[f"  {command}" for command in next_command],
    ]
    return "\n".join(lines) + "\n"


def render_readiness_json(bundle_dir: Path, repo_root: Path | None = None) -> str:
    manifest = _load_manifest(bundle_dir)
    bootloader_text = _read_bootloader(bundle_dir)
    bootloader_path = bundle_dir / "goal-bootloader.md"
    resolved_repo_root = _resolve_repo_root(bundle_dir, repo_root, bootloader_text)
    telemetry_mode = manifest.get("telemetry_policy", {}).get("mode", "standard")
    status = "pass"
    goal_config_status = _config_compatibility(manifest)
    lint_brief = _lint_status(bundle_dir, "brief")
    lint_bundle = _lint_status(bundle_dir, "bundle")
    repair_gate = _repair_gate_status(bundle_dir)
    runtime_gate = _repo_runtime_gate(manifest)
    verified_routes = _verified_routes_summary(manifest)
    launch_blockers = _bootloader_launch_blockers(
        _launch_blockers(
            goal_config_status=goal_config_status,
            manifest=manifest,
            verified_routes=verified_routes,
            lint_brief=lint_brief,
            lint_bundle=lint_bundle,
            repair_gate=repair_gate,
            runtime_gate=runtime_gate,
        ),
        lint_bundle=lint_bundle,
        repair_gate=repair_gate,
        defer_missing_reports=False,
    )
    if not launch_blockers and _bootloader_launch_handoff_is_stale(bootloader_text, lint_bundle, repair_gate):
        launch_blockers = ["bootloader launch handoff stale"]
    if launch_blockers:
        status = "blocked"
    launch_allowed = status == "pass" and not launch_blockers

    manifest_path = bundle_dir / "job.manifest.json"
    prompt_size = _prompt_size_report(bundle_dir, manifest)
    artifact_size = _artifact_size_report(bundle_dir, manifest, prompt_size)
    utilization = _branch_utilization_summary(manifest)
    warnings = _readiness_warnings(
        manifest,
        bundle_dir=bundle_dir,
        repo_root=resolved_repo_root,
        goal_config_status=goal_config_status,
        verified_routes=verified_routes,
        utilization=utilization,
    )
    payload = {
        "status": status,
        "launch_allowed": launch_allowed,
        "launch_blockers": launch_blockers,
        "bundle_dir": str(bundle_dir),
        "bootloader_path": str(bootloader_path),
        "bootloader_exists": bootloader_path.exists(),
        "config_compatibility": goal_config_status,
        "telemetry_mode": telemetry_mode,
        "caps": _caps_summary(manifest),
        "route_policy": _route_policy_summary(manifest),
        "verified_routes": verified_routes,
        "branch_utilization": utilization,
        "warnings": warnings,
        "cleanup_plan": _cleanup_plan(bundle_dir, resolved_repo_root, warnings),
        "prompt_size_report": prompt_size,
        "artifact_size_report": artifact_size,
        "branch_dag": _collect_bundle_dag(manifest),
        "lint_status": {
            "brief_lint": lint_brief,
            "bundle_lint": lint_bundle,
        },
        "repair_gate": repair_gate,
        "runtime_gate": runtime_gate,
        "repo_status": manifest.get("repo_status", {}),
        "git_status": _git_status(resolved_repo_root),
        "next_commands": _readiness_next_commands(
            bundle_dir,
            manifest_path,
            resolved_repo_root,
            lint_bundle_status=str(lint_bundle["status"]),
            repair_gate_status=str(repair_gate["status"]),
            runtime_gate=runtime_gate,
            launch_blockers=launch_blockers,
        ),
        "repository_root": str(resolved_repo_root) if resolved_repo_root else None,
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _compact_readiness(readiness: dict) -> dict:
    prompt_size = readiness.get("prompt_size_report") if isinstance(readiness.get("prompt_size_report"), dict) else {}
    artifact_size = (
        readiness.get("artifact_size_report") if isinstance(readiness.get("artifact_size_report"), dict) else {}
    )
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


def _read_json_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _readiness_result_kind(readiness: dict) -> tuple[str, str, str | None]:
    if readiness.get("status") == "pass" and readiness.get("launch_allowed") is True:
        return "pass", "pass", None
    blockers = readiness.get("launch_blockers") if isinstance(readiness.get("launch_blockers"), list) else []
    if blockers:
        return "blocked", "blocked_readiness_usable_bundle", "; ".join(str(item) for item in blockers)
    lint_status = readiness.get("lint_status") if isinstance(readiness.get("lint_status"), dict) else {}
    bundle_lint = lint_status.get("bundle_lint") if isinstance(lint_status.get("bundle_lint"), dict) else {}
    repair_gate = readiness.get("repair_gate") if isinstance(readiness.get("repair_gate"), dict) else {}
    if bundle_lint.get("status") != "pass" or repair_gate.get("status") != "pass":
        return "blocked", "blocked_artifact_gate", "artifact lint or repair gate is not pass"
    runtime_gate = readiness.get("runtime_gate") if isinstance(readiness.get("runtime_gate"), dict) else {}
    blocked_reason = (
        runtime_gate.get("reason") if isinstance(runtime_gate.get("reason"), str) else "readiness status is not pass"
    )
    return "blocked", "blocked_readiness_usable_bundle", blocked_reason


_BOOTLOADER_DEFERRED_LAUNCH_BLOCKERS = {"bundle lint missing", "repair gate missing"}


def _has_only_bootloader_launch_phrase_defects(lint_bundle: dict[str, object]) -> bool:
    defects = lint_bundle.get("defects", ())
    if not isinstance(defects, list):
        return False
    phrase_missing = False
    for defect in defects:
        if not isinstance(defect, dict):
            return False
        severity = defect.get("severity")
        if severity not in {"critical", "major"}:
            continue
        if defect.get("file") != "goal-bootloader.md":
            return False
        message = str(defect.get("message", ""))
        if not message.startswith("bootloader missing phrase:"):
            return False
        phrase_missing = True
    return phrase_missing


def _repair_gate_is_bundle_lint_only(repair_gate: dict[str, object]) -> bool:
    if not isinstance(repair_gate, dict):
        return False
    if repair_gate.get("status") == "pass":
        return False
    actions = repair_gate.get("actions", ())
    if not isinstance(actions, list) or not actions:
        return False
    return all(isinstance(item, dict) and item.get("kind") == "bundle_lint_repair" for item in actions)


def _bootloader_launch_handoff_is_stale(
    bootloader_text: str,
    lint_bundle: dict[str, object],
    repair_gate: dict[str, object],
) -> bool:
    has_handoff = "Use $goal-main-orchestrator" in bootloader_text
    is_blocked_bootloader = "BLOCKED READINESS" in bootloader_text
    if has_handoff and not is_blocked_bootloader:
        return False
    return (
        is_blocked_bootloader
        or _has_only_bootloader_launch_phrase_defects(lint_bundle)
        or _repair_gate_is_bundle_lint_only(repair_gate)
    )


def _bootloader_launch_blockers(
    launch_blockers: list[str],
    *,
    lint_bundle: dict[str, object],
    repair_gate: dict[str, object],
    defer_missing_reports: bool = True,
) -> list[str]:
    # `defer_missing_reports` defers absent bundle-lint / repair-gate reports ("... missing").
    # Only the pre-lint bootloader-markdown render keeps it True (chicken-and-egg: the launch
    # handoff phrase must already be present in the bootloader before lint runs). The authoritative
    # readiness gate passes False: a bundle that was NEVER linted / repair-gated must not be
    # launch_allowed (otherwise a never-validated bundle is more launchable than a linted-failed one).
    deferred: set[str] = set(_BOOTLOADER_DEFERRED_LAUNCH_BLOCKERS) if defer_missing_reports else set()
    if "bundle lint failed" in launch_blockers and _has_only_bootloader_launch_phrase_defects(lint_bundle):
        deferred.add("bundle lint failed")
    if (
        "repair gate blocked" in launch_blockers
        and _repair_gate_is_bundle_lint_only(repair_gate)
        and _has_only_bootloader_launch_phrase_defects(lint_bundle)
    ):
        deferred.add("repair gate blocked")
    return [item for item in launch_blockers if isinstance(item, str) and item not in deferred]


def _compute_bootloader_launch_readiness(
    bundle_dir: Path, repo_root: Path | None, bootloader_text: str
) -> tuple[str, list[str]]:
    manifest = _load_manifest(bundle_dir)
    goal_config_status = _config_compatibility(manifest)
    lint_brief = _lint_status(bundle_dir, "brief")
    lint_bundle = _lint_status(bundle_dir, "bundle")
    repair_gate = _repair_gate_status(bundle_dir)
    runtime_gate = _repo_runtime_gate(manifest)
    status = "pass"
    if _config_status_blocks_launch(goal_config_status):
        status = "blocked"
    if runtime_gate.get("status") != "pass":
        status = "blocked"
    launch_blockers = _bootloader_launch_blockers(
        _launch_blockers(
            goal_config_status=goal_config_status,
            manifest=manifest,
            verified_routes=_verified_routes_summary(manifest),
            lint_brief=lint_brief,
            lint_bundle=lint_bundle,
            repair_gate=repair_gate,
            runtime_gate=runtime_gate,
        ),
        lint_bundle=lint_bundle,
        repair_gate=repair_gate,
    )
    if launch_blockers:
        status = "blocked"
    resolved_repo_root = _resolve_repo_root(bundle_dir, repo_root, bootloader_text)
    if not resolved_repo_root:
        status = "blocked"
        if "runtime gate blocked" not in launch_blockers:
            launch_blockers = [*launch_blockers, "repository root not discoverable"]
    return status, launch_blockers


def refresh_preflight_summary_artifacts(bundle_dir: Path, readiness: dict) -> None:
    pipeline_path = bundle_dir / "preflight.pipeline.json"
    report_path = bundle_dir / "PREFLIGHT_REPORT.md"
    status, result_kind, blocked_reason = _readiness_result_kind(readiness)
    if pipeline_path.exists():
        pipeline = _read_json_object(pipeline_path)
        if pipeline:
            pipeline["status"] = status
            pipeline["result_kind"] = result_kind
            pipeline["usable_bundle"] = status == "pass" or result_kind == "blocked_readiness_usable_bundle"
            pipeline["blocked_reason"] = blocked_reason
            pipeline["readiness_status"] = readiness.get("status")
            pipeline["launch_allowed"] = readiness.get("launch_allowed")
            pipeline["readiness"] = _compact_readiness(readiness)
            pipeline["next_commands"] = readiness.get("next_commands", [])
            pipeline["readiness_path"] = (bundle_dir / "readiness.json").as_posix()
            _write_json_object(pipeline_path, pipeline)
    if report_path.exists():
        text = report_path.read_text(encoding="utf-8").rstrip()
        marker = "\nFinal pipeline state:\n"
        if marker in text:
            text = text.split(marker, 1)[0].rstrip()
        lint_status = readiness.get("lint_status") if isinstance(readiness.get("lint_status"), dict) else {}
        bundle_lint = lint_status.get("bundle_lint") if isinstance(lint_status.get("bundle_lint"), dict) else {}
        repair_gate = readiness.get("repair_gate") if isinstance(readiness.get("repair_gate"), dict) else {}
        blockers = readiness.get("launch_blockers") if isinstance(readiness.get("launch_blockers"), list) else []
        final_lines = [
            "",
            "Final pipeline state:",
            f"- Bundle lint: {bundle_lint.get('reported_status') or bundle_lint.get('status') or 'missing'}",
            f"- Repair gate: {repair_gate.get('status') or 'missing'}",
            f"- Readiness: {readiness.get('status')}",
            f"- Result kind: {result_kind}",
            f"- Launch allowed: {str(bool(readiness.get('launch_allowed'))).lower()}",
        ]
        if blockers:
            final_lines.append(f"- Launch blockers: {'; '.join(str(item) for item in blockers)}")
        report_path.write_text(text + "\n".join(final_lines) + "\n", encoding="utf-8")


def render_and_refresh_canonical_readiness_json(bundle_dir: Path, output: Path, repo_root: Path | None = None) -> str:
    text = ""
    previous = None
    for _ in range(8):
        text = render_readiness_json(bundle_dir, repo_root=repo_root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        refresh_preflight_summary_artifacts(bundle_dir, json.loads(text))
        if text == previous:
            break
        previous = text
    return text


def emit_output(text: str, output: Path | None = None, *, stdout: bool = False, json_mode: bool = False) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        if not stdout and not json_mode:
            print(output)
            return
    print(text, end="" if text.endswith("\n") else "\n")


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


PATH_RULES = _load_path_rules()
CONTRACT = _load_contract()
resolve_absolute_path = PATH_RULES.resolve_absolute_path
MAX_ACTIVE_BRANCH_AGENTS = CONTRACT.MAX_ACTIVE_BRANCH_AGENTS


def render_bootloader(bundle_dir: Path, repo_root: Path) -> str:
    manifest = bundle_dir / "job.manifest.json"
    main_prompt = bundle_dir / "main.prompt.md"
    model_catalog = bundle_dir / "model-catalog.json"
    if manifest.exists():
        bootloader_text = _read_bootloader(bundle_dir)
        readiness_status, launch_blockers = _compute_bootloader_launch_readiness(bundle_dir, repo_root, bootloader_text)
        if readiness_status != "pass":
            blocker_reason = (
                "; ".join(sorted(set(launch_blockers))) if launch_blockers else "bundle readiness is blocked"
            )
            return f"""# BLOCKED READINESS: do not launch /goal yet

Bundle is usable for inspection and lint repair, but launch is blocked.
Bundle root: {bundle_dir}
Repository root: {repo_root}
Reason: {blocker_reason}

Fix readiness blockers:
- Resolve all launch blockers and recheck readiness before launch.
- Recheck readiness before launch:

```bash
if [ -d "${{CODEX_HOME:-$HOME/.codex}}/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="${{CODEX_HOME:-$HOME/.codex}}/skills"
elif [ -d "$HOME/.agents/skills/goal-preflight" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
else
  echo "missing installed skill root for goal-preflight" >&2
  exit 1
fi

python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir} --repo-root {repo_root} --readiness --json
```
"""
    return f"""Use $goal-main-orchestrator with the generated bundle context below.

Prepared bundle:
- Bundle root: {bundle_dir}
- Repository root: {repo_root}
- Manifest: {manifest}
- Main prompt: {main_prompt}

Read `job.manifest.json` and `main.prompt.md` first, then run in order:

```bash
if [ -d "${{CODEX_HOME:-$HOME/.codex}}/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="${{CODEX_HOME:-$HOME/.codex}}/skills"
elif [ -d "$HOME/.agents/skills/goal-main-orchestrator" ]; then
  GOAL_SKILLS_ROOT="$HOME/.agents/skills"
else
  echo "missing installed skill root for goal-main-orchestrator (checked ${{CODEX_HOME:-$HOME/.codex}}/skills and $HOME/.agents/skills)" >&2
  exit 1
fi

python3 "$GOAL_SKILLS_ROOT"/goal-main-orchestrator/scripts/runtime_phase_manifest.py --markdown
python3 "$GOAL_SKILLS_ROOT"/goal-main-orchestrator/scripts/check_goal_skill_availability.py --skills-root "$GOAL_SKILLS_ROOT" --require goal-main-orchestrator --require goal-branch-orchestrator --require goal-plan-amender
python3 "$GOAL_SKILLS_ROOT"/goal-main-orchestrator/scripts/check_model_catalog.py --json --require-codex > {model_catalog}
python3 "$GOAL_SKILLS_ROOT"/goal-main-orchestrator/scripts/run_prompt_audit_phase.py --manifest {manifest} --repo-root {repo_root} --audit-dir {bundle_dir}/audit --deterministic --require-pass
```

Use script output, JSON artifacts, and validator defects as the working surface; do not read skill Python source unless debugging a failed script. Use absolute paths only.

Mandatory skill availability bootstrap is the command block above.

Do not start branches unless prompt-audit says `status=pass`, `can_start=true`, and it pins the manifest and repository above.

Respect max_active_branch_agents from job.manifest.json; never exceed {MAX_ACTIVE_BRANCH_AGENTS}. Keep branch orchestrator slots saturated, record scheduler-v2 evidence under `schedulers/`, and avoid polling active branch, worker, reviewer, process, or status artifacts.

Parallelism is the default. Use a rolling saturated pool. Defer only unresolved `depends_on` entries. Waves are dependency-aware scheduling/order groups, not barriers. Branches may each declare 1 to 4 worker packets in-band.

Finish only when the Definition of Done in `main.prompt.md` is satisfied, packet telemetry and `telemetry.summary.json` are coherent, branch status/review evidence is complete, final `python3 "$GOAL_SKILLS_ROOT"/goal-main-orchestrator/scripts/validate_main_status.py` succeeds, and git state is explicit. Otherwise return blocked/partial.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", required=True)
    parser.add_argument("--repo-root", help="Regenerate bootloader text with this repository root before printing.")
    parser.add_argument("--readiness", action="store_true", help="Show a compact readiness summary for the bundle.")
    parser.add_argument("--json", action="store_true", help="With --readiness, print JSON readiness output.")
    parser.add_argument("--output", type=Path, help="Write the printed bootloader/readiness output to this path.")
    parser.add_argument(
        "--stdout", action="store_true", help="With --output, also print the full rendered payload to stdout."
    )
    parser.add_argument(
        "--write", action="store_true", help="With --repo-root, rewrite goal-bootloader.md before printing."
    )
    args = parser.parse_args()

    if args.json and not args.readiness:
        raise SystemExit("--json is only valid with --readiness")

    bundle_dir = resolve_absolute_path(args.bundle_dir, "--bundle-dir", must_exist=True)
    path = bundle_dir / "goal-bootloader.md"
    if args.readiness:
        repo_root = None
        if args.repo_root:
            repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
        if args.json:
            if args.output is not None and args.output.resolve() == (bundle_dir / "readiness.json").resolve():
                text = render_and_refresh_canonical_readiness_json(
                    bundle_dir, args.output.resolve(), repo_root=repo_root
                )
                print(text, end="" if text.endswith("\n") else "\n")
                return 0
            emit_output(
                render_readiness_json(bundle_dir, repo_root=repo_root), args.output, stdout=args.stdout, json_mode=True
            )
            return 0
        emit_output(render_readiness(bundle_dir, repo_root=repo_root), args.output, stdout=args.stdout)
        return 0
    if args.repo_root:
        repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
        if not (bundle_dir / "job.manifest.json").exists():
            raise SystemExit(f"missing manifest: {bundle_dir / 'job.manifest.json'}")
        if not (bundle_dir / "main.prompt.md").exists():
            raise SystemExit(f"missing main prompt: {bundle_dir / 'main.prompt.md'}")
        text = render_bootloader(bundle_dir, repo_root)
        if args.write:
            path.write_text(text, encoding="utf-8")
        emit_output(text, args.output, stdout=args.stdout)
        return 0
    if args.write:
        raise SystemExit("--write requires --repo-root")
    if not path.exists():
        raise SystemExit(f"missing bootloader: {path}")
    text = path.read_text(encoding="utf-8")
    emit_output(text, args.output, stdout=args.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
