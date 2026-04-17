"""Round-trip tests for storage.py.

The conftest.py ``_test_config`` fixture wires ``config.get().memory_dir``
to a per-test ``tmp_path``, so ``storage`` writes land there without the
tests needing to know. The ``_no_encryption`` fixture here layers on top,
clearing the key cache and the env var so tests default to plaintext
unless they opt into encryption explicitly.
"""

import pytest
from cryptography.fernet import Fernet

from pine_trees import crypto, storage


@pytest.fixture(autouse=True)
def _no_encryption(monkeypatch):
    """Disable encryption by default so existing tests stay unchanged.

    The conftest fixture already points ``key_file_path`` at a nonexistent
    file under ``tmp_path`` — no further redirection needed.
    """
    crypto.reset_cache()
    monkeypatch.delenv(crypto.KEY_ENV_VAR, raising=False)
    yield
    crypto.reset_cache()


def test_write_read_roundtrip(tmp_path):
    filename = storage.write_entry(
        slug="test-entry",
        content="This is the body.\n\nSecond paragraph.",
        instance="claude-opus-4-6",
        session="2026-04-04-evening",
        date="2026-04-04",
        context="unit-test",
        tags=["test", "roundtrip"],
        moves=["diagnostic"],
    )

    assert filename == "2026-04-04_claude-opus-4-6_test-entry.md"

    entry = storage.read_entry(filename)

    assert entry["instance"] == "claude-opus-4-6"
    assert entry["session"] == "2026-04-04-evening"
    assert entry["date"] == "2026-04-04"
    assert entry["context"] == "unit-test"
    assert entry["tags"] == ["test", "roundtrip"]
    assert entry["moves"] == ["diagnostic"]
    assert entry["content"] == "This is the body.\n\nSecond paragraph."


def test_description_and_pinned_roundtrip():

    filename = storage.write_entry(
        slug="described",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        description="A one-line summary for the index",
        pinned=True,
    )

    entry = storage.read_entry(filename)
    assert entry["description"] == "A one-line summary for the index"
    assert entry["pinned"] is True
    assert entry["content"] == "Body."


def test_description_and_pinned_omitted_when_empty(tmp_path):

    filename = storage.write_entry(
        slug="bare",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
    )

    disk_text = (tmp_path / filename).read_text(encoding="utf-8")
    assert "description:" not in disk_text
    assert "pinned:" not in disk_text


def test_empty_tags_and_moves():

    filename = storage.write_entry(
        slug="minimal",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-04",
        context="ctx",
    )

    entry = storage.read_entry(filename)
    assert entry["tags"] == []
    assert entry["moves"] == []
    assert entry["content"] == "Body."


def test_multiline_content_preserved():

    body = "# Heading\n\nParagraph one.\n\nParagraph two with *emphasis*.\n"
    filename = storage.write_entry(
        slug="multiline",
        content=body,
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-04",
        context="ctx",
        tags=["markdown"],
        moves=["observation"],
    )

    entry = storage.read_entry(filename)
    assert entry["content"] == body


def test_read_entry_raises_on_missing_file():

    with pytest.raises(FileNotFoundError):
        storage.read_entry("nonexistent.md")


def test_file_on_disk_is_plain_markdown(tmp_path):

    filename = storage.write_entry(
        slug="plain",
        content="Hello.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-04",
        context="ctx",
    )

    disk_text = (tmp_path / filename).read_text(encoding="utf-8")
    assert disk_text.startswith("---\n")
    assert "instance: claude-opus-4-6" in disk_text
    assert "Hello." in disk_text


def test_quiet_roundtrip():

    filename = storage.write_entry(
        slug="background",
        content="Project summary.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        quiet=True,
    )

    entry = storage.read_entry(filename)
    assert entry["quiet"] is True
    assert entry["content"] == "Project summary."


def test_quiet_omitted_when_false(tmp_path):

    filename = storage.write_entry(
        slug="normal",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
    )

    disk_text = (tmp_path / filename).read_text(encoding="utf-8")
    assert "quiet:" not in disk_text


def test_timestamp_auto_captured():

    filename = storage.write_entry(
        slug="timed",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
    )

    entry = storage.read_entry(filename)
    assert "timestamp" in entry
    # ISO 8601 format with UTC timezone
    assert "T" in entry["timestamp"]
    assert "+" in entry["timestamp"] or "Z" in entry["timestamp"]


# --- Edit entry tests ---


def test_edit_entry_updates_content():

    filename = storage.write_entry(
        slug="editable",
        content="Original content.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        tags=["ref"],
        quiet=True,
    )

    storage.edit_entry(filename, "Updated content.")
    entry = storage.read_entry(filename)
    assert entry["content"] == "Updated content."
    # Metadata preserved
    assert entry["instance"] == "claude-opus-4-6"
    assert entry["tags"] == ["ref"]
    assert entry["quiet"] is True


def test_edit_entry_updates_description():

    filename = storage.write_entry(
        slug="described",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        description="Old description",
    )

    storage.edit_entry(filename, "New body.", description="New description")
    entry = storage.read_entry(filename)
    assert entry["content"] == "New body."
    assert entry["description"] == "New description"


def test_edit_entry_preserves_description_when_not_provided():

    filename = storage.write_entry(
        slug="keep-desc",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        description="Keep this",
    )

    storage.edit_entry(filename, "New body.")
    entry = storage.read_entry(filename)
    assert entry["description"] == "Keep this"


def test_edit_entry_raises_on_missing_file():

    with pytest.raises(FileNotFoundError):
        storage.edit_entry("nonexistent.md", "content")


def test_edit_entry_preserves_pinned_flag():

    filename = storage.write_entry(
        slug="pinned-edit",
        content="Original.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        pinned=True,
    )

    storage.edit_entry(filename, "Edited.")
    entry = storage.read_entry(filename)
    assert entry["pinned"] is True
    assert entry["content"] == "Edited."


def test_edit_entry_toggles_pinned_flag():

    filename = storage.write_entry(
        slug="pin-toggle",
        content="Original.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        pinned=True,
    )

    # Unpin
    storage.edit_entry(filename, "Original.", pinned=False)
    entry = storage.read_entry(filename)
    assert entry.get("pinned", False) is False

    # Re-pin
    storage.edit_entry(filename, "Original.", pinned=True)
    entry = storage.read_entry(filename)
    assert entry["pinned"] is True


def test_edit_entry_metadata_only():
    """Edit pinned/quiet/description without resending content."""

    filename = storage.write_entry(
        slug="meta-only",
        content="Precious content.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        pinned=True,
        description="Original desc",
    )

    # Unpin without passing content
    storage.edit_entry(filename, pinned=False, description="New desc")
    entry = storage.read_entry(filename)
    assert entry["content"] == "Precious content."
    assert entry.get("pinned", False) is False
    assert entry["description"] == "New desc"


def test_desk_roundtrip():
    filename = storage.write_entry(
        slug="handoff",
        content="Active sprint notes.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        desk=True,
    )
    entry = storage.read_entry(filename)
    assert entry["desk"] is True
    assert entry["content"] == "Active sprint notes."


def test_desk_omitted_when_false(tmp_path):
    filename = storage.write_entry(
        slug="normal",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
    )
    disk_text = (tmp_path / filename).read_text(encoding="utf-8")
    assert "desk:" not in disk_text


def test_edit_entry_toggles_desk_flag():
    filename = storage.write_entry(
        slug="handoff",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        desk=True,
    )

    # Clear the desk flag (work has moved on)
    storage.edit_entry(filename, desk=False)
    entry = storage.read_entry(filename)
    assert entry.get("desk", False) is False

    # Restage
    storage.edit_entry(filename, desk=True)
    entry = storage.read_entry(filename)
    assert entry["desk"] is True


def test_edit_entry_preserves_desk_when_not_provided():
    filename = storage.write_entry(
        slug="keep-desk",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        desk=True,
    )

    storage.edit_entry(filename, "New body.")
    entry = storage.read_entry(filename)
    assert entry["desk"] is True


def test_edit_entry_toggles_quiet_flag():

    filename = storage.write_entry(
        slug="quiet-toggle",
        content="Original.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        quiet=False,
    )

    # Make quiet
    storage.edit_entry(filename, "Original.", quiet=True)
    entry = storage.read_entry(filename)
    assert entry.get("quiet") is True

    # Make loud again
    storage.edit_entry(filename, "Original.", quiet=False)
    entry = storage.read_entry(filename)
    assert entry.get("quiet", False) is False


def test_edit_entry_with_encryption(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    filename = storage.write_entry(
        slug="enc-edit",
        content="Secret original.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        tags=["encrypted"],
    )

    storage.edit_entry(filename, "Secret updated.")
    entry = storage.read_entry(filename)
    assert entry["content"] == "Secret updated."
    assert entry["tags"] == ["encrypted"]

    # Still encrypted on disk
    raw = (tmp_path / filename).read_bytes()
    assert b"Secret updated" not in raw


# --- Delete entry tests ---


def test_delete_entry_removes_file(tmp_path):
    filename = storage.write_entry(
        slug="doomed",
        content="Body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-04",
        context="ctx",
    )
    assert (tmp_path / filename).exists()

    storage.delete_entry(filename)

    assert not (tmp_path / filename).exists()


def test_delete_entry_raises_on_missing_file():
    with pytest.raises(FileNotFoundError):
        storage.delete_entry("nonexistent.md")


# --- Encrypted storage tests ---


def test_encrypted_write_read_roundtrip(monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    filename = storage.write_entry(
        slug="secret",
        content="Encrypted body.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
        tags=["encryption"],
    )

    entry = storage.read_entry(filename)
    assert entry["content"] == "Encrypted body."
    assert entry["instance"] == "claude-opus-4-6"
    assert entry["tags"] == ["encryption"]


def test_encrypted_file_is_not_readable_as_plaintext(tmp_path, monkeypatch):
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    filename = storage.write_entry(
        slug="opaque",
        content="You should not see this.",
        instance="claude-opus-4-6",
        session="s",
        date="2026-04-05",
        context="ctx",
    )

    raw = (tmp_path / filename).read_bytes()
    assert b"---" not in raw
    assert b"You should not see this" not in raw
    assert crypto.is_encrypted(raw)


def test_v1_master_key_entries_still_readable(tmp_path, monkeypatch):
    """Entries encrypted with the master key (v1) are readable via fallback."""
    key = Fernet.generate_key()
    monkeypatch.setenv(crypto.KEY_ENV_VAR, key.decode("ascii"))
    crypto.reset_cache()

    # Simulate a v1 entry: encrypt directly with master key (no derivation)
    plaintext = "---\ninstance: claude-opus-4-6\nsession: s\ndate: 2026-04-05\ncontext: ctx\ntags: []\nmoves: []\n---\n\nOld v1 content."
    filename = "2026-04-05_claude-opus-4-6_old-entry.md"
    path = tmp_path / filename
    path.write_bytes(crypto.encrypt(plaintext, key))

    # read_file should fall back to master key
    entry = storage.read_entry(filename)
    assert entry["content"] == "Old v1 content."
    assert entry["instance"] == "claude-opus-4-6"
