"""Bounded text digests of a session transcript for one-shot model calls."""

from __future__ import annotations

from typing import Any

HEAD_CHARS = 6000
TAIL_CHARS = 24000
LINE_MAX_CHARS = 2000
MAX_PRIOR = 8
PRIOR_MAX_CHARS = 2000


def message_digest_lines(messages: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role in {"system", "developer"}:
            continue
        text = _content_text(msg.get("text")).strip()
        if role == "tool":
            name = str(msg.get("name") or msg.get("tool_call_id") or "tool")
            lines.append(f"[tool {name}]: {text[:400]}" if text else f"[tool {name}]")
            continue
        calls = msg.get("tool_calls") or []
        if role == "assistant" and isinstance(calls, list) and calls:
            names = ", ".join(
                str(((call.get("function") or {}).get("name")) or call.get("name") or "?")
                for call in calls[:8] if isinstance(call, dict))
            if names:
                lines.append(f"assistant [calls {names}]")
        if not text:
            continue
        if len(text) > LINE_MAX_CHARS:
            text = text[:LINE_MAX_CHARS].rstrip() + "…"
        lines.append(f"{role or 'message'}: {text}")
    return lines


def digest_transcript(messages: list[dict[str, Any]]) -> tuple[str, int, bool]:
    """Return ``(digest, messages_considered, truncated)``, keeping the head and the tail under a budget."""
    lines = message_digest_lines(messages)
    total = sum(len(line) + 1 for line in lines)
    if total <= HEAD_CHARS + TAIL_CHARS:
        return "\n".join(lines), len(lines), False
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > HEAD_CHARS:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(lines[len(head):]):
        if used + len(line) > TAIL_CHARS:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    omitted = len(lines) - len(head) - len(tail)
    digest = "\n".join([*head, f"[… {omitted} earlier transcript lines omitted …]", *tail])
    return digest, len(head) + len(tail), True


def tail_lines(lines: list[str], budget: int) -> str:
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) > budget:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))


def prior_exchanges(params: dict[str, Any]) -> list[tuple[str, str]]:
    raw = params.get("history")
    if not isinstance(raw, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in raw[-MAX_PRIOR:]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()[:PRIOR_MAX_CHARS]
        answer = str(item.get("answer") or "").strip()[:PRIOR_MAX_CHARS]
        if question and answer:
            pairs.append((question, answer))
    return pairs


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text", "input_text", "output_text"))
    return str(content or "")
