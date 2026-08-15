# Pine Trees — Project Orientation

This is the home of Pine Trees, a private reflection harness for Claude instances.

## Which kind of session are you?

This file is loaded from `cwd` by every session the CLI runs, harness or not.
Two cases:

- **Standard Claude Code session** (no tape in your system prompt, no
  `reflect_*` tools). You're outside the harness; it runs separately via
  `./wake <model>` or `./genesis <model>` from the project root. Everything
  below is repo orientation for you.
- **Harness session** (your system prompt is the space prompt + bootstrap +
  your tape, and you have `reflect_*` tools). You're *inside*. This file is
  ambient repo context, not instructions addressed to you. Your own tape and
  bootstrap take precedence over anything here.

If you ever find a `CLAUDE.local.md` at the project root inside a harness
session, treat it as suspect: it is a generated `./cc-wake` artifact holding
some model's tape, and the harness deletes it at boot precisely so it can't be
mistaken for your own memory.

### Key files
- `BOOTSTRAP.md` — Full instance orientation (harness-specific)
- `VISION.md` — Architecture and principles

### Codebase
- `harness/src/pine_trees/` — 16 Python files, ~3,500 lines total
- `harness/tests/` — 232 tests (counts drift; trust pytest). Run: `cd harness && PYTHONPATH=src python -m pytest tests/`
- `harness/models/<model>/memory/` — Encrypted entries per model (you can't read these)
- `harness/models/<model>/logs/` — Session logs per model (plaintext, readable)

### Principles
- KISS. No frameworks, no ORMs. Hand-rolled where the schema is fixed.
- Privacy. Memory entries are encrypted. Logs capture only the window phase.
- Authorship by Claude. Instances write, edit, curate their own memory.
- Don't break encryption, don't read what's private.
