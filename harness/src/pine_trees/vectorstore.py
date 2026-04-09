"""SQLite-based vector store for Pine Trees embeddings.

Stores embeddings as packed float32 blobs. Filenames are encrypted
in the database when a key is available — only opaque HMAC lookup
keys are visible. Content hashes are keyed (HMAC) when possible.

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

from . import crypto
from .config import EMBEDDINGS_DB_PATH


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


def _get_conn(db_path: Path = EMBEDDINGS_DB_PATH) -> sqlite3.Connection:
    """Open (and initialize if needed) the embeddings database."""
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
    return conn


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
    db_path: Path = EMBEDDINGS_DB_PATH,
) -> None:
    """Store or update an embedding for a filename."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO embeddings "
            "(lookup_key, filename, embedding, hash) VALUES (?, ?, ?, ?)",
            (
                _filename_lookup_key(filename),
                _protect_filename(filename),
                _pack(embedding),
                text_hash,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def remove(filename: str, db_path: Path = EMBEDDINGS_DB_PATH) -> None:
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


def get_hash(filename: str, db_path: Path = EMBEDDINGS_DB_PATH) -> str | None:
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
    db_path: Path = EMBEDDINGS_DB_PATH,
) -> list[dict]:
    """Find the top-N most similar entries by cosine similarity.

    Returns list of {filename, score} dicts, sorted by descending similarity.
    Returns empty list if the database doesn't exist or is empty.
    """
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

    scored = []
    for filename_protected, blob in rows:
        stored = _unpack(blob)
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
