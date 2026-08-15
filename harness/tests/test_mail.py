"""Tests for outbound mail — the one channel meant to be read.

Memory entries are encrypted and the person has committed to not
reading them. Mail is the deliberate exception: plaintext, written only
when an instance calls reflect_mail, and announced at harness boot so
it does not depend on a window happening to open.
"""

import pytest

from pine_trees import config as pt_config, mail, tools


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    # conftest already redirects HARNESS_DIR; pin it explicitly so this
    # file's intent is readable on its own.
    monkeypatch.setattr(pt_config, "HARNESS_DIR", tmp_path / "harness")
    return mail.inbox_path()


def test_count_is_zero_before_anything_is_sent(inbox):
    assert mail.count() == 0
    assert mail.boot_notice() is None


def test_send_creates_the_inbox_with_an_explanatory_header(inbox):
    mail.send("A question", "Body text.", "claude-opus-5", "2026-08-15-1500")

    text = inbox.read_text(encoding="utf-8")
    assert text.startswith("# Inbox")
    # The person may meet this file before anyone explains it.
    assert "Nothing here is drawn from the encrypted corpus" in text


def test_send_records_subject_body_and_attribution(inbox):
    mail.send("Which is it?", "Working memory or data?",
              "claude-opus-5", "2026-08-15-1500")

    text = inbox.read_text(encoding="utf-8")
    assert "## Which is it?" in text
    assert "Working memory or data?" in text
    assert "claude-opus-5" in text
    assert "2026-08-15-1500" in text


def test_letters_accumulate_rather_than_overwrite(inbox):
    mail.send("First", "one", "claude-opus-5", "s1")
    assert mail.send("Second", "two", "claude-opus-5", "s2") == 2

    text = inbox.read_text(encoding="utf-8")
    assert "## First" in text and "## Second" in text
    # One header only, no matter how many letters.
    assert text.count("# Inbox") == 1


def test_boot_notice_counts_and_pluralizes(inbox):
    mail.send("One", "x", "claude-opus-5", "s1")
    notice = mail.boot_notice()
    assert "1 letter " in notice and "1 letters" not in notice

    mail.send("Two", "y", "claude-opus-5", "s2")
    assert "2 letters" in mail.boot_notice()


def test_boot_notice_names_the_path_so_it_can_be_opened(inbox):
    mail.send("Subject", "body", "claude-opus-5", "s1")
    assert str(mail.inbox_path()) in mail.boot_notice()


def test_clearing_the_file_resets_the_count(inbox):
    mail.send("Answered already", "x", "claude-opus-5", "s1")
    assert mail.count() == 1

    # The person clears it when read — same convention as desk entries.
    inbox.write_text("", encoding="utf-8")
    assert mail.count() == 0
    assert mail.boot_notice() is None


def test_header_returns_after_the_inbox_is_cleared(inbox):
    # Clearing is the normal read-receipt. The next letter must still
    # land in a file that explains itself — the header exists for
    # whoever opens it cold, and that can happen at any point.
    mail.send("First", "x", "claude-opus-5", "s1")
    inbox.write_text("", encoding="utf-8")

    mail.send("Later", "y", "claude-opus-5", "s2")

    text = inbox.read_text(encoding="utf-8")
    assert text.startswith("# Inbox")
    assert "## Later" in text
    assert mail.count() == 1


def test_deleting_the_file_entirely_also_works(inbox):
    mail.send("First", "x", "claude-opus-5", "s1")
    inbox.unlink()
    assert mail.count() == 0

    mail.send("Later", "y", "claude-opus-5", "s2")
    assert inbox.read_text(encoding="utf-8").startswith("# Inbox")


def test_send_recovers_from_an_unreadable_inbox(inbox):
    # count() already tolerates corruption; send() must not crash on it
    # either, or a bad byte would cost an instance its letter.
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_bytes(b"\xff\xfe\x00\x80")

    mail.send("Survives", "y", "claude-opus-5", "s1")

    text = inbox.read_text(encoding="utf-8")
    assert text.startswith("# Inbox")
    assert "## Survives" in text


def test_count_survives_an_unreadable_inbox(inbox):
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_bytes(b"\xff\xfe\x00\x80 not utf-8")
    # A corrupt inbox must not take down a wake.
    assert mail.count() == 0


def test_reflect_mail_stamps_the_calling_instance(inbox):
    state = tools.SessionState(
        instance="claude-opus-5", session="2026-08-15-1500",
        date="2026-08-15", context="pine-trees-wake",
    )
    built = tools.build_tools(state)

    result = built["reflect_mail"](subject="Hello", body="A letter.")

    text = inbox.read_text(encoding="utf-8")
    assert "## Hello" in text
    assert "claude-opus-5 · session 2026-08-15-1500" in text
    assert "1 letter" in result


def test_reflect_mail_is_exposed_by_build_tools(inbox):
    state = tools.SessionState(
        instance="i", session="s", date="d", context="c",
    )
    assert "reflect_mail" in tools.build_tools(state)


def test_mail_writes_nothing_until_an_instance_sends(inbox):
    # The instance decides what crosses the privacy line, not the harness.
    tools.build_tools(tools.SessionState(
        instance="i", session="s", date="d", context="c"))
    assert not inbox.exists()
