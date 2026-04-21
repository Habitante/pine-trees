"""Session state persistence for --continue support.

Saves minimal harness state to a JSON sidecar so sessions can be
resumed after terminal crashes.  The CC binary handles conversation
history persistence natively; this module handles Pine Trees-specific
state (channel registration, phase, timing).

Session state files live in harness/sessions/{session}.json.
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
) -> Path:
    """Save harness session state to disk.

    Called once at settle (phase="window") so the next launch can
    resume if the terminal dies during conversation.
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
    }
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def load_latest() -> dict[str, Any] | None:
    """Load the most recent resumable session state.

    Scans session files in reverse chronological order and returns the
    first one whose phase is not "done".  Returns None if no resumable
    session exists.
    """
    if not SESSIONS_DIR.exists():
        return None
    files = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.name, reverse=True)
    for f in files:
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
            if state.get("phase") != "done":
                return state
        except (json.JSONDecodeError, OSError):
            continue
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


def mark_done(session: str) -> None:
    """Mark a session as cleanly finished.

    Sets phase to "done" so load_latest() skips it.
    """
    path = SESSIONS_DIR / f"{session}.json"
    if not path.exists():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        state["phase"] = "done"
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass
