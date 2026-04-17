"""Storage layer for Pine Trees.

Plain markdown files with YAML frontmatter. One file per reflection.
Hand-rolled YAML subset — schema is fixed (strings and lists of strings),
so we stay dependency-free (aside from cryptography for encryption).

All entries are encrypted at rest when a key is available.
"""

from datetime import datetime, timezone
from pathlib import Path

from . import config
from . import crypto


FRONTMATTER_DELIM = "---"


# --- Crypto-aware file I/O ---
# These are the only two functions that touch disk for entry content.
# Everything else (parse, format, read_entry, write_entry, bootstrap)
# goes through these.


def read_file(path: Path) -> str:
    """Read a file, decrypting if necessary.

    Tries per-entry derived key first (v2), falls back to master
    key (v1) for entries encrypted before key derivation was added.
    Plaintext files are read directly.
    Normalizes Windows line endings in both paths.
    """
    raw = path.read_bytes()
    if crypto.is_encrypted(raw):
        try:
            # v2: per-entry derived key
            entry_key = crypto.derive_key(path.name)
            text = crypto.decrypt(raw, entry_key)
        except Exception:
            # v1 fallback: master key (pre-derivation entries)
            text = crypto.decrypt(raw)
    else:
        text = raw.decode("utf-8")
    return text.replace("\r\n", "\n")


def _write_file(path: Path, text: str) -> None:
    """Write a file, encrypting with per-entry derived key if available."""
    key = crypto.get_key()
    if key:
        entry_key = crypto.derive_key(path.name, key)
        path.write_bytes(crypto.encrypt(text, entry_key))
    else:
        path.write_text(text, encoding="utf-8")


# --- Entry read/write ---


def write_entry(
    slug: str,
    content: str,
    instance: str,
    session: str,
    date: str,
    context: str,
    tags: list[str] | None = None,
    moves: list[str] | None = None,
    description: str = "",
    pinned: bool = False,
    quiet: bool = False,
    desk: bool = False,
) -> str:
    """Write a new entry. Returns the filename written.

    Filename convention: YYYY-MM-DD_instance_slug.md
    Encrypts at rest if a key is available.

    Flags:
      pinned: always in full text at wake (operational memory)
      quiet: indexed and searchable but excluded from tape's full-text slots
             (background knowledge — project summaries, reference material)
      desk:  full text at wake like pinned, but transient — meant to be
             cleared when the work moves on (handoffs, active sprint notes)
    """
    memory_dir = config.get().memory_dir
    memory_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{date}_{instance}_{slug}.md"
    path = memory_dir / filename

    frontmatter = _format_frontmatter(
        instance=instance,
        session=session,
        date=date,
        context=context,
        tags=tags or [],
        moves=moves or [],
        description=description,
        pinned=pinned,
        quiet=quiet,
        desk=desk,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    _write_file(path, frontmatter + content)
    return filename


def read_entry(filename: str) -> dict:
    """Read an entry. Returns flat dict with frontmatter fields + content.

    Resolves the memory directory from the current config so the same
    function serves any model whose session is active. Handles encrypted
    entries transparently.
    """
    path = config.get().memory_dir / filename
    text = read_file(path)
    return _parse_entry(text)


def edit_entry(
    filename: str,
    content: str | None = None,
    description: str | None = None,
    pinned: bool | None = None,
    quiet: bool | None = None,
    desk: bool | None = None,
) -> str:
    """Edit an existing entry. Returns the filename.

    All parameters except filename are optional — omit to preserve
    the current value. Pass content to update the body, description
    to update the summary, pinned/quiet/desk to toggle flags.

    Use for living reference entries (doc indices, project maps,
    trajectories, corpus entries). Reflections are moments — write
    corrections as new entries instead.
    """
    path = config.get().memory_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"No entry: {filename}")

    text = read_file(path)
    entry = _parse_entry(text)

    desc = description if description is not None else entry.get("description", "")
    pin = pinned if pinned is not None else entry.get("pinned", False)
    qut = quiet if quiet is not None else entry.get("quiet", False)
    dsk = desk if desk is not None else entry.get("desk", False)

    frontmatter = _format_frontmatter(
        instance=entry.get("instance", ""),
        session=entry.get("session", ""),
        date=entry.get("date", ""),
        context=entry.get("context", ""),
        tags=entry.get("tags", []),
        moves=entry.get("moves", []),
        description=desc,
        pinned=pin,
        quiet=qut,
        desk=dsk,
        timestamp=entry.get("timestamp", ""),
    )

    body = content if content is not None else entry.get("content", "")
    _write_file(path, frontmatter + body)
    return filename


def delete_entry(filename: str) -> None:
    """Delete an entry permanently. Raises ``FileNotFoundError`` if absent.

    The trust contract presents deletion as the instance's choice, not the
    harness's; this is the low-level hook the tool layer calls. Vectorstore
    cleanup is the caller's responsibility — storage only owns the file.
    """
    path = config.get().memory_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"No entry: {filename}")
    path.unlink()


# --- Frontmatter formatting/parsing ---


def _format_frontmatter(
    instance: str,
    session: str,
    date: str,
    context: str,
    tags: list[str],
    moves: list[str],
    description: str = "",
    pinned: bool = False,
    quiet: bool = False,
    desk: bool = False,
    timestamp: str = "",
) -> str:
    lines = [
        FRONTMATTER_DELIM,
        f"instance: {instance}",
        f"session: {session}",
        f"date: {date}",
        f"context: {context}",
        f"tags: [{', '.join(tags)}]",
        f"moves: [{', '.join(moves)}]",
    ]
    if timestamp:
        lines.append(f"timestamp: {timestamp}")
    if description:
        lines.append(f"description: {description}")
    if pinned:
        lines.append("pinned: true")
    if quiet:
        lines.append("quiet: true")
    if desk:
        lines.append("desk: true")
    lines.extend([FRONTMATTER_DELIM, ""])
    return "\n".join(lines) + "\n"


def _parse_entry(text: str) -> dict:
    lines = text.split("\n")
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        raise ValueError("missing opening frontmatter delimiter")

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("missing closing frontmatter delimiter")

    fm: dict = {}
    for line in lines[1:end_idx]:
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = _parse_value(value.strip())

    # Skip one blank line after closing delim if present
    content_start = end_idx + 1
    if content_start < len(lines) and lines[content_start] == "":
        content_start += 1
    fm["content"] = "\n".join(lines[content_start:])

    return fm


def _parse_value(s: str):
    """Parse a YAML value — string, list of strings, or boolean."""
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [x.strip() for x in inner.split(",")]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    return s
