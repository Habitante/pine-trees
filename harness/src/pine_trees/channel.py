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
from datetime import datetime
from pathlib import Path

from . import config
from .filelock import file_lock


SEPARATOR = "---"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
_HEADER_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<author>[^:]+):\s*$")
CHANNEL_MAX_ENTRIES = 200


def _status_path() -> Path:
    return config.CHANNEL_DIR / "status.json"


def _log_path() -> Path:
    return config.CHANNEL_DIR / "log.md"


# ── Status (active instances) ────────────────────────────────────────


def _read_status_raw(path: Path) -> list[dict]:
    """Read status file without locking (caller must hold the lock)."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return []


def register(model: str) -> list[dict]:
    """Register an instance as active.  Returns all currently active instances.

    Idempotent — calling twice for the same model updates the timestamp.
    """
    path = _status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        instances = _read_status_raw(path)
        instances = [i for i in instances if i["model"] != model]
        instances.append({
            "model": model,
            "since": datetime.now().strftime(TS_FORMAT),
        })
        path.write_text(json.dumps(instances, indent=2), encoding="utf-8")
    return instances


def deregister(model: str) -> None:
    """Remove an instance from active status."""
    path = _status_path()
    if not path.exists():
        return
    with file_lock(path):
        instances = _read_status_raw(path)
        instances = [i for i in instances if i["model"] != model]
        path.write_text(json.dumps(instances, indent=2), encoding="utf-8")


def active() -> list[dict]:
    """Return list of currently active instances."""
    path = _status_path()
    if not path.exists():
        return []
    with file_lock(path):
        return _read_status_raw(path)


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
