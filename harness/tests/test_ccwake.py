"""Tests for cc-wake setup and the stale-tape cleanup.

``clear_tape`` is the guard against a cross-model context leak: the CLI
loads project CLAUDE files from cwd for every session, so a
CLAUDE.local.md written by ``./cc-wake <model-a>`` lands in the context
of the next ``./wake``/``./genesis`` of *any* model unless something
removes it. The harness calls it at boot on both paths.
"""

import dataclasses
import json

import pytest

from pine_trees import ccwake, config as pt_config


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(pt_config, "PROJECT_ROOT", root)
    monkeypatch.setattr(pt_config, "HARNESS_DIR", root / "harness")
    return root


def test_clear_tape_removes_leftover(project_root):
    tape = project_root / "CLAUDE.local.md"
    tape.write_text("# Claude Code wake (cc-wake mode)\n", encoding="utf-8")

    assert ccwake.clear_tape() is True
    assert not tape.exists()


def test_clear_tape_is_a_noop_when_absent(project_root):
    assert ccwake.clear_tape() is False


def test_clear_tape_leaves_the_mcp_config_alone(project_root):
    # A live cc-wake session in another terminal may still be pointed at
    # this file; only the auto-loaded tape is a context hazard.
    (project_root / "CLAUDE.local.md").write_text("x", encoding="utf-8")
    mcp = project_root / ".cc-mcp.json"
    mcp.write_text("{}", encoding="utf-8")

    ccwake.clear_tape()

    assert mcp.exists()


def test_clear_tape_does_not_touch_project_claude_md(project_root):
    # CLAUDE.md is checked in and belongs to the repo, not to a model.
    claude_md = project_root / "CLAUDE.md"
    claude_md.write_text("# Pine Trees\n", encoding="utf-8")

    ccwake.clear_tape()

    assert claude_md.exists()


def test_setup_refuses_a_model_without_genesis(project_root, monkeypatch, tmp_path):
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    empty = dataclasses.replace(pt_config.get(), memory_dir=tmp_path / "no-memory")
    monkeypatch.setattr(pt_config, "_config", empty)

    with pytest.raises(SystemExit, match="No memory found"):
        ccwake.setup("claude-opus-4-6")


def test_setup_writes_tape_and_mcp_config(project_root, monkeypatch, tmp_path):
    monkeypatch.setattr(ccwake.config, "init", lambda name: None)
    monkeypatch.setattr(ccwake.bootstrap, "assemble_tape", lambda n=3: "TAPE BODY")
    # memory_dir is tmp_path (see conftest); make it look post-genesis.
    (tmp_path / "some-entry.md").write_text("entry", encoding="utf-8")

    tape_path, mcp_path = ccwake.setup("claude-opus-4-6")

    tape = (project_root / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert tape.startswith("# Claude Code wake (cc-wake mode)")
    assert tape.endswith("TAPE BODY")
    assert tape_path == str(project_root / "CLAUDE.local.md")

    cfg = json.loads((project_root / ".cc-mcp.json").read_text(encoding="utf-8"))
    args = cfg["mcpServers"]["pine_trees"]["args"]
    assert args[-2:] == ["--model", "claude-opus-4-6"]
    assert mcp_path == str(project_root / ".cc-mcp.json")
