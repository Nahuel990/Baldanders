"""Layer 2: multilingual GLiNER model for names, addresses and passports.

Runs fully offline from the files `baldanders download` fetched. Optional:
needs `pip install "baldanders[ner]"`.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
import warnings
from pathlib import Path
from typing import Iterator

from baldanders.scrubber.regex import Span

log = logging.getLogger(__name__)

# Zero-shot labels sent to GLiNER, mapped to our entity types.
# Keep this focused: fewer labels, fewer false positives.
_LABEL_MAP = {
    "person": "PERSON",
    "street address": "ADDRESS",
    "passport number": "PASSPORT",
}
PERSONAL_DATA_LABELS = list(_LABEL_MAP)

# Confidence needed per entity type, tuned on multilingual samples: names
# need a high bar to keep verbs and product names out; the model is less
# sure about addresses (the regex layer covers those too).
DEFAULT_THRESHOLDS = {
    "PERSON": 0.7,
    "ADDRESS": 0.4,
    "PASSPORT": 0.5,
}
# Names written in Chinese, Japanese or Korean score lower for the same
# certainty, so they get a lower bar.
_CJK_PERSON_THRESHOLD = 0.5
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_DIGIT = re.compile(r"\d")

# Minimum character length per entity type to reduce noise. CJK names are
# often two characters, so PERSON stays at 2.
_MIN_LENGTH = {
    "PERSON": 2,
    "ADDRESS": 4,
    "PASSPORT": 5,
}

# Patterns that are commonly misclassified as personal data
_FALSE_POSITIVE_PATTERNS = [
    # Common words misclassified as person names
    re.compile(r"^(today|tomorrow|yesterday|now|here|there|this|that|the|none|null|true|false|yes|no|user|admin|me|you|i)$", re.IGNORECASE),
    # SQL keywords
    re.compile(r"^(select|from|where|insert|update|delete|create|drop|alter|join|union|group|order|having|limit|offset|values|into|set|table|index|view|function|returns|begin|end|return|query|all|and|or|not|in|is|as|on|by|with|case|when|then|else)$", re.IGNORECASE),
    # Programming keywords
    re.compile(r"^(def|class|import|return|if|else|for|while|try|except|finally|raise|yield|async|await|lambda|pass|break|continue|global|nonlocal)$", re.IGNORECASE),
    # Pure numbers or very short tokens
    re.compile(r"^\d+$"),
    # UK postcodes (commonly misclassified as driver's licenses)
    re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE),
    # Placeholder tokens Baldanders itself produces
    re.compile(r"^\[?[A-Z_]+_\d+\]?$"),
]

# Words per model call. The model accepts more, but shorter windows keep
# accuracy up on long pastes; windows overlap so no entity is cut in half.
_CHUNK_WORDS = 250
_CHUNK_OVERLAP = 40


def _is_false_positive(text: str, entity_type: str) -> bool:
    """Check if a detected entity is likely a false positive."""
    min_len = _MIN_LENGTH.get(entity_type, 2)
    if len(text.strip()) < min_len:
        return True

    for pattern in _FALSE_POSITIVE_PATTERNS:
        if pattern.match(text.strip()):
            return True

    return False


def _is_ideographic(ch: str) -> bool:
    """Scripts written without spaces: each character is its own word."""
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x30FF      # Hiragana, Katakana
        or 0x3400 <= cp <= 0x4DBF   # CJK Extension A
        or 0x4E00 <= cp <= 0x9FFF   # CJK Unified
        or 0xF900 <= cp <= 0xFAFF   # CJK Compatibility
        or 0x20000 <= cp <= 0x2FA1F
        or 0x0E00 <= cp <= 0x0E7F   # Thai
        or 0x0E80 <= cp <= 0x0EFF   # Lao
        or 0x1000 <= cp <= 0x109F   # Myanmar
        or 0x1780 <= cp <= 0x17FF   # Khmer
    )


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    # Letters, combining marks (Devanagari vowel signs, Arabic harakat), digits.
    return cat[0] in "LMN"


def split_words(text: str) -> Iterator[tuple[str, int, int]]:
    """Language-agnostic word splitter, yielding (word, start, end).

    Hyphens and apostrophes inside a word (O'Connor, Jean-Luc) stay part of it.
    """
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if _is_ideographic(ch) or not _is_word_char(ch):
            yield ch, i, i + 1
            i += 1
            continue
        start = i
        i += 1
        while i < n:
            c = text[i]
            if _is_word_char(c) and not _is_ideographic(c):
                i += 1
            elif c in "-'’" and i + 1 < n and _is_word_char(text[i + 1]) and not _is_ideographic(text[i + 1]):
                i += 2
            else:
                break
        yield text[start:i], start, i


class _Splitter:
    """Adapter for GLiNER's words_splitter interface."""

    def __call__(self, text: str) -> Iterator[tuple[str, int, int]]:
        yield from split_words(text)


def _find_onnx_file(model_dir: Path) -> str | None:
    for candidate in ("onnx/model_quantized.onnx", "onnx/model.onnx", "model.onnx"):
        if (model_dir / candidate).exists():
            return candidate
    return None


class NERDetector:
    """Wraps a GLiNER ONNX model for contextual personal-data detection."""

    def __init__(self, model_dir: Path | None = None, threshold: float | None = None) -> None:
        self._model = None
        self._model_dir = model_dir
        # A single threshold overrides the per-type defaults.
        self._thresholds = (
            dict.fromkeys(DEFAULT_THRESHOLDS, threshold) if threshold is not None else dict(DEFAULT_THRESHOLDS)
        )
        self._available = False
        self._load_attempted = False
        self.load_error: str | None = None

    def _try_load(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True

        if self._model_dir is None or not self._model_dir.exists():
            self.load_error = "model not downloaded"
            log.info("NER model not found, running in regex-only mode")
            return

        onnx_file = _find_onnx_file(self._model_dir)
        if onnx_file is None:
            self.load_error = "model files incomplete, run `baldanders download`"
            return

        # Everything is local; make sure nothing reaches the network.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                for noisy in ("transformers", "gliner", "torch", "huggingface_hub"):
                    logging.getLogger(noisy).setLevel(logging.ERROR)
                from gliner import GLiNER
                self._model = GLiNER.from_pretrained(
                    str(self._model_dir),
                    load_onnx_model=True,
                    onnx_model_file=onnx_file,
                    map_location="cpu",
                    local_files_only=True,
                    words_splitter=_Splitter(),
                )
            self._available = True
            log.info("NER model loaded from %s", self._model_dir)
        except ImportError:
            self.load_error = 'NER extra not installed: pip install "baldanders[ner]"'
            log.warning("gliner not installed, running in regex-only mode")
        except Exception as e:
            self.load_error = f"failed to load model: {e}"
            log.warning("Failed to load NER model, running in regex-only mode", exc_info=True)

    @property
    def available(self) -> bool:
        self._try_load()
        return self._available

    def detect(self, text: str, threshold: float | None = None) -> list[Span]:
        """Detect personal-data entities. Returns an empty list if the model is unavailable."""
        self._try_load()
        if not self._available or self._model is None or not text.strip():
            return []
        thresholds = dict.fromkeys(self._thresholds, threshold) if threshold is not None else self._thresholds
        floor = min(thresholds.values())

        best: dict[tuple[int, int], Span] = {}
        scores: dict[tuple[int, int], float] = {}
        for offset, chunk in _chunks(text):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    entities = self._model.predict_entities(chunk, PERSONAL_DATA_LABELS, threshold=floor)
            except Exception:
                log.warning("NER inference failed", exc_info=True)
                continue

            for ent in entities:
                entity_type = _LABEL_MAP.get(ent.get("label", ""))
                if entity_type is None:
                    continue
                score = float(ent.get("score", 0.0))
                needed = thresholds[entity_type]
                if entity_type == "PERSON" and threshold is None and _CJK.search(ent.get("text", "")):
                    needed = min(needed, _CJK_PERSON_THRESHOLD)
                if score < needed:
                    continue
                start, end = ent["start"] + offset, ent["end"] + offset
                if entity_type == "PASSPORT":
                    trimmed = _trim_to_code(text, start, end)
                    if trimmed is None:
                        continue
                    start, end = trimmed
                matched_text = text[start:end]
                if _is_false_positive(matched_text, entity_type):
                    log.debug("Filtered false positive: %r as %s", matched_text, entity_type)
                    continue
                # A street address has a house number; without one it's a
                # city, a country or a stray word ("IBAN").
                if entity_type == "ADDRESS" and not _DIGIT.search(matched_text):
                    continue
                key = (start, end)
                if score > scores.get(key, -1.0):
                    scores[key] = score
                    best[key] = Span(start, end, entity_type, matched_text)

        return sorted(best.values(), key=lambda s: s.start)


_CODE = re.compile(r"[A-Za-z0-9]*\d[A-Za-z0-9]*")


def _trim_to_code(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Shrink a passport span to the alphanumeric code itself.

    The model sometimes includes the keyword ("DNI 20.123.456", "passport
    X1234567"); the number is the only part worth hiding.
    """
    codes = [m for m in _CODE.finditer(text, start, end) if len(m.group()) >= 5]
    if not codes:
        return None
    return codes[-1].start(), codes[-1].end()


def _chunks(text: str) -> Iterator[tuple[int, str]]:
    """Yield (offset, chunk) windows of at most _CHUNK_WORDS words."""
    words = list(split_words(text))
    if len(words) <= _CHUNK_WORDS:
        yield 0, text
        return
    step = _CHUNK_WORDS - _CHUNK_OVERLAP
    for i in range(0, len(words), step):
        window = words[i:i + _CHUNK_WORDS]
        start, end = window[0][1], window[-1][2]
        yield start, text[start:end]
        if i + _CHUNK_WORDS >= len(words):
            break
