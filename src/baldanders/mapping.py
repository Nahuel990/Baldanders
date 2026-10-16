"""Session-scoped personal-data token mapping and rehydration."""

from __future__ import annotations

import re
from typing import Any

from baldanders.scrubber.regex import Span

# Matches a token the way an LLM might echo it back: [PERSON_1], PERSON_1,
# [person_1], [PERSON 1], or markdown-escaped \[PERSON\_1\].
_TOKEN_LIKE = re.compile(
    r"(?P<open>\\?\[\s?)?"
    r"(?<![A-Za-z0-9_])"
    r"(?P<type>[A-Za-z]+(?:\\?_[A-Za-z]+)*)"
    r"(?P<sep>\\?_| )"
    r"(?P<num>\d+)(?![\d])"
    r"(?P<close>\s?\\?\])?"
)


class SessionMap:
    """Maps real personal values to deterministic placeholder tokens.

    The same real value always maps to the same token within a session.
    The mapping lives in memory; the CLI saves it with the session (owner-only
    file) so --resume can restore it.
    """

    def __init__(self) -> None:
        # real_value -> token
        self._forward: dict[str, str] = {}
        # token -> real_value
        self._reverse: dict[str, str] = {}
        # entity_type -> next counter
        self._counters: dict[str, int] = {}

    def get_or_create_token(self, real_value: str, entity_type: str) -> str:
        """Return existing token or create a new one for this value."""
        if real_value in self._forward:
            return self._forward[real_value]
        entity_type = re.sub(r"[^A-Z_]", "_", entity_type.upper()) or "PII"
        count = self._counters.get(entity_type, 0) + 1
        self._counters[entity_type] = count
        token = f"[{entity_type}_{count}]"
        self._forward[real_value] = token
        self._reverse[token] = real_value
        return token

    def scrub(self, text: str, spans: list[Span]) -> str:
        """Replace personal-data spans with tokens. Spans must not overlap."""
        # Assign tokens in reading order so numbering follows the text.
        ordered = sorted(spans, key=lambda s: s.start)
        tokens = [self.get_or_create_token(s.text, s.entity_type) for s in ordered]
        parts = []
        pos = 0
        for span, token in zip(ordered, tokens):
            if span.start < pos:
                continue  # defensive: skip an overlapping span
            parts.append(text[pos:span.start])
            parts.append(token)
            pos = span.end
        parts.append(text[pos:])
        return "".join(parts)

    def rehydrate(self, text: str) -> str:
        """Replace tokens in an LLM response with the original values.

        Single pass, so a restored value is never itself re-substituted.
        Tolerates dropped brackets, changed case and markdown escaping.
        """
        if not self._reverse:
            return text

        def replace(m: re.Match[str]) -> str:
            raw_type = m.group("type").replace("\\", "")
            bracketed = m.group("open") is not None or m.group("close") is not None
            # Loose forms ("person 1", "phone_2") only count when bracketed
            # or written in capitals, so ordinary prose like "phone 1" survives.
            if not bracketed and (not raw_type.isupper() or m.group("sep") == " "):
                return m.group(0)
            token = f"[{raw_type.upper()}_{m.group('num')}]"
            real = self._reverse.get(token)
            return real if real is not None else m.group(0)

        return _TOKEN_LIKE.sub(replace, text)

    def get_map(self) -> dict[str, str]:
        """Return the current token -> real value mapping (for /map command)."""
        return dict(self._reverse)

    def to_dict(self) -> dict[str, Any]:
        return {
            "forward": dict(self._forward),
            "reverse": dict(self._reverse),
            "counters": dict(self._counters),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMap":
        m = cls()
        m._reverse = {str(k): str(v) for k, v in (data.get("reverse") or {}).items()}
        m._forward = {v: k for k, v in m._reverse.items()}
        m._counters = {str(k): int(v) for k, v in (data.get("counters") or {}).items()}
        # Make sure counters never re-issue an existing token.
        for token in m._reverse:
            match = re.match(r"^\[([A-Z_]+)_(\d+)\]$", token)
            if match:
                etype, n = match.group(1), int(match.group(2))
                m._counters[etype] = max(m._counters.get(etype, 0), n)
        return m
