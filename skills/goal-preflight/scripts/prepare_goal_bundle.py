#!/usr/bin/env python3
"""Run the canonical goal-preflight pipeline and persist handoff artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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


def safe_name(path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.name).strip("-")
    return value or "goal-config"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)


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
    result = {
        "status": remediation.get("status"),
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
    accepted = summary.get("accepted_route_count")
    return isinstance(accepted, int) and not isinstance(accepted, bool) and accepted > 0


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


def check_config_candidate(config: Path, check_dir: Path) -> dict:
    check_script = SKILLS_ROOT / "goal-config" / "scripts" / "check_goal_config.py"
    stem = safe_name(config)
    original_report = check_dir / f"{stem}.preflight-check.json"
    remediated_config = check_dir / f"{stem}.remediated.json"
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
    result = run(command)
    original = read_json(original_report) if original_report.exists() else {"status": "failed", "failures": [result.stdout]}
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
    }
    if result.returncode == 0 and original.get("status") == "pass":
        candidate.update(
            {
                "selected": True,
                "eligible": True,
                "selected_config_path": config.as_posix(),
                "selected_check_path": original_report.as_posix(),
                "selection_reason": "original config passed preflight compatibility",
            }
        )
        return candidate

    if remediated_config.exists() and original.get("remediation", {}).get("actions"):
        remediated_report = check_dir / f"{stem}.remediated.preflight-check.json"
        remediated_result = run(
            [
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
        )
        remediated = read_json(remediated_report) if remediated_report.exists() else {"status": "failed", "failures": [remediated_result.stdout]}
        candidate["remediated_config_path"] = remediated_config.as_posix()
        candidate["remediated_check_path"] = remediated_report.as_posix()
        candidate["remediated_status"] = remediated.get("status")
        candidate["remediated_failures"] = remediated.get("failures", [])
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


def select_config(brief: Path, repo_root: Path, out_dir: Path, explicit: list[str], skip: bool) -> dict:
    selection_path = out_dir / "goal-config-selection.json"
    if skip:
        selection = {"status": "skipped", "selected": None, "candidates": [], "reason": "--no-goal-config"}
        write_json(selection_path, selection)
        return selection
    check_dir = out_dir / "config-checks"
    candidates = [check_config_candidate(path, check_dir) for path in candidate_configs(brief, repo_root, out_dir, explicit)]
    selected_index = next((index for index, item in enumerate(candidates) if item.get("eligible")), None)
    for index, item in enumerate(candidates):
        item["selected"] = selected_index == index
    selected = candidates[selected_index] if selected_index is not None else None
    selection = {
        "status": "pass" if selected else "not_selected",
        "selected": selected,
        "candidates": candidates,
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
    parser.add_argument("--no-goal-config", action="store_true", help="Do not auto-detect or embed a goal config.")
    parser.add_argument("--allow-blocked-readiness", action="store_true", help="Return zero even when readiness is blocked.")
    parser.add_argument("--json", action="store_true", help="Print pipeline JSON to stdout.")
    parser.add_argument("--output", help="Write pipeline result JSON. Defaults to <bundle>/preflight.pipeline.json.")
    args = parser.parse_args()

    brief = resolve_absolute_path(args.brief, "--brief", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    out_dir = resolve_absolute_path(args.out_dir, "--out-dir", must_exist=False)
    out_dir.mkdir(parents=True, exist_ok=True)

    commands: list[dict] = []

    brief_lint = out_dir / "preflight.brief.lint.json"
    brief_lint_result = run(
        [
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
    )
    commands.append({"phase": "brief_lint", "returncode": brief_lint_result.returncode, "output": brief_lint.as_posix()})
    if brief_lint_result.returncode != 0:
        result = {"status": "failed", "phase": "brief_lint", "bundle_dir": out_dir.as_posix(), "commands": commands}
        write_json(Path(args.output) if args.output else out_dir / "preflight.pipeline.json", result)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    config_selection = select_config(brief, repo_root, out_dir, args.goal_config, args.no_goal_config)
    selected = config_selection.get("selected") if isinstance(config_selection.get("selected"), dict) else None

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
    create_result = run(create_command)
    commands.append({"phase": "create_bundle", "returncode": create_result.returncode, "output": (out_dir / "create-bundle-result.json").as_posix()})
    if create_result.returncode != 0:
        result = {
            "status": "failed",
            "phase": "create_bundle",
            "bundle_dir": out_dir.as_posix(),
            "config_selection": config_selection,
            "stdout": create_result.stdout,
            "commands": commands,
        }
        write_json(Path(args.output) if args.output else out_dir / "preflight.pipeline.json", result)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 1

    bundle_lint = out_dir / "preflight.lint.json"
    lint_result = run(
        [
            sys.executable,
            (SKILLS_ROOT / "goal-preflight" / "scripts" / "lint_goal_bundle.py").as_posix(),
            "--bundle-dir",
            out_dir.as_posix(),
            "--json",
            "--output",
            bundle_lint.as_posix(),
        ]
    )
    commands.append({"phase": "bundle_lint", "returncode": lint_result.returncode, "output": bundle_lint.as_posix()})

    repair_gate = out_dir / "repair-gate.json"
    repair_result = run(
        [
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
            "--json",
            "--output",
            repair_gate.as_posix(),
        ]
    )
    commands.append({"phase": "repair_gate", "returncode": repair_result.returncode, "output": repair_gate.as_posix()})

    readiness = out_dir / "readiness.json"
    readiness_result = run(
        [
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
    )
    commands.append({"phase": "readiness", "returncode": readiness_result.returncode, "output": readiness.as_posix()})
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
    result = {
        "schema_version": 1,
        "status": status,
        "result_kind": result_kind,
        "usable_bundle": status == "pass" or result_kind == "blocked_readiness_usable_bundle",
        "blocked_reason": blocked_reason,
        "bundle_dir": out_dir.as_posix(),
        "bootloader_path": (out_dir / "goal-bootloader.md").as_posix(),
        "config_selection": config_selection,
        "brief_lint_path": brief_lint.as_posix(),
        "bundle_lint_path": bundle_lint.as_posix(),
        "repair_gate_path": repair_gate.as_posix(),
        "readiness_path": readiness.as_posix(),
        "readiness_status": readiness_data.get("status"),
        "readiness": readiness_data,
        "next_commands": readiness_data.get("next_commands", []),
        "commands": commands,
    }
    output_path = resolve_absolute_path(args.output, "--output", must_exist=False) if args.output else out_dir / "preflight.pipeline.json"
    write_json(output_path, result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(output_path)
    return 0 if status == "pass" or args.allow_blocked_readiness else 1


if __name__ == "__main__":
    raise SystemExit(main())
