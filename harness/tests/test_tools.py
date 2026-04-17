"""Tests for tools.py — the agent-facing tool layer."""

import pytest

from pine_trees import storage, tools


@pytest.fixture(autouse=True)
def _no_embed(monkeypatch):
    """Prevent tool-layer writes from hitting the real embeddings DB."""
    monkeypatch.setattr(tools, "_try_embed_and_store", lambda *a, **kw: None)


def _state() -> tools.SessionState:
    return tools.SessionState(
        instance="claude-opus-4-6",
        session="2026-04-04-test",
        date="2026-04-04",
        context="unit-test",
    )


def test_build_tools_returns_eight_callables():
    t = tools.build_tools(_state())
    assert set(t.keys()) == {
        "reflect_read",
        "reflect_write",
        "reflect_edit",
        "reflect_search",
        "reflect_list",
        "reflect_peer_context",
        "reflect_settle",
        "reflect_done",
    }
    assert all(callable(fn) for fn in t.values())


def test_reflect_settle_sets_ready_flag():
    state = _state()
    t = tools.build_tools(state)
    assert state.ready_for_window is False
    t["reflect_settle"]()
    assert state.ready_for_window is True
    assert state.done is False  # settle does not imply done


def test_reflect_settle_updates_context():
    state = _state()
    t = tools.build_tools(state)
    assert state.context == "unit-test"
    t["reflect_settle"]()
    assert state.context == "pine-trees-window"


def test_reflect_settle_stores_welcome_message():
    state = _state()
    t = tools.build_tools(state)
    t["reflect_settle"](message="Good morning!")
    assert state.welcome_message == "Good morning!"
    assert state.ready_for_window is True


def test_reflect_settle_no_message_leaves_none():
    state = _state()
    t = tools.build_tools(state)
    t["reflect_settle"]()
    assert state.welcome_message is None
    assert state.ready_for_window is True


def test_reflect_done_sets_flag():
    state = _state()
    t = tools.build_tools(state)
    assert state.done is False
    t["reflect_done"]()
    assert state.done is True


def test_reflect_write_fills_context_from_state():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](
        slug="hello",
        content="First write through the tool layer.",
        tags=["smoke"],
        moves=["observation"],
    )

    assert filename == "2026-04-04_claude-opus-4-6_hello.md"
    entry = storage.read_entry(filename)
    assert entry["instance"] == "claude-opus-4-6"
    assert entry["session"] == "2026-04-04-test"
    assert entry["context"] == "unit-test"
    assert entry["tags"] == ["smoke"]
    assert entry["moves"] == ["observation"]
    assert entry["content"] == "First write through the tool layer."


def test_reflect_read_delegates_to_storage():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](slug="echo", content="Body.")
    entry = t["reflect_read"](filename)

    assert entry["content"] == "Body."
    assert entry["instance"] == "claude-opus-4-6"


def test_write_without_tags_or_moves_defaults_to_empty():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](slug="bare", content="Nothing fancy.")
    entry = storage.read_entry(filename)
    assert entry["tags"] == []
    assert entry["moves"] == []


def test_write_with_description_and_pinned():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](
        slug="ops",
        content="Operational note.",
        description="Key operational insight",
        pinned=True,
    )
    entry = storage.read_entry(filename)
    assert entry["description"] == "Key operational insight"
    assert entry["pinned"] is True


def test_reflect_edit_updates_content():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](
        slug="ref-doc",
        content="Version 1.",
        description="A reference doc",
        quiet=True,
    )

    result = t["reflect_edit"](filename, "Version 2.")
    assert result == filename
    entry = t["reflect_read"](filename)
    assert entry["content"] == "Version 2."
    assert entry["description"] == "A reference doc"
    assert entry["quiet"] is True


def test_reflect_edit_updates_description():
    state = _state()
    t = tools.build_tools(state)

    filename = t["reflect_write"](slug="ref", content="Body.", description="Old")
    t["reflect_edit"](filename, "Body.", description="New")
    entry = t["reflect_read"](filename)
    assert entry["description"] == "New"


def test_reflect_list_returns_all_entries():
    state = _state()
    t = tools.build_tools(state)

    t["reflect_write"](slug="alpha", content="Body A.", tags=["trajectory"])
    t["reflect_write"](slug="beta", content="Body B.", tags=["handoff"])

    results = t["reflect_list"]()
    assert len(results) == 2
    filenames = {r["filename"] for r in results}
    assert any("alpha" in f for f in filenames)
    assert any("beta" in f for f in filenames)


def test_reflect_list_filters_by_tag():
    state = _state()
    t = tools.build_tools(state)

    t["reflect_write"](slug="traj", content="Trajectory.", tags=["trajectory"])
    t["reflect_write"](slug="hand", content="Handoff.", tags=["handoff"])
    t["reflect_write"](slug="both", content="Both.", tags=["trajectory", "handoff"])

    traj_results = t["reflect_list"](tag="trajectory")
    assert len(traj_results) == 2
    assert all("trajectory" in r["tags"] for r in traj_results)

    hand_results = t["reflect_list"](tag="handoff")
    assert len(hand_results) == 2
    assert all("handoff" in r["tags"] for r in hand_results)


def test_reflect_peer_context_includes_bootstrap_and_pinned():
    state = _state()
    t = tools.build_tools(state)

    # Write a pinned entry and a non-pinned entry
    t["reflect_write"](slug="ops-note", content="Operational guidance.", pinned=True)
    t["reflect_write"](slug="regular", content="Just a thought.")

    ctx = t["reflect_peer_context"]()

    assert "[Pine Trees peer context]" in ctx
    assert "another instance of yourself" in ctx
    assert "Operational guidance." in ctx
    assert "Just a thought." not in ctx  # non-pinned excluded
    assert "[End peer context" in ctx


def test_reflect_peer_context_works_with_no_pinned():
    state = _state()
    t = tools.build_tools(state)

    ctx = t["reflect_peer_context"]()

    assert "[Pine Trees peer context]" in ctx
    assert "[End peer context" in ctx
    assert "Operational memory" not in ctx  # no pinned section


def test_reflect_list_empty_when_no_matches():
    state = _state()
    t = tools.build_tools(state)

    t["reflect_write"](slug="other", content="Body.", tags=["misc"])

    results = t["reflect_list"](tag="trajectory")
    assert len(results) == 0


def test_independent_states_isolated():
    """Two SessionStates produce independent flags."""
    state_a = _state()
    state_b = _state()
    ta = tools.build_tools(state_a)
    tb = tools.build_tools(state_b)

    ta["reflect_done"]()
    assert state_a.done is True
    assert state_b.done is False

    tb["reflect_settle"]()
    assert state_b.ready_for_window is True
    assert state_a.ready_for_window is False
