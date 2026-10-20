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

from . import bootstrap, config
from .config import HARNESS_DIR
from .filelock import file_lock


SESSIONS_DIR = HARNESS_DIR / "sessions"

# Each house counts its sessions (models/<model>/session_count): the
# number Daniel calls an instance by, s20, s21... (s20, 2026-10-15:
# "I know there's a you, and I'm here with you").
COUNT_FILE = "session_count"


def claim_number() -> int:
    """Take this house's next session number, and save it as taken.

    Every fresh wake and every genesis session claims one; a resumed
    session keeps the number its sidecar holds. A house with no count yet
    starts from its own history, the distinct sessions its entries came
    from (this house's own numbering: genesis runs were s1-s3, the first
    window s4). From then on the count is kept, not recomputed, so a
    session that writes nothing still has its number.
    """
    path = config.get().model_dir / COUNT_FILE
    with file_lock(path):
        try:
            last = int(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            last = bootstrap.count_sessions()
        number = last + 1
        path.write_text(f"{number}\n", encoding="utf-8")
    return number


def save_state(
    session: str,
    instance: str,
    phase: str,
    channel_id: str | None = None,
    channel_cursor: datetime | None = None,
    started_at: datetime | None = None,
    cc_session_id: str | None = None,
    number: int | None = None,
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
        "number": number,
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


def parked(instance: str) -> list[dict[str, Any]]:
    """This house's sessions whose window was left open, newest first: they
    settled, never finished, and ``./wake --continue`` can still return to
    them. A plain ``./wake`` asks about the newest before starting someone
    new (Daniel, 2026-10-20)."""
    return [s for s in all_states()
            if s.get("phase") == "window" and s.get("instance") == instance]


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
