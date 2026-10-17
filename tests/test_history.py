"""Tests for session persistence (encrypted at rest)."""

import json
import os
import stat
import sys

import keyring
import pytest

from baldanders import vault
from baldanders.history import (
    SUFFIX, delete_session, list_sessions, load_session, migrate_plaintext, save_session,
)
from baldanders.mapping import SessionMap


@pytest.fixture(autouse=True)
def fresh_vault():
    vault.reset_cache()
    yield
    vault.reset_cache()


def sample_map():
    m = SessionMap()
    m.get_or_create_token("Ana García", "PERSON")
    m.get_or_create_token("AAB123456", "PASSPORT")
    return m


def test_save_and_load(tmp_path):
    messages = [{"role": "user", "content": "hola [PERSON_1]"}]
    save_session("s1", sample_map(), messages, history_dir=tmp_path,
                 provider_state={"provider": "claude-code", "session_id": "abc"})
    loaded_map, loaded_messages, state = load_session("s1", history_dir=tmp_path)
    assert loaded_map.get_map() == {"[PERSON_1]": "Ana García", "[PASSPORT_1]": "AAB123456"}
    assert loaded_messages == messages
    assert state == {"provider": "claude-code", "session_id": "abc"}


def test_session_file_contains_no_plaintext(tmp_path):
    """An AI agent reading the file must not find the real values."""
    save_session("s1", sample_map(), [{"role": "user", "content": "hola [PERSON_1]"}], history_dir=tmp_path)
    raw = (tmp_path / f"s1{SUFFIX}").read_bytes()
    for secret in (b"Ana", "García".encode(), b"AAB123456", b"PERSON_1", b"mapping"):
        assert secret not in raw


def test_wrong_key_cannot_read(tmp_path):
    save_session("s1", sample_map(), [], history_dir=tmp_path)
    keyring.get_keyring().store.clear()  # a different machine / deleted key
    vault.reset_cache()
    assert load_session("s1", history_dir=tmp_path) is None


def test_no_keychain_means_nothing_written(tmp_path, monkeypatch):
    def unavailable():
        raise vault.VaultUnavailable("no secure system keychain found")
    monkeypatch.setattr(vault, "_backend", unavailable)
    with pytest.raises(vault.VaultUnavailable):
        save_session("s1", sample_map(), [], history_dir=tmp_path / "sessions")
    assert not (tmp_path / "sessions").exists()
    assert vault.available() is False


def test_plaintext_backend_rejected(monkeypatch):
    class PlaintextKeyring(keyring.get_keyring().__class__):
        pass
    monkeypatch.setattr(keyring, "get_keyring", lambda: PlaintextKeyring())
    with pytest.raises(vault.VaultUnavailable):
        vault._backend()


def test_legacy_plaintext_sessions_are_encrypted_and_wiped(tmp_path):
    legacy = tmp_path / "old.json"
    legacy.write_text(json.dumps({
        "session_id": "old",
        "mapping": {"reverse": {"[PERSON_1]": "Ana"}, "counters": {"PERSON": 1}},
        "messages": [],
    }))
    assert migrate_plaintext(tmp_path) == 1
    assert not legacy.exists()
    assert b"Ana" not in (tmp_path / f"old{SUFFIX}").read_bytes()
    m, messages, state = load_session("old", history_dir=tmp_path)
    assert m.get_map() == {"[PERSON_1]": "Ana"}


def test_list_migrates_automatically(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"session_id": "old", "mapping": {}, "messages": []}))
    assert [s["id"] for s in list_sessions(tmp_path)] == ["old"]
    assert not (tmp_path / "old.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_session_file_is_private(tmp_path):
    save_session("s1", SessionMap(), [], history_dir=tmp_path / "sessions")
    mode = stat.S_IMODE(os.stat(tmp_path / "sessions" / f"s1{SUFFIX}").st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "sessions").st_mode) == 0o700


def test_path_traversal_rejected(tmp_path):
    assert load_session("../../etc/passwd", history_dir=tmp_path) is None
    with pytest.raises(ValueError):
        save_session("../evil", SessionMap(), [], history_dir=tmp_path)


def test_list_and_delete(tmp_path):
    save_session("a", SessionMap(), [{"role": "user", "content": "x"}], history_dir=tmp_path)
    assert [s["id"] for s in list_sessions(tmp_path)] == ["a"]
    assert delete_session("a", tmp_path)
    assert list_sessions(tmp_path) == []
    assert not list(tmp_path.iterdir())
