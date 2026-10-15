from __future__ import annotations

from hermes_plus.store import ApprovalJournal


def _request(journal: ApprovalJournal, request_id: str = "req_1", session_id: str = "sess_1") -> None:
    journal.record_request(request_id=request_id, session_id=session_id, tool_call_id="call_1",
                           tool_name="shell", command="ls", description="list files")


def test_pending_request_becomes_allowed_on_grant(store):
    journal = ApprovalJournal(store)
    _request(journal)
    assert journal.get("req_1")["status"] == "pending"
    assert journal.decide("req_1", "session")["status"] == "allowed"


def test_first_decision_sticks(store):
    journal = ApprovalJournal(store)
    _request(journal)
    journal.decide("req_1", "deny")
    assert journal.decide("req_1", "always")["status"] == "denied"


def test_decide_unknown_request_returns_none(store):
    assert ApprovalJournal(store).decide("req_missing", "once") is None


def test_list_is_scoped_to_one_session(store):
    journal = ApprovalJournal(store)
    _request(journal, "req_1", "sess_1")
    _request(journal, "req_2", "sess_2")
    assert [row["request_id"] for row in journal.list_for_session("sess_1")] == ["req_1"]
