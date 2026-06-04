#!/usr/bin/env python3
"""Fixture checks for goal-config deterministic helpers."""

from __future__ import annotations

import json
import os
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


def run(command: list[str], *, expect: int = 0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=command_env,
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


def write_fake_codex_catalog(bin_dir: Path, models: list[str]) -> None:
    catalog = {
        "models": [
            {
                "slug": model,
                "display_name": model,
                "supported_in_api": True,
                "visibility": "fixture",
            }
            for model in models
        ]
    }
    catalog_json = json.dumps(catalog, sort_keys=True).replace("'", "'\"'\"'")
    script = bin_dir / "codex"
    script.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" != \"debug\" ] || [ \"$2\" != \"models\" ]; then\n"
        "  exit 2\n"
        "fi\n"
        f"printf '%s\\n' '{catalog_json}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def build_generic_cli_config(base_path: Path, source_path: Path) -> None:
    config = json.loads(source_path.read_text(encoding="utf-8"))
    config["harnesses"]["antigravity"] = {
        "kind": "generic-cli",
        "command": "python3",
        "smoke_args": ["-c", "import sys; print('cli boilerplate before'); print(sys.argv[1]); print('cli boilerplate after')", "{prompt}"],
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


def build_counting_generic_cli_config(base_path: Path, source_path: Path, count_path: Path) -> None:
    config = json.loads(source_path.read_text(encoding="utf-8"))
    config["harnesses"]["antigravity"] = {
        "kind": "generic-cli",
        "command": "python3",
        "smoke_args": [
            "-c",
            "import pathlib, sys; p = pathlib.Path(sys.argv[2]); "
            "n = int(p.read_text() or '0') if p.exists() else 0; "
            "p.write_text(str(n + 1)); print(sys.argv[1])",
            "{prompt}",
            count_path.as_posix(),
        ],
    }
    for role in ("generic_agent", "generic_agent_copy"):
        config["models"][role] = {
            "alias": role,
            "role": role,
            "harness": "antigravity",
            "provider": "local",
            "model": "local/reused-generic-agent",
            "purpose": "fixture route-level smoke reuse",
        }
        config["harness_smokes"][role] = {
            "prompt": f"{role.upper()}_SMOKE_OK",
            "expect": f"{role.upper()}_SMOKE_OK",
            "timeout_seconds": 10,
            "readback": "stdout",
        }
    base_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_fake_opencode_discovery_config(
    base_path: Path,
    source_path: Path,
    *,
    list_script: Path,
    smoke_script: Path,
    count_path: Path,
    db_path: Path,
) -> None:
    config = json.loads(source_path.read_text(encoding="utf-8"))
    config["harnesses"]["opencode"]["command"] = "python3"
    config["harnesses"]["opencode"]["model_list_args"] = [
        list_script.as_posix(),
        count_path.as_posix(),
        "{provider}",
    ]
    config["harnesses"]["opencode"]["smoke_args"] = [
        smoke_script.as_posix(),
        "{role}",
        "{prompt}",
        db_path.as_posix(),
    ]
    base_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_fake_opencode_discovery_scripts(list_script: Path, smoke_script: Path) -> None:
    list_script.write_text(
        "\n".join(
            [
                "import pathlib, sys",
                "count_path = pathlib.Path(sys.argv[1])",
                "provider = sys.argv[2]",
                "count = int(count_path.read_text() or '0') if count_path.exists() else 0",
                "count_path.write_text(str(count + 1))",
                "if provider == 'deepseek':",
                "    print('deepseek/deepseek-v4-flash')",
                "    print('deepseek/deepseek-v4-pro')",
                "else:",
                "    print('openrouter/deepseek/deepseek-v4-flash')",
                "    print('openrouter/deepseek/deepseek-v4-pro')",
                "    print('~openrouter/latest')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    smoke_script.write_text(
        "\n".join(
            [
                "import json, sqlite3, sys, time",
                "role, prompt, db_path = sys.argv[1], sys.argv[2], sys.argv[3]",
                "con = sqlite3.connect(db_path)",
                "con.execute('create table if not exists session (id text primary key, model text, tokens_input integer, tokens_output integer, tokens_reasoning integer, tokens_cache_read integer, tokens_cache_write integer, time_created integer, time_updated integer)')",
                "con.execute('create table if not exists message (id text primary key, session_id text, data text, time_created integer)')",
                "con.execute('create table if not exists part (message_id text, session_id text, data text, time_created integer)')",
                "now = int(time.time())",
                "message_id = role + '-message'",
                "con.execute('insert or replace into session values (?, ?, ?, ?, ?, ?, ?, ?, ?)', (role, json.dumps({'providerID': 'openrouter', 'id': 'fixture'}), 1, 1, 0, 0, 0, now, now))",
                "con.execute('insert or replace into message values (?, ?, ?, ?)', (message_id, role, json.dumps({'role': 'assistant'}), now))",
                "con.execute('insert into part values (?, ?, ?, ?)', (message_id, role, json.dumps({'type': 'text', 'text': prompt}), now))",
                "con.commit()",
                "con.close()",
                "print(json.dumps({'sessionID': role}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_counting_fake_opencode_smoke_script(smoke_script: Path, count_path: Path) -> None:
    smoke_script.write_text(
        "\n".join(
            [
                "import json, pathlib, sqlite3, sys, time",
                "role, prompt, db_path = sys.argv[1], sys.argv[2], sys.argv[3]",
                f"count_path = pathlib.Path(r'{count_path.as_posix()}')",
                "count = int(count_path.read_text() or '0') if count_path.exists() else 0",
                "count_path.write_text(str(count + 1))",
                "con = sqlite3.connect(db_path)",
                "con.execute('create table if not exists session (id text primary key, model text, tokens_input integer, tokens_output integer, tokens_reasoning integer, tokens_cache_read integer, tokens_cache_write integer, time_created integer, time_updated integer)')",
                "con.execute('create table if not exists message (id text primary key, session_id text, data text, time_created integer)')",
                "con.execute('create table if not exists part (message_id text, session_id text, data text, time_created integer)')",
                "now = int(time.time())",
                "message_id = role + '-message'",
                "con.execute('insert or replace into session values (?, ?, ?, ?, ?, ?, ?, ?, ?)', (role, json.dumps({'providerID': 'openrouter', 'id': 'fixture'}), 1, 1, 0, 0, 0, now, now))",
                "con.execute('insert or replace into message values (?, ?, ?, ?)', (message_id, role, json.dumps({'role': 'assistant'}), now))",
                "con.execute('insert into part values (?, ?, ?, ?)', (message_id, role, json.dumps({'type': 'text', 'text': prompt}), now))",
                "con.commit()",
                "con.close()",
                "print(json.dumps({'sessionID': role}))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def build_integration_brief(path: Path) -> None:
    brief = {
        "job_id": "config-integration-fixture",
        "title": "Config integration fixture",
        "base_ref": "main",
        "goal": "Verify checked goal-config profiles are consumed by preflight and runtime packet generation.",
        "source_summary": "Fixture brief for deterministic config integration coverage.",
        "required_evidence": [
            "Bundle lint passes with copied goal config.",
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
    goal_config = json.loads(config_path.read_text(encoding="utf-8"))
    require(manifest.get("goal_config_path") == "goal.config.json", "manifest must reference copied goal config")
    require(manifest.get("goal_config_check_path") == "goal-config.check.json", "manifest must reference copied goal config check")
    require("goal_config" not in manifest, "manifest must not embed full goal config")
    require("goal_config_check" not in manifest, "manifest must not embed full goal config check")
    require(manifest.get("goal_config_sha256"), "manifest must hash copied goal config")
    require(manifest.get("goal_config_check_sha256"), "manifest must hash copied goal config check")
    require(
        manifest.get("goal_config_summary", {}).get("profile") == goal_config.get("profile"),
        "manifest should include compact goal config summary",
    )
    require(
        manifest.get("goal_config_check_summary", {}).get("status") == "pass",
        "manifest should include compact goal config check summary",
    )
    require((bundle_dir / "goal.config.json").exists(), "bundle must copy goal.config.json")
    require((bundle_dir / "goal-config.check.json").exists(), "bundle must copy goal-config.check.json")
    require(
        manifest["worker_model_policy"]["default_ladder"] == ["demanding_agent", "lite_agent"],
        "manifest worker model policy should come from goal config",
    )
    require(
        manifest["review_model_policy"]["routes"]["standard"] == ["demanding_agent"],
        "manifest reviewer policy should come from goal config",
    )
    require(
        manifest["review_model_policy"]["routes"]["light"] == ["lite_agent"],
        "manifest light reviewer policy should use the configured lite route",
    )
    route_catalog = json.loads(
        run(
            [
                sys.executable,
                (ROOT / "skills" / "goal-main-orchestrator" / "scripts" / "check_model_catalog.py").as_posix(),
                "--json",
                "--manifest",
                manifest_path.as_posix(),
            ]
        ).stdout
    )
    require(route_catalog["status"] == "pass", "manifest route catalog should validate configured routes")
    require(
        route_catalog.get("checked_aliases") == ["demanding_agent", "lite_agent"],
        "manifest route catalog should check every configured route alias",
    )
    require(
        route_catalog.get("checked_harnesses") == ["opencode"],
        "manifest route catalog should cover the configured opencode harness",
    )
    configured_rows = {row.get("alias"): row for row in route_catalog.get("configured_route_models", [])}
    for alias in ["demanding_agent", "lite_agent"]:
        row = configured_rows.get(alias, {})
        require(row.get("harness_kind") == "opencode", f"{alias} route catalog harness mismatch")
        require(row.get("model_check_status") == "pass", f"{alias} route catalog model check missing")
        require(row.get("smoke_status") == "pass", f"{alias} route catalog smoke check missing")
        require(row.get("packet_runner_viable") is True, f"{alias} route catalog should preserve smoke viability")

    no_codex_bin = tmp_path / "no-codex-bin"
    no_codex_bin.mkdir()
    missing_codex_catalog = json.loads(
        run(
            [
                sys.executable,
                (ROOT / "skills" / "goal-main-orchestrator" / "scripts" / "check_model_catalog.py").as_posix(),
                "--json",
                "--require-codex",
                "--manifest",
                manifest_path.as_posix(),
                "--source",
                "live",
            ],
            env={"PATH": no_codex_bin.as_posix()},
            expect=1,
        ).stdout
    )
    require(
        missing_codex_catalog["status"] == "failed",
        "manifest route catalog must preserve --require-codex failure when Codex CLI is missing",
    )
    require(
        missing_codex_catalog["source"] == "missing",
        "missing Codex CLI should be reported as a missing catalog source",
    )

    codex_manifest_dir = tmp_path / "codex-route-manifest"
    codex_manifest_dir.mkdir()
    codex_config_path = codex_manifest_dir / "goal.config.json"
    codex_check_path = codex_manifest_dir / "goal-config.check.json"
    codex_manifest_path = codex_manifest_dir / "job.manifest.json"
    codex_config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "models": {
                    "bad_codex": {
                        "alias": "bad-codex",
                        "role": "bad_codex",
                        "harness": "codex",
                        "provider": "openai",
                        "model": "gpt-missing-fixture",
                        "purpose": "fixture missing Codex catalog model",
                    }
                },
                "harnesses": {
                    "codex": {
                        "kind": "codex",
                        "command": "codex",
                    }
                },
                "model_policies": {
                    "worker_model_policy": {
                        "default_ladder": ["bad_codex"],
                        "routes": {"normal-code": ["bad_codex"]},
                    }
                },
                "model_ladders": {"worker": ["bad_codex"]},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    codex_check_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pass",
                "harnesses": [
                    {
                        "role": "bad_codex",
                        "model_check": {"status": "pass"},
                        "smoke": {"status": "pass"},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    codex_manifest_path.write_text(
        json.dumps(
            {
                "goal_config_path": "goal.config.json",
                "goal_config_check_path": "goal-config.check.json",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    fake_codex_bin = tmp_path / "fake-codex-bin"
    fake_codex_bin.mkdir()
    write_fake_codex_catalog(fake_codex_bin, ["gpt-5.4"])
    absent_configured_codex = json.loads(
        run(
            [
                sys.executable,
                (ROOT / "skills" / "goal-main-orchestrator" / "scripts" / "check_model_catalog.py").as_posix(),
                "--json",
                "--require-codex",
                "--manifest",
                codex_manifest_path.as_posix(),
                "--source",
                "live",
            ],
            env={"PATH": fake_codex_bin.as_posix()},
            expect=1,
        ).stdout
    )
    require(
        absent_configured_codex["status"] == "failed",
        "manifest route catalog must fail when a configured Codex model is absent from the live catalog",
    )
    codex_rows = {row.get("alias"): row for row in absent_configured_codex.get("configured_route_models", [])}
    missing_codex_row = codex_rows.get("bad_codex", {})
    require(missing_codex_row.get("present") is False, "missing configured Codex model should report present=false")
    require(missing_codex_row.get("status") == "failed", "missing configured Codex model should fail its row")
    require(
        any(
            "bad_codex: configured Codex model absent from catalog" in failure
            for failure in absent_configured_codex.get("configured_route_failures", [])
        ),
        "missing configured Codex model should be included in configured_route_failures",
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
    status_schema = json.loads((packet_dir / "status.schema.json").read_text(encoding="utf-8"))
    require(route["selected_ladder"] == ["lite_agent"], "packet route must use configured cheap route-class ladder")
    require(route["default_ladder"] == ["demanding_agent", "lite_agent"], "packet default ladder must use config")
    require("goal_config" in route["selection_reason"], "packet route reason should cite goal_config")
    require("Codex Spark" not in route["selection_reason"], "configured route reason must not cite legacy Codex ladder")
    selected_ladder_schema = status_schema["properties"]["selected_ladder"]
    require(
        selected_ladder_schema.get("minItems") == 1
        and selected_ladder_schema.get("maxItems") == 1
        and selected_ladder_schema.get("items", {}).get("enum") == ["lite_agent"],
        "worker status schema must accept the configured selected ladder",
    )
    attempts = launch_config.get("attempts", [])
    require(len(attempts) == 1, "configured cheap route-class worker launch should have one attempt")
    require(attempts[0]["alias"] == "lite_agent", "first attempt should be lite agent")
    require(attempts[0]["harness_kind"] == "opencode", "first attempt should use opencode")
    require(attempts[0]["model"] == "deepseek/deepseek-v4-flash", "first attempt model mismatch")
    require(attempts[0]["effort"] == "configured", "configured attempt effort must be telemetry-valid")
    require(attempts[0].get("run_args"), "configured harness attempts must carry rendered run args")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="goal-config-fixtures-") as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "goal.config.json"
        report_path = tmp_path / "goal-config-check.json"
        generic_config_path = tmp_path / "goal.config.generic.json"
        current_override_path = tmp_path / "goal.config.current-overrides.json"
        normalized_roles_path = tmp_path / "goal.config.normalized-roles.json"
        openrouter_config_path = tmp_path / "goal.config.openrouter.json"
        discover_config_path = tmp_path / "goal.config.discover.json"
        profile_discover_config_path = tmp_path / "goal.config.profile-discover.json"
        from_discovery_config_path = tmp_path / "goal.config.from-discovery.json"
        thorough_debug_path = tmp_path / "goal.config.thorough-debug.json"
        counting_generic_path = tmp_path / "goal.config.counting-generic.json"
        discovery_reuse_report_path = tmp_path / "goal-config-discovery-smoke.json"
        for_preflight_report_path = tmp_path / "goal-config-for-preflight.json"
        for_preflight_state_path = tmp_path / "goal-config-for-preflight-state.json"
        for_preflight_mismatch_state_path = tmp_path / "goal-config-for-preflight-mismatch-state.json"
        for_preflight_mismatch_report_path = tmp_path / "goal-config-for-preflight-mismatch-report.json"
        for_preflight_bad_caps_path = tmp_path / "goal-config-bad-caps.json"
        for_preflight_bad_caps_report_path = tmp_path / "goal-config-bad-caps-report.json"
        for_preflight_remediated_path = tmp_path / "goal-config-bad-caps-remediated.json"
        for_preflight_remediated_report_path = tmp_path / "goal-config-bad-caps-remediated-report.json"
        for_preflight_bad_telemetry_path = tmp_path / "goal-config-bad-telemetry.json"
        for_preflight_bad_telemetry_report_path = tmp_path / "goal-config-bad-telemetry-report.json"
        discovery_reuse_count_path = tmp_path / "opencode-discovery-smoke-count.txt"
        discover_list_script = tmp_path / "fake_opencode_models.py"
        discover_smoke_script = tmp_path / "fake_opencode_smoke.py"
        normal_cache_count_path = tmp_path / "fake-opencode-normal-model-count.txt"
        baseline_smoke_count_path = tmp_path / "fake-opencode-baseline-model-count.txt"
        discover_count_path = tmp_path / "fake-opencode-model-count.txt"
        profile_discover_count_path = tmp_path / "fake-opencode-profile-model-count.txt"
        counting_smoke_count_path = tmp_path / "generic-smoke-count.txt"
        discover_db_path = tmp_path / "fake-opencode.db"
        baseline_smoke_db_path = tmp_path / "fake-opencode-baseline.db"
        models_path = tmp_path / "deepseek-models.txt"
        openrouter_models_path = tmp_path / "openrouter-models.txt"
        missing_models_path = tmp_path / "missing-models.txt"
        billing_config_path = tmp_path / "goal.config.billing.json"
        models_path.write_text(
            "deepseek/deepseek-chat\n"
            "deepseek/deepseek-reasoner\n"
            "deepseek/deepseek-v4-flash\n"
            "deepseek/deepseek-v4-pro\n",
            encoding="utf-8",
        )
        openrouter_models_path.write_text(
            "openrouter/deepseek/deepseek-v4-flash\n"
            "openrouter/deepseek/deepseek-v4-pro\n"
            "~openrouter/latest\n",
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
        require(
            set(config["harness_smokes"]) == set(config["models"]),
            "generated config must include smoke definitions for every model role",
        )
        require(config["harnesses"]["antigravity"]["command"] == "agy", "antigravity default command should be agy")
        require(
            config["harnesses"]["antigravity"]["smoke_args"] == ["--print", "{prompt}"],
            "antigravity default smoke should use agy --print",
        )
        require(
            config["harnesses"]["antigravity"]["run_args"] == ["--print", "{prompt}"],
            "antigravity default runtime args should use agy --print",
        )
        for forbidden in ("usd", "dollar", "pricing", "price"):
            require(
                forbidden not in serialized,
                f"config must not contain billing field or unit: {forbidden}",
            )

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "current-default",
                "--max-active-branch-agents",
                "3",
                "--max-active-worker-packets",
                "6",
                "--max-waves",
                "8",
                "--lite-timeout-seconds",
                "900",
                "--demanding-timeout-seconds",
                "2400",
                "--output",
                current_override_path.as_posix(),
            ]
        )
        current_override = json.loads(current_override_path.read_text(encoding="utf-8"))
        require(current_override["aggressiveness"]["max_active_branch_agents"] == 3, "branch cap override drifted")
        require(current_override["aggressiveness"]["max_active_worker_packets"] == 4, "worker cap override should be normalized to preflight max")
        require(current_override["aggressiveness"]["max_waves"] == 5, "max waves override should be normalized to preflight max")
        require(current_override["aggressiveness"]["total_branch_cap"] == 15, "total branch cap did not normalize overrides")
        current_override_adjustments = current_override.get("compatibility", {}).get("aggressiveness_adjustments", [])
        require(len(current_override_adjustments) == 1, "numeric cap overrides should record adjustment provenance")
        require(
            current_override_adjustments[0]["source"] == "numeric overrides",
            "numeric override provenance should be explicit",
        )
        require(
            current_override_adjustments[0]["adjustments"] == ["max_active_worker_packets 6 -> 4", "max_waves 8 -> 5"],
            "numeric override adjustment details should be recorded",
        )
        require(current_override["effort"]["lite_timeout_seconds"] == 900, "lite timeout override drifted")
        require(current_override["effort"]["demanding_timeout_seconds"] == 2400, "demanding timeout override drifted")
        require(
            set(current_override["harness_smokes"]) == set(current_override["models"]),
            "current-default must generate smoke definitions for all roles",
        )
        current_worker_ladder = ["worker_primary", "worker_opencode", "worker_fallback", "lite_agent"]
        require(
            current_override["model_ladders"]["worker"] == current_worker_ladder,
            "current-default worker ladder must keep Opencode and Gemini external fallbacks",
        )
        current_worker_policy = current_override["model_policies"]["worker_model_policy"]
        require(
            current_worker_policy["default_ladder"] == current_worker_ladder,
            "current-default worker policy default ladder must match model_ladders.worker",
        )
        require(
            current_worker_policy["allowed_routes"] == current_worker_ladder,
            "current-default worker policy allowed routes must include the external fallback",
        )
        require(
            [current_override["models"][role]["harness"] for role in current_worker_ladder]
            == ["codex", "opencode", "codex", "gemini"],
            "current-default worker fallback ladder must diversify beyond Codex",
        )
        require(
            current_override["models"]["worker_opencode"]["provider"] == "deepseek",
            "worker_opencode provider mismatch",
        )
        require(
            current_override["models"]["worker_opencode"]["model"] == "deepseek/deepseek-v4-flash",
            "worker_opencode model mismatch",
        )
        require(
            current_worker_policy["route_classes"]["normal-code"] == current_worker_ladder,
            "normal-code workers must retain Codex routes before external fallbacks",
        )
        require(
            current_worker_policy["route_classes"]["small-edit"] == [
                "worker_opencode",
                "worker_fallback",
                "lite_agent",
            ],
            "small-edit workers should prefer Opencode before cheap Codex fallback",
        )
        require(
            current_worker_policy["route_classes"]["docs"] == ["worker_opencode", "lite_agent"],
            "docs workers should use cheap external fallback routes",
        )

        thorough_state_path = tmp_path / "goal-config-thorough-state.json"
        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "current-default",
                "--effort-profile",
                "thorough",
                "--validation-mode",
                "debug",
                "--state-output",
                thorough_state_path.as_posix(),
                "--output",
                thorough_debug_path.as_posix(),
            ]
        )
        thorough_debug = json.loads(thorough_debug_path.read_text(encoding="utf-8"))
        require(thorough_debug["effort_profile"] == "thorough", "thorough effort profile should be recorded")
        require(thorough_debug["aggressiveness"]["max_active_branch_agents"] == 4, "thorough branch cap should be capped to preflight compatibility")
        require(thorough_debug["aggressiveness"]["max_active_worker_packets"] == 4, "thorough worker cap should be capped to preflight compatibility")
        require(thorough_debug["aggressiveness"]["max_waves"] == 5, "thorough max waves should be capped to preflight compatibility")
        require(thorough_debug["aggressiveness"]["total_branch_cap"] == 20, "thorough total branch cap should be based on normalized values")
        thorough_adjustments = thorough_debug.get("compatibility", {}).get("aggressiveness_adjustments", [])
        require(len(thorough_adjustments) == 1, "thorough normalization should record compatibility adjustments")
        require(
            thorough_adjustments[0]["source"] == "effort-profile:thorough",
            "thorough cap normalization should record its origin",
        )
        require(
            thorough_adjustments[0]["requested"],
            "thorough compatibility adjustment should include requested values",
        )
        require(thorough_debug["effort"]["lite_timeout_seconds"] == 900, "thorough lite timeout mismatch")
        require(thorough_debug["effort"]["demanding_timeout_seconds"] == 2400, "thorough demanding timeout mismatch")
        require(thorough_debug["validation"]["mode"] == "debug", "debug validation mode should be recorded")
        require(thorough_debug["telemetry"]["mode"] == "debug", "debug validation should set telemetry mode")
        require(thorough_debug["telemetry"]["raw_text"] is False, "debug validation should keep raw_text disabled")
        require(
            thorough_debug.get("preflight_intent", {}).get("telemetry_mode") == "debug",
            "debug validation should set preflight telemetry intent",
        )
        thorough_state = json.loads(thorough_state_path.read_text(encoding="utf-8"))
        require(thorough_state["phase"] == "config_created", "create state should record config_created phase")
        require(thorough_state["complete"] is False, "create state should not mark validation complete")
        require("--smoke" in thorough_state["next_command"], "debug validation state should route to smoke check")
        goal_config_phase_manifest = run(
            [
                sys.executable,
                (ROOT / "skills" / "goal-config" / "scripts" / "runtime_phase_manifest.py").as_posix(),
                "--markdown",
            ]
        ).stdout
        require(
            "for validation.mode=smoke or debug, add --smoke" in goal_config_phase_manifest,
            "goal-config phase manifest should not prescribe plain --for-preflight for debug configs",
        )

        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--for-preflight",
                "--output",
                for_preflight_report_path.as_posix(),
                "--state-output",
                for_preflight_state_path.as_posix(),
            ],
        ).stdout
        for_preflight_default = json.loads(for_preflight_report_path.read_text(encoding="utf-8"))
        require(for_preflight_default["status"] == "pass", "default config should pass preflight compatibility")
        require(
            for_preflight_default["check_mode"] == "check",
            "default preflight compatibility should persist actual check mode",
        )
        for_preflight = for_preflight_default
        require(for_preflight["status"] == "pass", "for-preflight should pass for model-check config")
        require(for_preflight["check_mode"] == "check", "for-preflight should persist actual check mode")
        require(for_preflight["config_validation_mode"] == "model-check", "for-preflight should persist config validation mode")
        preflight_state = json.loads(for_preflight_state_path.read_text(encoding="utf-8"))
        require(preflight_state["check_mode"] == "check", "preflight state should record actual check mode")
        require(preflight_state["config_validation_mode"] == "model-check", "preflight state should record requested validation mode")
        require(
            preflight_state["next_command"] and "--require-models" in preflight_state["next_command"],
            "preflight pass should point to model-only follow-up",
        )

        preflight_bad_caps = json.loads(current_override_path.read_text(encoding="utf-8"))
        preflight_bad_caps["aggressiveness"]["max_active_branch_agents"] = 9
        preflight_bad_caps["aggressiveness"]["max_active_worker_packets"] = 9
        preflight_bad_caps["aggressiveness"]["max_waves"] = 9
        preflight_bad_caps["aggressiveness"]["total_branch_cap"] = 81
        preflight_bad_caps["telemetry"]["collect"] = ["route_decisions", "unsupported_raw_payload"]
        for_preflight_bad_caps_path.write_text(json.dumps(preflight_bad_caps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                for_preflight_bad_caps_path.as_posix(),
                "--for-preflight",
                "--output",
                for_preflight_bad_caps_report_path.as_posix(),
                "--remediated-output",
                for_preflight_remediated_path.as_posix(),
                "--state-output",
                for_preflight_state_path.as_posix(),
            ],
            expect=1,
        )
        preflight_bad_caps_report = json.loads(for_preflight_bad_caps_report_path.read_text(encoding="utf-8"))
        require(preflight_bad_caps_report["status"] == "failed", "for-preflight should fail for out-of-range cap values")
        require(
            any("max_active_branch_agents" in failure for failure in preflight_bad_caps_report["failures"]),
            "preflight cap failures should mention max_active_branch_agents",
        )
        remediation = preflight_bad_caps_report.get("remediation", {})
        require(remediation.get("available") is True, "preflight cap failure should emit available remediation")
        require(for_preflight_remediated_path.exists(), "preflight remediation should write sanitized config output")
        remediated = json.loads(for_preflight_remediated_path.read_text(encoding="utf-8"))
        require(remediated["aggressiveness"]["max_active_branch_agents"] == 4, "remediation should clamp branch cap")
        require(remediated["aggressiveness"]["max_active_worker_packets"] == 4, "remediation should clamp worker cap")
        require(remediated["aggressiveness"]["max_waves"] == 5, "remediation should clamp max_waves")
        require(
            "unsupported_raw_payload" not in remediated["telemetry"]["collect"],
            "remediation should remove unsupported telemetry collect fields",
        )
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                for_preflight_remediated_path.as_posix(),
                "--for-preflight",
                "--output",
                for_preflight_remediated_report_path.as_posix(),
                "--state-output",
                for_preflight_state_path.as_posix(),
            ],
        )
        preflight_remediated_report = json.loads(for_preflight_remediated_report_path.read_text(encoding="utf-8"))
        require(preflight_remediated_report["status"] == "pass", "remediated config should pass preflight compatibility")

        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                thorough_debug_path.as_posix(),
                "--for-preflight",
                "--output",
                for_preflight_mismatch_report_path.as_posix(),
                "--state-output",
                for_preflight_mismatch_state_path.as_posix(),
            ],
            expect=1,
        )
        preflight_debug_report = json.loads(for_preflight_mismatch_report_path.read_text(encoding="utf-8"))
        require(preflight_debug_report["status"] == "failed", "preflight should fail when check mode does not match debug intent")
        require(
            any("requires smoke/discover check mode" in failure for failure in preflight_debug_report["failures"]),
            "preflight mismatch should explain mode incompatibility",
        )
        preflight_debug_state = json.loads(for_preflight_mismatch_state_path.read_text(encoding="utf-8"))
        require(preflight_debug_state["check_mode"] == "check", "for-preflight mismatch should preserve check-mode")
        require(preflight_debug_state["config_validation_mode"] == "debug", "for-preflight mismatch should preserve requested debug validation mode")
        require(preflight_debug_state["next_command"] and "--smoke" in preflight_debug_state["next_command"], "preflight mismatch should suggest smoke compatibility check")

        preflight_bad_telemetry = json.loads(config_path.read_text(encoding="utf-8"))
        preflight_bad_telemetry["telemetry"]["mode"] = "unsupported"
        for_preflight_bad_telemetry_path.write_text(json.dumps(preflight_bad_telemetry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                for_preflight_bad_telemetry_path.as_posix(),
                "--for-preflight",
                "--output",
                for_preflight_bad_telemetry_report_path.as_posix(),
            ],
            expect=1,
        )
        preflight_bad_telemetry_report_json = json.loads(for_preflight_bad_telemetry_report_path.read_text(encoding="utf-8"))
        require(
            preflight_bad_telemetry_report_json["status"] == "failed",
            "preflight should fail for unsupported telemetry mode",
        )
        require(
            any("telemetry.mode must be one of" in failure for failure in preflight_bad_telemetry_report_json["failures"]),
            "preflight should validate telemetry policy mode",
        )

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "current-default",
                "--role-model",
                "codex_heavy:codex:gpt-5.4",
                "--role-model",
                "gemini_flash:gemini:gemini-3-flash-preview",
                "--output",
                normalized_roles_path.as_posix(),
            ]
        )
        normalized_roles = json.loads(normalized_roles_path.read_text(encoding="utf-8"))
        require(normalized_roles["models"]["codex_heavy"]["provider"] == "openai", "codex provider mismatch")
        require(normalized_roles["models"]["codex_heavy"]["model"] == "gpt-5.4", "codex model should omit provider prefix")
        require(normalized_roles["models"]["gemini_flash"]["provider"] == "gemini", "gemini provider mismatch")
        require(
            normalized_roles["models"]["gemini_flash"]["model"] == "gemini-3-flash-preview",
            "gemini model should omit provider prefix",
        )
        require("codex_heavy" in normalized_roles["harness_smokes"], "role override must generate codex smoke")
        require("gemini_flash" in normalized_roles["harness_smokes"], "role override must generate gemini smoke")

        inline_harness_path = tmp_path / "goal.config.inline-harness.json"
        inline_spec = json.dumps(
            {
                "name": "inline_harness",
                "kind": "generic-cli",
                "command": "python3",
                "smoke_args": ["-c", "import sys; print(sys.argv[1])", "{prompt}"],
                "run_args": ["-c", "import sys; print(sys.argv[1])", "{prompt}"],
                "run_readback": "stdout",
            },
            sort_keys=True,
        )
        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "current-default",
                "--harness-spec",
                inline_spec,
                "--role-model",
                "inline_agent:inline_harness:local/inline-model",
                "--output",
                inline_harness_path.as_posix(),
            ]
        )
        inline_harness = json.loads(inline_harness_path.read_text(encoding="utf-8"))
        require("inline_harness" in inline_harness["harnesses"], "inline --harness-spec should add harness")
        require(
            inline_harness["models"]["inline_agent"]["harness"] == "inline_harness",
            "inline --harness-spec should support role-model mapping",
        )

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "opencode-deepseek-v4",
                "--provider",
                "openrouter",
                "--lite-model",
                "deepseek/deepseek-v4-flash",
                "--demanding-model",
                "deepseek/deepseek-v4-pro",
                "--output",
                openrouter_config_path.as_posix(),
            ]
        )
        openrouter_config = json.loads(openrouter_config_path.read_text(encoding="utf-8"))
        require(
            openrouter_config["models"]["demanding_agent"]["model"] == "openrouter/deepseek/deepseek-v4-pro",
            "opencode provider override must preserve nested OpenRouter model id",
        )
        openrouter_report_path = tmp_path / "goal-config-openrouter-check.json"
        check_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                openrouter_config_path.as_posix(),
                "--require-models",
                "--models-output",
                openrouter_models_path.as_posix(),
                "--output",
                openrouter_report_path.as_posix(),
            ]
        )
        openrouter_report = json.loads(openrouter_report_path.read_text(encoding="utf-8"))
        require(openrouter_report["status"] == "pass", "nested OpenRouter model ids should pass availability")
        require(len(openrouter_report["accepted_routes"]) == 2, "passing OpenRouter check should accept both routes")
        require(openrouter_report["rejected_routes"] == [], "passing OpenRouter check should not reject routes")
        require(
            all(harness["model_check"].get("model_available") for harness in openrouter_report["harnesses"]),
            "nested OpenRouter model checks should mark models available",
        )

        write_fake_opencode_discovery_scripts(discover_list_script, discover_smoke_script)
        build_fake_opencode_discovery_config(
            config_path,
            config_path,
            list_script=discover_list_script,
            smoke_script=discover_smoke_script,
            count_path=baseline_smoke_count_path,
            db_path=baseline_smoke_db_path,
        )
        build_fake_opencode_discovery_config(
            discover_config_path,
            config_path,
            list_script=discover_list_script,
            smoke_script=discover_smoke_script,
            count_path=normal_cache_count_path,
            db_path=discover_db_path,
        )
        normal_cache_report_path = tmp_path / "goal-config-normal-cache.json"
        check_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                discover_config_path.as_posix(),
                "--require-models",
                "--harness",
                "lite_agent,demanding_agent",
                "--output",
                normal_cache_report_path.as_posix(),
            ]
        )
        normal_cache_report = json.loads(normal_cache_report_path.read_text(encoding="utf-8"))
        require(normal_cache_report["status"] == "pass", "normal opencode cache check should pass")
        require(
            normal_cache_count_path.read_text(encoding="utf-8") == "1",
            "normal checks should cache opencode models listing per provider",
        )
        require(
            [harness["model_check"].get("models_cache") for harness in normal_cache_report["harnesses"]] == ["miss", "hit"],
            "normal same-provider model checks should hit cache after first listing",
        )

        build_fake_opencode_discovery_config(
            discover_config_path,
            config_path,
            list_script=discover_list_script,
            smoke_script=discover_smoke_script,
            count_path=discover_count_path,
            db_path=discover_db_path,
        )
        discover_report_path = tmp_path / "goal-config-discover.json"
        check_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                discover_config_path.as_posix(),
                "--discover-provider",
                "openrouter",
                "--smoke",
                "--opencode-db",
                discover_db_path.as_posix(),
                "--output",
                discover_report_path.as_posix(),
            ]
        )
        discover_report = json.loads(discover_report_path.read_text(encoding="utf-8"))
        require(discover_report["status"] == "pass", "discovery smoke should pass")
        require(discover_report["mode"] == "discover", "discovery report must declare mode")
        require(len(discover_report["candidate_routes"]) == 3, "discovery should emit all listed candidates")
        require(len(discover_report["accepted_routes"]) == 3, "discovery should accept all passing routes")
        require(discover_report["rejected_routes"] == [], "passing discovery should not reject routes")
        require(
            any(route["model"] == "~openrouter/latest" for route in discover_report["candidate_routes"]),
            "discovery must preserve ~provider/latest aliases",
        )
        require(
            discover_count_path.read_text(encoding="utf-8") == "1",
            "discovery should cache opencode models listing per provider",
        )
        require(
            all(harness["model_check"].get("models_cache") == "hit" for harness in discover_report["harnesses"]),
            "candidate checks should use cached provider model listing",
        )

        from_discovery_state_path = tmp_path / "goal-config-from-discovery-state.json"
        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--from-discovery",
                discover_report_path.as_posix(),
                "--mapping",
                "auto",
                "--effort-profile",
                "lean",
                "--validation-mode",
                "smoke",
                "--state-output",
                from_discovery_state_path.as_posix(),
                "--output",
                from_discovery_config_path.as_posix(),
            ]
        )
        from_discovery_config = json.loads(from_discovery_config_path.read_text(encoding="utf-8"))
        require(from_discovery_config["profile"] == "from-discovery", "from-discovery config should record profile")
        require(
            from_discovery_config["source_discovery"]["accepted_route_count"] == 3,
            "from-discovery config should record accepted route count",
        )
        require(from_discovery_config["effort_profile"] == "lean", "from-discovery config should apply effort profile")
        require(from_discovery_config["validation"]["mode"] == "smoke", "from-discovery config should apply validation mode")
        require(
            set(from_discovery_config["harness_smokes"]) == set(from_discovery_config["models"]),
            "from-discovery config should generate smokes for all discovered roles",
        )
        from_discovery_state = json.loads(from_discovery_state_path.read_text(encoding="utf-8"))
        require("--smoke" in from_discovery_state["next_command"], "from-discovery state should route to smoke check")
        discovery_reuse_count_path.write_text("0\n", encoding="utf-8")
        discovery_reuse_report_source_path = tmp_path / "goal-config-discovery-smoke-source.json"
        discovery_reuse_final_config_path = tmp_path / "goal.config.discovery-reuse-final.json"
        discovery_reuse_counting_smoke_script = tmp_path / "fake-opencode-discovery-reuse-smoke.py"
        write_counting_fake_opencode_smoke_script(discovery_reuse_counting_smoke_script, discovery_reuse_count_path)
        build_fake_opencode_discovery_config(
            discovery_reuse_report_source_path,
            config_path,
            list_script=discover_list_script,
            smoke_script=discovery_reuse_counting_smoke_script,
            count_path=normal_cache_count_path,
            db_path=discover_db_path,
        )
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                discovery_reuse_report_source_path.as_posix(),
                "--discover-provider",
                "deepseek",
                "--smoke",
                "--models-output",
                models_path.as_posix(),
                "--opencode-db",
                discover_db_path.as_posix(),
                "--output",
                discovery_reuse_report_path.as_posix(),
            ]
        )
        discovery_reuse_discovery = json.loads(discovery_reuse_report_path.read_text(encoding="utf-8"))
        require(discovery_reuse_discovery["status"] == "pass", "reused-discovery smoke report should pass")
        require(len(discovery_reuse_discovery["accepted_routes"]) >= 1, "reused-discovery should accept at least one deepseek model")
        require(
            int(discovery_reuse_count_path.read_text(encoding="utf-8")) >= 1,
            "discovery should execute smoke at least once for accepted routes",
        )
        discovery_reuse_discovery_smoke_count = int(discovery_reuse_count_path.read_text(encoding="utf-8"))
        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--from-discovery",
                discovery_reuse_report_path.as_posix(),
                "--mapping",
                "auto",
                "--validation-mode",
                "smoke",
                "--output",
                discovery_reuse_final_config_path.as_posix(),
            ]
        )
        final_reuse_smoke_report_path = tmp_path / "goal-config-discovery-reuse-smoke-final.json"
        final_reuse_smoke_state_path = tmp_path / "goal-config-discovery-reuse-state.json"
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                discovery_reuse_final_config_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "lite_agent,demanding_agent",
                "--reuse-smoke-report",
                discovery_reuse_report_path.as_posix(),
                "--models-output",
                models_path.as_posix(),
                "--opencode-db",
                discover_db_path.as_posix(),
                "--output",
                final_reuse_smoke_report_path.as_posix(),
                "--state-output",
                final_reuse_smoke_state_path.as_posix(),
            ]
        )
        final_reuse_smoke_report = json.loads(final_reuse_smoke_report_path.read_text(encoding="utf-8"))
        require(final_reuse_smoke_report["status"] == "pass", "final smoke should pass using reused discovery evidence")
        require(
            final_reuse_smoke_report["summary"]["token_telemetry"]["available_routes"] >= 1,
            "opencode smoke summary should record token telemetry availability",
        )
        require(
            int(discovery_reuse_count_path.read_text(encoding="utf-8")) == discovery_reuse_discovery_smoke_count,
            "reused routes should not rerun smoke for routes already passed in discovery report",
        )
        for harness_report in final_reuse_smoke_report["harnesses"]:
            require(
                harness_report.get("smoke", {}).get("reused") is True,
                "smoke evidence from discovery should be marked as reused",
            )
            require(
                smoke_path := harness_report.get("smoke", {}).get("reused_from_report"),
                "reused smoke evidence should include source report",
            )
            require("goal-config-discovery-smoke.json" in str(smoke_path), "reused smoke source should be discovery report")
            require(
                harness_report.get("smoke", {}).get("token_telemetry", {}).get("available") is True,
                "reused opencode smoke should preserve token telemetry availability",
            )
        final_reuse_smoke_state = json.loads(final_reuse_smoke_state_path.read_text(encoding="utf-8"))
        require(
            "goal-config-smoke.json" in final_reuse_smoke_state.get("next_command", ""),
            "final smoke state should reference the smoke output artifact",
        )

        profile_discover_config = json.loads(config_path.read_text(encoding="utf-8"))
        profile_discover_config["harnesses"]["opencode"]["command"] = "python3"
        profile_discover_config["harnesses"]["opencode"]["model_list_args"] = [
            discover_list_script.as_posix(),
            profile_discover_count_path.as_posix(),
            "{provider}",
        ]
        profile_discover_config["harnesses"]["opencode"]["smoke_args"] = [
            discover_smoke_script.as_posix(),
            "{role}",
            "{prompt}",
            discover_db_path.as_posix(),
        ]
        for harness_name in ("codex", "gemini", "antigravity"):
            profile_discover_config["harnesses"][harness_name]["command"] = "python3"
            profile_discover_config["harnesses"][harness_name]["smoke_args"] = [
                "-c",
                "import sys; print(sys.argv[1])",
                "{prompt}",
            ]
        profile_discover_config_path.write_text(
            json.dumps(profile_discover_config, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        profile_discover_report_path = tmp_path / "goal-config-profile-discover.json"
        profile_discover_state_path = tmp_path / "goal-config-profile-discover-state.json"
        profile_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                profile_discover_config_path.as_posix(),
                "--discover-profile",
                "mixed-fast",
                "--smoke",
                "--models-output",
                models_path.as_posix(),
                "--opencode-db",
                discover_db_path.as_posix(),
                "--output",
                profile_discover_report_path.as_posix(),
                "--state-output",
                profile_discover_state_path.as_posix(),
            ]
        ).stdout
        profile_discover_report = json.loads(profile_discover_report_path.read_text(encoding="utf-8"))
        require(profile_summary.startswith("status=pass mode=discover"), "discovery with output should print summary")
        require("unvisited=3" in profile_summary, "early-stopped discovery summary should count unvisited routes")
        require(not profile_summary.lstrip().startswith("{"), "discovery summary should not dump full JSON")
        require(profile_discover_report["discover_profile"] == "mixed-fast", "mixed-fast report should record profile")
        require(len(profile_discover_report["candidate_routes"]) == 7, "mixed-fast should list all static candidates")
        require(len(profile_discover_report["checked_roles"]) == 4, "early stop should distinguish checked roles")
        require(len(profile_discover_report["accepted_routes"]) == 4, "mixed-fast should stop after four accepted routes")
        require(len(profile_discover_report["unvisited_routes"]) == 3, "early stop should report unvisited routes")
        require(
            any(route["harness"] == "antigravity" for route in profile_discover_report["unvisited_routes"]),
            "early stop should make unvisited agy/antigravity explicit",
        )
        require(
            any(route["harness"] == "codex" for route in profile_discover_report["accepted_routes"]),
            "mixed-fast should include non-opencode harness candidates",
        )
        profile_discover_state = json.loads(profile_discover_state_path.read_text(encoding="utf-8"))
        require(profile_discover_state["phase"] == "discovery", "discovery state should record discovery phase")
        require(profile_discover_state["complete"] is False, "discovery state should require final config creation")
        require("--from-discovery" in profile_discover_state["next_command"], "discovery state should point to create")

        all_candidates_report_path = tmp_path / "goal-config-profile-discover-all.json"
        all_candidates_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                profile_discover_config_path.as_posix(),
                "--discover-profile",
                "mixed-fast",
                "--discover-all-candidates",
                "--smoke",
                "--models-output",
                models_path.as_posix(),
                "--opencode-db",
                discover_db_path.as_posix(),
                "--output",
                all_candidates_report_path.as_posix(),
            ]
        ).stdout
        all_candidates_report = json.loads(all_candidates_report_path.read_text(encoding="utf-8"))
        require("unvisited=0" in all_candidates_summary, "all-candidate discovery should not leave unvisited routes")
        require(len(all_candidates_report["candidate_routes"]) == 7, "all-candidate discovery should list seven candidates")
        require(len(all_candidates_report["checked_roles"]) == 7, "all-candidate discovery should check every candidate")
        require(len(all_candidates_report["accepted_routes"]) == 7, "all-candidate fixture should accept every route")
        require(all_candidates_report["unvisited_routes"] == [], "all-candidate discovery should have no unvisited routes")
        require(
            any(route["harness"] == "antigravity" for route in all_candidates_report["accepted_routes"]),
            "all-candidate discovery should reach agy/antigravity",
        )

        auth_stop_config = json.loads(profile_discover_config_path.read_text(encoding="utf-8"))
        auth_stop_config["harnesses"]["opencode"]["smoke_args"] = [
            "-c",
            "import json, sys; print(json.dumps(dict(type='error', status=401, message='AuthenticateToken authentication failed'))); sys.exit(1)",
        ]
        auth_stop_path = tmp_path / "goal.config.profile-auth-stop.json"
        auth_stop_path.write_text(json.dumps(auth_stop_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        auth_stop_report_path = tmp_path / "goal-config-profile-auth-stop.json"
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                auth_stop_path.as_posix(),
                "--discover-profile",
                "mixed-fast",
                "--smoke",
                "--models-output",
                models_path.as_posix(),
                "--output",
                auth_stop_report_path.as_posix(),
                "--stdout",
                "none",
            ]
        )
        auth_stop_report = json.loads(auth_stop_report_path.read_text(encoding="utf-8"))
        require(auth_stop_report["status"] == "pass", "auth-stopped profile should continue with other harnesses")
        require(
            any(
                "provider stopped after auth error" in " ".join(route.get("reasons", []))
                for route in auth_stop_report["rejected_routes"]
            ),
            "mixed-fast should skip later routes for a provider after auth failure",
        )
        require(
            any(route["harness"] == "gemini" for route in auth_stop_report["accepted_routes"]),
            "auth-stopped profile should keep trying other harnesses",
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
        questions = json.loads(run([sys.executable, SCAN.as_posix(), "--questions-json"]).stdout)
        require(questions["status"] == "pass", "preference question inventory should pass")
        question_ids = {item.get("id") for item in questions.get("questions", []) if isinstance(item, dict)}
        for question_id in ("model_profile", "effort_profile", "validation_mode"):
            require(question_id in question_ids, f"missing preference question: {question_id}")
        interaction = questions.get("interaction", {})
        require(
            interaction.get("ask_order") == ["model_profile", "effort_profile", "validation_mode"],
            "preference intake must ask model, effort, then validation",
        )
        require(interaction.get("max_sections_per_turn") == 3, "preference intake should support compact completion")
        require(
            any("one compact pass" in item for item in interaction.get("instructions", [])),
            "preference intake should tell agents how to finish when the user says continue",
        )
        require(
            any("Prefer smoke checks for normal validation" in item for item in interaction.get("instructions", [])),
            "preference intake should steer debug only for trace workflows",
        )
        by_id = {
            item["id"]: item
            for item in questions.get("questions", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        expected_options = {
            "model_profile": {
                "reuse_checked",
                "current_default",
                "opencode_deepseek_v4",
                "discover_available",
                "gemini",
                "agy_generic_cli",
                "custom_mixed",
            },
            "effort_profile": {"lean", "balanced", "thorough", "custom"},
            "validation_mode": {"model_check_only", "model_check_plus_smoke", "full_debug_trace", "custom_validation"},
        }
        for question_id, option_ids in expected_options.items():
            question = by_id[question_id]
            require(question.get("order") in (1, 2, 3), f"{question_id} must include display order")
            require(question.get("explain_to_user"), f"{question_id} must explain the preference to the user")
            options = question.get("options", [])
            require(all(isinstance(option, dict) for option in options), f"{question_id} options must be structured")
            found_option_ids = {option.get("id") for option in options}
            require(option_ids <= found_option_ids, f"{question_id} missing user-visible options")
            for option in options:
                require(option.get("label"), f"{question_id} option missing label")
                require(option.get("description"), f"{question_id} option missing description")
                if question_id == "validation_mode" and option.get("id") == "full_debug_trace":
                    require(
                        "trace analysis" in option.get("description", "").lower(),
                        "full_debug_trace should explain debug is for trace analysis",
                    )
                if question_id == "validation_mode" and option.get("id") == "model_check_plus_smoke":
                    require(
                        "lean" in option.get("description", "").lower(),
                        "model_check_plus_smoke should be described as lean normal validation",
                    )
        effort_options = {option["id"]: option for option in by_id["effort_profile"]["options"]}
        require(
            "create_goal_config.py --effort-profile thorough" in effort_options["thorough"]["maps_to"],
            "thorough profile must map to exact create command",
        )
        validation_options = {option["id"]: option for option in by_id["validation_mode"]["options"]}
        require(
            "create_goal_config.py --validation-mode debug" in validation_options["full_debug_trace"]["maps_to"],
            "debug validation must map to exact create command",
        )
        discovery_option = {
            option["id"]: option for option in by_id["model_profile"]["options"]
        }["discover_available"]
        require(
            any("create_goal_config.py --preset current-default --output /abs/seed.goal.config.json" in item for item in discovery_option["maps_to"]),
            "discovery option must include seed config creation",
        )
        require(
            any("--config /abs/seed.goal.config.json --discover-profile mixed-fast" in item for item in discovery_option["maps_to"]),
            "discovery option must pass the seed config to the checker",
        )
        require(
            any("--discover-all-candidates" in item for item in discovery_option["maps_to"]),
            "discover_available must request all profile candidates",
        )
        require(
            any("--reuse-smoke-report /abs/goal-config-discovery.json" in item for item in discovery_option["maps_to"]),
            "discovery option should mention reusing discovery smoke output when routes are unchanged",
        )
        require(questions.get("ask_only_missing") is True, "preference intake must ask only missing categories")
        require(
            any("do not create" in item.lower() for item in questions.get("do_not_create_until", [])),
            "preference intake must block silent config creation",
        )
        phase_manifest = run(
            [
                sys.executable,
                (GOAL_CONFIG / "scripts" / "runtime_phase_manifest.py").as_posix(),
                "--markdown",
            ]
        ).stdout
        require("preference_intake" in phase_manifest, "phase manifest must include preference intake")
        require("--questions-json" in phase_manifest, "phase manifest must point to question inventory")
        require("one compact pass" in phase_manifest, "phase manifest must support compact preference completion")
        require("--discover-all-candidates" in phase_manifest, "phase manifest should use all-candidate discovery")
        skill_text = (GOAL_CONFIG / "SKILL.md").read_text(encoding="utf-8")
        require(
            "--config /abs/seed.goal.config.json" in skill_text,
            "SKILL.md discovery command must include the required seed config",
        )
        require(
            "--discover-all-candidates" in skill_text,
            "SKILL.md discovery command must request all candidates",
        )
        require(
            "reuse-smoke-report /abs/goal-config-discovery.json" in skill_text,
            "SKILL.md should mention reuse-smoke-report for unchanged discovered routes",
        )
        readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
        require(
            "--reuse-smoke-report /abs/goal-config-discovery.json" in readme_text,
            "README should mention reuse-smoke-report for discovery smoke reuse",
        )
        require(
            "$HOME/.agents/skills/.system" in skill_text,
            "SKILL.md should clarify .system is system metadata",
        )
        require(
            'Runtime goal-config instructions live at `"$GOAL_SKILLS_ROOT/goal-config/SKILL.md"`' in readme_text,
            "README should point runtime docs to GOAL_SKILLS_ROOT",
        )
        require(
            "`.agents/skills/.system` is for system-level wrappers/metadata" in readme_text,
            "README should clarify .system is system-level metadata only",
        )
        require(
            "discovery-path validation" in readme_text,
            "README should classify mixed-fast discovery as path validation",
        )
        require(
            "/home/jakub/.agents/skills/.system/goal-config/SKILL.md" not in readme_text,
            "README should not advertise a stale absolute .system runtime path",
        )
        contract_text = (GOAL_CONFIG / "references" / "configuration-contract.md").read_text(encoding="utf-8")
        require(
            "Prefer smoke by default and reserve debug for trace analysis." in contract_text,
            "contract should default to smoke and reserve debug",
        )
        require(
            "--discover-all-candidates" in contract_text and "discovery path coverage" in contract_text,
            "contract should classify discover-all-candidates as discovery coverage",
        )
        require(
            "For large reports, use scoped inspection with `jq`" in contract_text,
            "contract should recommend scoped jq inspection for large reports",
        )
        require(
            "--reuse-smoke-report" in contract_text
            and "accepted route set is unchanged" in contract_text.lower()
            and "follow-on smoke check" in contract_text.lower(),
            "contract should mention reusing discovery smoke output when route set is unchanged",
        )
        manifest_text = (ROOT / "skills/_goal_shared/scripts/runtime_phase_manifest.py").read_text(encoding="utf-8")
        require(
            "route_discovery" in manifest_text and "discovery/use-all-available" in manifest_text,
            "phase manifest should still describe discovery option",
        )
        create_help = run([sys.executable, CREATE.as_posix(), "--help"]).stdout
        require(
            "Path to a JSON harness spec, or an inline JSON object" in create_help,
            "--harness-spec help should match path-or-inline behavior",
        )
        check_help = run([sys.executable, CHECK.as_posix(), "--help"]).stdout
        require("--json" in check_help, "check_goal_config.py should expose a --json stdout alias")

        check_summary = run(
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
        ).stdout
        require(check_summary.startswith("status=pass mode=check"), "check with output should print summary by default")
        require("output=" in check_summary, "summary stdout should include output path")
        require(not check_summary.lstrip().startswith("{"), "summary stdout should not dump JSON")
        normal_check_state_path = tmp_path / "goal-config-check-state.json"
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
                "--state-output",
                normal_check_state_path.as_posix(),
            ]
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report["status"] == "pass", "fixture model check should pass")
        require(len(report["harnesses"]) == 2, "expected two harness reports")
        require(report["mode"] == "check", "check report should persist mode")
        require(
            report.get("command") and "check_goal_config.py" in report.get("command"),
            "check report should persist executed command",
        )
        check_state = json.loads(normal_check_state_path.read_text(encoding="utf-8"))
        require(
            check_state["next_command"] is not None and "goal-config-check.json" in check_state["next_command"],
            "validation state should reference non-smoke check artifact for model-check-only",
        )
        smoke_report_path = tmp_path / "goal-config-smoke.json"
        smoke_state_path = tmp_path / "goal-config-smoke-state.json"
        smoke_summary = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--smoke",
                "--models-output",
                models_path.as_posix(),
                "--opencode-db",
                baseline_smoke_db_path.as_posix(),
                "--output",
                smoke_report_path.as_posix(),
                "--state-output",
                smoke_state_path.as_posix(),
            ]
        ).stdout
        require(smoke_summary.startswith("status=pass mode=smoke"), "smoke check should report mode=smoke")
        smoke_report = json.loads(smoke_report_path.read_text(encoding="utf-8"))
        require(smoke_report["mode"] == "smoke", "smoke report should persist mode=smoke")
        smoke_state = json.loads(smoke_state_path.read_text(encoding="utf-8"))
        require(
            smoke_state["next_command"] is not None and "goal-config-smoke.json" in smoke_state["next_command"],
            "smoke validation state should reference smoke report artifact",
        )
        report_serialized = json.dumps(report, sort_keys=True).lower()
        for forbidden in ("cost", "usd", "dollar", "pricing", "price"):
            require(
                forbidden not in report_serialized,
                f"report must not contain billing field or unit: {forbidden}",
            )

        stdout_full = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--models-output",
                models_path.as_posix(),
                "--output",
                (tmp_path / "goal-config-stdout-full.json").as_posix(),
                "--stdout",
                "full",
            ]
        ).stdout
        require(stdout_full.lstrip().startswith("{"), "--stdout full should print full JSON")
        stdout_json_alias = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--models-output",
                models_path.as_posix(),
                "--output",
                (tmp_path / "goal-config-json-alias.json").as_posix(),
                "--json",
            ]
        ).stdout
        require(stdout_json_alias.lstrip().startswith("{"), "--json should print full JSON")
        stdout_none = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                config_path.as_posix(),
                "--require-models",
                "--models-output",
                models_path.as_posix(),
                "--output",
                (tmp_path / "goal-config-stdout-none.json").as_posix(),
                "--stdout",
                "none",
            ]
        ).stdout
        require(stdout_none == "", "--stdout none should print nothing")

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
        require(
            generic_smoke.get("response_excerpt") == "GENERIC_CLI_SMOKE_OK",
            "generic cli smoke excerpt should focus the expected assistant token, not CLI boilerplate",
        )
        require(
            generic_smoke.get("response_excerpt_source") == "expected_line",
            "generic cli smoke excerpt should record expected-line source",
        )
        require(
            generic_smoke.get("token_telemetry", {}).get("available") is False,
            "generic cli smoke should explicitly mark token telemetry unavailable",
        )
        require(
            generic_report["summary"]["token_telemetry"]["unavailable_routes"] == 1,
            "generic cli smoke summary should count unavailable token telemetry",
        )

        build_counting_generic_cli_config(counting_generic_path, config_path, counting_smoke_count_path)
        counting_report_path = tmp_path / "goal-config-counting-generic-smoke.json"
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                counting_generic_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "generic_agent,generic_agent_copy",
                "--output",
                counting_report_path.as_posix(),
                "--stdout",
                "none",
            ]
        )
        counting_report = json.loads(counting_report_path.read_text(encoding="utf-8"))
        require(counting_report["status"] == "pass", "counting generic cli smoke should pass")
        require(counting_smoke_count_path.read_text(encoding="utf-8") == "1", "duplicate route should smoke once")
        require(
            counting_report["harnesses"][1]["smoke"].get("reused") is True,
            "second duplicate route should reuse cached smoke evidence",
        )

        comma_selected_report_path = tmp_path / "goal-config-comma-selected.json"
        run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                custom_smoke_path.as_posix(),
                "--require-models",
                "--harness",
                "lite_agent,generic_agent",
                "--models-output",
                models_path.as_posix(),
                "--output",
                comma_selected_report_path.as_posix(),
            ]
        )
        comma_selected_report = json.loads(comma_selected_report_path.read_text(encoding="utf-8"))
        require(
            comma_selected_report["checked_roles"] == ["lite_agent", "generic_agent"],
            "comma-separated --harness list should preserve selected role order",
        )

        missing_smoke_config = json.loads(custom_smoke_path.read_text(encoding="utf-8"))
        del missing_smoke_config["harness_smokes"]["generic_agent"]
        missing_smoke_path = tmp_path / "goal.config.missing-smoke.json"
        missing_smoke_path.write_text(json.dumps(missing_smoke_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        missing_smoke_failed = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                missing_smoke_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "generic_agent",
                "--models-output",
                models_path.as_posix(),
            ],
            expect=1,
        ).stdout
        missing_smoke_report = json.loads(missing_smoke_failed)
        require(missing_smoke_report["status"] == "failed", "missing smoke config should fail")
        require(
            any("missing smoke config" in failure for failure in missing_smoke_report["failures"]),
            "missing smoke config should fail before running route smokes",
        )

        opencode_auth_config = json.loads(config_path.read_text(encoding="utf-8"))
        opencode_auth_config["harnesses"]["opencode"]["command"] = "python3"
        opencode_auth_config["harnesses"]["opencode"]["smoke_args"] = [
            "-c",
            "import json, sys; print(json.dumps(dict(type='error', status=401, message='AuthenticateToken authentication failed'))); sys.exit(1)",
        ]
        opencode_auth_path = tmp_path / "goal.config.opencode-auth.json"
        opencode_auth_path.write_text(json.dumps(opencode_auth_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        opencode_auth_failed = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                opencode_auth_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "lite_agent",
                "--models-output",
                models_path.as_posix(),
            ],
            expect=1,
        ).stdout
        opencode_auth_report = json.loads(opencode_auth_failed)
        opencode_auth_smoke = opencode_auth_report["harnesses"][0]["smoke"]
        require(opencode_auth_report["status"] == "failed", "opencode auth smoke should fail")
        require(opencode_auth_report["accepted_routes"] == [], "auth-failed route should not be accepted")
        require(opencode_auth_report["rejected_routes"], "auth-failed route should be rejected with reasons")
        require(
            opencode_auth_smoke.get("opencode_errors", [{}])[0].get("status") == "401",
            "opencode smoke should extract JSON error status",
        )
        require(
            opencode_auth_smoke.get("opencode_errors", [{}])[0].get("provider") == "deepseek",
            "opencode smoke should record failing provider",
        )
        require(
            "raw_messages" not in opencode_auth_smoke.get("opencode_errors", [{}])[0],
            "raw provider errors should be omitted by default",
        )
        require(
            any("AuthenticateToken authentication failed" in failure for failure in opencode_auth_report["failures"]),
            "opencode smoke failure should surface auth message",
        )
        opencode_auth_raw_failed = run(
            [
                sys.executable,
                CHECK.as_posix(),
                "--config",
                opencode_auth_path.as_posix(),
                "--require-models",
                "--smoke",
                "--harness",
                "lite_agent",
                "--models-output",
                models_path.as_posix(),
                "--include-raw-errors",
            ],
            expect=1,
        ).stdout
        opencode_auth_raw_report = json.loads(opencode_auth_raw_failed)
        require(
            opencode_auth_raw_report["harnesses"][0]["smoke"]["opencode_errors"][0].get("raw_messages"),
            "--include-raw-errors should preserve raw provider messages",
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
        run_integration_fixture(tmp_path, config_path, smoke_report_path)

    print("status=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
