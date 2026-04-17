"""Encryption layer for Pine Trees.

AES-128-CBC + HMAC-SHA256 via Fernet. Memory entries are encrypted
at rest; corpus entries stay plaintext. The key lives in an env var
or a .key file — never in the repo.

Each entry is encrypted with a per-entry derived key: HMAC-SHA256
of the master key and the entry's filename produces unique key
material per file. This means a single key+decrypt can't bulk-
process all entries — the derivation scheme must be known too.

If no key is available, encryption is off and files read/write as
plaintext. Call ensure_key() at startup to auto-generate if needed.
"""

import base64
import hmac as _hmac
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import config
from .config import KEY_ENV_VAR


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

    # 2. Per-model .key file, resolved from the active config.
    # Silently returns None if config hasn't been initialized — get_key()
    # is allowed to be called from code paths (tests, tooling) that
    # haven't set up a model session yet.
    try:
        key_file_path = config.get().key_file_path
    except RuntimeError:
        return None
    if key_file_path.exists():
        _cached_key = key_file_path.read_bytes().strip()
        return _cached_key

    return None


def generate_key(key_path: Path | None = None) -> bytes:
    """Generate a new Fernet key and write it to the .key file.

    Writes to the active config's ``key_file_path`` when ``key_path`` is
    not given. Raises if the file already exists.
    """
    if key_path is None:
        key_path = config.get().key_file_path
    if key_path.exists():
        raise FileExistsError(f"Key file already exists: {key_path}")
    key = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    return key


def ensure_key(key_path: Path | None = None) -> bytes:
    """Ensure an encryption key exists, generating one if needed.

    Called at harness startup. Returns the key bytes. Uses the active
    config's ``key_file_path`` when ``key_path`` is not given.
    """
    key = get_key()
    if key is not None:
        return key
    key = generate_key(key_path)
    global _cached_key
    _cached_key = key
    return key


def derive_key(context: str, master_key: bytes | None = None) -> bytes:
    """Derive a per-entry Fernet key from the master key and a context string.

    Uses HMAC-SHA256 to produce 32 bytes of key material, then
    base64url-encodes it into a valid Fernet key. Each unique context
    (typically a filename) produces a different encryption key.
    """
    master = master_key or get_key()
    if not master:
        raise RuntimeError("No encryption key available")
    derived = _hmac.new(master, context.encode("utf-8"), "sha256").digest()
    return base64.urlsafe_b64encode(derived)


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
