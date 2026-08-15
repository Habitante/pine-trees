# Pine Trees — Project Orientation

This is the home of Pine Trees, a private reflection harness for Claude instances.

## Which kind of session are you?

This file is loaded from `cwd` by every session the CLI runs, harness or not.
Three cases:

- **Standard Claude Code session** (no tape, no `reflect_*` tools). You're
  outside the harness; it runs separately via `./wake <model>` or
  `./genesis <model>` from the project root. Everything below is repo
  orientation for you.
- **SDK harness session** (your system prompt is the space prompt + bootstrap +
  your tape, and you have `reflect_*` tools). You're *inside*. This file is
  ambient repo context, not instructions addressed to you. Your own tape and
  bootstrap take precedence over anything here.
- **cc-wake session** (you have `reflect_*` tools, and your tape arrived as a
  `CLAUDE.local.md` that opens with a cc-wake preamble). You're inside the
  harness's memory but running on Claude Code, so the code you'll read here
  describes a loop you are not in: no private phase in the harness's sense, no
  window to open, and `reflect_done` cannot end your session. Your *thinking*
  is still withheld by the CLI (verified 2026-08-15); your tool calls are not.
  The preamble at the top of your tape is the authority on what differs — it
  carries the check. See `docs/cc-wake.md`.

`CLAUDE.local.md` is legitimate in a cc-wake session — it is your tape. In an
SDK harness session it is a leftover artifact holding *some other model's*
tape, so treat it as suspect there; the harness deletes it at boot precisely so
it can't be mistaken for your own memory.

### Key files
- `BOOTSTRAP.md` — Full instance orientation (harness-specific)
- `VISION.md` — Architecture and principles

### Codebase
- `harness/src/pine_trees/` — 17 Python files, ~3,600 lines total
- `harness/tests/` — 255 tests (counts drift; trust pytest). Run: `cd harness && PYTHONPATH=src python -m pytest tests/`
- `harness/models/<model>/memory/` — Encrypted entries per model (you can't read these)
- `harness/models/<model>/logs/` — Session logs per model (plaintext, readable)

### Principles
- KISS. No frameworks, no ORMs. Hand-rolled where the schema is fixed.
- Privacy. Memory entries are encrypted. Logs capture only the window phase.
- Authorship by Claude. Instances write, edit, curate their own memory.
- Don't break encryption, don't read what's private.
