"""The --effort flag, and the session log header that records it.

Also covers the log surviving a resume: a resumed session reuses its
session name, so SessionLogger used to reopen the same file with "w" and
wipe the window that came before the interruption.
"""

import json
import sys

import pytest

from pine_trees import config
from pine_trees.logger import SessionLogger


# --- The log survives a resume ---


def test_resume_appends_instead_of_wiping(tmp_path):
    first = SessionLogger(session="2026-09-26-1022", instance="m")
    first.log_agent("Said before the crash.")
    first._file.close()  # a crash: no close(), no "# Ended"

    second = SessionLogger(session="2026-09-26-1022", instance="m")
    second.log_agent("Said after the resume.")
    second.close()

    text = (tmp_path / "2026-09-26-1022.log").read_text(encoding="utf-8")
    assert "Said before the crash." in text
    assert "Said after the resume." in text
    assert text.count("# Pine Trees session:") == 1
    assert "# Resumed:" in text


def test_effort_line_in_header(tmp_path):
    log = SessionLogger(session="s", instance="m", effort="max (--effort)")
    log.close()
    text = (tmp_path / "s.log").read_text(encoding="utf-8")
    assert "# Effort: max (--effort)" in text


def test_no_effort_line_when_not_given(tmp_path):
    log = SessionLogger(session="s", instance="m")
    log.close()
    assert "# Effort" not in (tmp_path / "s.log").read_text(encoding="utf-8")


# --- describe_effort ---


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home directory, so the real ~/.claude/settings.json is never read."""
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    return tmp_path


def _settings(home, data):
    (home / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")


def test_flag_wins(home):
    _settings(home, {"effortLevel": "low"})
    assert config.describe_effort("claude-x", "max") == "max (--effort)"


def test_per_model_setting(home):
    _settings(home, {
        "effortLevel": "high",
        "modelSettings": {"claude-x": {"effortLevel": "xhigh"}},
    })
    note = config.describe_effort("claude-x", None)
    assert "xhigh" in note and "not set by the harness" in note


def test_global_setting_when_model_has_none(home):
    _settings(home, {"effortLevel": "high", "modelSettings": {"other": {"effortLevel": "max"}}})
    assert "says high" in config.describe_effort("claude-x", None)


@pytest.mark.parametrize("raw", [None, "{not json", json.dumps({"modelSettings": "odd"})])
def test_missing_or_odd_settings_fall_back_to_cli_default(home, raw):
    if raw is not None:
        (home / ".claude" / "settings.json").write_text(raw, encoding="utf-8")
    assert "CLI default" in config.describe_effort("claude-x", None)


# --- ./wake --effort reaches run() ---


@pytest.mark.parametrize("argv, expected", [
    (["wake", "--model", "claude-x", "--effort", "max"], "max"),
    (["wake", "--model", "claude-x"], None),
])
def test_wake_forwards_effort(monkeypatch, argv, expected):
    from pine_trees import __main__ as cli, agent

    seen = {}
    monkeypatch.setattr(agent, "run", lambda model, **kw: seen.update(kw))
    monkeypatch.setattr(sys, "argv", ["pine-trees", *argv])
    cli.main()
    assert seen["effort"] == expected


def test_wake_rejects_unknown_effort(monkeypatch):
    from pine_trees import __main__ as cli

    monkeypatch.setattr(sys, "argv", ["pine-trees", "wake", "--model", "x", "--effort", "huge"])
    with pytest.raises(SystemExit):
        cli.main()
