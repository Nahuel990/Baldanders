"""Session history — persists token mapping and conversation across restarts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from baldanders.mapping import SessionMap

log = logging.getLogger(__name__)

DEFAULT_HISTORY_DIR = Path.home() / ".baldanders" / "sessions"


def _session_path(session_id: str, history_dir: Path = DEFAULT_HISTORY_DIR) -> Path:
    return history_dir / f"{session_id}.json"


def save_session(
    session_id: str,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> None:
    """Save session state to disk."""
    history_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(session_id, history_dir)
    data = {
        "session_id": session_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mapping": {
            "forward": session_map._forward,
            "reverse": session_map._reverse,
            "counters": session_map._counters,
        },
        "messages": messages,
    }
    path.write_text(json.dumps(data, indent=2))
    log.debug("Session saved to %s", path)


def load_session(
    session_id: str,
    history_dir: Path = DEFAULT_HISTORY_DIR,
) -> tuple[SessionMap, list[dict[str, str]]] | None:
    """Load a saved session. Returns (session_map, messages) or None."""
    path = _session_path(session_id, history_dir)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to load session %s", session_id)
        return None

    session_map = SessionMap()
    mapping = data.get("mapping", {})
    session_map._forward = mapping.get("forward", {})
    session_map._reverse = mapping.get("reverse", {})
    session_map._counters = mapping.get("counters", {})

    messages = data.get("messages", [])
    return session_map, messages


def list_sessions(history_dir: Path = DEFAULT_HISTORY_DIR) -> list[dict]:
    """List all saved sessions."""
    if not history_dir.exists():
        return []

    sessions = []
    for path in sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text())
            sessions.append({
                "id": data.get("session_id", path.stem),
                "updated_at": data.get("updated_at", ""),
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return sessions


def new_session_id() -> str:
    """Generate a session ID from the current timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
