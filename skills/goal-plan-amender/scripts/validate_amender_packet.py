#!/usr/bin/env python3
"""Validate a route-bound plan-amender packet and write packet validation evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from amendment_lib import CONTRACT, ensure_amendment_id, load_json_object, resolve_absolute_path, sha256_file, write_json


def defect(defects: list[str], path: str, message: str) -> None:
    defects.append(f"{path}: {message}")


def require_object(defects: list[str], value: object, path: str) -> dict:
    if not isinstance(value, dict):
        defect(defects, path, "must be an object")
        return {}
    return value


def require_string(defects: list[str], value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        defect(defects, path, "must be a non-empty string")
        return ""
    return value


def require_string_list(defects: list[str], value: object, path: str, *, min_items: int = 0) -> list[str]:
    if not isinstance(value, list) or len(value) < min_items:
        defect(defects, path, f"must be an array with at least {min_items} item(s)")
        return []
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            defect(defects, f"{path}[{index}]", "must be a non-empty string")
            continue
        result.append(item)
    return result


def load_json_for_validation(defects: list[str], path: Path, label: str) -> dict:
    if not path.exists():
        defect(defects, label, f"does not exist: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        defect(defects, label, f"must be readable JSON: {exc}")
        return {}
    return require_object(defects, data, label)


def validate_decision(defects: list[str], decision: dict, *, amendment_id: str, manifest_path: Path) -> None:
    if decision.get("schema_version") != 1:
        defect(defects, "$.decision.schema_version", "must be 1")
    if decision.get("amendment_id") != amendment_id:
        defect(defects, "$.decision.amendment_id", f"must be {amendment_id!r}")
    if decision.get("decision") != "launch":
        defect(defects, "$.decision.decision", "must be 'launch'")
    if decision.get("reason_code") not in CONTRACT.AMENDMENT_LAUNCH_REASON_CODES:
        defect(defects, "$.decision.reason_code", f"must be one of {list(CONTRACT.AMENDMENT_LAUNCH_REASON_CODES)}")
    require_string(defects, decision.get("reason"), "$.decision.reason")
    if decision.get("manifest") != manifest_path.as_posix():
        defect(defects, "$.decision.manifest", "must match --manifest")
    if decision.get("manifest_sha256") != sha256_file(manifest_path):
        defect(defects, "$.decision.manifest_sha256", "must match current job.manifest.json")
    require_string_list(defects, decision.get("terminal_branch_ids"), "$.decision.terminal_branch_ids", min_items=1)


def validate_route(defects: list[str], route: dict, *, amendment_id: str) -> list[str]:
    if route.get("schema_version") != 1:
        defect(defects, "$.route.schema_version", "must be 1")
    if route.get("packet_id") != amendment_id:
        defect(defects, "$.route.packet_id", f"must be {amendment_id!r}")
    if route.get("role") != CONTRACT.AMENDER_ROLE:
        defect(defects, "$.route.role", f"must be {CONTRACT.AMENDER_ROLE!r}")
    selected = require_string_list(defects, route.get("selected_ladder"), "$.route.selected_ladder", min_items=1)
    try:
        normalized = CONTRACT.normalize_route_ladder(
            selected,
            default_ladder=CONTRACT.DEFAULT_AMENDER_LADDER,
            allowed_routes=CONTRACT.ALLOWED_AMENDER_ROUTES,
            route_name="amender",
        )
    except ValueError as exc:
        defect(defects, "$.route.selected_ladder", str(exc))
        normalized = selected
    if selected and normalized != selected:
        defect(defects, "$.route.selected_ladder", "must preserve allowed route order exactly")
    require_string(defects, route.get("selection_reason"), "$.route.selection_reason")
    if route.get("policy") != CONTRACT.AMENDER_MODEL_POLICY:
        defect(defects, "$.route.policy", "must match shared amender_model_policy")
    return selected


def validate_input_files(
    defects: list[str],
    data: dict,
    *,
    amendment_id: str,
    manifest_path: Path,
    decision_path: Path,
    route: dict,
) -> None:
    if data.get("schema_version") != 1:
        defect(defects, "$.input_files.schema_version", "must be 1")
    if data.get("amendment_id") != amendment_id:
        defect(defects, "$.input_files.amendment_id", f"must be {amendment_id!r}")
    if data.get("manifest") != manifest_path.as_posix():
        defect(defects, "$.input_files.manifest", "must match --manifest")
    if data.get("decision_path") != decision_path.as_posix():
        defect(defects, "$.input_files.decision_path", "must match amendment decision artifact")
    if data.get("selected_ladder") != route.get("selected_ladder"):
        defect(defects, "$.input_files.selected_ladder", "must match route.json")
    if data.get("selection_reason") != route.get("selection_reason"):
        defect(defects, "$.input_files.selection_reason", "must match route.json")
    sources = data.get("source_files")
    if not isinstance(sources, list) or not sources:
        defect(defects, "$.input_files.source_files", "must be a non-empty array")
        return
    for index, item in enumerate(sources):
        item_path = f"$.input_files.source_files[{index}]"
        source = require_object(defects, item, item_path)
        path_text = require_string(defects, source.get("path"), f"{item_path}.path")
        expected_sha = require_string(defects, source.get("sha256"), f"{item_path}.sha256")
        if not path_text:
            continue
        source_path = Path(path_text)
        if not source_path.exists():
            defect(defects, f"{item_path}.path", f"source file does not exist: {source_path}")
        elif expected_sha and expected_sha != sha256_file(source_path):
            defect(defects, f"{item_path}.sha256", "does not match current source file")


def validate_telemetry(defects: list[str], telemetry: dict, *, amendment_id: str, route: dict, proposal_name: str) -> None:
    if telemetry.get("schema_version") != 1:
        defect(defects, "$.telemetry.schema_version", "must be 1")
    if telemetry.get("packet_id") != amendment_id:
        defect(defects, "$.telemetry.packet_id", f"must be {amendment_id!r}")
    if telemetry.get("role") != CONTRACT.AMENDER_ROLE:
        defect(defects, "$.telemetry.role", f"must be {CONTRACT.AMENDER_ROLE!r}")
    if telemetry.get("output_artifact") != f"../{proposal_name}":
        defect(defects, "$.telemetry.output_artifact", f"must be '../{proposal_name}'")
    attempts = telemetry.get("attempts")
    selected = route.get("selected_ladder") if isinstance(route.get("selected_ladder"), list) else []
    if not isinstance(attempts, list):
        defect(defects, "$.telemetry.attempts", "must be an array")
        return
    aliases = [item.get("alias") for item in attempts if isinstance(item, dict)]
    if aliases != selected:
        defect(defects, "$.telemetry.attempts", "declared aliases must match route.json selected_ladder exactly")
    called_aliases = [item.get("alias") for item in attempts if isinstance(item, dict) and item.get("called") is True]
    if called_aliases != selected[: len(called_aliases)]:
        defect(defects, "$.telemetry.attempts", "called aliases must be a prefix of route.json selected_ladder")
    accepted = [item.get("alias") for item in attempts if isinstance(item, dict) and item.get("accepted") is True]
    if len(accepted) > 1:
        defect(defects, "$.telemetry.attempts", "must mark at most one accepted attempt")
    if telemetry.get("accepted_alias") is not None and telemetry.get("accepted_alias") not in accepted:
        defect(defects, "$.telemetry.accepted_alias", "must match the accepted attempt alias")
    for index, item in enumerate(attempts):
        attempt = require_object(defects, item, f"$.telemetry.attempts[{index}]")
        if attempt.get("timeout_seconds") != CONTRACT.AMENDER_ATTEMPT_TIMEOUT_SECONDS:
            defect(defects, f"$.telemetry.attempts[{index}].timeout_seconds", f"must be {CONTRACT.AMENDER_ATTEMPT_TIMEOUT_SECONDS}")
        alias = attempt.get("alias")
        if isinstance(alias, str) and alias in CONTRACT.ALLOWED_AMENDER_ROUTES:
            expected_model = CONTRACT.codex_model(alias)
            if attempt.get("model") != expected_model:
                defect(defects, f"$.telemetry.attempts[{index}].model", f"must be {expected_model!r}")
        if attempt.get("provider") != "codex":
            defect(defects, f"$.telemetry.attempts[{index}].provider", "must be 'codex'")


def validate_packet(*, manifest_path: Path, amendment_id: str, packet_dir: Path) -> dict:
    defects: list[str] = []
    manifest = load_json_for_validation(defects, manifest_path, "$.manifest")
    if manifest and manifest.get("amender_model_policy") != CONTRACT.AMENDER_MODEL_POLICY:
        defect(defects, "$.manifest.amender_model_policy", "must match shared amender_model_policy")
    if packet_dir.name != f"{amendment_id}.packet":
        defect(defects, "$.packet_dir", f"must be named {amendment_id}.packet")
    amendments_dir = packet_dir.parent
    decision_path = amendments_dir / f"{amendment_id}.decision.json"
    route_path = packet_dir / "route.json"
    input_path = packet_dir / "input-files.json"
    telemetry_path = packet_dir / "telemetry.json"
    proposal_name = f"{amendment_id}.proposal.json"
    proposal_path = amendments_dir / proposal_name

    decision = load_json_for_validation(defects, decision_path, "$.decision")
    route = load_json_for_validation(defects, route_path, "$.route")
    inputs = load_json_for_validation(defects, input_path, "$.input_files")
    telemetry = load_json_for_validation(defects, telemetry_path, "$.telemetry")
    proposal = load_json_for_validation(defects, proposal_path, "$.proposal")

    validate_decision(defects, decision, amendment_id=amendment_id, manifest_path=manifest_path)
    validate_route(defects, route, amendment_id=amendment_id)
    validate_input_files(
        defects,
        inputs,
        amendment_id=amendment_id,
        manifest_path=manifest_path,
        decision_path=decision_path,
        route=route,
    )
    validate_telemetry(defects, telemetry, amendment_id=amendment_id, route=route, proposal_name=proposal_name)
    if proposal:
        if proposal.get("schema_version") != 1:
            defect(defects, "$.proposal.schema_version", "must be 1")
        if proposal.get("amendment_id") != amendment_id:
            defect(defects, "$.proposal.amendment_id", f"must be {amendment_id!r}")
        if proposal.get("job_id") != manifest.get("job_id"):
            defect(defects, "$.proposal.job_id", "must match manifest job_id")
        if not isinstance(proposal.get("operations"), list):
            defect(defects, "$.proposal.operations", "must be an array")

    return {
        "schema_version": 1,
        "amendment_id": amendment_id,
        "status": "pass" if not defects else "failed",
        "manifest": manifest_path.as_posix(),
        "packet_dir": packet_dir.as_posix(),
        "decision": decision_path.as_posix(),
        "route": route_path.as_posix(),
        "telemetry": telemetry_path.as_posix(),
        "proposal": proposal_path.as_posix(),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.exists() else None,
        "proposal_sha256": sha256_file(proposal_path) if proposal_path.exists() else None,
        "defects": defects,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--amendment-id", required=True)
    parser.add_argument("--packet-dir")
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest_path = resolve_absolute_path(args.manifest, "--manifest", must_exist=True)
    amendment_id = ensure_amendment_id(args.amendment_id)
    default_packet = manifest_path.parent / "amendments" / f"{amendment_id}.packet"
    packet_dir = resolve_absolute_path(args.packet_dir, "--packet-dir", must_exist=True) if args.packet_dir else default_packet
    result = validate_packet(manifest_path=manifest_path, amendment_id=amendment_id, packet_dir=packet_dir)
    output_path = Path(args.output).resolve() if args.output else packet_dir / "packet.validation.json"
    write_json(output_path, result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']}")
        for item in result["defects"]:
            print(item)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
