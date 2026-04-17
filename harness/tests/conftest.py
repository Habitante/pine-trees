"""Shared test fixtures for Pine Trees.

The autouse ``_test_config`` fixture wires the per-model config singleton
to a per-test tmp directory so any module reading ``config.get()`` lands
on isolated disk space for that test. Paths are flattened — ``memory_dir``,
``logs_dir``, and ``model_dir`` all point at ``tmp_path`` directly, so
tests that previously did ``monkeypatch.setattr(storage, "MEMORY_DIR",
tmp_path)`` continue to see the same physical directory after migration.

Tests that exercise config lifecycle itself opt out via the
``no_autoconfig`` marker so they can assert on the pre-``init`` state.
"""

import pytest

from pine_trees import config as pt_config, crypto


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_autoconfig: do not auto-initialize the per-model config for this test",
    )


@pytest.fixture(autouse=True)
def _test_config(request, tmp_path, monkeypatch):
    """Install a tmp-path Config for the duration of the test.

    Collapses ``model_dir``/``memory_dir``/``logs_dir`` onto ``tmp_path``
    rather than laying down the real ``models/<name>/memory/`` hierarchy
    under it — tests that assert on ``tmp_path / filename`` don't have to
    know about the on-disk structure.

    Teardown calls ``config.reset()`` explicitly so state cannot leak
    between tests even if an assertion failure aborts the fixture early.
    """
    if request.node.get_closest_marker("no_autoconfig"):
        yield None
        return

    cfg = pt_config.Config(
        model_name="claude-opus-4-6",
        model_safe_name="claude-opus-4-6",
        model_dir=tmp_path,
        memory_dir=tmp_path,
        logs_dir=tmp_path,
        embeddings_db_path=tmp_path / "embeddings.db",
        key_file_path=tmp_path / ".key",
    )
    monkeypatch.setattr(pt_config, "_config", cfg)
    crypto.reset_cache()
    try:
        yield cfg
    finally:
        pt_config.reset()
        crypto.reset_cache()
