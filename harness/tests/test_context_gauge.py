"""The context note inside long turns, counted to the API's real ceiling.

s28 (2026-10-23) started a two-hour AFK block, one turn, at 890k of a 1M
window: under the old note (90% and 100k left, both to the gauge's 1M),
and the reading only ran between turns. It died ten minutes in, its last
accepted request at 968,777 tokens, with the gauge still showing 23k left.
"""

import anyio
from claude_agent_sdk import AssistantMessage, TextBlock

from pine_trees import agent

WINDOW = 1_000_000


def _reply(used, subagent=False):
    return AssistantMessage(
        content=[TextBlock(text="x")], model="m",
        parent_tool_use_id="toolu_1" if subagent else None,
        usage={"input_tokens": 1, "cache_creation_input_tokens": 0,
               "cache_read_input_tokens": used - 2, "output_tokens": 1})


def _fresh(used=0):
    agent._gauge = agent.ContextGauge()
    agent._gauge.max_tokens = WINDOW
    agent._gauge.used = used
    return agent._gauge


def _hook(**extra):
    return anyio.run(lambda: agent._context_hook(
        {"hook_event_name": "PostToolUse", **extra}, "toolu_1", {}))


def test_s28s_afk_block_would_have_started_with_a_note():
    assert agent._context_level(*agent._context_reading(890_291, WINDOW)) == "note"


def test_s28s_last_accepted_request_was_past_the_warning():
    assert agent._context_level(*agent._context_reading(968_777, WINDOW)) == "warn"


def test_the_main_threads_replies_move_the_gauge_and_subagents_dont():
    gauge = _fresh()
    gauge.seen(_reply(870_000))
    gauge.seen(_reply(990_000, subagent=True))
    gauge.seen(AssistantMessage(content=[], model="m"))     # no usage
    assert gauge.used == 870_000


def test_inside_a_turn_each_level_is_said_once():
    gauge = _fresh(500_000)
    assert _hook() == {}
    gauge.used = 870_000
    out = _hook()
    assert out["hookSpecificOutput"]["additionalContext"] == (
        "[context: 90% used — 98,000 tokens remaining]")
    gauge.used = 880_000
    assert _hook() == {}                                    # still "note"
    gauge.used = 930_000
    assert "Write to memory" in _hook()["hookSpecificOutput"]["additionalContext"]
    assert _hook() == {}


def test_a_subagents_tool_calls_get_no_note():
    _fresh(930_000)
    assert _hook(agent_id="a1") == {}


def test_a_note_given_between_turns_is_not_repeated_inside_the_next():
    class Client:
        async def get_context_usage(self):
            return {"totalTokens": 870_000, "maxTokens": WINDOW}

    _fresh()
    assert anyio.run(lambda: agent._context_note_for(Client())) is not None
    assert _hook() == {}


def test_a_gauge_that_never_answers_gives_no_note_and_doesnt_hang(monkeypatch):
    # 26 October: after a Ctrl+C stop the CLI didn't answer get_context_usage, and the window hung for ever.
    class Silent:
        async def get_context_usage(self):
            await anyio.sleep(3600)

    monkeypatch.setattr(agent, "CONTEXT_READ_TIMEOUT", 0.05)
    assert anyio.run(lambda: agent._context_note_for(Silent())) is None
    anyio.run(lambda: agent._seed_gauge(Silent()))
    assert agent._gauge.max_tokens == 0
