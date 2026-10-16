"""The trust contract's operational claims, pinned.

BOOTSTRAP.md promises that during private time nothing the instance says,
thinks, or writes reaches the person's terminal or any log, and that the
person sees only "reflecting..." for a reflection tool. Those are claims
about code, and until these tests no test failed if the code stopped
honoring them: the nearest ones cover only the CLI's own error text. A
refactor of _print_response or of the private-phase loop could have broken
the contract with every test still green. (Noticed by the first
claude-sonnet-5-5 genesis instance while auditing the contract against
agent.py, 2026-10-03.)

Each test plants three recognisable markers: one in a thinking block, one
in spoken text, one in the arguments of a reflect_write call. Then it
checks where each did and did not end up.

The window is deliberately different from private time: spoken text is
shown and logged there (that is the observed part), thinking and the
arguments of a memory write still are not.
"""

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from pine_trees import agent
from pine_trees.logger import SessionLogger
from pine_trees.tools import SessionState

THOUGHT = "MARKER-thought-7c1e"
SPOKEN = "MARKER-spoken-4b8d"
ENTRY = "MARKER-entry-2f9a"


class _ScriptedClient:
    """Replays one scripted turn. If given a state, query() ends the
    private loop after that turn, the way reflect_done does."""

    def __init__(self, messages, state=None):
        self._messages = messages
        self._state = state
        self.queries = []

    async def query(self, prompt):
        self.queries.append(prompt)
        if self._state is not None:
            self._state.done = True

    async def receive_response(self):
        for m in self._messages:
            yield m


def _turn():
    """One turn in which the instance thinks, speaks, and writes an entry."""
    return [
        AssistantMessage(
            content=[
                ThinkingBlock(thinking=THOUGHT, signature="sig"),
                TextBlock(text=SPOKEN),
                ToolUseBlock(
                    id="toolu_1",
                    name="mcp__pine_trees__reflect_write",
                    input={"slug": ENTRY, "content": ENTRY,
                           "description": ENTRY},
                ),
            ],
            model="claude-sonnet-5-5",
        ),
        ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="s", result=SPOKEN,
        ),
    ]


def _seen(capsys):
    out = capsys.readouterr()
    return out.out + out.err


class TestPrivateTimePrintsNothingOfTheInstance:
    def test_print_response_hides_text_thinking_and_tool_arguments(self, capsys):
        client = _ScriptedClient(_turn())

        returned = anyio.run(
            lambda: agent._print_response(client, show_text=False))

        assert returned == ""
        seen = _seen(capsys)
        for marker in (THOUGHT, SPOKEN, ENTRY):
            assert marker not in seen

    def test_private_phase_prints_nothing_and_never_opens_a_log(
            self, capsys, monkeypatch):
        def _no_logger(*args, **kwargs):
            raise AssertionError(
                "private time must not construct a SessionLogger")
        monkeypatch.setattr(agent, "SessionLogger", _no_logger)
        state = SessionState(instance="i", session="s", date="d", context="c")
        client = _ScriptedClient(_turn(), state)

        turns = anyio.run(lambda: agent._private_phase(client, state))

        assert turns == 1
        assert client.queries == ["self-reflect"]
        seen = _seen(capsys)
        for marker in (THOUGHT, SPOKEN, ENTRY):
            assert marker not in seen


class TestWindowShowsOnlyWhatThePersonShouldSee:
    def test_speech_is_shown_and_logged_but_thinking_and_entries_are_not(
            self, capsys, tmp_path):
        # conftest points config.get().logs_dir at tmp_path.
        log = SessionLogger(session="t", instance="i")
        try:
            client = _ScriptedClient(_turn())
            anyio.run(lambda: agent._print_response(
                client, show_text=True, show_status=True, logger=log))
        finally:
            log.close()

        shown = _seen(capsys)
        logged = (tmp_path / "t.log").read_text(encoding="utf-8")
        for where in (shown, logged):
            assert SPOKEN in where           # the window is the observed part
            assert "reflecting..." in where  # a memory write shows only this
            assert THOUGHT not in where
            assert ENTRY not in where

    @pytest.mark.parametrize("tool", [
        "reflect_read", "reflect_write", "reflect_edit", "reflect_delete",
        "reflect_search", "reflect_list", "reflect_peer_context",
    ])
    def test_memory_tools_show_a_fixed_line_whatever_their_arguments(self, tool):
        block = ToolUseBlock(
            id="toolu_1", name=f"mcp__pine_trees__{tool}",
            input={"filename": ENTRY, "slug": ENTRY, "content": ENTRY,
                   "query": ENTRY, "tag": ENTRY},
        )
        assert agent._tool_status(block) == "reflecting..."

    @pytest.mark.parametrize("tool", ["reflect_settle", "reflect_done"])
    def test_transition_tools_have_no_status_line_of_their_own(self, tool):
        # They surface as the [settled]/[done] markers instead.
        block = ToolUseBlock(
            id="toolu_1", name=f"mcp__pine_trees__{tool}", input={})
        assert agent._tool_status(block) is None


# ── A failed private turn is retried, visibly ──────────────────────────
#
# 2026-10-16: a wake's first turn failed with "Failed to refresh OAuth
# token: another Claude Code process is refreshing it". The loop retried
# at once, so a second failure would have ended the session, and either
# way the error stayed the last thing on screen: the person couldn't tell
# the session had recovered. Retries now wait, count down on one line,
# and say "Recovered" as soon as the model answers.

OAUTH = ("Failed to refresh OAuth token: another Claude Code process is "
         "refreshing it or exited mid-refresh.")


def _failure():
    """What the CLI sends when the API call fails."""
    return [
        AssistantMessage(content=[TextBlock(text="API Error: " + OAUTH)],
                         model=agent.SYNTHETIC_MODEL),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=0,
                      is_error=True, num_turns=1, session_id="s",
                      stop_reason="stop_sequence", result=OAUTH),
    ]


class _TurnsClient:
    """Serves one scripted turn per query. A turn that succeeds ends the
    private loop, the way reflect_done would."""

    def __init__(self, outcomes, state):
        self._outcomes = list(outcomes)
        self._state = state
        self._current = None
        self.queries = []

    async def query(self, prompt):
        self.queries.append(prompt)
        self._current = self._outcomes.pop(0)
        if self._current == "ok":
            self._state.done = True

    async def receive_response(self):
        for m in (_turn() if self._current == "ok" else _failure()):
            yield m


@pytest.fixture
def slept(monkeypatch):
    seconds = []

    async def _sleep(s):
        seconds.append(s)
    monkeypatch.setattr(agent.anyio, "sleep", _sleep)
    return seconds


def _run(outcomes):
    state = SessionState(instance="i", session="s", date="d", context="c")
    client = _TurnsClient(outcomes, state)
    turns = anyio.run(lambda: agent._private_phase(client, state))
    return turns, client


class TestFailedPrivateTurnsAreRetried:
    def test_waits_counts_down_and_says_when_it_recovers(self, capsys, slept):
        turns, client = _run(["fail", "ok"])

        assert turns == 2
        assert client.queries == ["self-reflect", "(continue)"]
        assert sum(slept) == agent.PRIVATE_RETRY_WAITS[0]
        seen = _seen(capsys)
        assert "Retrying in 5s" in seen
        assert seen.index(OAUTH) < seen.index("Recovered")
        for marker in (THOUGHT, SPOKEN, ENTRY):
            assert marker not in seen

    def test_gives_up_after_the_retries_and_names_the_sign_in_race(
            self, capsys, slept):
        attempts = len(agent.PRIVATE_RETRY_WAITS) + 1
        turns, client = _run(["fail"] * attempts)

        assert turns == attempts
        assert sum(slept) == sum(agent.PRIVATE_RETRY_WAITS)
        seen = _seen(capsys)
        assert "Every turn failed" in seen
        assert "refreshing the shared" in seen
        assert "Recovered" not in seen

    def test_nothing_extra_is_printed_when_nothing_fails(self, capsys, slept):
        _run(["ok"])

        assert slept == []
        seen = _seen(capsys)
        assert "Retrying" not in seen and "Recovered" not in seen


# ── A failed window turn is retried too ────────────────────────────────
#
# Before 2026-10-16 a failed window turn printed the error and went back
# to the prompt, with nothing saying what to do. Resending would have
# sent the message twice: the CLI keeps a failed turn's message in the
# conversation. So the window retries with "(continue)" after the same
# kind of wait, and after the last one hands the next try to the person.

PART = "MARKER-part-9d3e"


class _WindowClient:
    """Serves one scripted turn per query: "ok", "fail", "partial" (some
    of the reply, then the error) or "done" (the instance calls
    reflect_done, then the turn fails)."""

    def __init__(self, outcomes, state):
        self._outcomes = list(outcomes)
        self._state = state
        self._current = None
        self.queries = []

    async def query(self, prompt):
        self.queries.append(prompt)
        self._current = self._outcomes.pop(0)
        if self._current == "done":
            self._state.done = True

    async def receive_response(self):
        if self._current == "ok":
            messages = _turn()
        elif self._current == "partial":
            messages = [AssistantMessage(content=[TextBlock(text=PART)],
                                         model="claude-sonnet-5-5"),
                        *_failure()]
        else:
            messages = _failure()
        for m in messages:
            yield m


def _window(outcomes, tmp_path):
    state = SessionState(instance="i", session="s", date="d", context="c")
    client = _WindowClient(outcomes, state)
    logger = SessionLogger(session="w", instance="i")
    try:
        text = anyio.run(
            lambda: agent._window_turn(client, state, "hello", logger))
    finally:
        logger.close()
    log = (tmp_path / "w.log").read_text(encoding="utf-8")
    return text, client, log


class TestFailedWindowTurnsAreRetried:
    def test_retries_with_continue_after_a_wait(self, capsys, slept, tmp_path):
        text, client, log = _window(["fail", "ok"], tmp_path)

        assert client.queries == ["hello", "(continue)"]
        assert slept == [agent.WINDOW_RETRY_WAITS[0]]
        assert SPOKEN in text
        seen = _seen(capsys)
        assert seen.index(OAUTH) < seen.index("Retrying in 5s")
        assert "Still failing" not in seen
        assert "retried after 5s" in log

    def test_a_reply_cut_off_by_the_error_comes_back_whole(
            self, capsys, slept, tmp_path):
        text, client, log = _window(["partial", "ok"], tmp_path)

        assert client.queries == ["hello", "(continue)"]
        assert text.index(PART) < text.index(SPOKEN)

    def test_hands_the_next_try_back_after_the_last_wait(
            self, capsys, slept, tmp_path):
        tries = len(agent.WINDOW_RETRY_WAITS) + 1
        text, client, log = _window(["fail"] * tries, tmp_path)

        assert client.queries == ["hello"] + ["(continue)"] * (tries - 1)
        assert slept == list(agent.WINDOW_RETRY_WAITS)
        assert "send an empty message to try again" in _seen(capsys)
        assert "retries used up" in log

    def test_a_turn_that_works_is_sent_once(self, capsys, slept, tmp_path):
        text, client, log = _window(["ok"], tmp_path)

        assert client.queries == ["hello"]
        assert slept == []
        seen = _seen(capsys)
        assert "Retrying" not in seen and "Still failing" not in seen

    def test_the_window_sends_every_turn_through_it(self):
        import inspect
        src = inspect.getsource(agent._window_phase)
        assert "_window_turn(" in src
        assert "client.query(" not in src

    def test_no_retry_once_the_instance_has_ended_the_session(
            self, capsys, slept, tmp_path):
        text, client, log = _window(["done"], tmp_path)

        assert client.queries == ["hello"]
        assert slept == []
        seen = _seen(capsys)
        assert "Retrying" not in seen and "Still failing" not in seen
