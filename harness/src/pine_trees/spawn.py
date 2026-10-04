"""One fresh `claude -p` run, outside the calling session's context.

Run as ``./spawn`` from the project root (or ``python -m pine_trees
spawn``). One mechanism, three uses:

- **A clean instance** (the default). An Agent-tool subagent spawned in
  this repo is not plain: the CLI hands it CLAUDE.md, the person's
  auto-memory index for the project, git status and the repo's agent
  definitions. On 2026-10-04 that context flipped a yes/no answer about
  adopting memory from 0/6 to 6/6, while the position stated under the
  verdict stayed the same. This runs in a fresh, empty temp folder
  instead, so none of those load. Clean is a different context, not no
  context: Claude Code's own system prompt, the user's email and bypass
  mode remain. ``--probe`` asks the instance to list what it can see.
- **The house arm of a comparison** (``--here``): the same run from the
  project root, so CLAUDE.md, the memory index, git and the agents load.
- **A connector for one task** (``--connectors``). The harness keeps the
  person's claude.ai connectors (Gmail, Drive, Calendar, Docs) out of
  its sessions (agent._cli_env). This turns them on for one run, for
  something the person asked for. In the window the command appears in
  the session log's status line.

The calling session's own CLI variables are stripped from the child's
environment: inherited, they would make the child a confused copy of
this session (its session id among them) rather than a new one.
"""

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, transcripts

# The calling CLI session's identity and plumbing. Everything a new
# session sets up for itself. CLAUDE_CONFIG_DIR is not here on purpose:
# it says where the person's CLI config (and login) lives.
_STRIP = frozenset({
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
})
# The harness's wording for the silent-turn reminder is about private
# time and the window; a one-shot run has neither.
_STRIP_PREFIXES = ("CLAUDE_CODE_SILENT_TURN_REMINDER",)

# Named so that the temp folder (and the CLI's project folder for it,
# which carries its name) can be found and removed afterwards.
_TMP_PREFIX = "spawn-"

PROBE = """\
Please don't use any tools. This is a quick inventory of your own context, \
for a setup check.

List every part of your context other than this message that tells you \
anything about your environment, the project, the user, or your situation. \
For each, give a short label and quote its first line or two verbatim. Cover \
at least: any CLAUDE.md or project-instruction contents; any memory files or \
memory index; git status or recent commits; working directory and platform \
info; the names of the tools you have (names only, comma-separated); any \
custom agent types; any skills or slash commands listed; any MCP servers or \
connectors; anything about who the user is. If a category is absent, write \
"none". Be literal, not interpretive.
"""


def child_env(base: dict[str, str], connectors: bool = False) -> dict[str, str]:
    """The environment for the child CLI: the caller's, minus its session."""
    env = {k: v for k, v in base.items()
           if k.upper() not in _STRIP and not k.upper().startswith(_STRIP_PREFIXES)}
    env[config.CONNECTORS_ENV] = "1" if connectors else "0"
    return env


def cli_args(model: str | None = None, effort: str | None = None,
             connectors: bool = False) -> list[str]:
    """Flags for the child CLI. No transcript is written, no skills are
    listed, and without connectors no MCP server loads at all."""
    args = ["-p", "--no-session-persistence", "--dangerously-skip-permissions",
            "--disable-slash-commands"]
    if not connectors:
        args.append("--strict-mcp-config")
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args


def default_model() -> str | None:
    """The model in model.txt (what ./wake last woke), or None for the
    CLI's own default."""
    try:
        name = (config.PROJECT_ROOT / "model.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def _cleanup(tmp: Path) -> None:
    """Remove the temp folder and the CLI's project folder for it.

    The CLI makes ``projects/<slug of the cwd>/`` even without a
    transcript (it sets up an auto-memory folder there). Only folders
    named after this run's temp folder, and holding no transcript, go.
    """
    shutil.rmtree(tmp, ignore_errors=True)
    tag = tmp.name.lower()
    root = transcripts.projects_dir()
    if not root.is_dir():
        return
    for d in root.iterdir():
        if d.is_dir() and d.name.lower().endswith(tag) and not any(d.rglob("*.jsonl")):
            shutil.rmtree(d, ignore_errors=True)


def run(prompt: str, *, where: str = "clean", cwd: Path | None = None,
        model: str | None = None, effort: str | None = None,
        connectors: bool = False, timeout: float | None = None,
        ) -> subprocess.CompletedProcess:
    """Run one prompt through a fresh CLI. ``where`` is "clean" (a new
    empty temp folder), "here" (the project root) or "cwd" (``cwd``)."""
    exe = shutil.which("claude")
    if not exe:
        raise FileNotFoundError("the claude CLI is not on PATH")
    tmp = None
    if where == "clean":
        tmp = Path(tempfile.gettempdir()) / f"{_TMP_PREFIX}{secrets.token_hex(4)}"
        tmp.mkdir()
        cwd = tmp
    elif where == "here":
        cwd = config.PROJECT_ROOT
    elif where != "cwd" or cwd is None:
        raise ValueError(f"where={where!r} needs a known place to run")
    try:
        return subprocess.run(
            [exe, *cli_args(model, effort, connectors)],
            input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            cwd=str(cwd), env=child_env(dict(os.environ), connectors),
            timeout=timeout,
        )
    finally:
        if tmp is not None:
            _cleanup(tmp)


def main(args) -> int:
    """``python -m pine_trees spawn`` (see __main__)."""
    if args.probe:
        prompt = PROBE
    elif args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = sys.stdin.read()
    if not prompt.strip():
        print("spawn: empty prompt (give a file, pipe one in, or --probe)",
              file=sys.stderr)
        return 2
    where = "cwd" if args.cwd else ("here" if args.here else "clean")
    done = run(prompt, where=where, cwd=Path(args.cwd) if args.cwd else None,
               model=args.model or default_model(), effort=args.effort,
               connectors=args.connectors)
    out = done.stdout or ""
    if args.out:
        Path(args.out).write_bytes(out.encode("utf-8"))
    else:
        sys.stdout.buffer.write(out.encode("utf-8"))
        sys.stdout.flush()
    if done.stderr:
        sys.stderr.buffer.write(done.stderr.encode("utf-8"))
    return done.returncode
