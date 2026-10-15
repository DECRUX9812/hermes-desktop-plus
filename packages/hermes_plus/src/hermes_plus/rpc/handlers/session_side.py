"""session.ask and session.predict_next: one-shot model answers over a live session's transcript.

Neither call appends to the session, so the live conversation's prompt cache is untouched. The
transcript comes from the stock gateway's session.history, which only knows live sessions.
"""

from __future__ import annotations

import logging
from typing import Any

from hermes_plus.adapter import load_config
from hermes_plus.rpc.context import RpcContext, UpstreamCaller, UpstreamRpcError, require_internal
from hermes_plus.rpc.errors import (
    ASK_FAILED,
    ASK_NO_TRANSCRIPT,
    ASK_QUESTION_REQUIRED,
    ASK_SESSION_REQUIRED,
    FEATURE_UNAVAILABLE,
    METHOD_NOT_FOUND,
    RpcFault,
)
from hermes_plus.rpc.params import read_text, require_text
from hermes_plus.rpc.registry import method
from hermes_plus.rpc.transcript import digest_transcript, message_digest_lines, prior_exchanges, tail_lines

log = logging.getLogger(__name__)

ASK_TASK = "session_ask"
ASK_MAX_TOKENS = 1024
ASK_INSTRUCTIONS = (
    "You answer questions about a Hermes agent session. The session's transcript is provided "
    "below. Answer from the transcript only — quote file paths, commands, errors, and outcomes "
    "precisely when they matter, and say plainly when the transcript does not contain the answer "
    "instead of guessing. Keep answers concise; markdown code spans are fine."
)

PREDICT_TASK = "composer_prediction"
PREDICT_MAX_TOKENS = 160
PREDICT_TAIL_CHARS = 8000
STYLE_SAMPLES = 6
STYLE_SAMPLE_CHARS = 300
MAX_PREDICTION_CHARS = 280
PREDICT_INSTRUCTIONS = (
    "You predict the very next message the USER will send in a conversation with their AI agent. "
    "Write it exactly as the user would type it: match their length, tone, casing, punctuation and "
    "vocabulary from their earlier messages. Usually it is a short follow-up, a go-ahead, a "
    "correction or the obvious next request given where the assistant left off. Output only the "
    "message text, with no quotes, labels or explanation. If there is no confident, natural next "
    "message, output nothing."
)


@method("session.ask")
async def session_ask(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    session_id = require_text(params, "session_id", ASK_SESSION_REQUIRED, "session_id required")
    question = require_text(params, "question", ASK_QUESTION_REQUIRED, "question required")
    caller = require_internal(ctx, "session.ask")
    messages = await _transcript(caller, "session.ask", session_id)
    digest, considered, truncated = digest_transcript(messages)
    if not digest.strip():
        raise RpcFault(ASK_NO_TRANSCRIPT, "session has no transcript yet")
    parts = [f"Transcript for session {session_id}:", digest]
    prior = prior_exchanges(params)
    if prior:
        parts.append("Earlier questions and answers about this session:")
        parts.extend(f"Q: {q}\nA: {a}" for q, a in prior)
    parts.append(f"Question: {question}")
    answer = (await _oneshot(caller, "session.ask", task=ASK_TASK, instructions=ASK_INSTRUCTIONS,
                             input="\n\n".join(parts), max_tokens=ASK_MAX_TOKENS)).strip()
    if not answer:
        raise RpcFault(ASK_FAILED, "session.ask returned an empty answer")
    return {"answer": answer, "resolved_id": session_id,
            "messages_considered": considered, "truncated": truncated}


@method("session.predict_next")
async def session_predict_next(ctx: RpcContext, params: dict[str, Any]) -> dict[str, Any]:
    session_id = require_text(params, "session_id", ASK_SESSION_REQUIRED, "session_id required")
    caller = require_internal(ctx, "session.predict_next")
    if not _prediction_enabled(read_text(params, "profile") or None):
        return {"text": ""}
    try:
        messages = await _transcript(caller, "session.predict_next", session_id)
        lines = message_digest_lines(messages)
        if not lines or not lines[-1].startswith("assistant: "):
            return {"text": ""}
        parts = ["Conversation so far (most recent last):", tail_lines(lines, PREDICT_TAIL_CHARS)]
        samples = _user_style_samples(lines)
        if samples:
            parts.append("How this user writes (their recent messages):\n" + "\n".join(f"- {s}" for s in samples))
        parts.append("The user's next message:")
        raw = await _oneshot(caller, "session.predict_next", task=PREDICT_TASK, instructions=PREDICT_INSTRUCTIONS,
                             input="\n\n".join(parts), max_tokens=PREDICT_MAX_TOKENS)
    except UpstreamRpcError as err:
        log.info("session.predict_next skipped: %s", err.message)
        return {"text": ""}
    return {"text": clean_prediction(raw)}


def clean_prediction(raw: str) -> str:
    text = raw.strip().strip('"“”').strip()
    if text.lower().startswith(("user:", "you:", "next message:", "message:")):
        text = text.split(":", 1)[1].strip().strip('"“”').strip()
    if not text or len(text) > MAX_PREDICTION_CHARS or "\n\n" in text:
        return ""
    return " ".join(text.split())


def _prediction_enabled(profile: str | None) -> bool:
    auxiliary = load_config(profile).get("auxiliary") or {}
    return bool((auxiliary.get(PREDICT_TASK) or {}).get("enabled"))


def _user_style_samples(lines: list[str]) -> list[str]:
    samples: list[str] = []
    for line in reversed(lines):
        if line.startswith("user: "):
            samples.append(line[len("user: "):][:STYLE_SAMPLE_CHARS])
            if len(samples) == STYLE_SAMPLES:
                break
    return list(reversed(samples))


async def _transcript(caller: UpstreamCaller, capability: str, session_id: str) -> list[dict[str, Any]]:
    reply = await _gateway_call(caller, capability, "session.history", {"session_id": session_id})
    return list(reply.get("messages") or [])


async def _oneshot(caller: UpstreamCaller, capability: str, **params: Any) -> str:
    reply = await _gateway_call(caller, capability, "llm.oneshot", params)
    return str(reply.get("text") or "")


async def _gateway_call(caller: UpstreamCaller, capability: str, name: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        return await caller.call(name, params)
    except UpstreamRpcError as err:
        if err.code == METHOD_NOT_FOUND:
            raise RpcFault(FEATURE_UNAVAILABLE, f"{capability} needs {name}, which this Hermes gateway lacks",
                           data={"capability": capability, "method": name}) from err
        raise
