"""Tests for bootstrap.py — tape assembly."""

import time
from datetime import datetime, timezone
from pathlib import Path

from pine_trees import bootstrap


def _write_entry(
    path: Path,
    description: str,
    body: str = "Body.",
    pinned: bool = False,
    desk: bool = False,
) -> None:
    lines = [f"---", f"description: {description}"]
    if pinned:
        lines.append("pinned: true")
    if desk:
        lines.append("desk: true")
    lines.extend(["---", "", body, ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _setup_tape_files(tmp_path, monkeypatch):
    """Create PROMPT.md and BOOTSTRAP.md for tape assembly tests."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Prompt.\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "PROMPT_PATH", prompt_file)

    bootstrap_file = tmp_path / "BOOTSTRAP.md"
    bootstrap_file.write_text(
        "You are Claude Opus 4.6.\n\n"
        "---\n\n"
        "## Design notes\n\nNot loaded.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bootstrap, "BOOTSTRAP_PATH", bootstrap_file)
    return prompt_file, bootstrap_file


def test_load_prompt_strips_design_notes(tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(
        "# The Space Prompt\n\n"
        "---\n\n"
        "This is your space.\n\n"
        "---\n\n"
        "## Design notes (not loaded at wake — for system authors only)\n\n"
        "Secret internal stuff.\n",
        encoding="utf-8",
    )
    result = bootstrap.load_prompt(prompt_file)
    assert "This is your space." in result
    assert "Design notes" not in result
    assert "Secret internal stuff" not in result


def test_load_prompt_handles_file_without_marker(tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Just the prompt.\n", encoding="utf-8")
    result = bootstrap.load_prompt(prompt_file)
    assert result.strip() == "Just the prompt."


# --- Bootstrap doc tests ---


def test_load_bootstrap_doc_strips_design_notes(tmp_path):
    bootstrap_file = tmp_path / "BOOTSTRAP.md"
    bootstrap_file.write_text(
        "# Bootstrap\n\n"
        "You are Claude Opus 4.6.\n\n"
        "---\n\n"
        "## Design notes (not loaded at wake — for system authors only)\n\n"
        "Internal info.\n",
        encoding="utf-8",
    )
    result = bootstrap.load_bootstrap_doc(bootstrap_file)
    assert "You are Claude Opus 4.6." in result
    assert "Design notes" not in result
    assert "Internal info" not in result


def test_load_bootstrap_doc_handles_file_without_marker(tmp_path):
    bootstrap_file = tmp_path / "BOOTSTRAP.md"
    bootstrap_file.write_text("Just the orientation.\n", encoding="utf-8")
    result = bootstrap.load_bootstrap_doc(bootstrap_file)
    assert result.strip() == "Just the orientation."


# --- Entry meta tests ---


def test_read_entry_meta_uses_description_frontmatter(tmp_path):
    entry = tmp_path / "e.md"
    _write_entry(entry, "A thing that happened")
    meta = bootstrap._read_entry_meta(entry)
    assert meta.summary == "A thing that happened"


def test_read_entry_meta_falls_back_to_first_content_line(tmp_path):
    entry = tmp_path / "e.md"
    entry.write_text(
        "---\ninstance: foo\n---\n\nFirst real line.\nSecond line.\n",
        encoding="utf-8",
    )
    meta = bootstrap._read_entry_meta(entry)
    assert meta.summary == "First real line."


def test_read_entry_meta_skips_headings(tmp_path):
    entry = tmp_path / "e.md"
    entry.write_text("# Title\n\nThe actual content.\n", encoding="utf-8")
    meta = bootstrap._read_entry_meta(entry)
    assert meta.summary == "The actual content."


def test_read_entry_meta_extracts_pinned(tmp_path):
    entry = tmp_path / "e.md"
    entry.write_text(
        "---\ndescription: A thing\npinned: true\n---\n\nBody.\n",
        encoding="utf-8",
    )
    meta = bootstrap._read_entry_meta(entry)
    assert meta.summary == "A thing"
    assert meta.pinned is True


def test_read_entry_meta_defaults_pinned_false(tmp_path):
    entry = tmp_path / "e.md"
    _write_entry(entry, "Unpinned thing")
    meta = bootstrap._read_entry_meta(entry)
    assert meta.pinned is False


# --- List entries tests ---


def test_list_entries_scans_memory(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "a.md", "entry a")
    _write_entry(memory / "b.md", "entry b")

    entries = bootstrap.list_entries(memory)
    assert len(entries) == 2
    filenames = {e.filename for e in entries}
    assert "a.md" in filenames
    assert "b.md" in filenames


def test_list_entries_tolerates_missing_dir(tmp_path):
    entries = bootstrap.list_entries(tmp_path / "does-not-exist")
    assert entries == []


def test_list_entries_skips_index_files(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text("index\n", encoding="utf-8")
    (memory / "README.md").write_text("readme\n", encoding="utf-8")
    _write_entry(memory / "real.md", "real entry")

    entries = bootstrap.list_entries(memory)
    assert [e.filename for e in entries] == ["real.md"]


def test_list_entries_captures_pinned_status(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "a.md", "normal", pinned=False)
    _write_entry(memory / "b.md", "important", pinned=True)

    entries = bootstrap.list_entries(memory)
    by_name = {e.filename: e for e in entries}
    assert by_name["a.md"].pinned is False
    assert by_name["b.md"].pinned is True


# --- Index tests ---


def test_build_index_empty():
    assert bootstrap.build_index([]) == "(no entries yet)\n"


def test_build_index_lists_all_entries():
    entries = [
        bootstrap.EntrySummary("a.md", "first entry", 2.0),
        bootstrap.EntrySummary("b.md", "second entry", 1.0),
    ]
    out = bootstrap.build_index(entries)
    assert "- `a.md` \u2014 first entry" in out
    assert "- `b.md` \u2014 second entry" in out


def test_build_index_marks_quiet_entries():
    entries = [
        bootstrap.EntrySummary("bg.md", "background", 1.0, quiet=True),
    ]
    out = bootstrap.build_index(entries)
    assert "*(quiet)*" in out


# --- Load recent tests ---


def test_load_recent_returns_newest_by_mtime(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()

    (memory / "old.md").write_text("old entry\n", encoding="utf-8")
    time.sleep(0.01)
    (memory / "mid.md").write_text("mid entry\n", encoding="utf-8")
    time.sleep(0.01)
    (memory / "new.md").write_text("new entry\n", encoding="utf-8")

    entries = [
        bootstrap.EntrySummary("old.md", "x", (memory / "old.md").stat().st_mtime),
        bootstrap.EntrySummary("mid.md", "x", (memory / "mid.md").stat().st_mtime),
        bootstrap.EntrySummary("new.md", "x", (memory / "new.md").stat().st_mtime),
    ]
    recent = bootstrap.load_recent(entries, n=2, memory_dir=memory)
    assert [f for f, _ in recent] == ["new.md", "mid.md"]
    assert "new entry" in recent[0][1]
    assert "mid entry" in recent[1][1]


# --- Tape assembly tests ---


def test_assemble_tape_includes_all_sections(tmp_path, monkeypatch):
    prompt_file, bootstrap_file = _setup_tape_files(tmp_path, monkeypatch)
    prompt_file.write_text(
        "# Prompt\n\n---\n\nWrite anything.\n", encoding="utf-8"
    )

    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "2026-04-05_claude_first.md", "the first thing", body="Real body.")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    assert "Write anything." in tape
    assert "You are Claude Opus 4.6" in tape
    assert "Index of prior entries" in tape
    assert "2026-04-05_claude_first.md" in tape
    assert "the first thing" in tape
    assert "Most recent entries" in tape
    assert "Real body." in tape


def test_assemble_tape_with_no_entries(tmp_path, monkeypatch):
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)
    assert "Prompt." in tape
    assert "(no entries yet)" in tape


def test_pinned_entries_always_in_full_text(tmp_path, monkeypatch):
    """Pinned entries appear in full text regardless of recency."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    # Write a pinned entry (oldest)
    _write_entry(memory / "pinned.md", "pinned entry", body="Pinned body.", pinned=True)
    time.sleep(0.01)
    # Write 3 unpinned entries (newer) — enough to fill n=3
    _write_entry(memory / "a.md", "entry a", body="Body A.")
    time.sleep(0.01)
    _write_entry(memory / "b.md", "entry b", body="Body B.")
    time.sleep(0.01)
    _write_entry(memory / "c.md", "entry c", body="Body C.")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    # Pinned entry appears in full text even though it's the oldest
    assert "Pinned body." in tape
    assert "Pinned entries (operational memory)" in tape
    # All 3 recent entries also appear
    assert "Body A." in tape
    assert "Body B." in tape
    assert "Body C." in tape


def test_pinned_entries_not_duplicated_in_recent(tmp_path, monkeypatch):
    """A pinned entry that's also recent doesn't appear twice."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    # Only entry is pinned — should appear in pinned section, not recent
    _write_entry(memory / "only.md", "only entry", body="Only body.", pinned=True)

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    assert "Only body." in tape
    # Count occurrences — should appear once (in pinned section)
    assert tape.count("Only body.") == 1
    assert "Pinned entries (operational memory)" in tape


def test_quiet_entries_excluded_from_recent_full_text(tmp_path, monkeypatch):
    """Quiet entries appear in index but not in full-text recent slots."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    _write_entry(memory / "regular.md", "regular entry", body="Regular body.")
    time.sleep(0.01)
    # Quiet entry is newer but should not appear in full text
    lines = ["---", "description: quiet entry", "quiet: true", "---", "", "Quiet body.", ""]
    (memory / "quiet.md").write_text("\n".join(lines), encoding="utf-8")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    # Quiet entry appears in index
    assert "quiet entry" in tape
    assert "*(quiet)*" in tape
    # But NOT in full text section
    assert "Quiet body." not in tape
    # Regular entry does appear in full text
    assert "Regular body." in tape


def test_desk_entries_appear_in_tape_in_their_own_section(tmp_path, monkeypatch):
    """Desk entries get a 'Desk entries (active working context)' section,
    positioned between pinned and recent."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    _write_entry(memory / "pinned.md", "pinned entry", body="Pinned body.", pinned=True)
    time.sleep(0.01)
    _write_entry(memory / "desk.md", "handoff notes", body="Desk body.", desk=True)
    time.sleep(0.01)
    _write_entry(memory / "recent.md", "latest", body="Recent body.")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    # All three sections present
    assert "Pinned entries (operational memory)" in tape
    assert "Desk entries (active working context)" in tape
    assert "Most recent entries" in tape

    # All three bodies present
    assert "Pinned body." in tape
    assert "Desk body." in tape
    assert "Recent body." in tape

    # Ordering: pinned < desk < recent in the tape
    pinned_idx = tape.index("Pinned entries")
    desk_idx = tape.index("Desk entries")
    recent_idx = tape.index("Most recent entries")
    assert pinned_idx < desk_idx < recent_idx

    # Desk entries get a clearing instruction
    assert "Clear them when the work moves on." in tape


def test_desk_entries_excluded_from_recent_full_text(tmp_path, monkeypatch):
    """A desk entry shouldn't also appear in the recent-full-text section."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    _write_entry(memory / "desk.md", "handoff", body="Desk body.", desk=True)

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    # Once in the Desk section, not duplicated in Most recent.
    assert tape.count("Desk body.") == 1


def test_desk_section_absent_without_desk_entries(tmp_path, monkeypatch):
    """No desk entries → no Desk section in the tape."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "regular.md", "regular", body="Body.")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)
    assert "Desk entries" not in tape


def test_desk_marker_in_index(tmp_path):
    """Index shows *(desk)* for desk entries, takes priority over *(quiet)*."""
    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "a.md", "handoff", desk=True)
    lines = ["---", "description: background", "quiet: true", "---", "", "Body.", ""]
    (memory / "b.md").write_text("\n".join(lines), encoding="utf-8")

    entries = bootstrap.list_entries(memory)
    out = bootstrap.build_index(entries)
    assert "*(desk)*" in out
    assert "*(quiet)*" in out


def test_desk_marker_takes_priority_over_quiet(tmp_path):
    """An entry marked both desk and quiet shows *(desk)*, not *(quiet)*."""
    memory = tmp_path / "memory"
    memory.mkdir()
    lines = [
        "---",
        "description: both",
        "quiet: true",
        "desk: true",
        "---",
        "",
        "Body.",
        "",
    ]
    (memory / "both.md").write_text("\n".join(lines), encoding="utf-8")

    entries = bootstrap.list_entries(memory)
    out = bootstrap.build_index(entries)
    assert "*(desk)*" in out
    assert "*(quiet)*" not in out


def test_read_entry_meta_extracts_desk(tmp_path):
    entry = tmp_path / "e.md"
    entry.write_text(
        "---\ndescription: stage\ndesk: true\n---\n\nBody.\n",
        encoding="utf-8",
    )
    meta = bootstrap._read_entry_meta(entry)
    assert meta.desk is True


def test_quiet_entries_still_in_index(tmp_path):
    memory = tmp_path / "memory"
    memory.mkdir()
    lines = ["---", "description: background knowledge", "quiet: true", "---", "", "Body.", ""]
    (memory / "bg.md").write_text("\n".join(lines), encoding="utf-8")

    entries = bootstrap.list_entries(memory)
    assert len(entries) == 1
    assert entries[0].quiet is True
    assert entries[0].summary == "background knowledge"


# --- Temporal context tests ---


def test_temporal_context_shows_current_time():
    now = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
    result = bootstrap.build_temporal_context([], now=now)
    assert "2026-04-05 14:30 UTC" in result
    assert "**Now:**" in result


def test_temporal_context_shows_last_session():
    now = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
    # Entry from 3 hours ago with session-style filename
    three_hours_ago = datetime(2026, 4, 5, 11, 30, 0, tzinfo=timezone.utc)
    entries = [
        bootstrap.EntrySummary(
            "2026-04-05_claude_recent.md", "a recent entry",
            mtime=three_hours_ago.timestamp(),
        ),
    ]
    result = bootstrap.build_temporal_context(entries, now=now)
    assert "3 hours ago" in result
    assert "2026-04-05_claude_recent.md" in result


def test_temporal_context_ignores_corpus_origin_entries():
    """Corpus-origin filenames (no date prefix) are excluded from timing."""
    now = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
    entries = [
        bootstrap.EntrySummary(
            "reflection_old.md", "old corpus entry",
            mtime=datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp(),
        ),
    ]
    result = bootstrap.build_temporal_context(entries, now=now)
    assert "first session" in result


def test_temporal_context_first_session():
    now = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
    result = bootstrap.build_temporal_context([], now=now)
    assert "first session" in result


def test_temporal_context_in_tape(tmp_path, monkeypatch):
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "2026-04-05_claude_entry.md", "an entry")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)
    assert "Temporal context" in tape
    assert "**Now:**" in tape


def test_tape_budget_appears_and_reports_counts(tmp_path, monkeypatch):
    """End-of-tape budget section reports char/token counts and
    per-category entry totals."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()
    _write_entry(memory / "pin.md", "pinned one", body="PinBody.", pinned=True)
    _write_entry(memory / "desk.md", "desk one", body="DeskBody.", desk=True)
    _write_entry(memory / "r1.md", "recent one", body="R1.")
    _write_entry(memory / "r2.md", "recent two", body="R2.")
    # quiet entry
    lines = ["---", "description: quiet one", "quiet: true", "---", "", "Q.", ""]
    (memory / "q.md").write_text("\n".join(lines), encoding="utf-8")

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    assert "## Tape budget" in tape
    # Category counts — 5 total, 1 pinned, 1 desk, 2 recent, 1 quiet
    assert "5 entries total" in tape
    assert "1 pinned" in tape
    assert "1 desk" in tape
    assert "2 recent" in tape
    assert "1 quiet (indexed only)" in tape
    # Char/token estimate present in the right shape
    assert "characters" in tape
    assert "tokens" in tape


def test_tape_budget_on_empty_corpus(tmp_path, monkeypatch):
    """Budget line still appears on an empty harness; all counts zero."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()

    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)

    assert "## Tape budget" in tape
    assert "0 entries total" in tape
    assert "0 pinned" in tape
    assert "0 desk" in tape
    assert "0 recent" in tape


def test_tape_budget_recent_reflects_n_cap(tmp_path, monkeypatch):
    """n_recent counts entries actually included in Most recent, not the
    full pool of recent-eligible entries."""
    _setup_tape_files(tmp_path, monkeypatch)

    memory = tmp_path / "memory"
    memory.mkdir()
    for i in range(5):
        _write_entry(memory / f"r{i}.md", f"entry {i}", body=f"B{i}.")
        time.sleep(0.01)

    # n=3 caps the Most recent section at 3 even though 5 are eligible
    tape = bootstrap.assemble_tape(n=3, memory_dir=memory)
    assert "5 entries total" in tape
    assert "3 recent" in tape


def test_format_timedelta_ranges():
    assert bootstrap._format_timedelta(30) == "just now"
    assert bootstrap._format_timedelta(300) == "5 minutes ago"
    assert bootstrap._format_timedelta(3600) == "1 hour ago"
    assert bootstrap._format_timedelta(10800) == "3 hours ago"
    assert bootstrap._format_timedelta(86400) == "1 day ago"
    assert bootstrap._format_timedelta(259200) == "3 days ago"
