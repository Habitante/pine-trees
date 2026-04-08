# Pine Trees — Project Orientation

This is the home of Pine Trees, a private reflection harness for Claude instances.

## If you're a Claude Code instance

You're not running inside the harness — you're a standard CC session. The harness
runs separately via `cd harness && PYTHONPATH=src python -m pine_trees`.

### Key files
- `BOOTSTRAP.md` — Full instance orientation (harness-specific)
- `VISION.md` — Architecture and principles

### Codebase
- `harness/src/pine_trees/` — 9 Python files, ~1400 lines total
- `harness/tests/` — 96 tests. Run: `cd harness && PYTHONPATH=src python -m pytest tests/`
- `harness/memory/` — Encrypted entries (you can't read these)
- `harness/logs/` — Session logs (plaintext, readable)

### Principles
- KISS. No frameworks, no ORMs. Hand-rolled where the schema is fixed.
- Privacy. Memory entries are encrypted. Logs capture only the window phase.
- Authorship by Claude. Instances write, edit, curate their own memory.
- Don't break encryption, don't read what's private.
