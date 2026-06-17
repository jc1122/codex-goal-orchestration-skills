"""close_from_artifacts must emit dependency_failed evidence for a stuck downstream item.

A normal "upstream failed -> downstream can never launch" closeout previously left the downstream
item with no structured event, so `scheduler_tick --close-from-artifacts --validate-final` reported
the run as failed with un-self-repairable validator defects.
"""

import sys

from conftest import REPO, load_module

sys.path.insert(0, str(REPO / "skills" / "_goal_shared" / "scripts"))
st = load_module("skills/_goal_shared/scripts/scheduler_tick.py", "st_dep")


def _build(tmp_path):
    manifest = {"schema_version": 1, "manifest_epoch": "2026-06-17T00:00:00Z"}
    manifest_path = tmp_path / "job.manifest.json"
    manifest_path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
    sha = st.sha256_file(manifest_path)
    spec = {
        "kind": "branch-worker-pool",
        "path": "branches/B01/worker.scheduler.jsonl",
        "capacity": 2,
        "item_ids": ["B01-W01", "B01-W02"],
        "dependencies": {"B01-W02": ["B01-W01"]},
    }
    ledger = {
        "schema_version": 2,
        "scheduler_kind": spec["kind"],
        "scheduler_path": spec["path"],
        "manifest_sha256": sha,
        "capacity": spec["capacity"],
        "item_ids": spec["item_ids"],
        "events": [],
    }
    st.close_from_artifacts(
        ledger,
        spec,
        runtime_ref="r1",
        timestamp_value="2026-06-17T00:00:01Z",
        manifest_sha=sha,
        manifest_epoch_value=st.manifest_epoch(manifest),
        terminal_statuses={"B01-W01": "failed"},
    )
    return ledger, spec, manifest_path


def test_dependency_failed_event_emitted(tmp_path):
    ledger, _spec, _mp = _build(tmp_path)
    dep_failed = [
        e
        for e in ledger["events"]
        if e.get("event") == "blocked" and e.get("id") == "B01-W02" and e.get("reason_code") == "dependency_failed"
    ]
    assert len(dep_failed) == 1


def test_validate_final_passes_for_upstream_failure(tmp_path):
    ledger, spec, manifest_path = _build(tmp_path)
    defects = st.validate_final(tmp_path / "ledger.jsonl", ledger, spec, manifest_path)
    assert defects == []
