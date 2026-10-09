"""Session-scoped personal-data token mapping and rehydration."""

from __future__ import annotations

from baldanders.scrubber.regex import Span


class SessionMap:
    """Maps real personal values to deterministic placeholder tokens.

    The same real value always maps to the same token within a session.
    The mapping lives only in memory — nothing is persisted to disk.
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
        count = self._counters.get(entity_type, 0) + 1
        self._counters[entity_type] = count
        token = f"[{entity_type}_{count}]"
        self._forward[real_value] = token
        self._reverse[token] = real_value
        return token

    def scrub(self, text: str, spans: list[Span]) -> str:
        """Replace personal-data spans with tokens, processing right-to-left."""
        # Sort spans right-to-left so replacements don't shift indices
        sorted_spans = sorted(spans, key=lambda s: s.start, reverse=True)
        result = text
        for span in sorted_spans:
            token = self.get_or_create_token(span.text, span.entity_type)
            result = result[:span.start] + token + result[span.end:]
        return result

    def rehydrate(self, text: str) -> str:
        """Replace tokens in LLM response with original values.

        Processes longest tokens first to avoid partial replacement issues.
        Also handles cases where the LLM drops brackets or changes case.
        """
        result = text
        # Sort by token length descending — longer tokens first to avoid
        # [PERSON_10] being partially matched by [PERSON_1]
        sorted_tokens = sorted(self._reverse.items(), key=lambda t: len(t[0]), reverse=True)
        for token, real_value in sorted_tokens:
            result = result.replace(token, real_value)

        # Second pass: handle LLM mangling — sometimes brackets get dropped
        # e.g., "PERSON_1" instead of "[PERSON_1]"
        for token, real_value in sorted_tokens:
            bare = token[1:-1]  # strip [ ]
            if bare in result:
                result = result.replace(bare, real_value)

        return result

    def get_map(self) -> dict[str, str]:
        """Return the current token -> real value mapping (for /map command)."""
        return dict(self._reverse)
