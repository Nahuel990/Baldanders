"""Tests for session persistence."""

import os
import stat
import sys

import pytest

from baldanders.history import delete_session, list_sessions, load_session, save_session
from baldanders.mapping import SessionMap


def test_save_and_load(tmp_path):
    m = SessionMap()
    m.get_or_create_token("Ana García", "PERSON")
    messages = [{"role": "user", "content": "hola [PERSON_1]"}]
    save_session("s1", m, messages, history_dir=tmp_path, provider_state={"provider": "claude", "session_id": "abc"})

    loaded_map, loaded_messages, state = load_session("s1", history_dir=tmp_path)
    assert loaded_map.get_map() == {"[PERSON_1]": "Ana García"}
    assert loaded_messages == messages
    assert state == {"provider": "claude", "session_id": "abc"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_session_file_is_private(tmp_path):
    save_session("s1", SessionMap(), [], history_dir=tmp_path / "sessions")
    mode = stat.S_IMODE(os.stat(tmp_path / "sessions" / "s1.json").st_mode)
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


def test_load_old_format(tmp_path):
    """Sessions saved by 0.1.0 pre-releases (no provider_state) still load."""
    (tmp_path / "old.json").write_text(
        '{"session_id": "old", "mapping": {"forward": {"Ana": "[PERSON_1]"}, '
        '"reverse": {"[PERSON_1]": "Ana"}, "counters": {"PERSON": 1}}, "messages": []}'
    )
    m, messages, state = load_session("old", history_dir=tmp_path)
    assert m.get_map() == {"[PERSON_1]": "Ana"}
    assert state == {}
