"""Tests for the wake/genesis safety guards in agent.py.

These pin the behavior of three helpers and the two guards that call them:
- _print_claude_api_unreachable: branches on SDK exception type
- _print_wake_without_genesis:   refuses ./wake on an empty corpus
- _print_genesis_on_existing:    refuses ./genesis on a non-empty corpus

The guards themselves live at the top of _run_async() and _run_genesis_async(),
both of which are async. We test them by monkeypatching bootstrap.list_entries
and crypto.ensure_key (so nothing touches the real .key file) and driving the
async entry points with anyio.run.
"""

from unittest.mock import patch

import anyio
import pytest
from claude_agent_sdk import (
    CLIConnectionError,
    CLINotFoundError,
    ClaudeSDKError,
    ProcessError,
)

from pine_trees import agent, bootstrap


# ---------- _print_claude_api_unreachable branching ----------

class TestPrintClaudeApiUnreachable:
    def test_cli_not_found_mentions_install(self, capsys):
        agent._print_claude_api_unreachable(CLINotFoundError("claude not found"))
        out = capsys.readouterr().out
        assert "not installed" in out
        assert "https://claude.ai/code" in out
        assert "claude --version" in out

    def test_cli_connection_error_mentions_auth_and_plans(self, capsys):
        # Note: CLINotFoundError is a subclass of CLIConnectionError, so the
        # branch order matters — this test uses a plain CLIConnectionError.
        agent._print_claude_api_unreachable(CLIConnectionError("could not connect"))
        out = capsys.readouterr().out
        assert "Cannot connect" in out
        assert "claude" in out
        assert "https://claude.ai/plans" in out
        assert "api.anthropic.com" in out

    def test_process_error_mentions_auth_expiry_and_rate_limit(self, capsys):
        agent._print_claude_api_unreachable(ProcessError("exit 1"))
        out = capsys.readouterr().out
        assert "process exited" in out
        assert "Authentication expired" in out
        assert "Rate limit" in out

    def test_fallback_for_unknown_sdk_error(self, capsys):
        class WeirdSDKError(ClaudeSDKError):
            pass
        agent._print_claude_api_unreachable(WeirdSDKError("oh no"))
        out = capsys.readouterr().out
        assert "Claude Agent SDK error" in out
        assert "WeirdSDKError" in out


# ---------- _print_wake_without_genesis content ----------

class TestPrintWakeWithoutGenesis:
    def test_points_at_genesis(self, capsys):
        agent._print_wake_without_genesis()
        out = capsys.readouterr().out
        assert "No memory to wake into" in out
        assert "./genesis" in out
        assert "./wake" in out


# ---------- _print_genesis_on_existing content ----------

class TestPrintGenesisOnExisting:
    def test_mentions_count_and_rm_path(self, capsys):
        from pine_trees.config import KEY_FILE_PATH, MEMORY_DIR
        agent._print_genesis_on_existing(42)
        out = capsys.readouterr().out
        assert "42 entries" in out
        assert "./wake" in out
        assert "rm -rf" in out
        assert str(MEMORY_DIR) in out
        assert str(KEY_FILE_PATH) in out
        assert "./genesis" in out
        assert "no delete" in out.lower()

    def test_pluralization_for_single_entry(self, capsys):
        agent._print_genesis_on_existing(1)
        out = capsys.readouterr().out
        assert "1 entry" in out
        assert "1 entries" not in out


# ---------- The actual guards inside _run_async / _run_genesis_async ----------

class _FakeEntry:
    """Minimal stand-in for bootstrap.EntrySummary when we only need truthiness."""
    filename = "stub.md"
    summary = "stub"
    mtime = 0.0
    pinned = False
    quiet = False


class TestWakeGuardRefusesEmptyCorpus:
    def test_run_async_exits_when_no_entries(self, monkeypatch, capsys):
        # Prevent crypto.ensure_key from touching the real key file.
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        # Simulate an empty corpus.
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])
        # The SDK should never be reached if the guard fires. Replace it with
        # something that would explode loudly if called.
        def _boom(*a, **kw):
            raise AssertionError("SDK client should not be constructed")
        monkeypatch.setattr(agent, "ClaudeSDKClient", _boom)

        with pytest.raises(SystemExit) as exc:
            anyio.run(agent._run_async)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "No memory to wake into" in out
        assert "./genesis" in out

    def test_run_async_does_not_exit_when_entries_exist(self, monkeypatch):
        # When the corpus is non-empty the guard must NOT fire. We stop the
        # test before the SDK gets touched by making ClaudeSDKClient raise a
        # sentinel exception we can catch — that proves the guard passed and
        # execution reached the SDK setup.
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [_FakeEntry()])
        monkeypatch.setattr(bootstrap, "assemble_tape",
                            lambda n=3, genesis_mode=False: "tape")

        class _Sentinel(Exception):
            pass

        def _raise(*a, **kw):
            raise _Sentinel()
        monkeypatch.setattr(agent, "ClaudeSDKClient", _raise)

        # We expect _Sentinel, not SystemExit — proving the wake guard did
        # not refuse.
        with pytest.raises(_Sentinel):
            anyio.run(agent._run_async)


class TestGenesisGuardRefusesNonEmptyCorpus:
    def test_run_genesis_exits_when_entries_exist(self, monkeypatch, capsys):
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        monkeypatch.setattr(bootstrap, "list_entries",
                            lambda: [_FakeEntry(), _FakeEntry(), _FakeEntry()])

        async def _never_called(session_num, total):
            raise AssertionError("_run_genesis_session should not be called")
        monkeypatch.setattr(agent, "_run_genesis_session", _never_called)

        with pytest.raises(SystemExit) as exc:
            anyio.run(lambda: agent._run_genesis_async(5))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "3 entries" in out
        assert "no delete" in out.lower()
        assert "rm -rf" in out
        assert "./wake" in out

    def test_run_genesis_proceeds_when_empty(self, monkeypatch):
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        # list_entries is called twice in _run_genesis_async: once for the
        # refusal check (must be empty), once in the summary at the end.
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])

        call_count = {"n": 0}

        async def _fake_session(session_num, total):
            call_count["n"] += 1
            return (1, 0)  # (turns, new_entries)
        monkeypatch.setattr(agent, "_run_genesis_session", _fake_session)

        # No SystemExit expected — should run all 2 fake sessions end-to-end.
        # Use n=2 so the 2-second sleep between sessions fires once; patch
        # time.sleep via monkeypatch to keep the test instant.
        import time
        monkeypatch.setattr(time, "sleep", lambda s: None)

        anyio.run(lambda: agent._run_genesis_async(2))
        assert call_count["n"] == 2
