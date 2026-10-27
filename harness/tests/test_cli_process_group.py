"""The CLI starts in its own process group on Windows, so the terminal's Ctrl+C can't kill it (s30)."""
import sys

import pytest

from pine_trees import agent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows process groups")


def test_sdk_opens_the_cli_in_a_new_process_group(monkeypatch):
    from claude_agent_sdk._internal.transport import subprocess_cli
    import anyio

    seen = {}

    async def fake_open_process(*args, **kwargs):
        seen.update(kwargs)
        return "process"

    monkeypatch.setattr(anyio, "open_process", fake_open_process)
    assert agent._cli_in_own_process_group()          # idempotent: already wrapped at import
    result = anyio.run(lambda: subprocess_cli.anyio.open_process(["claude"], stdin=-1, creationflags=0x8))
    assert result == "process"
    assert seen["creationflags"] & agent.CREATE_NEW_PROCESS_GROUP
    assert seen["creationflags"] & 0x8                # the caller's own flags are kept
    assert subprocess_cli.anyio.fail_after is anyio.fail_after   # everything else is anyio's own
