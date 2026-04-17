"""One-shot migration from the pre-multi-model layout.

Public users upgrading across the multi-model split have their
single-model data at ``harness/memory/``, ``harness/logs/``,
``harness/embeddings.db``, and ``harness/.key``. This module moves
those into ``harness/models/claude-opus-4-6/`` on first wake/genesis
after the upgrade, then becomes a silent no-op.

The code is deliberately isolated in its own module so it can be
deleted in a single commit once the migration window has closed.
"""

import sys
from pathlib import Path

from . import config


LEGACY_MODEL = "claude-opus-4-6"


def migrate_legacy_layout_if_needed(
    harness_dir: Path | None = None,
    models_dir: Path | None = None,
    project_root: Path | None = None,
) -> bool:
    """Move legacy single-model data into ``models/claude-opus-4-6/``.

    Returns True if migration ran, False if the layout was already
    current. Paths default to ``config`` module constants but can be
    overridden in tests.
    """
    harness_dir = harness_dir or config.HARNESS_DIR
    models_dir = models_dir or config.MODELS_DIR
    project_root = project_root or config.PROJECT_ROOT

    sources = [
        (harness_dir / "memory", "memory"),
        (harness_dir / "logs", "logs"),
        (harness_dir / "embeddings.db", "embeddings.db"),
        (harness_dir / ".key", ".key"),
    ]
    if not any(src.exists() for src, _ in sources):
        return False

    target = models_dir / LEGACY_MODEL
    if target.exists():
        # User migrated by hand (or ran the migration before) — don't clobber.
        return False

    target.mkdir(parents=True, exist_ok=True)
    moved = []
    for src, name in sources:
        if src.exists():
            src.rename(target / name)
            moved.append(name)

    model_txt = project_root / "model.txt"
    if not model_txt.exists():
        model_txt.write_text(f"{LEGACY_MODEL}\n", encoding="utf-8")

    print(
        f"[migrate] moved legacy layout into {target}: {', '.join(moved)}",
        file=sys.stderr,
    )
    return True
