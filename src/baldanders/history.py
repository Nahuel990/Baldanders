"""Session history. Persists token mapping and conversation across restarts.

Session files contain the real values behind every token, so they are
encrypted (see vault.py) and written atomically with owner-only permissions.
Plaintext sessions left by older versions are encrypted and wiped on first use.
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

from baldanders import vault
from baldanders.config import BASE_DIR
from baldanders.mapping import SessionMap

log = logging.getLogger(__name__)

DEFAULT_HISTORY_DIR = BASE_DIR / "sessions"

SUFFIX = ".bald"
_LEGACY_SUFFIX = ".json"

_SESSION_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _session_path(session_id: str, history_dir: Path = DEFAULT_HISTORY_DIR) -> Path:
    if not _SESSION_ID.match(session_id):
        raise ValueError(f"Invalid session id: {session_id!r}")
    return history_dir / f"{session_id}{SUFFIX}"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private(path: Path, data: bytes) -> None:
    # mkstemp creates the file with 0600 permissions.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=SUFFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _wipe(path: Path) -> None:
    """Overwrite a file before deleting it, so its contents aren't left on disk."""
    try:
        size = path.stat().st_size
        with path.open("r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(vault.decrypt(path.read_bytes()).decode("utf-8"))
    except (OSError, ValueError, vault.VaultUnavailable, UnicodeDecodeError):
        log.warning("Failed to read session %s", path.name)
        return None


def migrate_plaintext(history_dir: Path = DEFAULT_HISTORY_DIR) -> int:
    """Encrypt sessions saved in plaintext by older versions, then wipe the originals."""
    if not history_dir.exists() or not vault.available():
        return 0
    migrated = 0
    for legacy in history_dir.glob(f"*{_LEGACY_SUFFIX}"):
        if legacy.name.startswith(".tmp-"):
            _wipe(legacy)
            continue
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        target = history_dir / f"{legacy.stem}{SUFFIX}"
        _write_private(target, vault.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))
        _wipe(legacy)
        migrated += 1
    return migrated


def save_session(
    session_id: str,
    session_map: SessionMap,
    messages: list[dict[str, str]],
    history_dir: Path = DEFAULT_HISTORY_DIR,
    provider_state: dict[str, Any] | None = None,
) -> None:
    """Encrypt and save session state. Raises VaultUnavailable without a secure keychain."""
    path = _session_path(session_id, history_dir)
    payload = json.dumps({
        "session_id": session_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "mapping": session_map.to_dict(),
        "messages": messages,
        "provider_state": provider_state or {},
    }, ensure_ascii=False).encode("utf-8")
    token = vault.encrypt(payload)  # before touching the disk: no key, no file
    _ensure_private_dir(history_dir)
    _write_private(path, token)
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
    migrate_plaintext(history_dir)
    if not path.exists():
        return None
    data = _read(path)
    if data is None:
        return None
    session_map = SessionMap.from_dict(data.get("mapping", {}))
    return session_map, data.get("messages", []), data.get("provider_state") or {}


def list_sessions(history_dir: Path = DEFAULT_HISTORY_DIR) -> list[dict]:
    """List all saved sessions."""
    if not history_dir.exists():
        return []
    migrate_plaintext(history_dir)
    sessions = []
    for path in sorted(history_dir.glob(f"*{SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.startswith(".tmp-"):
            continue
        data = _read(path)
        if data is None:
            continue
        sessions.append({
            "id": data.get("session_id", path.stem),
            "updated_at": data.get("updated_at", ""),
            "message_count": len(data.get("messages", [])),
            "provider": (data.get("provider_state") or {}).get("provider", ""),
        })
    return sessions


def delete_session(session_id: str, history_dir: Path = DEFAULT_HISTORY_DIR) -> bool:
    try:
        path = _session_path(session_id, history_dir)
    except ValueError:
        return False
    found = False
    for p in (path, history_dir / f"{session_id}{_LEGACY_SUFFIX}"):
        if p.exists():
            _wipe(p)
            found = True
    return found


def new_session_id() -> str:
    """Generate a session ID from the current timestamp."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
