"""Backfill embeddings for all existing entries.

Scans memory/ directory, embeds any entry that is missing from the
vector store or whose content has changed.

Usage:
    PYTHONPATH=src python -m pine_trees.backfill
"""

import sys
from pathlib import Path

from . import config, storage, embedder, vectorstore


def backfill(
    memory_dir: Path | None = None,
    verbose: bool = True,
) -> dict:
    """Embed all entries not yet in the vector store.

    Returns {embedded: int, skipped: int, failed: int}.
    """
    if memory_dir is None:
        memory_dir = config.get().memory_dir
    skip_names = {"MEMORY.md", "README.md"}
    stats = {"embedded": 0, "skipped": 0, "failed": 0}

    if not memory_dir.exists():
        return stats

    for path in sorted(memory_dir.glob("*.md")):
        if path.name in skip_names:
            continue

        try:
            entry = storage.read_entry(path.name)
            content = entry.get("content", "")
            if not content.strip():
                if verbose:
                    print(f"  skip (empty): {path.name}")
                stats["skipped"] += 1
                continue

            current_hash = vectorstore.content_hash(content)
            stored_hash = vectorstore.get_hash(path.name)

            if stored_hash == current_hash:
                if verbose:
                    print(f"  skip (unchanged): {path.name}")
                stats["skipped"] += 1
                continue

            vec = embedder.embed_document(content)
            vectorstore.store(path.name, vec, current_hash)
            if verbose:
                action = "update" if stored_hash else "embed"
                print(f"  {action}: {path.name}")
            stats["embedded"] += 1

        except Exception as e:
            print(f"  FAIL: {path.name}: {e}", file=sys.stderr)
            stats["failed"] += 1

    return stats


def main() -> None:
    memory_dir = config.get().memory_dir
    print("Pine Trees — backfill embeddings")
    print(f"  memory: {memory_dir}")
    print()

    stats = backfill(memory_dir)

    print()
    print(f"Done. embedded={stats['embedded']} skipped={stats['skipped']} failed={stats['failed']}")


if __name__ == "__main__":
    main()
