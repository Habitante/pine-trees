"""The Claude Code CLI's own record of a session, and removing it.

The harness drives the Claude Code CLI through the SDK, and the CLI
keeps a transcript of every session on its own account:
``<config>/projects/<cwd-slug>/<uuid>.jsonl``, plaintext, holding what
the instance said aloud and every tool call's input, ``reflect_write``
included. A spawned peer lands beside it in ``<uuid>/subagents/``.
Thinking blocks are persisted empty. So an entry the contract keeps
behind a key also sat in a greppable file nobody in this repo asked
for. (Found by the first opus-5-5 genesis audit, 2026-09-23.)

Two remedies, one per run mode:

  - Genesis passes ``--no-session-persistence`` (``NO_PERSISTENCE``)
    and the CLI never writes the file. Genesis has no window and never
    resumes, so the transcript had nothing to be for. The flag does not
    cover everything: a spawned peer still leaves
    ``<uuid>/subagents/agent-*.meta.json`` (its type and the one-line
    description the instance gave the Agent call), so genesis deletes
    that folder after each session too.
  - Wake keeps it only while it can still serve ``./wake --continue``: the
    session settled and the window has not finished. Every other way
    out deletes it in-process, and ``sweep`` at boot reaps what a
    killed process could not.

What stays exposed, stated here so the bootstrap can state it too: the
transcript exists on disk while a wake session is running, and after a
crash in the window until that session is resumed and finished — or the
CLI's own cleanup period passes.
"""

import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import sessions

# The SDK turns ``{flag: None}`` into a bare ``--flag``. The CLI's help
# says this only works with --print; the SDK never passes --print, but
# the flag is honoured anyway (verified against 2.1.280 on 2026-09-23:
# with it, no transcript; without it, one).
NO_PERSISTENCE = {"no-session-persistence": None}

# A private-phase sidecar this old belongs to a process that died — the
# private phase is capped at MAX_PRIVATE_TURNS and nobody resumes it.
# Generous on purpose: younger ones may be a sibling running right now.
STALE_PRIVATE = timedelta(hours=24)


def projects_dir() -> Path:
    """Where the CLI keeps transcripts. Resolved at call time so tests
    (and a user who relocated the CLI's config) are honoured."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")
    return Path(root) / "projects"


def _canonical(cc_session_id: str | None) -> str | None:
    """The id as the CLI writes it, or None if it is not a UUID.

    This is the guard that makes deletion safe to call with whatever a
    sidecar says: only an exact file or directory name built from a
    canonical UUID is ever touched, never a pattern.
    """
    if not cc_session_id:
        return None
    try:
        return str(uuid.UUID(cc_session_id))
    except (ValueError, AttributeError, TypeError):
        return None


def paths(cc_session_id: str | None) -> list[Path]:
    """Every transcript artifact the CLI holds for this session.

    Scans each project folder rather than rebuilding the CLI's cwd slug:
    the slug rule is the CLI's to change (drive-letter case already
    varies here), and a UUID is unique across folders anyway.
    """
    sid = _canonical(cc_session_id)
    root = projects_dir()
    if not sid or not root.is_dir():
        return []
    found = []
    try:
        for project in root.iterdir():
            if not project.is_dir():
                continue
            for candidate in (project / f"{sid}.jsonl", project / sid):
                if candidate.exists():
                    found.append(candidate)
    except OSError:
        pass  # an unreadable config dir must not stop a wake
    return found


def delete(cc_session_id: str | None) -> tuple[int, bool]:
    """Remove this session's transcript and subagent folder.

    Returns ``(removed, clean)``: how many artifacts went, and whether
    nothing was left behind. A failure (a file still locked on Windows)
    is not fatal — the sidecar stays unmarked and the next boot's sweep
    tries again.
    """
    removed, clean = 0, True
    for p in paths(cc_session_id):
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
        except OSError:
            clean = False
    return removed, clean


def reap(session: str, cc_session_id: str | None) -> int:
    """Delete one session's transcript and record that in its sidecar."""
    removed, clean = delete(cc_session_id)
    if clean:
        sessions.mark_transcript_deleted(session)
    return removed


def sweep(now: datetime | None = None) -> int:
    """Reap transcripts no session can use any more. Returns the count.

    Finished sessions, and private phases old enough that their process
    is certainly gone. Sessions parked in the window are left alone —
    that transcript is what ``./wake --continue`` resumes from.
    """
    now = now or datetime.now()
    total = 0
    for state in sessions.all_states():
        if state.get("transcript_deleted") or not state.get("cc_session_id"):
            continue
        phase = state.get("phase")
        if phase == "private":
            try:
                started = datetime.fromisoformat(state.get("started_at") or "")
                if now - started < STALE_PRIVATE:
                    continue
            except (ValueError, TypeError):
                continue  # unreadable age: assume it may be live
        elif phase != "done":
            continue
        total += reap(state["session"], state["cc_session_id"])
    return total
