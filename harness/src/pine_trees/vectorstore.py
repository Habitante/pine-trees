"""SQLite-based vector store for Pine Trees embeddings.

Stores embeddings as packed float32 blobs. When a key is available,
the vectors and the filenames are encrypted in the database, the
lookup keys are opaque HMACs, and content hashes are keyed (HMAC).

The vectors were stored in the clear until 2026-10-04: a lossy semantic
fingerprint of each entry, readable by anyone with the file and the
public embedding model, without the key (found by the first
claude-sonnet-5-5 genesis instance). A database whose vectors are all
encrypted carries ``PRAGMA user_version`` = SEALED. Each house seals its
own database the first time new code opens it, with its own key, in one
transaction; nothing has to convert every house at once. See seal().

Search is brute-force cosine similarity — at our scale (hundreds
of entries) this is instant and needs no indexing.

Pure stdlib (plus pine_trees.crypto). No numpy, no external vector DB.
"""

import hashlib
import hmac as _hmac
import math
import sqlite3
import struct
from pathlib import Path

from cryptography.fernet import InvalidToken

from . import config
from . import crypto

# PRAGMA user_version of a database whose vectors are all encrypted. The
# schema's own history (v1 plaintext filenames, v2 encrypted filenames)
# is told apart by its columns; this marks the vectors, so the format is
# never guessed from a blob's bytes.
SEALED = 3


def _pack(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary blob."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack(blob: bytes) -> list[float]:
    """Unpack a binary blob back to a float list."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


def content_hash(text: str) -> str:
    """Keyed hash of content, used to detect changes.

    Uses HMAC-SHA256 when an encryption key is available (prevents
    content verification by someone with only database access).
    Falls back to plain SHA-256 when no key is configured.
    """
    key = crypto.get_key()
    if key:
        return _hmac.new(key, text.encode("utf-8"), "sha256").hexdigest()[:16]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _filename_lookup_key(filename: str) -> str:
    """Opaque lookup key for a filename.

    HMAC-SHA256 when a key is available, plaintext filename otherwise.
    """
    key = crypto.get_key()
    if key:
        return _hmac.new(key, filename.encode("utf-8"), "sha256").hexdigest()
    return filename


def _protect_filename(filename: str) -> bytes:
    """Encrypt a filename for database storage."""
    key = crypto.get_key()
    if key:
        return crypto.encrypt(filename, key)
    return filename.encode("utf-8")


def _recover_filename(data: bytes) -> str:
    """Recover a filename from database storage."""
    if crypto.is_encrypted(data):
        return crypto.decrypt(data)
    return data.decode("utf-8")


def _get_conn(db_path: Path | None = None, seal: bool = True) -> sqlite3.Connection:
    """Open (and initialize if needed) the embeddings database, sealing
    it on first open unless ``seal`` is False (seal() counts for itself)."""
    if db_path is None:
        db_path = config.get().embeddings_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    _migrate_if_needed(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embeddings (
            lookup_key  TEXT PRIMARY KEY,
            filename    BLOB NOT NULL,
            embedding   BLOB NOT NULL,
            hash        TEXT NOT NULL
        )"""
    )
    if seal and conn.execute("PRAGMA user_version").fetchone()[0] < SEALED:
        _seal(conn)
    return conn


def _seal(conn: sqlite3.Connection) -> int:
    """Encrypt every vector still stored in the clear; return how many.

    Does nothing without a key (encryption off). One IMMEDIATE
    transaction, so a crash leaves the database as it was and two
    processes can't seal at once. A blob that already looks like a
    Fernet token is left alone, so a wrong key can never encrypt the
    vectors twice; the price is that a vector in the clear whose first
    bytes happen to be b"gA" (about 1 in 65,536) stays as it is, and
    _open_vector reads it as such.
    """
    key = crypto.get_key()
    if key is None:
        return 0
    # An UPDATE frees a long blob's old overflow pages without wiping
    # them, so the vectors in the clear stayed readable in the file
    # (found by a dry run on a real house: 768-float vectors spill onto
    # overflow pages; the 3-float ones in the first tests didn't).
    # secure_delete zeroes freed content, and the VACUUM below rebuilds
    # the file. What the filesystem keeps of the deleted rollback journal
    # is beyond reach from here.
    conn.execute("PRAGMA secure_delete = ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Read inside the lock: if another process sealed the database
        # while this one waited, every row is skipped below.
        rows = conn.execute("SELECT lookup_key, embedding FROM embeddings").fetchall()
        sealed = 0
        for lookup_key, blob in rows:
            if crypto.is_encrypted(blob):
                continue
            conn.execute(
                "UPDATE embeddings SET embedding = ? WHERE lookup_key = ?",
                (crypto.encrypt_bytes(blob, key), lookup_key),
            )
            sealed += 1
        conn.execute(f"PRAGMA user_version = {SEALED}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    if sealed:
        conn.execute("VACUUM")
    return sealed


def seal(db_path: Path | None = None) -> int:
    """Encrypt any vector still in the clear. Returns how many were.

    Opening a database seals it once (see _get_conn). Wake also calls
    this at boot, to catch vectors written in the clear afterwards by a
    session still running code from before the change.
    """
    if db_path is None:
        db_path = config.get().embeddings_db_path
    if not db_path.exists():
        return 0
    conn = _get_conn(db_path, seal=False)
    try:
        return _seal(conn)
    finally:
        conn.close()


def _open_vector(blob: bytes, key: bytes | None) -> list[float]:
    """Unpack a stored vector, decrypting it when it is a token."""
    if key is not None and crypto.is_encrypted(blob):
        try:
            return _unpack(crypto.decrypt_bytes(blob, key))
        except InvalidToken:
            pass  # a vector in the clear that happens to begin with b"gA"
    return _unpack(blob)


def _migrate_if_needed(conn: sqlite3.Connection) -> None:
    """Migrate from v1 schema (plaintext filenames) to v2 (encrypted).

    v1: filename TEXT PRIMARY KEY, embedding BLOB, hash TEXT
    v2: lookup_key TEXT PRIMARY KEY, filename BLOB, embedding BLOB, hash TEXT

    Old plaintext filenames are encrypted and HMAC'd during migration.
    Embedding vectors and content hashes are preserved as-is.
    """
    cursor = conn.execute("PRAGMA table_info(embeddings)")
    columns = {row[1] for row in cursor.fetchall()}

    if not columns:  # Table doesn't exist yet
        return

    if "lookup_key" in columns:  # Already v2
        return

    # v1 detected — read, drop, recreate, re-insert
    rows = conn.execute(
        "SELECT filename, embedding, hash FROM embeddings"
    ).fetchall()
    conn.execute("DROP TABLE embeddings")
    conn.execute(
        """CREATE TABLE embeddings (
            lookup_key  TEXT PRIMARY KEY,
            filename    BLOB NOT NULL,
            embedding   BLOB NOT NULL,
            hash        TEXT NOT NULL
        )"""
    )

    for old_filename, embedding_blob, old_hash in rows:
        conn.execute(
            "INSERT INTO embeddings (lookup_key, filename, embedding, hash) "
            "VALUES (?, ?, ?, ?)",
            (
                _filename_lookup_key(old_filename),
                _protect_filename(old_filename),
                embedding_blob,
                old_hash,
            ),
        )

    conn.commit()


def store(
    filename: str,
    embedding: list[float],
    text_hash: str,
    db_path: Path | None = None,
) -> None:
    """Store or update an embedding for a filename."""
    blob = _pack(embedding)
    key = crypto.get_key()
    if key is not None:
        blob = crypto.encrypt_bytes(blob, key)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO embeddings "
            "(lookup_key, filename, embedding, hash) VALUES (?, ?, ?, ?)",
            (
                _filename_lookup_key(filename),
                _protect_filename(filename),
                blob,
                text_hash,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def remove(filename: str, db_path: Path | None = None) -> None:
    """Remove an embedding by filename."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "DELETE FROM embeddings WHERE lookup_key = ?",
            (_filename_lookup_key(filename),),
        )
        conn.commit()
    finally:
        conn.close()


def get_hash(filename: str, db_path: Path | None = None) -> str | None:
    """Return stored content hash for a filename, or None if not indexed."""
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT hash FROM embeddings WHERE lookup_key = ?",
            (_filename_lookup_key(filename),),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def search(
    query_embedding: list[float],
    limit: int = 5,
    db_path: Path | None = None,
) -> list[dict]:
    """Find the top-N most similar entries by cosine similarity.

    Returns list of {filename, score} dicts, sorted by descending similarity.
    Returns empty list if the database doesn't exist or is empty.
    """
    if db_path is None:
        db_path = config.get().embeddings_db_path
    if not db_path.exists():
        return []

    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT filename, embedding FROM embeddings"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    key = crypto.get_key()
    scored = []
    for filename_protected, blob in rows:
        stored = _open_vector(blob, key)
        sim = _cosine_similarity(query_embedding, stored)
        scored.append({
            "filename": _recover_filename(filename_protected),
            "score": sim,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
