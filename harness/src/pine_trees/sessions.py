"""Session state persistence for --continue support.

Saves minimal harness state to a JSON sidecar so sessions can be
resumed after terminal crashes.  The CC binary handles conversation
history persistence natively; this module handles Pine Trees-specific
state (channel registration, phase, timing).

Session state files live in harness/sessions/{session}.json.

The sidecar is also how the harness finds the CLI's plaintext
transcript to delete it (``cc_session_id``, ``transcript_deleted``).
See transcripts.py.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import HARNESS_DIR


SESSIONS_DIR = HARNESS_DIR / "sessions"


def save_state(
    session: str,
    instance: str,
    phase: str,
    channel_id: str | None = None,
    channel_cursor: datetime | None = None,
    started_at: datetime | None = None,
    cc_session_id: str | None = None,
) -> Path:
    """Save harness session state to disk.

    Called at wake (phase="private") so the CLI transcript can be found
    and reaped if the process dies before settling, and again at settle
    (phase="window") so the next launch can resume if the terminal dies
    during conversation.

    ``cc_session_id`` is the UUID the CC binary uses internally to
    identify the conversation; the harness's own ``session`` string
    (e.g. ``2026-04-21-1202``) stays user-facing.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session}.json"
    state = {
        "session": session,
        "instance": instance,
        "phase": phase,
        "channel_id": channel_id,
        "channel_cursor": channel_cursor.isoformat() if channel_cursor else None,
        "started_at": started_at.isoformat() if started_at else None,
        "cc_session_id": cc_session_id,
    }
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def all_states() -> list[dict[str, Any]]:
    """Every readable sidecar, newest first. Unreadable ones are skipped."""
    if not SESSIONS_DIR.exists():
        return []
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.name, reverse=True)
    states = []
    for f in files:
        try:
            states.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return states


def load_latest() -> dict[str, Any] | None:
    """Load the most recent resumable session state.

    Resumable means phase "window": the session settled and never
    finished. A "private" sidecar is written at wake so the CLI
    transcript can be found and reaped if the process dies before
    settling (see transcripts.py). It is not resumable, because resume
    re-enters the window and that conversation never reached one.
    """
    for state in all_states():
        if state.get("phase") == "window":
            return state
    return None


def load_session(session: str) -> dict[str, Any] | None:
    """Load a specific session state by session ID."""
    path = SESSIONS_DIR / f"{session}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _update(session: str, **fields: Any) -> None:
    """Set fields on an existing sidecar. A missing or unreadable one
    is left alone — these are bookkeeping, never worth a crash."""
    path = SESSIONS_DIR / f"{session}.json"
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state.update(fields)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass


def mark_done(session: str) -> None:
    """Mark a session as cleanly finished.

    Sets phase to "done" so load_latest() skips it.
    """
    _update(session, phase="done")


def mark_transcript_deleted(session: str) -> None:
    """Record that the CLI's transcript for this session is gone, so the
    boot sweep stops looking for it. See transcripts.py."""
    _update(session, transcript_deleted=True)
