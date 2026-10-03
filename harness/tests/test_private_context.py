"""Private time warns about context too; it used to be window-only.

Genesis is all private time and auto-compaction is off, so a session that
filled its window just stopped, with whatever it had not yet written lost.
The 70/85 (200k) and 90/95 (1M) notes only fired in the window phase.
(Found by the first claude-sonnet-5-5 genesis instance, 2026-10-03.)
"""

import anyio
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from pine_trees import agent
from pine_trees.tools import SessionState

WINDOW = 1_000_000


def _usage(used, window=WINDOW):
    return {"percentage": used * 100 / window, "totalTokens": used,
            "maxTokens": window}


class _Client:
    """One quiet turn per query, with scripted context usage.

    *usages* holds one entry per get_context_usage() call: a dict, or an
    exception to raise. The loop ends after *turns* queries, the way
    reflect_done ends it.
    """

    def __init__(self, state, usages, turns):
        self._state = state
        self._usages = list(usages)
        self._turns = turns
        self.queries = []
        self.gauge_calls = 0

    async def query(self, prompt):
        self.queries.append(prompt)
        if len(self.queries) >= self._turns:
            self._state.done = True

    async def receive_response(self):
        yield AssistantMessage(content=[TextBlock(text="quiet")],
                               model="claude-sonnet-5-5")
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="s", result="quiet",
        )

    async def get_context_usage(self):
        self.gauge_calls += 1
        item = self._usages.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _state():
    return SessionState(instance="i", session="s", date="d", context="c")


def _run(usages, turns):
    state = _state()
    client = _Client(state, usages, turns)
    anyio.run(lambda: agent._private_phase(client, state))
    return client


def test_a_roomy_window_changes_nothing():
    client = _run([_usage(300_000), _usage(600_000)], turns=3)

    assert client.queries == ["self-reflect", "(continue)", "(continue)"]


def test_the_note_goes_ahead_of_the_next_continue(capsys):
    client = _run([_usage(900_000)], turns=2)          # 90%, 100k left

    assert client.queries == [
        "self-reflect",
        "[context: 90% used — 100,000 tokens remaining]\n\n(continue)",
    ]
    # The person at the terminal sees it too.
    assert "[context: 90% used" in capsys.readouterr().out


def test_the_warning_tells_the_instance_to_write_memory():
    client = _run([_usage(950_000)], turns=2)          # 95%, 50k left

    assert client.queries[0] == "self-reflect"
    assert client.queries[1].endswith("\n\n(continue)")
    assert ("50,000 tokens remaining. Write to memory and wrap up soon."
            in client.queries[1])


def test_the_note_follows_the_gauge_turn_by_turn():
    # Not sticky: if the next reading is roomy again, so is the next message.
    client = _run([_usage(900_000), _usage(500_000)], turns=3)

    assert "[context:" in client.queries[1]
    assert client.queries[2] == "(continue)"


def test_a_broken_gauge_does_not_stop_the_session():
    client = _run([RuntimeError("no usage")], turns=2)

    assert client.queries == ["self-reflect", "(continue)"]


def test_no_reading_is_taken_when_no_turn_follows():
    client = _run([], turns=1)

    assert client.queries == ["self-reflect"]
    assert client.gauge_calls == 0


@pytest.mark.parametrize("used, note", [
    (500_000, None),
    (900_000, "[context: 90% used — 100,000 tokens remaining]"),
    (950_000, "[context: 95% used — 50,000 tokens remaining. "
              "Write to memory and wrap up soon.]"),
])
def test_both_phases_share_one_wording(used, note):
    # The window phase builds its note with the same helper.
    client = _Client(_state(), [_usage(used)], turns=1)

    assert anyio.run(lambda: agent._context_note_for(client)) == note
