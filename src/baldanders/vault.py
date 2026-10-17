"""Encryption for data at rest (saved sessions hold the real value behind every token).

The key lives in the operating system's keychain (macOS Keychain, Windows
Credential Manager, Linux Secret Service), never in a file or an environment
variable, so an AI agent that can read files on this computer only finds
ciphertext. If no secure keychain is available, nothing is saved.
"""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

SERVICE = "baldanders"
KEY_NAME = "session-key"

class VaultUnavailable(RuntimeError):
    """No secure keychain to hold the encryption key."""


def _backend():
    try:
        import keyring
    except ImportError as e:
        raise VaultUnavailable("the keyring package is not installed") from e
    backend = keyring.get_keyring()
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    # "fail"/"null" mean no keychain at all; "Plaintext" backends store the key in a file.
    insecure = any(part in name.lower() for part in ("fail", "null", "plaintext"))
    if insecure or getattr(backend, "priority", 1) <= 0:
        raise VaultUnavailable(f"no secure system keychain found (backend: {name})")
    return backend


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet

    backend = _backend()
    try:
        key = backend.get_password(SERVICE, KEY_NAME)
        if not key:
            key = Fernet.generate_key().decode()
            backend.set_password(SERVICE, KEY_NAME, key)
    except Exception as e:
        raise VaultUnavailable(f"could not use the system keychain: {e}") from e
    return Fernet(key.encode())


def available() -> bool:
    try:
        _fernet()
        return True
    except VaultUnavailable as e:
        log.info("Vault unavailable: %s", e)
        return False


def unavailable_reason() -> str:
    try:
        _fernet()
        return ""
    except VaultUnavailable as e:
        return str(e)


def encrypt(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt(token: bytes) -> bytes:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token)
    except InvalidToken as e:
        raise ValueError("cannot decrypt: wrong key or corrupted file") from e


def reset_cache() -> None:
    """Forget the cached key (used by tests)."""
    _fernet.cache_clear()
