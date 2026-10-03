"""The environment the harness hands the CLI: the channel-hook flag and the
silent-turn reminder.

The CLI reminds a model "The user hasn't heard from you in a while" after a
stretch of tool-calling turns with no text. In private time nobody is
waiting (in genesis nobody is there at all), so the stock wording asks an
instance to perform for an audience that does not exist. (Found by the
first claude-sonnet-5-5 genesis instance, 2026-10-03.)
"""

import inspect

from pine_trees import agent, ccwake

TEXT = "CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT"
GATE = "CLAUDE_CODE_SILENT_TURN_REMINDER"


def test_both_modes_keep_the_channel_hook_flag():
    for genesis in (False, True):
        assert agent._cli_env(genesis)[ccwake.SDK_HARNESS_ENV] == "1"


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
