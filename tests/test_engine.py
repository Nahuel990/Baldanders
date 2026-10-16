"""Tests for the scrub engine (regex + config + mapping integration)."""

from baldanders.config import Config
from baldanders.mapping import SessionMap
from baldanders.scrubber.engine import ScrubEngine
from baldanders.scrubber.regex import Span


def engine(**config):
    m = SessionMap()
    return m, ScrubEngine(m, config=Config(**config))


def test_scrub_email():
    m, e = engine()
    result = e.scrub("Contact jane@example.com")
    assert result == "Contact [EMAIL_1]"


def test_scrub_iban():
    m, e = engine()
    assert e.scrub("Pay to GB29NWBK60161331926819") == "Pay to [IBAN_1]"


def test_scrub_phone():
    m, e = engine()
    assert e.scrub("Call +44 7911 123456") == "Call [PHONE_1]"


def test_scrub_multiple():
    m, e = engine()
    result = e.scrub("Email jane@example.com, IBAN GB29NWBK60161331926819")
    assert result == "Email [EMAIL_1], IBAN [IBAN_1]"


def test_no_personal_data_unchanged():
    m, e = engine()
    text = "This is a normal sentence"
    assert e.scrub(text) == text


def test_round_trip():
    m, e = engine()
    original = "Send 10k to jane@example.com from GB29NWBK60161331926819"
    scrubbed = e.scrub(original)
    assert scrubbed == "Send 10k to [EMAIL_1] from [IBAN_1]"
    ai_response = "Transfer of 10k to [EMAIL_1] from [IBAN_1] processed."
    assert m.rehydrate(ai_response) == "Transfer of 10k to jane@example.com from GB29NWBK60161331926819 processed."


def test_consistency_across_messages():
    m, e = engine()
    r1 = e.scrub("Email jane@example.com about the transfer")
    r2 = e.scrub("Confirm with jane@example.com it went through")
    assert "[EMAIL_1]" in r1 and "[EMAIL_1]" in r2


def test_credit_card_scrubbed():
    m, e = engine()
    assert e.scrub("Card number 4532015112830366") == "Card number [CREDIT_CARD_1]"


def test_spanish_message():
    m, e = engine(regions=["AR"])
    result = e.scrub("Hola, mi DNI es 20.123.456, mi CUIL 20-05536168-2 y mi cel 11 4567-8901")
    assert result == "Hola, mi DNI es [NATIONAL_ID_1], mi CUIL [AR_CUIT_1] y mi cel [PHONE_1]"


def test_denylist_case_insensitive_whole_word():
    m, e = engine(denylist={"Project Falcon": "PROJECT"})
    assert e.scrub("status of project falcon?") == "status of [PROJECT_1]?"
    # Not inside another word
    assert e.scrub("Project Falconry") == "Project Falconry"


def test_denylist_overlapping_regex_does_not_corrupt_text():
    """Regression: overlapping spans used to garble the output."""
    m, e = engine(denylist={"jane@example.com backup": "CONTACT"})
    result = e.scrub("write to jane@example.com backup now")
    assert result == "write to [CONTACT_1] now"


def test_custom_pattern_overlapping_regex():
    m, e = engine(custom_patterns={"customer_id": r"CID-\d{8}"})
    assert e.scrub("ticket CID-12345678 for +44 7911 123456") == "ticket [CUSTOMER_ID_1] for [PHONE_1]"


def test_custom_pattern_capture_group():
    m, e = engine(custom_patterns={"employee": r"EMP:(\d{5})"})
    assert e.scrub("badge EMP:55123") == "badge EMP:[EMPLOYEE_1]"


def test_invalid_custom_pattern_reported():
    m, e = engine(custom_patterns={"bad": "("})
    assert e.config_errors and "bad" in e.config_errors[0]


def test_allowlist():
    m, e = engine(allowlist=["ops@company.com"])
    assert e.scrub("page ops@company.com or jane@example.com") == "page ops@company.com or [EMAIL_1]"


def test_ner_spans_merge_without_overlap(monkeypatch):
    m, e = engine()
    monkeypatch.setattr(e._ner, "detect", lambda text: [
        Span(0, 10, "PERSON", "Ana García"),
        Span(14, 30, "ADDRESS", "ana@example.com,"),  # bogus overlap with the email
    ])
    result = e.scrub("Ana García at ana@example.com, hi")
    # Overlaps merge into one span; the email label wins as it covers most of it.
    assert result == "[PERSON_1] at [EMAIL_1] hi"
    assert m.get_map()["[EMAIL_1]"] == "ana@example.com,"


def test_partial_regex_match_cannot_split_an_address(monkeypatch):
    """The model's address must swallow a house number a regex rule also matched."""
    m, e = engine(custom_patterns={"num": r"\b1234\b"})
    monkeypatch.setattr(e._ner, "detect", lambda text: [Span(8, 27, "ADDRESS", "Av. Corrientes 1234")])
    assert e.scrub("vivo en Av. Corrientes 1234, CABA") == "vivo en [ADDRESS_1], CABA"


def test_paranoid_mode():
    m, e = engine(paranoid=True)
    assert e.scrub("ref 44051401359") == "ref [PL_PESEL_1]"
