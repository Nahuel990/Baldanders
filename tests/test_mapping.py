"""Tests for session mapping and rehydration."""

import pytest

from baldanders.mapping import SessionMap
from baldanders.scrubber.regex import Span


def test_get_or_create_token():
    m = SessionMap()
    assert m.get_or_create_token("Alex Rivera", "PERSON") == "[PERSON_1]"
    assert m.get_or_create_token("Jane Smith", "PERSON") == "[PERSON_2]"


def test_same_value_same_token():
    m = SessionMap()
    assert m.get_or_create_token("Alex Rivera", "PERSON") == m.get_or_create_token("Alex Rivera", "PERSON")


def test_different_types_separate_counters():
    m = SessionMap()
    assert m.get_or_create_token("jane@example.com", "EMAIL") == "[EMAIL_1]"
    assert m.get_or_create_token("Alex Rivera", "PERSON") == "[PERSON_1]"


def test_scrub_replaces_spans():
    m = SessionMap()
    spans = [
        Span(0, 11, "PERSON", "Alex Rivera"),
        Span(20, 36, "EMAIL", "jane@example.com"),
    ]
    assert m.scrub("Alex Rivera emailed jane@example.com", spans) == "[PERSON_1] emailed [EMAIL_1]"


def test_tokens_numbered_in_reading_order():
    m = SessionMap()
    text = "Ana met Bob"
    spans = [Span(8, 11, "PERSON", "Bob"), Span(0, 3, "PERSON", "Ana")]
    assert m.scrub(text, spans) == "[PERSON_1] met [PERSON_2]"


def test_scrub_preserves_non_personal_data():
    m = SessionMap()
    spans = [Span(10, 26, "EMAIL", "jane@example.com")]
    assert m.scrub("Send mail jane@example.com today", spans) == "Send mail [EMAIL_1] today"


def test_rehydrate():
    m = SessionMap()
    m.get_or_create_token("Alex Rivera", "PERSON")
    m.get_or_create_token("GB29NWBK60161331926819", "IBAN")
    text = "Transfer for [PERSON_1] to [IBAN_1] is done."
    assert m.rehydrate(text) == "Transfer for Alex Rivera to GB29NWBK60161331926819 is done."


@pytest.mark.parametrize("mangled", [
    "PERSON_1", "[person_1]", "[PERSON 1]", r"\[PERSON\_1\]", r"PERSON\_1", "[ PERSON_1 ]",
])
def test_rehydrate_mangled_tokens(mangled):
    m = SessionMap()
    m.get_or_create_token("Alex Rivera", "PERSON")
    assert m.rehydrate(f"Hi {mangled}!") == "Hi Alex Rivera!"


def test_rehydrate_double_digit_tokens():
    m = SessionMap()
    for i in range(1, 12):
        m.get_or_create_token(f"Person {i}", "PERSON")
    assert m.rehydrate("[PERSON_1] and [PERSON_10] and PERSON_11") == "Person 1 and Person 10 and Person 11"


def test_rehydrate_leaves_ordinary_prose():
    m = SessionMap()
    m.get_or_create_token("+44 7911 123456", "PHONE")
    # Lowercase/space forms outside brackets are normal words, not tokens.
    assert m.rehydrate("Option phone 1 or phone_1 is fine") == "Option phone 1 or phone_1 is fine"


def test_rehydrate_does_not_resubstitute():
    m = SessionMap()
    m.get_or_create_token("[EMAIL_1]", "PERSON")  # pathological value that looks like a token
    m.get_or_create_token("jane@example.com", "EMAIL")
    assert m.rehydrate("[PERSON_1]") == "[EMAIL_1]"


def test_rehydrate_unknown_token_left_alone():
    m = SessionMap()
    m.get_or_create_token("Alex", "PERSON")
    assert m.rehydrate("[PERSON_7] and [STEP_1]") == "[PERSON_7] and [STEP_1]"


def test_rehydrate_no_tokens():
    m = SessionMap()
    assert m.rehydrate("No tokens here") == "No tokens here"


def test_get_map():
    m = SessionMap()
    m.get_or_create_token("Alex Rivera", "PERSON")
    m.get_or_create_token("jane@example.com", "EMAIL")
    assert m.get_map() == {"[PERSON_1]": "Alex Rivera", "[EMAIL_1]": "jane@example.com"}


def test_round_trip_dict():
    m = SessionMap()
    m.get_or_create_token("Alex Rivera", "PERSON")
    m.get_or_create_token("jane@example.com", "EMAIL")
    restored = SessionMap.from_dict(m.to_dict())
    assert restored.get_map() == m.get_map()
    assert restored.get_or_create_token("Alex Rivera", "PERSON") == "[PERSON_1]"
    assert restored.get_or_create_token("Bea", "PERSON") == "[PERSON_2]"


def test_from_dict_never_reissues_tokens():
    # Counters missing from an old session file must not cause collisions.
    m = SessionMap.from_dict({"reverse": {"[PERSON_3]": "Alex"}})
    assert m.get_or_create_token("Bea", "PERSON") == "[PERSON_4]"
