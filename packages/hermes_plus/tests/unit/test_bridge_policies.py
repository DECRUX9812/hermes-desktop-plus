"""The fork's retry-loop policy as a plugin hook: real detector calls, real recorder, no mocks."""

from __future__ import annotations

from typing import Any

from hermes_plus.bridge.policies import (
    DEFAULT_MAX_IDENTICAL,
    RetryLoopDetector,
    register_policy_hooks,
)

_CALL = {"tool_name": "terminal", "args": {"command": "ls -la"}, "session_id": "sess-1", "turn_id": "turn-1"}


def _repeat(detector: RetryLoopDetector, times: int, **overrides: Any) -> list[str | None]:
    call = {**_CALL, **overrides}
    return [detector.observe(**call) for _ in range(times)]


def test_the_sixth_repeat_runs_and_the_seventh_is_blocked() -> None:
    detector = RetryLoopDetector()
    assert _repeat(detector, DEFAULT_MAX_IDENTICAL) == [None] * DEFAULT_MAX_IDENTICAL
    verdict = _repeat(detector, 1)[0]
    assert verdict is not None
    assert "retry loop" in verdict and "terminal" in verdict


def test_different_arguments_are_different_calls() -> None:
    detector = RetryLoopDetector()
    for index in range(DEFAULT_MAX_IDENTICAL * 3):
        assert detector.observe(tool_name="terminal", args={"command": f"echo {index}"},
                                session_id="sess-1", turn_id="turn-1") is None


def test_an_alternating_loop_is_caught_too() -> None:
    detector = RetryLoopDetector()
    blocked = []
    for index in range(2 * DEFAULT_MAX_IDENTICAL + 1):
        args = {"command": "ls"} if index % 2 == 0 else {"command": "pwd"}
        blocked.append(detector.observe(tool_name="terminal", args=args,
                                        session_id="sess-1", turn_id="turn-1"))
    assert any(verdict is not None for verdict in blocked)


def test_each_turn_gets_its_own_window() -> None:
    detector = RetryLoopDetector()
    assert _repeat(detector, DEFAULT_MAX_IDENTICAL) == [None] * DEFAULT_MAX_IDENTICAL
    assert _repeat(detector, DEFAULT_MAX_IDENTICAL, turn_id="turn-2") == [None] * DEFAULT_MAX_IDENTICAL


def test_without_a_turn_identity_the_detector_fails_open() -> None:
    detector = RetryLoopDetector()
    assert _repeat(detector, DEFAULT_MAX_IDENTICAL * 3, turn_id="") == [None] * (DEFAULT_MAX_IDENTICAL * 3)


def test_the_signature_ignores_key_order_and_uses_string_args_verbatim() -> None:
    detector = RetryLoopDetector(limit=2)
    assert detector.observe(tool_name="write_file", args={"path": "a.txt", "content": "x"},
                            session_id="s", turn_id="t") is None
    assert detector.observe(tool_name="write_file", args={"content": "x", "path": "a.txt"},
                            session_id="s", turn_id="t") is None
    assert detector.observe(tool_name="write_file", args={"path": "a.txt", "content": "x"},
                            session_id="s", turn_id="t") is not None
    # The fork hashes an argument string as it stands (assistant tool calls arrive as JSON text).
    strings = RetryLoopDetector(limit=2)
    verdicts = [strings.observe(tool_name="write_file", args='{"path": "a.txt"}',
                                session_id="s", turn_id="t") for _ in range(3)]
    assert verdicts == [None, None, verdicts[2]]
    assert verdicts[2] is not None


class _Ctx:
    """The plugin settings surface the detector reads (ctx.get_config in Hermes)."""

    def __init__(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = settings or {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)


def _register(ctx: _Ctx) -> dict[str, Any]:
    hooks: dict[str, Any] = {}
    features = register_policy_hooks(ctx, lambda name, callback: hooks.__setitem__(name, callback))
    assert features == ["hook.pre_tool_call"]
    return hooks


def test_register_policy_hooks_wires_a_blocking_pre_tool_call() -> None:
    hooks = _register(_Ctx())
    callback = hooks["pre_tool_call"]
    for _ in range(DEFAULT_MAX_IDENTICAL):
        assert callback(**_CALL) is None
    verdict = callback(**_CALL)
    assert verdict is not None and verdict["action"] == "block"
    assert callback(**{**_CALL, "turn_id": "turn-2"}) is None


def test_register_policy_hooks_honours_a_custom_limit() -> None:
    hooks = _register(_Ctx({"retry_loop_max_identical": 2}))
    callback = hooks["pre_tool_call"]
    assert callback(**_CALL) is None
    assert callback(**_CALL) is None
    assert callback(**_CALL) is not None


def test_register_policy_hooks_can_be_disabled() -> None:
    assert register_policy_hooks(_Ctx({"retry_loop_detector": False}), lambda *_: None) == []
    assert register_policy_hooks(_Ctx({"retry_loop_max_identical": 0}), lambda *_: None) == []
