"""Tests for deterministic personal-data detection."""

import pytest

from baldanders.scrubber.regex import detect


def types(text, **kw):
    return [(s.entity_type, s.text) for s in detect(text, **kw)]


def test_email():
    spans = detect("Send to jane@example.com please")
    assert len(spans) == 1
    assert spans[0].entity_type == "EMAIL"
    assert spans[0].text == "jane@example.com"


def test_email_unicode():
    assert types("Escribile a josé.pérez@correo.com.ar") == [("EMAIL", "josé.pérez@correo.com.ar")]


def test_credit_card_valid_luhn():
    spans = detect("Card 4532015112830366")
    assert [s.entity_type for s in spans] == ["CREDIT_CARD"]


def test_credit_card_with_spaces():
    assert types("Card 4532 0151 1283 0366 exp 12/29") == [("CREDIT_CARD", "4532 0151 1283 0366")]


def test_credit_card_invalid_luhn():
    spans = detect("Card 1234567890123456")
    assert not [s for s in spans if s.entity_type == "CREDIT_CARD"]


def test_iban():
    spans = detect("Transfer to GB29NWBK60161331926819")
    assert len(spans) == 1
    assert spans[0].entity_type == "IBAN"
    assert spans[0].text == "GB29NWBK60161331926819"


def test_iban_spaced_stops_at_country_length():
    assert types("IBAN GB82 WEST 1234 5698 7654 32 AND THEN") == [("IBAN", "GB82 WEST 1234 5698 7654 32")]


def test_two_ibans_back_to_back():
    found = types("DE89370400440532013000 GB29NWBK60161331926819")
    assert found == [("IBAN", "DE89370400440532013000"), ("IBAN", "GB29NWBK60161331926819")]


def test_iban_bad_checksum():
    assert not types("GB29NWBK60161331926818")


def test_phone_international():
    spans = detect("Call +44 7911 123456")
    assert len(spans) == 1
    assert spans[0].entity_type == "PHONE"


@pytest.mark.parametrize("text, region, number", [
    ("cel 11 4567-8901", "AR", "11 4567-8901"),
    ("call (415) 555-2671 tonight", "US", "(415) 555-2671"),
    ("Ruf mich an: 030 12345678", "DE", "030 12345678"),
    ("appelle-moi au 06 12 34 56 78", "FR", "06 12 34 56 78"),
    ("ligue (11) 98765-4321", "BR", "(11) 98765-4321"),
    ("call 98765 43210", "IN", "98765 43210"),
    ("电话 138 1234 5678", "CN", "138 1234 5678"),
])
def test_phone_national_formats(text, region, number):
    assert ("PHONE", number) in types(text, regions=[region])


@pytest.mark.parametrize("number", [
    "+54 9 11 4567-8901", "+1 415 555 2671", "+49 30 12345678", "+33 6 12 34 56 78",
    "+55 11 98765-4321", "+91 98765 43210", "+86 138 1234 5678", "+81 90-1234-5678",
    "+234 803 123 4567", "+61 412 345 678",
])
def test_phone_international_any_region(number):
    assert ("PHONE", number) in types(f"my number is {number}.")


def test_uk_nino():
    spans = detect("NI number AB 12 34 56 C")
    assert len(spans) == 1
    assert spans[0].entity_type == "UK_NINO"


def test_ip_address():
    assert types("Server at 192.168.1.100") == [("IP_ADDRESS", "192.168.1.100")]


def test_ipv6():
    assert types("host 2001:db8:85a3::8a2e:370:7334 is up") == [("IP_ADDRESS", "2001:db8:85a3::8a2e:370:7334")]


def test_mac_address():
    assert types("MAC 00:1A:2B:3C:4D:5E") == [("MAC_ADDRESS", "00:1A:2B:3C:4D:5E")]


def test_ssn():
    assert types("SSN 536-90-4399") == [("SSN", "536-90-4399")]


def test_ssn_reserved_number_not_flagged():
    # 000 area numbers are never issued.
    assert not types("ref 000-12-3456")


# Valid example identifiers (from python-stdnum's documentation), each in a
# sentence with the keyword people would naturally write next to it.
WORLDWIDE = [
    ("US", "ITIN 912-90-3456", "912-90-3456"),
    ("CA", "my SIN is 123-456-782", "123-456-782"),
    ("BR", "CPF 390.533.447-05", "390.533.447-05"),
    ("BR", "CPF 39053344705", "39053344705"),
    ("BR", "CNPJ 16.727.230/0001-97", "16.727.230/0001-97"),
    ("AR", "CUIT 20-05536168-2", "20-05536168-2"),
    ("AR", "CUIL 20267565393", "20267565393"),
    ("AR", "DNI 20.123.456", "20.123.456"),
    ("AR", "mi documento es 20123456", "20123456"),
    ("CL", "RUT 76.086.428-5", "76.086.428-5"),
    ("MX", "CURP BOXW310820HNERXN09", "BOXW310820HNERXN09"),
    ("MX", "RFC GODE561231GR8", "GODE561231GR8"),
    ("CO", "NIT 213.123.432-1", "213.123.432-1"),
    ("EC", "cédula 171430710-3", "171430710-3"),
    ("UY", "RUT 21-100342-001-7", "21-100342-001-7"),
    ("ES", "DNI 54362315-K", "54362315-K"),
    ("ES", "NIE X-2482300W", "X-2482300W"),
    ("PT", "NIF 501 964 843", "501 964 843"),
    ("FR", "NIR 2 95 10 99 126 111 93", "2 95 10 99 126 111 93"),
    ("IT", "codice fiscale RCCMNL83S18D969H", "RCCMNL83S18D969H"),
    ("DE", "Steuer-ID 36 574 261 809", "36 574 261 809"),
    ("NL", "BSN 111222333", "111222333"),
    ("BE", "rijksregister 85.07.30-033.28", "85.07.30-033.28"),
    ("PL", "PESEL 44051401359", "44051401359"),
    ("SE", "personnummer 880320-0016", "880320-0016"),
    ("NO", "fødselsnummer 151086 95088", "151086 95088"),
    ("DK", "CPR 211062-5629", "211062-5629"),
    ("FI", "henkilötunnus 131052-308T", "131052-308T"),
    ("CH", "AHV 756.9217.0769.85", "756.9217.0769.85"),
    ("AT", "SVNR 1237 010180", "1237 010180"),
    ("IE", "PPS 6433435F", "6433435F"),
    ("CZ", "rodné číslo 710319/2745", "710319/2745"),
    ("RO", "CNP 1630615123457", "1630615123457"),
    ("HR", "OIB 33392005961", "33392005961"),
    ("TR", "TC Kimlik No 17291716060", "17291716060"),
    ("IL", "teudat zehut 39337423", "39337423"),
    ("IN", "Aadhaar 2341 2341 2346", "2341 2341 2346"),
    ("IN", "PAN ACUPA7085R", "ACUPA7085R"),
    ("PK", "CNIC 34201-0891231-8", "34201-0891231-8"),
    ("CN", "身份证 360426199101010071", "360426199101010071"),
    ("HK", "HKID A123456(3)", "A123456(3)"),
    ("TW", "身分證 A123456789", "A123456789"),
    ("JP", "マイナンバー 6214 9832 0257", "6214 9832 0257"),
    ("KR", "주민등록번호 971013-9019902", "971013-9019902"),
    ("MY", "MyKad 770305-02-1234", "770305-02-1234"),
    ("SG", "NRIC S1234567D", "S1234567D"),
    ("ID", "NIK 3171011708450001", "3171011708450001"),
    ("TH", "national ID 3100600445635", "3100600445635"),
    ("AU", "TFN 123 456 782", "123 456 782"),
    ("NZ", "IRD 49-098-576", "49-098-576"),
    ("ZA", "ID number 7503305044089", "7503305044089"),
    ("RU", "ИНН 1234567894", "1234567894"),
]


@pytest.mark.parametrize("country, text, value", WORLDWIDE, ids=[f"{c}-{v}" for c, _, v in WORLDWIDE])
def test_worldwide_identifiers(country, text, value):
    found = [s.text for s in detect(text)]
    assert value in found, f"{country}: {value!r} not detected in {text!r} (got {found})"


@pytest.mark.parametrize("text", [
    "ref 390.533.447-06",        # CPF shape, bad check digits
    "ref 20-05536168-3",         # CUIT
    "ref 54362315-J",            # DNI
    "ref 2 95 10 99 126 111 94", # NIR
    "ref 360426199101010072",    # Chinese resident ID
    "ref 2341 2341 2347",        # Aadhaar
])
def test_bad_checksums_rejected(text):
    assert not types(text)


def test_mistyped_id_next_to_keyword_still_scrubbed():
    # Safer to over-scrub: the keyword says it's an ID even if a digit is off.
    assert [t for t, _ in types("DNI 54362315-J")] == ["NATIONAL_ID"]


def test_digit_ids_need_context_unless_paranoid():
    # A valid PESEL with no keyword nearby is just a number...
    assert not types("order 44051401359 shipped")
    # ...unless paranoid mode is on.
    assert ("PL_PESEL", "44051401359") in types("order 44051401359 shipped", paranoid=True)


@pytest.mark.parametrize("text, value", [
    ("my passport number is X1234567", "X1234567"),
    ("número de pasaporte: AAB123456", "AAB123456"),
    ("Reisepass C01X00T47", "C01X00T47"),
])
def test_passport_with_context(text, value):
    assert ("PASSPORT", value) in types(text)


@pytest.mark.parametrize("text, value", [
    ("DOB 12/03/1985", "12/03/1985"),
    ("born on March 12, 1985", "March 12, 1985"),
    ("Nací el 12 de marzo de 1985", "12 de marzo de 1985"),
    ("geboren am 12.03.1985", "12.03.1985"),
    ("出生日期 1985年3月12日", "1985年3月12日"),
])
def test_date_of_birth(text, value):
    assert ("DATE_OF_BIRTH", value) in types(text)


@pytest.mark.parametrize("text, entity", [
    ("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE", "API_KEY"),
    ("token ghp_" + "a" * 36, "API_KEY"),
    ("key: sk-ant-api03-" + "x" * 40, "API_KEY"),
    ("stripe sk_live_" + "4" * 24, "API_KEY"),
    ("password: hunter2xyz", "PASSWORD"),
    ("contraseña = MiClave2024!", "PASSWORD"),
    ("postgres://admin:s3cretpass@db.internal:5432/app", "PASSWORD"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "JWT"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "PRIVATE_KEY"),
])
def test_secrets(text, entity):
    assert entity in [s.entity_type for s in detect(text)]


def test_database_url_scrubs_only_password():
    assert types("postgres://admin:s3cretpass@db:5432/app") == [("PASSWORD", "s3cretpass")]


def test_crypto_wallets():
    assert ("CRYPTO_WALLET", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2") in types("send to 1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
    assert ("CRYPTO_WALLET", "0x52908400098527886E0F7030069857D2E4169EE7") in types("eth 0x52908400098527886E0F7030069857D2E4169EE7")


def test_multiple_entities():
    text = "Email jane@example.com, IBAN GB29NWBK60161331926819, call +44 7911 123456"
    assert {s.entity_type for s in detect(text)} == {"EMAIL", "IBAN", "PHONE"}


def test_no_personal_data():
    assert detect("This is a normal sentence with no personal data") == []


@pytest.mark.parametrize("text, address", [
    ("vivo en Av. Corrientes 1234, Buenos Aires.", "Av. Corrientes 1234"),
    ("Av. 9 de Julio 1234", "Av. 9 de Julio 1234"),
    ("Calle Mayor 10, 3º B, 28013 Madrid", "Calle Mayor 10"),
    ("Calle 50 # 10-20, Bogotá", "Calle 50 # 10-20"),
    ("moro na Rua Augusta 1500, São Paulo", "Rua Augusta 1500"),
    ("Avenida Paulista, 1578", "Avenida Paulista, 1578"),
    ("Via del Corso 12, Roma", "Via del Corso 12"),
    ("I live at 221B Baker Street, London", "221B Baker Street"),
    ("ship to 1600 Pennsylvania Ave NW, Washington", "1600 Pennsylvania Ave NW"),
    ("my address: 42 MG Road, Bengaluru", "42 MG Road"),
    ("j'habite 12 rue de Rivoli, 75001 Paris", "12 rue de Rivoli"),
    ("ich wohne in der Hauptstraße 5, 10115 Berlin", "Hauptstraße 5"),
    ("Berliner Straße 12a", "Berliner Straße 12a"),
    ("Keizersgracht 123, Amsterdam", "Keizersgracht 123"),
    ("Storgatan 4, Stockholm", "Storgatan 4"),
    ("Vesterbrogade 10, København", "Vesterbrogade 10"),
    ("ул. Ленина, д. 5", "ул. Ленина, д. 5"),
    ("Jl. Sudirman No. 5, Jakarta", "Jl. Sudirman No. 5"),
    ("住在北京市朝阳区建国路88号。", "北京市朝阳区建国路88号"),
    ("東京都千代田区丸の内1丁目9番2号", "東京都千代田区丸の内1丁目9番2号"),
    ("서울 강남구 테헤란로 152", "테헤란로 152"),
])
def test_street_addresses(text, address):
    assert ("ADDRESS", address) in types(text)


@pytest.mark.parametrize("text", [
    "Via the API 3 times", "We ran Calle tests 5 times", "Avenue Q is a musical", "Route 66 diner",
    "Spring 2024 release", "Delegate 5 tasks", "Bring 3 apples", "Brigade 3 arrived",
    "학교로 3시에 가요", "집으로 2명이 왔다", "Chapter 5 Street Fighter review",
])
def test_street_address_false_positives(text):
    assert types(text) == []


# Text that must come through untouched: code, maths, dates, versions, amounts.
CLEAN = [
    "SELECT * FROM orders WHERE id = 12345678 AND total > 1000.50;",
    "arr[1::2] and std::vector<int> v; a::b::c",
    "Upgrade from version 1.2.3.4 to 2.0.0",
    "The meeting is on 2024-01-15 at 14:30:00",
    "Order 2024-01-15 shipped, tracking 123456789",
    "Revenue grew 12.345.678 dollars in Q3",
    "The answer is 3.14159265358979",
    "Use port 8080 and timeout 30000ms",
    "for i in range(10000000): total += i * 2",
    "commit 78e5eb0a1b2c3d4e5f60718293a4b5c6d7e8f901",
    "uuid 123e4567-e89b-12d3-a456-426614174000",
    "Population: 8,045,311,447 people in 2023",
    "localhost is 127.0.0.1 and 0.0.0.0 binds all",
]


@pytest.mark.parametrize("text", CLEAN)
def test_no_false_positives(text):
    assert types(text, regions=["US"]) == []


def test_no_overlaps():
    text = "Contact jane@example.com or +44 7911 123456, DNI 20.123.456, CUIT 20-05536168-2"
    spans = detect(text)
    for a, b in zip(spans, spans[1:]):
        assert a.end <= b.start
