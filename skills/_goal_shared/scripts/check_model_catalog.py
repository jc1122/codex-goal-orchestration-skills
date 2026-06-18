#!/usr/bin/env python3
"""Report whether configured Codex route models exist in the local Codex catalog."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CONTRACT = SCRIPT_DIR / "orchestration_contract.py"
CATALOG_TIMEOUT_SECONDS = 30


def load_contract():
    spec = importlib.util.spec_from_file_location("goal_shared_orchestration_contract", CONTRACT)
    if spec is None or spec.loader is None:
        raise SystemExit(f"could not load shared orchestration contract: {CONTRACT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_catalog(*, bundled: bool) -> tuple[dict[str, Any] | None, str]:
    command = ["codex", "debug", "models"]
    if bundled:
        command.append("--bundled")
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=CATALOG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {CATALOG_TIMEOUT_SECONDS}s: {' '.join(command)}"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return None, f"command failed with {result.returncode}: {' '.join(command)}; {detail}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"catalog JSON parse failed: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None, "catalog JSON must contain a models array"
    return data, ""


def load_catalog(source: str) -> tuple[dict[str, Any] | None, str, list[str]]:
    warnings: list[str] = []
    if shutil.which("codex") is None:
        return None, "missing", ["codex CLI is not on PATH"]
    if source in {"live", "auto"}:
        data, warning = run_catalog(bundled=False)
        if data is not None:
            return data, "live", warnings
        warnings.append(f"live catalog unavailable: {warning}")
        if source == "live":
            return None, "live", warnings
    data, warning = run_catalog(bundled=True)
    if data is not None:
        return data, "bundled", warnings
    warnings.append(f"bundled catalog unavailable: {warning}")
    return None, "bundled", warnings


def model_rows(models: list[Any]) -> list[dict[str, Any]]:
    rows = []
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        rows.append(
            {
                "slug": slug,
                "display_name": item.get("display_name"),
                "supported_in_api": item.get("supported_in_api"),
                "visibility": item.get("visibility"),
            }
        )
    return sorted(rows, key=lambda row: row["slug"])


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"{path} does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def safe_manifest_child(manifest_path: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    child = manifest_path.parent / value
    try:
        child.resolve().relative_to(manifest_path.parent.resolve())
    except ValueError:
        return None
    return child


def load_manifest_config(manifest_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    manifest = read_json(manifest_path)
    config_path = safe_manifest_child(manifest_path, manifest.get("goal_config_path"))
    check_path = safe_manifest_child(manifest_path, manifest.get("goal_config_check_path"))
    config = None
    check = None
    if config_path is not None and config_path.exists():
        config = read_json(config_path)
    elif manifest.get("goal_config_path") is not None:
        warnings.append(
            f"manifest goal_config_path does not resolve to an existing bundle file: {manifest.get('goal_config_path')}"
        )
    if check_path is not None and check_path.exists():
        check = read_json(check_path)
    elif manifest.get("goal_config_check_path") is not None:
        warnings.append(
            f"manifest goal_config_check_path does not resolve to an existing bundle file: {manifest.get('goal_config_check_path')}"
        )
    return config, check, warnings


def collect_policy_aliases(value: Any, known_aliases: set[str], target: set[str]) -> None:
    if isinstance(value, str):
        if value in known_aliases:
            target.add(value)
    elif isinstance(value, list):
        for item in value:
            collect_policy_aliases(item, known_aliases, target)
    elif isinstance(value, dict):
        for item in value.values():
            collect_policy_aliases(item, known_aliases, target)


def manifest_route_aliases(config: dict[str, Any]) -> list[str]:
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    known_aliases = {key for key in models if isinstance(key, str)}
    aliases: set[str] = set()
    policies = config.get("model_policies") if isinstance(config.get("model_policies"), dict) else {}
    collect_policy_aliases(policies, known_aliases, aliases)
    ladders = config.get("model_ladders") if isinstance(config.get("model_ladders"), dict) else {}
    collect_policy_aliases(ladders, known_aliases, aliases)
    if not aliases:
        aliases.update(known_aliases)
    return sorted(aliases)


def harness_report_by_role(check: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if check is None:
        return {}
    reports = check.get("harnesses")
    if not isinstance(reports, list):
        return {}
    return {str(item["role"]): item for item in reports if isinstance(item, dict) and isinstance(item.get("role"), str)}


def configured_route_rows(
    *,
    config: dict[str, Any],
    check: dict[str, Any] | None,
    codex_by_slug: dict[str, dict[str, Any]],
    codex_catalog_loaded: bool,
    require_codex: bool,
    bridge_aliases: frozenset[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    harnesses = config.get("harnesses") if isinstance(config.get("harnesses"), dict) else {}
    checks_by_role = harness_report_by_role(check)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for alias in manifest_route_aliases(config):
        model = models.get(alias)
        if not isinstance(model, dict):
            rows.append({"alias": alias, "status": "failed", "reason": "missing model role"})
            failures.append(f"{alias}: missing model role")
            continue
        harness_name = model.get("harness")
        harness = harnesses.get(harness_name) if isinstance(harness_name, str) else None
        harness_kind = harness.get("kind") if isinstance(harness, dict) else None
        check_report = checks_by_role.get(alias, {})
        model_check = check_report.get("model_check") if isinstance(check_report.get("model_check"), dict) else {}
        smoke = check_report.get("smoke") if isinstance(check_report.get("smoke"), dict) else {}
        configured_model = str(model.get("model")) if model.get("model") is not None else ""
        # Bridge aliases (deepseek via opencode-bridge) are never validated
        # against the Codex catalog, even if a manifest mislabels their harness
        # kind as "codex" — bridge-route readiness is the bridge's concern.
        is_codex_route = harness_kind == "codex" and alias not in bridge_aliases
        codex_catalog = codex_by_slug.get(configured_model) if is_codex_route and codex_catalog_loaded else None
        status = "pass"
        reason = ""
        if not isinstance(harness, dict):
            status = "failed"
            reason = f"unknown harness: {harness_name!r}"
            failures.append(f"{alias}: {reason}")
        elif is_codex_route and not codex_catalog_loaded and require_codex:
            status = "failed"
            reason = "Codex catalog unavailable"
            failures.append(f"{alias}: {reason}")
        elif is_codex_route and codex_catalog_loaded and codex_catalog is None:
            status = "failed"
            reason = f"configured Codex model absent from catalog: {configured_model}"
            failures.append(f"{alias}: {reason}")
        elif check is not None and model_check.get("status") not in {None, "pass"}:
            status = "failed"
            reason = f"model_check status is {model_check.get('status')!r}"
            failures.append(f"{alias}: {reason}")
        rows.append(
            {
                "alias": alias,
                "harness": harness_name,
                "harness_kind": harness_kind,
                "provider": model.get("provider"),
                "model": model.get("model"),
                "present": (
                    codex_catalog is not None
                    if is_codex_route and codex_catalog_loaded
                    else (model_check.get("status") == "pass" if model_check else None)
                ),
                "supported_in_api": codex_catalog.get("supported_in_api") if codex_catalog else None,
                "visibility": codex_catalog.get("visibility") if codex_catalog else None,
                "model_check_status": model_check.get("status"),
                "smoke_status": smoke.get("status"),
                "packet_runner_viable": smoke.get("status") == "pass" if smoke else None,
                "status": status,
                "reason": reason,
            }
        )
    return rows, failures


def build_report(*, source: str, require_codex: bool, manifest: Path | None = None) -> dict[str, Any]:
    contract = load_contract()
    catalog, actual_source, warnings = load_catalog(source)
    # Only native codex/gpt route aliases are validated against the local Codex
    # catalog. Bridge aliases (deepseek via opencode-bridge) are NOT codex
    # models, so exclude them defensively via is_bridge_alias() — they are
    # recorded as bridge-managed and never flagged missing from the codex
    # catalog (bridge-route readiness is the bridge's concern, not this gate).
    route_models = {
        alias: model
        for alias, model in sorted(contract.CODEX_ROUTE_MODELS.items())
        if not contract.is_bridge_alias(alias)
    }
    bridge_route_models = [
        {
            "alias": alias,
            "model": contract.bridge_model(alias),
            "provider": contract.BRIDGE_PROVIDER_ID,
            "harness_kind": contract.BRIDGE_HARNESS_KIND,
            "codex_catalog_validated": False,
            "note": "managed by opencode-bridge / not codex-catalog validated",
        }
        for alias in contract.BRIDGE_ROUTE_ALIASES
    ]
    codex_by_slug: dict[str, dict[str, Any]] = {}
    if catalog is None:
        status = "failed" if require_codex else "skipped"
        report = {
            "schema_version": 1,
            "status": status,
            "source": actual_source,
            "warnings": warnings,
            "models": [],
            "route_models": [
                {"alias": alias, "model": model, "present": None, "supported_in_api": None, "visibility": None}
                for alias, model in route_models.items()
            ],
            "missing_route_models": [],
            "bridge_route_models": bridge_route_models,
        }
        if manifest is None:
            return report
        route_rows = report["route_models"]
    else:
        models = model_rows(catalog["models"])
        codex_by_slug = {row["slug"]: row for row in models}

        route_rows = []
        missing = []
        for alias, model in route_models.items():
            found = codex_by_slug.get(model)
            present = found is not None
            if not present:
                missing.append(f"{alias} -> {model}")
            route_rows.append(
                {
                    "alias": alias,
                    "model": model,
                    "present": present,
                    "supported_in_api": found.get("supported_in_api") if found else None,
                    "visibility": found.get("visibility") if found else None,
                }
            )

        status = "pass"
        if missing and actual_source == "live":
            status = "failed"
        elif missing:
            status = "warning"
            warnings.append(
                "bundled catalog may be stale; route model absence is advisory unless live catalog also misses it"
            )

        report = {
            "schema_version": 1,
            "status": status,
            "source": actual_source,
            "warnings": warnings,
            "models": models,
            "route_models": route_rows,
            "missing_route_models": missing,
            "bridge_route_models": bridge_route_models,
        }
    if manifest is not None:
        config, check, config_warnings = load_manifest_config(manifest)
        report["warnings"].extend(config_warnings)
        report["manifest_path"] = manifest.as_posix()
        report["goal_config_path"] = (manifest.parent / "goal.config.json").as_posix() if config is not None else None
        report["goal_config_check_path"] = (
            (manifest.parent / "goal-config.check.json").as_posix() if check is not None else None
        )
        if config is None:
            report["checked_aliases"] = [row["alias"] for row in route_rows]
            if config_warnings:
                report["status"] = "failed" if require_codex else "warning"
            return report
        configured_rows, configured_failures = configured_route_rows(
            config=config,
            check=check,
            codex_by_slug=codex_by_slug,
            codex_catalog_loaded=catalog is not None,
            require_codex=require_codex,
            bridge_aliases=frozenset(contract.BRIDGE_ROUTE_ALIASES),
        )
        report["configured_route_models"] = configured_rows
        report["checked_aliases"] = [row["alias"] for row in configured_rows]
        report["checked_harnesses"] = sorted(
            {str(row["harness"]) for row in configured_rows if isinstance(row.get("harness"), str)}
        )
        if check is None:
            report["warnings"].append("goal-config.check.json is missing; packet-runner viability is unknown")
        elif check.get("status") != "pass":
            configured_failures.append(f"goal-config.check.json status is {check.get('status')!r}")
        if configured_failures:
            report["status"] = "failed"
            report["configured_route_failures"] = configured_failures
        elif report["status"] == "skipped":
            report["status"] = "pass"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print a machine-readable model catalog report.")
    parser.add_argument(
        "--check", action="store_true", help="Print a concise check result and fail on live catalog mismatches."
    )
    parser.add_argument(
        "--source", choices=("auto", "live", "bundled"), default="auto", help="Catalog source to inspect."
    )
    parser.add_argument(
        "--require-codex",
        action="store_true",
        help="Fail instead of skipping when the Codex CLI/catalog is unavailable.",
    )
    parser.add_argument(
        "--manifest",
        help="Accepted for runtime command compatibility; model catalog checks are account/CLI scoped and ignore this path.",
    )
    args = parser.parse_args()

    if bool(args.json) == bool(args.check):
        raise SystemExit("choose exactly one of --json or --check")

    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    report = build_report(source=args.source, require_codex=args.require_codex, manifest=manifest_path)
    if args.json:
        print(json.dumps(report, indent=2) + "\n", end="")
    else:
        print(f"status={report['status']} source={report['source']}")
        for row in report["route_models"]:
            present = "yes" if row["present"] is True else "no" if row["present"] is False else "unknown"
            print(
                f"- {row['alias']}: model={row['model']} present={present} "
                f"supported_in_api={row['supported_in_api']} visibility={row['visibility']}"
            )
        for row in report.get("bridge_route_models", []):
            print(
                f"- bridge {row['alias']}: model={row['model']} "
                f"provider={row['provider']} harness_kind={row['harness_kind']} "
                f"codex_catalog_validated={row['codex_catalog_validated']}"
            )
        for row in report.get("configured_route_models", []):
            print(
                f"- configured {row['alias']}: harness={row.get('harness')} "
                f"kind={row.get('harness_kind')} model={row.get('model')} "
                f"model_check={row.get('model_check_status')} smoke={row.get('smoke_status')}"
            )
        for warning in report.get("warnings", []):
            print(f"warning: {warning}", file=sys.stderr)

    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
