"""The plaintext tape file: written just before the CLI connects, gone in
every way out.

The tape reaches the CLI as --system-prompt-file because Windows caps a
command line far below a tape's length. Until 2026-10-04 it had one fixed
name and was deleted only on a clean connect or an SDK error, so an
interrupted connect or a refused resume left a decrypted copy of the
whole tape behind (found by an audit of the old cc-wake work).
"""

import fnmatch
import os
import time

import anyio
import pytest

from pine_trees import agent, bootstrap
from pine_trees import config as pt_config


class _Entry:
    filename = "stub.md"
    summary = "stub"
    mtime = 0.0
    pinned = False
    quiet = False


class _Sentinel(Exception):
    pass


@pytest.fixture
def harness_dir(monkeypatch, tmp_path):
    d = tmp_path / "harness"
    d.mkdir()
    monkeypatch.setattr(agent, "HARNESS_DIR", d)
    monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
    monkeypatch.setattr(bootstrap, "list_entries", lambda: [_Entry()])
    monkeypatch.setattr(bootstrap, "assemble_tape",
                        lambda n=3, genesis_mode=False: "the whole tape")
    return d


def _tapes(d):
    return sorted(p.name for p in d.glob(".tape*.md"))


def test_each_session_and_process_gets_its_own_file(harness_dir):
    a, b = agent._tape_file("2026-10-04-1200"), agent._tape_file("2026-10-04-1201")
    assert a != b
    assert a.parent == harness_dir
    assert str(os.getpid()) in a.name
    # harness/.gitignore covers it
    assert fnmatch.fnmatch(a.name, ".tape*.md")


def test_the_sweep_takes_only_files_old_enough_to_be_orphans(harness_dir):
    now = time.time()
    old = harness_dir / ".tape.md"  # the old fixed name, from a killed boot
    fresh = harness_dir / ".tape-2026-10-04-1200-999.md"  # another terminal, booting now
    old.write_text("x")
    fresh.write_text("x")
    os.utime(old, (now - agent.TAPE_FILE_STALE_AFTER - 5,) * 2)

    assert agent._sweep_tape_files(now=now) == 1
    assert _tapes(harness_dir) == [fresh.name]


def test_an_interrupted_connect_leaves_no_tape(harness_dir, monkeypatch):
    # Any non-SDK exception while connecting (Ctrl-C is the common one)
    # used to skip the deletion.
    seen = {}

    def _connect(*a, **kw):
        seen["tapes_at_connect"] = _tapes(harness_dir)
        raise _Sentinel()

    monkeypatch.setattr(agent, "ClaudeSDKClient", _connect)
    with pytest.raises(_Sentinel):
        anyio.run(agent._run_async)
    assert len(seen["tapes_at_connect"]) == 1  # the CLI had its tape to read
    assert _tapes(harness_dir) == []


def test_a_refused_resume_never_writes_the_tape(harness_dir, monkeypatch):
    # A sidecar without a CLI session id can't be resumed; the tape used
    # to be written before that check, then left when it exited.
    prior = {"phase": "window", "instance": pt_config.get().model_safe_name,
             "session": "2026-10-04-1200", "date": "2026-10-04"}
    monkeypatch.setattr(agent.sessions, "load_latest", lambda: prior)
    monkeypatch.setattr(agent, "ClaudeSDKClient",
                        lambda *a, **kw: pytest.fail("must not connect"))
    with pytest.raises(SystemExit):
        anyio.run(lambda: agent._run_async(continue_session=True))
    assert _tapes(harness_dir) == []


def test_genesis_leaves_no_tape_when_connect_fails(harness_dir, monkeypatch):
    monkeypatch.setattr(bootstrap, "list_entries", lambda: [])

    def _connect(*a, **kw):
        raise _Sentinel()

    monkeypatch.setattr(agent, "ClaudeSDKClient", _connect)
    with pytest.raises(_Sentinel):
        anyio.run(lambda: agent._run_genesis_session(1, 1))
    assert _tapes(harness_dir) == []
