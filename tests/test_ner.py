"""Tests for NER detection — runs with the real model if downloaded."""

import pytest
from pathlib import Path

from baldanders.model_manager import model_dir, is_downloaded
from baldanders.scrubber.ner import NERDetector, _is_false_positive


# --- False positive filter tests (always run) ---

def test_false_positive_common_words():
    assert _is_false_positive("today", "PERSON") is True
    assert _is_false_positive("tomorrow", "PERSON") is True
    assert _is_false_positive("SELECT", "PERSON") is True
    assert _is_false_positive("NULL", "PERSON") is True


def test_false_positive_short_strings():
    assert _is_false_positive("ab", "PERSON") is True
    assert _is_false_positive("x", "ADDRESS") is True


def test_false_positive_uk_postcodes():
    assert _is_false_positive("SL4 2JW", "DRIVERS_LICENSE") is True
    assert _is_false_positive("SW1A 1AA", "DRIVERS_LICENSE") is True
    assert _is_false_positive("EC1A1BB", "DRIVERS_LICENSE") is True


def test_not_false_positive_real_names():
    assert _is_false_positive("John Smith", "PERSON") is False
    assert _is_false_positive("Alex Rivera", "PERSON") is False
    assert _is_false_positive("Jamie Chen", "PERSON") is False


def test_false_positive_pure_numbers():
    assert _is_false_positive("12345", "PERSON") is True
    assert _is_false_positive("0", "ADDRESS") is True


# --- Integration tests (only if model is downloaded) ---

needs_model = pytest.mark.skipif(
    not is_downloaded(),
    reason="GLiNER model not downloaded — run `baldanders download` first",
)


@needs_model
def test_ner_loads():
    detector = NERDetector(model_dir=model_dir())
    assert detector.available is True


@needs_model
def test_ner_detects_person():
    detector = NERDetector(model_dir=model_dir())
    spans = detector.detect("Please transfer funds to John Smith immediately")
    person_spans = [s for s in spans if s.entity_type == "PERSON"]
    assert len(person_spans) >= 1
    assert any("John Smith" in s.text for s in person_spans)


@needs_model
def test_ner_no_false_positive_today():
    detector = NERDetector(model_dir=model_dir())
    spans = detector.detect("What date is today?")
    # "today" should not appear as any entity
    assert all(s.text.lower() != "today" for s in spans)


@needs_model
def test_ner_no_false_positive_sql():
    detector = NERDetector(model_dir=model_dir())
    spans = detector.detect("SELECT * FROM users WHERE id = 1")
    # SQL keywords should not be detected
    sql_words = {"select", "from", "where"}
    for s in spans:
        assert s.text.lower() not in sql_words
