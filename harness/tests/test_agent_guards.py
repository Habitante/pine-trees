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

import inspect
import re
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
from pine_trees.logger import SessionLogger


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
        from pine_trees import config
        cfg = config.get()
        agent._print_genesis_on_existing(42)
        out = capsys.readouterr().out
        assert "42 entries" in out
        assert "./wake" in out
        assert "rm -rf" in out
        # With per-model isolation, the rm target is the whole model dir —
        # memory_dir and key_file_path both live under it.
        assert str(cfg.model_dir) in out
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


# ---------- Regression guards: logger method names in agent.py ----------


class TestLoggerMethodCallsAreValid:
    """Regression guard: agent.py had `logger.log_assistant(welcome_message)`
    which crashed at window open because SessionLogger defines `log_agent`,
    not `log_assistant`. This test statically scans agent.py for every
    `logger.log_*` call and verifies each one exists on SessionLogger. Any
    typo in a logger method name anywhere in agent.py fails the test.
    """

    _CALL_RE = re.compile(r"logger\.(log_[A-Za-z_]+)")

    def _extract_logger_calls(self, func) -> set[str]:
        source = inspect.getsource(func)
        return set(self._CALL_RE.findall(source))

    def test_window_phase_logger_calls_exist_on_session_logger(self):
        calls = self._extract_logger_calls(agent._window_phase)
        assert calls, "_window_phase should call the logger at least once"
        for name in calls:
            assert hasattr(SessionLogger, name), (
                f"agent._window_phase calls logger.{name}() but SessionLogger "
                f"has no such method. Defined methods: "
                f"{sorted(m for m in vars(SessionLogger) if m.startswith('log_'))}"
            )

    def test_print_response_logger_calls_exist_on_session_logger(self):
        # _print_response is the other place agent.py touches the logger —
        # pin it here too so any typo there also fails loudly.
        calls = self._extract_logger_calls(agent._print_response)
        for name in calls:
            assert hasattr(SessionLogger, name), (
                f"agent._print_response calls logger.{name}() but "
                f"SessionLogger has no such method."
            )


class TestWelcomeMessageIsLoggedWithoutCrashing:
    """Direct smoke test for the exact call pattern that crashed in f2d05d6.

    Exercises SessionLogger with the same method agent._window_phase now uses
    to log the welcome_message. If the method is ever renamed on SessionLogger
    without updating agent.py (or vice versa), this test surfaces it as an
    AttributeError instead of a runtime crash on the next `./wake`.
    """

    def test_log_agent_accepts_welcome_message_text(self, tmp_path):
        # conftest's _test_config fixture points config.get().logs_dir at
        # tmp_path, so SessionLogger writes here without further setup.
        log = SessionLogger(session="test-session", instance="test-instance")
        try:
            # This is the exact call _window_phase makes when welcome_message
            # is set. It must not raise.
            log.log_agent("Tuesday morning. Read the tape.")
        finally:
            log.close()

        contents = (tmp_path / "test-session.log").read_text(encoding="utf-8")
        assert "Tuesday morning. Read the tape." in contents
