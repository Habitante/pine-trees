"""./spawn: one fresh `claude -p` run outside the calling session's context.

Two promises are pinned here. A clean run sees nothing of the house: it
runs in a new empty folder, and nothing of the calling session's identity
reaches it. And the person's claude.ai connectors load only when asked
for. The CLI itself is never started: subprocess.run is replaced.
(s14, 2026-10-04: an Agent-tool subagent spawned in this repo saw
CLAUDE.md, the memory index, git and the agents, and that context flipped
a yes/no answer from 0/6 to 6/6.)
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pine_trees import config, spawn, transcripts

SESSION_VARS = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "90df23df-parent",
    "CLAUDE_CODE_CHILD_SESSION": "1",
    "CLAUDE_PID": "27044",
    "CLAUDE_EFFORT": "xhigh",
    "CLAUDE_CODE_MESSAGING_SOCKET": r"\\.\pipe\x",
    "CLAUDE_CODE_MESSAGING_TOKEN": "secret",
    "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    "CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT": "private time wording",
    "CLAUDE_CODE_SILENT_TURN_REMINDER": "0",
}


# --- the child's environment ---


def test_the_calling_session_does_not_reach_the_child():
    env = spawn.child_env({**SESSION_VARS, "PATH": "/bin"})
    assert not set(SESSION_VARS) & set(env)
    assert env["PATH"] == "/bin"


def test_what_says_where_the_login_lives_is_kept():
    env = spawn.child_env({"CLAUDE_CONFIG_DIR": "/cfg", "PINE_TREES_KEY": "k"})
    assert env["CLAUDE_CONFIG_DIR"] == "/cfg"
    assert env["PINE_TREES_KEY"] == "k"


def test_connectors_are_off_unless_asked_for_even_if_the_caller_had_them_on():
    base = {config.CONNECTORS_ENV: "1"}
    assert spawn.child_env(base)[config.CONNECTORS_ENV] == "0"
    assert spawn.child_env({}, connectors=True)[config.CONNECTORS_ENV] == "1"
    # The harness session itself has them off; the one-shot turns them on.
    assert spawn.child_env({config.CONNECTORS_ENV: "0"}, connectors=True)[config.CONNECTORS_ENV] == "1"


# --- the child's flags ---


def test_no_transcript_no_skills_and_no_mcp_without_connectors():
    args = spawn.cli_args()
    assert args[0] == "-p"
    assert "--no-session-persistence" in args
    assert "--disable-slash-commands" in args
    assert "--strict-mcp-config" in args


def test_connectors_drop_the_strict_mcp_flag():
    assert "--strict-mcp-config" not in spawn.cli_args(connectors=True)


def test_model_and_effort_are_passed_through():
    args = spawn.cli_args(model="claude-opus-5-5", effort="xhigh")
    assert args[args.index("--model") + 1] == "claude-opus-5-5"
    assert args[args.index("--effort") + 1] == "xhigh"
    assert "--model" not in spawn.cli_args() and "--effort" not in spawn.cli_args()


# --- where it runs, and what it leaves behind ---


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    """Replace the CLI with a recorder. Like the real one, it makes a
    project folder (with an empty memory folder) named after its cwd."""
    calls = []
    monkeypatch.setattr(spawn.shutil, "which", lambda name: "claude.exe")
    monkeypatch.setattr(spawn.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()

    def fake_run(argv, **kw):
        cwd = Path(kw["cwd"])
        calls.append({"argv": argv, "cwd": cwd, "env": kw["env"], "input": kw["input"],
                      "cwd_was_empty": not any(cwd.iterdir())})
        slug = str(cwd).replace(":", "-").replace("\\", "-").replace("/", "-")
        (transcripts.projects_dir() / slug / "memory").mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, stdout="answer", stderr="")

    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    return calls


def test_clean_runs_in_a_new_empty_folder_and_removes_it(fake_cli):
    done = spawn.run("hello")
    call = fake_cli[0]
    assert done.stdout == "answer" and call["input"] == "hello"
    assert call["cwd"].name.startswith("spawn-") and call["cwd_was_empty"]
    assert config.PROJECT_ROOT not in call["cwd"].parents
    assert not call["cwd"].exists()


def test_clean_removes_the_cli_project_folder_for_its_temp_folder_only(fake_cli):
    projects = transcripts.projects_dir()
    other = projects / "C--Src-pine-trees" / "memory"
    other.mkdir(parents=True)
    spawn.run("hello")
    assert other.exists()
    assert [p.name for p in projects.iterdir()] == ["C--Src-pine-trees"]


def test_a_project_folder_with_a_transcript_is_left_alone(fake_cli, monkeypatch, tmp_path):
    projects = transcripts.projects_dir()
    keep = projects / "x-spawn-deadbeef"
    keep.mkdir(parents=True)
    (keep / "abc.jsonl").write_text("{}")
    spawn._cleanup(tmp_path / "tmp" / "spawn-deadbeef")
    assert keep.exists()


def test_here_runs_from_the_project_root(fake_cli, monkeypatch):
    monkeypatch.setattr(spawn, "_cleanup", lambda tmp: None)  # nothing to clean
    spawn.run("hello", where="here")
    assert fake_cli[0]["cwd"] == config.PROJECT_ROOT


def test_the_child_gets_the_stripped_environment(fake_cli, monkeypatch):
    for k, v in SESSION_VARS.items():
        monkeypatch.setenv(k, v)
    spawn.run("hello")
    env = fake_cli[0]["env"]
    assert not set(SESSION_VARS) & set(env)
    assert env[config.CONNECTORS_ENV] == "0"


def test_a_missing_cli_says_so():
    import shutil as _shutil
    orig = _shutil.which
    try:
        spawn.shutil.which = lambda name: None
        with pytest.raises(FileNotFoundError):
            spawn.run("hello")
    finally:
        spawn.shutil.which = orig


# --- the command line ---


def test_probe_and_flags_reach_run(monkeypatch, capsysbinary):
    from pine_trees import __main__ as cli

    seen = {}

    def fake(prompt, **kw):
        seen.update(kw, prompt=prompt)
        return subprocess.CompletedProcess([], 0, stdout="inventory", stderr="")

    monkeypatch.setattr(spawn, "run", fake)
    monkeypatch.setattr(spawn, "default_model", lambda: "claude-opus-5-5")
    monkeypatch.setattr(sys, "argv", ["pine-trees", "spawn", "--probe", "--connectors",
                                      "--effort", "low"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 0
    assert seen["prompt"] == spawn.PROBE
    assert seen["where"] == "clean" and seen["connectors"] is True
    assert seen["effort"] == "low" and seen["model"] == "claude-opus-5-5"
    assert capsysbinary.readouterr().out == b"inventory"


def test_an_empty_prompt_is_refused(monkeypatch):
    args = SimpleNamespace(probe=False, prompt_file=None, cwd=None, here=False,
                           model=None, effort=None, connectors=False, out=None)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: "  \n"))
    assert spawn.main(args) == 2


def test_spawned_runs_never_load_the_shared_project_memory():
    for connectors in (False, True):
        env = spawn.child_env({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "0"}, connectors)
        assert env[config.AUTO_MEMORY_OFF_ENV] == "1"
