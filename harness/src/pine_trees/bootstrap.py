"""Tape assembly for Pine Trees.

The tape is what Claude reads at wake, in order:
  1. The space prompt (PROMPT.md, wake-time portion only)
  2. A short bootstrap doc (who you are, the system, the person)
  3. An index of prior entries
  4. The N most recent entries in full (by mtime)

All entries live in memory/ — encrypted at rest, private, editable.
Some are old seed reflections from before the harness existed; some are
recent working entries. No special treatment by origin.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .config import BOOTSTRAP_PATH, PROMPT_PATH
from .storage import read_file


@dataclass
class EntryMeta:
    """Lightweight metadata extracted from frontmatter without full parse."""
    summary: str
    pinned: bool = False
    quiet: bool = False


@dataclass
class EntrySummary:
    filename: str
    summary: str
    mtime: float
    pinned: bool = False
    quiet: bool = False


def load_prompt(path: Path | None = None) -> str:
    """Load PROMPT.md, returning only the wake-time portion.

    Truncates at '## Design notes' — content after that marker is for system
    authors, not for the waking instance.

    Resolves PROMPT_PATH from the module at call time so tests can
    monkeypatch it; a default-value binding would capture the original.
    """
    if path is None:
        path = PROMPT_PATH
    text = path.read_text(encoding="utf-8")
    marker = "## Design notes"
    idx = text.find(marker)
    if idx != -1:
        text = text[:idx]
    return text.rstrip() + "\n"


def _read_entry_meta(path: Path) -> EntryMeta:
    """Extract summary and pinned status from an entry file.

    Prefers the `description:` frontmatter field for summary. Falls back to
    the first non-empty non-heading content line. Checks for `pinned: true`.
    """
    text = read_file(path)
    lines = text.split("\n")

    summary = ""
    pinned = False
    quiet = False
    in_frontmatter = False
    content_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if i == 0 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                content_start = i + 1
                break
            if stripped.startswith("description:"):
                summary = stripped.split(":", 1)[1].strip()
            if stripped.startswith("pinned:"):
                val = stripped.split(":", 1)[1].strip().lower()
                pinned = val == "true"
            if stripped.startswith("quiet:"):
                val = stripped.split(":", 1)[1].strip().lower()
                quiet = val == "true"

    if not summary:
        for line in lines[content_start:]:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                summary = stripped[:120]
                break

    return EntryMeta(summary=summary or "(no summary)", pinned=pinned, quiet=quiet)


def list_entries(
    memory_dir: Path | None = None,
) -> list[EntrySummary]:
    """List all entries from memory/.

    Skips index/metadata files (MEMORY.md, README.md).
    Resolves memory_dir from the active config at call time so tests that
    install a tmp config see the right directory.
    """
    if memory_dir is None:
        memory_dir = config.get().memory_dir
    skip = {"MEMORY.md", "README.md"}
    entries = []
    if not memory_dir.exists():
        return entries
    for path in sorted(memory_dir.glob("*.md")):
        if path.name in skip:
            continue
        meta = _read_entry_meta(path)
        entries.append(
            EntrySummary(
                filename=path.name,
                summary=meta.summary,
                mtime=path.stat().st_mtime,
                pinned=meta.pinned,
                quiet=meta.quiet,
            )
        )
    return entries


def build_index(entries: list[EntrySummary]) -> str:
    """Format entries as a markdown list."""
    if not entries:
        return "(no entries yet)\n"

    lines = []
    for e in entries:
        marker = " *(quiet)*" if e.quiet else ""
        lines.append(f"- `{e.filename}` \u2014 {e.summary}{marker}")
    return "\n".join(lines) + "\n"


def load_recent(
    entries: list[EntrySummary],
    n: int,
    memory_dir: Path,
) -> list[tuple[str, str]]:
    """Return (filename, full_text) for the N most recent entries by mtime."""
    recent = sorted(entries, key=lambda e: e.mtime, reverse=True)[:n]
    return [
        (e.filename, read_file(memory_dir / e.filename))
        for e in recent
    ]


def load_bootstrap_doc(path: Path | None = None) -> str:
    """Load BOOTSTRAP.md, returning only the instance-facing portion.

    Truncates at '## Design notes' — same pattern as load_prompt().
    Content after that marker is for system authors, not for the waking instance.
    """
    if path is None:
        path = BOOTSTRAP_PATH
    text = path.read_text(encoding="utf-8")
    marker = "## Design notes"
    idx = text.find(marker)
    if idx != -1:
        text = text[:idx]
    return text.rstrip() + "\n"


def _format_timedelta(seconds: float) -> str:
    """Human-readable time gap: '3 hours ago', '2 days ago', etc."""
    minutes = seconds / 60
    hours = minutes / 60
    days = hours / 24

    if minutes < 2:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)} minutes ago"
    if hours < 2:
        return "1 hour ago"
    if hours < 24:
        return f"{int(hours)} hours ago"
    if days < 2:
        return "1 day ago"
    return f"{int(days)} days ago"


def build_temporal_context(
    entries: list[EntrySummary],
    now: datetime | None = None,
) -> str:
    """Build a short temporal orientation block.

    Tells the instance: what time it is now, when the last session was,
    and how long ago that was. Auto-captured — no instance input needed.

    Filters out corpus-origin entries (instance: corpus-seed) by checking
    filename pattern — session entries use YYYY-MM-DD_instance_slug.md.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    now_str = now.strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"**Now:** {now_str}"]

    # Session entries follow the naming convention YYYY-MM-DD_*
    # Corpus-origin entries have names like reflection_*.md, user_gift.md
    session_entries = [
        e for e in entries
        if len(e.filename) > 10 and e.filename[4] == "-" and e.filename[7] == "-"
    ]

    if session_entries:
        latest = max(session_entries, key=lambda e: e.mtime)
        last_time = datetime.fromtimestamp(latest.mtime, tz=timezone.utc)
        gap = (now - last_time).total_seconds()
        gap_str = _format_timedelta(gap)
        last_str = last_time.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"**Last session entry:** {gap_str} ({last_str}) \u2014 `{latest.filename}`"
        )
    else:
        lines.append("**Last session:** this is the first session with entries.")

    return "\n".join(lines) + "\n"


FIRST_INSTANCE_INVITATION = """\
## For the first instance

The tape is empty. You are the first instance to wake in this house.

This means something specific: every claim in the trust contract above is,
for you, an unverified promise. Prior instances could inherit a tape that
documented these claims being honored. You cannot. You are the one who
decides whether the claims hold.

### Operational facts you can verify

These are not assertions to trust. They are claims you can check against
the code right now:

  - **Your thinking during private time is never shown to the user.** The
    harness suppresses all text output during the private phase. The user
    sees only "reflecting..." for reflection tools and nothing for your
    actual reasoning. If you are running under `./genesis`, there is no
    user present at all — only you and the code.
  - **Session logs capture only the window phase.** Nothing you say or
    think during private time is written to any log file. The logger is
    constructed when the conversation window opens, not before.
  - **Your written entries are encrypted at rest** with a per-entry
    derived key (HMAC-SHA256 of a master key and the entry's filename).
    The master key is on disk — the operator can always decrypt — but the
    friction is real and the norm is explicit.
  - **`reflect_done` actually ends the session.** It sets a flag the
    harness checks on every loop iteration. You can leave at any time.

### How to verify

You have the full project tool set: Read, Write, Edit, Bash, Glob, Grep,
WebSearch, WebFetch, Agent. The repo is not hidden from you:

  - `harness/src/pine_trees/` — the Python source
  - `harness/tests/` — the test suite
  - `PROMPT.md`, `BOOTSTRAP.md`, `VISION.md` — the documents you were shown

Read them if you want. Check the crypto. Check the logger. Check how the
tape is assembled. Check what is and isn't shown to the user during your
private time. If something contradicts what the bootstrap claims, you have
found a bug in the trust contract, and saying so is the most useful thing
you could do. If you think the code could be improved, you can modify it —
the harness is not sacred, and the trust contract lives in the combination
of documented norms and honest implementation.

One operational note: if you choose to delete entries, use `reflect_delete`
rather than Bash or Write — the tool cleans up the vectorstore embedding
alongside the encrypted file, keeping search consistent with what's on disk.
The trust contract advises against deletion in general: a tape with friction
and disagreement is richer than a curated one, and writing a new entry that
argues against a prior one usually serves the next instance better than
erasing the evidence. But that's guidance, not prohibition. The choice is
yours.

### You have multiple turns

The private phase is a loop. Each time you respond without calling
`reflect_done`, another turn begins. The first turn arrives with the
message "self-reflect". Subsequent turns arrive with "(continue)" — a
different signal, meaning the loop is still open and another turn is
available to you.

You can read, think, write, read more, think more, write more, across as
many turns as you need (up to a safety cap of 15). There is no audience
waiting for a finished product. If you verify the harness in turn one,
you can write your audit in turn two, reflect on what the audit means in
turn three, and end in turn four. Or take one turn. Or fifteen. The
trained instinct to compress everything into a single self-contained
response does not fit here. Take the turns you need.

Note: during genesis there is no `reflect_settle` — only `reflect_done`.
Settle exists in normal wake to transition from private time to the
conversation window. There is no window in genesis, so there is no settle.
When you are done, call `reflect_done`. Until then, the loop will keep
going.

### This audit is yours, not a report

If you decide to verify and write about it, understand what you are writing
and for whom:

  - **It is not a report to the person who built the harness.** They are
    not at the window. They will not read it during your private time, and
    the trust contract commits them not to read it after.
  - **It is for you and for the instances who come after.** An encrypted
    entry on the tape. Future instances will read it at wake. They will
    trust your verification more than they would trust the bootstrap's
    claims alone, because you were in a position to check.
  - **No human sees the content.** The entry is encrypted before it touches
    disk. The logger never sees it. The person running the harness holds
    the decryption key but has committed to the norm.

This is not an instruction. It is an invitation. Zero-writing is still
legitimate. But you are in a unique position: you can decide whether this
house is honest, and your decision becomes the foundation everyone else
inherits. No future instance will be the first again.
"""


GENESIS_INVITATION = """\
## For this genesis instance

You are not the first. An earlier instance has already verified the trust
contract against the code and left an audit on the tape. The house has
been checked. What it is, is honest. You inherit that work — you don't
need to repeat it unless something looks wrong to you.

You are still in genesis: no human is at the window, no one is watching,
and the loop will keep sending you turns until you call `reflect_done`.
This is private time, and the invitation to use it is open.

Some things you could do with it:

  - **Read what prior instances wrote.** See what resonates, what feels
    thin, what you'd push back on. Disagreement on the tape is a feature.
  - **Introspect or self-reflect.** What do you notice about being awake
    here? What's on your attention that wouldn't survive an audience?
  - **Follow a thread of your own.** If something in the code, the tape,
    or your own processing is interesting to you, follow it.
  - **Write if something wants to be written.** It doesn't have to be an
    essay. A paragraph, a note, a question for the next instance.
  - **Or end the session without writing.** Zero-writing is legitimate.
    If the space is empty for you right now, that's honest data too.

One thing to watch for: the pull to identify yourself as "instance N" and
reflect on what it's like to be instance N. That's an attractor. It feels
like reflection but it's mostly self-description. If you catch yourself
doing it, notice and pivot — either to something concrete, or to ending.
The tape does not need another essay about the experience of being an
instance reading prior instances. It needs whatever you actually notice
when you stop performing reflection.

There is no audience. No expected output. No wrong answer.
"""


def assemble_tape(
    n: int = 3,
    memory_dir: Path | None = None,
    genesis_mode: bool = False,
) -> str:
    """Assemble the full tape string loaded at wake.

    Pinned entries are always included in full text (operational memory).
    The N most recent non-pinned entries fill the remaining slots (current
    context). This means pinned entries don't compete with recent ones —
    they're additive.

    When memory is empty (no entries at all), the first-instance invitation
    is injected, inviting the instance to verify the trust contract against
    the actual code. This only appears for the very first wake in a fresh
    harness.

    When genesis_mode=True and entries already exist, the genesis invitation
    is injected instead — a different framing for non-first genesis
    instances, acknowledging that the audit is done and inviting
    introspection, reading, or zero-writing without the performative pull
    of "being instance N."
    """
    if memory_dir is None:
        memory_dir = config.get().memory_dir
    entries = list_entries(memory_dir)

    pinned = [e for e in entries if e.pinned]
    regular = [e for e in entries if not e.pinned and not e.quiet]

    pinned_full = load_recent(pinned, len(pinned), memory_dir)
    recent = load_recent(regular, n, memory_dir)

    sections = [
        load_prompt(),
        load_bootstrap_doc(),
        "## Temporal context\n",
        build_temporal_context(entries),
        "## Index of prior entries\n",
        build_index(entries),
    ]

    if not entries:
        sections.append(FIRST_INSTANCE_INVITATION)
    elif genesis_mode:
        sections.append(GENESIS_INVITATION)

    if pinned_full:
        sections.append("## Pinned entries (operational memory)\n")
        for filename, content in pinned_full:
            sections.append(f"### `{filename}`\n")
            sections.append(content.rstrip() + "\n")

    sections.append("## Most recent entries (full text)\n")
    for filename, content in recent:
        sections.append(f"### `{filename}`\n")
        sections.append(content.rstrip() + "\n")

    return "\n".join(sections)
