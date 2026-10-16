"""Tests for NER detection. Integration tests run only if the model is downloaded."""

import pytest

from baldanders.model_manager import is_downloaded, model_dir
from baldanders.scrubber import ner
from baldanders.scrubber.ner import NERDetector, _chunks, _is_false_positive, split_words


# --- False positive filter tests (always run) ---

def test_false_positive_common_words():
    for word in ("today", "tomorrow", "SELECT", "NULL"):
        assert _is_false_positive(word, "PERSON") is True


def test_false_positive_short_strings():
    assert _is_false_positive("a", "PERSON") is True
    assert _is_false_positive("x", "ADDRESS") is True


def test_cjk_two_char_name_not_filtered():
    assert _is_false_positive("王明", "PERSON") is False


def test_false_positive_uk_postcodes():
    assert _is_false_positive("SL4 2JW", "DRIVERS_LICENSE") is True
    assert _is_false_positive("SW1A 1AA", "DRIVERS_LICENSE") is True


def test_not_false_positive_real_names():
    for name in ("John Smith", "Alex Rivera", "Jamie Chen"):
        assert _is_false_positive(name, "PERSON") is False


def test_false_positive_pure_numbers():
    assert _is_false_positive("12345", "PERSON") is True


def test_false_positive_own_tokens():
    assert _is_false_positive("[PERSON_1]", "PERSON") is True


# --- Word splitter ---

def words(text):
    return [w for w, _, _ in split_words(text)]


def test_split_latin_with_apostrophes_and_hyphens():
    assert words("Sarah O'Connor-Smith, hi!") == ["Sarah", "O'Connor-Smith", ",", "hi", "!"]


def test_split_cjk_per_character():
    assert words("我叫王小明") == ["我", "叫", "王", "小", "明"]


def test_split_devanagari_keeps_vowel_signs():
    assert words("राहुल शर्मा") == ["राहुल", "शर्मा"]


def test_split_offsets_round_trip():
    text = "Hans Müller, Hauptstraße 5, 10115 Berlin. 我叫王小明。"
    for w, s, e in split_words(text):
        assert text[s:e] == w


def test_chunks_cover_long_text(monkeypatch):
    monkeypatch.setattr(ner, "_CHUNK_WORDS", 10)
    monkeypatch.setattr(ner, "_CHUNK_OVERLAP", 3)
    text = " ".join(f"w{i}" for i in range(35))
    chunks = list(_chunks(text))
    assert len(chunks) > 1
    covered = set()
    for offset, chunk in chunks:
        assert text[offset:offset + len(chunk)] == chunk
        covered.update(range(offset, offset + len(chunk)))
    assert all(i in covered for i, c in enumerate(text) if not c.isspace())


def test_detect_offsets_across_chunks(monkeypatch):
    monkeypatch.setattr(ner, "_CHUNK_WORDS", 6)
    monkeypatch.setattr(ner, "_CHUNK_OVERLAP", 2)

    class FakeModel:
        def predict_entities(self, text, labels, threshold):
            i = text.find("Ana García")
            return [] if i < 0 else [{"start": i, "end": i + 10, "text": "Ana García", "label": "person", "score": 0.9}]

    d = NERDetector()
    d._load_attempted, d._available, d._model = True, True, FakeModel()
    text = "one two three four five six seven eight nine Ana García ten eleven"
    spans = d.detect(text)
    assert [(text[s.start:s.end], s.entity_type) for s in spans] == [("Ana García", "PERSON")]


def test_missing_model_is_regex_only(tmp_path):
    d = NERDetector(tmp_path / "nope")
    assert d.available is False
    assert d.detect("John Smith") == []
    assert d.load_error


# --- Integration tests (only if model is downloaded) ---

needs_model = pytest.mark.skipif(
    not is_downloaded() or __import__("importlib").util.find_spec("gliner") is None,
    reason="model not downloaded; run `baldanders download` (needs baldanders[ner])",
)


@pytest.fixture(scope="module")
def detector():
    return NERDetector(model_dir=model_dir())


@needs_model
def test_ner_loads(detector):
    assert detector.available is True


@needs_model
@pytest.mark.parametrize("text, name", [
    ("Please transfer funds to John Smith immediately", "John Smith"),
    ("Hola, me llamo Nahuel Nucera y vivo en Córdoba", "Nahuel Nucera"),
    ("Je m'appelle Marie Dupont", "Marie Dupont"),
    ("Mein Name ist Hans Müller", "Hans Müller"),
    ("我叫王小明。", "王小明"),
    ("اسمي أحمد محمد", "أحمد محمد"),
])
def test_ner_detects_person_multilingual(detector, text, name):
    assert any(name in s.text for s in detector.detect(text) if s.entity_type == "PERSON")


@needs_model
@pytest.mark.parametrize("text", [
    "Saludame por mi nombre y repetí mi mail.",
    "Ayudame con este código Python.",
    "Contame un chiste.",
    "Explicame qué es Kubernetes.",
    "Please summarize this article about Apple and Microsoft.",
    "Grazie mille, ciao!",
])
def test_ner_no_false_positive_names(detector, text):
    assert [s.text for s in detector.detect(text) if s.entity_type == "PERSON"] == []


@needs_model
def test_ner_no_false_positive_today(detector):
    assert all(s.text.lower() != "today" for s in detector.detect("What date is today?"))


@needs_model
def test_ner_no_false_positive_sql(detector):
    spans = detector.detect("SELECT * FROM users WHERE id = 1")
    assert not {s.text.lower() for s in spans} & {"select", "from", "where"}
