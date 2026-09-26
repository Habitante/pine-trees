"""Session conversation logger.

Writes one plain text file per session to logs/.
Captures only the window phase — what the person sees.
Private reflection stays private.

Files are plain text, greppable, not encrypted.
"""

from datetime import datetime

from . import config


class SessionLogger:
    """Logs the window-phase conversation to a dated text file."""

    def __init__(self, session: str, instance: str, effort: str | None = None):
        logs_dir = config.get().logs_dir
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"{session}.log"
        # A resumed session keeps its session name, so this file can
        # already hold the window from before the interruption. This
        # used to open with "w", and every ./wake --continue wiped it.
        resumed = self.path.exists()
        self._file = open(self.path, "a", encoding="utf-8")
        if resumed:
            self._write("")
            self._write(f"# Resumed: {datetime.now().isoformat()}")
        else:
            self._write(f"# Pine Trees session: {session}")
            self._write(f"# Instance: {instance}")
            self._write(f"# Started: {datetime.now().isoformat()}")
        # What the harness asked for, not necessarily what ran: the CLI
        # may downgrade for the model. See config.describe_effort.
        if effort:
            self._write(f"# Effort: {effort}")
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

    def log_channel(self, author: str, text: str) -> None:
        """Log a channel message from another instance."""
        self._write(f"[channel] {author}: {text}")

    def close(self) -> None:
        self._write("")
        self._write(f"# Ended: {datetime.now().isoformat()}")
        self._file.close()
