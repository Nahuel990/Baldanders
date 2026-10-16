"""Scrub engine — orchestrates regex + NER layers + config."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from baldanders.config import Config
from baldanders.mapping import SessionMap
from baldanders.scrubber import regex
from baldanders.scrubber.ner import NERDetector
from baldanders.scrubber.regex import Span, resolve_overlaps

log = logging.getLogger(__name__)

# Detection layers, most trusted first.
_USER_PRIORITY = -1
_REGEX_PRIORITY = 0
_NER_PRIORITY = 1


def _term_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term)
    if term[:1].isalnum():
        escaped = r"(?<![^\W_])" + escaped
    if term[-1:].isalnum():
        escaped = escaped + r"(?![^\W_])"
    return re.compile(escaped, re.IGNORECASE)


class ScrubEngine:
    """Layered personal-data detection.

    1. Denylist and custom patterns from config (always win).
    2. Deterministic rules: secrets, contact details, IBANs, cards, ~60 national IDs.
    3. Multilingual NER model for names and addresses (if installed).
    Earlier layers win overlaps; within a layer the longest span wins.
    Allowlisted text is never scrubbed.
    """

    def __init__(
        self,
        session_map: SessionMap,
        model_dir: Path | None = None,
        config: Config | None = None,
    ) -> None:
        self.session_map = session_map
        self._config = config or Config()
        self._ner = NERDetector(model_dir, threshold=self._config.ner_threshold)
        self._allowlist_lower = {t.casefold() for t in self._config.allowlist}
        self._deny = [(_term_pattern(v), t) for v, t in self._config.denylist.items() if v]
        self._custom: list[tuple[str, re.Pattern[str]]] = []
        self.config_errors: list[str] = []

        for name, pattern_str in self._config.custom_patterns.items():
            try:
                self._custom.append((name.upper(), re.compile(pattern_str)))
            except re.error as e:
                self.config_errors.append(f"custom pattern {name!r} is invalid: {e}")

    @property
    def ner_available(self) -> bool:
        return self._ner.available

    @property
    def ner_status(self) -> str:
        return "loaded" if self._ner.available else (self._ner.load_error or "unavailable")

    def detect(self, text: str) -> list[Span]:
        """Return the non-overlapping spans that would be scrubbed."""
        candidates: list[tuple[Span, int]] = []

        for pattern, entity_type in self._deny:
            for m in pattern.finditer(text):
                candidates.append((Span(m.start(), m.end(), entity_type, m.group()), _USER_PRIORITY))

        for entity_type, pattern in self._custom:
            for m in pattern.finditer(text):
                group = 1 if pattern.groups else 0
                start, end = m.span(group)
                if start < end:
                    candidates.append((Span(start, end, entity_type, text[start:end]), _USER_PRIORITY))

        for s in regex.detect(text, regions=self._config.regions, paranoid=self._config.paranoid):
            candidates.append((s, _REGEX_PRIORITY))

        for s in self._ner.detect(text):
            candidates.append((s, _NER_PRIORITY))

        candidates = [
            (s, p) for s, p in candidates
            if s.text.strip().casefold() not in self._allowlist_lower
        ]

        layers = [
            (p, resolve_overlaps(c for c in candidates if c[1] == p))
            for p in (_USER_PRIORITY, _REGEX_PRIORITY, _NER_PRIORITY)
        ]
        return _merge_layers(text, layers)

    def scrub(self, text: str) -> str:
        """Detect personal data and return scrubbed text."""
        spans = self.detect(text)
        if not spans:
            return text
        return self.session_map.scrub(text, spans)


def _merge_layers(text: str, layers: list[tuple[int, list[Span]]]) -> list[Span]:
    """Merge spans from all layers into non-overlapping spans.

    Overlapping spans become their union, so a partial match in one layer
    (a house number) can never leave the rest of a value (the street name)
    exposed. The union takes its label from the most trusted span that
    covers most of it, or else from the longest span.
    """
    tagged = sorted(
        ((s, rank) for rank, (_, spans) in enumerate(layers) for s in spans),
        key=lambda t: (t[0].start, -t[0].end),
    )
    clusters: list[list[tuple[Span, int]]] = []
    end = -1
    for span, rank in tagged:
        if clusters and span.start < end:
            clusters[-1].append((span, rank))
            end = max(end, span.end)
        else:
            clusters.append([(span, rank)])
            end = span.end

    merged = []
    for cluster in clusters:
        start = min(s.start for s, _ in cluster)
        stop = max(s.end for s, _ in cluster)
        if len(cluster) == 1:
            merged.append(cluster[0][0])
            continue
        size = stop - start
        by_trust = sorted(cluster, key=lambda t: (t[1], -(t[0].end - t[0].start)))
        label = next(
            (s.entity_type for s, _ in by_trust if (s.end - s.start) >= 0.8 * size),
            max(cluster, key=lambda t: t[0].end - t[0].start)[0].entity_type,
        )
        merged.append(Span(start, stop, label, text[start:stop]))
    return merged
