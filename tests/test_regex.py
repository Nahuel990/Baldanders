"""Tests for regex personal-data detection."""

from baldanders.scrubber.regex import detect


def test_email():
    spans = detect("Send to jane@example.com please")
    assert len(spans) == 1
    assert spans[0].entity_type == "EMAIL"
    assert spans[0].text == "jane@example.com"


def test_credit_card_valid_luhn():
    # 4532015112830366 passes Luhn
    spans = detect("Card 4532015112830366")
    assert len(spans) == 1
    assert spans[0].entity_type == "CREDIT_CARD"


def test_credit_card_invalid_luhn():
    # 1234567890123456 fails Luhn
    spans = detect("Card 1234567890123456")
    cc_spans = [s for s in spans if s.entity_type == "CREDIT_CARD"]
    assert len(cc_spans) == 0


def test_iban():
    spans = detect("Transfer to GB29NWBK60161331926819")
    assert len(spans) == 1
    assert spans[0].entity_type == "IBAN"
    assert spans[0].text == "GB29NWBK60161331926819"


def test_phone_international():
    spans = detect("Call +44 7911 123456")
    assert len(spans) == 1
    assert spans[0].entity_type == "PHONE"


def test_uk_nino():
    spans = detect("NI number AB 12 34 56 C")
    assert len(spans) == 1
    assert spans[0].entity_type == "UK_NINO"


def test_ip_address():
    spans = detect("Server at 192.168.1.100")
    assert len(spans) == 1
    assert spans[0].entity_type == "IP_ADDRESS"


def test_ssn():
    spans = detect("SSN 123-45-6789")
    assert len(spans) == 1
    assert spans[0].entity_type == "SSN"


def test_multiple_entities():
    text = "Email jane@example.com, IBAN GB29NWBK60161331926819, call +44 7911 123456"
    spans = detect(text)
    types = {s.entity_type for s in spans}
    assert "EMAIL" in types
    assert "IBAN" in types
    assert "PHONE" in types


def test_no_personal_data():
    spans = detect("This is a normal sentence with no personal data")
    assert len(spans) == 0


def test_no_overlaps():
    text = "Contact jane@example.com or +44 7911 123456"
    spans = detect(text)
    for i in range(len(spans) - 1):
        assert spans[i].end <= spans[i + 1].start
