#!/usr/bin/env python3
"""Create or run a deterministic terminal-blocker repair amendment packet."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from amendment_lib import (
    CONTRACT,
    add_if_exists,
    branch_map,
    add_lineage_stage,
    amender_model_policy,
    amendment_lineage_path,
    ensure_amendment_id,
    init_lineage,
    lineage_path_rel,
    load_json_object,
    protected_ids,
    relative_path_defect,
    resolve_absolute_path,
    sha256_file,
    source_record,
    validate_amender_model_policy,
    write_json,
)
import contextlib


FILE_RE = re.compile(
    r"(?<![/A-Za-z0-9_.-])((?:src|tests|scripts|plans|docs|\.github)/[A-Za-z0-9_./+-]+\.[A-Za-z0-9_+-]+)"
)
MODULE_RE = re.compile(r"`?(marketnn(?:\.[A-Za-z_][A-Za-z0-9_]*){1,})`?")
BARE_PY_RE = re.compile(r"(?<![/A-Za-z0-9_])`?([A-Za-z_][A-Za-z0-9_]*\.py)`?")
MISSING_WORD_RE = re.compile(
    r"\b(absent|missing|not found|required verification test files|no module named|cannot import name)\b", re.I
)
TEST_FAIL_RE = re.compile(r"((?:tests)/[A-Za-z0-9_./+-]+\.py).*?fail(?:s|ed)? to collect", re.I)
ALLOWED_PREFIXES = ("src/", "tests/", "scripts/", "plans/", "docs/", ".github/")
DETERMINISTIC_MODE = "deterministic_blocker_repair"


def safe_path(value: str) -> str | None:
    # Strip wrapping punctuation but NOT a leading '.' (else ".github/..." paths, which
    # FILE_RE/ALLOWED_PREFIXES intentionally support, get silently dropped); trailing dots
    # from prose are removed separately.
    path = value.strip().strip("`'\",:;()[]{}").rstrip(".")
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


def normalized_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def review_evidence_record(path: Path) -> dict:
    data = load_json_object(path)
    fields = {
        "findings": normalized_text_list(data.get("findings")),
        "verification_gaps": normalized_text_list(data.get("verification_gaps")),
        "residual_risks": normalized_text_list(data.get("residual_risks")),
        "commands_run": normalized_text_list(data.get("commands_run")),
    }
    return {
        "source_review_path": path.as_posix(),
        "source_review_sha256": sha256_file(path),
        "packet_id": data.get("packet_id") if isinstance(data.get("packet_id"), str) else None,
        "verdict": data.get("verdict") if isinstance(data.get("verdict"), str) else None,
        **fields,
    }


def review_items_for_repair(evidence: object) -> list[str]:
    if not isinstance(evidence, dict):
        return []
    items: list[str] = []
    for field in ["findings", "verification_gaps"]:
        for item in normalized_text_list(evidence.get(field)):
            items.append(f"review {field}: {item}")
    for item in normalized_text_list(evidence.get("residual_risks")):
        if FILE_RE.search(item) or MODULE_RE.search(item):
            items.append(f"review residual_risks: {item}")
    return items


def blockers_from_status(status: dict) -> list[str]:
    blockers: list[str] = []
    for item in _as_list(status.get("blockers")):
        if isinstance(item, str) and item.strip():
            blockers.append(item.strip())
    for worker in _as_list(status.get("worker_statuses")):
        if not isinstance(worker, dict):
            continue
        for item in _as_list(worker.get("blockers")):
            if isinstance(item, str) and item.strip():
                blockers.append(item.strip())
    return blockers


def extract_repair_paths(blockers: list[str], *, review_context: bool = False) -> tuple[list[str], list[str]]:
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
            if review_context and path.startswith("tests/"):
                append_unique(owned_paths, path)
                append_unique(verification_tests, path)
                continue
            if (
                path.startswith("tests/")
                and "fail to collect" in lowered
                and not ("absent" in lowered or "not found" in lowered)
            ):
                append_unique(verification_tests, path)
                continue
            if missing_context or review_context:
                append_unique(owned_paths, path)
            elif path.startswith("tests/"):
                append_unique(verification_tests, path)
    return owned_paths, verification_tests


def append_unique_many(target: list[str], values: list[str]) -> None:
    for value in values:
        append_unique(target, value)


def source_review_path(branch: dict, bundle_dir: Path) -> Path | None:
    value = branch.get("review_path")
    if not isinstance(value, str) or relative_path_defect(value, "review_path"):
        return None
    path = bundle_dir / value
    if path.exists() and path.is_file():
        return path
    return None


def _as_list(value: object) -> list:
    """A non-list `branches`/`obsolete_branches` (e.g. JSON null/scalar) must iterate as empty,
    not TypeError — `.get(k, [])` only defaults on an ABSENT key, not a present non-list value."""
    return value if isinstance(value, list) else []


def all_owned_paths(manifest: dict) -> dict[str, str]:
    owners: dict[str, str] = {}
    for branch in _as_list(manifest.get("branches")):
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str):
            continue
        branch_id = branch["id"]
        for path in _as_list(branch.get("owned_paths")):
            if isinstance(path, str):
                owners.setdefault(path, branch_id)
    return owners


def next_branch_id(manifest: dict, offset: int) -> str:
    used = set()
    for branch in _as_list(manifest.get("branches")):
        if isinstance(branch, dict) and isinstance(branch.get("id"), str):
            used.add(branch["id"])
    for branch in _as_list(manifest.get("obsolete_branches")):
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
    review_evidence: dict | None = None,
    review_items: list[str] | None = None,
) -> dict:
    job_id = str(manifest.get("job_id", "goal"))
    base_ref = str(manifest.get("base_ref", "main"))
    branch_name = f"{job_id}-repair-{terminal_branch_id.lower()}"
    context_files: list[str] = []
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
    review_items = review_items or []
    review_source = review_evidence.get("source_review_path") if isinstance(review_evidence, dict) else None
    for index, (name, paths) in enumerate(nonempty_groups, start=1):
        verification = [f"git diff --check {base_ref}...HEAD"]
        if any(path.startswith("src/") for path in paths):
            verification.append(compile_command(paths))
        group_tests = [path for path in paths + verification_tests if path.startswith("tests/")]
        if group_tests:
            verification.append("env PYTHONPATH=src python3 -m pytest " + " ".join(dict.fromkeys(group_tests)) + " -q")
        matching_review_items = [item for item in review_items if any(path in item for path in paths)]
        review_dod = (
            [f"Reviewer evidence from {review_source} is resolved for this worker's assigned paths."]
            if isinstance(review_source, str) and matching_review_items
            else []
        )
        review_dod.extend(f"Reviewer finding addressed: {item}" for item in matching_review_items[:6])
        work_items.append(
            {
                "id": f"W{index:02d}",
                "objective": f"Repair {name} blockers and reviewer findings from terminal branch {terminal_branch_id}.",
                "owned_paths": paths,
                "context_files": context_files,
                "verification": verification,
                "dod": [
                    f"Paths assigned to this worker resolve terminal {terminal_branch_id} blocker evidence.",
                    "Focused verification commands pass or preserve explicit residual blocker evidence.",
                    *review_dod,
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
        "objective": f"Deterministically repair local files and tests cited by terminal branch {terminal_branch_id} blockers or reviewer findings.",
        "scope": "Repair branch added by deterministic blocker and reviewer-finding diagnosis; terminal branch evidence remains immutable.",
        "branch_name": branch_name,
        "worktree_path": f".worktrees/{branch_name}",
        "depends_on": [],
        "recovers_from": [terminal_branch_id],
        "supersedes": [terminal_branch_id],
        "recovery_mode": "replacement_branch",
        "max_active_worker_packets": CONTRACT.MAX_WORKER_PACKETS_PER_BRANCH,
        "work_items": work_items,
        "tests": branch_tests(base_ref, owned_paths, verification_tests),
        "dod": [
            f"Terminal blocker paths from {terminal_branch_id} are implemented or explicitly narrowed.",
            *(
                [f"Reviewer findings from {review_source} are implemented or explicitly narrowed."]
                if isinstance(review_source, str) and review_items
                else []
            ),
            "Native branch status can be re-evaluated without missing local-file blocker evidence.",
        ],
    }
    if has_overlap:
        branch["contention_reason"] = (
            "Terminal-blocker repair intentionally overlaps prior protected ownership while leaving terminal artifacts immutable."
        )
        branch["worker_contention_reason"] = "Repair workers may touch paths from preserved blocker evidence."
    return branch


def generate_proposal(input_data: dict) -> dict:
    # Fail closed on a malformed --emit-proposal input-files.json: these four fields are consumed
    # by direct subscript below, so a missing/non-string value must be a clean SystemExit.
    for key in ("manifest", "repo_root", "amendment_id", "job_id"):
        if not isinstance(input_data.get(key), str) or not input_data[key].strip():
            raise SystemExit(f"input-files.json missing required string field: {key}")
    manifest_path = Path(input_data["manifest"])
    repo_root = Path(input_data["repo_root"])
    manifest = load_json_object(manifest_path)
    bundle_dir = manifest_path.parent
    branches = branch_map(manifest)
    existing_owned = all_owned_paths(manifest)
    already_recovered = {
        target
        for branch in branches.values()
        if isinstance(branch, dict)
        for key in ("recovers_from", "supersedes")
        for target in _as_list(branch.get(key))
        if isinstance(target, str)
    }
    operations = []
    terminal_ids = [item for item in _as_list(input_data.get("terminal_branch_ids")) if isinstance(item, str)]
    branch_offset = 0
    for terminal_branch_id in terminal_ids:
        branch = branches.get(terminal_branch_id)
        if not isinstance(branch, dict):
            continue
        if terminal_branch_id in already_recovered:
            # Idempotency: this terminal is already recovered/superseded by an existing manifest
            # branch (e.g. a prior deterministic blocker-repair run). Re-proposing would create a
            # duplicate repair branch with the same recovers_from/supersedes target, which the
            # apply-operations validator does not reject.
            continue
        status_path_value = branch.get("status_path")
        if not isinstance(status_path_value, str) or relative_path_defect(
            status_path_value, f"{terminal_branch_id}.status_path"
        ):
            continue
        status_path = bundle_dir / status_path_value
        if not status_path.exists():
            continue
        status = load_json_object(status_path)
        if status.get("status") == "pass":
            continue
        blockers = blockers_from_status(status)
        owned_paths, verification_tests = extract_repair_paths(blockers)
        reviews = input_data.get("terminal_branch_reviews")
        review_evidence = reviews.get(terminal_branch_id) if isinstance(reviews, dict) else None
        review_items = review_items_for_repair(review_evidence)
        review_owned_paths, review_verification_tests = extract_repair_paths(review_items, review_context=True)
        append_unique_many(owned_paths, review_owned_paths)
        append_unique_many(verification_tests, review_verification_tests)
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
                    review_evidence=review_evidence if isinstance(review_evidence, dict) else None,
                    review_items=review_items,
                ),
            }
        )
    return {
        "schema_version": 1,
        "amendment_id": input_data["amendment_id"],
        "job_id": input_data["job_id"],
        "rationale": "Deterministic blocker and reviewer-finding diagnosis converted terminal non-pass evidence into repair branches.",
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
    amendment_id = ensure_amendment_id(input_data.get("amendment_id"))
    bundle_dir = proposal_path.parent.parent
    lineage_path = amendment_lineage_path(bundle_dir, amendment_id)
    lineage = init_lineage(amendment_id)
    proposal_rel = lineage_path_rel(bundle_dir, proposal_path)
    generated_sha = sha256_file(proposal_path)
    add_lineage_stage(lineage, stage="generated_proposal", path=proposal_rel, sha256=generated_sha, parent_sha256=None)
    add_lineage_stage(
        lineage,
        stage="deterministic_repair",
        path=proposal_rel,
        sha256=generated_sha,
        parent_sha256=generated_sha,
    )
    add_lineage_stage(
        lineage,
        stage="final_proposal",
        path=proposal_rel,
        sha256=generated_sha,
        parent_sha256=generated_sha,
    )
    write_json(lineage_path, lineage)
    command = f"python3 {Path(__file__).resolve().as_posix()} --emit-proposal --input-files {input_path.as_posix()} --proposal {proposal_path.as_posix()} --telemetry {telemetry_path.as_posix()}"
    write_json(
        telemetry_path, telemetry(str(input_data["amendment_id"]), proposal_path, input_path, telemetry_path, command)
    )


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
    try:
        policy = validate_amender_model_policy(manifest, manifest_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    bundle_dir = manifest_path.parent
    amendments_dir = bundle_dir / "amendments"
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    if not decision_path.exists():
        raise SystemExit(f"missing launch decision artifact: {decision_path}")
    decision = load_json_object(decision_path)
    if (
        decision.get("schema_version") != 1
        or decision.get("amendment_id") != amendment_id
        or decision.get("decision") != "launch"
    ):
        raise SystemExit(f"amendment decision must be a launch decision for {amendment_id}: {decision_path}")
    if decision.get("manifest") != manifest_path.as_posix() or decision.get("manifest_sha256") != sha256_file(
        manifest_path
    ):
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

    try:
        active, terminal, terminal_status = protected_ids(
            manifest_path,
            manifest,
            active_ids=args.active_branch,
            terminal_ids=args.terminal_branch,
            infer_scheduler=True,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    decision_active = sorted(item for item in _as_list(decision.get("active_branch_ids")) if isinstance(item, str))
    decision_terminal = sorted(item for item in _as_list(decision.get("terminal_branch_ids")) if isinstance(item, str))
    if sorted(active) != decision_active:
        raise SystemExit("amendment decision active_branch_ids do not match packet protected active ids")
    if sorted(terminal) != decision_terminal:
        raise SystemExit("amendment decision terminal_branch_ids do not match packet protected terminal ids")

    records: list[dict] = [
        source_record(manifest_path, "live manifest"),
        source_record(decision_path, "amendment launch decision"),
        source_record(main_prompt, "main prompt"),
    ]
    terminal_reviews: dict[str, dict] = {}
    audit_path = (
        resolve_absolute_path(args.prompt_audit, "--prompt-audit", must_exist=True)
        if args.prompt_audit
        else bundle_dir / "audit" / "prompt-audit.json"
    )
    add_if_exists(records, audit_path, "prompt audit")
    scheduler_path = (
        manifest.get("parallelization", {}).get("scheduler_path")
        if isinstance(manifest.get("parallelization"), dict)
        else None
    )
    if isinstance(scheduler_path, str) and not relative_path_defect(scheduler_path, "scheduler_path"):
        add_if_exists(records, bundle_dir / scheduler_path, "main scheduler")
    for branch in _as_list(manifest.get("branches")):
        if not isinstance(branch, dict) or not isinstance(branch.get("id"), str) or branch["id"] not in terminal:
            continue
        value = branch.get("status_path")
        if isinstance(value, str) and not relative_path_defect(value, "status_path"):
            add_if_exists(records, bundle_dir / value, f"terminal branch status {branch['id']}")
        review_path = source_review_path(branch, bundle_dir)
        if review_path is not None:
            add_if_exists(records, review_path, f"terminal branch review {branch['id']}")
            try:
                terminal_reviews[branch["id"]] = review_evidence_record(review_path)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 -- load_json_object fails closed via SystemExit
                terminal_reviews[branch["id"]] = {
                    "source_review_path": review_path.as_posix(),
                    "source_review_sha256": sha256_file(review_path),
                    "load_error": str(exc),
                }

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
        "terminal_branch_reviews": terminal_reviews,
        "selected_ladder": [],
        "selection_reason": "Deterministic blocker and reviewer-finding diagnosis from terminal artifacts.",
        "route_policy": policy,
        "source_files": records,
    }
    route = {
        "schema_version": 1,
        "packet_id": amendment_id,
        "role": CONTRACT.AMENDER_ROLE,
        "mode": DETERMINISTIC_MODE,
        "selected_ladder": [],
        "selection_reason": "Deterministic blocker and reviewer-finding diagnosis from terminal artifacts.",
        "policy": amender_model_policy(manifest, manifest_path),
        "source_review_paths": sorted(
            item["source_review_path"]
            for item in terminal_reviews.values()
            if isinstance(item, dict) and isinstance(item.get("source_review_path"), str)
        ),
    }
    precomputed_proposal = generate_proposal(packet)
    if not precomputed_proposal.get("operations"):
        no_op_path = amendments_dir / f"{amendment_id}.no-op.json"
        no_op_record = {
            "schema_version": 1,
            "amendment_id": amendment_id,
            "job_id": manifest.get("job_id"),
            "status": "no_legal_future_work",
            "reason_code": "no_legal_future_work",
            "reason": "Deterministic blocker repair found terminal non-pass evidence but no legal future-work operation to propose.",
            "manifest": manifest_path.as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
            "decision_path": decision_path.as_posix(),
            "generated_operations_count": 0,
            "terminal_branch_ids": sorted(terminal),
            "terminal_branch_statuses": {
                branch_id: terminal_status[branch_id] for branch_id in sorted(terminal_status)
            },
            "source_files": records,
        }
        write_json(no_op_path, no_op_record)
        updated_decision = dict(decision)
        updated_decision.update(
            {
                "decision": "skip",
                "reason_code": "no_legal_future_work",
                "reason": no_op_record["reason"],
                "packet_path": None,
                "proposal_path": None,
                "validation_path": None,
                "accepted_path": None,
                "no_op_path": no_op_path.as_posix(),
            }
        )
        write_json(decision_path, updated_decision)
        with contextlib.suppress(OSError):
            packet_dir.rmdir()
        return no_op_path
    write_json(packet_dir / "input-files.json", packet)
    write_json(packet_dir / "route.json", route)
    (packet_dir / "task.md").write_text(
        "Deterministically diagnose terminal branch blockers and write a repair-branch proposal.\n", encoding="utf-8"
    )
    (packet_dir / "prompt.md").write_text(
        "Deterministic local-script packet; no model prompt is used.\n", encoding="utf-8"
    )
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
