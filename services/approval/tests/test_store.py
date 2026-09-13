import pytest

from approval import ApprovalNotFoundError, ApprovalStore, InvalidTransitionError

REQUEST = {
    "id": "appr-1",
    "task_id": "task-1",
    "summary": "roll back checkout deploy",
    "proposed_actions": [{"instruction": "revert commit abc123"}],
    "status": "pending",
    "created_at": "2026-09-13T00:00:00Z",
}


def test_create_then_get_round_trips(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    store.create(REQUEST)
    assert store.get("appr-1") == REQUEST


def test_create_rejects_non_pending_status(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    with pytest.raises(InvalidTransitionError):
        store.create(dict(REQUEST, status="approved"))


def test_get_missing_raises(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    with pytest.raises(ApprovalNotFoundError):
        store.get("does-not-exist")


def test_decide_approves_and_updates_status(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    store.create(REQUEST)
    updated = store.decide("appr-1", status="approved", decided_by="emma", decided_at="2026-09-13T01:00:00Z")
    assert updated["status"] == "approved"
    assert updated["decided_by"] == "emma"
    assert store.get("appr-1")["status"] == "approved"


def test_decide_twice_raises(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    store.create(REQUEST)
    store.decide("appr-1", status="approved", decided_by="emma", decided_at="2026-09-13T01:00:00Z")
    with pytest.raises(InvalidTransitionError):
        store.decide("appr-1", status="rejected", decided_by="emma", decided_at="2026-09-13T02:00:00Z")


def test_decide_rejects_invalid_status(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    store.create(REQUEST)
    with pytest.raises(InvalidTransitionError):
        store.decide("appr-1", status="maybe", decided_by="emma", decided_at="2026-09-13T01:00:00Z")


def test_list_pending_excludes_decided(tmp_path):
    store = ApprovalStore(tmp_path / "approvals.jsonl")
    store.create(REQUEST)
    store.create(dict(REQUEST, id="appr-2"))
    store.decide("appr-2", status="rejected", decided_by="emma", decided_at="2026-09-13T01:00:00Z")

    pending = store.list_pending()
    assert [r["id"] for r in pending] == ["appr-1"]


def test_store_persists_across_instances(tmp_path):
    path = tmp_path / "approvals.jsonl"
    ApprovalStore(path).create(REQUEST)
    reloaded = ApprovalStore(path)
    assert reloaded.get("appr-1")["status"] == "pending"


def test_accepts_a_plain_string_path():
    """Regression test: scripts/decide.py passed sys.argv[1] (a str) straight
    through without wrapping it in Path(), and __init__ crashed on
    self._path.parent since str has no .parent. Constructor now normalizes
    str | PathLike itself rather than relying on every caller to remember."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path_str = f"{tmp}/nested/approvals.jsonl"
        store = ApprovalStore(path_str)
        store.create(REQUEST)
        assert store.get("appr-1")["status"] == "pending"
