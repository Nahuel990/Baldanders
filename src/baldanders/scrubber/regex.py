"""Layer 1 — deterministic regex personal-data detection."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    text: str


def _luhn_check(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    # Email
    ("EMAIL", re.compile(
        r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
    ), False),

    # Credit card (13-19 digits, optional separators)
    ("CREDIT_CARD", re.compile(
        r"\b(\d[ \-]?){12,18}\d\b"
    ), True),  # needs Luhn validation

    # IBAN (2 letter country + 2 check digits + up to 30 alphanumeric)
    ("IBAN", re.compile(
        r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{4,30}\b"
    ), False),

    # UK sort code — must use hyphens or spaces as separators to avoid matching dates
    ("SORT_CODE", re.compile(
        r"(?<!\d[\-/])(?<!\d)\b\d{2}[\-\s]\d{2}[\-\s]\d{2}\b(?![\-/]\d)"
    ), False),

    # UK National Insurance number
    ("UK_NINO", re.compile(
        r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
        re.IGNORECASE,
    ), False),

    # Phone (international formats)
    ("PHONE", re.compile(
        r"(?<!\d)"
        r"(?:\+\d{1,3}[\s\-]?)?"
        r"(?:\(?\d{2,4}\)?[\s\-]?)?"
        r"\d{3,4}[\s\-]?\d{3,4}"
        r"(?!\d)"
    ), False),

    # IP address (v4)
    ("IP_ADDRESS", re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
    ), False),

    # SSN (US)
    ("SSN", re.compile(
        r"\b\d{3}[\-\s]?\d{2}[\-\s]?\d{4}\b"
    ), False),
]


def detect(text: str) -> list[Span]:
    """Return all regex-detected personal-data spans in the text."""
    spans: list[Span] = []
    for entity_type, pattern, needs_luhn in _PATTERNS:
        for m in pattern.finditer(text):
            if needs_luhn:
                digits = re.sub(r"\D", "", m.group())
                if not _luhn_check(digits):
                    continue
            spans.append(Span(m.start(), m.end(), entity_type, m.group()))
    # Sort by position, longest match first for overlaps
    spans.sort(key=lambda s: (s.start, -(s.end - s.start)))
    return _remove_overlaps(spans)


def _remove_overlaps(spans: list[Span]) -> list[Span]:
    """Keep longest span when two overlap."""
    result: list[Span] = []
    last_end = -1
    for s in spans:
        if s.start >= last_end:
            result.append(s)
            last_end = s.end
    return result
