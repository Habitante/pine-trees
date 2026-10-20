"""A plain ./wake asks about a window left open (Daniel, 2026-10-20).

"When I close the window or Ctrl+C out of it, instead of a formal exit from both of us, could it warn
me if next day I type just ./wake because I forgot for any reason?" Enter or y goes back to the open
session, as --continue would; n starts a new one and leaves the open one resumable, asked about again
on the next plain ./wake; Ctrl+C at the question starts nothing; with no console to ask on, a new
session starts as before. Only this house's open windows count.
"""

import uuid
from datetime import datetime, timedelta

import anyio
import pytest

from pine_trees import agent, bootstrap, config, sessions

MODEL = "claude-opus-4-6"     # the test config's house (conftest.py)


class _ReachedClient(Exception):
    """The wake got as far as starting the CLI: what it decided is in `told`."""


@pytest.fixture
def wake(monkeypatch, tmp_path):
    """Run the real _run_async as far as the SDK client, answering the question with `answers`
    (strings, or exceptions to raise). Returns what the wake told the tape and whether it asked."""
    from tests.test_agent_guards import _FakeEntry
    monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
    monkeypatch.setattr(bootstrap, "list_entries", lambda: [_FakeEntry()])
    told = {"asked": []}

    def _tape(n=3, genesis_mode=False, number=None):
        told["number"] = number
        return "tape"
    monkeypatch.setattr(bootstrap, "assemble_tape", _tape)

    def _client(*a, **kw):
        raise _ReachedClient()
    monkeypatch.setattr(agent, "ClaudeSDKClient", _client)

    def run(answers=(), **kwargs):
        queue = list(answers)

        async def _ask(message):
            told["asked"].append(message)
            a = queue.pop(0)
            if isinstance(a, BaseException):
                raise a
            return a
        monkeypatch.setattr(agent, "_ask_line", _ask)
        try:
            anyio.run(lambda: agent._run_async(**kwargs))
            told["started"] = False
        except _ReachedClient:
            told["started"] = True
        return told
    return run


def _count(n: int) -> None:
    (config.get().model_dir / sessions.COUNT_FILE).write_text(f"{n}\n", encoding="utf-8")


def _left_open(session="2026-10-19-1655", number=25, instance=MODEL):
    sessions.save_state(session=session, instance=instance, phase="window", number=number,
                        started_at=datetime(2026, 10, 19, 18, 55),
                        cc_session_id=str(uuid.uuid5(uuid.NAMESPACE_URL, session)))


def test_nothing_open_nothing_asked(wake):
    _count(25)
    told = wake()
    assert told["asked"] == [] and told["started"] and told["number"] == 26


def test_enter_goes_back_to_the_open_window(wake, capsys):
    _count(25)
    _left_open()
    told = wake([""])
    out = capsys.readouterr().out
    assert "s25's window was left open (2026-10-19-1655" in out
    assert told["asked"] == ["Continue with s25? [Y/n] "]
    assert told["started"] and told["number"] == 25                 # the same session, its own number
    assert (config.get().model_dir / sessions.COUNT_FILE).read_text().strip() == "25"   # none claimed


def test_yes_too(wake):
    _count(25)
    _left_open()
    assert wake(["Y"])["number"] == 25


def test_no_starts_a_new_one_and_leaves_it_open(wake, capsys):
    _count(25)
    _left_open()
    told = wake(["n"])
    assert told["started"] and told["number"] == 26
    assert sessions.load_session("2026-10-19-1655")["phase"] == "window"   # still resumable
    assert "./wake --resume 2026-10-19-1655 returns to it" in capsys.readouterr().out


def test_it_asks_again_until_answered(wake):
    _count(25)
    _left_open()
    told = wake(["maybe", "n"])
    assert len(told["asked"]) == 2 and told["number"] == 26


@pytest.mark.parametrize("key", [KeyboardInterrupt(), EOFError()])
def test_ctrl_c_at_the_question_starts_nothing(wake, key):
    _count(25)
    _left_open()
    told = wake([key])
    assert not told["started"]
    assert (config.get().model_dir / sessions.COUNT_FILE).read_text().strip() == "25"   # no number taken


def test_no_console_starts_a_new_session(wake, capsys):
    _count(25)
    _left_open()
    told = wake([OSError("no console")])
    assert told["started"] and told["number"] == 26
    assert "./wake --resume 2026-10-19-1655" in capsys.readouterr().out


def test_only_this_houses_windows(wake):
    _count(25)
    _left_open(instance="claude-fable-1")
    told = wake()
    assert told["asked"] == [] and told["number"] == 26


def test_continue_and_resume_never_ask(wake):
    _count(25)
    _left_open()
    assert wake(continue_session=True)["asked"] == []
    assert wake(resume_session="2026-10-19-1655")["asked"] == []


def test_the_newest_is_named_and_the_others_counted(wake, capsys):
    _count(26)
    _left_open("2026-10-18-0900", 24)
    _left_open("2026-10-19-1655", 25)
    told = wake([""])
    out = capsys.readouterr().out
    assert told["number"] == 25 and "1 older one(s) too" in out


def test_its_last_activity_is_its_logs_last_write(capsys):
    _left_open()
    log = config.get().logs_dir / "2026-10-19-1655.log"
    log.write_text("x", encoding="utf-8")
    then = datetime.now() - timedelta(hours=9, minutes=5)
    import os
    os.utime(log, (then.timestamp(), then.timestamp()))
    assert agent._last_active(sessions.load_session("2026-10-19-1655")).replace(microsecond=0) == then.replace(microsecond=0)
    assert agent._ago(then) == "9 h ago"


def test_ago():
    now = datetime(2026, 10, 20, 8, 0)
    assert agent._ago(now - timedelta(minutes=25), now) == "25 min ago"
    assert agent._ago(now - timedelta(hours=30), now) == "30 h ago"
    assert agent._ago(now - timedelta(days=3, hours=2), now) == "3 days ago"
