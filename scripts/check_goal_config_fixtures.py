#!/usr/bin/env python3
"""Fixture checks for goal-config deterministic helpers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOAL_CONFIG = ROOT / "skills" / "goal-config"
CREATE = GOAL_CONFIG / "scripts" / "create_goal_config.py"
CHECK = GOAL_CONFIG / "scripts" / "check_goal_config.py"
SCAN = GOAL_CONFIG / "scripts" / "scan_configurables.py"


def run(command: list[str], *, expect: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != expect:
        print(f"command failed with {result.returncode}, expected {expect}: {' '.join(command)}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr)
        raise SystemExit(1)
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="goal-config-fixtures-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "goal.config.json"
        report_path = tmp_path / "goal-config-check.json"
        models_path = tmp_path / "deepseek-models.txt"
        missing_models_path = tmp_path / "missing-models.txt"
        models_path.write_text(
            "deepseek/deepseek-chat\n"
            "deepseek/deepseek-reasoner\n"
            "deepseek/deepseek-v4-flash\n"
            "deepseek/deepseek-v4-pro\n",
            encoding="utf-8",
        )
        missing_models_path.write_text("deepseek/deepseek-chat\n", encoding="utf-8")

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "opencode-deepseek-v4",
                "--output",
                config_path.as_posix(),
            ]
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        serialized = json.dumps(config, sort_keys=True).lower()
        require(config["models"]["lite_agent"]["model"] == "deepseek/deepseek-v4-flash", "lite model mismatch")
        require(config["models"]["demanding_agent"]["model"] == "deepseek/deepseek-v4-pro", "demanding model mismatch")
        for forbidden in ("usd", "dollar", "pricing", "price"):
            require(forbidden not in serialized, f"config must not contain billing field or unit: {forbidden}")

        scan = run([sys.executable, SCAN.as_posix(), "--json"]).stdout
        inventory = json.loads(scan)
        for category in ("aggressiveness", "timeouts", "worker_routes", "lite", "telemetry", "harnesses"):
            require(category in inventory["categories"], f"missing inventory category: {category}")

        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--models-output",
                models_path.as_posix(),
                "--output",
                report_path.as_posix(),
            ]
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report["status"] == "pass", "fixture model check should pass")
        require(len(report["harnesses"]) == 2, "expected two harness reports")
        report_serialized = json.dumps(report, sort_keys=True).lower()
        for forbidden in ("cost", "usd", "dollar", "pricing", "price"):
            require(forbidden not in report_serialized, f"report must not contain billing field or unit: {forbidden}")

        failed = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--models-output",
                missing_models_path.as_posix(),
            ],
            expect=1,
        ).stdout
        missing_report = json.loads(failed)
        require(missing_report["status"] == "failed", "missing model fixture should fail")
        require(missing_report["failures"], "missing model fixture should record failures")

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
