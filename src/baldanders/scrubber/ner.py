"""Layer 2 — GLiNER ONNX-based contextual PII detection."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from baldanders.scrubber.regex import Span

log = logging.getLogger(__name__)

# Entity labels to request from GLiNER (zero-shot)
# Keep this focused — fewer labels = fewer false positives
PII_LABELS = [
    "person name",
    "street address",
    "passport number",
]

# Map GLiNER labels to our entity types
_LABEL_MAP = {
    "person name": "PERSON",
    "street address": "ADDRESS",
    "passport number": "PASSPORT",
}

# Minimum character length per entity type to reduce noise
_MIN_LENGTH = {
    "PERSON": 3,
    "ADDRESS": 5,
    "PASSPORT": 5,
}

# Patterns that are commonly misclassified as PII
_FALSE_POSITIVE_PATTERNS = [
    # Common words misclassified as person names
    re.compile(r"^(today|tomorrow|yesterday|now|here|there|this|that|the|none|null|true|false|yes|no)$", re.IGNORECASE),
    # SQL keywords
    re.compile(r"^(select|from|where|insert|update|delete|create|drop|alter|join|union|group|order|having|limit|offset|values|into|set|table|index|view|function|returns|begin|end|return|query|all|and|or|not|in|is|as|on|by|with|case|when|then|else)$", re.IGNORECASE),
    # Programming keywords
    re.compile(r"^(def|class|import|return|if|else|for|while|try|except|finally|raise|yield|async|await|lambda|pass|break|continue|global|nonlocal)$", re.IGNORECASE),
    # Pure numbers or very short tokens
    re.compile(r"^\d+$"),
    # UK postcodes (commonly misclassified as driver's licenses)
    re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE),
]


def _is_false_positive(text: str, entity_type: str) -> bool:
    """Check if a detected entity is likely a false positive."""
    min_len = _MIN_LENGTH.get(entity_type, 2)
    if len(text.strip()) < min_len:
        return True

    for pattern in _FALSE_POSITIVE_PATTERNS:
        if pattern.match(text.strip()):
            return True

    return False


class NERDetector:
    """Wraps a GLiNER ONNX model for contextual PII detection."""

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model = None
        self._model_dir = model_dir
        self._available = False
        self._load_attempted = False

    def _try_load(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True

        if self._model_dir is None or not self._model_dir.exists():
            log.info("NER model not found — running in regex-only mode")
            return

        try:
            from gliner import GLiNER
            self._model = GLiNER.from_pretrained(
                str(self._model_dir),
                runtime="onnxruntime",
                map_location="cpu",
            )
            self._available = True
            log.info("NER model loaded from %s", self._model_dir)
        except ImportError:
            log.warning("gliner not installed — running in regex-only mode")
        except Exception:
            log.warning("Failed to load NER model — running in regex-only mode", exc_info=True)

    @property
    def available(self) -> bool:
        self._try_load()
        return self._available

    def detect(self, text: str, threshold: float = 0.5) -> list[Span]:
        """Detect PII entities using GLiNER. Returns empty list if model unavailable."""
        self._try_load()
        if not self._available or self._model is None:
            return []

        try:
            entities = self._model.predict_entities(text, PII_LABELS, threshold=threshold)
        except Exception:
            log.warning("NER inference failed", exc_info=True)
            return []

        spans = []
        for ent in entities:
            label = ent.get("label", "")
            entity_type = _LABEL_MAP.get(label, label.upper().replace(" ", "_"))
            matched_text = ent["text"]

            if _is_false_positive(matched_text, entity_type):
                log.debug("Filtered false positive: %r as %s", matched_text, entity_type)
                continue

            spans.append(Span(
                start=ent["start"],
                end=ent["end"],
                entity_type=entity_type,
                text=matched_text,
            ))
        return spans
