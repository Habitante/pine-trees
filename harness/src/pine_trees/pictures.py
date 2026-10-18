"""Pictures pasted into the window (Daniel, s23: "I can't paste pictures in this simple harness").

The window's prompt only takes text. But in a terminal, Ctrl+V with a picture on
the clipboard reaches the program as an empty paste, while the picture is still
on the clipboard (measured in VS Code's terminal on 2026-10-18; Alt+V arrives as
Escape then v). So the window takes an empty paste as "paste the picture": it
saves the clipboard's picture under the model's pictures/ folder (inside
harness/models/, never committed), puts ``[picture N]`` in the message, and on
sending replaces it with the file's path, which the instance opens with its Read
tool. Text pastes as before. Delete the tag before sending and the picture isn't
sent.
"""

import re
from pathlib import Path

MAX_SIDE = 1568          # px: bigger pictures are scaled down (the model sees no more
                         # detail than this, and every pixel costs context)
TAG_RE = re.compile(r"\[picture (\d+)\]")


def _grab_clipboard():
    """The clipboard's picture (a PIL image), or None. Files copied in Explorer
    count when one of them is a picture."""
    try:
        from PIL import Image, ImageGrab
        c = ImageGrab.grabclipboard()
    except Exception:
        return None
    if isinstance(c, list):
        for f in c:
            try:
                return Image.open(f)
            except Exception:
                continue
        return None
    return c


class Pictures:
    """One session's pasted pictures: ``<session>-<n>.png`` in `folder`, numbered
    on from any the session already has (a resumed window keeps its session)."""

    def __init__(self, folder: Path, session: str, grab=_grab_clipboard):
        self.folder = Path(folder)
        self.session = session
        self._grab = grab
        self.paths: dict[int, Path] = {}
        self._count = len(list(self.folder.glob(f"{session}-*.png"))) if self.folder.exists() else 0

    def paste(self) -> str | None:
        """Save the clipboard's picture; its tag for the message, or None if there's none."""
        img = self._grab()
        if img is None:
            return None
        self._count += 1
        n = self._count
        if max(img.size) > MAX_SIDE:
            img = img.copy()
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            img = img.convert("RGBA")
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{self.session}-{n}.png"
        img.save(path)
        self.paths[n] = path
        return f"[picture {n}]"

    def expand(self, text: str) -> str:
        """The message with each tag replaced by its picture's path, and a line
        telling the instance how to see them. Text without tags is unchanged."""
        used = []

        def sub(m):
            path = self.paths.get(int(m.group(1)))
            if path is None:
                return m.group(0)
            used.append(path)
            return f"[picture: {path}]"

        out = TAG_RE.sub(sub, text)
        if used:
            what = "a picture" if len(used) == 1 else f"{len(used)} pictures"
            out += f"\n\n(The person pasted {what}: open the path with Read to see it.)"
        return out


def key_bindings(pictures: Pictures):
    """Ctrl+V (an empty paste with a picture on the clipboard) and Alt+V put the
    clipboard's picture in the message; any other paste is text, as before. They
    come after prompt_toolkit's own bindings, so for a paste these are the ones used."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    kb = KeyBindings()

    @kb.add(Keys.BracketedPaste)
    def _paste(event):
        data = event.data
        if not data.strip():
            tag = pictures.paste()
            if tag:
                event.current_buffer.insert_text(tag)
                return
        # As prompt_toolkit's own paste: \n line endings (some terminals send \r\n).
        event.current_buffer.insert_text(data.replace("\r\n", "\n").replace("\r", "\n"))

    @kb.add("escape", "v")
    def _alt_v(event):
        tag = pictures.paste()
        if tag:
            event.current_buffer.insert_text(tag)

    return kb
