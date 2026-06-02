#!/usr/bin/env python3
"""Synchronize generated wrappers for shared goal skill support code."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "skills"
SHARED_ROOT = SKILLS_ROOT / "_goal_shared"
SKILLS = (
    "goal-config",
    "goal-preflight",
    "goal-main-orchestrator",
    "goal-branch-orchestrator",
    "goal-plan-amender",
)
SHARED_SCRIPTS = (
    "append_scheduler_event.py",
    "check_model_catalog.py",
    "create_lite_advice_packet.py",
    "context_pack.py",
    "extract_telemetry.py",
    "runtime_lite_runner.py",
    "runtime_phase_manifest.py",
    "reconcile_goal_run.py",
    "script_only_repair_gate.py",
    "scheduler_tick.py",
    "validate_lite_advice.py",
    "check_goal_skill_availability.py",
)
SHARED_REFERENCES = ("lite-advisor-contract.md",)
CHECKED_IN_WRAPPER_RATIONALE = (
    "Skill SKILL.md files, generated phase manifests, and installed-skill workflows call "
    "skill-local script paths directly; checked-in wrappers keep those paths executable "
    "while delegating implementation to skills/_goal_shared."
)


SCRIPT_WRAPPER_TEMPLATE = """#!/usr/bin/env python3
\"\"\"Dispatch to the shared goal orchestration implementation.\"\"\"

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_shared():
    script_path = Path(__file__).resolve()
    skill_name = script_path.parents[1].name
    shared_path = script_path.parents[2] / "_goal_shared" / "scripts" / script_path.name
    if not shared_path.exists():
        raise SystemExit(f"missing shared goal script: {shared_path}")
    spec = importlib.util.spec_from_file_location(f"_goal_shared_{script_path.stem}", shared_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared goal script: {shared_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.SKILL_NAME_OVERRIDE = skill_name
    module.SCRIPT_DIR_OVERRIDE = script_path.parent
    return module


_SHARED_MODULE = _load_shared()

for _name in dir(_SHARED_MODULE):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_SHARED_MODULE, _name)


if __name__ == "__main__":
    raise SystemExit(_SHARED_MODULE.main())
"""


REFERENCE_WRAPPER_TEMPLATE = """# Lite Advisor Contract

The authoritative Lite advisor contract is shared by the goal skill package.

Read `../../_goal_shared/references/lite-advisor-contract.md` before creating or validating Lite packets.
"""


def expected_files() -> dict[Path, str]:
    files: dict[Path, str] = {}
    for script in SHARED_SCRIPTS:
        shared_script = SHARED_ROOT / "scripts" / script
        if not shared_script.exists():
            raise SystemExit(f"missing shared script: {shared_script}")
    for reference in SHARED_REFERENCES:
        shared_reference = SHARED_ROOT / "references" / reference
        if not shared_reference.exists():
            raise SystemExit(f"missing shared reference: {shared_reference}")
    for skill in SKILLS:
        for script in SHARED_SCRIPTS:
            files[SKILLS_ROOT / skill / "scripts" / script] = SCRIPT_WRAPPER_TEMPLATE
        for reference in SHARED_REFERENCES:
            files[SKILLS_ROOT / skill / "references" / reference] = REFERENCE_WRAPPER_TEMPLATE
    return files


def summary_payload(*, drift: list[Path]) -> dict:
    wrapper_count = len(SKILLS) * len(SHARED_SCRIPTS)
    reference_wrapper_count = len(SKILLS) * len(SHARED_REFERENCES)
    return {
        "status": "failed" if drift else "pass",
        "skills": list(SKILLS),
        "shared_scripts": list(SHARED_SCRIPTS),
        "shared_references": list(SHARED_REFERENCES),
        "generated_script_wrappers": wrapper_count,
        "generated_reference_wrappers": reference_wrapper_count,
        "checked_in_wrapper_rationale": CHECKED_IN_WRAPPER_RATIONALE,
        "drift": [path.relative_to(ROOT).as_posix() for path in drift],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Rewrite generated wrappers.")
    parser.add_argument("--json", action="store_true", help="Print wrapper ownership and drift details as JSON.")
    args = parser.parse_args()

    drift: list[Path] = []
    for path, expected in expected_files().items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")
            if path.suffix == ".py":
                os.chmod(path, 0o755)
            continue
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        if actual != expected:
            drift.append(path)

    if args.json:
        print(json.dumps(summary_payload(drift=drift), indent=2, sort_keys=True))
    elif drift:
        print("status=failed")
        for path in drift:
            print(f"- generated wrapper drift: {path.relative_to(ROOT)}")
    else:
        print("status=pass")
    if drift:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
