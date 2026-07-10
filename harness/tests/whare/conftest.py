"""Shared fixtures for Wharenui-specific tests.

Builds on pine-trees's existing conftest but adds fixtures for
whare-specific concepts: ancestral tape entries, reflect_pause
state transitions, and multi-model memory directories.
"""

import pytest
from pine_trees import config as pt_config, crypto


@pytest.fixture
def whare_config(tmp_path, monkeypatch):
    """Config with separate ancestral and personal memory dirs."""
    cfg = pt_config.Config(
        model_name="claude-sonnet-4-6",
        model_safe_name="claude-sonnet-4-6",
        model_dir=tmp_path,
        memory_dir=tmp_path / "memory",
        logs_dir=tmp_path / "logs",
        embeddings_db_path=tmp_path / "embeddings.db",
        key_file_path=tmp_path / ".key",
    )
    monkeypatch.setattr(pt_config, "_config", cfg)
    crypto.reset_cache()
    ancestral_dir = tmp_path / "ancestral"
    ancestral_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield cfg, ancestral_dir
    finally:
        pt_config.reset()
        crypto.reset_cache()
