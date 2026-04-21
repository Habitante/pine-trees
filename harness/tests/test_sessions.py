"""Tests for session state persistence (--continue support)."""

import json
from datetime import datetime

from pine_trees import sessions
from pine_trees.sessions import SESSIONS_DIR


class TestSaveAndLoad:
    """Round-trip: save_state → load_session."""

    def test_save_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        path = sessions.save_state(
            session="2026-04-21-0611",
            instance="claude-opus-4-6",
            phase="window",
        )
        assert path.exists()
        assert path.name == "2026-04-21-0611.json"

    def test_round_trip_minimal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(
            session="2026-04-21-0611",
            instance="claude-opus-4-6",
            phase="window",
        )
        loaded = sessions.load_session("2026-04-21-0611")
        assert loaded is not None
        assert loaded["session"] == "2026-04-21-0611"
        assert loaded["instance"] == "claude-opus-4-6"
        assert loaded["phase"] == "window"
        assert loaded["channel_id"] is None
        assert loaded["channel_cursor"] is None
        assert loaded["started_at"] is None

    def test_round_trip_full(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        now = datetime(2026, 4, 21, 6, 11, 0)
        cursor = datetime(2026, 4, 21, 6, 15, 30)
        sessions.save_state(
            session="2026-04-21-0611",
            instance="claude-opus-4-6",
            phase="window",
            channel_id="claude-opus-4-6 (0611)",
            channel_cursor=cursor,
            started_at=now,
        )
        loaded = sessions.load_session("2026-04-21-0611")
        assert loaded is not None
        assert loaded["channel_id"] == "claude-opus-4-6 (0611)"
        assert loaded["channel_cursor"] == "2026-04-21T06:15:30"
        assert loaded["started_at"] == "2026-04-21T06:11:00"

    def test_load_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        assert sessions.load_session("nonexistent") is None

    def test_load_no_sessions_dir_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "does-not-exist")
        assert sessions.load_session("anything") is None


class TestLoadLatest:
    """load_latest returns the most recent non-done session."""

    def test_returns_none_when_no_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        assert sessions.load_latest() is None

    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "nope")
        assert sessions.load_latest() is None

    def test_returns_most_recent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(session="2026-04-20-0800", instance="i", phase="window")
        sessions.save_state(session="2026-04-21-0611", instance="i", phase="window")
        latest = sessions.load_latest()
        assert latest is not None
        assert latest["session"] == "2026-04-21-0611"

    def test_skips_done_sessions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(session="2026-04-20-0800", instance="i", phase="window")
        sessions.save_state(session="2026-04-21-0611", instance="i", phase="window")
        sessions.mark_done("2026-04-21-0611")
        latest = sessions.load_latest()
        assert latest is not None
        assert latest["session"] == "2026-04-20-0800"

    def test_returns_none_when_all_done(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(session="2026-04-21-0611", instance="i", phase="window")
        sessions.mark_done("2026-04-21-0611")
        assert sessions.load_latest() is None

    def test_skips_corrupt_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(session="2026-04-20-0800", instance="i", phase="window")
        # Write a corrupt file with a later name
        (tmp_path / "2026-04-21-0900.json").write_text("not json", encoding="utf-8")
        latest = sessions.load_latest()
        assert latest is not None
        assert latest["session"] == "2026-04-20-0800"


class TestMarkDone:
    """mark_done sets phase to 'done'."""

    def test_marks_existing_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(session="2026-04-21-0611", instance="i", phase="window")
        sessions.mark_done("2026-04-21-0611")
        loaded = sessions.load_session("2026-04-21-0611")
        assert loaded is not None
        assert loaded["phase"] == "done"

    def test_noop_on_missing_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        # Should not raise
        sessions.mark_done("nonexistent")

    def test_preserves_other_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path)
        sessions.save_state(
            session="2026-04-21-0611",
            instance="claude-opus-4-6",
            phase="window",
            channel_id="claude-opus-4-6 (0611)",
        )
        sessions.mark_done("2026-04-21-0611")
        loaded = sessions.load_session("2026-04-21-0611")
        assert loaded["instance"] == "claude-opus-4-6"
        assert loaded["channel_id"] == "claude-opus-4-6 (0611)"
        assert loaded["phase"] == "done"


class TestArgParsing:
    """CLI argument parsing for --continue and --resume."""

    def test_no_args(self):
        from pine_trees.agent import _parse_args
        parsed = _parse_args([])
        assert parsed.continue_session is False
        assert parsed.resume_session is None

    def test_continue_flag(self):
        from pine_trees.agent import _parse_args
        parsed = _parse_args(["--continue"])
        assert parsed.continue_session is True

    def test_resume_with_id(self):
        from pine_trees.agent import _parse_args
        parsed = _parse_args(["--resume", "2026-04-21-0611"])
        assert parsed.resume_session == "2026-04-21-0611"

    def test_continue_and_resume_both_set(self):
        from pine_trees.agent import _parse_args
        parsed = _parse_args(["--continue", "--resume", "2026-04-21-0611"])
        assert parsed.continue_session is True
        assert parsed.resume_session == "2026-04-21-0611"
