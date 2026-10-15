from __future__ import annotations

from support import fault, ok

from hermes_plus.store import ApprovalJournal


def _seed(ctx, request_id: str = "req_1", session_id: str = "sess_1") -> None:
    ApprovalJournal(ctx.store).record_request(
        request_id=request_id, session_id=session_id, tool_call_id="call_1",
        tool_name="shell", command="ls", description="list files")


async def test_list_requires_a_session(call):
    assert fault(await call("fork.approvals.list", {}))["code"] == 4006


async def test_list_returns_the_session_journal(ctx, call):
    _seed(ctx)
    _seed(ctx, "req_2", "sess_2")
    approvals = ok(await call("fork.approvals.list", {"session_id": "sess_1"}))["approvals"]
    assert [row["request_id"] for row in approvals] == ["req_1"]


async def test_decide_validates_its_inputs(call):
    assert fault(await call("fork.approvals.decide", {"session_id": "sess_1", "request_id": "req_1",
                                                      "choice": "maybe"}))["code"] == 4006
    assert fault(await call("fork.approvals.decide", {"session_id": "sess_1", "request_id": "req_1",
                                                      "choice": ["once"]}))["code"] == 4006


async def test_decide_unknown_or_foreign_request_is_not_found(ctx, call):
    _seed(ctx)
    assert fault(await call("fork.approvals.decide", {"session_id": "sess_1", "request_id": "req_none",
                                                      "choice": "once"}))["code"] == 4004
    assert fault(await call("fork.approvals.decide", {"session_id": "sess_other", "request_id": "req_1",
                                                      "choice": "once"}))["code"] == 4004


async def test_decide_without_a_gateway_journals_and_reports_not_resolved(ctx, call):
    assert ctx.upstream is None
    _seed(ctx)
    reply = ok(await call("fork.approvals.decide", {"session_id": "sess_1", "request_id": "req_1",
                                                    "choice": "session"}))
    assert reply["resolved"] is False
    assert reply["approval"]["status"] == "allowed"
