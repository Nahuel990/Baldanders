"""Layer 1 — deterministic personal-data detection.

Every rule is a candidate regex plus, where one exists, a checksum or
structure validator (mostly from python-stdnum). Identifiers that are just a
run of digits with no distinctive shape (a German tax ID, a Dutch BSN, an
Argentine DNI, ...) also need a nearby keyword such as "DNI" or "BSN", unless
paranoid mode is on. Phone numbers go through Google's libphonenumber.
"""

from __future__ import annotations

import hashlib
import importlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable, Sequence


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    entity_type: str
    text: str


Validator = Callable[[str], bool]


@dataclass(frozen=True)
class Rule:
    entity: str
    pattern: re.Pattern[str]
    validate: Validator | None = None
    # Keywords that must appear near the match (lowercase, accents stripped).
    context: tuple[str, ...] = ()
    # When False, the rule only fires in paranoid mode or with context present.
    always: bool = True
    group: int = 0
    # Skip the match when the text right before it matches this (e.g. "version ").
    not_after: re.Pattern[str] | None = None
    _context_re: re.Pattern[str] | None = field(default=None, compare=False)


# --- Validators ------------------------------------------------------------

def _stdnum(*modules: str) -> Validator:
    """Validator that passes if any of the given stdnum modules accepts it."""
    def check(value: str) -> bool:
        for name in modules:
            try:
                mod = importlib.import_module(f"stdnum.{name}")
                if mod.is_valid(value):
                    return True
            except Exception:
                continue
        return False
    return check


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


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _credit_card(value: str) -> bool:
    d = _digits(value)
    return 13 <= len(d) <= 19 and d[0] in "23456" and _luhn_check(d)


# ISO 13616 IBAN lengths per country (SWIFT registry).
_IBAN_LENGTHS = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22,
    "BH": 22, "BI": 27, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24,
    "DE": 22, "DJ": 27, "DK": 18, "DO": 28, "EE": 20, "EG": 29, "ES": 24, "FI": 18,
    "FK": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27,
    "GT": 28, "HN": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IQ": 23, "IS": 26,
    "IT": 27, "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20,
    "LU": 20, "LV": 21, "LY": 25, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MN": 20,
    "MR": 27, "MT": 31, "MU": 30, "NI": 28, "NL": 18, "NO": 15, "OM": 23, "PK": 24,
    "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "RU": 33, "SA": 24,
    "SC": 31, "SD": 18, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "SO": 23, "ST": 25,
    "SV": 28, "TL": 23, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24, "XK": 20,
    "YE": 30,
}


def _iban_mod97(compact: str) -> bool:
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


def _ipv4(value: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified or ip == ipaddress.IPv4Address("255.255.255.255"))


def _ipv6(value: str) -> bool:
    # Skip short forms like "1::2" (Python slices, C++ scopes) — not addresses
    # anyone needs hidden.
    if len(value) < 8 or len([g for g in value.split(":") if g]) < 3:
        return False
    try:
        ip = ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return not (ip.is_loopback or ip.is_unspecified)


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58check(value: str) -> bool:
    n = 0
    for ch in value:
        idx = _B58.find(ch)
        if idx < 0:
            return False
        n = n * 58 + idx
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\x00" * (len(value) - len(value.lstrip("1"))) + raw
    if len(raw) < 5:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def _uk_nino(value: str) -> bool:
    v = re.sub(r"\s", "", value).upper()
    return v[:2] not in {"BG", "GB", "KN", "NK", "NT", "TN", "ZZ"}


def _singapore_nric(value: str) -> bool:
    v = value.upper()
    weights = (2, 7, 6, 5, 4, 3, 2)
    total = sum(int(d) * w for d, w in zip(v[1:8], weights))
    if v[0] in "TG":
        total += 4
    elif v[0] == "M":
        total += 3
    tables = {
        "ST": "JZIHGFEDCBA",
        "FG": "XWUTRQPNMLK",
        "M": "KLJNPQRTUWX",
    }
    for prefixes, table in tables.items():
        if v[0] in prefixes:
            return table[total % 11] == v[8]
    return False


def _hkid(value: str) -> bool:
    v = re.sub(r"[()\s]", "", value).upper()
    letters, digits, check = re.match(r"([A-Z]{1,2})(\d{6})([0-9A])", v).groups()
    if len(letters) == 1:
        letters = " " + letters
    total = 0
    for i, ch in enumerate(letters):
        val = 36 if ch == " " else ord(ch) - 55
        total += val * (9 - i)
    for i, ch in enumerate(digits):
        total += int(ch) * (7 - i)
    expected = (11 - total % 11) % 11
    return check == ("A" if expected == 10 else str(expected))


_TW_LETTERS = "ABCDEFGHJKLMNPQRSTUVXYWZIO"


def _taiwan_id(value: str) -> bool:
    v = value.upper()
    code = _TW_LETTERS.index(v[0]) + 10
    nums = [code // 10, code % 10] + [int(c) for c in v[1:]]
    weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]
    return sum(n * w for n, w in zip(nums, weights)) % 10 == 0


def _date_like(value: str) -> bool:
    return bool(re.search(r"\d", value))


# --- Context keywords ------------------------------------------------------

PHONE_CONTEXT = (
    "phone", "tel", "tel.", "telephone", "mobile", "cell", "call me", "whatsapp", "sms",
    "telefono", "movil", "celular", "cel", "fijo", "telefone", "telemovel",
    "telephone", "portable", "telefon", "handy", "mobil", "cellulare", "telefoon",
    "gsm", "puhelin", "telefonnummer", "телефон", "моб", "電話", "电话", "手机", "携帯",
    "전화", "휴대폰", "هاتف", "جوال", "फोन", "मोबाइल", "telefonu", "điện thoại",
)

DOB_CONTEXT = (
    "born", "birth", "birthday", "dob", "d.o.b", "date of birth", "nacimiento", "naci",
    "nacido", "nacida", "cumpleanos", "nascimento", "nasceu", "naissance", "ne le",
    "nee le", "geboren", "geburtsdatum", "geburtstag", "nascita", "nato il", "nata il",
    "geboortedatum", "urodzenia", "fodt", "fodd", "syntynyt", "dogum", "rodenja",
    "дата рождения", "родился", "родилась", "出生", "生日", "生年月日", "생년월일",
    "تاريخ الميلاد", "जन्म",
)

NATIONAL_ID_CONTEXT = (
    "dni", "d.n.i", "documento", "documento de identidad", "cedula", "c.c", "c.i",
    "cedula de identidad", "identificacion", "id number", "id no", "id #", "identity",
    "national id", "identity card", "id card", "carte d'identite", "cni",
    "personalausweis", "ausweis", "carta d'identita", "bilhete de identidade",
    "rg", "registro geral", "rut", "cuil", "cuit", "nit", "nif", "nie", "curp",
    "ruc", "rfc", "bsn", "burgerservicenummer", "sofinummer", "pesel", "personnummer",
    "fodselsnummer", "fnr", "cpr", "henkilotunnus", "hetu", "ahv", "avs",
    "sozialversicherungsnummer", "svnr", "pps", "cnp", "egn", "oib", "amka",
    "isikukood", "asmens kodas", "rodne cislo", "emso", "tc kimlik", "kimlik",
    "teudat zehut", "aadhaar", "aadhar", "cnic", "nric", "mykad", "nik", "ktp",
    "my number", "tax id", "tax number", "taxpayer", "tin", "tfn", "tax file",
    "ird", "steuer-id", "steuerid", "steuer id", "steuernummer", "identifikationsnummer",
    "idnr", "codice fiscale", "nir", "numero de securite sociale", "securite sociale",
    "social security", "ssn", "social insurance", "nhs", "nino",
    "national insurance", "inn", "snils", "rntrc", "medicare", "nin",
    "інн", "инн", "снилс", "паспорт", "身份证", "身分證", "マイナンバー", "個人番号",
    "주민등록번호", "رقم الهوية", "الرقم القومي", "आधार", "kimlik no", "cpf", "cnpj",
)

PASSPORT_CONTEXT = (
    "passport", "pasaporte", "passeport", "reisepass", "pass-nr", "passnummer",
    "passaporto", "passaporte", "paspoort", "paszport", "passi", "pasaport",
    "паспорт", "护照", "護照", "パスポート", "여권", "جواز السفر", "पासपोर्ट",
)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


# Letters that don't decompose into base letter + accent under NFKD.
_FOLD = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ł": "l", "Ł": "l",
                       "đ": "d", "Đ": "d", "ð": "d", "þ": "th", "ı": "i", "œ": "oe"})


def _normalize(text: str) -> str:
    return _strip_accents(text.translate(_FOLD)).casefold()


def _keyword_regex(keywords: Iterable[str]) -> re.Pattern[str]:
    parts = []
    for kw in sorted({_normalize(k) for k in keywords}, key=len, reverse=True):
        escaped = re.escape(kw)
        # Word boundaries only make sense for scripts that separate words with spaces.
        if kw[0].isascii() and kw[0].isalnum():
            escaped = r"(?<![^\W_])" + escaped
        if kw[-1].isascii() and kw[-1].isalnum():
            escaped = escaped + r"(?![^\W_])"
        parts.append(escaped)
    return re.compile("|".join(parts))


# --- Rules -----------------------------------------------------------------

# Boundaries that treat letters, digits and underscores as word characters.
_L = r"(?<![\w])"
_R = r"(?![\w])"
_LD = r"(?<![\w.,/-])"  # left boundary for numbers: not inside another number
_RD = r"(?![\w]|[.,/-]\d)"

_I = re.IGNORECASE


def _r(pattern: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(pattern, flags)


_RULES: list[Rule] = [
    # ---- Secrets (scrubbed first: they're never safe to send) ----
    Rule("PRIVATE_KEY", _r(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----")),
    Rule("API_KEY", _r(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b")),
    Rule("API_KEY", _r(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    Rule("API_KEY", _r(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b")),
    Rule("API_KEY", _r(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    Rule("API_KEY", _r(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    Rule("API_KEY", _r(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    Rule("API_KEY", _r(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    Rule("API_KEY", _r(r"\bsk-(?:ant-|proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}")),
    Rule("API_KEY", _r(r"\bhf_[A-Za-z0-9]{30,}\b")),
    Rule("API_KEY", _r(r"\bnpm_[A-Za-z0-9]{36}\b")),
    Rule("JWT", _r(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    Rule("PASSWORD", _r(r"[a-z][a-z0-9+.-]*://[^\s:/@]+:([^\s@/]+)@", _I), group=1),
    Rule("PASSWORD", _r(
        r"(?:password|passwd|pwd|passcode|secret|token|api[_ -]?key|access[_ -]?key|"
        r"contrase[nñ]a|clave|senha|mot de passe|passwort|kennwort|wachtwoord|"
        r"haslo|hasło|parola|пароль|密码|密碼|パスワード|비밀번호)"
        r"[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{4,})",
        _I,
    ), group=1),

    # ---- Contact ----
    Rule("EMAIL", _r(r"(?<![\w.+%-])[\w.+%-]+@(?:[^\W_](?:[\w-]*[^\W_])?\.)+[^\W\d_]{2,}(?![\w-])")),

    # ---- Street addresses (street type + name + house number) ----
    # Street type first: Av. Corrientes 1234 · Calle 50 # 10-20 · Rua Augusta, 1500 · Via Roma 10 · ул. Ленина, д. 5
    Rule("ADDRESS", _r(
        r"\b(?:Av(?:da)?\.|Avenida|Avenue|Calle|C/|Carrera|Cra\.|Diagonal|Transversal|Rua|R\.|"
        r"Boulevard|Bv\.|Blvd\.|Via|Viale|Piazza|Piazzale|Corso|Vicolo|Plaza|Pasaje|Camino|Ruta|"
        r"Paseo|Travessa|Alameda|Rodovia|Estrada|Strada|Calea|Bulevardul|ulica|ul\.|aleja|Jalan|Jl\.|"
        r"улица|ул\.|проспект|пр-т|переулок|пер\.|бульвар|шоссе)"
        # Street name: starts with a capital or digit, optionally after a connector (de, del, da, ...)
        r"\s+(?:(?:de|del|de la|de los|della|delle|dei|do|da|dos|das|di|des|du|la|le)\s+)?"
        r"[A-ZÀ-ÝА-ЯЁ0-9][\w'’.-]*(?:\s+[\w'’.-]+){0,4}?"
        r",?\s*(?:N[º°o]\.?\s*|#\s*|д\.\s*|дом\s+)?\d{1,5}[A-Za-z]?(?:-\d{1,4})?"
        r"(?:\s*(?:#|No\.?|N[º°])\s*\d{1,4}(?:-\d{1,4})?)?(?![\w])"
    )),
    # House number first: 221B Baker Street · 1600 Pennsylvania Ave NW · 42 MG Road
    Rule("ADDRESS", _r(
        r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'’.-]*\s+){1,4}"
        r"(?:Street|St\.|Road|Rd\.?|Avenue|Ave\.?|Lane|Ln\.?|Drive|Dr\.|Boulevard|Blvd\.?|Way|Court|Ct\.?|"
        r"Place|Pl\.|Terrace|Close|Crescent|Highway|Hwy\.?|Square|Sq\.|Parkway|Pkwy\.?|Circle|Trail|Mews|Gardens|Grove)"
        r"(?:\s+(?:NE|NW|SE|SW|N|S|E|W)\b)?"
    )),
    # French style: 12 rue de Rivoli · 3 bis avenue Foch
    Rule("ADDRESS", _r(
        r"\b\d{1,4}(?:\s?(?:bis|ter))?,?\s+(?:rue|avenue|boulevard|bd|place|chemin|allée|impasse|quai|cours|passage|route)"
        r"(?:\s+(?:d[eu']|des|la|le|l'|du)?\s*[A-ZÀ-Ý][\w'’-]*){1,4}"
    )),
    # Germanic / Nordic compounds: Hauptstraße 5 · Berliner Str. 12 · Keizersgracht 123 · Storgatan 4 · Vesterbrogade 10
    Rule("ADDRESS", _r(
        # ("-ring", "-gate", "-tie" are left out: Spring, Delegate, Smoothie...)
        r"\b(?:[A-ZÄÖÜÅÆØ][\wäöüßåæø-]*"
        r"(?:straße|strasse|str\.|weg|gasse|platz|allee|damm|ufer|chaussee|straat|laan|gracht|plein|kade|"
        r"singel|gatan|vägen|gata|(?<!Bri)(?<!Rene)gade|vej|veien|vegen|katu)"
        r"|[A-ZÄÖÜ][\wäöüß-]*\s+(?:Straße|Strasse|Str\.|Weg|Gasse|Platz|Allee|Ring|Damm|Ufer|Chaussee))"
        r"\s+\d{1,5}[a-zA-Z]?\b"
    )),
    # Chinese: 北京市朝阳区建国路88号 (leading verbs like 住在 are left out of the span)
    Rule("ADDRESS", _r(
        r"(?:住在|住址|地址|位于|位於|在)?"
        r"((?:[一-鿿]{2,6}(?:省|市|自治区))?(?:[一-鿿]{1,6}?(?:市|区|县|區|縣))*"
        r"[一-鿿]{1,8}?(?:路|街|道|巷|弄|大道)\d{1,5}(?:号|號)(?:\d{1,4}(?:楼|室|樓))*)"
    ), group=1),
    # Japanese block addresses: 千代田区丸の内1丁目9番2号
    Rule("ADDRESS", _r(r"[一-鿿぀-ヿ]{0,10}\d{1,3}丁目\d{1,4}番地?(?:\d{1,4}号)?")),
    # Korean road addresses: 테헤란로 152 · 강남대로94길 20
    # ("으로" is the everyday particle "toward", not a road.)
    # ("으로" is the everyday particle "toward", and a counter after the number
    # — 3시 "3 o'clock", 2명 "2 people" — means it isn't a house number.)
    Rule("ADDRESS", _r(r"[가-힣]{2,}(?<!으)(?:로|길)(?:\s?\d{1,4}(?:번?길))?\s?\d{1,4}(?:-\d{1,4})?(?![\d]|\s?[시분초명개번월일년원살층대권회주])")),

    # ---- Financial ----
    # Unbroken, or in the groupings printed on real cards (4-4-4-4[-3], Amex 4-6-5, Diners 4-6-4).
    Rule("CREDIT_CARD", _r(_LD + r"(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{4}(?:\1\d{3})?|\d{4}([ -])\d{6}\2\d{4,5})" + _RD), _credit_card),
    # IBAN candidates are trimmed to their country's length in _iban_spans().

    # ---- Network / device ----
    Rule("IP_ADDRESS", _r(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)"), _ipv4,
         not_after=_r(r"(?:version|ver\.?|v|release|build|versi[oó]n|vers[aã]o)\s*$", _I)),
    Rule("IP_ADDRESS", _r(r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}(?:[0-9A-Fa-f]{1,4}|(?:\d{1,3}\.){3}\d{1,3})?(?![\w:])"), _ipv6),
    Rule("MAC_ADDRESS", _r(r"\b[0-9A-Fa-f]{2}(?:([:-])[0-9A-Fa-f]{2})(?:\1[0-9A-Fa-f]{2}){4}\b")),
    Rule("CRYPTO_WALLET", _r(r"\bbc1[ac-hj-np-z02-9]{11,71}\b")),
    Rule("CRYPTO_WALLET", _r(r"\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b"), _base58check),
    Rule("CRYPTO_WALLET", _r(r"\b0x[a-fA-F0-9]{40}\b")),

    # ---- Americas ----
    Rule("SSN", _r(_LD + r"\d{3}[- ]\d{2}[- ]\d{4}" + _RD), _stdnum("us.ssn", "us.itin")),
    Rule("SSN", _r(_LD + r"\d{9}" + _RD), _stdnum("us.ssn", "us.itin"), ("ssn", "social security", "itin", "seguro social"), always=False),
    Rule("CA_SIN", _r(_LD + r"\d{3}[- ]\d{3}[- ]\d{3}" + _RD), _stdnum("ca.sin"), ("sin", "social insurance", "nas", "assurance sociale"), always=False),
    Rule("BR_CPF", _r(_LD + r"\d{3}\.\d{3}\.\d{3}-\d{2}" + _RD), _stdnum("br.cpf")),
    Rule("BR_CPF", _r(_LD + r"\d{11}" + _RD), _stdnum("br.cpf"), ("cpf",), always=False),
    Rule("BR_CNPJ", _r(_LD + r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}" + _RD), _stdnum("br.cnpj")),
    Rule("AR_CUIT", _r(_LD + r"(?:20|23|24|27|30|33|34)-\d{8}-\d" + _RD), _stdnum("ar.cuit")),
    Rule("AR_CUIT", _r(_LD + r"(?:20|23|24|27|30|33|34)\d{9}" + _RD), _stdnum("ar.cuit"), ("cuit", "cuil", "cdi"), always=False),
    Rule("CL_RUT", _r(_LD + r"\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]" + _R), _stdnum("cl.rut")),
    Rule("MX_CURP", _r(r"\b[A-Z]{4}\d{6}[HMX][A-Z]{5}[A-Z\d]\d\b", _I), _stdnum("mx.curp")),
    Rule("MX_RFC", _r(r"\b[A-ZÑ&]{4}\d{6}[A-Z\d]{3}\b", _I), _stdnum("mx.rfc")),
    Rule("CO_NIT", _r(_LD + r"\d{1,3}(?:\.\d{3}){2}-\d" + _RD), _stdnum("co.nit"), ("nit",), always=False),
    Rule("UY_RUT", _r(_LD + r"\d{2}-?\d{6}-?\d{3}-?\d" + _RD), _stdnum("uy.rut"), ("rut",), always=False),
    Rule("EC_CI", _r(_LD + r"\d{9}-?\d" + _RD), _stdnum("ec.ci"), ("cedula", "ci", "c.i"), always=False),

    # ---- Europe ----
    Rule("UK_NINO", _r(r"\b[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z] ?\d{2} ?\d{2} ?\d{2} ?[A-D]\b", _I), _uk_nino),
    Rule("UK_NHS", _r(_LD + r"\d{3}[- ]?\d{3}[- ]?\d{4}" + _RD), _stdnum("gb.nhs"), ("nhs",), always=False),
    Rule("ES_DNI", _r(_LD + r"\d{8}[- ]?[A-Z]" + _R, _I), _stdnum("es.dni")),
    Rule("ES_NIE", _r(r"\b[XYZ][- ]?\d{7}[- ]?[A-Z]\b", _I), _stdnum("es.nie")),
    Rule("PT_NIF", _r(_LD + r"\d{3} ?\d{3} ?\d{3}" + _RD), _stdnum("pt.nif"), ("nif", "contribuinte"), always=False),
    Rule("FR_NIR", _r(_LD + r"[12] ?\d{2} ?(?:0[1-9]|1[0-2]|[2-9]\d) ?(?:\d{2}|2[AB]) ?\d{3} ?\d{3} ?\d{2}" + _RD, _I), _stdnum("fr.nir")),
    Rule("IT_CODICE_FISCALE", _r(r"\b[A-Z]{6}[\dLMNP-V]{2}[A-EHLMPR-T][\dLMNP-V]{2}[A-Z][\dLMNP-V]{3}[A-Z]\b", _I), _stdnum("it.codicefiscale")),
    Rule("DE_TAX_ID", _r(_LD + r"\d{2} ?\d{3} ?\d{3} ?\d{3}" + _RD), _stdnum("de.idnr"), ("steuer", "idnr", "identifikationsnummer", "tax id", "tin"), always=False),
    Rule("NL_BSN", _r(_LD + r"\d{4}\.?\d{2}\.?\d{3}" + _RD), _stdnum("nl.bsn"), ("bsn", "burgerservicenummer", "sofinummer", "sofi"), always=False),
    Rule("BE_NATIONAL_NUMBER", _r(_LD + r"\d{2}[. ]\d{2}[. ]\d{2}[- ]?\d{3}[. ]?\d{2}" + _RD), _stdnum("be.nn")),
    Rule("BE_NATIONAL_NUMBER", _r(_LD + r"\d{11}" + _RD), _stdnum("be.nn"), ("rijksregister", "registre national", "numero national", "nn", "insz", "niss"), always=False),
    Rule("PL_PESEL", _r(_LD + r"\d{11}" + _RD), _stdnum("pl.pesel"), ("pesel",), always=False),
    Rule("NATIONAL_ID", _r(_LD + r"(?:\d{2})?\d{6}[-+]\d{4}" + _RD), _stdnum("se.personnummer", "dk.cpr")),
    Rule("NATIONAL_ID", _r(_LD + r"\d{6} ?\d{5}" + _RD), _stdnum("no.fodselsnummer"), ("fodselsnummer", "fnr", "personnummer"), always=False),
    Rule("FI_HETU", _r(_LD + r"\d{6}[-+A-FU-Y]\d{3}[0-9A-Y]" + _R, _I), _stdnum("fi.hetu")),
    Rule("CH_AHV", _r(_LD + r"756\.?\d{4}\.?\d{4}\.?\d{2}" + _RD), _stdnum("ch.ssn")),
    Rule("AT_SVNR", _r(_LD + r"\d{4} ?\d{6}" + _RD), _stdnum("at.vnr"), ("svnr", "sozialversicherung", "versicherungsnummer"), always=False),
    Rule("IE_PPS", _r(_LD + r"\d{7}[A-W][A-IW]?" + _R, _I), _stdnum("ie.pps")),
    Rule("NATIONAL_ID", _r(_LD + r"\d{6}/\d{3,4}" + _RD), _stdnum("cz.rc")),
    Rule("NATIONAL_ID", _r(_LD + r"\d{13}" + _RD), _stdnum("ro.cnp", "za.idnr", "th.pin"), NATIONAL_ID_CONTEXT, always=False),
    Rule("NATIONAL_ID", _r(_LD + r"\d{10}" + _RD), _stdnum("bg.egn", "cz.rc"), NATIONAL_ID_CONTEXT, always=False),
    Rule("NATIONAL_ID", _r(_LD + r"\d{11}" + _RD), _stdnum("hr.oib", "gr.amka", "ee.ik", "lt.asmens", "tr.tckimlik", "pl.pesel", "de.idnr"), NATIONAL_ID_CONTEXT, always=False),

    # ---- Middle East / Asia / Oceania / Africa ----
    Rule("IL_ID", _r(_LD + r"\d{7,8}-?\d" + _RD), _stdnum("il.idnr"), ("teudat", "zehut", "תעודת זהות", "ת.ז", "id"), always=False),
    Rule("IN_AADHAAR", _r(_LD + r"[2-9]\d{3}[ -]\d{4}[ -]\d{4}" + _RD), _stdnum("in_.aadhaar")),
    Rule("IN_AADHAAR", _r(_LD + r"[2-9]\d{11}" + _RD), _stdnum("in_.aadhaar"), ("aadhaar", "aadhar", "uid", "आधार"), always=False),
    Rule("IN_PAN", _r(r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b"), _stdnum("in_.pan")),
    Rule("PK_CNIC", _r(_LD + r"\d{5}-\d{7}-\d" + _RD), _stdnum("pk.cnic")),
    Rule("CN_RESIDENT_ID", _r(_LD + r"\d{17}[\dXx]" + _R), _stdnum("cn.ric")),
    Rule("HK_ID", _r(r"\b[A-Z]{1,2}\d{6}\(?[0-9A]\)?(?![\w])"), _hkid),
    Rule("TW_ID", _r(r"\b[A-Z][12]\d{8}\b"), _taiwan_id),
    Rule("JP_MY_NUMBER", _r(_LD + r"\d{4} ?\d{4} ?\d{4}" + _RD), _stdnum("jp.in_"), ("my number", "マイナンバー", "個人番号"), always=False),
    Rule("KR_RRN", _r(_LD + r"\d{6}-\d{7}" + _RD), _stdnum("kr.rrn")),
    Rule("MY_NRIC", _r(_LD + r"\d{6}-\d{2}-\d{4}" + _RD), _stdnum("my.nric")),
    Rule("SG_NRIC", _r(r"\b[STFGM]\d{7}[A-Z]\b", _I), _singapore_nric),
    Rule("ID_NIK", _r(_LD + r"\d{16}" + _RD), _stdnum("id.nik"), ("nik", "ktp", "kependudukan"), always=False),
    Rule("AU_TFN", _r(_LD + r"\d{3} ?\d{3} ?\d{3}" + _RD), _stdnum("au.tfn"), ("tfn", "tax file"), always=False),
    Rule("NZ_IRD", _r(_LD + r"\d{2,3}-?\d{3}-?\d{3}" + _RD), _stdnum("nz.ird"), ("ird",), always=False),
    Rule("RU_INN", _r(_LD + r"\d{10}(?:\d{2})?" + _RD), _stdnum("ru.inn", "ua.rntrc"), ("inn", "инн", "інн", "rntrc", "ипн", "іпн"), always=False),

    # ---- Context-only catch-alls ----
    Rule("PASSPORT", _r(r"(?<![\w])(?=[A-Z0-9]*\d)[A-Z0-9]{6,9}(?![\w])", _I), None, PASSPORT_CONTEXT, always=False),
    Rule("NATIONAL_ID", _r(_LD + r"[A-Z]{0,3}\d{1,3}(?:[. ]?\d{3}){1,4}(?:-?[\dKk])?" + _RD, _I), None, NATIONAL_ID_CONTEXT, always=False),
    Rule("DATE_OF_BIRTH", _r(_LD + r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}" + _RD), _date_like, DOB_CONTEXT, always=False),
    Rule("DATE_OF_BIRTH", _r(r"\b\d{1,2}(?:st|nd|rd|th|er|º|°)?\.?(?:\s+de)?\s+[^\W\d_]{3,12}\.?(?:\s+de)?,?\s+(?:1[89]|20)\d{2}\b"), None, DOB_CONTEXT, always=False),
    Rule("DATE_OF_BIRTH", _r(r"\b[^\W\d_]{3,12}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:1[89]|20)\d{2}\b"), None, DOB_CONTEXT, always=False),
    Rule("DATE_OF_BIRTH", _r(r"(?:1[89]|20)\d{2}\s?[年년]\s?\d{1,2}\s?[月월]\s?\d{1,2}\s?[日일]?"), None, DOB_CONTEXT, always=False),
    Rule("PHONE", _r(_LD + r"\+?\(?\d{1,4}\)?(?:[ .-]?\(?\d{1,4}\)?){1,5}" + _RD), lambda v: 7 <= len(_digits(v)) <= 15, PHONE_CONTEXT, always=False),
]

# Compile each rule's context regex once.
_RULES = [
    Rule(r.entity, r.pattern, r.validate, r.context, r.always, r.group, r.not_after,
         _keyword_regex(r.context) if r.context else None)
    for r in _RULES
]

_IBAN_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2})(\d{2})((?:[ ]?[A-Z0-9]){8,32})", re.IGNORECASE)

_URL_USERINFO = re.compile(r"[a-z][a-z0-9+.-]*://[^\s:/@]+:$", re.IGNORECASE)

# Keywords almost always precede the value ("DNI 30.123.456"); a short
# look-ahead still allows "30.123.456 (DNI)".
_CONTEXT_BEFORE = 60
_CONTEXT_AFTER = 10


def _has_context(rule: Rule, text: str, start: int, end: int) -> bool:
    if rule._context_re is None:
        return False
    window = text[max(0, start - _CONTEXT_BEFORE):start] + " " + text[end:end + _CONTEXT_AFTER]
    return bool(rule._context_re.search(_normalize(window)))


def _iban_spans(text: str) -> list[Span]:
    spans = []
    pos = 0
    while (m := _IBAN_CANDIDATE.search(text, pos)) is not None:
        pos = m.start() + 1
        country = m.group(1).upper()
        length = _IBAN_LENGTHS.get(country)
        if not length:
            continue
        # Walk the match until we've consumed exactly `length` alphanumerics.
        count = 0
        end = m.start()
        for i in range(m.start(), m.end()):
            if text[i].isalnum():
                count += 1
                if count == length:
                    end = i + 1
                    break
        if count != length:
            continue
        if end < len(text) and text[end].isalnum():
            continue  # glued to more characters, not a clean IBAN
        candidate = text[m.start():end]
        compact = re.sub(r"\s", "", candidate).upper()
        if _iban_mod97(compact):
            spans.append(Span(m.start(), end, "IBAN", candidate))
            pos = end
    return spans


# --- Phones ----------------------------------------------------------------

@lru_cache(maxsize=1)
def _phonenumbers():
    try:
        import phonenumbers
    except ImportError:
        return None
    return phonenumbers


def _phone_spans(text: str, regions: Sequence[str]) -> list[Span]:
    pn = _phonenumbers()
    if pn is None:
        return []
    spans: list[Span] = []
    seen: set[tuple[int, int]] = set()
    # None finds only numbers written in international (+CC) format.
    for region in [*regions, None]:
        try:
            matcher = pn.PhoneNumberMatcher(text, region, leniency=pn.Leniency.VALID)
            for match in matcher:
                key = (match.start, match.end)
                if key not in seen:
                    seen.add(key)
                    spans.append(Span(match.start, match.end, "PHONE", match.raw_string))
        except Exception:
            continue
    return spans


# --- Public API ------------------------------------------------------------

def detect(text: str, regions: Sequence[str] = (), paranoid: bool = False) -> list[Span]:
    """Return non-overlapping personal-data spans found in the text.

    regions: ISO 3166 country codes used to read phone numbers written without
    a +country prefix. paranoid: also flag checksum-valid identifiers that
    have no keyword nearby.
    """
    candidates: list[tuple[Span, int]] = []
    for priority, rule in enumerate(_RULES):
        for m in rule.pattern.finditer(text):
            start, end = m.span(rule.group)
            if start == end:
                continue
            value = text[start:end]
            if rule.not_after is not None and rule.not_after.search(text, max(0, start - 20), start):
                continue
            if rule.entity == "EMAIL" and _URL_USERINFO.search(text, max(0, start - 200), start):
                continue  # the "pass@host" part of scheme://user:pass@host
            if rule.validate is not None:
                try:
                    if not rule.validate(value):
                        continue
                except Exception:
                    continue
            if not rule.always:
                if rule.validate is None or not paranoid:
                    if not _has_context(rule, text, start, end):
                        continue
            candidates.append((Span(start, end, rule.entity, value), priority))

    phone_priority = len(_RULES)
    candidates += [(s, phone_priority) for s in _iban_spans(text)]
    candidates += [(s, phone_priority) for s in _phone_spans(text, regions)]
    return resolve_overlaps(candidates)


def resolve_overlaps(candidates: Iterable[tuple[Span, int]]) -> list[Span]:
    """Pick non-overlapping spans: longest first, then lowest priority number."""
    ordered = sorted(candidates, key=lambda c: (-(c[0].end - c[0].start), c[1], c[0].start))
    taken: list[Span] = []
    for span, _ in ordered:
        if all(span.end <= t.start or span.start >= t.end for t in taken):
            taken.append(span)
    taken.sort(key=lambda s: s.start)
    return taken
