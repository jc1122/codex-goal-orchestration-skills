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
CREATE_BUNDLE = ROOT / "skills" / "goal-preflight" / "scripts" / "create_goal_bundle.py"
LINT_BUNDLE = ROOT / "skills" / "goal-preflight" / "scripts" / "lint_goal_bundle.py"
CREATE_PACKET = ROOT / "skills" / "goal-branch-orchestrator" / "scripts" / "create_runtime_packet.py"


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


def build_generic_cli_config(base_path: Path, source_path: Path) -> None:
    config = json.loads(source_path.read_text(encoding="utf-8"))
    config["harnesses"]["antigravity"] = {
        "kind": "generic-cli",
        "command": "python3",
        "smoke_args": ["-c", "import sys; print(sys.argv[1])", "{prompt}"],
    }
    config["models"]["generic_agent"] = {
        "alias": "generic-agent",
        "role": "generic_agent",
        "harness": "antigravity",
        "provider": "local",
        "model": "local/generic-agent",
        "purpose": "fixture generic CLI smoke role",
    }
    config["harness_smokes"]["generic_agent"] = {
        "prompt": "GENERIC_CLI_SMOKE_OK",
        "expect": "GENERIC_CLI_SMOKE_OK",
        "timeout_seconds": 10,
        "readback": "stdout",
    }
    base_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_integration_brief(path: Path) -> None:
    brief = {
        "job_id": "config-integration-fixture",
        "title": "Config integration fixture",
        "base_ref": "main",
        "goal": "Verify checked goal-config profiles are consumed by preflight and runtime packet generation.",
        "source_summary": "Fixture brief for deterministic config integration coverage.",
        "required_evidence": [
            "Bundle lint passes with embedded goal config.",
            "Worker launch config uses the configured harness attempts.",
        ],
        "final_dod": ["Configured model policy is visible in generated runtime packets."],
        "max_active_branch_agents": 1,
        "parallelization_rationale": "Fixture uses one independent branch.",
        "branches": [
            {
                "id": "B01",
                "branch_name": "config-integration-fixture",
                "objective": "Create one configured worker packet.",
                "worktree_path": ".worktrees/config-integration-fixture",
                "work_items": [
                    {
                        "id": "W01",
                        "objective": "Exercise configured worker route generation.",
                        "owned_paths": ["README.md"],
                        "context_files": ["README.md"],
                        "depends_on": [],
                        "route_class": "normal-code",
                        "verification": ["true"],
                        "dod": ["Launch config contains the configured harness and model ladder."],
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(brief, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_integration_fixture(tmp_path: Path, config_path: Path, report_path: Path) -> None:
    brief_path = tmp_path / "integration-brief.json"
    bundle_dir = tmp_path / "bundle"
    packet_root = tmp_path / "packets"
    build_integration_brief(brief_path)

    run(
        [
            sys.executable,
            CREATE_BUNDLE.as_posix(),
            "--brief",
            brief_path.as_posix(),
            "--repo-root",
            ROOT.as_posix(),
            "--out-dir",
            bundle_dir.as_posix(),
            "--goal-config",
            config_path.as_posix(),
            "--goal-config-check",
            report_path.as_posix(),
        ]
    )
    run([sys.executable, LINT_BUNDLE.as_posix(), "--bundle-dir", bundle_dir.as_posix()])

    manifest_path = bundle_dir / "job.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("goal_config_path") == "goal.config.json", "manifest must reference embedded goal config")
    require(manifest.get("goal_config_check_path") == "goal-config.check.json", "manifest must reference embedded goal config check")
    require((bundle_dir / "goal.config.json").exists(), "bundle must copy goal.config.json")
    require((bundle_dir / "goal-config.check.json").exists(), "bundle must copy goal-config.check.json")
    require(manifest.get("goal_config_check", {}).get("status") == "pass", "embedded goal config check must pass")
    require(
        manifest["worker_model_policy"]["default_ladder"] == ["demanding_agent", "lite_agent"],
        "manifest worker model policy should come from goal config",
    )
    require(
        manifest["review_model_policy"]["routes"]["standard"] == ["demanding_agent"],
        "manifest reviewer policy should come from goal config",
    )

    run(
        [
            sys.executable,
            CREATE_PACKET.as_posix(),
            "--role",
            "worker",
            "--packet-id",
            "B01-W01",
            "--branch",
            "B01",
            "--worktree",
            ROOT.as_posix(),
            "--out-dir",
            packet_root.as_posix(),
            "--manifest",
            manifest_path.as_posix(),
            "--task-file",
            (bundle_dir / "branches" / "B01.prompt.md").as_posix(),
            "--owned-file",
            "README.md",
            "--context-file",
            (ROOT / "README.md").as_posix(),
            "--replace",
        ]
    )
    packet_dir = packet_root / "B01-W01"
    route = json.loads((packet_dir / "route.json").read_text(encoding="utf-8"))
    launch_config = json.loads((packet_dir / "launch-config.json").read_text(encoding="utf-8"))
    require(route["selected_ladder"] == ["demanding_agent", "lite_agent"], "packet route must use configured ladder")
    require(route["default_ladder"] == ["demanding_agent", "lite_agent"], "packet default ladder must use config")
    require("goal_config" in route["selection_reason"], "packet route reason should cite goal_config")
    require("Codex Spark" not in route["selection_reason"], "configured route reason must not cite legacy Codex ladder")
    attempts = launch_config.get("attempts", [])
    require(len(attempts) == 2, "configured worker launch should have two attempts")
    require(attempts[0]["alias"] == "demanding_agent", "first attempt should be demanding agent")
    require(attempts[0]["harness_kind"] == "opencode", "first attempt should use opencode")
    require(attempts[0]["model"] == "deepseek/deepseek-v4-pro", "first attempt model mismatch")
    require(attempts[1]["alias"] == "lite_agent", "second attempt should be lite agent")
    require(attempts[1]["harness_kind"] == "opencode", "second attempt should use opencode")
    require(attempts[1]["model"] == "deepseek/deepseek-v4-flash", "second attempt model mismatch")
    require(attempts[0].get("run_args"), "configured harness attempts must carry rendered run args")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="goal-config-fixtures-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "goal.config.json"
        report_path = tmp_path / "goal-config-check.json"
        generic_config_path = tmp_path / "goal.config.generic.json"
        models_path = tmp_path / "deepseek-models.txt"
        missing_models_path = tmp_path / "missing-models.txt"
        billing_config_path = tmp_path / "goal.config.billing.json"
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
            require(
                forbidden not in serialized,
                f"config must not contain billing field or unit: {forbidden}",
            )

        scan = run([sys.executable, SCAN.as_posix(), "--json"]).stdout
        inventory = json.loads(scan)
        for category in (
            "aggressiveness",
            "timeouts",
            "worker_routes",
            "lite",
            "telemetry",
            "harnesses",
        ):
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
            require(
                forbidden not in report_serialized,
                f"report must not contain billing field or unit: {forbidden}",
            )

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "opencode-deepseek-v4",
                "--role-model",
                "reporting_agent:opencode:deepseek/deepseek-v4-flash",
                "--output",
                generic_config_path.as_posix(),
            ]
        )
        configurable_config = json.loads(generic_config_path.read_text(encoding="utf-8"))
        require(
            configurable_config["models"].get("reporting_agent", {}).get("harness") == "opencode",
            "role-model override did not set harness",
        )
        require(
            configurable_config["models"].get("reporting_agent", {}).get("model") == "deepseek/deepseek-v4-flash",
            "role-model override did not set model",
        )
        require(
            "reporting_agent" in configurable_config["models"],
            "role-model override did not add role",
        )

        custom_smoke_path = tmp_path / "goal.config.generic-smoke.json"
        build_generic_cli_config(custom_smoke_path, config_path)
        generic_report_path = tmp_path / "goal-config-generic-smoke.json"
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                custom_smoke_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "generic_agent",
                "--models-output",
                models_path.as_posix(),
                "--output",
                generic_report_path.as_posix(),
            ]
        )
        generic_report = json.loads(generic_report_path.read_text(encoding="utf-8"))
        require(generic_report["status"] == "pass", "generic cli smoke should pass")
        generic_smoke = generic_report["harnesses"][0]["smoke"]
        require(
            generic_smoke.get("contains_expected") is True,
            "generic cli smoke output must match expected",
        )

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

        billing_config = json.loads(config_path.read_text(encoding="utf-8"))
        billing_config["billing"] = {"estimated_price_usd": 0.0}
        billing_config_path.write_text(json.dumps(billing_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        billing_failed = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                billing_config_path.as_posix(),
                "--require-models",
            ],
            expect=1,
        ).stdout
        billing_report = json.loads(billing_failed)
        require(billing_report["status"] == "failed", "billing-bearing config should fail")
        require(
            any("billing" in failure.lower() for failure in billing_report["failures"]),
            "billing-bearing config should fail with billing rejection",
        )
        run_integration_fixture(tmp_path, config_path, report_path)

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
