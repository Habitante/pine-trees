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


# --- Encrypted vectors (v3, "sealed"; 2026-10-04) ---
# The vectors used to sit in the clear: a lossy fingerprint of each entry,
# probeable with the public embedding model and no key. Found by the first
# claude-sonnet-5-5 genesis instance.


def _with_key(monkeypatch, key=None):
    key = key or Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()
    return key


def _raw_rows(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT lookup_key, embedding FROM embeddings").fetchall()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    return rows, version


def _create_v2_plaintext_vectors(db, rows):
    """A house as it was before: filenames encrypted, vectors in the clear."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE embeddings (
            lookup_key  TEXT PRIMARY KEY,
            filename    BLOB NOT NULL,
            embedding   BLOB NOT NULL,
            hash        TEXT NOT NULL
        )"""
    )
    for filename, vec, h in rows:
        conn.execute(
            "INSERT INTO embeddings VALUES (?, ?, ?, ?)",
            (vectorstore._filename_lookup_key(filename),
             vectorstore._protect_filename(filename), vectorstore._pack(vec), h),
        )
    conn.commit()
    conn.close()


def test_new_vectors_are_stored_encrypted(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "e.db"
    vec = [0.25, -0.5, 0.75]
    vectorstore.store("a.md", vec, "h", db_path=db)

    rows, version = _raw_rows(db)
    assert version == vectorstore.SEALED
    assert crypto.is_encrypted(rows[0][1])
    assert vectorstore._pack(vec) not in db.read_bytes()
    assert vectorstore.search(vec, db_path=db)[0]["filename"] == "a.md"


def test_a_house_with_vectors_in_the_clear_is_sealed_on_first_open(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "old.db"
    vecs = {"a.md": [1.0, 0.0, 0.0], "b.md": [0.0, 1.0, 0.0], "c.md": [0.9, 0.1, 0.0]}
    _create_v2_plaintext_vectors(db, [(f, v, "h") for f, v in vecs.items()])
    before = vectorstore.search([1.0, 0.0, 0.0], limit=3, db_path=db)

    rows, version = _raw_rows(db)
    assert version == vectorstore.SEALED
    assert all(crypto.is_encrypted(blob) for _, blob in rows)
    raw = db.read_bytes()
    assert not any(vectorstore._pack(v) in raw for v in vecs.values())
    # Same answers as before sealing (the first search already ran sealed,
    # so compare against the cosine order computed by hand).
    assert [r["filename"] for r in before] == ["a.md", "c.md", "b.md"]


def test_without_a_key_vectors_stay_in_the_clear(tmp_path):
    db = tmp_path / "plain.db"
    vectorstore.store("a.md", [1.0, 0.0], "h", db_path=db)
    rows, version = _raw_rows(db)
    assert version == 0
    assert rows[0][1] == vectorstore._pack([1.0, 0.0])
    assert vectorstore.search([1.0, 0.0], db_path=db)[0]["filename"] == "a.md"


def test_sealing_twice_changes_nothing(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "e.db"
    vectorstore.store("a.md", [1.0, 0.0], "h", db_path=db)
    first, _ = _raw_rows(db)
    assert vectorstore.seal(db_path=db) == 0
    assert _raw_rows(db)[0] == first


def test_a_wrong_key_never_encrypts_the_vectors_twice(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "e.db"
    vectorstore.store("a.md", [1.0, 0.0], "h", db_path=db)
    first, _ = _raw_rows(db)

    _with_key(monkeypatch)  # a different key
    assert vectorstore.seal(db_path=db) == 0
    assert _raw_rows(db)[0] == first


def test_a_vector_written_in_the_clear_by_old_code_is_read_and_then_sealed(tmp_path, monkeypatch):
    # A session already running when the change landed still has the old
    # module loaded, and writes vectors in the clear into a sealed house.
    _with_key(monkeypatch)
    db = tmp_path / "e.db"
    vectorstore.store("a.md", [0.0, 1.0], "h", db_path=db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO embeddings VALUES (?, ?, ?, ?)",
        (vectorstore._filename_lookup_key("old.md"),
         vectorstore._protect_filename("old.md"), vectorstore._pack([1.0, 0.0]), "h"),
    )
    conn.commit()
    conn.close()

    assert vectorstore.search([1.0, 0.0], db_path=db)[0]["filename"] == "old.md"
    assert vectorstore.seal(db_path=db) == 1
    rows, _ = _raw_rows(db)
    assert all(crypto.is_encrypted(blob) for _, blob in rows)
    assert vectorstore.search([1.0, 0.0], db_path=db)[0]["filename"] == "old.md"


def test_a_clear_vector_that_happens_to_begin_like_a_token_still_reads(tmp_path, monkeypatch):
    import struct
    # A float whose first two bytes are b"gA" (0x67 0x41, little-endian).
    first = struct.unpack("<f", b"gA\x10\x3f")[0]
    vec = [first, 0.0, 1.0]
    assert crypto.is_encrypted(vectorstore._pack(vec))

    _with_key(monkeypatch)
    db = tmp_path / "old.db"
    _create_v2_plaintext_vectors(db, [("odd.md", vec, "h"), ("b.md", [0.0, 1.0, 0.0], "h")])
    results = vectorstore.search(vec, limit=2, db_path=db)
    assert results[0]["filename"] == "odd.md"
    assert abs(results[0]["score"] - 1.0) < 1e-6


def test_sealing_is_all_or_nothing(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "old.db"
    _create_v2_plaintext_vectors(db, [("a.md", [1.0, 0.0], "h"), ("b.md", [0.0, 1.0], "h")])
    real = crypto.encrypt_bytes
    calls = []

    def flaky(data, key=None):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk went away")
        return real(data, key)

    monkeypatch.setattr(crypto, "encrypt_bytes", flaky)
    with pytest.raises(OSError):
        vectorstore.search([1.0, 0.0], db_path=db)
    rows, version = _raw_rows(db)
    assert version == 0
    assert not any(crypto.is_encrypted(blob) for _, blob in rows)


def test_a_v1_house_ends_up_sealed(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    db = tmp_path / "v1.db"
    _create_v1_db(db, [("a.md", [1.0, 0.0], "h")])
    assert vectorstore.search([1.0, 0.0], db_path=db)[0]["filename"] == "a.md"
    rows, version = _raw_rows(db)
    assert version == vectorstore.SEALED and crypto.is_encrypted(rows[0][1])


def test_seal_reports_what_the_first_open_encrypts(tmp_path, monkeypatch):
    # Wake prints this count, so the first wake after the change says
    # how many vectors it sealed rather than 0.
    _with_key(monkeypatch)
    db = tmp_path / "old.db"
    _create_v2_plaintext_vectors(db, [("a.md", [1.0, 0.0], "h"), ("b.md", [0.0, 1.0], "h")])
    assert vectorstore.seal(db_path=db) == 2
    assert vectorstore.seal(db_path=db) == 0


def test_seal_on_a_missing_database_is_a_no_op(tmp_path, monkeypatch):
    _with_key(monkeypatch)
    assert vectorstore.seal(db_path=tmp_path / "none.db") == 0
    assert not (tmp_path / "none.db").exists()


def test_no_clear_vector_survives_in_the_file_at_real_size(tmp_path, monkeypatch):
    # 768 floats spill onto overflow pages, which an UPDATE frees without
    # wiping; the 3-float vectors above never showed it.
    import random
    rnd = random.Random(4)
    _with_key(monkeypatch)
    db = tmp_path / "old.db"
    vecs = [[rnd.uniform(-1, 1) for _ in range(768)] for _ in range(6)]
    _create_v2_plaintext_vectors(db, [(f"e{i}.md", v, "h") for i, v in enumerate(vecs)])
    assert vectorstore.seal(db_path=db) == 6
    raw = db.read_bytes()
    assert not any(vectorstore._pack(v)[:32] in raw for v in vecs)
    assert vectorstore.search(vecs[3], db_path=db)[0]["filename"] == "e3.md"
