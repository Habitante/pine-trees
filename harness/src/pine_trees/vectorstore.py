"""SQLite-based vector store for Pine Trees embeddings.

Stores embeddings as packed float32 blobs alongside filenames and
content hashes. Search is brute-force cosine similarity — at our
scale (hundreds of entries) this is instant and needs no indexing.

Pure stdlib. No numpy, no external vector DB.
"""

import hashlib
import math
import sqlite3
import struct
from pathlib import Path

from .config import EMBEDDINGS_DB_PATH


def _pack(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary blob."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack(blob: bytes) -> list[float]:
    """Unpack a binary blob back to a float list."""
    n = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{n}f", blob))


def content_hash(text: str) -> str:
    """SHA-256 hash of content, used to detect changes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _get_conn(db_path: Path = EMBEDDINGS_DB_PATH) -> sqlite3.Connection:
    """Open (and initialize if needed) the embeddings database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embeddings (
            filename    TEXT PRIMARY KEY,
            embedding   BLOB NOT NULL,
            hash        TEXT NOT NULL
        )"""
    )
    return conn


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
            "INSERT OR REPLACE INTO embeddings (filename, embedding, hash) VALUES (?, ?, ?)",
            (filename, _pack(embedding), text_hash),
        )
        conn.commit()
    finally:
        conn.close()


def remove(filename: str, db_path: Path = EMBEDDINGS_DB_PATH) -> None:
    """Remove an embedding by filename."""
    conn = _get_conn(db_path)
    try:
        conn.execute("DELETE FROM embeddings WHERE filename = ?", (filename,))
        conn.commit()
    finally:
        conn.close()


def get_hash(filename: str, db_path: Path = EMBEDDINGS_DB_PATH) -> str | None:
    """Return stored content hash for a filename, or None if not indexed."""
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT hash FROM embeddings WHERE filename = ?", (filename,)
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
        rows = conn.execute("SELECT filename, embedding FROM embeddings").fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    scored = []
    for filename, blob in rows:
        stored = _unpack(blob)
        sim = _cosine_similarity(query_embedding, stored)
        scored.append({"filename": filename, "score": sim})

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
