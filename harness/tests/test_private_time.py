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
