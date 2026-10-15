"""JSON-RPC protocol codes, fork domain codes, and the fault type handlers raise."""

from __future__ import annotations

from typing import Any

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL = -32603
FEATURE_UNAVAILABLE = -32004

APPROVAL_PARAMS = 4006
APPROVAL_NOT_FOUND = 4004
PROFILE_REQUIRED = 4063
IDENTITY_CHANGED = 4091
EMPTY_DISPLAY_NAME = 4092
MEMORY_ITEM_CHANGED = 4093
MEMORY_REJECTED = 4094
MAILBOX_SEND_PARAMS = 4100
MAILBOX_NO_TARGET = 4101
MAILBOX_UPDATE_PARAMS = 4102
MAILBOX_NOTE_NOT_FOUND = 4103
MAILBOX_ILLEGAL_TRANSITION = 4104
TEAM_REFUSED = 4110
TEAM_NOT_FOUND = 4111
MAILBOX_LIST_FAILED = 5100
MAILBOX_SEND_FAILED = 5102
MAILBOX_UPDATE_FAILED = 5103
TEAM_FAILED = 5110


class RpcFault(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error
