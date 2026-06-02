#!/usr/bin/env python3
"""Print or regenerate the final /goal bootloader text from a preflight bundle."""

from __future__ import annotations

import argparse
import json
import importlib.util
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


def _route_policy_summary(manifest: dict) -> dict[str, object]:
    routes: dict[str, object] = {}
    worker_policy = manifest.get("worker_model_policy", {})
    routes["worker"] = worker_policy.get("default_ladder", [])
    amender_policy = manifest.get("amender_model_policy", {})
    routes["amender"] = amender_policy.get("default_ladder", [])
    lite_policy = manifest.get("lite_model_policy", {})
    routes["lite"] = lite_policy.get("allowed_routes", [])
    review_policy = manifest.get("review_model_policy", {})
    routes["reviewer"] = review_policy.get("routes", {})
    parallelization = manifest.get("parallelization", {})
    routes["dependency_parallel_policy"] = {
        "max_active_branch_agents": parallelization.get("max_active_branch_agents", manifest.get("max_active_branch_agents")),
        "max_waves": parallelization.get("max_waves", 0),
        "max_branches_per_wave": parallelization.get("max_branches_per_wave", 0),
    }
    return routes


def _caps_summary(manifest: dict) -> dict[str, object]:
    parallelization = manifest.get("parallelization", {})
    return {
        "max_active_branch_agents": manifest.get("max_active_branch_agents"),
        "max_active_worker_packets_default": parallelization.get("max_branches_per_wave", parallelization.get("max_active_branch_agents", 0)),
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
    return {
        "label": label,
        "status": payload.get("status", "unknown"),
        "defects": payload.get("defects", payload.get("errors", [])),
        "path": str(path),
    }


def _repair_gate_status(bundle_dir: Path) -> dict[str, object]:
    path = bundle_dir / "repair-gate.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    payload = _read_json(path, "repair gate report")
    return {
        "status": "pass" if payload.get("decision") == "pass_no_actions" else "blocked",
        "decision": payload.get("decision"),
        "path": str(path),
        "model_launch_allowed": payload.get("model_launch_allowed"),
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
    if not has_config and not check_report:
        return "no goal config supplied"
    if not isinstance(check_report, dict):
        return "goal config check summary malformed"
    if check_report.get("status") == "pass":
        return "goal config check pass"
    return f"goal config check {check_report.get('status', 'missing')}"


def _verified_routes_summary(manifest: dict) -> dict[str, object]:
    check_report = manifest.get("goal_config_check_summary", {})
    if not isinstance(check_report, dict):
        return {"status": "missing", "route_model_availability_verified": False}
    summary = dict(check_report)
    accepted = summary.get("accepted_route_count")
    route_verified = isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0
    summary["route_model_availability_verified"] = route_verified
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
    per_file_min_margin = None if budget is None else min((budget - int(item["chars"]) for item in entries), default=budget)
    branch_prompt_entries = [item for item in entries if str(item.get("path", "")).startswith("branches/")]
    repeated_sections = {
        "branch_prompt_count": len(branch_prompt_entries),
        "shared_runtime_boilerplate_sections": len(branch_prompt_entries)
        * 5,
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
    lint_brief: dict[str, object],
    lint_bundle: dict[str, object],
    repair_gate: dict[str, object],
    runtime_gate: dict[str, object],
) -> list[str]:
    blockers: list[str] = []
    if goal_config_status != "no goal config supplied" and not goal_config_status.endswith("pass"):
        blockers.append(goal_config_status)
    if lint_brief["status"] not in {"pass", "missing"}:
        blockers.append(f"brief lint {lint_brief['status']}")
    if lint_bundle["status"] != "pass":
        blockers.append(f"bundle lint {lint_bundle['status']}")
    if repair_gate["status"] != "pass":
        blockers.append(f"repair gate {repair_gate['status']}")
    if runtime_gate["status"] != "pass":
        blockers.append(f"runtime gate blocked: {runtime_gate.get('reason')}")
    return blockers


def _readiness_next_commands(
    bundle_dir: Path,
    manifest_path: Path,
    repo_root: Path | None,
    *,
    lint_bundle_status: str,
    repair_gate_status: str,
    runtime_gate: dict[str, object],
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
    commands.append(f'python3 "$GOAL_SKILLS_ROOT"/goal-preflight/scripts/render_goal_bootloader.py --bundle-dir {bundle_dir}')
    if lint_bundle_status == "pass" and repair_gate_status == "pass" and runtime_gate.get("status") == "pass":
        commands.append("/goal")
    return commands


def render_readiness(bundle_dir: Path, repo_root: Path | None = None) -> str:
    manifest = _load_manifest(bundle_dir)
    bootloader_text = _read_bootloader(bundle_dir)
    bootloader_path = bundle_dir / "goal-bootloader.md"
    resolved_repo_root = _resolve_repo_root(bundle_dir, repo_root, bootloader_text)
    telemetry_mode = manifest.get("telemetry_policy", {}).get("mode", "standard")
    status = "pass"
    goal_config_status = _config_compatibility(manifest)
    if goal_config_status != "no goal config supplied" and not goal_config_status.endswith("pass"):
        status = "blocked"
    lint_brief = _lint_status(bundle_dir, "brief")
    lint_bundle = _lint_status(bundle_dir, "bundle")
    repair_gate = _repair_gate_status(bundle_dir)
    runtime_gate = _repo_runtime_gate(manifest)
    if lint_brief["status"] not in {"pass", "missing"}:
        status = "blocked"
    if lint_bundle["status"] != "pass":
        status = "blocked"
    if repair_gate["status"] != "pass":
        status = "blocked"
    if runtime_gate["status"] != "pass":
        status = "blocked"
    launch_blockers = _launch_blockers(
        goal_config_status=goal_config_status,
        lint_brief=lint_brief,
        lint_bundle=lint_bundle,
        repair_gate=repair_gate,
        runtime_gate=runtime_gate,
    )
    launch_allowed = status == "pass" and not launch_blockers

    branch_dag = _collect_bundle_dag(manifest)
    route_policy = _route_policy_summary(manifest)
    caps = _caps_summary(manifest)
    verified_routes = _verified_routes_summary(manifest)
    prompt_size = _prompt_size_report(bundle_dir, manifest)
    manifest_path = bundle_dir / "job.manifest.json"
    next_command = _readiness_next_commands(
        bundle_dir,
        manifest_path,
        resolved_repo_root,
        lint_bundle_status=str(lint_bundle["status"]),
        repair_gate_status=str(repair_gate["status"]),
        runtime_gate=runtime_gate,
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
        f"route_policy={json.dumps(route_policy, sort_keys=True)}",
        f"verified_routes={json.dumps(verified_routes, sort_keys=True)}",
        f"prompt_size_report={json.dumps(prompt_size, sort_keys=True)}",
        f"runtime_gate={json.dumps(runtime_gate, sort_keys=True)}",
        f"repair_gate={json.dumps(repair_gate, sort_keys=True)}",
        "branch_dag:",
        *[f"  {branch['id']}: wave={branch['wave']} dependency_level={branch['dependency_level']} depends_on={branch['depends_on']} worker_cap={branch['worker_cap']}" for branch in branch_dag],
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
    if goal_config_status != "no goal config supplied" and not goal_config_status.endswith("pass"):
        status = "blocked"
    lint_brief = _lint_status(bundle_dir, "brief")
    lint_bundle = _lint_status(bundle_dir, "bundle")
    repair_gate = _repair_gate_status(bundle_dir)
    runtime_gate = _repo_runtime_gate(manifest)
    if lint_brief["status"] not in {"pass", "missing"}:
        status = "blocked"
    if lint_bundle["status"] != "pass":
        status = "blocked"
    if repair_gate["status"] != "pass":
        status = "blocked"
    if runtime_gate["status"] != "pass":
        status = "blocked"
    launch_blockers = _launch_blockers(
        goal_config_status=goal_config_status,
        lint_brief=lint_brief,
        lint_bundle=lint_bundle,
        repair_gate=repair_gate,
        runtime_gate=runtime_gate,
    )
    launch_allowed = status == "pass" and not launch_blockers

    manifest_path = bundle_dir / "job.manifest.json"
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
        "verified_routes": _verified_routes_summary(manifest),
        "prompt_size_report": _prompt_size_report(bundle_dir, manifest),
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
        ),
        "repository_root": str(resolved_repo_root) if resolved_repo_root else None,
    }
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def emit_output(text: str, output: Path | None = None) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
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
        runtime_gate = _repo_runtime_gate(_load_manifest(bundle_dir))
        if runtime_gate.get("status") != "pass":
            return f"""# BLOCKED READINESS: do not launch /goal yet

Bundle is usable for inspection and lint repair, but runtime branch/worktree orchestration is blocked.
Bundle root: {bundle_dir}
Repository root: {repo_root}
Reason: {runtime_gate.get("reason")}

Fix the runtime gate:
- Use an existing git work tree for the repository root, initialize this directory as a git work tree before preflight/runtime, or use an explicit supported no-git runtime mode once one exists.
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
    parser.add_argument("--write", action="store_true", help="With --repo-root, rewrite goal-bootloader.md before printing.")
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
            emit_output(render_readiness_json(bundle_dir, repo_root=repo_root), args.output)
            return 0
        emit_output(render_readiness(bundle_dir, repo_root=repo_root), args.output)
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
        emit_output(text, args.output)
        return 0
    if args.write:
        raise SystemExit("--write requires --repo-root")
    if not path.exists():
        raise SystemExit(f"missing bootloader: {path}")
    text = path.read_text(encoding="utf-8")
    emit_output(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
