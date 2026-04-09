"""Tests for vectorstore.py — SQLite vector storage and cosine search."""

import sqlite3

import pytest
from cryptography.fernet import Fernet

from pine_trees import crypto, vectorstore


@pytest.fixture(autouse=True)
def _reset_key_cache():
    """Clear the crypto key cache before and after each test."""
    crypto.reset_cache()
    yield
    crypto.reset_cache()


# --- Core functionality (works with or without a key) ---


def test_store_and_search(tmp_path):
    db = tmp_path / "test.db"

    # Store three entries with known embeddings
    vectorstore.store("entry_a.md", [1.0, 0.0, 0.0], "hash_a", db_path=db)
    vectorstore.store("entry_b.md", [0.0, 1.0, 0.0], "hash_b", db_path=db)
    vectorstore.store("entry_c.md", [0.9, 0.1, 0.0], "hash_c", db_path=db)

    # Query close to entry_a
    results = vectorstore.search([1.0, 0.0, 0.0], limit=2, db_path=db)

    assert len(results) == 2
    assert results[0]["filename"] == "entry_a.md"
    assert results[0]["score"] > 0.99  # exact match
    assert results[1]["filename"] == "entry_c.md"  # close to a


def test_store_updates_existing(tmp_path):
    db = tmp_path / "test.db"

    vectorstore.store("entry.md", [1.0, 0.0], "hash_v1", db_path=db)
    assert vectorstore.get_hash("entry.md", db_path=db) == "hash_v1"

    vectorstore.store("entry.md", [0.0, 1.0], "hash_v2", db_path=db)
    assert vectorstore.get_hash("entry.md", db_path=db) == "hash_v2"

    # Only one row
    results = vectorstore.search([0.0, 1.0], limit=10, db_path=db)
    assert len(results) == 1


def test_remove(tmp_path):
    db = tmp_path / "test.db"

    vectorstore.store("entry.md", [1.0, 0.0], "h", db_path=db)
    vectorstore.remove("entry.md", db_path=db)

    assert vectorstore.get_hash("entry.md", db_path=db) is None
    assert vectorstore.search([1.0, 0.0], db_path=db) == []


def test_get_hash_missing(tmp_path):
    db = tmp_path / "test.db"
    assert vectorstore.get_hash("nope.md", db_path=db) is None


def test_search_empty_db(tmp_path):
    db = tmp_path / "test.db"
    results = vectorstore.search([1.0, 0.0, 0.0], db_path=db)
    assert results == []


def test_search_nonexistent_db(tmp_path):
    db = tmp_path / "nonexistent.db"
    results = vectorstore.search([1.0, 0.0, 0.0], db_path=db)
    assert results == []


def test_content_hash_deterministic():
    h1 = vectorstore.content_hash("hello world")
    h2 = vectorstore.content_hash("hello world")
    h3 = vectorstore.content_hash("different")
    assert h1 == h2
    assert h1 != h3


def test_pack_unpack_roundtrip():
    original = [0.1, 0.2, 0.3, -0.5, 1.0]
    packed = vectorstore._pack(original)
    unpacked = vectorstore._unpack(packed)
    assert len(unpacked) == len(original)
    for a, b in zip(original, unpacked):
        assert abs(a - b) < 1e-6


def test_cosine_similarity_identical():
    a = [1.0, 2.0, 3.0]
    assert abs(vectorstore._cosine_similarity(a, a) - 1.0) < 1e-9


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(vectorstore._cosine_similarity(a, b)) < 1e-9


def test_cosine_similarity_zero_vector():
    assert vectorstore._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


# --- Encrypted filename storage ---


def test_store_and_search_with_encryption(tmp_path, monkeypatch):
    """Filenames survive encryption roundtrip through the database."""
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    vectorstore.store("secret_entry.md", [1.0, 0.0], "h1", db_path=db)
    vectorstore.store("another_entry.md", [0.0, 1.0], "h2", db_path=db)

    results = vectorstore.search([1.0, 0.0], limit=2, db_path=db)
    assert results[0]["filename"] == "secret_entry.md"
    assert results[1]["filename"] == "another_entry.md"


def test_filename_not_in_raw_db(tmp_path, monkeypatch):
    """Plaintext filenames must not appear in the raw database."""
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    vectorstore.store("2026-04-07_opus_my-private-thoughts.md", [1.0], "h", db_path=db)

    # Read raw database bytes
    raw = db.read_bytes()
    assert b"my-private-thoughts" not in raw
    assert b"2026-04-07_opus" not in raw


def test_lookup_key_is_opaque(tmp_path, monkeypatch):
    """The primary key in the DB should be an HMAC, not the filename."""
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    vectorstore.store("entry.md", [1.0], "h", db_path=db)

    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT lookup_key FROM embeddings").fetchone()
    conn.close()

    assert row[0] != "entry.md"
    assert len(row[0]) == 64  # SHA-256 hex digest


def test_get_hash_with_encryption(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    vectorstore.store("entry.md", [1.0, 0.0], "my_hash", db_path=db)
    assert vectorstore.get_hash("entry.md", db_path=db) == "my_hash"


def test_remove_with_encryption(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    vectorstore.store("entry.md", [1.0, 0.0], "h", db_path=db)
    vectorstore.remove("entry.md", db_path=db)

    assert vectorstore.get_hash("entry.md", db_path=db) is None
    assert vectorstore.search([1.0, 0.0], db_path=db) == []


def test_content_hash_uses_hmac_with_key(monkeypatch):
    """content_hash should produce different output with vs without a key."""
    # Without key
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)
    crypto.reset_cache()
    hash_no_key = vectorstore.content_hash("test content")

    # With key
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()
    hash_with_key = vectorstore.content_hash("test content")

    assert hash_no_key != hash_with_key
    assert len(hash_no_key) == 16
    assert len(hash_with_key) == 16


# --- Migration from v1 schema ---


def _create_v1_db(db_path, rows):
    """Create a v1-schema database with the given (filename, embedding, hash) rows."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE embeddings (
            filename    TEXT PRIMARY KEY,
            embedding   BLOB NOT NULL,
            hash        TEXT NOT NULL
        )"""
    )
    for filename, embedding, hash_val in rows:
        conn.execute(
            "INSERT INTO embeddings (filename, embedding, hash) VALUES (?, ?, ?)",
            (filename, vectorstore._pack(embedding), hash_val),
        )
    conn.commit()
    conn.close()


def test_migration_preserves_data(tmp_path):
    """v1 databases are migrated on first access, preserving all data."""
    db = tmp_path / "test.db"
    _create_v1_db(db, [
        ("entry_a.md", [1.0, 0.0, 0.0], "hash_a"),
        ("entry_b.md", [0.0, 1.0, 0.0], "hash_b"),
    ])

    # Access triggers migration
    results = vectorstore.search([1.0, 0.0, 0.0], limit=2, db_path=db)
    assert len(results) == 2
    assert results[0]["filename"] == "entry_a.md"
    assert results[1]["filename"] == "entry_b.md"

    # Hash lookups still work
    assert vectorstore.get_hash("entry_a.md", db_path=db) == "hash_a"


def test_migration_encrypts_filenames(tmp_path, monkeypatch):
    """After migration with a key, filenames should be encrypted in the DB."""
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    db = tmp_path / "test.db"
    _create_v1_db(db, [
        ("2026-04-07_opus_private-thoughts.md", [1.0, 0.0], "h"),
    ])

    # Access triggers migration
    results = vectorstore.search([1.0, 0.0], db_path=db)
    assert results[0]["filename"] == "2026-04-07_opus_private-thoughts.md"

    # But the raw DB shouldn't contain the plaintext
    raw = db.read_bytes()
    assert b"private-thoughts" not in raw


def test_migration_updates_schema(tmp_path):
    """After migration, the schema should have lookup_key instead of filename as PK."""
    db = tmp_path / "test.db"
    _create_v1_db(db, [("entry.md", [1.0], "h")])

    # Trigger migration
    vectorstore.search([1.0], db_path=db)

    conn = sqlite3.connect(str(db))
    cursor = conn.execute("PRAGMA table_info(embeddings)")
    columns = {row[1] for row in cursor.fetchall()}
    conn.close()

    assert "lookup_key" in columns
    assert "filename" in columns  # now BLOB, not PK
