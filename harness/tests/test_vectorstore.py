"""Tests for vectorstore.py — SQLite vector storage and cosine search."""

from pine_trees import vectorstore


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
