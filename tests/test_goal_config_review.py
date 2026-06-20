"""Regression tests for the 2026-06-18 deep-review fixes (goal-config).

Pins the verified defects:
- check_goal_config.load_json fails closed (SystemExit) on malformed/missing config;
- config-supplied timeouts no longer crash with int() ValueError;
- opencode-bridge roles validate provider/model against the known bridge routes
  (contract MUST that was previously unenforced);
- create_goal_config file-input loaders fail closed on malformed/missing JSON.
"""

import argparse
import ast
import os
import json
import subprocess
import re
import shlex
import sys
from pathlib import Path

import pytest
from conftest import REPO, load_module

cgc = load_module("skills/goal-config/scripts/check_goal_config.py", "cgc_review")
crc = load_module("skills/goal-config/scripts/create_goal_config.py", "crc_review")
scanc = load_module("skills/goal-config/scripts/scan_configurables.py", "scanc_review")
cgcf = load_module("scripts/check_goal_config_fixtures.py", "cgcf_review")


def _create_goal_config_args(**overrides):
    defaults = {
        "lite_model": "deepseek/deepseek-v4-flash",
        "demanding_model": "deepseek/deepseek-v4-pro",
        "provider": None,
        "harness_spec": [],
        "role_model": [],
        "effort_profile": "balanced",
        "validation_mode": "model-check",
        "output": None,
        "state_output": None,
        "from_discovery": None,
        "mapping": None,
        "lite_ladder": None,
        "worker_ladder": None,
        "reviewer_ladder": None,
        "amender_ladder": None,
        "max_active_branch_agents": None,
        "max_active_worker_packets": None,
        "max_waves": None,
        "lite_timeout_seconds": None,
        "demanding_timeout_seconds": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# --- 2026-06-18 convergence pass: docs must not advertise a check_goal_config.py flag that
#     build_parser does not define (following the doc gave an argparse error). ---
def test_docs_do_not_reference_nonexistent_check_flag():
    parser_flags = {opt for action in cgc.build_parser()._actions for opt in action.option_strings}
    assert "--include-raw-errors" not in parser_flags  # confirms reality: the flag does not exist
    for rel in (
        "skills/goal-config/SKILL.md",
        "skills/goal-config/references/configuration-contract.md",
        "README.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "--include-raw-errors" not in text, f"{rel} still references the nonexistent flag"


def test_docs_do_not_advertise_openrouter_for_opencode_bridge():
    stale_phrases = (
        "openrouter/deepseek/deepseek-v4-pro",
        "The checker accepts nested model ids such as `openrouter/deepseek/deepseek-v4-pro`",
        "accepting nested provider model ids such as `openrouter/deepseek/deepseek-v4-pro`",
    )
    for rel in (
        "skills/goal-config/references/configuration-contract.md",
        "README.md",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, f"{rel} still advertises stale opencode-bridge OpenRouter route {phrase!r}"


def test_goal_config_skill_check_commands_parse_with_required_config():
    skill_text = (REPO / "skills/goal-config/SKILL.md").read_text(encoding="utf-8")
    snippets = re.findall(r"`([^`]*check_goal_config\.py[^`]*)`", skill_text)
    commands = [snippet for snippet in snippets if snippet.startswith("check_goal_config.py ")]

    assert commands, "expected documented check_goal_config.py commands in goal-config skill docs"
    parser = cgc.build_parser()
    for command in commands:
        tokens = shlex.split(command)
        script_index = tokens.index("check_goal_config.py")
        try:
            args = parser.parse_args(tokens[script_index + 1 :])
        except SystemExit as exc:
            pytest.fail(f"documented command does not parse with required arguments: {command!r} exited {exc.code}")
        assert args.config is not None, f"documented command omits --config: {command!r}"


def _extract_contract_template_tokens(line: str) -> set[str]:
    return {token for token in re.findall(r"\{([a-zA-Z0-9_]+)\}", line)}


def _dict_string_keys(node: ast.Dict) -> set[str]:
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _render_context_keys(path: Path, function_name: str, *, context_via_assignment: bool = True) -> set[str]:
    source = path.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in ast.walk(module):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue

        for child in ast.walk(node):
            if context_via_assignment and isinstance(child, ast.Assign):
                if (
                    isinstance(child.value, ast.Dict)
                    and len(child.targets) == 1
                    and isinstance(child.targets[0], ast.Name)
                    and child.targets[0].id == "context"
                ):
                    return _dict_string_keys(child.value)
            if not context_via_assignment and isinstance(child, ast.Call):
                for keyword in child.keywords:
                    if keyword.arg != "context" or not isinstance(keyword.value, ast.Dict):
                        continue
                    return _dict_string_keys(keyword.value)
        break
    raise RuntimeError(f"could not parse context keys from {path}:{function_name}")


def test_documented_smoke_args_tokens_match_smoke_render_context():
    contract_text = (REPO / "skills/goal-config/references/configuration-contract.md").read_text(encoding="utf-8")

    smoke_line = next(
        line
        for line in contract_text.splitlines()
        if "`smoke_args` templates" in line and "rendered by the checker with" in line
    )
    run_line = next(
        line
        for line in contract_text.splitlines()
        if "`run_args` templates" in line and "rendered by runtime packet launchers" in line
    )

    smoke_tokens = _extract_contract_template_tokens(smoke_line)
    run_tokens = _extract_contract_template_tokens(run_line)

    smoke_context = {"prompt", "provider", "model", "role", "alias"}
    runtime_context = _render_context_keys(
        REPO / "skills/goal-branch-orchestrator/scripts/runtime_packet_runner.py", "render_runtime_args"
    ) | _render_context_keys(
        REPO / "skills/goal-branch-orchestrator/scripts/create_runtime_packet.py",
        "configured_route_commands",
        context_via_assignment=False,
    )

    assert smoke_tokens <= smoke_context
    assert smoke_tokens.isdisjoint({"prompt_file", "packet_id", "schema_name", "output_file", "worktree", "packet_dir"})
    assert run_tokens == runtime_context - smoke_context
    assert "schema_file" in run_tokens
    assert "schema_name" not in run_tokens


def test_contract_documents_bridge_doctor_smoke_status_ok_exemption():
    contract_text = (REPO / "skills/goal-config/references/configuration-contract.md").read_text(encoding="utf-8")
    assert (
        "Missing models, missing harness/binary, auth/API errors" in contract_text
    ), "contract should still mark smoke failures as errors"
    assert "opencode-bridge" in contract_text
    assert 'zero-exit JSON smoke responses like `{"status": "ok"}`' in contract_text


def test_goal_config_skill_reflects_bridge_readiness_without_assistant_text_requirement():
    skill_text = (REPO / "skills/goal-config/SKILL.md").read_text(encoding="utf-8")
    lowered = skill_text.lower()

    assert "missing assistant output" not in lowered
    assert "opencode-bridge" in lowered
    assert '"status": "ok"' in skill_text
    assert "zero-exit json" in lowered
    assert "zero-exit" in lowered or "zero exit" in lowered
    assert "as blocked evidence" in lowered


def test_contract_documents_model_policy_nesting_requirements():
    contract_text = (REPO / "skills/goal-config/references/configuration-contract.md").read_text(encoding="utf-8")
    lowered = contract_text.lower()

    assert "worker_model_policy.route_classes" in lowered
    assert "review_model_policy.default_tier" in lowered
    assert "review_model_policy.routes" in lowered
    assert "amender_model_policy.allowed_routes" in lowered
    assert "lite_model_policy.model_map" in lowered

    for route_class in cgc.WORKER_POLICY_ROUTE_CLASSES:
        assert route_class in lowered
    for tier in cgc.REVIEW_POLICY_ROUTE_TIERS:
        assert tier in lowered


def test_contract_documents_default_harness_runtime_invocation_model():
    contract_text = (REPO / "skills/goal-config/references/configuration-contract.md").read_text(encoding="utf-8")
    lowered = contract_text.lower()

    assert "runtime invocation is synthesized by orchestrators" in lowered
    assert "run_args" in lowered and "can be omitted" in lowered
    assert (
        "custom harnesses and non-default launcher-managed paths should usually provide `run_args` explicitly"
        in lowered
    )
    assert "`run_args` templates" in contract_text


# --- check_goal_config: malformed / missing config fails closed (SystemExit, not traceback) ---
def test_check_load_json_fails_closed(tmp_path):
    bad = tmp_path / "goal.config.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cgc.load_json(bad)
    with pytest.raises(SystemExit):
        cgc.load_json(tmp_path / "does-not-exist.json")


# --- 2026-06-18 convergence pass 3: check_goal_config.load_json also fails closed on a non-UTF-8
#     --config (UnicodeDecodeError is a ValueError, not OSError/JSONDecodeError) ---
def test_check_load_json_fails_closed_on_non_utf8(tmp_path):
    nonutf8 = tmp_path / "goal.config.json"
    nonutf8.write_bytes(b"\xff\xfe{}")
    with pytest.raises(SystemExit):
        cgc.load_json(nonutf8)


# --- config-supplied non-numeric timeout no longer crashes the smoke path ---
def test_run_harness_smoke_tolerates_non_numeric_timeout():
    # Missing prompt/expect short-circuits to a failed result; the int() on a non-numeric
    # timeout used to raise ValueError before reaching that return.
    result, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
        {"timeout_seconds": "not-an-int"},
        harness={"kind": "codex", "command": "true"},
    )
    assert result["status"] == "failed"
    assert failures  # missing prompt/expect recorded, no crash


def test_opencode_deepseek_role_overrides_preserve_model_role_references():
    contract = cgc.load_contract()
    args = argparse.Namespace(
        lite_model="deepseek/deepseek-v4-flash",
        demanding_model="deepseek/deepseek-v4-pro",
        provider=None,
        harness_spec=[],
        role_model=[
            "prompt_audit_agent:codex:openai/gpt-5.5",
            "worker_codex_spark:codex:openai/gpt-5.3-codex-spark",
        ],
        effort_profile="balanced",
        validation_mode="model-check",
        output=None,
        state_output=None,
        from_discovery=None,
        mapping=None,
        lite_ladder=None,
        worker_ladder=None,
        reviewer_ladder=None,
        amender_ladder=None,
        max_active_branch_agents=None,
        max_active_worker_packets=None,
        max_waves=None,
        lite_timeout_seconds=None,
        demanding_timeout_seconds=None,
    )
    config = crc.opencode_deepseek_v4_config(contract, args)
    config["model_policies"] = crc.build_model_policies(config, contract)
    model_roles = set(config["models"].keys())

    for route_group in config["model_policies"]["worker_model_policy"]["route_classes"].values():
        assert all(route in model_roles for route in route_group)
    for route_group in config["model_policies"]["review_model_policy"]["routes"].values():
        assert all(route in model_roles for route in route_group)


def test_ensure_harness_smokes_fails_closed_on_malformed_lite_ladder_entry():
    config = {
        "models": {
            "lite_agent": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "harness": "opencode-bridge",
            }
        },
        "model_ladders": {"lite": [{}], "worker": []},
        "harness_smokes": {},
    }

    with pytest.raises(SystemExit) as excinfo:
        crc.ensure_harness_smokes(config)

    assert "model_ladders.lite" in str(excinfo.value)


def test_build_model_policies_fails_closed_on_malformed_worker_ladder_entry():
    config = {
        "models": {"lite_agent": {"provider": "deepseek", "model": "deepseek-v4-flash"}},
        "model_ladders": {"worker": [{}]},
    }

    with pytest.raises(SystemExit) as excinfo:
        crc.build_model_policies(config, cgc.load_contract())

    assert "model_ladders.worker" in str(excinfo.value)


def test_opencode_deepseek_explicit_alias_overrides_preserve_policy_refs_as_roles():
    contract = cgc.load_contract()
    args = argparse.Namespace(
        lite_model="deepseek/deepseek-v4-flash",
        demanding_model="deepseek/deepseek-v4-pro",
        provider=None,
        harness_spec=[],
        role_model=[
            "lite_agent:opencode-bridge:deepseek/deepseek-v4-flash:ds-flash-legacy",
            "demanding_agent:opencode-bridge:deepseek/deepseek-v4-pro:ds-pro-legacy",
            "worker_codex_spark:codex:openai/gpt-5.3-codex-spark:codex-spark-legacy",
            "worker_codex_mini:codex:openai/gpt-5.4-mini:codex-mini-legacy",
            "prompt_audit_agent:codex:openai/gpt-5.5:gpt-5.5-legacy",
        ],
        effort_profile="balanced",
        validation_mode="model-check",
        output=None,
        state_output=None,
        from_discovery=None,
        mapping=None,
        lite_ladder=None,
        worker_ladder=None,
        reviewer_ladder=None,
        amender_ladder=None,
        max_active_branch_agents=None,
        max_active_worker_packets=None,
        max_waves=None,
        lite_timeout_seconds=None,
        demanding_timeout_seconds=None,
    )
    config = crc.opencode_deepseek_v4_config(contract, args)
    crc.finalize_config(config, contract, args)
    policy_refs = [
        item for refs in config["model_policies"]["worker_model_policy"]["route_classes"].values() for item in refs
    ] + [item for refs in config["model_policies"]["review_model_policy"]["routes"].values() for item in refs]
    model_roles = set(config["models"].keys())
    assert all(item in model_roles for item in policy_refs)


def _opencode_deepseek_policy_regression_config(tmp_path: Path):
    contract = cgc.load_contract()
    args = argparse.Namespace(
        lite_model="deepseek/deepseek-v4-flash",
        demanding_model="deepseek/deepseek-v4-pro",
        provider=None,
        harness_spec=[],
        role_model=[],
        effort_profile="balanced",
        validation_mode="model-check",
        output=None,
        state_output=None,
        from_discovery=None,
        mapping=None,
        lite_ladder=None,
        worker_ladder=None,
        reviewer_ladder=None,
        amender_ladder=None,
        max_active_branch_agents=None,
        max_active_worker_packets=None,
        max_waves=None,
        lite_timeout_seconds=None,
        demanding_timeout_seconds=None,
    )
    config = crc.opencode_deepseek_v4_config(contract, args)
    crc.finalize_config(config, contract, args)
    for harness_name, harness in config["harnesses"].items():
        if isinstance(harness, dict):
            harness["command"] = "python3"
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


@pytest.mark.parametrize(
    "policy_key, bad_value",
    [
        ("worker_model_policy", []),
        ("review_model_policy", "bad"),
        ("amender_model_policy", 7),
        ("lite_model_policy", None),
    ],
)
def test_check_goal_config_fails_closed_on_non_object_nested_model_policy(tmp_path, policy_key, bad_value):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"][policy_key] = bad_value
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / f"goal-config-check-{policy_key}.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--state-output",
            str(tmp_path / f"goal-config-state-{policy_key}.json"),
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(f"{policy_key} must be an object" in failure for failure in report["failures"])


def test_check_goal_config_reports_malformed_lite_ladder_without_traceback(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"]["lite_model_policy"]["default_ladder"] = 1
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check.json"
    result = subprocess.run(
        [
            "python3",
            str(REPO / "skills/goal-config/scripts/check_goal_config.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--stdout",
            "none",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert any(
        "lite_model_policy.default_ladder must be an array of role IDs" in failure for failure in report["failures"]
    )


def test_check_goal_config_fails_closed_on_unhashable_model_harness(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["models"]["lite_agent"]["harness"] = []
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-unhashable-harness.json"
    result = subprocess.run(
        [
            "python3",
            str(REPO / "skills/goal-config/scripts/check_goal_config.py"),
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--stdout",
            "none",
        ],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert any("lite_agent: missing harness" in failure for failure in report["failures"])
    lite_report = next(item for item in report["harnesses"] if item["role"] == "lite_agent")
    assert lite_report["model_check"] == {"status": "failed", "reason": "missing harness"}


def test_opencode_deepseek_generated_default_policy_routes_are_coherent():
    contract = cgc.load_contract()
    args = _create_goal_config_args()
    config = crc.opencode_deepseek_v4_config(contract, args)
    crc.finalize_config(config, contract, args)

    policy = config["model_policies"]
    worker_policy = policy["worker_model_policy"]
    worker_allowed = set(worker_policy["allowed_routes"])
    worker_default = set(worker_policy["default_ladder"])
    assert cgc.validate_model_policy_references(config) == []
    for route_class, roles in worker_policy["route_classes"].items():
        assert set(roles) <= worker_allowed, f"{route_class} escapes worker allowed_routes"
        assert set(roles) <= worker_default, f"{route_class} escapes worker default_ladder"

    reviewer_ladder = set(config["model_ladders"]["reviewer"])
    for tier, roles in policy["review_model_policy"]["routes"].items():
        assert set(roles) <= reviewer_ladder, f"{tier} escapes model_ladders.reviewer"


def test_opencode_deepseek_explicit_ladders_bind_generated_policy_routes():
    contract = cgc.load_contract()
    args = _create_goal_config_args(
        worker_ladder="worker_codex_mini",
        reviewer_ladder="lite_agent",
        amender_ladder="lite_agent",
    )
    config = crc.opencode_deepseek_v4_config(contract, args)
    crc.finalize_config(config, contract, args)

    worker_policy = config["model_policies"]["worker_model_policy"]
    worker_routes = set(worker_policy["allowed_routes"])
    assert worker_policy["default_ladder"] == ["worker_codex_mini"]
    assert worker_policy["allowed_routes"] == ["worker_codex_mini"]
    for route_class, roles in worker_policy["route_classes"].items():
        assert set(roles) <= worker_routes, f"{route_class} route escapes explicit worker ladder"

    review_routes = set(config["model_ladders"]["reviewer"])
    assert config["model_ladders"]["reviewer"] == ["lite_agent"]
    for tier, roles in config["model_policies"]["review_model_policy"]["routes"].items():
        assert set(roles) <= review_routes, f"{tier} route escapes explicit reviewer ladder"


def test_check_goal_config_rejects_policy_routes_outside_configured_ladders(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_ladders"]["worker"] = ["worker_codex_mini"]
    config["model_ladders"]["reviewer"] = ["lite_agent"]
    config["model_policies"]["worker_model_policy"]["default_ladder"] = ["worker_codex_mini"]
    config["model_policies"]["worker_model_policy"]["allowed_routes"] = ["worker_codex_mini"]
    config["model_policies"]["worker_model_policy"]["route_classes"]["mechanical"] = ["lite_agent"]
    config["model_policies"]["review_model_policy"]["routes"]["standard"] = ["demanding_agent"]
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-incoherent-routes.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output), "--for-preflight"])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("worker_model_policy.route_classes.mechanical" in failure for failure in report["failures"])
    assert any("review_model_policy.routes.standard" in failure for failure in report["failures"])


def test_check_goal_config_rejects_default_opencode_policy_bypass_incoherence(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_ladders"]["worker"] = ["lite_agent", "worker_codex_spark", "worker_codex_mini"]
    config["model_ladders"]["reviewer"] = ["demanding_agent"]
    config["model_policies"]["worker_model_policy"]["default_ladder"] = [
        "lite_agent",
        "worker_codex_spark",
        "worker_codex_mini",
    ]
    config["model_policies"]["worker_model_policy"]["allowed_routes"] = [
        "lite_agent",
        "worker_codex_spark",
        "worker_codex_mini",
    ]
    config["model_policies"]["worker_model_policy"]["route_classes"]["complex-code"] = [
        "demanding_agent",
        "worker_codex_spark",
    ]
    config["model_policies"]["review_model_policy"]["routes"]["light"] = ["lite_agent"]
    config["model_policies"]["review_model_policy"]["routes"]["heavy"] = [
        "demanding_agent",
        "prompt_audit_agent",
    ]
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-default-bypass.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("worker_model_policy.route_classes.complex-code" in failure for failure in report["failures"])
    assert any("review_model_policy.routes.light" in failure for failure in report["failures"])
    assert any("review_model_policy.routes.heavy" in failure for failure in report["failures"])


def test_check_goal_config_fails_closed_on_unknown_policy_route_refs(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"]["worker_model_policy"]["route_classes"]["mechanical"].append("unknown_worker_role")
    config["model_policies"]["review_model_policy"]["routes"]["standard"].append("unknown_reviewer_role")
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("route_classes.mechanical" in failure for failure in report["failures"])
    assert any("routes.standard" in failure for failure in report["failures"])


@pytest.mark.parametrize(
    "mutation, expected_fragment",
    [
        ("missing", "model_policies must be an object"),
        ("empty", "model_policies missing required keys"),
        ("non_object", "model_policies must be an object"),
    ],
)
def test_check_goal_config_fails_closed_when_model_policies_missing_or_invalid(
    tmp_path: Path, mutation: str, expected_fragment: str
):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    if mutation == "missing":
        config.pop("model_policies", None)
    elif mutation == "empty":
        config["model_policies"] = {}
    else:
        config["model_policies"] = "broken"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / f"goal-config-check-{mutation}.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(expected_fragment in failure for failure in report["failures"])


def test_check_goal_config_fails_closed_on_non_string_policy_ref(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"]["worker_model_policy"]["route_classes"]["docs"].append(123)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-non-string.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("contains non-string role reference" in failure for failure in report["failures"])


def test_check_goal_config_fails_closed_when_review_routes_missing_required_tiers(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    model_roles = list(config["models"])
    config["model_policies"]["review_model_policy"]["routes"] = {
        "light": [model_roles[0]],
    }
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-review-tiers.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("missing required route keys" in failure for failure in report["failures"])


def test_check_goal_config_fails_closed_when_worker_route_classes_missing_required_keys(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"]["worker_model_policy"]["route_classes"] = {"mechanical": ["lite_agent"]}
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-route-classes.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("missing required route keys" in failure for failure in report["failures"])


@pytest.mark.parametrize(
    "mutation, expected_fragment",
    [
        ("empty", "lite_model_policy.model_map is missing entries for roles: lite_agent"),
        ("mismatch", "lite_model_policy.model_map[lite_agent] must match config.models[lite_agent].model"),
    ],
)
def test_check_goal_config_fails_closed_when_lite_model_map_is_invalid(tmp_path, mutation, expected_fragment):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    if mutation == "empty":
        config["model_policies"]["lite_model_policy"]["model_map"] = {}
    else:
        config["model_policies"]["lite_model_policy"]["model_map"]["lite_agent"] = "totally-wrong-model-id"

    output = tmp_path / f"goal-config-check-lite-model-map-{mutation}.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any(expected_fragment in failure for failure in report["failures"])


def test_check_goal_config_fails_closed_when_review_default_tier_is_unknown(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["model_policies"]["review_model_policy"]["default_tier"] = "bogus"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check-review-default-tier.json"
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--output", str(output)])
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("review_model_policy.default_tier must be one of" in failure for failure in report["failures"])


def test_opencode_deepseek_rejects_openrouter_bridge_provider_override():
    contract = cgc.load_contract()
    args = argparse.Namespace(
        lite_model="deepseek/deepseek-v4-flash",
        demanding_model="deepseek/deepseek-v4-pro",
        provider="openrouter",
        harness_spec=[],
        role_model=[],
        effort_profile="balanced",
        validation_mode="model-check",
        output=None,
        state_output=None,
        from_discovery=None,
        mapping=None,
        lite_ladder=None,
        worker_ladder=None,
        reviewer_ladder=None,
        amender_ladder=None,
        max_active_branch_agents=None,
        max_active_worker_packets=None,
        max_waves=None,
        lite_timeout_seconds=None,
        demanding_timeout_seconds=None,
    )
    with pytest.raises(SystemExit):
        crc.opencode_deepseek_v4_config(contract, args)


def test_opencode_deepseek_rejects_openrouter_bridge_role_model_override():
    contract = cgc.load_contract()
    args = _create_goal_config_args(role_model=["demanding_agent:opencode-bridge:openrouter/deepseek/deepseek-v4-pro"])
    config = crc.opencode_deepseek_v4_config(contract, args)

    with pytest.raises(SystemExit, match="provider 'openrouter' is not valid for harness 'opencode-bridge'"):
        crc.finalize_config(config, contract, args)


# --- opencode-bridge route validation (contract MUST) ---
def test_bridge_route_validation():
    assert cgc._bridge_route_failures({"model": "deepseek-v4-flash"}) == []
    assert cgc._bridge_route_failures({"model": "deepseek-v4-pro"}) == []
    assert cgc._bridge_route_failures({"model": "openrouter/deepseek/deepseek-v4-pro"})
    # an unknown bridge model is rejected
    assert cgc._bridge_route_failures({"model": "deepseek-v9-imaginary"})
    # missing model is deferred to the basic check (no duplicate failure here)
    assert cgc._bridge_route_failures({}) == []


def test_check_model_for_harness_rejects_bad_bridge_route():
    result, failures = cgc.check_model_for_harness(
        {"provider": "deepseek", "model": "deepseek-v9-imaginary"},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "true"},
    )
    assert result["status"] == "failed"
    assert any("not a known bridge route" in f for f in failures)


def test_check_model_for_harness_rejects_bridge_non_deepseek_provider():
    result, failures = cgc.check_model_for_harness(
        {"provider": "openai", "model": "deepseek-v4-pro"},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "true"},
    )
    assert result["status"] == "failed"
    assert any("opencode-bridge provider" in failure for failure in failures)


def test_opencode_deepseek_default_route_policy_aliases_align_with_contract():
    contract = cgc.load_contract()
    args = argparse.Namespace(
        lite_model="deepseek/deepseek-v4-flash",
        demanding_model="deepseek/deepseek-v4-pro",
        provider=None,
        harness_spec=[],
        role_model=[],
        effort_profile="balanced",
        validation_mode="model-check",
        output=None,
        state_output=None,
        from_discovery=None,
        mapping=None,
        lite_ladder=None,
        worker_ladder=None,
        reviewer_ladder=None,
        amender_ladder=None,
        max_active_branch_agents=None,
        max_active_worker_packets=None,
        max_waves=None,
        lite_timeout_seconds=None,
        demanding_timeout_seconds=None,
    )
    config = crc.opencode_deepseek_v4_config(contract, args)
    config["model_policies"] = crc.build_model_policies(config, contract)
    policy = config["model_policies"]
    assert policy["worker_model_policy"]["route_classes"]["mechanical"] == ["lite_agent"]
    assert policy["worker_model_policy"]["route_classes"]["docs"] == ["lite_agent"]
    assert policy["worker_model_policy"]["route_classes"]["normal-code"] == ["lite_agent", "worker_codex_spark"]
    assert policy["review_model_policy"]["routes"]["light"] == ["lite_agent"]
    assert policy["review_model_policy"]["routes"]["standard"] == ["demanding_agent"]
    assert policy["review_model_policy"]["routes"]["heavy"] == ["demanding_agent", "prompt_audit_agent"]


def test_parse_role_model_rejects_provider_mismatch_for_implied_harness():
    with pytest.raises(SystemExit):
        crc.parse_role_model("worker:codex:anthropic/claude-sonnet")


# --- 2026-06-18 convergence pass 2: render_tokens fails closed on an unknown/invalid template
#     token (a doc-sanctioned smoke_args token absent from the smoke context used to KeyError-crash) ---
def test_render_tokens_fails_closed_on_unknown_token():
    with pytest.raises(SystemExit):
        cgc.render_tokens(["{prompt_file}"], context={"prompt": "x"})
    # well-formed tokens still render
    assert cgc.render_tokens(["--p", "{prompt}"], context={"prompt": "hello"}) == ["--p", "hello"]


# --- 2026-06-18 convergence pass 2: an invalid --discover-model-filter regex fails closed
#     (was an uncaught re.error traceback) ---
def test_profile_discovery_rejects_invalid_regex():
    with pytest.raises(SystemExit):
        cgc.profile_discovery_candidates("mixed-fast", model_filter="[", max_candidates=None)


# --- 2026-06-18 convergence pass 7: --reuse-smoke-report reader tolerates a non-list
#     harnesses/checked_roles (external semi-trusted artifact) instead of TypeError ---
def test_load_smoke_cache_tolerates_non_list_harnesses(tmp_path):
    rpt = tmp_path / "smoke.json"
    rpt.write_text(json.dumps({"status": "pass", "harnesses": 5}), encoding="utf-8")
    assert isinstance(cgc.load_smoke_cache([rpt]), dict)  # was TypeError on the non-list harnesses


def _standard_smoke_cache_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "smoke"},
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "models": {
                    "worker": {
                        "alias": "worker",
                        "harness": "codex_worker",
                        "provider": "openai",
                        "model": "gpt-5.5",
                    }
                },
                "harnesses": {
                    "codex_worker": {
                        "kind": "codex",
                        "command": "python3",
                        "smoke_args": ["-c", "print('NO_MATCH')"],
                    }
                },
                "harness_smokes": {
                    "worker": {
                        "prompt": "TOKEN",
                        "expect": "TOKEN",
                        "timeout_seconds": 5,
                    }
                },
                "model_policies": {
                    "worker_model_policy": {
                        "route_classes": {
                            "mechanical": ["worker"],
                            "docs": ["worker"],
                            "small-edit": ["worker"],
                            "normal-code": ["worker"],
                            "complex-code": ["worker"],
                            "custom": ["worker"],
                        },
                        "default_ladder": ["worker"],
                        "allowed_routes": ["worker"],
                    },
                    "review_model_policy": {
                        "default_tier": "standard",
                        "routes": {
                            "light": ["worker"],
                            "standard": ["worker"],
                            "heavy": ["worker"],
                        },
                    },
                    "amender_model_policy": {
                        "default_ladder": ["worker"],
                        "allowed_routes": ["worker"],
                    },
                    "lite_model_policy": {
                        "default_ladder": ["worker"],
                        "allowed_routes": ["worker"],
                        "model_map": {"worker": "gpt-5.5"},
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_reuse_report(
    path: Path,
    *,
    status: str = "pass",
    config_sha256: str | None = None,
    route_evidence: bool = False,
    failures: list[str] | None = None,
    rejected_routes: list[dict] | None = None,
) -> None:
    report = {
        "schema_version": 1,
        "status": status,
        "mode": "smoke",
        "check_mode": "smoke",
        "checked_roles": ["worker"],
        "failures": failures if failures is not None else ([] if status == "pass" else ["prior run failed"]),
        "harnesses": [
            {
                "role": "worker",
                "harness": "codex_worker",
                "provider": "openai",
                "model": "gpt-5.5",
                "model_check": {"status": "pass"},
                "smoke": {"status": "pass", "contains_expected": True},
            }
        ],
    }
    if route_evidence:
        report["accepted_routes"] = [
            {
                "role": "worker",
                "harness": "codex_worker",
                "provider": "openai",
                "model": "gpt-5.5",
            }
        ]
        report["rejected_routes"] = rejected_routes if rejected_routes is not None else []
    if config_sha256 is not None:
        report["config_sha256"] = config_sha256
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_standard_smoke_with_reuse(config_path: Path, reuse_report: Path, output: Path) -> tuple[int, dict]:
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--smoke",
            "--reuse-smoke-report",
            str(reuse_report),
            "--output",
            str(output),
            "--stdout",
            "none",
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    return exit_code, json.loads(output.read_text(encoding="utf-8"))


def test_standard_smoke_reuse_ignores_failed_top_level_report_and_runs_current_smoke(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    reuse_report = tmp_path / "failed-reuse-smoke.json"
    _write_reuse_report(reuse_report, status="failed")

    exit_code, report = _run_standard_smoke_with_reuse(
        config_path,
        reuse_report,
        tmp_path / "goal-config-smoke.json",
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    smoke = report["harnesses"][0]["smoke"]
    assert smoke["status"] == "failed"
    assert smoke.get("reused") is not True
    assert any("smoke output did not contain expected text" in failure for failure in report["failures"])


def test_standard_smoke_reuse_ignores_stale_config_sha_and_runs_current_smoke(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    reuse_report = tmp_path / "stale-reuse-smoke.json"
    _write_reuse_report(reuse_report, status="pass", config_sha256="stale-not-current")

    exit_code, report = _run_standard_smoke_with_reuse(
        config_path,
        reuse_report,
        tmp_path / "goal-config-smoke.json",
    )

    assert exit_code == 1
    assert report["status"] == "failed"
    smoke = report["harnesses"][0]["smoke"]
    assert smoke["status"] == "failed"
    assert smoke.get("reused") is not True


def test_standard_smoke_reuse_rejects_hashless_top_level_pass_and_runs_current_smoke(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    reuse_report = tmp_path / "hashless-reuse-smoke.json"
    _write_reuse_report(reuse_report, status="pass")

    exit_code, report = _run_standard_smoke_with_reuse(
        config_path,
        reuse_report,
        tmp_path / "goal-config-smoke.json",
    )

    smoke = report["harnesses"][0]["smoke"]
    assert {
        "exit_code": exit_code,
        "status": report["status"],
        "smoke_status": smoke["status"],
        "smoke_reused": smoke.get("reused"),
    } == {
        "exit_code": 1,
        "status": "failed",
        "smoke_status": "failed",
        "smoke_reused": None,
    }
    assert any("smoke output did not contain expected text" in failure for failure in report["failures"])


def test_standard_smoke_reuse_accepts_fresh_matching_config_sha(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    reuse_report = tmp_path / "fresh-reuse-smoke.json"
    _write_reuse_report(
        reuse_report,
        status="pass",
        config_sha256=cgc._config_sha256(config_path),
        route_evidence=True,
    )

    exit_code, report = _run_standard_smoke_with_reuse(
        config_path,
        reuse_report,
        tmp_path / "goal-config-smoke.json",
    )

    assert exit_code == 0
    assert report["status"] == "pass"
    smoke = report["harnesses"][0]["smoke"]
    assert smoke["status"] == "pass"
    assert smoke["reused"] is True
    assert smoke["reused_from_report"].endswith("fresh-reuse-smoke.json")


def test_standard_smoke_reuse_rejects_poisoned_matching_config_sha(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    reuse_report = tmp_path / "poisoned-reuse-smoke.json"
    output = tmp_path / "goal-config-smoke.json"
    _write_reuse_report(
        reuse_report,
        status="pass",
        config_sha256=cgc._config_sha256(config_path),
        route_evidence=True,
        failures=["poisoned route evidence"],
        rejected_routes=[
            {
                "role": "worker",
                "harness": "codex_worker",
                "provider": "openai",
                "model": "gpt-5.5",
                "reasons": ["poisoned"],
            }
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO / "skills/goal-config/scripts/check_goal_config.py"),
            "--config",
            str(config_path),
            "--smoke",
            "--reuse-smoke-report",
            str(reuse_report),
            "--output",
            str(output),
            "--stdout",
            "none",
        ],
        check=False,
        cwd=REPO,
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 1
    assert report["status"] == "failed"
    smoke = report["harnesses"][0]["smoke"]
    assert smoke["status"] == "failed"
    assert smoke.get("reused") is not True
    assert any("smoke output did not contain expected text" in failure for failure in report["failures"])


def test_standard_smoke_reuse_accepts_from_discovery_report_with_matching_route_set(tmp_path):
    config_path = _standard_smoke_cache_config(tmp_path)
    config = cgc.load_json(config_path)
    config["profile"] = "from-discovery"
    config["source_discovery"] = {"path": "goal-config-discovery.json", "mapping": "auto"}
    config["harnesses"]["codex_worker"]["smoke_args"] = ["-c", "raise SystemExit('harness invoked')"]
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reuse_report = tmp_path / "goal-config-discovery.json"
    reuse_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pass",
                "mode": "discovery",
                "check_mode": "discovery",
                "config_sha256": "seed-config-sha-not-final-config-sha",
                "checked_roles": ["worker"],
                "accepted_routes": [
                    {
                        "role": "worker",
                        "harness": "codex_worker",
                        "provider": "openai",
                        "model": "gpt-5.5",
                    }
                ],
                "rejected_routes": [],
                "failures": [],
                "harnesses": [
                    {
                        "role": "worker",
                        "harness": "codex_worker",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "model_check": {"status": "pass"},
                        "smoke": {"status": "pass", "contains_expected": True},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code, report = _run_standard_smoke_with_reuse(
        config_path,
        reuse_report,
        tmp_path / "goal-config-smoke.json",
    )

    assert exit_code == 0
    assert report["status"] == "pass"
    smoke = report["harnesses"][0]["smoke"]
    assert smoke["status"] == "pass"
    assert smoke["reused"] is True
    assert smoke["reused_from_report"].endswith("goal-config-discovery.json")


def test_for_preflight_reuse_smoke_report_rejects_stale_config_sha(tmp_path):
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "smoke"},
                "aggressiveness": {
                    "max_active_branch_agents": 1,
                    "max_active_worker_packets": 1,
                    "max_waves": 1,
                },
                "telemetry": {"mode": "standard", "collect": []},
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "models": {
                    "lite_agent": {
                        "alias": "lite-agent",
                        "harness": "opencode-bridge",
                        "provider": [],
                        "model": "deepseek/deepseek-v4-flash",
                    }
                },
                "harnesses": {
                    "opencode-bridge": {
                        "kind": cgc.BRIDGE_HARNESS_KIND,
                        "command": "python3",
                        "smoke_args": ["-c", "print('TOKEN')"],
                    }
                },
                "harness_smokes": {
                    "lite_agent": {
                        "prompt": "TOKEN",
                        "expect": "TOKEN",
                        "timeout_seconds": 5,
                    }
                },
                "model_policies": {
                    "worker_model_policy": {
                        "route_classes": {
                            "mechanical": ["lite_agent"],
                            "docs": ["lite_agent"],
                            "small-edit": ["lite_agent"],
                            "normal-code": ["lite_agent"],
                            "complex-code": ["lite_agent"],
                            "custom": ["lite_agent"],
                        },
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                    },
                    "review_model_policy": {
                        "default_tier": "standard",
                        "routes": {
                            "light": ["lite_agent"],
                            "standard": ["lite_agent"],
                            "heavy": ["lite_agent"],
                        },
                    },
                    "amender_model_policy": {
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                    },
                    "lite_model_policy": {
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                        "model_map": {"lite_agent": "deepseek/deepseek-v4-flash"},
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    stale_report = tmp_path / "stale-smoke.json"
    stale_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pass",
                "mode": "smoke",
                "check_mode": "smoke",
                "config_sha256": "stale-not-current",
                "checked_roles": ["lite_agent"],
                "accepted_routes": [
                    {
                        "role": "lite_agent",
                        "harness": "opencode-bridge",
                        "provider": "deepseek",
                        "model": "deepseek/deepseek-v4-flash",
                    }
                ],
                "failures": [],
                "harnesses": [
                    {
                        "role": "lite_agent",
                        "harness": "opencode-bridge",
                        "provider": "deepseek",
                        "model": "deepseek/deepseek-v4-flash",
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
    output = tmp_path / "goal-config-for-preflight.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--for-preflight",
            "--smoke",
            "--reuse-smoke-report",
            str(stale_report),
            "--output",
            str(output),
        ]
    )
    ctx = cgc.build_check_context(args)

    exit_code, _preflight_remediation = cgc.run_for_preflight_mode(args, ctx)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["accepted_routes"] == []
    assert any("config_sha256" in failure for failure in report["failures"])


def test_for_preflight_reuse_smoke_report_rejects_empty_route_evidence(tmp_path):
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "smoke"},
                "aggressiveness": {
                    "max_active_branch_agents": 1,
                    "max_active_worker_packets": 1,
                    "max_waves": 1,
                },
                "telemetry": {"mode": "standard", "collect": []},
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "models": {
                    "lite_agent": {
                        "alias": "lite-agent",
                        "harness": "opencode-bridge",
                        "provider": "deepseek",
                        "model": "deepseek/deepseek-v4-flash",
                    }
                },
                "harnesses": {
                    "opencode-bridge": {
                        "kind": cgc.BRIDGE_HARNESS_KIND,
                        "command": "python3",
                        "smoke_args": ["-c", "print('TOKEN')"],
                    }
                },
                "harness_smokes": {
                    "lite_agent": {
                        "prompt": "TOKEN",
                        "expect": "TOKEN",
                        "timeout_seconds": 5,
                    }
                },
                "model_policies": {
                    "worker_model_policy": {
                        "route_classes": {
                            "mechanical": ["lite_agent"],
                            "docs": ["lite_agent"],
                            "small-edit": ["lite_agent"],
                            "normal-code": ["lite_agent"],
                            "complex-code": ["lite_agent"],
                            "custom": ["lite_agent"],
                        },
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                    },
                    "review_model_policy": {
                        "default_tier": "standard",
                        "routes": {
                            "light": ["lite_agent"],
                            "standard": ["lite_agent"],
                            "heavy": ["lite_agent"],
                        },
                    },
                    "amender_model_policy": {
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                    },
                    "lite_model_policy": {
                        "default_ladder": ["lite_agent"],
                        "allowed_routes": ["lite_agent"],
                        "model_map": {"lite_agent": "deepseek/deepseek-v4-flash"},
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    empty_report = tmp_path / "empty-smoke.json"
    empty_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "pass",
                "mode": "smoke",
                "check_mode": "smoke",
                "config_sha256": cgc._config_sha256(config_path),
                "checked_roles": [],
                "accepted_routes": [],
                "failures": [],
                "harnesses": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "goal-config-for-preflight.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--for-preflight",
            "--smoke",
            "--reuse-smoke-report",
            str(empty_report),
            "--output",
            str(output),
        ]
    )
    ctx = cgc.build_check_context(args)

    exit_code, _preflight_remediation = cgc.run_for_preflight_mode(args, ctx)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["accepted_routes"] == []
    assert any("reusable smoke report contains no accepted route evidence" in failure for failure in report["failures"])


# --- create_goal_config: file-input loaders fail closed ---
def test_create_loaders_fail_closed(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        crc.load_discovery_report(bad)
    with pytest.raises(SystemExit):
        crc.load_discovery_report(tmp_path / "missing.json")
    with pytest.raises(SystemExit):
        crc.load_harness_spec("{not valid json")
    with pytest.raises(SystemExit):
        crc.load_harness_spec(str(tmp_path / "missing-harness.json"))


# --- 2026-06-18 fresh-audit pass: F2 find_route rejects a JSON boolean selector instead of silently
#     consuming it as a route index (bool is an int subclass: True->1, False->0). ---
def test_find_route_rejects_bool_selector():
    routes = [{"role": "lite"}, {"role": "demanding"}]
    with pytest.raises(SystemExit):
        crc.find_route(routes, True)
    with pytest.raises(SystemExit):
        crc.find_route(routes, False)
    assert crc.find_route(routes, 0)["role"] == "lite"  # genuine int index still works


def test_find_route_with_unhashable_route_value_still_fails_closed(tmp_path):
    routes = [{"role": [], "alias": [], "provider": "openai", "model": "gpt-5.5"}]
    with pytest.raises(SystemExit):
        crc.find_route(routes, "lite")


def test_check_goal_config_fixtures_rejects_unhashable_alias_in_configured_rows():
    with pytest.raises(SystemExit):
        cgcf._collect_dict_items_by_string_field([{"alias": []}], field="alias", context="configured route model row")


def test_check_goal_config_fixtures_rejects_unhashable_alias_in_codex_rows():
    with pytest.raises(SystemExit):
        cgcf._collect_dict_items_by_string_field([{"alias": {}}], field="alias", context="configured codex route row")


def test_check_goal_config_fixtures_rejects_unhashable_question_id():
    with pytest.raises(SystemExit):
        cgcf._collect_string_values([{"id": []}], field="id", context="preference question")


def test_check_goal_config_fixtures_rejects_unhashable_option_id():
    with pytest.raises(SystemExit):
        cgcf._collect_string_values([{"id": {}}], field="id", context="validation_mode preference option")


def test_apply_discovery_mapping_auto_fails_closed_with_bad_route_metadata(tmp_path):
    report = {
        "accepted_routes": [
            {
                "role": "lite_agent",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
            },
            {
                "role": "broken_agent",
                "harness": ["opencode-bridge"],
                "provider": {"id": "deepseek"},
                "model": "deepseek/deepseek-v4-pro",
            },
        ]
    }
    path = tmp_path / "discovery.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(SystemExit):
        crc.apply_discovery_mapping({}, path, "auto")


def test_apply_discovery_mapping_rejects_raw_unaccepted_route_object(tmp_path):
    report = {
        "accepted_routes": [
            {
                "role": "lite_agent",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            }
        ]
    }
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(json.dumps(report), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "worker_primary": {
                    "harness": "opencode-bridge",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        crc.apply_discovery_mapping({}, discovery_path, str(mapping_path))
    assert "did not match accepted route" in str(exc.value)


def test_apply_discovery_mapping_object_selector_null_requires_existing_route_field(tmp_path):
    discovery = {
        "accepted_routes": [
            {
                "role": "ds-flash-max",
                "alias": "ds-flash-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
            },
            {
                "role": "ds-pro-max",
                "alias": "ds-pro-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-pro",
            },
        ]
    }
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(json.dumps(discovery), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"worker_extra": {"not_a_route_field": None}}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        crc.apply_discovery_mapping({}, discovery_path, str(mapping_path))

    assert "did not match accepted route" in str(exc.value)


def test_apply_discovery_mapping_partial_mapping_backfills_required_roles_with_policy_selection(tmp_path):
    discovery = {
        "accepted_routes": [
            {
                "role": "ds-flash-max",
                "alias": "ds-flash-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
            },
            {
                "role": "ds-pro-max",
                "alias": "ds-pro-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-pro",
            },
        ]
    }
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(json.dumps(discovery), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"worker_extra": "ds-pro-max"}), encoding="utf-8")

    config: dict = {}
    crc.apply_discovery_mapping(config, discovery_path, str(mapping_path))

    assert config["models"]["demanding_agent"]["model"] == "deepseek/deepseek-v4-pro"
    assert config["models"]["lite_agent"]["model"] == "deepseek/deepseek-v4-flash"
    assert config["models"]["worker_extra"]["model"] == "deepseek/deepseek-v4-pro"


def test_apply_discovery_mapping_full_required_role_mapping_preserves_explicit_targets(tmp_path):
    discovery = {
        "accepted_routes": [
            {
                "role": "ds-flash-max",
                "alias": "ds-flash-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
            },
            {
                "role": "ds-pro-max",
                "alias": "ds-pro-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-pro",
            },
        ]
    }
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(json.dumps(discovery), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "lite_agent": "ds-flash-max",
                "demanding_agent": {
                    "alias": "ds-pro-max",
                    "harness": "opencode-bridge",
                    "provider": "deepseek",
                    "model": "deepseek/deepseek-v4-pro",
                },
                "worker_extra": "ds-pro-max",
            }
        ),
        encoding="utf-8",
    )

    config: dict = {}
    crc.apply_discovery_mapping(config, discovery_path, str(mapping_path))

    assert config["models"]["lite_agent"]["model"] == "deepseek/deepseek-v4-flash"
    assert config["models"]["demanding_agent"]["model"] == "deepseek/deepseek-v4-pro"
    assert config["models"]["worker_extra"]["model"] == "deepseek/deepseek-v4-pro"


def test_apply_discovery_mapping_rejects_explicit_demanding_downgrade_when_demanding_route_exists(tmp_path):
    discovery = {
        "accepted_routes": [
            {
                "role": "ds-flash-max",
                "alias": "ds-flash-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-flash",
            },
            {
                "role": "ds-pro-max",
                "alias": "ds-pro-max",
                "harness": "opencode-bridge",
                "provider": "deepseek",
                "model": "deepseek/deepseek-v4-pro",
            },
        ]
    }
    discovery_path = tmp_path / "discovery.json"
    discovery_path.write_text(json.dumps(discovery), encoding="utf-8")
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"demanding_agent": "ds-flash-max"}), encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        crc.apply_discovery_mapping({}, discovery_path, str(mapping_path))

    assert "demanding_agent" in str(exc.value)
    assert "downgrade" in str(exc.value)


def test_command_result_collects_timeout_context(monkeypatch):
    def fake_run(command, *, text, capture_output, check, timeout):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=timeout,
            output="stdout-snack",
            stderr="stderr-signal",
        )

    monkeypatch.setattr(cgc.subprocess, "run", fake_run)
    result = cgc.command_result(["python3", "-c", "print('never')"], timeout_seconds=3)

    assert result["timed_out"] is True
    assert result["returncode"] is None
    assert result["stdout"] == "stdout-snack"
    assert result["stderr"] == "stderr-signal"


def test_run_harness_smoke_preserves_structured_json_errors(monkeypatch):
    def fake_command_result(command, *, timeout_seconds):
        payload = json.dumps({"type": "error", "status": 401, "message": "AuthenticateToken authentication failed"})
        return {
            "returncode": 1,
            "stdout": payload,
            "stderr": "",
            "elapsed_ms": 7,
            "timed_out": False,
        }

    monkeypatch.setattr(cgc, "command_result", fake_command_result)

    report, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
        {"prompt": "TOKEN", "expect": "TOKEN", "timeout_seconds": 30},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "python3", "smoke_args": ["{prompt}"]},
    )

    assert report["status"] == "failed"
    assert report["provider_status"] == 401
    assert report["provider_message"] == "AuthenticateToken authentication failed"
    assert report["provider_error"]["status"] == 401
    assert report["provider_error"]["message"] == "AuthenticateToken authentication failed"
    assert any("AuthenticateToken authentication failed" in failure for failure in failures)


def test_discover_profile_stops_bridge_provider_after_auth_failure(monkeypatch):
    config = {
        "schema_version": 1,
        "usage_units": {
            "token_counts": ["input"],
            "text_counts": ["prompt_chars"],
            "time_counts": ["elapsed_ms"],
        },
        "harnesses": {
            "opencode-bridge": {
                "kind": cgc.BRIDGE_HARNESS_KIND,
                "command": "python3",
                "smoke_args": ["{prompt}"],
            },
            "codex": {"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]},
        },
        "effort": {"lite_timeout_seconds": 8},
    }
    call_log: list[str] = []

    def fake_run_or_reuse_smoke(role, model, smoke, *, harness, smoke_cache):
        call_log.append(role)
        if role == "discover_ds_flash_max":
            return (
                {
                    "status": "failed",
                    "returncode": 1,
                    "timed_out": False,
                    "elapsed_ms": 1,
                    "provider_status": 401,
                    "provider_message": "AuthenticateToken authentication failed",
                    "provider_count": 2,
                },
                ["auth failed"],
            )
        return (
            {
                "status": "pass",
                "returncode": 0,
                "timed_out": False,
                "elapsed_ms": 1,
            },
            [],
        )

    monkeypatch.setattr(cgc, "run_or_reuse_smoke", fake_run_or_reuse_smoke)
    candidates, reports, failures, unvisited_routes = cgc.discover_profile_routes(
        config,
        profile_name="mixed-fast",
        model_filter=None,
        max_candidates=None,
        require_model_catalog=False,
        smoke=True,
        smoke_cache={},
        discover_all_candidates=True,
    )
    assert failures == []
    assert len(candidates) == 6

    second = next(report for report in reports if report["role"] == "discover_ds_pro_max")
    assert second["smoke"]["status"] == "skipped"
    assert second["smoke"]["provider_status"] == 401
    assert second["smoke"]["provider_message"] == "AuthenticateToken authentication failed"
    assert second["smoke"]["provider_count"] == 2
    assert second["smoke"]["provider_error"]["status"] == 401
    assert second["smoke"]["provider_error"]["message"] == "AuthenticateToken authentication failed"
    assert second["smoke"]["provider_error"]["count"] == 2

    assert call_log.count("discover_ds_flash_max") == 1
    assert "discover_ds_pro_max" not in call_log

    accepted_routes, rejected_routes = cgc.classify_routes(reports, smoke_requested=True)
    assert any(route["harness"] == "codex" for route in accepted_routes)
    assert any(
        route["role"] == "discover_ds_flash_max" and route["harness"] == "opencode-bridge" for route in rejected_routes
    )
    assert not any(route["role"] == "discover_ds_pro_max" for route in accepted_routes)


def test_command_result_success_path_records_output_and_exit_code(monkeypatch):
    monkeypatch.setattr(
        cgc.subprocess,
        "run",
        lambda command, *, text, capture_output, check, timeout: subprocess.CompletedProcess(
            command, returncode=13, stdout="ok", stderr="warn"
        ),
    )
    result = cgc.command_result(["echo", "hello"], timeout_seconds=7)

    assert result["returncode"] == 13
    assert result["stdout"] == "ok"
    assert result["stderr"] == "warn"
    assert result["timed_out"] is False
    assert result["elapsed_ms"] >= 0


def test_run_harness_smoke_passes_with_rendered_smoke_args_and_expected_output(monkeypatch):
    def fake_command_result(command, *, timeout_seconds):
        assert "HELLO-GOAL" in command
        return {
            "returncode": 0,
            "stdout": "reply: HELLO-GOAL",
            "stderr": "",
            "elapsed_ms": 11,
            "timed_out": False,
        }

    monkeypatch.setattr(cgc, "command_result", fake_command_result)
    result, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "openai", "model": "gpt-5.5", "alias": "codex-heavy"},
        {"prompt": "HELLO-GOAL", "expect": "HELLO-GOAL", "timeout_seconds": "15"},
        harness={"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]},
    )
    assert result["status"] == "pass"
    assert result["contains_expected"] is True
    assert not failures
    assert result["token_telemetry"]["available"] is False


def test_run_harness_smoke_accepts_json_status_ok_with_expected_token(monkeypatch):
    def fake_command_result(command, *, timeout_seconds):
        payload = json.dumps({"status": "ok", "message": "TOKEN"})
        return {
            "returncode": 0,
            "stdout": payload,
            "stderr": "",
            "elapsed_ms": 4,
            "timed_out": False,
        }

    monkeypatch.setattr(cgc, "command_result", fake_command_result)

    result, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
        {"prompt": "TOKEN", "expect": "TOKEN", "timeout_seconds": 2},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "python3", "smoke_args": ["{prompt}"]},
    )

    assert result["status"] == "pass"
    assert not failures
    assert result["contains_expected"] is True


def test_run_harness_smoke_allows_bridge_json_ok_without_expected_token(monkeypatch):
    def fake_command_result(command, *, timeout_seconds):
        payload = json.dumps({"status": "ok"})
        return {
            "returncode": 0,
            "stdout": payload,
            "stderr": "",
            "elapsed_ms": 4,
            "timed_out": False,
        }

    monkeypatch.setattr(cgc, "command_result", fake_command_result)

    result, failures = cgc.run_harness_smoke(
        "worker",
        {"provider": "deepseek", "model": "deepseek-v4-flash"},
        {"prompt": "TOKEN", "expect": "TOKEN", "timeout_seconds": 2},
        harness={"kind": cgc.BRIDGE_HARNESS_KIND, "command": "python3", "smoke_args": ["{prompt}"]},
    )

    assert result["status"] == "pass"
    assert not failures


def test_run_for_preflight_mode_rewrites_over_limit_aggressiveness_and_telemetry(tmp_path):
    config_path = tmp_path / "goal.config.preflight.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "smoke"},
                "aggressiveness": {
                    "max_active_branch_agents": 99,
                    "max_active_worker_packets": 99,
                    "max_waves": 99,
                },
                "telemetry": {
                    "mode": "standard",
                    "collect": ["unsupported-item"],
                    "schema_version": 0,
                },
                "models": {"worker": {"provider": "openai", "model": "gpt-5.5", "harness": "codex"}},
                "harnesses": {"codex": {"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]}},
                "usage_units": {"token_counts": [], "text_counts": [], "time_counts": []},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "goal-config-preflight.json"
    state = tmp_path / "goal-config-state.json"

    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--for-preflight",
            "--output",
            str(output),
            "--state-output",
            str(state),
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code, preflight_remediation = cgc.run_for_preflight_mode(args, ctx)

    assert exit_code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert preflight_remediation["available"] is True
    assert any(
        action["field"] == "aggressiveness.max_active_branch_agents" for action in preflight_remediation["actions"]
    )
    assert any(action["field"] == "telemetry.collect" for action in preflight_remediation["actions"])


def test_check_goal_config_standard_mode_captures_model_failures_and_state(tmp_path):
    config_path = tmp_path / "goal.config.standard.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "usage_units": {
                    "token_counts": ["input", "output"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "validation": {"mode": "model-check"},
                "models": {
                    "lite_agent": {
                        "harness": "codex_worker",
                        "provider": "openai",
                        "model": "gpt-5.5",
                        "alias": "codex-heavy",
                    },
                    "worker_agent": {
                        "harness": "bridge_worker",
                        "provider": "deepseek",
                        "model": "deepseek-unknown",
                        "alias": "ds-pro-max",
                    },
                },
                "harnesses": {
                    "codex_worker": {"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]},
                    "bridge_worker": {
                        "kind": cgc.BRIDGE_HARNESS_KIND,
                        "command": "python3",
                        "smoke_args": ["{prompt}"],
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "goal-config-standard-check.json"
    state_output = tmp_path / "goal-config-standard-state.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--require-models",
            "--output",
            str(output),
            "--state-output",
            str(state_output),
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))
    state = json.loads(state_output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("model_policies must be an object" in failure for failure in report["failures"])
    assert state["phase"] == "blocked"
    assert "inspect " in (state.get("next_command") or "")


def test_check_goal_config_requires_live_codex_catalog_for_require_models(tmp_path, monkeypatch):
    fake_codex_bin = tmp_path / "fake-codex-bin"
    fake_codex_bin.mkdir()
    config = {
        "schema_version": 1,
        "validation": {"mode": "model-check"},
        "usage_units": {
            "token_counts": ["input"],
            "text_counts": ["prompt_chars"],
            "time_counts": ["elapsed_ms"],
        },
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
                "command": "python3",
                "smoke_args": ["{prompt}"],
            }
        },
        "model_policies": {
            "worker_model_policy": {
                "route_classes": {
                    "mechanical": ["bad_codex"],
                    "docs": ["bad_codex"],
                    "small-edit": ["bad_codex"],
                    "normal-code": ["bad_codex"],
                    "complex-code": ["bad_codex"],
                    "custom": ["bad_codex"],
                },
                "default_ladder": ["bad_codex"],
                "allowed_routes": ["bad_codex"],
            },
            "review_model_policy": {
                "default_tier": "standard",
                "routes": {
                    "light": ["bad_codex"],
                    "standard": ["bad_codex"],
                    "heavy": ["bad_codex"],
                },
            },
            "amender_model_policy": {
                "default_ladder": ["bad_codex"],
                "allowed_routes": ["bad_codex"],
            },
            "lite_model_policy": {
                "default_ladder": ["bad_codex"],
                "allowed_routes": ["bad_codex"],
                "model_map": {"bad_codex": "gpt-missing-fixture"},
            },
        },
        "model_ladders": {"worker": ["bad_codex"]},
    }
    config_path = tmp_path / "goal.config.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    cgcf.write_fake_codex_catalog(fake_codex_bin, ["gpt-5.4"])
    monkeypatch.setenv("PATH", f"{fake_codex_bin}:{os.environ['PATH']}")

    output = tmp_path / "goal-config-check.json"
    state_output = tmp_path / "goal-config-state.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--require-models",
            "--output",
            str(output),
            "--state-output",
            str(state_output),
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))
    state = json.loads(state_output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("configured Codex model absent from catalog" in failure for failure in report["failures"])
    assert state["phase"] == "blocked"
    assert state["complete"] is False

    config["models"]["bad_codex"]["model"] = "gpt-5.4"
    config["model_policies"]["lite_model_policy"]["model_map"]["bad_codex"] = "gpt-5.4"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_present = tmp_path / "goal-config-check-present.json"
    state_present = tmp_path / "goal-config-state-present.json"
    args_present = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--require-models",
            "--output",
            str(output_present),
            "--state-output",
            str(state_present),
        ]
    )
    ctx_present = cgc.build_check_context(args_present)
    exit_code_present = cgc.run_standard_mode(args_present, ctx_present, preflight_remediation=None)
    report_present = json.loads(output_present.read_text(encoding="utf-8"))
    state_present_payload = json.loads(state_present.read_text(encoding="utf-8"))

    assert exit_code_present == 0
    assert report_present["status"] == "pass"
    assert state_present_payload["phase"] == "validated"
    assert state_present_payload["complete"] is True


def test_standard_mode_rejected_route_does_not_claim_full_route_verification(tmp_path):
    config_path = _opencode_deepseek_policy_regression_config(tmp_path)
    config = cgc.load_json(config_path)
    config["models"]["lite_agent"]["provider"] = "openai"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output = tmp_path / "goal-config-check.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--output",
            str(output),
            "--stdout",
            "none",
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["failures"] == []
    assert report["accepted_routes"]
    assert report["rejected_routes"]
    assert report["summary"]["route_verification_status"] != "routes_verified"
    assert report["summary"]["route_model_availability_verified"] is not True
    assert report["route_verification_status"] != "routes_verified"
    assert report["route_model_availability_verified"] is not True


def test_check_goal_config_smoke_fails_closed_on_invalid_configured_bridge_provider(tmp_path):
    config_path = tmp_path / "goal.config.invalid-bridge-smoke.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "smoke"},
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "models": {
                    "demanding_agent": {
                        "alias": "ds-pro-max",
                        "harness": "opencode-bridge",
                        "provider": "openai",
                        "model": "deepseek/deepseek-v4-pro",
                    }
                },
                "harnesses": {
                    "opencode-bridge": {
                        "kind": cgc.BRIDGE_HARNESS_KIND,
                        "command": "python3",
                        "smoke_args": ["-c", "print('TOKEN')"],
                    }
                },
                "harness_smokes": {
                    "demanding_agent": {
                        "prompt": "TOKEN",
                        "expect": "TOKEN",
                        "timeout_seconds": 5,
                    }
                },
                "model_policies": {
                    "worker_model_policy": {
                        "route_classes": {
                            "mechanical": ["demanding_agent"],
                            "docs": ["demanding_agent"],
                            "small-edit": ["demanding_agent"],
                            "normal-code": ["demanding_agent"],
                            "complex-code": ["demanding_agent"],
                            "custom": ["demanding_agent"],
                        },
                        "default_ladder": ["demanding_agent"],
                        "allowed_routes": ["demanding_agent"],
                    },
                    "review_model_policy": {
                        "default_tier": "standard",
                        "routes": {
                            "light": ["demanding_agent"],
                            "standard": ["demanding_agent"],
                            "heavy": ["demanding_agent"],
                        },
                    },
                    "amender_model_policy": {
                        "default_ladder": ["demanding_agent"],
                        "allowed_routes": ["demanding_agent"],
                    },
                    "lite_model_policy": {
                        "default_ladder": ["demanding_agent"],
                        "allowed_routes": ["demanding_agent"],
                        "model_map": {"demanding_agent": "deepseek/deepseek-v4-pro"},
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "goal-config-smoke.json"

    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--smoke",
            "--output",
            str(output),
            "--stdout",
            "none",
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_standard_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("opencode-bridge provider" in failure for failure in report["failures"])
    assert report["rejected_routes"]
    assert "model_check=failed" in report["rejected_routes"][0]["reasons"]


def test_collect_role_reports_records_smoke_missing_when_required(tmp_path):
    config_path = tmp_path / "goal.config.smoke-missing.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "validation": {"mode": "smoke"},
                "models": {
                    "worker": {
                        "harness": "codex_worker",
                        "provider": "openai",
                        "model": "gpt-5.5",
                    }
                },
                "harnesses": {"codex_worker": {"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    args = cgc.build_parser().parse_args(["--config", str(config_path), "--smoke"])
    ctx = cgc.build_check_context(args)
    roles, reports = cgc.collect_role_reports(args, ctx)

    assert roles == []
    assert reports == []
    assert any("model_policies must be an object" in failure for failure in ctx.failures)


def test_profile_discovery_candidates_respects_max_candidates():
    candidates = cgc.profile_discovery_candidates("mixed-fast", model_filter=None, max_candidates=2)
    assert len(candidates) == 2
    assert candidates[0] != candidates[1]


def test_discover_mode_prefers_early_accept_and_tracks_unvisited_routes(tmp_path):
    config_path = tmp_path / "goal.config.discover.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "validation": {"mode": "model-check"},
                "usage_units": {
                    "token_counts": ["input"],
                    "text_counts": ["prompt_chars"],
                    "time_counts": ["elapsed_ms"],
                },
                "models": {
                    "worker": {"harness": "codex", "provider": "openai", "model": "gpt-5.5"},
                },
                "harnesses": {
                    "codex": {"kind": "codex", "command": "python3", "smoke_args": ["{prompt}"]},
                    "opencode-bridge": {
                        "kind": cgc.BRIDGE_HARNESS_KIND,
                        "command": "python3",
                        "smoke_args": ["{prompt}"],
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "goal-config-discover.json"
    state_output = tmp_path / "goal-config-discover-state.json"
    args = cgc.build_parser().parse_args(
        [
            "--config",
            str(config_path),
            "--discover-profile",
            "mixed-fast",
            "--output",
            str(output),
            "--state-output",
            str(state_output),
        ]
    )
    ctx = cgc.build_check_context(args)
    exit_code = cgc.run_discover_mode(args, ctx, preflight_remediation=None)
    report = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert report["status"] == "failed"
    assert any("model_policies must be an object" in failure for failure in report["failures"])
    assert len(report["candidate_routes"]) == 6
    assert report["unvisited_routes"]
    assert report["unvisited_routes"][0]["reason"] == "early_accept_count reached (4)"


def test_scan_configurables_build_inventory_contains_known_sections():
    inventory = scanc.build_inventory(scanc.load_contract())
    assert inventory["schema_version"] == 1
    assert set(inventory["categories"]).issuperset(
        {"aggressiveness", "timeouts", "worker_routes", "harnesses", "telemetry"}
    )


def test_scan_configurables_main_json_modes(monkeypatch, capsys):
    monkeypatch.setattr(scanc.sys, "argv", ["scan_configurables.py", "--questions-json"])
    assert scanc.main() == 0
    questions_output = capsys.readouterr().out
    questions_payload = json.loads(questions_output)
    assert questions_payload["schema_version"] == 1
    assert questions_payload["status"] == "pass"

    monkeypatch.setattr(scanc.sys, "argv", ["scan_configurables.py", "--json"])
    assert scanc.main() == 0
    inventory_output = capsys.readouterr().out
    inventory_payload = json.loads(inventory_output)
    assert inventory_payload["schema_version"] == 1
