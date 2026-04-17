"""Tests for the one-shot legacy-layout migration."""

import pytest

from pine_trees import migrate


pytestmark = pytest.mark.no_autoconfig


def _legacy_layout(harness: "pytest.Path") -> tuple:
    """Create a full legacy layout under ``harness``; return key paths."""
    memory = harness / "memory"
    memory.mkdir()
    (memory / "entry.md").write_text("entry body\n", encoding="utf-8")

    logs = harness / "logs"
    logs.mkdir()
    (logs / "session.log").write_text("log body\n", encoding="utf-8")

    db = harness / "embeddings.db"
    db.write_bytes(b"sqlite-ish\n")

    key = harness / ".key"
    key.write_bytes(b"fake-fernet-key-44bytes-padding-for-the-test-")

    return memory, logs, db, key


def test_no_legacy_is_noop(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    models = harness / "models"

    result = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    assert result is False
    assert not models.exists()
    assert not (tmp_path / "model.txt").exists()


def test_moves_all_four_artifacts(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    memory, logs, db, key = _legacy_layout(harness)
    models = harness / "models"

    result = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    assert result is True

    target = models / "claude-opus-4-6"
    assert (target / "memory" / "entry.md").read_text(encoding="utf-8") == "entry body\n"
    assert (target / "logs" / "session.log").read_text(encoding="utf-8") == "log body\n"
    assert (target / "embeddings.db").read_bytes() == b"sqlite-ish\n"
    assert (target / ".key").read_bytes().startswith(b"fake-fernet-key")

    # Originals are gone
    assert not memory.exists()
    assert not logs.exists()
    assert not db.exists()
    assert not key.exists()


def test_creates_model_txt_at_project_root(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    _legacy_layout(harness)
    models = harness / "models"

    migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )

    assert (tmp_path / "model.txt").read_text(encoding="utf-8").strip() == "claude-opus-4-6"


def test_preserves_existing_model_txt(tmp_path):
    """A user who set model.txt to something else (or already ran genesis
    for a different model) shouldn't have it overwritten by the migration."""
    harness = tmp_path / "harness"
    harness.mkdir()
    _legacy_layout(harness)
    models = harness / "models"

    existing = tmp_path / "model.txt"
    existing.write_text("claude-sonnet-4-6\n", encoding="utf-8")

    migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )

    assert existing.read_text(encoding="utf-8") == "claude-sonnet-4-6\n"


def test_refuses_to_clobber_existing_target(tmp_path, capsys):
    """If models/claude-opus-4-6/ already exists, migration stops — never
    overwrite a partial or manual migration, never merge two layouts."""
    harness = tmp_path / "harness"
    harness.mkdir()
    memory, _, _, _ = _legacy_layout(harness)
    models = harness / "models"
    target = models / "claude-opus-4-6"
    target.mkdir(parents=True)
    (target / "sentinel.md").write_text("don't touch me\n", encoding="utf-8")

    result = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    assert result is False

    # Sentinel survives
    assert (target / "sentinel.md").read_text(encoding="utf-8") == "don't touch me\n"
    # Legacy layout untouched
    assert memory.exists()
    assert (memory / "entry.md").exists()


def test_partial_legacy_layout(tmp_path):
    """Only .key and embeddings.db present (no memory/ or logs/) still
    triggers migration — some users may have lost one or the other."""
    harness = tmp_path / "harness"
    harness.mkdir()
    (harness / ".key").write_bytes(b"key")
    (harness / "embeddings.db").write_bytes(b"db")
    models = harness / "models"

    result = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    assert result is True

    target = models / "claude-opus-4-6"
    assert (target / ".key").exists()
    assert (target / "embeddings.db").exists()
    assert not (target / "memory").exists()
    assert not (target / "logs").exists()


def test_idempotent(tmp_path):
    """Second call is a no-op — target exists, no legacy left."""
    harness = tmp_path / "harness"
    harness.mkdir()
    _legacy_layout(harness)
    models = harness / "models"

    first = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    second = migrate.migrate_legacy_layout_if_needed(
        harness_dir=harness, models_dir=models, project_root=tmp_path,
    )
    assert first is True
    assert second is False
