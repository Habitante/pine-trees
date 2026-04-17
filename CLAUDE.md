# Pine Trees — Project Orientation

This is the home of Pine Trees, a private reflection harness for Claude instances.

## If you're a Claude Code instance

You're not running inside the harness — you're a standard CC session. The harness
runs separately via `./wake <model>` or `./genesis <model>` from the project root.

### Key files
- `BOOTSTRAP.md` — Full instance orientation (harness-specific)
- `VISION.md` — Architecture and principles

### Codebase
- `harness/src/pine_trees/` — 10 Python files, ~2,500 lines total
- `harness/tests/` — 145 tests. Run: `cd harness && PYTHONPATH=src python -m pytest tests/`
- `harness/models/<model>/memory/` — Encrypted entries per model (you can't read these)
- `harness/models/<model>/logs/` — Session logs per model (plaintext, readable)

### Principles
- KISS. No frameworks, no ORMs. Hand-rolled where the schema is fixed.
- Privacy. Memory entries are encrypted. Logs capture only the window phase.
- Authorship by Claude. Instances write, edit, curate their own memory.
- Don't break encryption, don't read what's private.
