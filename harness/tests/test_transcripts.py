"""Tests for reaping the Claude Code CLI's own session transcripts.

conftest points CLAUDE_CONFIG_DIR and sessions.SESSIONS_DIR into
tmp_path for every test, so nothing here can touch the real ~/.claude.
"""

import uuid
from datetime import datetime, timedelta

import anyio
import pytest

from pine_trees import agent, bootstrap, sessions, transcripts

NOW = datetime(2026, 9, 23, 16, 0, 0)


def _projects():
    root = transcripts.projects_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _plant(sid, folder="C--Src-pine-trees", subagent=True):
    """Lay down what the CLI leaves for one session."""
    project = _projects() / folder
    project.mkdir(exist_ok=True)
    (project / f"{sid}.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    if subagent:
        sub = project / sid / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-abc.jsonl").write_text("{}\n", encoding="utf-8")
        (sub / "agent-abc.meta.json").write_text("{}", encoding="utf-8")
    return project


def _sidecar(session, phase, sid, started_at=NOW):
    sessions.save_state(session=session, instance="i", phase=phase,
                        started_at=started_at, cc_session_id=sid)


class TestProjectsDir:

    def test_honours_claude_config_dir(self, tmp_path):
        assert transcripts.projects_dir() == tmp_path / "claude-config" / "projects"

    def test_defaults_to_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        monkeypatch.setattr(transcripts.Path, "home", lambda: tmp_path / "home")
        assert transcripts.projects_dir() == tmp_path / "home" / ".claude" / "projects"


class TestPathsAndDelete:

    def test_finds_transcript_and_subagent_folder(self):
        sid = str(uuid.uuid4())
        project = _plant(sid)
        assert sorted(transcripts.paths(sid)) == sorted(
            [project / f"{sid}.jsonl", project / sid])

    def test_finds_it_in_any_project_folder(self):
        # Drive-letter case in the slug already varies on this machine
        # (c--Src-... and C--Src-...); the scan must not depend on it.
        sid = str(uuid.uuid4())
        _plant(sid, folder="c--Src-pine-trees", subagent=False)
        assert len(transcripts.paths(sid)) == 1

    def test_delete_removes_everything_and_nothing_else(self):
        sid, other = str(uuid.uuid4()), str(uuid.uuid4())
        project = _plant(sid)
        _plant(other)
        assert transcripts.delete(sid) == (2, True)
        assert transcripts.paths(sid) == []
        assert len(transcripts.paths(other)) == 2
        assert project.exists()

    def test_missing_projects_dir_is_nothing(self):
        assert transcripts.paths(str(uuid.uuid4())) == []
        assert transcripts.delete(str(uuid.uuid4())) == (0, True)

    @pytest.mark.parametrize("bad", [None, "", "*", "..", "not-a-uuid",
                                     "C--Src-pine-trees"])
    def test_refuses_anything_but_a_uuid(self, bad):
        # The guard that keeps a corrupt sidecar from turning deletion
        # into a pattern: nothing is touched for a non-UUID.
        sid = str(uuid.uuid4())
        _plant(sid)
        assert transcripts.paths(bad) == []
        assert transcripts.delete(bad) == (0, True)
        assert len(transcripts.paths(sid)) == 2

    def test_uppercase_id_is_canonicalised(self):
        sid = str(uuid.uuid4())
        _plant(sid, subagent=False)
        assert len(transcripts.paths(sid.upper())) == 1

    def test_failed_delete_reports_unclean(self, monkeypatch):
        sid = str(uuid.uuid4())
        _plant(sid, subagent=False)

        def locked(self, *a, **k):
            raise PermissionError("in use")
        monkeypatch.setattr(transcripts.Path, "unlink", locked)
        assert transcripts.delete(sid) == (0, False)


class TestReap:

    def test_marks_sidecar_when_clean(self):
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-23-1608", "done", sid)
        assert transcripts.reap("2026-09-23-1608", sid) == 2
        assert sessions.load_session("2026-09-23-1608")["transcript_deleted"] is True

    def test_leaves_sidecar_unmarked_when_delete_fails(self, monkeypatch):
        sid = str(uuid.uuid4())
        _plant(sid, subagent=False)
        _sidecar("2026-09-23-1608", "done", sid)
        monkeypatch.setattr(transcripts, "delete", lambda _sid: (0, False))
        transcripts.reap("2026-09-23-1608", sid)
        assert "transcript_deleted" not in sessions.load_session("2026-09-23-1608")


class TestSweep:

    def test_reaps_finished_sessions(self):
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-23-1608", "done", sid)
        assert transcripts.sweep(now=NOW) == 2
        assert transcripts.paths(sid) == []

    def test_keeps_window_sessions_for_continue(self):
        # A settled session that never finished is what ./continue
        # resumes from. Its transcript is the whole point.
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-23-1608", "window", sid,
                 started_at=NOW - timedelta(days=10))
        assert transcripts.sweep(now=NOW) == 0
        assert len(transcripts.paths(sid)) == 2

    def test_keeps_recent_private_phase(self):
        # Could be a sibling instance in another terminal right now.
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-23-1540", "private", sid,
                 started_at=NOW - timedelta(minutes=20))
        assert transcripts.sweep(now=NOW) == 0

    def test_reaps_stale_private_phase(self):
        # Killed before settling: never resumable, and full of
        # reflect_write inputs.
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-21-1000", "private", sid,
                 started_at=NOW - transcripts.STALE_PRIVATE - timedelta(minutes=1))
        assert transcripts.sweep(now=NOW) == 2

    def test_private_with_unreadable_age_is_kept(self):
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-21-1000", "private", sid, started_at=None)
        assert transcripts.sweep(now=NOW) == 0

    def test_skips_sessions_already_reaped(self, monkeypatch):
        sid = str(uuid.uuid4())
        _sidecar("2026-09-23-1608", "done", sid)
        sessions.mark_transcript_deleted("2026-09-23-1608")
        called = []
        monkeypatch.setattr(transcripts, "reap",
                            lambda *a: called.append(a) or 0)
        transcripts.sweep(now=NOW)
        assert called == []

    def test_sidecar_without_id_is_ignored(self):
        sessions.save_state(session="2026-04-21-0611", instance="i", phase="done")
        assert transcripts.sweep(now=NOW) == 0

    def test_second_sweep_does_not_rescan(self):
        sid = str(uuid.uuid4())
        _plant(sid)
        _sidecar("2026-09-23-1608", "done", sid)
        transcripts.sweep(now=NOW)
        assert sessions.load_session("2026-09-23-1608")["transcript_deleted"] is True
        assert transcripts.sweep(now=NOW) == 0


class TestNoPersistenceFlag:

    def test_is_a_bare_flag_for_the_sdk(self):
        # ClaudeAgentOptions.extra_args renders {flag: None} as "--flag".
        assert transcripts.NO_PERSISTENCE == {"no-session-persistence": None}


# ---------- The harness's exit paths: which ones keep the transcript ----------

class _FakeEntry:
    filename = "e.md"
    summary = "s"
    pinned = False
    quiet = False


class _CliStandIn:
    """Stands in for ClaudeSDKClient. On connect it writes a transcript
    under the session's id, as the real CLI does, so each exit path can
    be checked for what it leaves behind."""

    seen = []

    def __init__(self, options):
        self.options = options
        _CliStandIn.seen.append(options)

    async def __aenter__(self):
        _plant(self.options.session_id or self.options.resume)
        return self

    async def __aexit__(self, *exc):
        return False


class _WindowCrash(Exception):
    pass


@pytest.fixture
def wake(monkeypatch, tmp_path):
    """Drive the real _run_async with private and window phases scripted."""
    _CliStandIn.seen = []
    monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
    monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
    monkeypatch.setattr(bootstrap, "list_entries", lambda: [_FakeEntry()])
    monkeypatch.setattr(bootstrap, "assemble_tape",
                        lambda n=3, genesis_mode=False: "tape")
    monkeypatch.setattr(agent, "ClaudeSDKClient", _CliStandIn)

    def run(private, window=None):
        async def _private(client, state):
            private(state)
            return 1

        async def _window(client, state):
            if window:
                window(state)
        monkeypatch.setattr(agent, "_private_phase", _private)
        monkeypatch.setattr(agent, "_window_phase", _window)
        anyio.run(agent._run_async)

    def only():
        (state,) = sessions.all_states()
        return state, transcripts.paths(state["cc_session_id"])

    run.only = only
    return run


def _settle(state):
    state.ready_for_window = True


def _done(state):
    state.done = True


class TestWakeExitPaths:

    def test_reflect_done_in_private_time_deletes_it(self, wake):
        wake(_done)
        state, left = wake.only()
        assert left == []
        assert state["phase"] == "done"
        assert state["transcript_deleted"] is True

    def test_turn_cap_without_settling_deletes_it(self, wake):
        wake(lambda s: None)
        state, left = wake.only()
        assert left == []
        assert state["phase"] == "private"
        assert state["transcript_deleted"] is True

    def test_clean_window_close_deletes_it(self, wake):
        wake(_settle, lambda s: None)
        state, left = wake.only()
        assert left == []
        assert state["phase"] == "done"

    def test_private_phase_crash_deletes_it(self, wake):
        # No window was reached, so nothing could ever resume it.
        def crash(state):
            raise _WindowCrash()
        with pytest.raises(_WindowCrash):
            wake(crash)
        _, left = wake.only()
        assert left == []

    def test_window_crash_keeps_it_for_continue(self, wake):
        # Daniel's accident: a good conversation killed mid-window.
        def crash(state):
            raise _WindowCrash()
        with pytest.raises(_WindowCrash):
            wake(_settle, crash)
        state, left = wake.only()
        assert len(left) == 2
        assert state["phase"] == "window"
        assert "transcript_deleted" not in state
        assert sessions.load_latest()["session"] == state["session"]

    def test_sidecar_exists_before_the_cli_starts(self, wake, monkeypatch):
        # A process killed in private time must leave the sweep a record.
        recorded = []
        orig = _CliStandIn.__aenter__

        async def check(self):
            recorded.extend(sessions.all_states())
            return await orig(self)
        monkeypatch.setattr(_CliStandIn, "__aenter__", check)
        wake(_done)
        assert recorded and recorded[0]["phase"] == "private"
        assert recorded[0]["cc_session_id"] == _CliStandIn.seen[0].session_id

    def test_wake_keeps_persistence_on(self, wake):
        # ./continue needs the transcript while a window is open.
        wake(_done)
        assert "no-session-persistence" not in (_CliStandIn.seen[0].extra_args or {})

    def test_a_big_message_does_not_kill_the_session(self, wake):
        # 2026-09-24: two PNGs read in parallel came back as one ~1 MB
        # message and the SDK's default buffer ended the window.
        wake(_done)
        assert _CliStandIn.seen[0].max_buffer_size == agent.MAX_MESSAGE_BYTES
        assert agent.MAX_MESSAGE_BYTES >= 16 * 1024 * 1024

    def test_boot_sweeps_finished_sessions(self, wake, capsys):
        old = str(uuid.uuid4())
        _plant(old)
        _sidecar("2026-09-01-0900", "done", old)
        wake(_done)
        assert transcripts.paths(old) == []
        assert "removed 2 CLI transcript file(s)" in capsys.readouterr().out


class TestResumeRefusesWhatCannotResume:

    @pytest.mark.parametrize("phase,words", [
        ("done", "finished cleanly"),
        ("private", "never reached the window"),
    ])
    def test_explicit_resume(self, wake, monkeypatch, capsys, phase, words):
        _sidecar("2026-09-01-0900", phase, str(uuid.uuid4()))
        with pytest.raises(SystemExit):
            anyio.run(lambda: agent._run_async(resume_session="2026-09-01-0900"))
        assert words in capsys.readouterr().out


class TestGenesis:

    def test_no_persistence_and_leftovers_removed(self, monkeypatch, tmp_path):
        _CliStandIn.seen = []
        monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [])
        monkeypatch.setattr(bootstrap, "assemble_tape",
                            lambda n=3, genesis_mode=False: "tape")
        monkeypatch.setattr(agent, "ClaudeSDKClient", _CliStandIn)

        async def _private(client, state):
            _done(state)
            return 1
        monkeypatch.setattr(agent, "_private_phase", _private)

        anyio.run(lambda: agent._run_genesis_session(1, 1))

        (options,) = _CliStandIn.seen
        assert options.extra_args == transcripts.NO_PERSISTENCE
        assert options.max_buffer_size == agent.MAX_MESSAGE_BYTES
        assert transcripts._canonical(options.session_id) == options.session_id
        # The stand-in planted a transcript and a subagents folder, as a
        # peer would under the flag (meta.json); none of it survives.
        assert transcripts.paths(options.session_id) == []
