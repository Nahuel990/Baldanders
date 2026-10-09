"""Scrub engine — orchestrates regex + NER layers + config."""

from __future__ import annotations

import re
from pathlib import Path

from baldanders.config import Config
from baldanders.mapping import SessionMap
from baldanders.scrubber.ner import NERDetector
from baldanders.scrubber.regex import Span
from baldanders.scrubber import regex


class ScrubEngine:
    """Two-layer personal-data detection: regex first, then NER on the gaps."""

    def __init__(
        self,
        session_map: SessionMap,
        model_dir: Path | None = None,
        config: Config | None = None,
    ) -> None:
        self.session_map = session_map
        self._ner = NERDetector(model_dir)
        self._config = config or Config()
        self._allowlist_lower = {t.lower() for t in self._config.allowlist}
        self._denylist_patterns: list[tuple[str, re.Pattern[str]]] = []

        # Compile custom patterns from config
        for name, pattern_str in self._config.custom_patterns.items():
            try:
                self._denylist_patterns.append((name.upper(), re.compile(pattern_str)))
            except re.error:
                pass

    @property
    def ner_available(self) -> bool:
        return self._ner.available

    def scrub(self, text: str) -> str:
        """Detect personal data and return scrubbed text."""
        # Layer 1: regex (deterministic)
        spans = regex.detect(text)

        # Layer 2: NER (contextual)
        ner_spans = self._ner.detect(text)
        if ner_spans:
            spans = _merge_spans(spans, ner_spans)

        # Layer 3: denylist from config (always-scrub terms)
        for value, entity_type in self._config.denylist.items():
            for m in re.finditer(re.escape(value), text):
                spans.append(Span(m.start(), m.end(), entity_type, m.group()))

        # Layer 4: custom regex patterns from config
        for entity_type, pattern in self._denylist_patterns:
            for m in pattern.finditer(text):
                spans.append(Span(m.start(), m.end(), entity_type, m.group()))

        if spans:
            spans = _merge_spans(spans, [])  # deduplicate
            spans.sort(key=lambda s: s.start)

        # Filter out allowlisted terms
        spans = [
            s for s in spans
            if s.text.lower() not in self._allowlist_lower
        ]

        if not spans:
            return text

        return self.session_map.scrub(text, spans)


def _merge_spans(primary: list[Span], secondary: list[Span]) -> list[Span]:
    """Merge two span lists. Primary spans win on overlaps."""
    covered = set()
    for s in primary:
        for i in range(s.start, s.end):
            covered.add(i)

    merged = list(primary)
    for s in secondary:
        if not any(i in covered for i in range(s.start, s.end)):
            merged.append(s)
            for i in range(s.start, s.end):
                covered.add(i)

    merged.sort(key=lambda s: s.start)
    return merged
