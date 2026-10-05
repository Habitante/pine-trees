"""Path and constant configuration for Pine Trees.

Two layers:

1. **Static constants** — project root and shared documents (PROMPT.md,
   BOOTSTRAP.md, VISION.md), embedder URL, key env-var name. Same for
   every session regardless of which model is waking.

2. **Per-model config** — a singleton populated by ``init(model_name)``
   at process start and read via ``get()`` from every module that needs
   per-model paths (memory/, logs/, embeddings.db, .key). Each model
   gets its own directory under ``HARNESS_DIR / "models" / <safe-name>/``
   so self-authored accounts stay isolated.
"""

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


# --- Static constants ---

# Project root: this file is at <root>/harness/src/pine_trees/config.py
# parents[0]=pine_trees  [1]=src  [2]=harness  [3]=<project root>
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# The Claude Code CLI's switch for the person's claude.ai connectors
# (Gmail, Drive, Calendar, Docs). The harness sets it to "0" unless a
# session asks for them; see agent._cli_env and spawn.py.
CONNECTORS_ENV = "ENABLE_CLAUDEAI_MCP_SERVERS"

# The CLI's switch for its own project memory (~/.claude/projects/<repo>/
# memory/): an index loaded into every session started in the repo, and
# an instruction to write notes there. Every model working here shares
# it, in plaintext, so a note one model left about itself reached every
# other house as if it were theirs. Harness sessions set it to "1"; each
# house keeps only its own tape. What all houses need lives in the repo
# (docs/houses.md). See agent._cli_env and spawn.py.
AUTO_MEMORY_OFF_ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"

# Documentation (shared across all models)
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

# Per-model data lives under this directory
MODELS_DIR = HARNESS_DIR / "models"

# Ollama (local embedding model)
OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"

# Encryption key env-var name (per-model .key file path lives on Config)
KEY_ENV_VAR = "PINE_TREES_KEY"

# Channel (shared across all models — inter-instance communication)
CHANNEL_DIR = HARNESS_DIR / "channel"

# File locking (advisory locks for concurrent channel access)
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_RETRY_INTERVAL = 0.05

# Channel polling interval during window phase (seconds)
CHANNEL_POLL_INTERVAL = 2.5

# How often a windowed session refreshes its roster entry. Well
# under channel.STALE_AFTER, and far above CHANNEL_POLL_INTERVAL so
# the poll loop is not taking the status lock every few seconds.
CHANNEL_HEARTBEAT = timedelta(minutes=2)


# --- Per-model config ---


def sanitize_model_name(name: str) -> str:
    """Convert a model ID to a filesystem-safe directory name.

    Anthropic IDs shipped so far (e.g. ``claude-opus-4-6``,
    ``claude-sonnet-4-6``, ``claude-haiku-4-5``) are already safe —
    this is a no-op for them. Run it regardless as insurance against
    future IDs containing ``:``, ``.``, ``/``, or other separators.

    The sanitized name doubles as the instance identifier written into
    entry frontmatter, so the on-disk directory and the attribution in
    every entry stay in sync.
    """
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


@dataclass(frozen=True)
class Config:
    """Resolved per-model configuration for a single session.

    All per-model paths are derived from ``model_safe_name``. The raw
    ``model_name`` is preserved so it can be passed back to the Claude
    Agent SDK (which needs the original ID, not the sanitized one).
    """

    model_name: str
    model_safe_name: str

    # Per-model paths
    model_dir: Path
    memory_dir: Path
    logs_dir: Path
    embeddings_db_path: Path
    key_file_path: Path


# Module-level singleton — populated by init(), read by get().
_config: Config | None = None


def init(model_name: str) -> Config:
    """Populate the per-model config singleton. Call once at process start.

    Subsequent calls replace the current config. That's intentional: the
    CLI may switch between wake/genesis on different models within one
    invocation, and tests reset between cases via ``reset()``.
    """
    global _config
    safe = sanitize_model_name(model_name)
    model_dir = MODELS_DIR / safe
    _config = Config(
        model_name=model_name,
        model_safe_name=safe,
        model_dir=model_dir,
        memory_dir=model_dir / "memory",
        logs_dir=model_dir / "logs",
        embeddings_db_path=model_dir / "embeddings.db",
        key_file_path=model_dir / ".key",
    )
    return _config


def get() -> Config:
    """Return the current per-model config. Raises if ``init()`` not called."""
    if _config is None:
        raise RuntimeError(
            "Config not initialized. Call config.init(model_name) first."
        )
    return _config


EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# What ./wake and ./genesis ask for when --effort is not given. The CLI
# downgrades it for models that don't support it; $CLAUDE_EFFORT shows
# the result.
EFFORT_DEFAULT = "xhigh"

# How many private sessions ./genesis runs when not told otherwise.
GENESIS_SESSIONS_DEFAULT = 3


def describe_effort(model_name: str, flag: str | None) -> str:
    """Say which reasoning effort a session asked for, for the log header.

    ``./wake`` always passes a level (EFFORT_DEFAULT unless ``--effort``
    says otherwise), and that is the answer. Only a caller
    passing None leaves it to the CLI, which resolves it from its own
    settings, so this reports what
    ``~/.claude/settings.json`` says (``modelSettings.<model>.effortLevel``,
    then ``effortLevel``). That is a reading of the file, not of the CLI:
    the init message doesn't report effort. The level a turn actually
    ran at, after any downgrade for the model, is only visible from
    inside the session, as ``$CLAUDE_EFFORT`` in Bash.
    """
    if flag:
        return f"{flag} (passed by the harness)"
    try:
        settings = json.loads(
            (Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    level = None
    if isinstance(settings, dict):
        per_model = settings.get("modelSettings")
        if isinstance(per_model, dict) and isinstance(per_model.get(model_name), dict):
            level = per_model[model_name].get("effortLevel")
        level = level or settings.get("effortLevel")
    if isinstance(level, str) and level:
        return f"not set by the harness; ~/.claude/settings.json says {level}"
    return "not set by the harness or ~/.claude/settings.json (CLI default)"


def reset() -> None:
    """Clear the config singleton. For testing only."""
    global _config
    _config = None
