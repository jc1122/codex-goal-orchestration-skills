"""A worker packet that resolves to no owned paths must be rejected at creation."""

import pytest

from conftest import load_module

crp = load_module("skills/goal-branch-orchestrator/scripts/create_runtime_packet.py", "crp_owned")


def _call(monkeypatch, tmp_path, work_item, owned_files):
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(crp, "find_manifest_context", lambda *a, **k: (manifest_path, {}, {"id": "B01"}, work_item))
    return crp.compact_worker_context(
        branch_id="B01",
        packet_id="B01-W01",
        task_file=None,
        task_text="# Task\n## Objective\nx\n## Scope\ny\n## Stop Conditions\nz",
        owned_files=owned_files,
        context_files=[str(manifest_path)],
    )


def test_worker_no_owned_paths_rejected(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _call(monkeypatch, tmp_path, work_item={}, owned_files=[])
    assert "owned" in str(exc.value).lower()


def test_worker_with_owned_paths_ok(monkeypatch, tmp_path):
    result = _call(monkeypatch, tmp_path, work_item={"owned_paths": ["src/x.py"]}, owned_files=[])
    assert result is not None
