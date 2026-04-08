"""Tests for embedder.py.

Uses monkeypatching to avoid real Ollama calls in unit tests.
Integration test (marked) hits the real endpoint.
"""

import json
import pytest

from pine_trees import embedder


class FakeResponse:
    """Minimal stand-in for urllib response context manager."""

    def __init__(self, data: dict):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_fake_urlopen(response_data):
    """Return a fake urlopen that returns a fixed response."""
    def fake_urlopen(req, timeout=None):
        return FakeResponse(response_data)
    return fake_urlopen


def test_embed_document_adds_prefix(monkeypatch):
    """Verify the search_document prefix is prepended."""
    captured = {}

    def spy_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return FakeResponse({"embeddings": [[0.1, 0.2, 0.3]]})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", spy_urlopen)

    result = embedder.embed_document("test text")

    assert captured["body"]["input"].startswith("search_document: ")
    assert "test text" in captured["body"]["input"]
    assert result == [0.1, 0.2, 0.3]


def test_embed_query_adds_prefix(monkeypatch):
    """Verify the search_query prefix is prepended."""
    captured = {}

    def spy_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return FakeResponse({"embeddings": [[0.4, 0.5, 0.6]]})

    monkeypatch.setattr(embedder.urllib.request, "urlopen", spy_urlopen)

    result = embedder.embed_query("find something")

    assert captured["body"]["input"].startswith("search_query: ")
    assert result == [0.4, 0.5, 0.6]


def test_embed_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(
        embedder.urllib.request, "urlopen",
        _make_fake_urlopen({"embeddings": []}),
    )

    with pytest.raises(ValueError, match="Empty embedding"):
        embedder.embed_document("anything")


def test_embed_raises_connection_error_on_network_failure(monkeypatch):
    import urllib.error

    def fail_urlopen(req, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(embedder.urllib.request, "urlopen", fail_urlopen)

    with pytest.raises(ConnectionError, match="Ollama unreachable"):
        embedder.embed_document("anything")


@pytest.mark.integration
def test_embed_real_ollama():
    """Integration test — requires Ollama running with nomic-embed-text."""
    vec = embedder.embed_document("This is a test of the embedding system.")
    assert isinstance(vec, list)
    assert len(vec) == 768
    assert all(isinstance(v, float) for v in vec)
