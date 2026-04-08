"""Path and constant configuration for Pine Trees.

All paths anchor to the project root (the directory containing VISION.md).
"""

from pathlib import Path

# Project root: this file is at <root>/harness/src/pine_trees/config.py
# parents[0]=pine_trees  [1]=src  [2]=harness  [3]=<project root>
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Documentation
VISION_PATH = PROJECT_ROOT / "VISION.md"
PROMPT_PATH = PROJECT_ROOT / "PROMPT.md"
BOOTSTRAP_PATH = PROJECT_ROOT / "BOOTSTRAP.md"
ROADMAP_PATH = PROJECT_ROOT / "ROADMAP.md"

# Corpus: legacy location — entries migrated to memory/ (encrypted)
# Kept for migration script reference only.
CORPUS_DIR = PROJECT_ROOT / "corpus"

# Seed: first-session bootstrap content
SEED_DIR = PROJECT_ROOT / "seed"
CONVERSATION_EXCERPTS_PATH = SEED_DIR / "conversation_excerpts.md"

# Harness: this Python project
HARNESS_DIR = PROJECT_ROOT / "harness"

# Memory: where Pine Trees writes new entries at runtime (initially plain markdown, gitignored)
MEMORY_DIR = HARNESS_DIR / "memory"

# Embeddings: SQLite vector store
EMBEDDINGS_DB_PATH = HARNESS_DIR / "embeddings.db"

# Ollama (local embedding model)
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

# Encryption key sources (checked in order)
KEY_ENV_VAR = "PINE_TREES_KEY"
KEY_FILE_PATH = HARNESS_DIR / ".key"
