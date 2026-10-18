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


class _Prompt:
    """A PromptSession stand-in: each prompt raises or returns the next of `script`."""

    def __init__(self, script):
        self.script = list(script)
        self.asked = 0

    async def prompt_async(self, message, prompt_continuation=None):
        self.asked += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        return step


class _Clock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def _ask(script, times=()):
    session = _Prompt(script)
    out = anyio.run(lambda: agent._prompt(session, ">", ".", clock=_Clock(times)))
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


def test_the_window_leaves_open_on_none():
    # A reading of the source, as test_agent_guards does for the channel: the
    # real window needs a terminal and a CLI, and the wake's tests fake it.
    import inspect
    src = inspect.getsource(agent._window_phase)
    assert "await _prompt(" in src
    assert "if user_input is None:" in src
    assert "raise LeftWithoutEnding()" in src
    assert 'user_input = "/end"' not in src
