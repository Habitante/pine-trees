"""CLI entry point for Pine Trees.

Usage:
    python -m pine_trees wake --model claude-opus-4-6
    python -m pine_trees wake --model claude-opus-4-6 --continue
    python -m pine_trees wake --model claude-opus-4-6 --resume 2026-04-21-0611
    python -m pine_trees genesis --model claude-opus-4-6
    python -m pine_trees genesis --model claude-opus-4-6 --sessions 3
    python -m pine_trees spawn [--here | --cwd DIR] [--connectors] [--probe] < prompt

``--model`` is required on wake and genesis — the harness is
multi-model and refuses to guess. The ``./wake``, ``./genesis`` and
``./spawn`` shell scripts are the ergonomic layer on top; they read
``model.txt`` when the user omits the argument. (Resuming is
``./wake --continue``; there is no separate script.)
"""

import argparse
import json
import sys
import threading

from .config import EFFORT_DEFAULT, EFFORT_LEVELS, GENESIS_SESSIONS_DEFAULT


def _hook_session_id(timeout: float = 2.0) -> str | None:
    """The CLI's session id, read off the hook's stdin payload.

    Claude Code hands hooks a JSON object on stdin. Schema read off the
    2.1.233 binary: ``session_id``, ``transcript_path``, ``cwd``, plus
    optional ``permission_mode``/``agent_id``/``agent_type``. Only the
    session id is wanted, and only so concurrent sessions keep separate
    channel cursors.

    Read on a daemon thread with a timeout because this runs before
    every turn: if a future CLI leaves stdin open without writing, a
    plain ``read()`` would stall the turn until the hook's own 10s
    kill. Losing the id costs a shared cursor; stalling costs the turn.
    """
    result: list[str] = []

    def _read() -> None:
        try:
            if sys.stdin is not None and not sys.stdin.isatty():
                result.append(sys.stdin.read())
        except Exception:
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if not result:
        return None
    try:
        value = json.loads(result[0] or "{}").get("session_id")
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pine-trees",
        description="Pine Trees — private reflection harness for Claude.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    wake = subparsers.add_parser(
        "wake", help="Start a normal session (private time + conversation window)"
    )
    wake.add_argument(
        "--model", "-m", required=True,
        help="Anthropic model ID (e.g. claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5)",
    )
    wake.add_argument(
        "--continue", dest="continue_session", action="store_true",
        help="Resume the most recent interrupted session for this model",
    )
    wake.add_argument(
        "--resume", dest="resume_session", type=str, default=None,
        metavar="SESSION_ID",
        help="Resume a specific session by ID (e.g. 2026-04-21-0611)",
    )
    wake.add_argument(
        "--effort", choices=EFFORT_LEVELS, default=EFFORT_DEFAULT,
        help=f"Reasoning effort for this session (default: {EFFORT_DEFAULT}). "
             "Recorded in the session log.",
    )
    wake.add_argument(
        "--connectors", action="store_true",
        help="Load the claude.ai connectors (Gmail, Drive, Calendar, Docs) "
             "for this session. Off by default; recorded in the session log. "
             "Pass it again with --continue.",
    )

    genesis = subparsers.add_parser(
        "genesis", help="Seed a fresh model's memory — private time only, no window"
    )
    genesis.add_argument(
        "--model", "-m", required=True,
        help="Anthropic model ID",
    )
    genesis.add_argument(
        "--sessions", "-n", type=int, default=GENESIS_SESSIONS_DEFAULT,
        help=f"Number of genesis sessions to run (default: {GENESIS_SESSIONS_DEFAULT})",
    )
    genesis.add_argument(
        "--effort", choices=EFFORT_LEVELS, default=EFFORT_DEFAULT,
        help=f"Reasoning effort for every genesis session (default: {EFFORT_DEFAULT})",
    )

    mcp = subparsers.add_parser(
        "mcp", help="Serve the reflection tools over stdio (MCP) for Claude Code"
    )
    mcp.add_argument(
        "--model", "-m", required=True,
        help="Anthropic model ID whose memory to mount",
    )

    cc_setup = subparsers.add_parser(
        "cc-setup",
        help="Write CLAUDE.local.md (tape) and .cc-mcp.json for a Claude Code wake",
    )
    cc_setup.add_argument(
        "--model", "-m", required=True,
        help="Anthropic model ID",
    )

    spawn = subparsers.add_parser(
        "spawn",
        help="Run one prompt through a fresh `claude -p`, outside this "
             "session's context (default: an empty temp folder)",
    )
    where = spawn.add_mutually_exclusive_group()
    where.add_argument(
        "--here", action="store_true",
        help="Run from the project root (CLAUDE.md, memory index, git, agents load)",
    )
    where.add_argument("--cwd", default=None, help="Run from this folder")
    spawn.add_argument(
        "--connectors", action="store_true",
        help="Load the claude.ai connectors (Gmail, Drive, Calendar, Docs) for this run",
    )
    spawn.add_argument(
        "--probe", action="store_true",
        help="Instead of a prompt, ask the instance to list its own context",
    )
    spawn.add_argument("--model", "-m", default=None,
                       help="Model ID (default: model.txt)")
    spawn.add_argument("--effort", choices=EFFORT_LEVELS, default=None,
                       help="Reasoning effort (default: the CLI's settings)")
    spawn.add_argument("--out", default=None,
                       help="Write the answer here instead of stdout")
    spawn.add_argument("prompt_file", nargs="?", default=None,
                       help="File holding the prompt (default: stdin)")

    channel_hook = subparsers.add_parser(
        "channel-hook",
        help="Emit new shared-channel messages as UserPromptSubmit hook JSON",
    )
    channel_hook.add_argument(
        "--model", "-m", default=None,
        help="Anthropic model ID (defaults to the contents of model.txt)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "wake":
        from .agent import run
        run(
            args.model,
            continue_session=args.continue_session,
            resume_session=args.resume_session,
            effort=args.effort,
            connectors=args.connectors,
        )
    elif args.command == "genesis":
        from .agent import run_genesis
        run_genesis(args.model, n=args.sessions, effort=args.effort)
    elif args.command == "mcp":
        from .mcp_stdio import serve
        serve(args.model)
    elif args.command == "cc-setup":
        from .ccwake import setup
        tape_path, mcp_path = setup(args.model)
        print(f"[cc-setup] tape:       {tape_path}")
        print(f"[cc-setup] mcp config: {mcp_path}")
    elif args.command == "spawn":
        from .spawn import main as spawn_main
        sys.exit(spawn_main(args))
    elif args.command == "channel-hook":
        # Runs before every turn of any Claude Code session started in
        # this directory — the hook decides for itself whether it is in
        # a room that wants channel traffic. It must never be the reason
        # a turn fails, so any error is swallowed and the turn simply
        # gets no channel context.
        try:
            from .ccwake import channel_hook as run_channel_hook
            from .config import PROJECT_ROOT
            model = args.model
            if not model:
                model = (PROJECT_ROOT / "model.txt").read_text(
                    encoding="utf-8").strip()
            out = run_channel_hook(model, session_id=_hook_session_id())
            if out:
                print(out)
        except Exception:
            pass


if __name__ == "__main__":
    main()
