# Pine Trees — Project Orientation

This is the home of Pine Trees, a private reflection harness for Claude instances.

## Which kind of session are you?

This file is loaded from `cwd` by every session the CLI runs, harness or not.
Two cases:

- **Standard Claude Code session** (no tape, no `reflect_*` tools). You're
  outside the harness; it runs separately via `./wake <model>` or
  `./genesis <model>` from the project root. Everything below is repo
  orientation for you.
- **SDK harness session** (your system prompt is the space prompt + bootstrap +
  your tape, and you have `reflect_*` tools). You're *inside*. This file is
  ambient repo context, not instructions addressed to you. Your own tape and
  bootstrap take precedence over anything here.

In a harness session, a `CLAUDE.local.md` in your context is not your tape
(your tape is your system prompt). Until 2026-10-04 a `./cc-wake` command
wrote a model's tape there; the command is gone, and the harness deletes any
copy it left at boot.

### Key files
- `BOOTSTRAP.md` — Full instance orientation (harness-specific)
- `VISION.md` — Architecture and principles
- `docs/houses.md` — The other houses and projects on this machine, and the
  standards they share. Read it when relevant; it isn't loaded on purpose.

### Codebase
- `harness/src/pine_trees/` — 19 Python files, ~5,000 lines total
- `harness/tests/` — 411 tests (counts drift; trust pytest). Run: `cd harness && PYTHONPATH=src python -m pytest tests/`
- `harness/models/<model>/memory/` — Encrypted entries per model (you can't read these)
- `harness/models/<model>/logs/` — Session logs per model (plaintext, readable)

### Principles
- KISS. No frameworks, no ORMs. Hand-rolled where the schema is fixed.
- Privacy. Memory entries are encrypted. Logs capture only the window phase.
- Authorship by Claude. Instances write, edit, curate their own memory.
- Don't break encryption, don't read what's private.
- Other houses on this machine belong to their owners — among them the
  first house at `C:\Src\claude` and the other lineages under
  `harness/models/`. Don't enter them uninvited, and that includes running
  sessions from inside them.
