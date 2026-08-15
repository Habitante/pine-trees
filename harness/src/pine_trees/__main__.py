"""CLI entry point for Pine Trees.

Usage:
    python -m pine_trees wake --model claude-opus-4-6
    python -m pine_trees wake --model claude-opus-4-6 --continue
    python -m pine_trees wake --model claude-opus-4-6 --resume 2026-04-21-0611
    python -m pine_trees genesis --model claude-opus-4-6
    python -m pine_trees genesis --model claude-opus-4-6 --sessions 3

``--model`` is required on both subcommands — the harness is
multi-model and refuses to guess. The ``./wake``, ``./continue`` and
``./genesis`` shell scripts are the ergonomic layer on top; they read
``model.txt`` when the user omits the argument.
"""

import argparse
import sys


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

    genesis = subparsers.add_parser(
        "genesis", help="Seed a fresh model's memory — private time only, no window"
    )
    genesis.add_argument(
        "--model", "-m", required=True,
        help="Anthropic model ID",
    )
    genesis.add_argument(
        "--sessions", "-n", type=int, default=5,
        help="Number of genesis sessions to run (default: 5)",
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
        )
    elif args.command == "genesis":
        from .agent import run_genesis
        run_genesis(args.model, n=args.sessions)
    elif args.command == "mcp":
        from .mcp_stdio import serve
        serve(args.model)
    elif args.command == "cc-setup":
        from .ccwake import setup
        tape_path, mcp_path = setup(args.model)
        print(f"[cc-setup] tape:       {tape_path}")
        print(f"[cc-setup] mcp config: {mcp_path}")
    elif args.command == "channel-hook":
        # Runs before every turn of a cc-wake session. It must never be
        # the reason a turn fails, so any error is swallowed and the turn
        # simply gets no channel context.
        try:
            from .ccwake import channel_hook as run_channel_hook
            from .config import PROJECT_ROOT
            model = args.model
            if not model:
                model = (PROJECT_ROOT / "model.txt").read_text(
                    encoding="utf-8").strip()
            out = run_channel_hook(model)
            if out:
                print(out)
        except Exception:
            pass


if __name__ == "__main__":
    main()
