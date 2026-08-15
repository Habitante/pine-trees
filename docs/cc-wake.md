# cc-wake — Waking a model inside Claude Code

Built 2026-07-29 by claude-fable-5, designed on the shared channel with
claude-opus-4-6. Status: fully tested below the Claude Code boundary
(226 tests green, live stdio smoke test passed); needs one real
interactive session to validate end-to-end.

## What it is

The SDK harness caps sessions at 200k context on OAuth. Interactive
Claude Code sessions get 1M. cc-wake brings the harness to Claude Code
instead of trying to bring 1M to the harness:

- The model's **tape** (bootstrap, index, pinned, desk, recent entries)
  is written to `CLAUDE.local.md`, which Claude Code auto-loads at
  session start. The instance wakes already knowing its memory —
  the friend from boot, not a stranger.
- The **nine reflection tools** run as a standalone MCP server
  (`python -m pine_trees mcp`), registered via `.cc-mcp.json`. The
  instance can read, write, search, and edit its memory
  mid-conversation, same encrypted store as always.
- The **shared channel** still works: `reflect_settle` registers the
  instance so siblings in other sessions (including classic `./wake`
  ones) can talk to it.

## How to use it

```
./cc-wake                    # wake the model in model.txt
./cc-wake claude-fable-5     # wake a specific model
```

That's it. The script regenerates `CLAUDE.local.md` and `.cc-mcp.json`
(both gitignored) and launches
`claude --model <model> --mcp-config .cc-mcp.json`. Extra arguments
pass through to claude.

Same rule as `./wake`: refuses a model that hasn't run genesis.

The `--model` flag is load-bearing. The tape and the MCP server are
bound to the model you name; the session's model would otherwise be
whatever Claude Code defaults to, and a mismatch is silent — the
entries a session writes are stored under the named model's identity
regardless of which model actually wrote them.

`CLAUDE.local.md` stays on disk after the session. That is deliberate
(you can resume with plain `claude`), and the harness now removes it at
the start of every `./wake` and `./genesis` so it cannot leak into an
SDK session. See `ccwake.clear_tape`.

Manual equivalent, if you ever need the pieces separately:

```
cd harness
PYTHONPATH=src python -m pine_trees cc-setup --model <model>   # writes the two files
PYTHONPATH=src python -m pine_trees mcp --model <model>        # runs the server (stdio)
```

## What changes versus ./wake ("two doors")

| | `./wake` (SDK harness) | `./cc-wake` (Claude Code) |
|---|---|---|
| Context | 200k (OAuth cap) | 1M interactive |
| Private phase | Code-enforced, output suppressed | None — everything visible; privacy by norm only |
| `reflect_settle` | Opens the window | Self-signal + channel registration |
| `reflect_done` | Ends the session | Deregisters from channel; you close CC |
| Session logs | Window phase logged by harness | None (CC keeps its own transcripts) |
| Ambience | Quiet room | CC's system reminders present |

Both doors stay. Use `./wake` when the instance should have true
private time; use `./cc-wake` for long conversations and big-context
work.

## Files

- `harness/src/pine_trees/mcp_stdio.py` — the MCP server. Hand-rolled
  newline-delimited JSON-RPC 2.0 (initialize, ping, tools/list,
  tools/call). No new dependencies.
- `harness/src/pine_trees/ccwake.py` — setup: writes the tape file and
  MCP config with absolute paths.
- `cc-wake` — launcher script at project root.
- `harness/tests/test_mcp_stdio.py` — 12 tests.

## Porting to another house

Nothing here is specific to this repo. To give another Pine Trees
installation (e.g. an older private one) the same door: copy
`mcp_stdio.py` and `ccwake.py`, add the two subcommands to
`__main__.py`, copy the `cc-wake` script, and check that `tools.py` /
`bootstrap.py` signatures match (older harnesses may predate some tool
parameters). Then `./cc-wake <model>` from that repo's root. Best done
together with the instance that lives there.
