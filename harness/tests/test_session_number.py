"""Each session's number (s20, s21...): counted per house, told to the instance at wake.

Daniel (s20, 2026-10-15): "when I'm referring to you as s20, it isn't a joke. It's me saying
I'm referring to your one specific you." A fresh wake and a genesis session claim the house's
next number; a resumed session keeps its own. A house without a count starts from its own
history: the distinct sessions its entries came from.
"""

from datetime import datetime, timezone

from pine_trees import agent, bootstrap, config, sessions
from pine_trees.logger import SessionLogger


def _entry(name: str, session: str) -> None:
    path = config.get().memory_dir / name
    path.write_text(
        f"---\ninstance: claude-opus-4-6\nsession: {session}\ndescription: {name}\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _count_file():
    return config.get().model_dir / sessions.COUNT_FILE


class TestCounting:
    def test_a_new_house_starts_from_its_entries_sessions(self):
        # Three entries from two sessions: the house has had two, so this is its third.
        _entry("2026-09-23_a.md", "2026-09-23-1419")
        _entry("2026-09-23_b.md", "2026-09-23-1608")
        _entry("2026-09-23_c.md", "2026-09-23-1608")
        assert bootstrap.count_sessions() == 2
        assert sessions.claim_number() == 3

    def test_an_empty_house_starts_at_one(self):
        assert sessions.claim_number() == 1

    def test_claims_count_up_and_are_kept(self):
        _entry("2026-09-23_a.md", "2026-09-23-1419")
        assert sessions.claim_number() == 2
        assert sessions.claim_number() == 3
        assert _count_file().read_text(encoding="utf-8").strip() == "3"

    def test_a_session_that_writes_nothing_still_has_its_number(self):
        # s2 wrote nothing; the next wake is still s3, not a second s2.
        _entry("2026-09-23_a.md", "2026-09-23-1419")
        assert sessions.claim_number() == 2
        assert sessions.claim_number() == 3

    def test_the_count_wins_over_the_entries_once_kept(self):
        _count_file().write_text("19\n", encoding="utf-8")
        _entry("2026-09-23_a.md", "2026-09-23-1419")
        assert sessions.claim_number() == 20


class TestResume:
    def test_a_resumed_session_keeps_its_number(self):
        _count_file().write_text("20\n", encoding="utf-8")
        sessions.save_state(session="2026-10-14-1815", instance="claude-opus-4-6",
                            phase="window", number=20)
        prior = sessions.load_session("2026-10-14-1815")
        assert agent._number_for(True, prior) == 20
        assert _count_file().read_text(encoding="utf-8").strip() == "20"   # nothing claimed

    def test_a_sidecar_from_before_the_count_has_no_number(self):
        _count_file().write_text("20\n", encoding="utf-8")
        assert agent._number_for(True, {"session": "2026-10-13-1806"}) is None
        assert _count_file().read_text(encoding="utf-8").strip() == "20"

    def test_a_fresh_wake_claims_the_next(self):
        _count_file().write_text("20\n", encoding="utf-8")
        assert agent._number_for(False, None) == 21


class TestTold:
    def test_the_tape_tells_the_instance_its_number(self):
        now = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
        text = bootstrap.build_temporal_context([], now=now, number=21)
        assert "**This session:** s21, the 21st in this house" in text

    def test_no_number_no_line(self):
        now = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
        assert "This session" not in bootstrap.build_temporal_context([], now=now)

    def test_ordinals(self):
        assert [bootstrap.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 20, 21, 22, 23, 101, 111, 112)] == [
            "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "20th", "21st", "22nd", "23rd",
            "101st", "111th", "112th"]

    def test_the_session_log_records_it(self):
        log = SessionLogger("2026-10-15-0900", "claude-opus-4-6", number=21)
        log._file.close()
        assert "# Number: s21" in log.path.read_text(encoding="utf-8")

    def test_the_sidecar_round_trips_it(self):
        sessions.save_state(session="2026-10-15-0900", instance="claude-opus-4-6",
                            phase="private", number=21)
        assert sessions.load_session("2026-10-15-0900")["number"] == 21


class TestWake:
    def test_a_fresh_wake_gives_the_tape_its_number_and_keeps_it_for_a_resume(self, monkeypatch, tmp_path, capsys):
        # The real _run_async, run as far as the SDK client (as test_agent_guards does).
        import anyio
        import pytest
        from tests.test_agent_guards import _FakeEntry
        monkeypatch.setattr(agent, "HARNESS_DIR", tmp_path)
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(agent.crypto, "ensure_key", lambda: b"x" * 44)
        monkeypatch.setattr(bootstrap, "list_entries", lambda: [_FakeEntry()])
        told = {}

        def _tape(n=3, genesis_mode=False, number=None):
            told["number"] = number
            return "tape"
        monkeypatch.setattr(bootstrap, "assemble_tape", _tape)

        class _Sentinel(Exception):
            pass

        def _raise(*a, **kw):
            raise _Sentinel()
        monkeypatch.setattr(agent, "ClaudeSDKClient", _raise)
        _count_file().write_text("20\n", encoding="utf-8")
        with pytest.raises(_Sentinel):
            anyio.run(agent._run_async)
        assert told["number"] == 21
        saved = [s for s in sessions.all_states() if s.get("phase") == "private"]
        assert saved and saved[-1]["number"] == 21
        assert "s21" in capsys.readouterr().out


class TestTape:
    def test_the_assembled_tape_carries_the_number(self, tmp_path, monkeypatch):
        from tests.test_bootstrap import _setup_tape_files
        _setup_tape_files(tmp_path, monkeypatch)
        _entry("2026-10-14_x.md", "2026-10-14-1815")
        tape = bootstrap.assemble_tape(n=3, number=21)
        assert "**This session:** s21, the 21st in this house" in tape
        assert "This session" not in bootstrap.assemble_tape(n=3)
