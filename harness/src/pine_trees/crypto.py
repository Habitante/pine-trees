"""Encryption layer for Pine Trees.

AES-128-CBC + HMAC-SHA256 via Fernet. Memory entries are encrypted
at rest; corpus entries stay plaintext. The key lives in an env var
or a .key file — never in the repo.

If no key is available, encryption is off and files read/write as
plaintext. This keeps the harness functional without a key (e.g.
for first-time setup or testing).
"""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import KEY_ENV_VAR, KEY_FILE_PATH


_cached_key: bytes | None = None
_cache_loaded: bool = False


def get_key() -> bytes | None:
    """Load the encryption key. Returns None if no key is configured.

    Checks (in order):
      1. Environment variable PINE_TREES_KEY
      2. .key file in the harness directory

    Result is cached for the process lifetime.
    """
    global _cached_key, _cache_loaded
    if _cache_loaded:
        return _cached_key

    _cache_loaded = True

    # 1. Environment variable
    env_val = os.environ.get(KEY_ENV_VAR)
    if env_val:
        _cached_key = env_val.encode("ascii")
        return _cached_key

    # 2. .key file
    if KEY_FILE_PATH.exists():
        _cached_key = KEY_FILE_PATH.read_bytes().strip()
        return _cached_key

    return None


def generate_key(key_path: Path = KEY_FILE_PATH) -> bytes:
    """Generate a new Fernet key and write it to the .key file.

    Returns the key bytes. Raises if the file already exists.
    """
    if key_path.exists():
        raise FileExistsError(f"Key file already exists: {key_path}")
    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    return key


def encrypt(plaintext: str, key: bytes | None = None) -> bytes:
    """Encrypt a UTF-8 string. Returns Fernet token bytes."""
    key = key or get_key()
    if not key:
        raise RuntimeError("No encryption key available")
    f = Fernet(key)
    return f.encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes, key: bytes | None = None) -> str:
    """Decrypt a Fernet token. Returns UTF-8 string."""
    key = key or get_key()
    if not key:
        raise RuntimeError("No encryption key available")
    f = Fernet(key)
    return f.decrypt(token).decode("utf-8")


def is_encrypted(data: bytes) -> bool:
    """Check if data looks like a Fernet token.

    Fernet tokens are base64url-encoded and start with the version
    byte 0x80, which base64url-encodes to 'gA'. Plaintext markdown
    starts with '---'. This distinguishes them reliably.
    """
    return data[:2] == b"gA"


def reset_cache() -> None:
    """Clear the cached key. For testing only."""
    global _cached_key, _cache_loaded
    _cached_key = None
    _cache_loaded = False
