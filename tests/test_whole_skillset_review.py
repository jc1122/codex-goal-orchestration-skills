"""Regression tests for the 2026-06-18 whole-skillset cross-cutting review.

Pins the cross-stage / consistency fixes that the per-skill passes could not see:
- amendment-decision sha matcher accepts the archived (pre-amendment) manifest sha, so a
  launched+applied amendment no longer blocks main `pass` (HIGH cross-stage bug);
- the two previously gate-dark runtime runners get pure-validator coverage;
- representative fail-closed readers that were missed by the per-skill sweep.
"""

import json
import sys
import shutil
from pathlib import Path

import pytest
from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-preflight" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "goal-branch-orchestrator" / "scripts"))

ams = load_module("skills/goal-main-orchestrator/scripts/assemble_main_status.py", "ams_ws")
rpa = load_module("skills/goal-main-orchestrator/scripts/runtime_prompt_audit_runner.py", "rpa_ws")
rlr = load_module("skills/_goal_shared/scripts/runtime_lite_runner.py", "rlr_ws")
cprg = load_module("skills/goal-branch-orchestrator/scripts/create_pre_review_gate.py", "cprg_ws")
cgb = load_module("skills/goal-preflight/scripts/create_goal_bundle.py", "cgb_ws")
crel = load_module("scripts/check_release.py", "crel_ws")
sync_goal_shared = load_module("scripts/sync_goal_shared.py", "sync_goal_shared")
cga = load_module("skills/_goal_shared/scripts/check_goal_skill_availability.py", "cga_ws")


# --- 2026-06-18 convergence pass 9: the release gate enforces that the harness-contract gate
#     ships in the package and is wired into npm run check (it could silently fall out before) ---
def test_check_release_enforces_harness_gate():
    assert "scripts/check_harness_contract.py" in crel.REQUIRED_PACKAGE_FILES
    assert "scripts/check_harness_contract.py" in crel.REQUIRED_PACKAGE_FILES_ENTRIES


def test_check_release_requires_goal_shared_lite_prompt_file():
    assert "skills/_goal_shared/scripts/lite_prompt.py" in crel.REQUIRED_PACKAGE_FILES


def test_check_release_includes_all_goal_shared_scripts_and_references_from_sync():
    for shared_script in sync_goal_shared.SHARED_SCRIPTS:
        assert f"skills/_goal_shared/scripts/{shared_script}" in crel.REQUIRED_PACKAGE_FILES
    for shared_reference in sync_goal_shared.SHARED_REFERENCES:
        assert f"skills/_goal_shared/references/{shared_reference}" in crel.REQUIRED_PACKAGE_FILES


def test_check_release_includes_all_goal_generated_goal_shared_paths():
    for skill in sync_goal_shared.SKILLS:
        for shared_script in sync_goal_shared.SHARED_SCRIPTS:
            assert f"skills/{skill}/scripts/{shared_script}" in crel.REQUIRED_PACKAGE_FILES
        for shared_reference in sync_goal_shared.SHARED_REFERENCES:
            assert f"skills/{skill}/references/{shared_reference}" in crel.REQUIRED_PACKAGE_FILES


def test_check_release_includes_check_goal_skill_availability_shared_support_files():
    shared_support_files = {f"skills/{path}" for path in cga.REQUIRED_SUPPORT_FILES if path.startswith("_goal_shared/")}
    missing = sorted(shared_support_files - crel.REQUIRED_PACKAGE_FILES)
    assert not missing, f"release check is missing shared support files: {missing}"


def test_check_release_rejects_stale_package_lock_top_level_version(tmp_path, monkeypatch):
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text(
        json.dumps(
            {
                "name": "codex-goal-orchestration-skills",
                "version": "0.2.107",
                "packages": {
                    "": {
                        "name": "codex-goal-orchestration-skills",
                        "version": "0.2.110",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(crel, "PACKAGE_LOCK", lockfile)

    with pytest.raises(SystemExit, match="package-lock.json version"):
        crel.check_package_lock("0.2.110")


def test_check_release_rejects_stale_package_lock_root_package_version(tmp_path, monkeypatch):
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text(
        json.dumps(
            {
                "name": "codex-goal-orchestration-skills",
                "version": "0.2.110",
                "packages": {
                    "": {
                        "name": "codex-goal-orchestration-skills",
                        "version": "0.2.107",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(crel, "PACKAGE_LOCK", lockfile)

    with pytest.raises(SystemExit, match="package-lock.json root package version"):
        crel.check_package_lock("0.2.110")


def test_check_release_runs_installed_goal_checkers_against_target_root(tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    shutil.copytree(REPO / "skills", installed)
    (installed / "_goal_shared" / "scripts" / "lite_prompt.py").unlink()

    fake = tmp_path / "fake_home"
    fake_codex = tmp_path / "fake_codex_home"
    for base in (fake, fake_codex / "skills", fake / ".codex" / "skills", fake / ".agents" / "skills"):
        shutil.copytree(REPO / "skills", base)
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("CODEX_HOME", str(fake_codex))

    with pytest.raises(SystemExit):
        crel._run_installed_goal_checker(
            installed,
            rel_path=Path("_goal_shared/scripts/check_goal_skill_availability.py"),
            scope="installed shared checker",
        )
    with pytest.raises(SystemExit):
        crel._run_installed_goal_checker(
            installed,
            rel_path=Path("goal-config/scripts/check_goal_skill_availability.py"),
            scope="installed local checker",
        )


def test_check_release_goal_shared_checks_run_in_installed_tree(tmp_path):
    installed = tmp_path / "installed"
    shutil.copytree(REPO / "skills", installed)
    shared_payload = crel._run_installed_goal_checker(
        installed,
        rel_path=Path("_goal_shared/scripts/check_goal_skill_availability.py"),
        scope="installed shared checker",
    )
    local_payload = crel._run_installed_goal_checker(
        installed,
        rel_path=Path("goal-config/scripts/check_goal_skill_availability.py"),
        scope="installed local checker",
    )
    assert shared_payload["status"] == "pass"
    assert local_payload["status"] == "pass"


# --- HIGH: amendment decision keyed to the archived (pre-apply) manifest sha is NOT dropped ---
def test_amendment_decision_accepts_archived_manifest_sha(tmp_path):
    bundle = tmp_path
    manifest = bundle / "job.manifest.json"
    manifest.write_text(json.dumps({"job_id": "phaseX", "v": "current"}), encoding="utf-8")
    amendments = bundle / "amendments"
    amendments.mkdir()
    # the archived pre-amendment manifest has a DIFFERENT sha than the current one
    archived = amendments / "A001.job.manifest.before.json"
    archived.write_text(json.dumps({"job_id": "phaseX", "v": "before"}), encoding="utf-8")
    pre_apply_sha = ams.sha256_file(archived)
    assert pre_apply_sha != ams.sha256_file(manifest)
    (amendments / "A001.decision.json").write_text(
        json.dumps(
            {
                "amendment_id": "A001",
                "decision": "skip",
                "manifest": manifest.as_posix(),
                "manifest_sha256": pre_apply_sha,  # recorded at decision time, before apply
                "terminal_branch_ids": ["B01"],
                "terminal_branch_statuses": {"B01": "pass"},
            }
        ),
        encoding="utf-8",
    )
    branch_statuses = [{"branch_id": "B01", "status": "pass"}]
    blockers: list[str] = []
    records, covered, ignored = ams.current_amendment_records(manifest, branch_statuses, {"active_ids": []}, blockers)
    assert any(r["amendment_id"] == "A001" for r in records), (records, ignored, blockers)
    assert "B01" in covered
    assert not any("ignored stale amendment decision" in b for b in blockers), blockers


# --- runtime_prompt_audit_runner: pure audit validator now covered (was gate-dark) ---
def _valid_audit():
    return {
        "manifest": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "status": "pass",
        "can_start": True,
        "checked_files": ["job.manifest.json"],
        "missing_dod_items": [],
        "commands_run": ["python3 deterministic_prompt_audit.py"],
        "defects": [],
        "actionability_verdict": "pass",
        "summary": "audit ok",
    }


def test_valid_audit_data():
    good = _valid_audit()
    assert rpa.valid_audit_data(good, manifest="/abs/job.manifest.json", repo_root="/abs/repo") is True
    # identity mismatch rejected
    assert rpa.valid_audit_data(good, manifest="/other", repo_root="/abs/repo") is False
    # missing required field rejected
    missing = {k: v for k, v in good.items() if k != "actionability_verdict"}
    assert rpa.valid_audit_data(missing, manifest="/abs/job.manifest.json", repo_root="/abs/repo") is False
    assert rpa.valid_defects([]) is True
    assert rpa.valid_defects("nope") is False


def test_runtime_prompt_audit_validation_helpers():
    bad = sys.argv[0]
    with pytest.raises(SystemExit):
        rpa.read_json(bad)  # path points to this file, not JSON
    malformed = REPO / "tests" / "does-not-exist.json"
    with pytest.raises(SystemExit):
        rpa.read_json(malformed)

    cfg = {
        "manifest_path": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "commands_run": "not-a-list",
        "attempts": [1],
        "model": "gpt-5",
    }
    with pytest.raises(SystemExit):
        rpa.string_value(cfg, "missing")
    with pytest.raises(SystemExit):
        rpa.int_value({"attempt_timeout_seconds": True}, "attempt_timeout_seconds")
    with pytest.raises(SystemExit):
        rpa.int_value(cfg, "attempts")
    with pytest.raises(SystemExit):
        rpa.list_value(cfg, "commands_run")
    with pytest.raises(SystemExit):
        rpa.list_value({"attempts": [1]}, "attempts")
    assert rpa.string_value({"manifest_path": "/abs/job.manifest.json"}, "manifest_path") == "/abs/job.manifest.json"
    assert rpa.int_value({"attempt_timeout_seconds": 3}, "attempt_timeout_seconds") == 3
    assert rpa.list_value({"attempts": [{"id": "a"}]}, "attempts")[0]["id"] == "a"


def test_runtime_prompt_audit_packet_paths_and_cleanup(tmp_path):
    packet_dir = tmp_path
    cfg = {
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        "attempts": [
            {"alias": "first", "event_logs": ["events.first.jsonl"]},
            {"alias": "second", "event_logs": ["events.first.jsonl", "events.second.jsonl"]},
            {},
        ],
    }
    (packet_dir / "prompt-audit.json").write_text("old", encoding="utf-8")
    (packet_dir / "telemetry.json").write_text("old", encoding="utf-8")
    (packet_dir / "events.first.jsonl").write_text("old", encoding="utf-8")
    (packet_dir / "events.second.jsonl").write_text("old", encoding="utf-8")

    rpa.clean_outputs(packet_dir, cfg)
    assert not (packet_dir / "prompt-audit.json").exists()
    assert not (packet_dir / "telemetry.json").exists()
    assert not (packet_dir / "events.first.jsonl").exists()
    assert not (packet_dir / "events.second.jsonl").exists()
    assert rpa.first_log_path(packet_dir, cfg["attempts"][0], "fallback.jsonl") == packet_dir / "events.first.jsonl"
    assert rpa.first_log_path(packet_dir, cfg["attempts"][2], "fallback.jsonl") == packet_dir / "fallback.jsonl"


def test_runtime_prompt_audit_debug_events(tmp_path):
    config = {
        "debug_events_name": "debug-events.jsonl",
        "packet_id": "p1",
        "role": "prompt-auditor",
    }
    rpa.append_debug_event(tmp_path, config, {"phase": "prompt_audit", "event": "start", "status": "running"})
    raw = (tmp_path / "debug-events.jsonl").read_text(encoding="utf-8").strip()
    payload = json.loads(raw)
    assert payload["schema_version"] == 1
    assert payload["phase"] == "prompt_audit"
    assert payload["event"] == "start"
    assert payload["packet_id"] == "p1"

    # no debug sink: helper exits without touching disk
    rpa.append_debug_event(tmp_path, {}, {"phase": "prompt_audit", "event": "ignored"})


def _valid_audit_data_for_recovery(manifest: str, repo_root: str, status: str = "pass") -> dict:
    return {
        "manifest": manifest,
        "repo_root": repo_root,
        "status": status,
        "can_start": True,
        "checked_files": ["job.manifest.json"],
        "defects": [],
        "missing_dod_items": [],
        "actionability_verdict": "pass",
        "commands_run": ["python3 deterministic_prompt_audit.py"],
        "summary": "audit ok",
    }


def test_recover_audit_from_events_reads_embedded_json(tmp_path):
    manifest = "/abs/job.manifest.json"
    repo_root = "/abs/repo"
    config = {"manifest_path": manifest, "repo_root": repo_root}
    log_path = tmp_path / "events.jsonl"
    event = {"item": {"text": json.dumps(_valid_audit_data_for_recovery(manifest, repo_root, "pass"))}}
    log_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    output_path = tmp_path / "prompt-audit.json"
    assert rpa.recover_audit_from_events(log_path, output_path, config) is True
    recovered = json.loads(output_path.read_text(encoding="utf-8"))
    assert recovered["status"] == "pass"
    assert rpa.recover_audit_from_events(tmp_path / "missing.jsonl", output_path, config) is False


def test_failure_summary_and_write_terminal_audit_fallback_commands(tmp_path):
    config = {
        "manifest_path": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "attempts": [
            {"alias": "attempt.one", "event_logs": ["events.attempt.one.jsonl"]},
            {"alias": "attempt-two", "event_logs": ["events-attempt-two.jsonl"]},
            {"alias": "attempt-three", "event_logs": ["events-attempt-three.jsonl"]},
            {"alias": "attempt-four", "event_logs": ["events-attempt-four.jsonl"]},
            {"alias": "attempt-five"},
        ],
        "commands_run": "bad-commands",
        "output_name": "prompt-audit.json",
    }
    (tmp_path / "events.attempt.one.jsonl").write_text(
        "Prompt audit command unavailable: not found",
        encoding="utf-8",
    )
    (tmp_path / "events-attempt-two.jsonl").write_text(
        "unsupported model: gpt-5.5 is not supported",
        encoding="utf-8",
    )
    (tmp_path / "events-attempt-three.jsonl").write_text("schema file invalid", encoding="utf-8")
    (tmp_path / "events-attempt-four.jsonl").write_text("timed out after 3 seconds", encoding="utf-8")
    summary = rpa.failure_summary(tmp_path, config)
    assert "events.attempt.one.jsonl: command-unavailable" in summary
    assert "events-attempt-two.jsonl: model-unsupported" in summary
    assert "events-attempt-three.jsonl: schema-or-output-invalid" in summary
    assert "events-attempt-four.jsonl: timeout" in summary
    assert "events-attempt-five.jsonl: missing" in summary

    rpa.write_terminal_audit(
        tmp_path,
        {
            "manifest_path": "/abs/job.manifest.json",
            "repo_root": "/abs/repo",
            "output_name": "prompt-audit.json",
            "attempts": [{"command": "codex exec"}],
            "commands_run": [],
        },
        "blocked",
    )
    blocked = json.loads((tmp_path / "prompt-audit.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["commands_run"] == []


def test_run_with_timeout_and_process_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(rpa.shutil, "which", lambda _name: "/usr/bin/timeout")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    log_path = tmp_path / "events.jsonl"

    class FakeProc:
        def wait(self):
            return 9

        def poll(self):
            return None

    popen_calls: list[list[str]] = []

    def _fake_popen(*args, **kwargs):
        popen_calls.append(args[0])  # verify command is prepared with timeout wrapper
        return FakeProc()

    monkeypatch.setattr(rpa.subprocess, "Popen", _fake_popen)
    assert (
        rpa.run_with_timeout(
            command=["echo", "audit"],
            timeout_seconds=3,
            kill_after_seconds=1,
            cwd=tmp_path,
            prompt_path=prompt_path,
            log_path=log_path,
        )
        == 9
    )
    assert rpa.ACTIVE_PROCESS is None
    assert popen_calls[0][:2] == ["timeout", "--foreground"]


def test_run_with_timeout_handles_launch_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(rpa.shutil, "which", lambda _name: "/usr/bin/timeout")
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")

    log_path = tmp_path / "missing.log"
    monkeypatch.setattr(
        rpa.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("x"))
    )
    assert (
        rpa.run_with_timeout(
            command=["echo", "audit"],
            timeout_seconds=3,
            kill_after_seconds=1,
            cwd=tmp_path,
            prompt_path=prompt_path,
            log_path=log_path,
        )
        == 127
    )
    assert "prompt-audit command unavailable" in log_path.read_text(encoding="utf-8")

    log_path2 = tmp_path / "error.log"
    monkeypatch.setattr(rpa.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    assert (
        rpa.run_with_timeout(
            command=["echo", "audit"],
            timeout_seconds=3,
            kill_after_seconds=1,
            cwd=tmp_path,
            prompt_path=prompt_path,
            log_path=log_path2,
        )
        == 1
    )
    assert "prompt-audit command failed before launch" in log_path2.read_text(encoding="utf-8")


def test_run_with_timeout_skips_without_system_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(rpa.shutil, "which", lambda _name: None)
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("prompt", encoding="utf-8")
    log_path = tmp_path / "missing-timeout.log"
    assert (
        rpa.run_with_timeout(
            command=["echo", "audit"],
            timeout_seconds=3,
            kill_after_seconds=1,
            cwd=tmp_path,
            prompt_path=prompt_path,
            log_path=log_path,
        )
        == 127
    )
    assert "timeout command not found" in log_path.read_text(encoding="utf-8")


def test_terminal_message_and_candidate_event_texts():
    assert rpa.terminal_message({"terminal_messages": {"git_invalid": "custom"}}, "git_invalid") == "custom"
    assert rpa.terminal_message({}, "invalid_output").startswith("Prompt audit did not produce")
    assert rpa.candidate_event_texts(
        {"item": {"text": " inner "}, "text": " main", "message": "", "content": "\nother"}
    ) == [
        "inner",
        "main",
        "other",
    ]


def test_runtime_prompt_audit_data_edge_paths(tmp_path):
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("[1]", encoding="utf-8")
    with pytest.raises(SystemExit):
        rpa.read_json(bad_file)
    assert rpa.valid_defects([{"file": "x", "severity": "critical", "message": "msg"}]) is True
    assert rpa.valid_defects([{"file": 1, "severity": "critical", "message": "msg"}]) is False
    pass_with_major = _valid_audit_data_for_recovery("/abs/job.manifest.json", "/abs/repo", "pass")
    pass_with_major["defects"] = [{"file": "a", "severity": "major", "message": "oops"}]
    assert rpa.valid_audit_data(pass_with_major, manifest="/abs/job.manifest.json", repo_root="/abs/repo") is False

    malformed = tmp_path / "bad-audit.json"
    malformed.write_text("not-json", encoding="utf-8")
    assert (
        rpa.valid_audit_file(
            malformed,
            {
                "manifest_path": "/abs/job.manifest.json",
                "repo_root": "/abs/repo",
            },
        )
        is False
    )

    output = tmp_path / "prompt-audit.json"
    bad_log = tmp_path / "events.jsonl"
    bad_log.write_text("not-json\n{}", encoding="utf-8")
    assert (
        rpa.recover_audit_from_events(
            bad_log, output, {"manifest_path": "/abs/job.manifest.json", "repo_root": "/abs/repo"}
        )
        is False
    )


def test_repo_is_valid_and_termination_branches(monkeypatch):
    class Dummy:
        returncode = 0

    assert rpa.repo_is_valid(Path("/tmp")) is False

    class FakeCompleted:
        returncode = 0

    monkeypatch.setattr(rpa.subprocess, "run", lambda *_args, **_kwargs: FakeCompleted())
    assert rpa.repo_is_valid(Path("/tmp")) is True

    class ActiveDone:
        def __init__(self, raise_on_kill=False):
            self.pid = 123
            self.raise_on_kill = raise_on_kill
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            if self.raise_on_kill:
                raise OSError("boom")
            self.terminated = True

    import os

    mp = pytest.MonkeyPatch()
    proc = ActiveDone(raise_on_kill=True)
    rpa.ACTIVE_PROCESS = proc
    mp.setattr(os, "killpg", lambda *_args, **_kwargs: None)
    rpa.terminate_active_process()  # takes terminate branch
    assert proc.terminated is False
    mp.undo()

    proc2 = ActiveDone()
    rpa.ACTIVE_PROCESS = proc2
    assert rpa.terminate_active_process() is None
    assert proc2.poll() is None

    proc3 = ActiveDone()
    proc3.poll = lambda: 1
    rpa.ACTIVE_PROCESS = proc3
    assert rpa.terminate_active_process() is None


def test_main_error_path_for_exception(tmp_path, monkeypatch):
    config = {
        "manifest_path": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "schema_version": 1,
        "role": "prompt-auditor",
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        "prompt_name": "prompt.md",
        "schema_name": "schema.json",
        "attempt_timeout_seconds": 1,
        "timeout_kill_after_seconds": 1,
        "attempts": [{"model": "gpt-5"}],
    }
    (tmp_path / "launch-config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(rpa, "read_json", lambda path: config)
    events: list[dict[str, object]] = []
    monkeypatch.setattr(rpa, "append_debug_event", lambda _pd, _cfg, event: events.append(event))
    monkeypatch.setattr(rpa, "run_packet", lambda _dir: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sys, "argv", ["runtime_prompt_audit_runner.py", "--packet-dir", str(tmp_path)])
    with pytest.raises(RuntimeError):
        rpa.main()
    assert any(event.get("status") == "error" for event in events)


def test_write_telemetry_and_main_entrypoint(tmp_path, monkeypatch):
    config = {
        "telemetry_script": "echo.py",
        "packet_id": "p1",
        "output_name": "prompt-audit.json",
        "prompt_name": "prompt.md",
        "telemetry_name": "telemetry.json",
        "repo_root": "/abs/repo",
        "attempts": [{"model": "gpt-5"}],
    }
    calls: list[list[str]] = []

    class FakeCompleted:
        returncode = 0

    def _fake_run(args, check=False):
        calls.append(args)
        return FakeCompleted()

    events: list[dict[str, object]] = []

    monkeypatch.setattr(rpa.subprocess, "run", _fake_run)
    monkeypatch.setattr(rpa, "append_debug_event", lambda _pd, _cfg, event: events.append(event))
    rpa.write_telemetry(tmp_path, config)
    assert calls and calls[0][0] == "python3"
    assert calls[0][:2] == ["python3", "echo.py"]
    assert calls[0][:-3] and events and events[0]["event"] == "written"

    # main() should route run_packet result into end debug event
    launched = {
        "manifest_path": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "schema_version": 1,
        "role": "prompt-auditor",
    }
    (tmp_path / "launch-config.json").write_text(json.dumps(launched), encoding="utf-8")
    monkeypatch.setattr(rpa, "run_packet", lambda _dir: 2)
    monkeypatch.setattr(rpa, "read_json", lambda path: launched)
    events.clear()
    monkeypatch.setattr(sys, "argv", ["runtime_prompt_audit_runner.py", "--packet-dir", str(tmp_path)])
    assert rpa.main() == 2
    assert any(event.get("status") == "nonzero" for event in events)


def test_write_telemetry_with_debug_name(tmp_path, monkeypatch):
    config = {
        "telemetry_script": "telemetry.py",
        "packet_id": "p2",
        "output_name": "prompt-audit.json",
        "prompt_name": "prompt.md",
        "telemetry_name": "telemetry.json",
        "telemetry_debug_name": "telemetry-debug.jsonl",
        "repo_root": "/abs/repo",
        "attempts": [{"model": "gpt-5"}],
    }

    class FakeCompleted:
        returncode = 0

    commands: list[list[str]] = []

    def _fake_run(args, check=False):
        commands.append(args)
        return FakeCompleted()

    monkeypatch.setattr(rpa.subprocess, "run", _fake_run)
    rpa.write_telemetry(tmp_path, config)
    assert commands and "--debug" in commands[0]
    assert "--debug-output" in commands[0]


def test_run_packet_runs_and_recovers_failures(tmp_path, monkeypatch):
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    manifest = packet_dir / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    prompt = packet_dir / "main.prompt.md"
    prompt.write_text("x", encoding="utf-8")
    schema = packet_dir / "prompt-audit.schema.json"
    schema.write_text("{}", encoding="utf-8")
    output = packet_dir / "prompt-audit.json"

    base = {
        "schema_version": 1,
        "role": "prompt-auditor",
        "manifest_path": manifest.as_posix(),
        "repo_root": str(packet_dir),
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        "prompt_name": "main.prompt.md",
        "schema_name": "prompt-audit.schema.json",
        "attempt_timeout_seconds": 1,
        "timeout_kill_after_seconds": 1,
        "attempts": [{"model": "gpt-5"}],
    }
    (packet_dir / "launch-config.json").write_text(json.dumps(base), encoding="utf-8")

    monkeypatch.setattr(rpa, "repo_is_valid", lambda _path: True)
    monkeypatch.setattr(rpa, "write_telemetry", lambda *_args, **_kwargs: None)

    def _run_with_timeout(output_status: str, **_kwargs):
        output.write_text(
            json.dumps(_valid_audit_data_for_recovery(manifest.as_posix(), str(packet_dir), output_status)),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(rpa, "run_with_timeout", lambda **kwargs: _run_with_timeout("pass", **kwargs))
    assert rpa.run_packet(packet_dir) == 0

    monkeypatch.setattr(rpa, "run_with_timeout", lambda **kwargs: _run_with_timeout("failed", **kwargs))
    assert rpa.run_packet(packet_dir) == 1

    def _run_with_timeout_for_recovery(**kwargs):
        # don't create output; write recoverable audit to log for fallback path
        kwargs["log_path"].write_text(
            json.dumps(_valid_audit_data_for_recovery(manifest.as_posix(), str(packet_dir))), encoding="utf-8"
        )
        return 0

    log = packet_dir / "events-attempt-1.jsonl"
    log.write_text(json.dumps(_valid_audit_data_for_recovery(manifest.as_posix(), str(packet_dir))), encoding="utf-8")
    if output.exists():
        output.unlink()
    monkeypatch.setattr(rpa, "run_with_timeout", _run_with_timeout_for_recovery)
    assert rpa.run_packet(packet_dir) == 0


def test_termination_and_interrupt_paths():
    class DummyProcess:
        def __init__(self):
            self.pid = 123
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    process = DummyProcess()
    rpa.ACTIVE_PROCESS = process

    import os

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(os, "killpg", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))
    rpa.terminate_active_process()
    assert process.terminated
    monkeypatch.undo()

    packet_dir = Path("/tmp")  # harmless; not mutated by assertion paths
    config = {
        "manifest_path": "/abs/job.manifest.json",
        "repo_root": "/abs/repo",
        "attempts": [{"command": "codex"}],
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
    }
    events: list[str] = []

    def _fake_write_terminal_audit(_packet_dir, _config, message):
        events.append(message)

    def _fake_write_telemetry(_packet_dir, _config):
        events.append("telemetry")

    rpa.ACTIVE_PACKET_DIR = packet_dir
    rpa.ACTIVE_CONFIG = config
    old_term = rpa.write_terminal_audit
    old_tele = rpa.write_telemetry
    old_term_fn = rpa.terminate_active_process
    try:
        rpa.write_terminal_audit = _fake_write_terminal_audit
        rpa.write_telemetry = _fake_write_telemetry
        rpa.terminate_active_process = lambda: None
        with pytest.raises(SystemExit) as exc:
            rpa.handle_interrupt(2, None)
        assert exc.value.code == 130
    finally:
        rpa.write_terminal_audit = old_term
        rpa.write_telemetry = old_tele
        rpa.terminate_active_process = old_term_fn


def test_run_packet_rejects_invalid_contract_and_reaches_missing_file_paths(tmp_path, monkeypatch):
    packet_dir = tmp_path / "packet-contract"
    packet_dir.mkdir()
    manifest = packet_dir / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    (packet_dir / "main.prompt.md").write_text("x", encoding="utf-8")
    (packet_dir / "prompt-audit.schema.json").write_text("{}", encoding="utf-8")

    def write_config(cfg):
        (packet_dir / "launch-config.json").write_text(json.dumps(cfg), encoding="utf-8")

    config = {
        "schema_version": 1,
        "role": "prompt-auditor",
        "manifest_path": manifest.as_posix(),
        "repo_root": str(packet_dir),
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        "prompt_name": "main.prompt.md",
        "schema_name": "prompt-audit.schema.json",
        "attempt_timeout_seconds": 1,
        "timeout_kill_after_seconds": 1,
        "attempts": [{"model": "gpt-5"}],
    }
    bad_schema = dict(config)
    bad_schema["schema_version"] = 2
    write_config(bad_schema)
    with pytest.raises(SystemExit):
        rpa.run_packet(packet_dir)

    bad_role = dict(config)
    bad_role["schema_version"] = 1
    bad_role["role"] = "lite_advisor"
    write_config(bad_role)
    with pytest.raises(SystemExit):
        rpa.run_packet(packet_dir)

    git_ok = dict(config)
    write_config(git_ok)
    monkeypatch.setattr(rpa, "repo_is_valid", lambda _path: False)
    monkeypatch.setattr(rpa, "write_telemetry", lambda *_args, **_kwargs: None)
    assert rpa.run_packet(packet_dir) == 1

    git_ok["prompt_name"] = "missing.prompt.md"
    write_config(git_ok)
    assert rpa.run_packet(packet_dir) == 1


def test_run_packet_final_fallback_blocks_on_invalid_output(tmp_path, monkeypatch):
    packet_dir = tmp_path / "packet-fallback"
    packet_dir.mkdir()
    manifest = packet_dir / "job.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    (packet_dir / "main.prompt.md").write_text("x", encoding="utf-8")
    (packet_dir / "prompt-audit.schema.json").write_text("{}", encoding="utf-8")
    config = {
        "schema_version": 1,
        "role": "prompt-auditor",
        "manifest_path": manifest.as_posix(),
        "repo_root": str(packet_dir),
        "output_name": "prompt-audit.json",
        "telemetry_name": "telemetry.json",
        "prompt_name": "main.prompt.md",
        "schema_name": "prompt-audit.schema.json",
        "attempt_timeout_seconds": 1,
        "timeout_kill_after_seconds": 1,
        "attempts": [{"model": "gpt-5"}],
    }
    (packet_dir / "launch-config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(rpa, "repo_is_valid", lambda _path: True)
    monkeypatch.setattr(rpa, "run_with_timeout", lambda **_kwargs: 0)
    monkeypatch.setattr(rpa, "write_telemetry", lambda *_args, **_kwargs: None)

    result = rpa.run_packet(packet_dir)
    assert result == 1
    blocked = json.loads((packet_dir / "prompt-audit.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"


# --- runtime_lite_runner: importable + bridge-artifact mapper degrades on an empty run dir ---
def test_lite_runner_map_bridge_artifacts_no_crash(tmp_path):
    result = rlr.map_bridge_artifacts(tmp_path)  # no artifacts present
    assert isinstance(result, dict)


# --- representative fail-closed readers now fail closed (SystemExit) on malformed JSON ---
def test_readers_fail_closed_on_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        cprg.read_json(bad)
    with pytest.raises(SystemExit):
        cgb.load_goal_config(bad)
