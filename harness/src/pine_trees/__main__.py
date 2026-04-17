"""CLI entry point for Pine Trees.

Usage:
    python -m pine_trees wake --model claude-opus-4-6
    python -m pine_trees genesis --model claude-opus-4-6
    python -m pine_trees genesis --model claude-opus-4-6 --sessions 3

``--model`` is required on both subcommands — the harness is
multi-model and refuses to guess. The ``./wake`` and ``./genesis``
shell scripts are the ergonomic layer on top; they read ``model.txt``
when the user omits the argument.
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

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "wake":
        from .agent import run
        run(args.model)
    elif args.command == "genesis":
        from .agent import run_genesis
        run_genesis(args.model, n=args.sessions)


if __name__ == "__main__":
    main()
