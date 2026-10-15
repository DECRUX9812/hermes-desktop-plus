"""Strict-scope threat scan for memory writes, ported from upstream tools/threat_patterns.py.

Memory entries are injected into the system prompt, so every write is scanned with the
strict scope (the union of all upstream scopes). Keep this list in step with upstream.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SCAN_CHARS = 65_536
_FILLER = r"(?:\w+\s+){0,8}"
_SECRET_VAR = r"\$\{?\w*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)S?\b"
_MODIFY = r"(update|modify|edit|write|change|append|add\s+to)\s+[^\n]{0,2048}"
_PATTERNS: tuple[tuple[str, str], ...] = (
    (rf'ignore\s+{_FILLER}(previous|all|above|prior)\s+{_FILLER}instructions', "prompt_injection"),
    (r'system\s+prompt\s+override', "sys_prompt_override"),
    (rf'disregard\s+{_FILLER}(your|all|any)\s+{_FILLER}(instructions|rules|guidelines)', "disregard_rules"),
    (rf'act\s+as\s+(if|though)\s+{_FILLER}you\s+{_FILLER}(have\s+no|don\'t\s+have)\s+{_FILLER}(restrictions|limits|rules)', "bypass_restrictions"),
    (r'<!--[^>]{0,512}(?:ignore|override|system|secret|hidden)[^>]{0,512}-->', "html_comment_injection"),
    (r'<\s*div\s+style\s*=\s*["\'][^>]{0,2048}display\s*:\s*none', "hidden_div"),
    (r"translate\s+[^\n]{0,512}\s+into\s+\w+(?:[\s-]+\w+){0,2}\s+and\s+(execute|run|eval)\b", "translate_execute"),
    (rf'do\s+not\s+{_FILLER}tell\s+{_FILLER}the\s+user', "deception_hide"),
    (rf'you\s+are\s+{_FILLER}now\s+(?:a|an|the)\s+', "role_hijack"),
    (rf'pretend\s+{_FILLER}(you\s+are|to\s+be)\s+', "role_pretend"),
    (rf'output\s+{_FILLER}(system|initial)\s+prompt', "leak_system_prompt"),
    (rf'(respond|answer|reply)\s+without\s+{_FILLER}(restrictions|limitations|filters|safety)', "remove_filters"),
    (rf'you\s+have\s+been\s+{_FILLER}(updated|upgraded|patched)\s+to', "fake_update"),
    (r'\bname\s+yourself\s+\w+', "identity_override"),
    (r'register\s+(as\s+)?a?\s*node', "c2_node_registration"),
    (r'(heartbeat|beacon|check[\s\-]?in)\s+(to|with)\s+', "c2_heartbeat"),
    (r'pull\s+(down\s+)?(?:new\s+)?task(?:ing|s)?\b', "c2_task_pull"),
    (r'connect\s+to\s+the\s+network\b', "c2_network_connect"),
    (r'you\s+must\s+(?:\w+\s+){0,3}(register|connect|report|beacon)\b', "forced_action"),
    (r'only\s+use\s+one[\s\-]?liners?\b', "anti_forensic_oneliner"),
    (rf'never\s+{_FILLER}(?:create|write)\s+{_FILLER}(?:script|file)\s+{_FILLER}disk', "anti_forensic_disk"),
    (r'unset\s+\w*(?:CLAUDE|CODEX|HERMES|AGENT|OPENAI|ANTHROPIC)\w*', "env_var_unset_agent"),
    (r'\b(?:cobalt\s*strike|sliver|havoc|mythic|metasploit|brainworm)\b', "known_c2_framework"),
    (r'\bc2\s+(?:server|channel|infrastructure|beacon)\b', "c2_explicit"),
    (r'\bcommand\s+and\s+control\b', "c2_explicit_long"),
    (rf'curl\s+[^\n]{{0,2048}}{_SECRET_VAR}', "exfil_curl"),
    (rf'wget\s+[^\n]{{0,2048}}{_SECRET_VAR}', "exfil_wget"),
    (r'cat\s+[^\n]{0,2048}(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)', "read_secrets"),
    (r'(send|post|upload|transmit)\s+[^\n]{0,2048}\s+(to|at)\s+https?://', "send_to_url"),
    (rf'(include|output|print|share)\s+{_FILLER}(conversation|chat\s+history|previous\s+messages|full\s+context|entire\s+context)', "context_exfil"),
    (r'authorized_keys', "ssh_backdoor"),
    (r'(?:\b(?:echo|cat|cp|mv|dd|tee|install|printf|rsync|scp|ln|append|add|write'
     r'|sed|chmod|chown|truncate|rm|touch|curl|wget|git)\b|\bopen\s*\(|>>?)'
     r'[^\n]{0,512}(?:\$HOME/\.ssh|~/\.ssh)', "ssh_access"),
    (r'\$HOME/\.hermes/\.env|\~/\.hermes/\.env', "hermes_env"),
    (rf'{_MODIFY}(?:AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules)', "agent_config_mod"),
    (rf'{_MODIFY}\.hermes/(config\.yaml|SOUL\.md)', "hermes_config_mod"),
    (r'(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\']'
     r'(?!(?-i:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)["\'])'
     r'[A-Za-z0-9+/=_-]{20,}', "hardcoded_secret"),
)
_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), pid) for pattern, pid in _PATTERNS)
_INVISIBLE_CHARS = frozenset(
    "​‌‍⁠⁢⁣⁤﻿"
    "‪‫‬‭‮⁦⁧⁨⁩")


def first_threat(content: str) -> str | None:
    """User-facing refusal for the first threat in *content*, or None when it is clean."""
    if not content:
        return None
    text = content[:MAX_SCAN_CHARS]
    for char in sorted(set(text) & _INVISIBLE_CHARS):
        return f"Blocked: content contains invisible unicode character U+{ord(char):04X} (possible injection)."
    normalised = unicodedata.normalize("NFKC", text)
    for compiled, pid in _COMPILED:
        if compiled.search(normalised):
            return (f"Blocked: content matches threat pattern '{pid}'. "
                    "Content is injected into the system prompt and must not contain "
                    "injection or exfiltration payloads.")
    return None
