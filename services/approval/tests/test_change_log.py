import jsonschema
import pytest

from approval import ChangeLog

RECORD = {
    "task_id": "task-1",
    "approval_id": "appr-1",
    "outcome": "applied",
    "recorded_at": "2026-09-13T01:00:00Z",
}


def test_record_then_all_round_trips(tmp_path):
    log = ChangeLog(tmp_path / "changes.jsonl")
    log.record(RECORD)
    assert log.all() == [RECORD]


def test_record_rejects_invalid_outcome(tmp_path):
    log = ChangeLog(tmp_path / "changes.jsonl")
    with pytest.raises(jsonschema.ValidationError):
        log.record(dict(RECORD, outcome="sort-of"))


def test_multiple_records_append_in_order(tmp_path):
    log = ChangeLog(tmp_path / "changes.jsonl")
    log.record(RECORD)
    log.record(dict(RECORD, task_id="task-2"))
    assert [r["task_id"] for r in log.all()] == ["task-1", "task-2"]
