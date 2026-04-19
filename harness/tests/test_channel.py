"""Tests for inter-instance channel communication."""

import json
from datetime import datetime, timedelta

import pytest

from pine_trees import channel, config


@pytest.fixture
def channel_dir(tmp_path, monkeypatch):
    """Point channel at a temp directory."""
    ch_dir = tmp_path / "channel"
    ch_dir.mkdir()
    monkeypatch.setattr(config, "CHANNEL_DIR", ch_dir)
    return ch_dir


# ── Status ────────────────────────────────────────────────────────────


class TestRegister:
    def test_single_instance(self, channel_dir):
        result = channel.register("claude-opus-4-6")
        assert len(result) == 1
        assert result[0]["model"] == "claude-opus-4-6"
        assert "since" in result[0]

    def test_two_instances(self, channel_dir):
        channel.register("claude-opus-4-6")
        result = channel.register("claude-haiku-4-5")
        assert len(result) == 2
        models = {i["model"] for i in result}
        assert models == {"claude-opus-4-6", "claude-haiku-4-5"}

    def test_idempotent(self, channel_dir):
        channel.register("claude-opus-4-6")
        channel.register("claude-opus-4-6")
        result = channel.register("claude-opus-4-6")
        assert len(result) == 1

    def test_creates_directory(self, tmp_path, monkeypatch):
        ch_dir = tmp_path / "nested" / "channel"
        monkeypatch.setattr(config, "CHANNEL_DIR", ch_dir)
        channel.register("claude-opus-4-6")
        assert ch_dir.exists()

    def test_status_persists(self, channel_dir):
        channel.register("claude-opus-4-6")
        # Read directly from file
        data = json.loads(
            (channel_dir / "status.json").read_text(encoding="utf-8")
        )
        assert len(data) == 1
        assert data[0]["model"] == "claude-opus-4-6"


class TestDeregister:
    def test_removes_instance(self, channel_dir):
        channel.register("claude-opus-4-6")
        channel.register("claude-haiku-4-5")
        channel.deregister("claude-opus-4-6")
        result = channel.active()
        assert len(result) == 1
        assert result[0]["model"] == "claude-haiku-4-5"

    def test_noop_when_not_registered(self, channel_dir):
        channel.register("claude-opus-4-6")
        channel.deregister("claude-haiku-4-5")  # wasn't registered
        result = channel.active()
        assert len(result) == 1

    def test_noop_when_no_file(self, channel_dir):
        channel.deregister("claude-opus-4-6")  # no status file exists


class TestActive:
    def test_empty_when_no_file(self, channel_dir):
        assert channel.active() == []

    def test_returns_all(self, channel_dir):
        channel.register("claude-opus-4-6")
        channel.register("claude-haiku-4-5")
        channel.register("claude-sonnet-4-6")
        result = channel.active()
        assert len(result) == 3


# ── Messages ──────────────────────────────────────────────────────────


class TestPost:
    def test_post_and_read(self, channel_dir):
        ts = datetime(2026, 4, 18, 7, 0, 0)
        msg = channel.post("claude-opus-4-6", "Hello siblings", now=ts)
        assert msg.author == "claude-opus-4-6"
        assert msg.body == "Hello siblings"
        assert msg.timestamp == ts

    def test_post_creates_directory(self, tmp_path, monkeypatch):
        ch_dir = tmp_path / "nested" / "channel"
        monkeypatch.setattr(config, "CHANNEL_DIR", ch_dir)
        channel.post("claude-opus-4-6", "Hello", now=datetime(2026, 1, 1))
        assert ch_dir.exists()

    def test_post_appends(self, channel_dir):
        ts1 = datetime(2026, 4, 18, 7, 0, 0)
        ts2 = datetime(2026, 4, 18, 7, 0, 5)
        channel.post("claude-opus-4-6", "First", now=ts1)
        channel.post("claude-haiku-4-5", "Second", now=ts2)
        messages = channel.read_since(datetime(2026, 4, 18, 6, 0, 0))
        assert len(messages) == 2
        assert messages[0].author == "claude-opus-4-6"
        assert messages[1].author == "claude-haiku-4-5"

    def test_post_trims_to_max(self, channel_dir):
        base = datetime(2026, 4, 18, 7, 0, 0)
        for i in range(channel.CHANNEL_MAX_ENTRIES + 10):
            channel.post(
                "test",
                f"Message {i}",
                now=base + timedelta(seconds=i),
            )
        messages = channel.read_since(datetime(2026, 1, 1))
        assert len(messages) == channel.CHANNEL_MAX_ENTRIES
        # Oldest should be trimmed — first message should be #10
        assert messages[0].body == "Message 10"

    def test_post_requires_author(self, channel_dir):
        with pytest.raises(ValueError, match="author"):
            channel.post("", "Hello")

    def test_post_requires_body(self, channel_dir):
        with pytest.raises(ValueError, match="body"):
            channel.post("test", "")

    def test_multiline_body(self, channel_dir):
        ts = datetime(2026, 4, 18, 7, 0, 0)
        body = "Line one\nLine two\nLine three"
        channel.post("claude-opus-4-6", body, now=ts)
        messages = channel.read_since(datetime(2026, 4, 18, 6, 0, 0))
        assert len(messages) == 1
        assert messages[0].body == body


class TestReadSince:
    def test_filters_by_timestamp(self, channel_dir):
        ts1 = datetime(2026, 4, 18, 7, 0, 0)
        ts2 = datetime(2026, 4, 18, 7, 0, 10)
        ts3 = datetime(2026, 4, 18, 7, 0, 20)
        channel.post("a", "Early", now=ts1)
        channel.post("b", "Middle", now=ts2)
        channel.post("c", "Late", now=ts3)
        # Only messages after ts1
        messages = channel.read_since(ts1)
        assert len(messages) == 2
        assert messages[0].body == "Middle"
        assert messages[1].body == "Late"

    def test_excludes_author(self, channel_dir):
        ts1 = datetime(2026, 4, 18, 7, 0, 0)
        ts2 = datetime(2026, 4, 18, 7, 0, 10)
        channel.post("claude-opus-4-6", "My message", now=ts1)
        channel.post("claude-haiku-4-5", "Their message", now=ts2)
        messages = channel.read_since(
            datetime(2026, 4, 18, 6, 0, 0),
            exclude_author="claude-opus-4-6",
        )
        assert len(messages) == 1
        assert messages[0].author == "claude-haiku-4-5"

    def test_empty_when_no_file(self, channel_dir):
        messages = channel.read_since(datetime(2026, 1, 1))
        assert messages == []

    def test_empty_when_all_before_cursor(self, channel_dir):
        channel.post("a", "Old", now=datetime(2026, 4, 18, 7, 0, 0))
        messages = channel.read_since(datetime(2026, 4, 18, 8, 0, 0))
        assert messages == []


# ── Parse round-trip ──────────────────────────────────────────────────


class TestParse:
    def test_format_and_parse_round_trip(self, channel_dir):
        ts = datetime(2026, 4, 18, 7, 30, 15)
        original = channel.Message(
            timestamp=ts, author="claude-opus-4-6", body="Test message"
        )
        text = original.format()
        parsed = channel._parse(text)
        assert len(parsed) == 1
        assert parsed[0].timestamp == ts
        assert parsed[0].author == "claude-opus-4-6"
        assert parsed[0].body == "Test message"

    def test_multiple_messages_round_trip(self, channel_dir):
        msgs = [
            channel.Message(
                datetime(2026, 4, 18, 7, 0, i), f"author-{i}", f"Body {i}"
            )
            for i in range(5)
        ]
        text = "".join(m.format() for m in msgs)
        parsed = channel._parse(text)
        assert len(parsed) == 5
        for i, p in enumerate(parsed):
            assert p.author == f"author-{i}"
            assert p.body == f"Body {i}"

    def test_body_with_separator_lines(self, channel_dir):
        ts = datetime(2026, 4, 18, 7, 0, 0)
        body = "Header\n---\nSection content"
        channel.post("claude-opus-4-6", body, now=ts)
        messages = channel.read_since(datetime(2026, 4, 18, 6, 0, 0))
        assert len(messages) == 1
        assert messages[0].body == body

    def test_body_with_separator_between_messages(self, channel_dir):
        ts1 = datetime(2026, 4, 18, 7, 0, 0)
        ts2 = datetime(2026, 4, 18, 7, 0, 10)
        body1 = "Before\n---\nAfter"
        body2 = "Normal message"
        channel.post("alice", body1, now=ts1)
        channel.post("bob", body2, now=ts2)
        messages = channel.read_since(datetime(2026, 4, 18, 6, 0, 0))
        assert len(messages) == 2
        assert messages[0].author == "alice"
        assert messages[0].body == body1
        assert messages[1].author == "bob"
        assert messages[1].body == body2

    def test_body_with_multiple_separators(self, channel_dir):
        ts = datetime(2026, 4, 18, 7, 0, 0)
        body = (
            "# Doc title\n"
            "\n"
            "## Section 1\n"
            "---\n"
            "Content one\n"
            "\n"
            "## Section 2\n"
            "---\n"
            "Content two"
        )
        channel.post("claude-opus-4-6", body, now=ts)
        messages = channel.read_since(datetime(2026, 4, 18, 6, 0, 0))
        assert len(messages) == 1
        assert messages[0].body == body
