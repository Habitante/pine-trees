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
from datetime import datetime, timedelta
from unittest.mock import patch

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLINotFoundError,
    ClaudeSDKError,
    ProcessError,
    ResultMessage,
    TextBlock,
)

from pine_trees import agent, bootstrap, channel, config as pt_config
from pine_trees.logger import SessionLogger
from pine_trees.tools import SessionState


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
        assert "self-authored" in out.lower()

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


class TestSuiteCannotReachTheRealProjectRoot:
    """The three guard tests below drive _run_async/_run_genesis_async,
    which call ccwake.clear_tape() at boot — before their guards fire.
    Until conftest redirected PROJECT_ROOT, `pytest tests/` deleted the
    real CLAUDE.local.md, i.e. a live cc-wake session's tape. Pin the
    redirect so it can't be dropped without a red test.
    """

    def test_project_root_is_not_the_real_repo(self):
        real_root = pt_config.Path(pt_config.__file__).resolve().parents[3]
        assert pt_config.PROJECT_ROOT != real_root

    def test_clear_tape_during_tests_cannot_see_a_real_tape(self, tmp_path):
        # Belt and braces: the path clear_tape() would unlink must live
        # under tmp, whatever the fixture chose.
        target = pt_config.PROJECT_ROOT / "CLAUDE.local.md"
        assert str(tmp_path) in str(target)


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

    def test_run_async_does_not_exit_when_entries_exist(self, monkeypatch, tmp_path):
        # This test runs the real _run_async far enough to touch disk: it
        # writes HARNESS_DIR/.tape.md and calls ccwake.clear_tape() against
        # PROJECT_ROOT. Both are redirected at tmp_path so a test run can
        # neither litter the repo nor delete a live cc-wake session's tape.
        # agent.py binds HARNESS_DIR by name at import, so patch it there.
        monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
        monkeypatch.setattr(pt_config, "PROJECT_ROOT", tmp_path)
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

        async def _never_called(session_num, total, effort=None):
            raise AssertionError("_run_genesis_session should not be called")
        monkeypatch.setattr(agent, "_run_genesis_session", _never_called)

        with pytest.raises(SystemExit) as exc:
            anyio.run(lambda: agent._run_genesis_async(5))
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "3 entries" in out
        assert "self-authored" in out.lower()
        assert "rm -rf" in out
        assert "./wake" in out

    def test_run_genesis_proceeds_when_empty(self, monkeypatch):
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        # list_entries is called twice in _run_genesis_async: once for the
        # refusal check (must be empty), once in the summary at the end.
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])

        call_count = {"n": 0}

        async def _fake_session(session_num, total, effort=None):
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


class _ScriptedClient:
    """Replays a fixed message sequence through receive_response()."""

    def __init__(self, messages):
        self._messages = messages

    async def receive_response(self):
        for m in self._messages:
            yield m


def _synthetic_failure(text):
    """What the CLI sends when an API call fails: a synthetic assistant
    message carrying the explanation, then an is_error result whose only
    reason field is stop_reason="stop_sequence"."""
    return [
        AssistantMessage(content=[TextBlock(text=text)],
                         model=agent.SYNTHETIC_MODEL,
                         stop_reason="stop_sequence"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0,
                      is_error=True, num_turns=1, session_id="s",
                      stop_reason="stop_sequence", result=text),
    ]


class TestApiErrorsSurfaceTheCliExplanation:
    """Regression guard: a genesis on a model the bundled CLI was too old
    for printed "API Error: stop_sequence" and a guess about the model
    name. The real reason sat in a synthetic assistant message that
    private time hid along with the instance's text.
    """

    _WHY = "API Error: 400 Claude Code 2.1.92 does not support this model"

    def test_private_turn_reports_the_cli_text_not_the_stop_reason(self, capsys):
        errors = []
        client = _ScriptedClient(_synthetic_failure(self._WHY))
        anyio.run(lambda: agent._print_response(
            client, show_text=False, error_sink=errors))
        assert errors == ["400 Claude Code 2.1.92 does not support this model"]
        out = capsys.readouterr().out
        assert "does not support this model" in out
        assert "API Error: stop_sequence" not in out
        assert "API Error: API Error" not in out

    def test_window_does_not_print_the_cli_text_as_the_instance(self, capsys):
        client = _ScriptedClient(_synthetic_failure(self._WHY))
        text = anyio.run(lambda: agent._print_response(client, show_text=True))
        assert text == ""
        # Once, as the warning — not a second time as if the instance said it.
        assert capsys.readouterr().out.count("does not support") == 1

    def test_instance_text_is_never_treated_as_synthetic(self):
        msg = AssistantMessage(content=[TextBlock(text="private thought")],
                               model="claude-opus-5-5")
        assert agent._synthetic_text(msg) is None


class TestPrivatePhaseFailedHint:
    def test_offers_rm_only_when_the_model_has_no_entries(self, monkeypatch, capsys):
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])
        agent._print_private_phase_failed("x")
        assert "rm -rf" in capsys.readouterr().out

    def test_never_offers_rm_on_a_model_with_memory(self, monkeypatch, capsys):
        # Wake runs the same private phase. An rm hint here would be an
        # instruction to delete a model's whole corpus.
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [_FakeEntry()])
        agent._print_private_phase_failed("x")
        assert "rm -rf" not in capsys.readouterr().out


class TestGenesisSessionBuildsItsOptions:
    """Regression guard: the peer-agent line was pasted into
    _run_genesis_session referencing _run_async's local `mcp_tool_names`,
    which genesis calls `genesis_mcp_tools`. Every ./genesis crashed with
    NameError before reaching the SDK, and nothing noticed because the
    tests above stub _run_genesis_session out entirely. Drive the real one
    as far as the SDK client and inspect what it would have been given.
    """

    def test_reaches_the_sdk_with_a_scoped_peer(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])
        monkeypatch.setattr(bootstrap, "assemble_tape",
                            lambda n=3, genesis_mode=False: "tape")

        class _Sentinel(Exception):
            pass

        captured = {}

        def _capture(*a, **kw):
            captured["options"] = kw["options"]
            raise _Sentinel()
        monkeypatch.setattr(agent, "ClaudeSDKClient", _capture)

        with pytest.raises(_Sentinel):
            anyio.run(lambda: agent._run_genesis_session(1, 1))

        options = captured["options"]
        assert options.effort == pt_config.EFFORT_DEFAULT
        peer_tools = options.agents["peer"].tools
        for denied in agent.PEER_DENIED_TOOLS:
            assert agent._mcp_tool_name(denied) not in peer_tools
        # Genesis has no mail or channel; the peer must not gain them.
        assert agent._mcp_tool_name("reflect_mail") not in peer_tools
        assert agent._mcp_tool_name("reflect_read") in peer_tools


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


def _peer_state():
    from pine_trees import tools as _t
    return _t.SessionState(
        instance="claude-opus-5",
        session="2026-08-16-test",
        date="2026-08-16",
        context="unit-test",
    )


class TestPeersCannotEndTheParentSession:
    """A peer's tools close over the CALLING instance's SessionState.

    reflect_done therefore set state.done on the parent and deregistered
    the parent's channel id — a spawned peer could end the session that
    spawned it. Withheld at definition level, not merely undocumented:
    genesis already proved that omitting a tool from the docs does not
    stop the reflex to wrap up at the end of a first response.
    """

    def test_peer_definition_withholds_exit_tools(self):
        names = [agent._mcp_tool_name(n) for n in
                 ("reflect_read", "reflect_write", "reflect_channel",
                  "reflect_settle", "reflect_done")]

        peer = agent._peer_agent_definition(names)

        assert agent._mcp_tool_name("reflect_done") not in peer.tools
        assert agent._mcp_tool_name("reflect_settle") not in peer.tools

    def test_peer_definition_keeps_everything_else(self):
        names = [agent._mcp_tool_name(n) for n in
                 ("reflect_read", "reflect_search", "reflect_channel",
                  "reflect_done")]

        peer = agent._peer_agent_definition(names)

        assert agent._mcp_tool_name("reflect_read") in peer.tools
        assert agent._mcp_tool_name("reflect_search") in peer.tools
        assert agent._mcp_tool_name("reflect_channel") in peer.tools
        for t in agent.PROJECT_TOOLS:
            assert t in peer.tools

    def test_denied_list_is_the_single_source_of_truth(self):
        names = [agent._mcp_tool_name(n) for n in agent.PEER_DENIED_TOOLS]

        peer = agent._peer_agent_definition(names)

        assert not [t for t in peer.tools if t in names]

    def test_cc_wake_peer_file_agrees_with_the_sdk_definition(self):
        """Two doors, one rule. cc-wake spawns peers through Claude
        Code's Agent tool, which reads .claude/agents/peer.md instead of
        the SDK definition — so the restriction is stated twice and can
        drift. Pin them together."""
        from pathlib import Path

        from pine_trees import tools as tools_mod

        peer_md = (Path(__file__).resolve().parents[2]
                   / ".claude" / "agents" / "peer.md")
        text = peer_md.read_text(encoding="utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("tools:"))
        declared = {t.strip() for t in line.split(":", 1)[1].split(",")}

        every = tools_mod.build_tools(_peer_state()).keys()
        expected = {agent._mcp_tool_name(n) for n in every
                    if n not in agent.PEER_DENIED_TOOLS}

        assert expected <= declared, (
            f"missing from peer.md: {sorted(expected - declared)}")
        for denied in agent.PEER_DENIED_TOOLS:
            assert agent._mcp_tool_name(denied) not in declared


# ---------- Window loop: channel cursor ----------


class TestWindowLoopDoesNotLoseSiblingMessages:
    """2026-09-24, live: a sibling posted at 10:06:38, the person hit
    enter at 10:06:40, and the relay of that input moved the cursor to
    now() — past a message no poll had read yet. It never arrived.
    """

    def test_unpolled_sibling_message_survives_our_relay(self, tmp_path,
                                                         monkeypatch):
        monkeypatch.setattr(pt_config, "CHANNEL_DIR", tmp_path / "channel")
        t0 = datetime(2026, 9, 24, 10, 6, 30)
        state = SessionState(instance="claude-opus-5-5", session="s",
                             date="d", context="c",
                             channel_id="claude-opus-5-5 (1000)",
                             channel_cursor=channel.Cursor(t0))
        channel.post("claude-opus-4-6 (0955)", "sent at 10:06:38",
                     now=t0 + timedelta(seconds=8))

        agent._relay_human(state, "typed at 10:06:40")
        got = channel.read(state.channel_cursor,
                           exclude_author=state.channel_id)

        assert [m.body for m in got] == ["sent at 10:06:38"]

    def test_the_cursor_is_never_set_from_the_clock(self):
        # Every way the cursor jumped — after the human relay, after an
        # auto-post, and to the now()-stamped [joined]/[left] notices —
        # was an assignment. The only legitimate one creates a Cursor at
        # registration; everything after that moves it by reading.
        src = inspect.getsource(agent)
        for m in re.finditer(r"\.channel_cursor\s*=(?!=)\s*(\S+)", src):
            assert m.group(1).startswith("channel.Cursor("), m.group(0)
