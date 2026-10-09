"""Tests for the scrub engine (regex + mapping integration)."""

from baldanders.mapping import SessionMap
from baldanders.scrubber.engine import ScrubEngine


def test_scrub_email():
    m = SessionMap()
    e = ScrubEngine(m)
    result = e.scrub("Contact jane@example.com")
    assert "[EMAIL_1]" in result
    assert "jane@example.com" not in result


def test_scrub_iban():
    m = SessionMap()
    e = ScrubEngine(m)
    result = e.scrub("Pay to GB29NWBK60161331926819")
    assert "[IBAN_1]" in result
    assert "GB29NWBK60161331926819" not in result


def test_scrub_phone():
    m = SessionMap()
    e = ScrubEngine(m)
    result = e.scrub("Call +44 7911 123456")
    assert "[PHONE_1]" in result
    assert "+44 7911 123456" not in result


def test_scrub_multiple():
    m = SessionMap()
    e = ScrubEngine(m)
    result = e.scrub("Email jane@example.com, IBAN GB29NWBK60161331926819")
    assert "[EMAIL_1]" in result
    assert "[IBAN_1]" in result
    assert "jane@example.com" not in result
    assert "GB29NWBK60161331926819" not in result


def test_no_pii_unchanged():
    m = SessionMap()
    e = ScrubEngine(m)
    text = "This is a normal sentence"
    assert e.scrub(text) == text


def test_round_trip():
    """Scrub then rehydrate should recover the original meaning."""
    m = SessionMap()
    e = ScrubEngine(m)
    original = "Send 10k to jane@example.com from GB29NWBK60161331926819"
    scrubbed = e.scrub(original)

    # Simulate AI response using the tokens
    ai_response = f"Transfer of 10k to {m.get_or_create_token('jane@example.com', 'EMAIL')} from {m.get_or_create_token('GB29NWBK60161331926819', 'IBAN')} processed."
    rehydrated = m.rehydrate(ai_response)

    assert "jane@example.com" in rehydrated
    assert "GB29NWBK60161331926819" in rehydrated


def test_consistency_across_messages():
    """Same PII in different messages gets the same token."""
    m = SessionMap()
    e = ScrubEngine(m)
    r1 = e.scrub("Email jane@example.com about the transfer")
    r2 = e.scrub("Confirm with jane@example.com it went through")
    # Both should have [EMAIL_1], not [EMAIL_1] and [EMAIL_2]
    assert r1.count("[EMAIL_1]") == 1
    assert r2.count("[EMAIL_1]") == 1


def test_credit_card_scrubbed():
    m = SessionMap()
    e = ScrubEngine(m)
    result = e.scrub("Card number 4532015112830366")
    assert "[CREDIT_CARD_1]" in result
    assert "4532015112830366" not in result
