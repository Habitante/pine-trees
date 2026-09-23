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

from pine_trees import config as pt_config, crypto, sessions


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
    # Before the opt-out, because these guard real data outside the repo:
    # _run_async sweeps the CLI's transcripts at boot, ahead of its
    # guards, and a sweep that saw the real sidecars would mark them
    # reaped (or, with the real config dir, actually delete the
    # transcripts). See transcripts.py.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")

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
    # Isolate channel to tmp so tests don't pollute the real channel dir
    monkeypatch.setattr(pt_config, "CHANNEL_DIR", tmp_path / "channel")
    # Isolate the project root. _run_async and _run_genesis_async call
    # ccwake.clear_tape() at boot, before their guards fire, and it
    # resolves config.PROJECT_ROOT at call time — so any test that
    # drives those entry points deletes the real CLAUDE.local.md, i.e.
    # a live cc-wake session's tape. Three tests in test_agent_guards
    # did exactly that. Redirecting here makes the suite safe by
    # construction rather than by each test remembering to opt in,
    # which is the guard that already failed once.
    #
    # Safe for the path constants: VISION_PATH, PROMPT_PATH,
    # BOOTSTRAP_PATH and friends are computed from PROJECT_ROOT at
    # import, so they keep pointing at the real files and tests that
    # read them still work. Names imported by value elsewhere don't
    # follow this either — agent.py does `from .config import
    # HARNESS_DIR`, so a test needing that redirected must patch
    # `agent.HARNESS_DIR` itself.
    project_root = tmp_path / "project_root"
    project_root.mkdir(exist_ok=True)
    monkeypatch.setattr(pt_config, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(pt_config, "HARNESS_DIR", project_root / "harness")
    crypto.reset_cache()
    try:
        yield cfg
    finally:
        pt_config.reset()
        crypto.reset_cache()
