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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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


def build_report(*, source: str, require_codex: bool) -> dict[str, Any]:
    contract = load_contract()
    catalog, actual_source, warnings = load_catalog(source)
    route_models = dict(sorted(contract.CODEX_ROUTE_MODELS.items()))
    if catalog is None:
        status = "failed" if require_codex else "skipped"
        return {
            "schema_version": 1,
            "status": status,
            "source": actual_source,
            "warnings": warnings,
            "models": [],
            "route_models": [
                {"alias": alias, "model": model, "present": None, "supported_in_api": None, "visibility": None}
                for alias, model in route_models.items()
            ],
        }

    models = model_rows(catalog["models"])
    by_slug = {row["slug"]: row for row in models}
    route_rows = []
    missing = []
    for alias, model in route_models.items():
        found = by_slug.get(model)
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
        warnings.append("bundled catalog may be stale; route model absence is advisory unless live catalog also misses it")

    return {
        "schema_version": 1,
        "status": status,
        "source": actual_source,
        "warnings": warnings,
        "models": models,
        "route_models": route_rows,
        "missing_route_models": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print a machine-readable model catalog report.")
    parser.add_argument("--check", action="store_true", help="Print a concise check result and fail on live catalog mismatches.")
    parser.add_argument("--source", choices=("auto", "live", "bundled"), default="auto", help="Catalog source to inspect.")
    parser.add_argument("--require-codex", action="store_true", help="Fail instead of skipping when the Codex CLI/catalog is unavailable.")
    parser.add_argument(
        "--manifest",
        help="Accepted for runtime command compatibility; model catalog checks are account/CLI scoped and ignore this path.",
    )
    args = parser.parse_args()

    if bool(args.json) == bool(args.check):
        raise SystemExit("choose exactly one of --json or --check")

    report = build_report(source=args.source, require_codex=args.require_codex)
    if args.manifest:
        report.setdefault("warnings", []).append("--manifest is accepted for compatibility and ignored")
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
        for warning in report.get("warnings", []):
            print(f"warning: {warning}", file=sys.stderr)

    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
