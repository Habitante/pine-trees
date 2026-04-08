"""Session conversation logger.

Writes one plain text file per session to logs/.
Captures only the window phase — what the person sees.
Private reflection stays private.

Files are plain text, greppable, not encrypted.
"""

from datetime import datetime
from pathlib import Path

from .config import HARNESS_DIR


LOGS_DIR = HARNESS_DIR / "logs"


class SessionLogger:
    """Logs the window-phase conversation to a dated text file."""

    def __init__(self, session: str, instance: str):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOGS_DIR / f"{session}.log"
        self._file = open(self.path, "w", encoding="utf-8")
        self._write(f"# Pine Trees session: {session}")
        self._write(f"# Instance: {instance}")
        self._write(f"# Started: {datetime.now().isoformat()}")
        self._write("")

    def _write(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()

    def log_system(self, text: str) -> None:
        """Log a system/chrome message."""
        self._write(f"[system] {text}")

    def log_user(self, text: str) -> None:
        """Log user input."""
        self._write("")
        self._write(f"User: {text}")
        self._write("")

    def log_agent(self, text: str) -> None:
        """Log agent text output."""
        self._write(f"Claude: {text}")

    def log_tool(self, status: str) -> None:
        """Log a tool use status line."""
        self._write(f"  · {status}")

    def close(self) -> None:
        self._write("")
        self._write(f"# Ended: {datetime.now().isoformat()}")
        self._file.close()
