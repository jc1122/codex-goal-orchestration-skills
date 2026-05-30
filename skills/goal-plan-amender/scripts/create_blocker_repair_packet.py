#!/usr/bin/env python3
"""Create or run a deterministic terminal-blocker repair amendment packet."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from amendment_lib import (
    CONTRACT,
    branch_map,
    ensure_amendment_id,
    load_json_object,
    protected_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    write_json,
)


FILE_RE = re.compile(r"(?<![/A-Za-z0-9_.-])((?:src|tests|scripts|plans|docs|\.github)/[A-Za-z0-9_./+-]+\.[A-Za-z0-9_+-]+)")
MODULE_RE = re.compile(r"`?(marketnn(?:\.[A-Za-z_][A-Za-z0-9_]*){1,})`?")
BARE_PY_RE = re.compile(r"(?<![/A-Za-z0-9_])`?([A-Za-z_][A-Za-z0-9_]*\.py)`?")
MISSING_WORD_RE = re.compile(r"\b(absent|missing|not found|required verification test files|no module named|cannot import name)\b", re.I)
TEST_FAIL_RE = re.compile(r"((?:tests)/[A-Za-z0-9_./+-]+\.py).*?fail(?:s|ed)? to collect", re.I)
ALLOWED_PREFIXES = ("src/", "tests/", "scripts/", "plans/", "docs/", ".github/")
DETERMINISTIC_MODE = "deterministic_blocker_repair"


def source_record(path: Path, label: str) -> dict:
    return {
        "label": label,
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def add_if_exists(records: list[dict], path: Path, label: str) -> None:
    if path.exists() and path.is_file():
        records.append(source_record(path, label))


def repo_relative(path: Path, repo_root: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def safe_path(value: str) -> str | None:
    path = value.strip().strip("`'\".,:;()[]{}")
    if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        return None
    if not path.startswith(ALLOWED_PREFIXES):
        return None
    if path.endswith(".json") and "/branches/" in path:
        return None
    if relative_path_defect(path, "repair path"):
        return None
    return path


def module_to_path(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2:
        return None
    if module in {"marketnn.motif_runtime"}:
        return None
    return safe_path("src/" + "/".join(parts) + ".py")


def append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def blockers_from_status(status: dict) -> list[str]:
    blockers: list[str] = []
    for item in status.get("blockers", []):
        if isinstance(item, str) and item.strip():
            blockers.append(item.strip())
    for worker in status.get("worker_statuses", []):
        if not isinstance(worker, dict):
            continue
        for item in worker.get("blockers", []):
            if isinstance(item, str) and item.strip():
                blockers.append(item.strip())
    return blockers


def extract_repair_paths(blockers: list[str]) -> tuple[list[str], list[str]]:
    owned_paths: list[str] = []
    verification_tests: list[str] = []
    for blocker in blockers:
        lowered = blocker.lower()
        missing_context = MISSING_WORD_RE.search(blocker) is not None
        for match in MODULE_RE.finditer(blocker):
            append_unique(owned_paths, module_to_path(match.group(1)))
        if "marketnn.motif_runtime" in blocker:
            for filename in BARE_PY_RE.findall(blocker):
                if filename in {"py.py"}:
                    continue
                append_unique(owned_paths, safe_path(f"src/marketnn/motif_runtime/{filename}"))
        for match in TEST_FAIL_RE.finditer(blocker):
            append_unique(verification_tests, safe_path(match.group(1)))
        for raw_path in FILE_RE.findall(blocker):
            path = safe_path(raw_path)
            if not path:
                continue
            if path.startswith("tests/") and "fail to collect" in lowered and not ("absent" in lowered or "not found" in lowered):
                append_unique(verification_tests, path)
                continue
            if missing_context:
                append_unique(owned_paths, path)
            elif path.startswith("tests/"):
                append_unique(verification_tests, path)
    return owned_paths, verification_tests


def all_owned_paths(manifest: dict) -> dict[str, str]:
    owners: dict[str, str] = {}
    for branch in manifest.get("branches", []):
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        branch_id = branch["id"]
        for path in branch.get("owned_paths", []):
            if isinstance(path, str):
                owners.setdefault(path, branch_id)
    return owners


def next_branch_id(manifest: dict, offset: int) -> str:
    used = set()
    for branch in manifest.get("branches", []):
        if isinstance(branch, dict) and isinstance(branch.get("id"), str):
            used.add(branch["id"])
    for branch in manifest.get("obsolete_branches", []):
        if isinstance(branch, dict) and isinstance(branch.get("branch_id"), str):
            used.add(branch["branch_id"])
    highest = 0
    for branch_id in used:
        match = re.fullmatch(r"B(\d+)", branch_id)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate_number = highest + 1 + offset
    while True:
        candidate = f"B{candidate_number:02d}"
        if candidate not in used:
            return candidate
        candidate_number += 1


def work_item_group(path: str) -> str:
    if path.startswith("src/"):
        return "runtime"
    if path.startswith("tests/"):
        return "tests"
    if path.startswith("scripts/"):
        return "scripts"
    if path.startswith("plans/") or path.startswith("docs/"):
        return "docs"
    return "other"


def compile_command(paths: list[str]) -> str:
    src_paths = [path for path in paths if path.startswith("src/")]
    if src_paths:
        return "env PYTHONPATH=src python3 -m compileall " + " ".join(src_paths)
    return "python3 -m compileall ."


def branch_tests(base_ref: str, owned_paths: list[str], verification_tests: list[str]) -> list[str]:
    commands = [f"git diff --check {base_ref}...HEAD"]
    runtime_paths = [path for path in owned_paths if path.startswith("src/")]
    if runtime_paths:
        commands.append(compile_command(runtime_paths))
    test_paths = [path for path in owned_paths + verification_tests if path.startswith("tests/")]
    if test_paths:
        commands.append("env PYTHONPATH=src python3 -m pytest " + " ".join(dict.fromkeys(test_paths)) + " -q")
    return commands


def repair_branch(
    *,
    manifest: dict,
    repo_root: Path,
    bundle_dir: Path,
    branch_id: str,
    terminal_branch_id: str,
    status_path: Path,
    owned_paths: list[str],
    verification_tests: list[str],
    has_overlap: bool,
) -> dict:
    job_id = str(manifest.get("job_id", "goal"))
    base_ref = str(manifest.get("base_ref", "main"))
    branch_name = f"{job_id}-repair-{terminal_branch_id.lower()}"
    context_files = ["main.prompt.md"]
    status_rel = repo_relative(status_path, repo_root)
    if status_rel:
        context_files.append(status_rel)
    bundle_prompt = bundle_dir / "main.prompt.md"
    prompt_rel = repo_relative(bundle_prompt, repo_root)
    if prompt_rel and prompt_rel not in context_files:
        context_files.append(prompt_rel)
    groups: dict[str, list[str]] = {"runtime": [], "tests": [], "scripts": [], "docs": [], "other": []}
    for path in owned_paths:
        groups[work_item_group(path)].append(path)
    nonempty_groups = [(name, paths) for name, paths in groups.items() if paths]
    if len(nonempty_groups) > CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH:
        kept = nonempty_groups[: CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH - 1]
        merged_paths = []
        for _name, paths in nonempty_groups[CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH - 1 :]:
            merged_paths.extend(paths)
        nonempty_groups = kept + [("mixed", merged_paths)]

    work_items = []
    for index, (name, paths) in enumerate(nonempty_groups, start=1):
        verification = [f"git diff --check {base_ref}...HEAD"]
        if any(path.startswith("src/") for path in paths):
            verification.append(compile_command(paths))
        group_tests = [path for path in paths + verification_tests if path.startswith("tests/")]
        if group_tests:
            verification.append("env PYTHONPATH=src python3 -m pytest " + " ".join(dict.fromkeys(group_tests)) + " -q")
        work_items.append(
            {
                "id": f"W{index:02d}",
                "objective": f"Repair {name} blockers from terminal branch {terminal_branch_id}.",
                "owned_paths": paths,
                "context_files": context_files,
                "verification": verification,
                "dod": [
                    f"Paths assigned to this worker resolve terminal {terminal_branch_id} blocker evidence.",
                    "Focused verification commands pass or preserve explicit residual blocker evidence.",
                ],
                **(
                    {
                        "contention_reason": "Terminal-blocker repair may need to touch paths previously owned by protected branches; the protected branch artifacts remain immutable."
                    }
                    if has_overlap
                    else {}
                ),
            }
        )

    branch = {
        "id": branch_id,
        "title": f"Repair {terminal_branch_id} Blockers",
        "objective": f"Deterministically repair missing local files and tests cited by terminal branch {terminal_branch_id} blockers.",
        "scope": "Repair branch added by deterministic blocker diagnosis; terminal branch evidence remains immutable.",
        "branch_name": branch_name,
        "worktree_path": f".worktrees/{branch_name}",
        "depends_on": [],
        "recovers_from": [terminal_branch_id],
        "max_active_worker_packets": CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH,
        "work_items": work_items,
        "tests": branch_tests(base_ref, owned_paths, verification_tests),
        "dod": [
            f"Terminal blocker paths from {terminal_branch_id} are implemented or explicitly narrowed.",
            "Native branch status can be re-evaluated without missing local-file blocker evidence.",
        ],
    }
    if has_overlap:
        branch["contention_reason"] = "Terminal-blocker repair intentionally overlaps prior protected ownership while leaving terminal artifacts immutable."
        branch["worker_contention_reason"] = "Repair workers may touch paths from preserved blocker evidence."
    return branch


def generate_proposal(input_data: dict) -> dict:
    manifest_path = Path(input_data["manifest"])
    repo_root = Path(input_data["repo_root"])
    manifest = load_json_object(manifest_path)
    bundle_dir = manifest_path.parent
    branches = branch_map(manifest)
    existing_owned = all_owned_paths(manifest)
    operations = []
    terminal_ids = [item for item in input_data.get("terminal_branch_ids", []) if isinstance(item, str)]
    branch_offset = 0
    for terminal_branch_id in terminal_ids:
        branch = branches.get(terminal_branch_id)
        if not isinstance(branch, dict):
            continue
        status_path_value = branch.get("status_path")
        if not isinstance(status_path_value, str) or relative_path_defect(status_path_value, f"{terminal_branch_id}.status_path"):
            continue
        status_path = bundle_dir / status_path_value
        if not status_path.exists():
            continue
        status = load_json_object(status_path)
        if status.get("status") == "pass":
            continue
        blockers = blockers_from_status(status)
        owned_paths, verification_tests = extract_repair_paths(blockers)
        if not owned_paths:
            continue
        has_overlap = any(path in existing_owned for path in owned_paths)
        branch_id = next_branch_id(manifest, branch_offset)
        branch_offset += 1
        operations.append(
            {
                "op": "add_branch",
                "branch": repair_branch(
                    manifest=manifest,
                    repo_root=repo_root,
                    bundle_dir=bundle_dir,
                    branch_id=branch_id,
                    terminal_branch_id=terminal_branch_id,
                    status_path=status_path,
                    owned_paths=owned_paths,
                    verification_tests=verification_tests,
                    has_overlap=has_overlap,
                ),
            }
        )
    return {
        "schema_version": 1,
        "amendment_id": input_data["amendment_id"],
        "job_id": input_data["job_id"],
        "rationale": "Deterministic blocker diagnosis converted terminal non-pass missing-file evidence into repair branches.",
        "operations": operations,
    }


def telemetry(packet_id: str, proposal_path: Path, input_path: Path, telemetry_path: Path, command: str) -> dict:
    proposal_text = proposal_path.read_text(encoding="utf-8")
    input_text = input_path.read_text(encoding="utf-8")
    alias = getattr(CONTRACT, "DETERMINISTIC_AMENDER_ALIAS", "deterministic-blocker-repair")
    return {
        "schema_version": 1,
        "packet_id": packet_id,
        "role": CONTRACT.AMENDER_ROLE,
        "output_artifact": f"../{proposal_path.name}",
        "prompt_artifact": "input-files.json",
        "prompt_chars": len(input_text),
        "prompt_bytes": len(input_text.encode("utf-8")),
        "output_chars": len(proposal_text),
        "output_bytes": len(proposal_text.encode("utf-8")),
        "event_log_chars": 0,
        "event_log_bytes": 0,
        "accepted_alias": alias,
        "attempts": [
            {
                "alias": alias,
                "provider": "local-script",
                "model": "goal-plan-amender.deterministic-blocker-repair",
                "effort": "deterministic",
                "command": command,
                "timeout_seconds": 1,
                "called": True,
                "accepted": True,
                "event_logs": [],
                "probe_logs": [],
                "usage": None,
            }
        ],
        "totals": {
            "attempts_declared": 1,
            "attempts_called": 1,
            "event_log_chars": 0,
            "event_log_bytes": 0,
            "known_usage": None,
        },
    }


def emit_proposal(input_path: Path, proposal_path: Path, telemetry_path: Path) -> None:
    input_data = load_json_object(input_path)
    proposal = generate_proposal(input_data)
    write_json(proposal_path, proposal)
    command = f"python3 {Path(__file__).resolve().as_posix()} --emit-proposal --input-files {input_path.as_posix()} --proposal {proposal_path.as_posix()} --telemetry {telemetry_path.as_posix()}"
    write_json(telemetry_path, telemetry(str(input_data["amendment_id"]), proposal_path, input_path, telemetry_path, command))


def launch_script() -> str:
    script_path = Path(__file__).resolve().as_posix()
    return f"""#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

packet_dir="$(pwd)"
proposal_path="$(cd "$packet_dir/.." && pwd)/$AMENDMENT_ID.proposal.json"
telemetry_path="$packet_dir/telemetry.json"

python3 {CONTRACT.shell_quote(script_path)} \\
  --emit-proposal \\
  --input-files "$packet_dir/input-files.json" \\
  --proposal "$proposal_path" \\
  --telemetry "$telemetry_path"
"""


def create_packet(args: argparse.Namespace) -> Path:
    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    main_prompt = resolve_absolute_path(args.main_prompt, "--main-prompt", must_exist=True)
    repo_root = resolve_absolute_path(args.repo_root, "--repo-root", must_exist=True)
    amendment_id = ensure_amendment_id(args.amendment_id)
    manifest = load_json_object(manifest_path)
    if manifest.get("amender_model_policy") != CONTRACT.AMENDER_MODEL_POLICY:
        raise SystemExit("manifest amender_model_policy does not match the shared deterministic plan-amender router policy")
    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    if not decision_path.exists():
        raise SystemExit(f"missing launch decision artifact: {decision_path}")
    decision = load_json_object(decision_path)
    if decision.get("schema_version") != 1 or decision.get("amendment_id") != amendment_id or decision.get("decision") != "launch":
        raise SystemExit(f"amendment decision must be a launch decision for {amendment_id}: {decision_path}")
    if decision.get("manifest") != manifest_path.as_posix() or decision.get("manifest_sha256") != sha256_file(manifest_path):
        raise SystemExit("amendment decision manifest path or sha256 does not match the live manifest")
    if decision.get("reason_code") not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        raise SystemExit("amendment decision reason_code is not valid for a launch decision")

    packet_dir = amendments_dir / f"{amendment_id}.packet"
    if packet_dir.exists() and not args.replace:
        raise SystemExit(f"blocker repair packet already exists; pass --replace to recreate: {packet_dir}")
    if packet_dir.exists():
        for child in sorted(packet_dir.iterdir(), reverse=True):
            if child.is_dir():
                raise SystemExit(f"refusing to replace non-empty nested packet directory: {child}")
            child.unlink()
    packet_dir.mkdir(parents=True, exist_ok=True)

    active, terminal, terminal_status = protected_ids(
        manifest_path,
        manifest,
        active_ids=args.active_branch,
        terminal_ids=args.terminal_branch,
        infer_scheduler=True,
    )
    decision_active = sorted(item for item in decision.get("active_branch_ids", []) if isinstance(item, str))
    decision_terminal = sorted(item for item in decision.get("terminal_branch_ids", []) if isinstance(item, str))
    if sorted(active) != decision_active:
        raise SystemExit("amendment decision active_branch_ids do not match packet protected active ids")
    if sorted(terminal) != decision_terminal:
        raise SystemExit("amendment decision terminal_branch_ids do not match packet protected terminal ids")

    records: list[dict] = [
        source_record(manifest_path, "live manifest"),
        source_record(decision_path, "amendment launch decision"),
        source_record(main_prompt, "main prompt"),
    ]
    audit_path = resolve_absolute_path(args.prompt_audit, "--prompt-audit", must_exist=True) if args.prompt_audit else bundle_dir / "audit" / "prompt-audit.json"
    add_if_exists(records, audit_path, "prompt audit")
    scheduler_path = manifest.get("parallelization", {}).get("scheduler_path") if isinstance(manifest.get("parallelization"), dict) else None
    if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path"):
        add_if_exists(records, bundle_dir / scheduler_path, "main scheduler")
    for branch in manifest.get("branches", []):
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str) or branch["id"] not in terminal:
            continue
        value = branch.get("status_path")
        if isinstance(value, str) and not relative_path_defect(value, "status_path"):
            add_if_exists(records, bundle_dir / value, f"terminal branch status {branch['id']}")

    packet = {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "job_id": manifest.get("job_id"),
        "manifest": manifest_path.as_posix(),
        "main_prompt": main_prompt.as_posix(),
        "repo_root": repo_root.as_posix(),
        "decision_path": decision_path.as_posix(),
        "proposal_path": (amendments_dir / f"{amendment_id}.proposal.json").as_posix(),
        "validation_path": (amendments_dir / f"{amendment_id}.validation.json").as_posix(),
        "accepted_path": (amendments_dir / f"{amendment_id}.accepted.json").as_posix(),
        "active_branch_ids": sorted(active),
        "terminal_branch_ids": sorted(terminal),
        "terminal_branch_statuses": {branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)},
        "selected_ladder": [],
        "selection_reason": "Deterministic blocker diagnosis from terminal status artifacts.",
        "source_files": records,
    }
    route = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": CONTRACT.AMENDER_ROLE,
        "mode": DETERMINISTIC_MODE,
        "selected_ladder": [],
        "selection_reason": "Deterministic blocker diagnosis from terminal status artifacts.",
        "policy": CONTRACT.AMENDER_MODEL_POLICY,
    }
    write_json(packet_dir / "input-files.json", packet)
    write_json(packet_dir / "route.json", route)
    (packet_dir / "task.md").write_text("Deterministically diagnose terminal branch blockers and write a repair-branch proposal.\n", encoding="utf-8")
    (packet_dir / "prompt.md").write_text("Deterministic local-script packet; no model prompt is used.\n", encoding="utf-8")
    launch = launch_script().replace("$AMENDMENT_ID", amendment_id)
    launch_path = packet_dir / "launch.sh"
    launch_path.write_text(launch, encoding="utf-8")
    os.chmod(launch_path, 0o755)
    return packet_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest")
    parser.add_argument("--main-prompt")
    parser.add_argument("--repo-root")
    parser.add_argument("--amendment-id")
    parser.add_argument("--prompt-audit")
    parser.add_argument("--active-branch", action="append", default=[])
    parser.add_argument("--terminal-branch", action="append", default=[])
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--emit-proposal", action="store_true")
    parser.add_argument("--input-files")
    parser.add_argument("--proposal")
    parser.add_argument("--telemetry")
    args = parser.parse_args()

    if args.emit_proposal:
        if not args.input_files or not args.proposal or not args.telemetry:
            raise SystemExit("--emit-proposal requires --input-files, --proposal, and --telemetry")
        emit_proposal(
            resolve_absolute_path(args.input_files, "--input-files", must_exist=True),
            resolve_absolute_path(args.proposal, "--proposal", must_exist=False),
            resolve_absolute_path(args.telemetry, "--telemetry", must_exist=False),
        )
        return 0

    for name in ["manifest", "main_prompt", "repo_root", "amendment_id"]:
        if getattr(args, name) is None:
            raise SystemExit(f"--{name.replace('_', '-')} is required")
    print(create_packet(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
