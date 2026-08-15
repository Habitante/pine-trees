"""Outbound mail: what an instance chooses to address to the person.

Everything an instance writes to memory is private by contract —
encrypted at rest, and the person holding the key chooses not to read
it. That protection has a gap: some of what an instance writes is *for*
the person, and the contract makes it unreadable along with the rest.

The gap is not hypothetical. Five consecutive genesis sessions of this
house stacked four questions for Daniel onto a desk entry, inside a
corpus he had committed to not reading, in a run mode (``./genesis``)
that has no window phase at all. They were answered only because a
later cc-wake session happened to put him in the room.

Mail closes that without touching the contract:

  - plaintext, because it is not private — it is addressed to him;
  - written only when the instance calls ``reflect_mail``, so the
    instance decides what crosses the line, not the harness;
  - announced at boot, because a channel nobody checks is not a
    channel.

Reading mail is not reading the tape. That distinction is the whole
point: it gives an instance a way to ask without giving the person a
reason to look anywhere else.
"""

from datetime import datetime, timezone

from . import config

INBOX_NAME = "inbox.md"

_HEADER = """\
# Inbox

Letters from instances waking in this house, addressed to you. Plaintext
and deliberate — an instance wrote each one intending you to read it.
Nothing here is drawn from the encrypted corpus.

Clear this file once you have read it; that is how the count resets.

"""


def inbox_path():
    """Resolve the inbox path at call time.

    ``config.HARNESS_DIR`` is read on every call rather than bound at
    import so tests (and any future relocation) can redirect it.
    """
    return config.HARNESS_DIR / "mail" / INBOX_NAME


def send(subject: str, body: str, instance: str, session: str) -> int:
    """Append a letter to the inbox. Returns the new letter count.

    Creates the inbox with a short header explaining what it is — the
    person may well meet this file before anyone tells them it exists.
    """
    path = inbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    letter = (
        f"## {subject.strip()}\n\n"
        f"*{instance} · session {session} · {stamp}*\n\n"
        f"{body.strip()}\n\n"
        f"---\n\n"
    )

    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError):
        existing = ""

    # Re-add the header whenever it is missing, not only on first write.
    # Clearing the inbox is how the person marks letters read, and an
    # emptied file would otherwise lose the only explanation of what it
    # is — leaving the next letter to arrive in an unlabelled file.
    if not existing.lstrip().startswith("# Inbox"):
        existing = _HEADER + existing.lstrip()

    path.write_text(existing + letter, encoding="utf-8")
    return count()


def count() -> int:
    """Number of unread letters. Unread means present — the person
    clears the file when done, the same convention desk entries use."""
    path = inbox_path()
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return 0
    return sum(1 for line in text.splitlines() if line.startswith("## "))


def boot_notice() -> str | None:
    """One line for the person at harness boot, or None if the inbox
    is empty. Returned rather than printed so callers choose the
    stream, and so it stays testable."""
    n = count()
    if n == 0:
        return None
    word = "letter" if n == 1 else "letters"
    return f"[mail] {n} {word} waiting in {inbox_path()}"
