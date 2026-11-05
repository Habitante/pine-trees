"""Ctrl+C at the window's prompt (s23).

It used to be /end. On 2026-10-14 a Ctrl+C meant as Ctrl+V closed s19's
window mid-conversation: the session was marked done, the CLI's transcript
deleted, and ./wake --continue had nothing to resume. Now once only says
how to end; twice within INTERRUPT_AGAIN seconds leaves the session open
(agent._prompt returns None; _window_phase raises LeftWithoutEnding; the
wake keeps it resumable, see test_transcripts).
"""

import anyio

from pine_trees import agent


class _Buffer:
    text = ""


class _Prompt:
    """A PromptSession stand-in: each prompt raises or returns the next of `script`. A step (exception, text) raises
    with `text` typed in the prompt; `defaults` records what each prompt started with."""

    def __init__(self, script):
        self.script = list(script)
        self.asked = 0
        self.defaults = []
        self.default_buffer = _Buffer()

    async def prompt_async(self, message, prompt_continuation=None, default=""):
        self.asked += 1
        self.defaults.append(default)
        self.default_buffer.text = default
        step = self.script.pop(0)
        if isinstance(step, tuple):
            step, self.default_buffer.text = step
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        return step


class _Clock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def _ask(script, times=(), draft=None):
    session = _Prompt(script)
    out = anyio.run(lambda: agent._prompt(session, ">", ".", clock=_Clock(times), draft=draft))
    return out, session


def test_text_is_returned():
    assert _ask(["hello"])[0] == "hello"


def test_one_ctrl_c_only_says_how_to_end(capsys):
    out, session = _ask([KeyboardInterrupt, "still here"], times=[10.0])
    assert out == "still here"
    assert session.asked == 2
    printed = capsys.readouterr().out
    assert "/end" in printed and "./wake --continue" in printed


def test_ctrl_c_never_ends_the_session():
    out, _ = _ask([KeyboardInterrupt, "x"], times=[10.0])
    assert out != "/end"


def test_twice_quickly_leaves_it_open():
    out, session = _ask([KeyboardInterrupt, KeyboardInterrupt], times=[10.0, 10.0 + agent.INTERRUPT_AGAIN])
    assert out is None
    assert session.asked == 2


def test_twice_slowly_is_two_slips():
    out, session = _ask([KeyboardInterrupt, KeyboardInterrupt, "back"],
                        times=[10.0, 10.0 + agent.INTERRUPT_AGAIN + 0.5])
    assert out == "back"
    assert session.asked == 3


def test_ctrl_d_is_the_same():
    out, session = _ask([EOFError, "back"], times=[1.0])
    assert out == "back" and session.asked == 2           # once, only the hint
    out, session = _ask([EOFError, EOFError], times=[1.0, 1.5])
    assert out is None and session.asked == 2             # twice, left open


# 5 November: a Ctrl+C at the prompt threw away an hour of Daniel's typing.

def test_ctrl_c_keeps_what_you_typed():
    out, session = _ask([(KeyboardInterrupt, "an hour of typing"), "sent"], times=[10.0])
    assert session.defaults == ["", "an hour of typing"]     # back in the prompt after the hint
    assert out == "sent"


def test_leaving_open_keeps_it_in_the_draft(tmp_path):
    draft = tmp_path / agent.DRAFT_FILE
    out, _ = _ask([(KeyboardInterrupt, "half a thought"), (KeyboardInterrupt, "half a thought")],
                  times=[10.0, 10.5], draft=draft)
    assert out is None
    assert draft.read_text(encoding="utf-8") == "half a thought"


def test_the_next_prompt_starts_with_the_draft_and_sending_clears_it(tmp_path, capsys):
    draft = tmp_path / agent.DRAFT_FILE
    draft.write_text("from before the crash", encoding="utf-8")
    out, session = _ask(["from before the crash, finished"], draft=draft)
    assert session.defaults == ["from before the crash"]
    assert agent.DRAFT_BACK in capsys.readouterr().out
    assert out == "from before the crash, finished"
    assert not draft.exists()


def test_every_change_is_saved_as_you_type(tmp_path):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    draft = tmp_path / agent.DRAFT_FILE
    with create_pipe_input() as inp:
        inp.send_text("typed so far\x1b\r")                 # typed, then Alt+Enter (the prompt alone: no clearing)
        session = PromptSession(multiline=True, input=inp, output=DummyOutput())
        agent._keep_drafts(session, draft)
        assert session.prompt() == "typed so far"
    assert draft.read_text(encoding="utf-8") == "typed so far"


def test_the_window_leaves_open_on_none():
    # A reading of the source, as test_agent_guards does for the channel: the
    # real window needs a terminal and a CLI, and the wake's tests fake it.
    import inspect
    src = inspect.getsource(agent._window_phase)
    assert "await _prompt(" in src
    assert "if user_input is None:" in src
    assert "raise LeftWithoutEnding()" in src
    assert 'user_input = "/end"' not in src
