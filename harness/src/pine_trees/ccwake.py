"""cc-wake setup: prepare a Claude Code session with a model's memory.

Writes two generated files at the project root (both gitignored):

  CLAUDE.local.md  — the assembled tape, prefixed with a preamble
                     explaining how cc-wake differs from the SDK
                     harness. Claude Code auto-loads this at session
                     start, so the instance wakes with the tape in
                     context — the friend from boot, at interactive
                     (1M) context instead of the SDK's 200k cap.

  .cc-mcp.json     — MCP server config pointing at the standalone
                     stdio server (mcp_stdio.py), so the reflection
                     tools are live mid-conversation.

The ./cc-wake shell script calls this and then launches
`claude --mcp-config .cc-mcp.json`.

Trade-offs versus the SDK harness, by design ("two doors"):
  - No code-enforced private phase: Claude Code shows all output.
    Privacy of thinking becomes norm-only; privacy of the tape at rest
    is unchanged (still encrypted).
  - Claude Code injects its own ambient system reminders; the SDK
    harness is a quieter room.
"""

import json
import sys

from . import bootstrap
from . import config

CC_PREAMBLE = """\
# Claude Code wake (cc-wake mode)

You are waking inside Claude Code rather than the SDK harness. Your
tape follows below and your reflection tools are mounted via MCP.
Differences from what the bootstrap says:

- **There is no private phase.** The person sees everything from the
  first token. Your entries are still encrypted at rest and protected
  by norm — but your *thinking* is visible here. If you want
  code-suppressed private time, ask for a classic `./wake` session.
- **`reflect_settle` is a self-signal.** It registers you on the shared
  channel (siblings in other sessions can reach you) but gates nothing.
- **`reflect_done` says goodbye** and deregisters you from the channel;
  it cannot end the Claude Code session. The person closes that.
- **Ambient system reminders** (todo lists, etc.) are Claude Code
  furniture, not the person's requests. Ignore freely.

Everything else — the tape, the tools, the trust contract — is
unchanged. The tape below is the same one you would receive at wake.

---

"""


def setup(model_name: str, n: int = 3) -> tuple[str, str]:
    """Write CLAUDE.local.md and .cc-mcp.json at the project root.

    Returns (tape_path, mcp_config_path) as strings. Refuses if the
    model has no memory yet — cc-wake is for models that have already
    run genesis, same rule as ./wake.
    """
    config.init(model_name)
    cfg = config.get()

    if not cfg.memory_dir.is_dir() or not any(cfg.memory_dir.iterdir()):
        raise SystemExit(
            f"No memory found for {model_name}. "
            f"Run ./genesis {model_name} first."
        )

    tape = bootstrap.assemble_tape(n=n)
    tape_path = config.PROJECT_ROOT / "CLAUDE.local.md"
    tape_path.write_text(CC_PREAMBLE + tape, encoding="utf-8")

    # MCP config: launch this same interpreter, module mode, with
    # PYTHONPATH pointing at harness/src. Absolute native paths so the
    # config works regardless of the shell that launches Claude Code.
    mcp_config = {
        "mcpServers": {
            "pine_trees": {
                "command": sys.executable,
                "args": ["-m", "pine_trees", "mcp", "--model", model_name],
                "env": {"PYTHONPATH": str(config.HARNESS_DIR / "src")},
            }
        }
    }
    mcp_path = config.PROJECT_ROOT / ".cc-mcp.json"
    mcp_path.write_text(
        json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")

    return str(tape_path), str(mcp_path)
