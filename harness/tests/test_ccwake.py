"""Tests for cc-wake setup and the stale-tape cleanup.

``clear_tape`` is the guard against a cross-model context leak: the CLI
loads project CLAUDE files from cwd for every session, so a
CLAUDE.local.md written by ``./cc-wake <model-a>`` lands in the context
of the next ``./wake``/``./genesis`` of *any* model unless something
removes it. The harness calls it at boot on both paths.
"""

import dataclasses
import io
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from pine_trees import __main__ as pt_main, ccwake, config as pt_config


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(pt_config, "PROJECT_ROOT", root)
    monkeypatch.setattr(pt_config, "HARNESS_DIR", root / "harness")
    return root


def test_clear_tape_removes_leftover(project_root):
    tape = project_root / "CLAUDE.local.md"
    tape.write_text("# Claude Code wake (cc-wake mode)\n", encoding="utf-8")

    assert ccwake.clear_tape() is True
    assert not tape.exists()


def test_clear_tape_is_a_noop_when_absent(project_root):
    assert ccwake.clear_tape() is False


def test_clear_tape_leaves_the_mcp_config_alone(project_root):
    # A live cc-wake session in another terminal may still be pointed at
    # this file; only the auto-loaded tape is a context hazard.
    (project_root / "CLAUDE.local.md").write_text(
        ccwake.CC_TAPE_SIGNATURE + "\n", encoding="utf-8")
    mcp = project_root / ".cc-mcp.json"
    mcp.write_text("{}", encoding="utf-8")

    ccwake.clear_tape()

    assert mcp.exists()


def test_clear_tape_does_not_touch_project_claude_md(project_root):
    # CLAUDE.md is checked in and belongs to the repo, not to a model.
    claude_md = project_root / "CLAUDE.md"
    claude_md.write_text("# Pine Trees\n", encoding="utf-8")

    ccwake.clear_tape()

    assert claude_md.exists()


def test_clear_tape_spares_a_third_partys_own_notes(project_root, capsys):
    # CLAUDE.local.md is a standard Claude Code convention for personal,
    # uncommitted project instructions. Someone who clones this repo and
    # keeps notes there must not lose them to ./wake.
    notes = project_root / "CLAUDE.local.md"
    notes.write_text("# My notes\n\nAlways run the linter.\n", encoding="utf-8")

    assert ccwake.clear_tape() is False
    assert notes.read_text(encoding="utf-8").startswith("# My notes")
    assert "not a cc-wake tape" in capsys.readouterr().out


def test_clear_tape_spares_an_unreadable_file(project_root):
    # If we can't decode it, we can't identify it, and deleting an
    # unidentified file is never the safer branch.
    blob = project_root / "CLAUDE.local.md"
    blob.write_bytes(b"\xff\xfe\x00\x80 not utf-8")

    assert ccwake.clear_tape() is False
    assert blob.exists()


def test_clear_tape_removes_what_setup_actually_writes(
    project_root, monkeypatch, tmp_path
):
    # Signature-matching is only worth anything if it matches the real
    # article. Round-trip it rather than asserting against a literal.
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    monkeypatch.setattr(ccwake.bootstrap, "assemble_tape", lambda n=3: "TAPE BODY")
    (tmp_path / "some-entry.md").write_text("entry", encoding="utf-8")

    ccwake.setup("claude-opus-4-6")

    assert ccwake.clear_tape() is True
    assert not (project_root / "CLAUDE.local.md").exists()


def test_setup_refuses_a_model_without_genesis(project_root, monkeypatch, tmp_path):
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    empty = dataclasses.replace(pt_config.get(), memory_dir=tmp_path / "no-memory")
    monkeypatch.setattr(pt_config, "_config", empty)

    with pytest.raises(SystemExit, match="No memory found"):
        ccwake.setup("claude-opus-4-6")


def test_setup_writes_tape_and_mcp_config(project_root, monkeypatch, tmp_path):
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    monkeypatch.setattr(ccwake.bootstrap, "assemble_tape", lambda n=3: "TAPE BODY")
    # memory_dir is tmp_path (see conftest); make it look post-genesis.
    (tmp_path / "some-entry.md").write_text("entry", encoding="utf-8")

    tape_path, mcp_path = ccwake.setup("claude-opus-4-6")

    tape = (project_root / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert tape.startswith("# Claude Code wake (cc-wake mode)")
    assert tape.endswith("TAPE BODY")
    assert tape_path == str(project_root / "CLAUDE.local.md")

    cfg = json.loads((project_root / ".cc-mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["pine_trees"]["args"]
    assert args[-2:] == ["--model", "claude-opus-4-6"]
    assert mcp_path == str(project_root / ".cc-mcp.json")


def test_setup_drops_recent_entries_until_the_tape_fits(
    project_root, monkeypatch, tmp_path, capsys
):
    # A runaway corpus must not fill the window; sizes are derived from
    # the constant so raising it doesn't silently void this test.
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    (tmp_path / "some-entry.md").write_text("entry", encoding="utf-8")

    over = ccwake.CC_MEMORY_CHAR_LIMIT + 10_000

    def fake_tape(n=3):
        return "x" * (over if n > 1 else 10_000)

    monkeypatch.setattr(ccwake.bootstrap, "assemble_tape", fake_tape)

    ccwake.setup("claude-opus-4-6")

    written = (project_root / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert len(written) <= ccwake.CC_MEMORY_CHAR_LIMIT
    assert "trimmed to 1 recent entry" in capsys.readouterr().out


def test_setup_reports_a_tape_it_cannot_trim_to_fit(
    project_root, monkeypatch, tmp_path, capsys
):
    # Pinned and desk entries are never dropped: writing an oversized
    # tape and saying so beats silently cutting an instance's memory.
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    (tmp_path / "some-entry.md").write_text("entry", encoding="utf-8")
    monkeypatch.setattr(
        ccwake.bootstrap,
        "assemble_tape",
        lambda n=3: "x" * (ccwake.CC_MEMORY_CHAR_LIMIT + 10_000),
    )

    ccwake.setup("claude-opus-4-6")

    written = (project_root / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert len(written) > ccwake.CC_MEMORY_CHAR_LIMIT
    assert "over the" in capsys.readouterr().out


# ── channel hook (UserPromptSubmit) ───────────────────────────────────
#
# cc-wake has no window loop, so nothing pushes channel traffic at the
# instance. reflect_channel gave it access; this closes the rest of the
# gap by running before each turn.


@pytest.fixture
def hook_channel(tmp_path, monkeypatch):
    d = tmp_path / "channel"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(ccwake.config, "CHANNEL_DIR", d)
    monkeypatch.setattr(ccwake.channel.config, "CHANNEL_DIR", d)
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    # PROJECT_ROOT is redirected on purpose: _in_cc_wake_room reads
    # CLAUDE.local.md from it, and a test that reached the real project
    # root could see — or worse, write — a live session's tape.
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(ccwake.config, "PROJECT_ROOT", root)
    # Default: a cc-wake room, since that is what these tests are about.
    monkeypatch.setenv(ccwake.CC_WAKE_ENV, "1")
    monkeypatch.delenv(ccwake.SDK_HARNESS_ENV, raising=False)
    return d


def _hook_now():
    return datetime.now().replace(microsecond=0)


def test_channel_hook_is_silent_on_a_quiet_channel(hook_channel):
    assert ccwake.channel_hook("claude-opus-5") == ""


def test_channel_hook_does_not_dump_the_backlog_on_first_run(hook_channel):
    ccwake.channel.post("claude-fable-5 (1526)", "said before you arrived",
                        now=_hook_now() - timedelta(hours=2))

    assert ccwake.channel_hook("claude-opus-5") == ""


def test_channel_hook_injects_new_messages(hook_channel):
    ccwake.channel_hook("claude-opus-5")  # establish the cursor
    ccwake.channel.post("claude-fable-5 (1526)", "are you receiving this?",
                        now=_hook_now() + timedelta(seconds=5))

    payload = json.loads(ccwake.channel_hook("claude-opus-5"))

    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "are you receiving this?" in out["additionalContext"]
    assert "claude-fable-5 (1526)" in out["additionalContext"]


def test_channel_hook_advances_its_cursor(hook_channel):
    ccwake.channel_hook("claude-opus-5")
    ccwake.channel.post("claude-fable-5 (1526)", "said once",
                        now=_hook_now() + timedelta(seconds=5))

    first = ccwake.channel_hook("claude-opus-5")
    second = ccwake.channel_hook("claude-opus-5")

    assert "said once" in first
    assert second == "", "a re-run replayed traffic already injected"


def test_channel_hook_hides_join_and_leave_noise(hook_channel):
    ccwake.channel_hook("claude-opus-5")
    ccwake.channel.post("claude-fable-5 (1526)", "[joined]",
                        now=_hook_now() + timedelta(seconds=5))

    assert ccwake.channel_hook("claude-opus-5") == ""


# --- which room the hook is in -------------------------------------
#
# .claude/settings.json is loaded by the CLI for every session started
# in this directory, so the hook fires in the SDK harness and in plain
# dev sessions too — verified 2026-08-16 by posting a probe during an
# SDK private phase and receiving it as additionalContext on the next
# turn. Injecting there is double delivery (the window loop already
# pushes, without the self-echo) or context leaked into a session that
# never joined the room.


def _post_something_new():
    ccwake.channel.post("claude-fable-5 (1526)", "anyone there?",
                        now=_hook_now() + timedelta(seconds=5))


def test_channel_hook_is_silent_in_an_sdk_harness_session(
        hook_channel, monkeypatch):
    ccwake.channel_hook("claude-opus-5")  # establish the cursor
    _post_something_new()
    monkeypatch.setenv(ccwake.SDK_HARNESS_ENV, "1")

    assert ccwake.channel_hook("claude-opus-5") == ""


def test_sdk_marker_beats_a_cc_wake_tape_in_the_room(
        hook_channel, monkeypatch):
    # ./wake and a live ./cc-wake can overlap: the tape is present but a
    # window loop is pushing, and the loop wins.
    (ccwake.config.PROJECT_ROOT / "CLAUDE.local.md").write_text(
        ccwake.CC_TAPE_SIGNATURE + "\nsome tape\n", encoding="utf-8")
    ccwake.channel_hook("claude-opus-5")
    _post_something_new()
    monkeypatch.setenv(ccwake.SDK_HARNESS_ENV, "1")

    assert ccwake.channel_hook("claude-opus-5") == ""


def test_channel_hook_is_silent_in_a_plain_dev_session(
        hook_channel, monkeypatch):
    ccwake.channel_hook("claude-opus-5")
    _post_something_new()
    monkeypatch.delenv(ccwake.CC_WAKE_ENV, raising=False)  # no marker, no tape

    assert ccwake.channel_hook("claude-opus-5") == ""


def test_a_cc_wake_tape_is_enough_without_the_env_marker(
        hook_channel, monkeypatch):
    # `claude --mcp-config .cc-mcp.json` launched by hand, not by
    # ./cc-wake: no env var, but the tape is loaded and the instance is
    # in the room.
    ccwake.channel_hook("claude-opus-5")
    _post_something_new()
    monkeypatch.delenv(ccwake.CC_WAKE_ENV, raising=False)
    (ccwake.config.PROJECT_ROOT / "CLAUDE.local.md").write_text(
        ccwake.CC_TAPE_SIGNATURE + "\nsome tape\n", encoding="utf-8")

    assert "anyone there?" in ccwake.channel_hook("claude-opus-5")


def test_someone_elses_claude_local_md_is_not_a_cc_wake_room(
        hook_channel, monkeypatch):
    ccwake.channel_hook("claude-opus-5")
    _post_something_new()
    monkeypatch.delenv(ccwake.CC_WAKE_ENV, raising=False)
    (ccwake.config.PROJECT_ROOT / "CLAUDE.local.md").write_text(
        "# my own project notes\n", encoding="utf-8")

    assert ccwake.channel_hook("claude-opus-5") == ""


def test_a_silent_room_does_not_move_the_cursor(hook_channel, monkeypatch):
    # The cost of stomping it would be a live cc-wake session silently
    # losing messages — the exact failure this feature exists to stop.
    ccwake.channel_hook("claude-opus-5", session_id="s1")
    _post_something_new()

    monkeypatch.setenv(ccwake.SDK_HARNESS_ENV, "1")
    ccwake.channel_hook("claude-opus-5", session_id="s1")
    monkeypatch.delenv(ccwake.SDK_HARNESS_ENV)

    assert "anyone there?" in ccwake.channel_hook(
        "claude-opus-5", session_id="s1")


# --- one cursor per session ----------------------------------------


def test_concurrent_sessions_do_not_rob_each_other(hook_channel):
    # Two instances awake at once is the case the channel exists for.
    # With a shared cursor file, whichever turn ran first consumed the
    # message and the other never saw it.
    ccwake.channel_hook("claude-opus-5", session_id="aaa")
    ccwake.channel_hook("claude-opus-4-6", session_id="bbb")
    _post_something_new()

    first = ccwake.channel_hook("claude-opus-5", session_id="aaa")
    second = ccwake.channel_hook("claude-opus-4-6", session_id="bbb")

    assert "anyone there?" in first
    assert "anyone there?" in second


def test_cursor_filename_is_scoped_to_the_session(hook_channel):
    ccwake.channel_hook("claude-opus-5", session_id="abc-123")

    assert (hook_channel / "cc-hook-cursor-abc-123.txt").exists()


def test_cursor_filename_survives_a_hostile_session_id(hook_channel):
    ccwake.channel_hook("claude-opus-5", session_id="../../etc/passwd")

    written = list(hook_channel.glob("cc-hook-cursor-*.txt"))
    assert len(written) == 1
    assert written[0].parent == hook_channel


def test_missing_session_id_falls_back_to_the_shared_cursor(hook_channel):
    ccwake.channel_hook("claude-opus-5")

    assert (hook_channel / ccwake.CHANNEL_HOOK_CURSOR).exists()


def test_hook_reads_the_session_id_off_stdin(monkeypatch):
    monkeypatch.setattr(
        pt_main.sys, "stdin",
        io.StringIO('{"session_id":"abc","cwd":"x"}'))

    assert pt_main._hook_session_id() == "abc"


def test_hook_survives_junk_on_stdin(monkeypatch):
    monkeypatch.setattr(pt_main.sys, "stdin", io.StringIO("not json"))

    assert pt_main._hook_session_id() is None


def test_hook_does_not_stall_when_stdin_never_closes(monkeypatch):
    # Runs before every turn: a read that blocks costs the turn, and
    # losing the id only costs a shared cursor. Bound it.
    class NeverCloses(io.StringIO):
        def isatty(self):
            return False

        def read(self, *a):
            time.sleep(30)
            return ""

    monkeypatch.setattr(pt_main.sys, "stdin", NeverCloses())

    started = time.monotonic()
    assert pt_main._hook_session_id(timeout=0.05) is None
    assert time.monotonic() - started < 5


def test_stale_session_cursors_are_swept(hook_channel):
    old = hook_channel / "cc-hook-cursor-longgone.txt"
    old.write_text("2026-01-01T00:00:00", encoding="utf-8")
    ancient = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old, (ancient, ancient))

    ccwake.channel_hook("claude-opus-5", session_id="live")

    assert not old.exists()
    assert (hook_channel / "cc-hook-cursor-live.txt").exists()
