"""Tests for the per-model config singleton and path derivation.

Covers ``sanitize_model_name``, ``init``/``get``/``reset``, and the shape
of the resolved ``Config`` dataclass.

These tests exercise the lifecycle of the singleton itself, so they opt
out of the autouse fixture that every other test relies on.
"""

import pytest

from pine_trees import config


pytestmark = pytest.mark.no_autoconfig


@pytest.fixture(autouse=True)
def reset_config():
    """Ensure each test starts with no config set."""
    config.reset()
    yield
    config.reset()


# --- sanitize_model_name ---


def test_sanitize_preserves_anthropic_ids():
    """Current Anthropic IDs are already filesystem-safe."""
    assert config.sanitize_model_name("claude-opus-4-6") == "claude-opus-4-6"
    assert config.sanitize_model_name("claude-sonnet-4-6") == "claude-sonnet-4-6"
    assert config.sanitize_model_name("claude-haiku-4-5") == "claude-haiku-4-5"


def test_sanitize_preserves_dots_and_dashes():
    """Dots, dashes, underscores are all allowed."""
    assert config.sanitize_model_name("model.v1.2-beta") == "model.v1.2-beta"
    assert config.sanitize_model_name("some_model") == "some_model"


def test_sanitize_replaces_unsafe_chars():
    """Colons, slashes, brackets become underscores."""
    assert config.sanitize_model_name("vendor/model:tag") == "vendor_model_tag"
    assert config.sanitize_model_name("model[1m]") == "model_1m_"
    assert config.sanitize_model_name("a b c") == "a_b_c"


# --- get() before init() ---


def test_get_without_init_raises():
    """Accessing config before init is a programmer error, not a silent default."""
    with pytest.raises(RuntimeError, match="Config not initialized"):
        config.get()


# --- init() populates the singleton ---


def test_init_returns_config_with_derived_paths():
    """init() produces a Config whose paths all descend from model_dir."""
    cfg = config.init("claude-opus-4-6")
    assert cfg.model_name == "claude-opus-4-6"
    assert cfg.model_safe_name == "claude-opus-4-6"
    assert cfg.model_dir == config.MODELS_DIR / "claude-opus-4-6"
    assert cfg.memory_dir == cfg.model_dir / "memory"
    assert cfg.logs_dir == cfg.model_dir / "logs"
    assert cfg.embeddings_db_path == cfg.model_dir / "embeddings.db"
    assert cfg.key_file_path == cfg.model_dir / ".key"


def test_init_preserves_raw_model_name():
    """model_name stays raw (for passing back to the Agent SDK) while
    model_safe_name is the on-disk identifier."""
    cfg = config.init("vendor/model:tag")
    assert cfg.model_name == "vendor/model:tag"
    assert cfg.model_safe_name == "vendor_model_tag"
    assert cfg.model_dir.name == "vendor_model_tag"


def test_get_returns_current_config():
    cfg = config.init("claude-sonnet-4-6")
    assert config.get() is cfg


def test_init_replaces_previous_config():
    """The CLI may switch models mid-process; init() overwrites."""
    config.init("claude-opus-4-6")
    cfg2 = config.init("claude-haiku-4-5")
    assert config.get() is cfg2
    assert cfg2.model_safe_name == "claude-haiku-4-5"


def test_isolation_across_models():
    """Two different models produce fully disjoint paths."""
    opus = config.init("claude-opus-4-6")
    opus_paths = (opus.memory_dir, opus.logs_dir, opus.embeddings_db_path, opus.key_file_path)

    sonnet = config.init("claude-sonnet-4-6")
    sonnet_paths = (sonnet.memory_dir, sonnet.logs_dir, sonnet.embeddings_db_path, sonnet.key_file_path)

    for p1, p2 in zip(opus_paths, sonnet_paths):
        assert p1 != p2


# --- reset ---


def test_reset_clears_config():
    config.init("claude-opus-4-6")
    config.reset()
    with pytest.raises(RuntimeError):
        config.get()
