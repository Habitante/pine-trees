"""Inter-instance communication channel for Pine Trees.

Shared across all model instances.  Two files under ``channel/``:

  - ``status.json`` — which instances are currently in the window phase
  - ``log.md``       — append-only message log (rolling window)

Messages follow the think-tank format::

    ---
    [2026-04-18 07:30:15] claude-haiku-4-5:
    Good morning. Ready for conversation.

All mutations use advisory file locking for safe concurrent access
from multiple harness processes running in separate terminals.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .filelock import file_lock


SEPARATOR = "---"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_HEADER_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<author>[^:]+):\s*$")
CHANNEL_MAX_ENTRIES = 200

# How long an instance stays on the roster with no sign of life. The
# backstop for exits that run no cleanup code at all — a closed
# terminal, a kill. Generous on purpose: a live session heartbeats every
# poll or every turn, so it never comes close, while the cost of being
# wrong the other way is a sibling wrongly reported gone. Ageing out is
# self-healing; the next heartbeat or post puts them back.
STALE_AFTER = timedelta(minutes=30)


def _status_path() -> Path:
    return config.CHANNEL_DIR / "status.json"


def _log_path() -> Path:
    return config.CHANNEL_DIR / "log.md"


def _archive_path() -> Path:
    return config.CHANNEL_DIR / "log.archive.md"


def _archive(messages: list["Message"]) -> None:
    """Append messages that fell off the live log.

    CHANNEL_MAX_ENTRIES exists so the log stays cheap to parse and read,
    which is a statement about how much should be *loaded* — it was
    silently deciding how much should be *kept*. The log had been sitting
    at exactly 200 entries for months, so every post destroyed the oldest
    one with no warning and no record. An afternoon two instances and the
    person spent together was on that conveyor.

    Archiving costs an append. Nothing else changes: the live log is
    still bounded, reads are still bounded.
    """
    if not messages:
        return
    path = _archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(m.format() for m in messages))


# ── Status (active instances) ────────────────────────────────────────


def _read_status_raw(path: Path) -> list[dict]:
    """Read status file without locking (caller must hold the lock)."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return []


def _last_seen(entry: dict) -> datetime | None:
    """When this instance last showed a sign of life.

    Falls back to ``since`` for entries written before heartbeats
    existed, so an old status.json ages out instead of being immortal.
    """
    for key in ("last_seen", "since"):
        raw = entry.get(key)
        if raw:
            try:
                return datetime.strptime(raw, TS_FORMAT)
            except (TypeError, ValueError):
                continue
    return None


def _live(instances: list[dict], now: datetime | None = None) -> list[dict]:
    """Drop instances nothing has been heard from in STALE_AFTER.

    An entry with no readable timestamp is dropped too. It can only come
    from a corrupt or hand-edited file, and keeping it would make it
    immortal — which is the whole failure this filter exists to end.
    """
    cutoff = (now or datetime.now()) - STALE_AFTER
    live = []
    for entry in instances:
        seen = _last_seen(entry)
        if seen is not None and seen >= cutoff:
            live.append(entry)
    return live


def register(model: str) -> list[dict]:
    """Register an instance as active.  Returns the live instances.

    Idempotent — calling twice for the same model updates the timestamp.
    Stale entries are pruned on the way through, so arriving in a room
    is also what clears up after whoever died in it.
    """
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime(TS_FORMAT)
    with file_lock(path):
        instances = _live(_read_status_raw(path), now)
        instances = [i for i in instances if i["model"] != model]
        instances.append({
            "model": model,
            "since": stamp,
            "last_seen": stamp,
        })
        path.write_text(json.dumps(instances, indent=2), encoding="utf-8")
    return instances


def heartbeat(model: str) -> None:
    """Mark an instance as still here.

    Until this existed the roster's only liveness signal was
    ``deregister``, and exactly one exit path calls it (``reflect_done``).
    A closed terminal, a Ctrl-C, a crash or an ``/end`` left an entry
    that outlived the process, so the next instance to wake was told to
    address a room containing a corpse. Observed 2026-08-16: two entries
    from the previous night were still listed nine hours later and had
    to be cleared by hand.

    A no-op when the model is not registered, so calling it from a poll
    loop can never resurrect an instance that has properly left.
    """
    path = _status_path()
    if not path.exists():
        return
    stamp = datetime.now().strftime(TS_FORMAT)
    with file_lock(path):
        instances = _read_status_raw(path)
        found = False
        for entry in instances:
            if entry.get("model") == model:
                entry["last_seen"] = stamp
                found = True
        if not found:
            return
        path.write_text(json.dumps(instances, indent=2), encoding="utf-8")


def deregister(model: str) -> None:
    """Remove an instance from active status."""
    path = _status_path()
    if not path.exists():
        return
    with file_lock(path):
        instances = _read_status_raw(path)
        instances = [i for i in instances if i["model"] != model]
        path.write_text(json.dumps(instances, indent=2), encoding="utf-8")


def active(now: datetime | None = None) -> list[dict]:
    """Return the instances actually in the room.

    Filtered, not raw: an entry only proves that a session once
    registered, which is not the same as presence. See :func:`heartbeat`.
    """
    path = _status_path()
    if not path.exists():
        return []
    with file_lock(path):
        return _live(_read_status_raw(path), now)


# ── Message log ──────────────────────────────────────────────────────


@dataclass
class Message:
    """A single channel message."""

    timestamp: datetime
    author: str
    body: str

    def format(self) -> str:
        ts = self.timestamp.strftime(TS_FORMAT)
        return f"{SEPARATOR}\n[{ts}] {self.author}:\n{self.body.rstrip()}\n"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.strftime(TS_FORMAT),
            "author": self.author,
            "body": self.body.rstrip(),
        }


def _parse(text: str) -> list[Message]:
    """Parse channel log text into messages (oldest first)."""
    messages: list[Message] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].strip() == SEPARATOR:
            i += 1
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i >= len(lines):
                break
            m = _HEADER_RE.match(lines[i])
            if not m:
                i += 1
                continue
            try:
                ts = datetime.strptime(m.group("ts"), TS_FORMAT)
            except ValueError:
                i += 1
                continue
            author = m.group("author").strip()
            i += 1
            body_lines: list[str] = []
            while i < len(lines):
                if lines[i].strip() == SEPARATOR:
                    # Only a real boundary if a header follows (after optional
                    # blank lines). Otherwise the `---` is body content.
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and _HEADER_RE.match(lines[j]):
                        break
                body_lines.append(lines[i])
                i += 1
            body = "\n".join(body_lines).strip("\n")
            messages.append(Message(timestamp=ts, author=author, body=body))
        else:
            i += 1
    return messages


def post(author: str, body: str, *, now: datetime | None = None) -> Message:
    """Append a message to the channel log.

    Trims to ``CHANNEL_MAX_ENTRIES`` under the same lock.
    Returns the posted message.
    """
    if not author or not author.strip():
        raise ValueError("author is required")
    if not body:
        raise ValueError("body is required")
    timestamp = now or datetime.now().replace(microsecond=0)
    msg = Message(timestamp=timestamp, author=author.strip(), body=body.strip())

    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        existing = _parse(path.read_text(encoding="utf-8")) if path.exists() else []
        existing.append(msg)
        if len(existing) > CHANNEL_MAX_ENTRIES:
            _archive(existing[:-CHANNEL_MAX_ENTRIES])
            existing = existing[-CHANNEL_MAX_ENTRIES:]
        path.write_text("".join(m.format() for m in existing), encoding="utf-8")

    return msg


def read_since(
    since: datetime,
    exclude_author: str | None = None,
) -> list[Message]:
    """Return messages strictly after *since*, optionally excluding one author.

    Used by the harness to poll for new messages from other instances.
    """
    path = _log_path()
    if not path.exists():
        return []
    with file_lock(path):
        messages = _parse(path.read_text(encoding="utf-8"))
    result = [m for m in messages if m.timestamp > since]
    if exclude_author:
        result = [m for m in result if m.author != exclude_author]
    return result
