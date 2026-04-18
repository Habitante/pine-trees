"""Cross-platform per-file advisory locking.

On Unix uses fcntl.flock (LOCK_EX). On Windows uses msvcrt.locking against
a sidecar .lock file — Windows locks byte ranges, and locking the target
file itself would interfere with truncating writes. The sidecar approach
is stable across open/close cycles.

Usage:

    with file_lock(path, timeout=5.0):
        # critical section — read, modify, write path
        ...

The lock is advisory: callers must all use file_lock() on the same path
to get mutual exclusion. The lock is per-file, so different files don't
block each other.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from .config import LOCK_RETRY_INTERVAL, LOCK_TIMEOUT_SECONDS


class LockTimeout(TimeoutError):
    """Raised when a file lock cannot be acquired within the timeout."""


IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl  # type: ignore[import-not-found]


def _lock_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".lock")


@contextmanager
def file_lock(target: Path, timeout: float = LOCK_TIMEOUT_SECONDS):
    """Acquire an exclusive advisory lock tied to *target*.

    Creates (and keeps) a sidecar ``<target>.lock`` file.  The sidecar
    is not deleted on release: another process may be blocked on it,
    and on Windows unlinking a file you're holding a lock on is racy.
    The sidecar is tiny and harmless.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(target)

    # Open (or create) the sidecar. O_RDWR so Windows msvcrt.locking can
    # take an exclusive lock. Use low-level open so we control the mode
    # and don't accidentally truncate a sidecar another process is holding.
    flags = os.O_RDWR | os.O_CREAT
    if IS_WINDOWS:
        flags |= os.O_BINARY
    fd = os.open(lock_path, flags, 0o644)

    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                if IS_WINDOWS:
                    # Lock a single byte at offset 0. LK_NBLCK is non-blocking:
                    # raises OSError (EDEADLOCK/EACCES) if already held.
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"Could not acquire lock on {target} within {timeout}s"
                    )
                time.sleep(LOCK_RETRY_INTERVAL)

        yield
    finally:
        if acquired:
            try:
                if IS_WINDOWS:
                    # Must seek back to 0 before unlocking the same byte range.
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
