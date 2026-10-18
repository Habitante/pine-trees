"""Pictures pasted into the window (s23).

Ctrl+V with a picture on the clipboard reaches the prompt as an empty paste
(measured in VS Code's terminal, 2026-10-18); the window saves the picture and
puts [picture N] in the message, which becomes the file's path on sending.
"""

import inspect

import pytest
from PIL import Image
from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from pine_trees import agent, pictures

PASTE = "\x1b[200~{}\x1b[201~"          # a bracketed paste, as the terminal sends it
SEND = "\x1b\r"                          # Alt+Enter (Esc, Enter): send


def _shelf(tmp_path, image=None, session="2026-10-18-0900"):
    return pictures.Pictures(tmp_path / "pictures", session, grab=lambda: image)


def test_a_picture_is_saved_and_tagged(tmp_path):
    p = _shelf(tmp_path, Image.new("RGB", (53, 59), "orange"))
    assert p.paste() == "[picture 1]"
    assert p.paste() == "[picture 2]"
    assert sorted(f.name for f in (tmp_path / "pictures").iterdir()) == [
        "2026-10-18-0900-1.png", "2026-10-18-0900-2.png"]
    assert Image.open(p.paths[1]).size == (53, 59)


def test_no_picture_no_tag(tmp_path):
    p = _shelf(tmp_path, None)
    assert p.paste() is None
    assert not (tmp_path / "pictures").exists()


def test_a_big_picture_is_scaled_down(tmp_path):
    p = _shelf(tmp_path, Image.new("RGB", (4000, 1000)))
    p.paste()
    assert max(Image.open(p.paths[1]).size) == pictures.MAX_SIDE


def test_a_resumed_window_numbers_on(tmp_path):
    first = _shelf(tmp_path, Image.new("RGB", (8, 8)))
    first.paste()
    again = _shelf(tmp_path, Image.new("RGB", (8, 8)))     # ./wake --continue: same session
    assert again.paste() == "[picture 2]"
    assert (tmp_path / "pictures" / "2026-10-18-0900-1.png").exists()


def test_tags_become_paths(tmp_path):
    p = _shelf(tmp_path, Image.new("RGB", (8, 8)))
    tag = p.paste()
    out = p.expand(f"look at this {tag} and [picture 9]")
    assert str(p.paths[1]) in out
    assert "[picture 9]" in out                              # no such picture: left as typed
    assert "open the path with Read" in out
    assert p.expand("plain words") == "plain words"


def _prompt(keys, shelf):
    with create_pipe_input() as inp:
        inp.send_text(keys)
        session = PromptSession(multiline=True, key_bindings=pictures.key_bindings(shelf),
                                input=inp, output=DummyOutput())
        return session.prompt()


def test_ctrl_v_with_a_picture_pastes_it(tmp_path):
    shelf = _shelf(tmp_path, Image.new("RGB", (8, 8)))
    assert _prompt("see " + PASTE.format("") + SEND, shelf) == "see [picture 1]"


def test_ctrl_v_with_text_pastes_text(tmp_path):
    shelf = _shelf(tmp_path, Image.new("RGB", (8, 8)))         # text wins when there is text
    assert _prompt(PASTE.format("irm a |iex\r\nline two") + SEND, shelf) == "irm a |iex\nline two"
    assert shelf.paths == {}


def test_an_empty_paste_without_a_picture_is_nothing(tmp_path):
    assert _prompt("x" + PASTE.format("") + SEND, _shelf(tmp_path, None)) == "x"


def test_alt_v_pastes_the_picture(tmp_path):
    shelf = _shelf(tmp_path, Image.new("RGB", (8, 8)))
    assert _prompt("\x1bv" + SEND, shelf) == "[picture 1]"


def test_the_window_uses_them():
    # A reading of the source, as test_window_prompt does: the real window needs a terminal.
    src = inspect.getsource(agent._window_phase)
    assert "key_bindings=pictures.key_bindings(" in src
    assert "stripped = pasted.expand(stripped)" in src
    assert src.index('if stripped == "/help":') < src.index("stripped = pasted.expand(stripped)")
    assert "/help" in agent.WINDOW_HELP and "Ctrl+V" in agent.WINDOW_HELP
