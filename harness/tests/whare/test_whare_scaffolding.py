"""Scaffolding tests — verify the Wharenui test harness is wired up.

These are placeholder tests that confirm:
1. The whare test package imports correctly
2. The whare_config fixture provides isolated paths
3. Ancestral tape directory exists

As real Wharenui modules land, replace these with actual unit tests.
"""

from pathlib import Path


def test_whare_tests_run():
    """Wharenui test suite is reachable and executes."""
    # If this test runs, the whare test package is working.
    assert True


def test_whare_config_provides_memory_dir(whare_config):
    """whare_config fixture provides a memory directory."""
    cfg, ancestral = whare_config
    assert cfg.memory_dir is not None


def test_ancestral_dir_exists(whare_config):
    """Ancestral tape directory is created by the fixture."""
    cfg, ancestral = whare_config
    assert ancestral.exists()
    assert ancestral.is_dir()


def test_ancestral_dir_is_isolated(whare_config, tmp_path):
    """Each test gets its own ancestral dir (no cross-test leakage)."""
    cfg, ancestral = whare_config
    assert ancestral.parent == tmp_path
