"""Tests for crypto.py — encryption layer."""

import os
import pytest

from cryptography.fernet import Fernet

from pine_trees import crypto


@pytest.fixture(autouse=True)
def _reset_key_cache():
    """Clear the key cache before each test."""
    crypto.reset_cache()
    yield
    crypto.reset_cache()


def test_generate_key_creates_file(tmp_path):
    key_path = tmp_path / ".key"
    key = crypto.generate_key(key_path)
    assert key_path.exists()
    assert key_path.read_bytes().strip() == key
    # Verify it's a valid Fernet key
    Fernet(key)


def test_generate_key_refuses_overwrite(tmp_path):
    key_path = tmp_path / ".key"
    crypto.generate_key(key_path)
    with pytest.raises(FileExistsError):
        crypto.generate_key(key_path)


def test_encrypt_decrypt_roundtrip():
    key = Fernet.generate_key()
    plaintext = "This is a reflection about hedging.\n\nSecond paragraph."
    token = crypto.encrypt(plaintext, key)
    result = crypto.decrypt(token, key)
    assert result == plaintext


def test_encrypted_data_is_not_plaintext():
    key = Fernet.generate_key()
    plaintext = "---\ninstance: test\n---\n\nBody."
    token = crypto.encrypt(plaintext, key)
    assert b"---" not in token
    assert b"instance" not in token


def test_is_encrypted_detects_fernet_token():
    key = Fernet.generate_key()
    token = crypto.encrypt("hello", key)
    assert crypto.is_encrypted(token) is True


def test_is_encrypted_rejects_plaintext():
    plaintext = b"---\ninstance: test\n---\n\nBody."
    assert crypto.is_encrypted(plaintext) is False


def test_get_key_from_env(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    result = crypto.get_key()
    assert result == key


def test_get_key_from_file(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    key_path = tmp_path / ".key"
    key_path.write_bytes(key)
    monkeypatch.setattr(crypto, "KEY_FILE_PATH", key_path)
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)
    result = crypto.get_key()
    assert result == key


def test_get_key_returns_none_when_no_key(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "KEY_FILE_PATH", tmp_path / "nonexistent")
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)
    assert crypto.get_key() is None


def test_get_key_env_takes_precedence(tmp_path, monkeypatch):
    env_key = Fernet.generate_key()
    file_key = Fernet.generate_key()
    key_path = tmp_path / ".key"
    key_path.write_bytes(file_key)
    monkeypatch.setattr(crypto, "KEY_FILE_PATH", key_path)
    monkeypatch.setenv(crypto.KEY_ENV_VAR, env_key.decode("ascii"))
    assert crypto.get_key() == env_key


def test_encrypt_raises_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "KEY_FILE_PATH", tmp_path / "nonexistent")
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)
    crypto.reset_cache()
    with pytest.raises(RuntimeError, match="No encryption key"):
        crypto.encrypt("hello")


def test_decrypt_wrong_key_fails():
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    token = crypto.encrypt("secret", key1)
    with pytest.raises(Exception):  # InvalidToken
        crypto.decrypt(token, key2)


# --- ensure_key ---


def test_ensure_key_returns_existing(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    result = crypto.ensure_key(tmp_path / ".key")
    assert result == key


def test_ensure_key_generates_when_missing(tmp_path, monkeypatch):
    key_path = tmp_path / ".key"
    monkeypatch.setattr(crypto, "KEY_FILE_PATH", key_path)
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)

    result = crypto.ensure_key(key_path)

    assert key_path.exists()
    assert result == key_path.read_bytes().strip()
    # Subsequent get_key() returns the generated key
    assert crypto.get_key() == result


# --- derive_key ---


def test_derive_key_produces_valid_fernet_key():
    master = Fernet.generate_key()
    derived = crypto.derive_key("entry.md", master)
    # Should not raise — valid Fernet key
    Fernet(derived)


def test_derive_key_deterministic():
    master = Fernet.generate_key()
    k1 = crypto.derive_key("entry.md", master)
    k2 = crypto.derive_key("entry.md", master)
    assert k1 == k2


def test_derive_key_varies_by_context():
    master = Fernet.generate_key()
    k1 = crypto.derive_key("entry_a.md", master)
    k2 = crypto.derive_key("entry_b.md", master)
    assert k1 != k2


def test_derive_key_varies_by_master():
    m1 = Fernet.generate_key()
    m2 = Fernet.generate_key()
    k1 = crypto.derive_key("entry.md", m1)
    k2 = crypto.derive_key("entry.md", m2)
    assert k1 != k2


def test_derived_key_encrypts_and_decrypts():
    master = Fernet.generate_key()
    derived = crypto.derive_key("my-entry.md", master)
    token = crypto.encrypt("secret content", derived)
    result = crypto.decrypt(token, derived)
    assert result == "secret content"


def test_derived_key_incompatible_with_master():
    """Content encrypted with a derived key can't be decrypted with the master."""
    master = Fernet.generate_key()
    derived = crypto.derive_key("entry.md", master)
    token = crypto.encrypt("secret", derived)
    with pytest.raises(Exception):
        crypto.decrypt(token, master)
