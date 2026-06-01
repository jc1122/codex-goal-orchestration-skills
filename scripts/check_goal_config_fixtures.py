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
    status_schema = json.loads((packet_dir / "status.schema.json").read_text(encoding="utf-8"))
    require(route["selected_ladder"] == ["demanding_agent", "lite_agent"], "packet route must use configured ladder")
    require(route["default_ladder"] == ["demanding_agent", "lite_agent"], "packet default ladder must use config")
    require("goal_config" in route["selection_reason"], "packet route reason should cite goal_config")
    require("Codex Spark" not in route["selection_reason"], "configured route reason must not cite legacy Codex ladder")
    require(
        status_schema["properties"]["selected_ladder"].get("const") == ["demanding_agent", "lite_agent"],
        "worker status schema must accept the configured selected ladder",
    )
    attempts = launch_config.get("attempts", [])
    require(len(attempts) == 2, "configured worker launch should have two attempts")
    require(attempts[0]["alias"] == "demanding_agent", "first attempt should be demanding agent")
    require(attempts[0]["harness_kind"] == "opencode", "first attempt should use opencode")
    require(attempts[0]["model"] == "deepseek/deepseek-v4-pro", "first attempt model mismatch")
    require(attempts[0]["effort"] == "configured", "configured attempt effort must be telemetry-valid")
    require(attempts[1]["alias"] == "lite_agent", "second attempt should be lite agent")
    require(attempts[1]["harness_kind"] == "opencode", "second attempt should use opencode")
    require(attempts[1]["model"] == "deepseek/deepseek-v4-flash", "second attempt model mismatch")
    require(attempts[1]["effort"] == "configured", "configured fallback effort must be telemetry-valid")
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
        discover_list_script = tmp_path / "fake_opencode_models.py"
        discover_smoke_script = tmp_path / "fake_opencode_smoke.py"
        normal_cache_count_path = tmp_path / "fake-opencode-normal-model-count.txt"
        discover_count_path = tmp_path / "fake-opencode-model-count.txt"
        discover_db_path = tmp_path / "fake-opencode.db"
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
        require(current_override["aggressiveness"]["max_active_worker_packets"] == 6, "worker cap override drifted")
        require(current_override["aggressiveness"]["max_waves"] == 8, "max waves override drifted")
        require(current_override["aggressiveness"]["total_branch_cap"] == 24, "total branch cap did not follow overrides")
        require(current_override["effort"]["lite_timeout_seconds"] == 900, "lite timeout override drifted")
        require(current_override["effort"]["demanding_timeout_seconds"] == 2400, "demanding timeout override drifted")
        require(
            set(current_override["harness_smokes"]) == set(current_override["models"]),
            "current-default must generate smoke definitions for all roles",
        )

        run(
            [
                sys.executable,
                CREATE.as_posix(),
                "--preset",
                "current-default",
                "--role-model",
                "codex_heavy:codex:openai/gpt-5.4",
                "--role-model",
                "gemini_flash:gemini:gemini/gemini-3-flash-preview",
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
        run(
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
            discover_config_path,
            config_path,
            list_script=discover_list_script,
            smoke_script=discover_smoke_script,
            count_path=normal_cache_count_path,
            db_path=discover_db_path,
        )
        normal_cache_report_path = tmp_path / "goal-config-normal-cache.json"
        run(
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
        run(
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
        require(interaction.get("max_sections_per_turn") == 1, "preference intake should ask one section at a time")
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
        require("ask one missing section at a time" in phase_manifest, "phase manifest must preserve sequential intake")

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
            any("AuthenticateToken authentication failed" in failure for failure in opencode_auth_report["failures"]),
            "opencode smoke failure should surface auth message",
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
