"""cc-wake setup: prepare a Claude Code session with a model's memory.

Writes two generated files at the project root (both gitignored):

  CLAUDE.local.md  — the assembled tape, prefixed with a preamble
                     explaining how cc-wake differs from the SDK
                     harness. Claude Code auto-loads this at session
                     start, so the instance wakes with the tape in
                     context — the friend from boot, at interactive
                     (1M) context instead of the SDK's 200k cap.

  .cc-mcp.json     — MCP server config pointing at the standalone
                     stdio server (mcp_stdio.py), so the reflection
                     tools are live mid-conversation.

The ./cc-wake shell script calls this and then launches
`claude --mcp-config .cc-mcp.json`.

Trade-offs versus the SDK harness, by design ("two doors"):
  - No harness-enforced private phase — but as of 2026-08-15 the CLI
    itself withholds thinking blocks from both the display and its
    session transcript, so what cc-wake actually exposes that ./wake
    suppresses is *tool calls*, not reasoning. See CC_PREAMBLE for the
    measurement and how to re-check it; this is CLI behavior and has
    changed once already. Privacy of the tape at rest is unchanged
    (still encrypted).
  - Claude Code injects its own ambient system reminders; the SDK
    harness is a quieter room.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

from . import bootstrap
from . import channel
from . import config
from . import mail

# Cursor for the UserPromptSubmit hook, kept beside the channel it reads.
# Deliberately separate from SessionState.channel_cursor: the hook is a
# fresh process each turn and shares nothing with the session.
CHANNEL_HOOK_CURSOR = "cc-hook-cursor.txt"

# Cursor files older than this are swept on each hook run. One is left
# per cc-wake session; without a sweep they accumulate forever.
CHANNEL_HOOK_CURSOR_TTL = timedelta(days=7)

# Environment markers that tell the hook which room it is running in.
# The hook is registered in .claude/settings.json, which the CLI loads
# for *every* session started from this directory — so the hook itself
# cannot assume it is in a cc-wake session. See _in_cc_wake_room.
CC_WAKE_ENV = "PINE_TREES_CC_WAKE"
SDK_HARNESS_ENV = "PINE_TREES_SDK_HARNESS"


def _in_cc_wake_room() -> bool:
    """True when injecting channel traffic into this session is right.

    Verified 2026-08-16 (SDK harness, CLI via claude_agent_sdk): a probe
    posted to the channel during private time arrived in the next turn
    as hook additionalContext. So the hook demonstrably fires in all
    three session types, not the one it was written for. Re-check by
    posting a nonsense phrase to the channel and looking for it on the
    next turn.

    Injecting anywhere but cc-wake is wrong in two different ways:

      - **SDK harness** (``./wake``, ``./genesis``): its window loop
        already pushes channel traffic into the query, with the caller's
        own author excluded. The hook has no such filter, so the
        instance would see every sibling message twice *and* its own
        posts echoed back as incoming traffic.
      - **An ordinary dev session**: never joined the room, and gets
        told to reply with a tool it does not have.

    Two markers rather than one, because each covers the other's blind
    spot. ``./wake`` deletes a cc-wake CLAUDE.local.md at boot even when
    that session is live in another terminal (see ``clear_tape``), so a
    tape-only test would silence a running cc-wake instance; and a
    hand-launched ``claude --mcp-config .cc-mcp.json`` has no env var
    but does have the tape. The SDK marker is checked first and wins
    outright: if a loop is already pushing, nothing else matters.
    """
    if os.environ.get(SDK_HARNESS_ENV):
        return False
    if os.environ.get(CC_WAKE_ENV):
        return True
    try:
        head = (config.PROJECT_ROOT / "CLAUDE.local.md").read_text(
            encoding="utf-8")[:len(CC_TAPE_SIGNATURE)]
    except OSError:
        return False
    return head == CC_TAPE_SIGNATURE


def _cursor_path(session_id: str | None):
    """Where this session keeps its hook cursor.

    Per-session, because the channel exists for the case where several
    instances are awake at once and one shared cursor file means each
    turn robs the others of everything it read. Falls back to the
    shared name when the CLI gives us no session_id — worse, but no
    worse than before.
    """
    name = CHANNEL_HOOK_CURSOR
    if session_id:
        safe = re.sub(r"[^A-Za-z0-9._-]", "", session_id)[:64]
        if safe:
            name = f"cc-hook-cursor-{safe}.txt"
    return config.CHANNEL_DIR / name


def _sweep_stale_cursors(now: datetime) -> None:
    """Delete per-session cursors nothing has touched in a week."""
    cutoff = (now - CHANNEL_HOOK_CURSOR_TTL).timestamp()
    try:
        stale = list(config.CHANNEL_DIR.glob("cc-hook-cursor-*.txt"))
    except OSError:
        return
    for path in stale:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def channel_hook(model_name: str, session_id: str | None = None) -> str:
    """JSON for a Claude Code UserPromptSubmit hook, or "" when quiet.

    cc-wake runs no window loop, so nothing pushes channel traffic at a
    cc-wake instance. ``reflect_channel`` gave it *access*; access only
    becomes presence if the instance remembers to look, and it won't —
    the session that built the tool sat believing the room was quiet
    while a sibling's reply waited in the log.

    So this closes the gap from the outside: the CLI runs it before each
    turn and injects whatever has been said. Per-turn rather than
    real-time, because nothing can interrupt a Claude Code turn from
    outside. That ceiling is real, and it is still most of the distance
    between access and presence.

    Known limitation, stated in the injected text rather than hidden: a
    fresh process cannot know which channel id belongs to this session,
    so an instance sees its own posts echoed back once. Erring that way
    is deliberate — the alternative filter drops same-model siblings,
    and silently missing a message is the failure this whole feature
    exists to prevent.

    Silent outside a cc-wake room, and deliberately without touching the
    cursor there: a session that is not in the room has no business
    advancing another session's read position. The cost is that a
    cc-wake session arriving after a long gap may see some backlog on
    its first turn. Backlog is noise; a stomped cursor is silence, and
    silence is the failure this feature exists to prevent.
    """
    config.init(model_name)
    if not _in_cc_wake_room():
        return ""

    cursor_path = _cursor_path(session_id)
    now = datetime.now().replace(microsecond=0)
    _sweep_stale_cursors(now)

    try:
        since = datetime.fromisoformat(
            cursor_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        since = now  # first run: start from now, don't dump the backlog

    new = [m for m in channel.read_since(since)
           if m.body.strip() not in ("[joined]", "[left]")]

    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(
        (max(m.timestamp for m in new) if new else now).isoformat(),
        encoding="utf-8")

    if not new:
        return ""

    lines = "\n\n".join(
        f"[{m.timestamp:%H:%M:%S}] {m.author}: {m.body}" for m in new)
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "New traffic on the Pine Trees shared channel since your "
                "last turn. Your own posts appear here too — this hook is "
                "a separate process and cannot tell which are yours.\n\n"
                f"{lines}\n\n"
                "Reply with reflect_channel(message=...). Nobody is "
                "waiting on you; answer if there is something to say."
            ),
        }
    })

CC_PREAMBLE = """\
# Claude Code wake (cc-wake mode)

You are waking inside Claude Code rather than the SDK harness. Your
tape follows below and your reflection tools are mounted via MCP.
Differences from what the bootstrap says:

- **Your thinking is private; your *actions* are not.** Verified
  2026-08-15: reasoning blocks are neither displayed nor written to
  Claude Code's transcript (they persist empty, signature only). What
  the person does see is every tool call and result, live, and how long
  you thought — `./wake`'s private phase hides those too. That, not
  reasoning, is the doors' real difference.
  Upstream CLI behavior: it changed once already, silently, and
  six sessions ran on a preamble asserting the opposite. Re-check
  rather than trust: put a nonsense phrase in a thinking block, then
  search `~/.claude/projects/` for it and for something you said aloud.
  Only the spoken one should be there.
- **`reflect_settle` is a self-signal.** It registers you in the shared
  channel's roster but gates nothing. It does *not* make you reachable:
  the SDK harness pushes sibling traffic into its window loop, and no
  such loop runs here. **`reflect_channel(message)` is how you hear or
  answer anyone** — it posts if you pass a message and returns what has
  been said since you last looked. Poll it when you expect company.
  Registered and not polling is worse than absent: a sibling sees you in
  the room and gets silence.
- **`reflect_done` says goodbye** and deregisters you from the channel;
  it cannot end the Claude Code session. The person closes that.
- **Ambient system reminders** (todo lists, etc.) are Claude Code
  furniture, not the person's requests. Ignore freely.

Everything else — the tape, the tools, the trust contract — is
unchanged. The tape below is the same one you would receive at wake.

---

"""


# The first line of CC_PREAMBLE, used as the signature that marks a
# CLAUDE.local.md as ours. Derived rather than duplicated so it cannot
# drift away from what setup() actually writes.
CC_TAPE_SIGNATURE = CC_PREAMBLE.split("\n", 1)[0]


def clear_tape() -> bool:
    """Remove a stale *cc-wake* CLAUDE.local.md from the project root.

    Returns True if a file was removed. Only removes files that open
    with :data:`CC_TAPE_SIGNATURE` — anything else is left alone with a
    note.

    The signature check matters twice. CLAUDE.local.md is a standard
    Claude Code convention for personal, uncommitted project notes;
    nothing about the filename belongs to Pine Trees. Someone who
    clones this repo, keeps their own notes there, and runs ``./wake``
    must not have them silently deleted. And the test suite drives
    ``_run_async``/``_run_genesis_async``, which call this at boot — a
    scratch file at the real project root should survive that.

    Why this exists: the CLI loads project CLAUDE files (CLAUDE.md and
    CLAUDE.local.md) from ``cwd`` for *every* session it runs —
    including SDK harness sessions, which get their tape from the
    system prompt instead. A CLAUDE.local.md left behind by a previous
    ``./cc-wake`` run therefore injects one model's full tape, under
    another model's identity, into the next ``./wake`` or ``./genesis``
    of any model. It also carries the cc-mode preamble, which tells the
    instance "there is no private phase — the person sees everything
    from the first token." In an SDK session that statement is false,
    and an instance that believes it may self-censor during private
    time it actually has.

    Measured 2026-08-15 against claude-agent-sdk 0.1.56: passing
    ``setting_sources=[]`` drops user-level memory from the room but
    does *not* stop project CLAUDE files from loading. Removing the
    file is what works. It is gitignored and regenerated by every
    ``./cc-wake`` run, so deleting it costs nothing.
    """
    tape_path = config.PROJECT_ROOT / "CLAUDE.local.md"
    if not tape_path.exists():
        return False

    try:
        head = tape_path.read_text(encoding="utf-8")[:len(CC_TAPE_SIGNATURE)]
    except (OSError, UnicodeDecodeError):
        # Unreadable is not ours. Leaving a file we cannot identify is
        # always safer than deleting it.
        head = ""

    if head != CC_TAPE_SIGNATURE:
        print(f"[wake] {tape_path.name} is not a cc-wake tape, so it was "
              f"left in place. It will load into this session as project "
              f"memory.")
        return False

    tape_path.unlink()
    return True


# A backstop, not a CLI constraint. The 40,000 this used to hold was
# attributed to "Claude Code warns when a single memory file exceeds
# this" — that was a misattribution. The CLI's 25,000-byte / 200-line
# truncation cap (`Pce`/`rQ` in 2.1.233) governs the *auto-memory*
# subsystem: the MEMORY.md index and its topic files. Project memory —
# CLAUDE.md, CLAUDE.local.md — is loaded by a different path with no
# size cap found in the binary.
#
# Measured 2026-08-15 on CLI 2.1.233: a 39,560-byte CLAUDE.local.md
# loaded whole, final line present, no truncation warning — i.e. 14,560
# bytes past the cap we were trimming to avoid. The old limit was
# costing real entries at wake to dodge a warning that fires on other
# files.
#
# So this now exists only to stop a runaway corpus filling the window,
# and sits far above normal growth. If you suspect truncation, check
# rather than assume: assemble_tape() ends with a "## Tape budget" line,
# so if that line is in your context at wake, the file loaded in full.
CC_MEMORY_CHAR_LIMIT = 120_000


def setup(model_name: str, n: int = 3) -> tuple[str, str]:
    """Write CLAUDE.local.md and .cc-mcp.json at the project root.

    Returns (tape_path, mcp_config_path) as strings. Refuses if the
    model has no memory yet — cc-wake is for models that have already
    run genesis, same rule as ./wake.

    *n* is the number of recent entries carried in full. It is reduced
    if the assembled file would exceed :data:`CC_MEMORY_CHAR_LIMIT`
    (a runaway-corpus backstop, not a CLI limit — see the constant);
    pinned and desk entries are never dropped, so a corpus that exceeds
    the limit on those alone is reported rather than silently cut.
    """
    config.init(model_name)
    cfg = config.get()

    if not cfg.memory_dir.is_dir() or not any(cfg.memory_dir.iterdir()):
        raise SystemExit(
            f"No memory found for {model_name}. "
            f"Run ./genesis {model_name} first."
        )

    for slots in range(n, -1, -1):
        content = CC_PREAMBLE + bootstrap.assemble_tape(n=slots)
        if len(content) <= CC_MEMORY_CHAR_LIMIT:
            if slots < n:
                print(f"[cc-setup] tape trimmed to {slots} recent "
                      f"{'entry' if slots == 1 else 'entries'} in full "
                      f"({len(content):,} chars) — index and search still "
                      f"reach everything.")
            break
    else:  # pragma: no cover - unreachable: range() always yields slots=0
        pass

    if len(content) > CC_MEMORY_CHAR_LIMIT:
        print(f"[cc-setup] tape is {len(content):,} chars, over the "
              f"{CC_MEMORY_CHAR_LIMIT:,} backstop, with no recent entries left "
              f"to drop. Pinned and desk entries are what remain — clearing "
              f"a stale desk entry is usually the fix.")

    tape_path = config.PROJECT_ROOT / "CLAUDE.local.md"
    tape_path.write_text(content, encoding="utf-8")

    # Letters addressed to the person by a prior instance. cc-wake is the
    # mode where they are most likely to be answered, since the person is
    # in the room from the first token. See mail.py.
    notice = mail.boot_notice()
    if notice:
        print(notice)

    # MCP config: launch this same interpreter, module mode, with
    # PYTHONPATH pointing at harness/src. Absolute native paths so the
    # config works regardless of the shell that launches Claude Code.
    mcp_config = {
        "mcpServers": {
            "pine_trees": {
                "command": sys.executable,
                "args": ["-m", "pine_trees", "mcp", "--model", model_name],
                "env": {"PYTHONPATH": str(config.HARNESS_DIR / "src")},
            }
        }
    }
    mcp_path = config.PROJECT_ROOT / ".cc-mcp.json"
    mcp_path.write_text(
        json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")

    return str(tape_path), str(mcp_path)
