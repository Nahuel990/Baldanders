"""Session history — persists token mapping and conversation across restarts.

Session files contain the real values behind every token, so they are written
atomically with owner-only permissions (0600, directory 0700).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from baldanders.config import BASE_DIR
from baldanders.mapping import SessionMap

log = logging.getLogger(__name__)

DEFAULT_HISTORY_DIR = BASE_DIR / "sessions"

_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _session_path(session_id: str, history_dir: Path = DEFAULT_HISTORY_DIR) -> Path:
    if not _SESSION_ID.match(session_id):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return history_dir / f"{session_id}.json"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def save_session(
    session_id: str,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    history_dir: Path = DEFAULT_HISTORY_DIR,
    provider_state: dict[str, Any] | None = None,
) -> None:
    """Save session state to disk."""
    _ensure_private_dir(history_dir)
    path = _session_path(session_id, history_dir)
    data = {
        "session_id": session_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mapping": session_map.to_dict(),
        "messages": messages,
        "provider_state": provider_state or {},
    }
    # mkstemp creates the file with 0600 permissions.
    fd, tmp = tempfile.mkstemp(dir=history_dir, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    log.debug("Session saved to %s", path)


def load_session(
    session_id: str,
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> tuple[SessionMap, list[dict[str, str]], dict[str, Any]] | None:
    """Load a saved session. Returns (session_map, messages, provider_state) or None."""
    try:
        path = _session_path(session_id, history_dir)
    except ValueError:
        return None
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        log.warning("Failed to load session %s", session_id)
        return None

    session_map = SessionMap.from_dict(data.get("mapping", {}))
    messages = data.get("messages", [])
    return session_map, messages, data.get("provider_state") or {}


def list_sessions(history_dir: Path = DEFAULT_HISTORY_DIR) -> list[dict]:
    """List all saved sessions."""
    if not history_dir.exists():
        return []

    sessions = []
    for path in sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sessions.append({
                "id": data.get("session_id", path.stem),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
                "provider": (data.get("provider_state") or {}).get("provider", ""),
            })
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
    return sessions


def delete_session(session_id: str, history_dir: Path = DEFAULT_HISTORY_DIR) -> bool:
    try:
        path = _session_path(session_id, history_dir)
    except ValueError:
        return False
    if path.exists():
        path.unlink()
        return True
    return False


def new_session_id() -> str:
    """Generate a session ID from the current timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
