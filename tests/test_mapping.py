"""Tests for session mapping and rehydration."""

from baldanders.mapping import SessionMap
from baldanders.scrubber.regex import Span


def test_get_or_create_token():
    m = SessionMap()
    t1 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    assert t1 == "[PERSON_1]"
    t2 = m.get_or_create_token("Jane Smith", "PERSON")
    assert t2 == "[PERSON_2]"


def test_same_value_same_token():
    m = SessionMap()
    t1 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    t2 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    assert t1 == t2


def test_different_types_separate_counters():
    m = SessionMap()
    t1 = m.get_or_create_token("jane@example.com", "EMAIL")
    t2 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    assert t1 == "[EMAIL_1]"
    assert t2 == "[PERSON_1]"


def test_scrub_replaces_spans():
    m = SessionMap()
    spans = [
        Span(0, 13, "PERSON", "Nahuel Nucera"),
        Span(22, 38, "EMAIL", "jane@example.com"),
    ]
    result = m.scrub("Nahuel Nucera emailed jane@example.com", spans)
    assert result == "[PERSON_1] emailed [EMAIL_1]"


def test_scrub_preserves_non_pii():
    m = SessionMap()
    spans = [Span(10, 26, "EMAIL", "jane@example.com")]
    result = m.scrub("Send mail jane@example.com today", spans)
    assert result == "Send mail [EMAIL_1] today"


def test_rehydrate():
    m = SessionMap()
    m.get_or_create_token("Nahuel Nucera", "PERSON")
    m.get_or_create_token("GB29NWBK60161331926819", "IBAN")
    text = "Transfer for [PERSON_1] to [IBAN_1] is done."
    result = m.rehydrate(text)
    assert result == "Transfer for Nahuel Nucera to GB29NWBK60161331926819 is done."


def test_rehydrate_no_tokens():
    m = SessionMap()
    text = "No tokens here"
    assert m.rehydrate(text) == text


def test_multi_turn_consistency():
    m = SessionMap()
    # Turn 1
    t1 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    # Turn 5 — same value, same token
    t2 = m.get_or_create_token("Nahuel Nucera", "PERSON")
    assert t1 == t2 == "[PERSON_1]"


def test_get_map():
    m = SessionMap()
    m.get_or_create_token("Nahuel Nucera", "PERSON")
    m.get_or_create_token("jane@example.com", "EMAIL")
    mapping = m.get_map()
    assert mapping["[PERSON_1]"] == "Nahuel Nucera"
    assert mapping["[EMAIL_1]"] == "jane@example.com"
