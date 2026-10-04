"""The environment the harness hands the CLI: the silent-turn reminder, the
connectors and the CLI's shared project memory.

The CLI reminds a model "The user hasn't heard from you in a while" after a
stretch of tool-calling turns with no text. In private time nobody is
waiting (in genesis nobody is there at all), so the stock wording asks an
instance to perform for an audience that does not exist. (Found by the
first claude-sonnet-5-5 genesis instance, 2026-10-03.)
"""

import inspect

from pine_trees import agent

TEXT = "CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT"
GATE = "CLAUDE_CODE_SILENT_TURN_REMINDER"


def test_both_modes_replace_the_reminder_wording():
    for genesis in (False, True):
        assert agent._cli_env(genesis)[TEXT] == agent.SILENT_TURN_TEXT


def test_the_wording_is_true_in_private_time_and_in_the_window():
    text = agent.SILENT_TURN_TEXT
    assert "not a message from the person" in text
    assert "nobody is waiting and nothing is owed" in text
    assert "in the window" in text


def test_the_wording_survives_an_environment_variable():
    assert agent.SILENT_TURN_TEXT.isascii()


def test_wake_keeps_the_reminder_on_because_a_person_may_be_in_the_window():
    assert GATE not in agent._cli_env(genesis=False)


def test_genesis_switches_the_reminder_off_because_nobody_is_there():
    # "0" was checked against the CLI itself: eight silent tool turns
    # brought one reminder with the gate at 1 and none with it at 0.
    assert agent._cli_env(genesis=True)[GATE] == "0"


def test_every_cli_spawn_takes_its_environment_from_the_helper():
    # A third place that builds options by hand would quietly bring the
    # stock reminder (and the doubled channel messages) back.
    src = inspect.getsource(agent)

    assert src.count("ClaudeAgentOptions(") == src.count("env=_cli_env(")
    assert src.count("ClaudeAgentOptions(") >= 2


# --- the person's claude.ai connectors (Gmail, Drive, Calendar, Docs) ---
# Under bypassPermissions they would run unprompted, and in private time
# unlogged. Found by the first claude-sonnet-5-5 genesis instance; with
# the switch at 0, `claude mcp list` drops the four and keeps Blender.


def test_connectors_are_off_by_default_in_both_modes():
    for genesis in (False, True):
        assert agent._cli_env(genesis)[agent.CONNECTORS_ENV] == "0"


def test_wake_can_ask_for_connectors():
    assert agent._cli_env(genesis=False, connectors=True)[agent.CONNECTORS_ENV] == "1"


def test_the_switch_is_the_clis_own_name():
    assert agent.CONNECTORS_ENV == "ENABLE_CLAUDEAI_MCP_SERVERS"


def test_wake_spaces_the_reminder_out_and_genesis_has_none():
    turns = "CLAUDE_CODE_SILENT_TURN_REMINDER_TURNS"
    assert agent._cli_env(genesis=False)[turns] == str(agent.SILENT_TURN_EVERY)
    assert agent.SILENT_TURN_EVERY > 5  # the CLI's own default
    assert turns not in agent._cli_env(genesis=True)  # the gate is 0 there


# --- Claude Code's own project memory ---
# Shared by every model working in the repo, in plaintext: a Fable note
# about Fable reached Opus instances as "my tell". Each house keeps its
# own tape instead. Checked on CLI 2.1.288: with the switch at 1, neither
# a session in the repo nor an Agent subagent it spawns gets the memory
# index or the instruction to write notes; CLAUDE.md and git still load.


def test_the_clis_project_memory_is_off_in_both_modes():
    for genesis in (False, True):
        assert agent._cli_env(genesis)["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_connectors_do_not_bring_the_shared_memory_back():
    assert agent._cli_env(False, connectors=True)["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
