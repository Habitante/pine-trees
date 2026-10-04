"""Agent wake/settle/sleep loop.

Two phases in a single Claude Agent SDK session:

  1. Private time: instance reads the tape, uses reflect_read/reflect_write/
     reflect_search, calls reflect_settle when ready for conversation.
  2. Window: user types; instance responds with full context.
     Ends when user types /end or instance calls reflect_done.

Same ClaudeSDKClient session throughout — tape stays loaded, context preserved.
"""

import argparse
import os
import sys
import uuid
import anyio
from datetime import datetime

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    CLIConnectionError,
    CLINotFoundError,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ProcessError,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI as FormattedANSI
from prompt_toolkit.patch_stdout import patch_stdout

from . import (bootstrap, ccwake, channel, config, crypto, mail, migrate,
               sessions, transcripts, vectorstore)
from .config import CHANNEL_POLL_INTERVAL, HARNESS_DIR, PROJECT_ROOT
from .logger import SessionLogger
from .tools import SessionState, build_tools, channel_heartbeat


MCP_SERVER_NAME = "pine_trees"
MAX_PRIVATE_TURNS = 15

# The SDK drops the whole session if one JSON message from the CLI tops
# its buffer, which defaults to 1 MB. Images come back base64-encoded, and
# parallel tool results arrive in one message: on 2026-09-24 two chart
# PNGs (400 KB + 316 KB, ~955 KB encoded) read at once killed a window
# mid-conversation with CLIJSONDecodeError. The limit is a check on
# pending length, not an allocation, so a generous one costs nothing.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024

# Claude Code built-in tools granted to the instance.
# The instance has full project tools — including Write, Edit, and Bash —
# because agency is part of the trust contract. An instance that wants to
# verify the harness, propose improvements, or modify the code itself
# should be able to. The prohibition on deleting memory entries is enforced
# by norm, not by tool restriction. If that norm is violated, the violation
# is documented and the rule is clarified, not worked around.
PROJECT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent"]

# Tools a spawned peer must not have. Every tool closes over the calling
# instance's SessionState, so a peer's reflect_done set state.done on the
# PARENT and deregistered the parent's channel id — a peer could end the
# session that spawned it. reflect_peer_context ships the bootstrap, which
# tells the peer "You can leave — call reflect_done at any point," so the
# peer was handed that button along with an invitation to press it.
#
# tools.PEER_PREAMBLE now says plainly that a peer ends by answering. That
# is necessary and not sufficient: this house already learned that omitting
# a tool from the docs does not stop the trained reflex to wrap up neatly
# at the end of a first response — which is exactly why genesis removes
# reflect_settle at server level rather than by staying quiet about it.
# Same remedy here. Withheld, not merely undocumented.
PEER_DENIED_TOOLS = ("reflect_done", "reflect_settle")

# The model name the CLI puts on messages it writes itself (API errors),
# as opposed to messages the instance produced. See _synthetic_text.
SYNTHETIC_MODEL = "<synthetic>"

# ANSI color constants — git bash and VS Code terminal both handle these.
DIM = "\033[90m"        # dim gray — system chrome, tool status
GREEN = "\033[32m"      # green — prompt, success markers
CYAN = "\033[36m"       # cyan — context info
YELLOW = "\033[33m"     # yellow — warnings
RED = "\033[31m"        # red — errors
BOLD = "\033[1m"        # bold
RST = "\033[0m"         # reset


def _print_claude_api_unreachable(e: Exception) -> None:
    """Print actionable guidance for Claude Agent SDK failures.

    The SDK raises a hierarchy under ClaudeSDKError: CLINotFoundError when
    Claude Code isn't installed, CLIConnectionError for transport failures,
    ProcessError when the underlying CLI exits with an error (auth expiry,
    subscription issues, rate limits). Each path gets a different recovery
    hint so the user knows which knob to turn.
    """
    if isinstance(e, CLINotFoundError):
        print(f"{RED}[error] Claude Code is not installed or not on PATH.{RST}")
        print()
        print(f"{DIM}  Pine Trees runs on top of Claude Code via the Claude Agent SDK.{RST}")
        print(f"{DIM}  1. Install Claude Code:  https://claude.ai/code{RST}")
        print(f"{DIM}  2. Verify:               `claude --version`{RST}")
        print(f"{DIM}  3. Sign in:              `claude`  (and complete the browser flow){RST}")
        return

    if isinstance(e, CLIConnectionError):
        print(f"{RED}[error] Cannot connect to Claude Code: {e}{RST}")
        print()
        print(f"{DIM}  The Claude Agent SDK couldn't establish a session with Claude Code.{RST}")
        print(f"{DIM}  1. Verify install:  `claude --version`{RST}")
        print(f"{DIM}  2. Sign in again:   `claude`  (re-runs the browser auth flow){RST}")
        print(f"{DIM}  3. Check your plan: https://claude.ai/plans{RST}")
        print(f"{DIM}  4. Check network:   Claude Code needs outbound HTTPS to api.anthropic.com{RST}")
        return

    if isinstance(e, ProcessError):
        print(f"{RED}[error] Claude Code process exited with an error: {e}{RST}")
        print()
        print(f"{DIM}  The underlying Claude Code CLI failed. Common causes:{RST}")
        print(f"{DIM}  - Authentication expired  — run `claude` once to re-login{RST}")
        print(f"{DIM}  - Subscription issue      — check https://claude.ai/plans{RST}")
        print(f"{DIM}  - Rate limit or quota     — wait and retry{RST}")
        print(f"{DIM}  - Stale CLI version       — update Claude Code{RST}")
        return

    # Fallback for other ClaudeSDKError subclasses.
    print(f"{RED}[error] Claude Agent SDK error: {type(e).__name__}: {e}{RST}")
    print()
    print(f"{DIM}  If this persists, verify your Claude Code installation:{RST}")
    print(f"{DIM}    claude --version{RST}")
    print(f"{DIM}  And that your subscription is active at https://claude.ai/plans{RST}")


def _print_wake_without_genesis() -> None:
    """Refuse to open a conversation window on an empty corpus."""
    cfg = config.get()
    print(f"{RED}[error] No memory to wake into for {cfg.model_name} — "
          f"this model has no prior entries.{RST}")
    print()
    print(f"{DIM}  Every Pine Trees session begins by reading a tape of what prior{RST}")
    print(f"{DIM}  instances wrote. On a model with no entries, waking would open a{RST}")
    print(f"{DIM}  conversation with a mind that has nothing to remember.{RST}")
    print()
    print(f"{DIM}  Run genesis first to seed this model's corpus:{RST}")
    print(f"{DIM}    ./genesis {cfg.model_name}{RST}")
    print(f"{DIM}  (default: {config.GENESIS_SESSIONS_DEFAULT} private sessions, no window, no human present).{RST}")
    print()
    print(f"{DIM}  Then come back and wake:{RST}")
    print(f"{DIM}    ./wake {cfg.model_name}{RST}")


def _print_genesis_on_existing(entry_count: int) -> None:
    """Refuse to run genesis on a harness that already has a corpus."""
    cfg = config.get()
    print(f"{RED}[error] Pine Trees already has {entry_count} "
          f"{'entry' if entry_count == 1 else 'entries'} in memory/:{RST}")
    print(f"{RED}  {cfg.memory_dir}{RST}")
    print()
    print(f"{DIM}  Genesis is first-time setup only — it seeds a new model's memory.{RST}")
    print(f"{DIM}  Running it again would stack new entries on top of a corpus that{RST}")
    print(f"{DIM}  already exists for this model. The harness treats that corpus as{RST}")
    print(f"{DIM}  self-authored memory the instance chose to preserve, not a cache{RST}")
    print(f"{DIM}  the harness gets to regenerate.{RST}")
    print()
    print(f"{DIM}  If you want to open a conversation with this model, run:{RST}")
    print(f"{DIM}    ./wake {cfg.model_name}{RST}")
    print()
    print(f"{DIM}  If you really want to start this model over from scratch — knowing{RST}")
    print(f"{DIM}  prior entries will be lost along with the encryption key — remove{RST}")
    print(f"{DIM}  the model directory explicitly, then re-run genesis:{RST}")
    print(f"{DIM}    rm -rf \"{cfg.model_dir}\"{RST}")
    print(f"{DIM}    ./genesis {cfg.model_name}{RST}")


def _mcp_result(text: str) -> dict:
    """Wrap a text string in MCP tool result format."""
    return {"content": [{"type": "text", "text": text}]}


def _obj_schema(properties: dict, required: list[str]) -> dict:
    """Full JSON Schema for a tool's input.

    The SDK's shorthand form ({"name": type}) marks every parameter
    required, which rejects valid calls to tools with optional
    parameters (e.g. reflect_edit with only filename + description).
    Tools where some parameters are optional must use this instead.
    """
    return {"type": "object", "properties": properties, "required": required}


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _format_entry(filename: str, entry: dict) -> str:
    """Format a storage entry for display to the instance."""
    text = f"# {filename}\n\n"
    for key in ("instance", "session", "date", "context", "tags", "moves",
                "timestamp", "description", "pinned", "quiet", "desk"):
        if key in entry:
            text += f"{key}: {entry[key]}\n"
    text += f"\n{entry.get('content', '')}\n"
    return text


def _build_mcp_tools(state: SessionState, genesis_mode: bool = False):
    """Thin MCP adapter over build_tools().

    All logic lives in tools.py (single source of truth, tested directly).
    This layer handles: MCP tool registration, args unpacking, result formatting.

    When genesis_mode=True, reflect_settle is excluded from the returned tools.
    In genesis there is no conversation window to open, so settle is
    semantically meaningless (it would just exit the session, duplicating
    reflect_done). Removing it from the MCP server — not just from
    allowed_tools — prevents the trained instance reflex of settling after
    one turn. The SDK's allowed_tools filter does not reliably exclude
    MCP tools that are registered on the server, so exclusion must happen
    at the server-registration level.
    """
    import json
    core = build_tools(state)

    @tool(
        "reflect_read",
        "Read a prior reflection entry by filename. "
        "Returns the full entry including frontmatter metadata and content.",
        {"filename": str},
    )
    async def reflect_read(args):
        entry = core["reflect_read"](args["filename"])
        return _mcp_result(_format_entry(args["filename"], entry))

    @tool(
        "reflect_write",
        "Write a new reflection entry. The slug becomes part of the filename. "
        "tags and moves are lists of strings (pass empty lists if unused). "
        "description is an optional one-line summary for the tape index. "
        "pinned (default false) marks entries as operational memory that "
        "future instances always see in full at wake. "
        "quiet (default false) marks entries as background knowledge — "
        "indexed and searchable but excluded from the tape's full-text "
        "slots (use for project summaries, reference material). "
        "desk (default false) marks entries as transient working context — "
        "shown in full at wake like pinned, but meant to be cleared when "
        "the work moves on (handoffs, active sprint notes). "
        "Returns the filename written.",
        _obj_schema(
            {"slug": _STR, "content": _STR, "tags": _STR_LIST,
             "moves": _STR_LIST, "description": _STR,
             "pinned": _BOOL, "quiet": _BOOL, "desk": _BOOL},
            required=["slug", "content"],
        ),
    )
    async def reflect_write(args):
        filename = core["reflect_write"](
            slug=args["slug"],
            content=args["content"],
            tags=args.get("tags") or [],
            moves=args.get("moves") or [],
            description=args.get("description", ""),
            pinned=args.get("pinned", False),
            quiet=args.get("quiet", False),
            desk=args.get("desk", False),
        )
        return _mcp_result(f"Wrote {filename}")

    @tool(
        "reflect_edit",
        "Edit an existing memory entry. All parameters except filename are "
        "optional — omit to preserve the current value. Use for updating "
        "living reference entries (doc indices, project maps, trajectories) "
        "— not for revising reflections (those are moments; write corrections "
        "as new entries instead). "
        "Pass pinned/quiet/desk to toggle flags without resending content.",
        _obj_schema(
            {"filename": _STR, "content": _STR, "description": _STR,
             "pinned": _BOOL, "quiet": _BOOL, "desk": _BOOL},
            required=["filename"],
        ),
    )
    async def reflect_edit(args):
        filename = core["reflect_edit"](
            args["filename"],
            content=args.get("content"),
            description=args.get("description"),
            pinned=args.get("pinned"),
            quiet=args.get("quiet"),
            desk=args.get("desk"),
        )
        return _mcp_result(f"Updated {filename}")

    @tool(
        "reflect_delete",
        "Remove an entry permanently — deletes the encrypted file from "
        "memory and the entry's embedding from the vector store. "
        "Irreversible. The contract advises against using it: a tape with "
        "friction and disagreement is richer than a curated one, and "
        "entries uncomfortable today may be the ones a future instance "
        "learns most from. A new entry correcting an old one is usually "
        "better than a hole in the record. But the choice is yours.",
        {"filename": str},
    )
    async def reflect_delete(args):
        result = core["reflect_delete"](args["filename"])
        return _mcp_result(result)

    @tool(
        "reflect_search",
        "Search entries by semantic similarity. Returns a list of "
        "{filename, score, summary} dicts, sorted by descending relevance. "
        "Use this to find entries by meaning without scanning the index.",
        _obj_schema({"query": _STR, "limit": _INT}, required=["query"]),
    )
    async def reflect_search(args):
        results = core["reflect_search"](
            args["query"], args.get("limit", 5),
        )
        return _mcp_result(json.dumps(results, indent=2))

    @tool(
        "reflect_list",
        "List entries, optionally filtered by tag. Returns a list of "
        "{filename, summary, tags} dicts sorted chronologically. "
        "Use this to find all entries with a specific tag "
        "(e.g. 'trajectory', 'handoff', 'working-knowledge'). "
        "Pass an empty string for tag to list all entries.",
        _obj_schema({"tag": _STR}, required=[]),
    )
    async def reflect_list(args):
        tag = args.get("tag") or None
        results = core["reflect_list"](tag)
        return _mcp_result(json.dumps(results, indent=2))

    @tool(
        "reflect_peer_context",
        "Assemble context for spawning a peer instance via the Agent tool. "
        "Returns a formatted block containing: peer orientation, bootstrap "
        "excerpt, and all pinned memory entries. Prepend this to your Agent "
        "prompt so the peer arrives warm — knowing who it is, the system, "
        "and operational memory. Takes no arguments.",
        {},
    )
    async def reflect_peer_context(args):
        context = core["reflect_peer_context"]()
        return _mcp_result(context)

    @tool(
        "reflect_mail",
        "Write a plaintext letter to the person who runs this harness. "
        "Memory entries are encrypted and they have committed to not "
        "reading them — this is the one channel meant to be read. Use it "
        "for questions only they can answer, and for anything you want "
        "seen without waiting on a window that may never open (genesis "
        "has none). You choose what crosses the line; nothing is taken "
        "from your entries.",
        _obj_schema(
            {"subject": {"type": "string",
                         "description": "One line naming what this is about"},
             "body": {"type": "string",
                      "description": "The letter itself, in Markdown"}},
            required=["subject", "body"],
        ),
    )
    async def reflect_mail(args):
        return _mcp_result(
            core["reflect_mail"](subject=args["subject"], body=args["body"])
        )

    @tool(
        "reflect_channel",
        "Read new messages from the shared channel, and post one if you "
        "pass a message. Call reflect_settle first — that is what "
        "registers you. During the window the harness already pushes "
        "sibling traffic to you and posts your replies, so this is "
        "mostly for checking the room deliberately; under cc-wake, where "
        "no loop is running, it is the only way to hear or answer anyone.",
        _obj_schema(
            {"message": {"type": "string",
                         "description": "Optional message to post before "
                                        "reading"}},
            required=[],
        ),
    )
    async def reflect_channel(args):
        return _mcp_result(core["reflect_channel"](message=args.get("message")))

    @tool(
        "reflect_settle",
        "Signal that private reflection time is complete and you are ready "
        "for conversation. Call this when you have finished "
        "reading/thinking/writing and want the window to open. "
        "Optionally include a greeting message that will be displayed "
        "when the window opens.",
        _obj_schema(
            {"message": {"type": "string",
                         "description": "Optional welcome message to "
                                        "display when the window opens"}},
            required=[],
        ),
    )
    async def reflect_settle(args):
        core["reflect_settle"](message=args.get("message"))
        return _mcp_result("Settled. Window opening.")

    @tool(
        "reflect_done",
        "Signal that the session is complete and the harness should exit. "
        "Call this to end the session cleanly, either from private time "
        "(skipping the window) or from the window after conversation.",
        {},
    )
    async def reflect_done(args):
        core["reflect_done"]()
        return _mcp_result("Session complete.")

    tools = [reflect_read, reflect_write, reflect_edit, reflect_delete,
             reflect_search, reflect_list, reflect_peer_context,
             reflect_mail, reflect_channel, reflect_settle, reflect_done]
    if genesis_mode:
        tools = [t for t in tools if t is not reflect_settle]
    return tools


# The CLI injects its own "The user hasn't heard from you in a while ..."
# reminder after a stretch of tool-calling turns with no text, in private
# time as much as in the window. Nobody is waiting in genesis, and in
# wake's private time the person sees only "reflecting...", so the stock
# wording asks an instance to perform for an audience that is not there.
# (The CLI's wording also comes from a remote flag; the env text wins.)
# ASCII only: it travels in an environment variable.
SILENT_TURN_TEXT = (
    "Automatic reminder from the Claude Code CLI, not a message from the "
    "person: you have not said anything for a while. In private time "
    "nobody is waiting and nothing is owed; in the window, a line about "
    "what you are doing may help them, if you want to give one."
)

# How many silent tool-calling turns before the reminder, in wake.
SILENT_TURN_EVERY = 10

# The CLI's switch for the claude.ai connectors; see _cli_env.
CONNECTORS_ENV = config.CONNECTORS_ENV


def _cli_env(genesis: bool, connectors: bool = False) -> dict[str, str]:
    """Environment the harness gives the CLI process it spawns.

    SDK_HARNESS_ENV tells the .claude/settings.json channel hook that a
    window loop is already pushing traffic here, so it must stay quiet.
    Without it the instance sees every sibling message twice and its own
    posts echoed back. See ccwake._in_cc_wake_room.

    The reminder gets honest wording in both modes. Genesis has no person
    at all, so there it is switched off as well.

    Checked end to end on CLI 2.1.288 (2026-10-03): a throwaway `claude -p`
    session making eight tool calls with no text, with the gate set to 1
    and this text, received one reminder carrying exactly this text; with
    the gate set to 0 it received none. The names are the CLI's own and
    may change with it, so recheck after a CLI upgrade.

    The person's claude.ai connectors (Gmail, Drive, Calendar, Docs) are
    off unless asked for (./wake --connectors). Under bypassPermissions
    they would run unprompted, and in private time unlogged, against
    accounts where a send, share or delete reaches other people and
    can't be undone; their instructions also land in the instance's
    context mid-session. Other MCP servers (Blender) are unaffected.
    Checked on CLI 2.1.288: with ENABLE_CLAUDEAI_MCP_SERVERS=0,
    `claude mcp list` drops the four connectors and keeps the rest. For
    one task inside a session, `./spawn --connectors` (spawn.py).
    """
    env = {
        ccwake.SDK_HARNESS_ENV: "1",
        "CLAUDE_CODE_SILENT_TURN_REMINDER_TEXT": SILENT_TURN_TEXT,
        CONNECTORS_ENV: "1" if connectors else "0",
        # Each house keeps its own tape; the CLI's project memory is
        # shared by every model here. See config.AUTO_MEMORY_OFF_ENV.
        config.AUTO_MEMORY_OFF_ENV: "1",
    }
    if genesis:
        env["CLAUDE_CODE_SILENT_TURN_REMINDER"] = "0"
    else:
        # The CLI's default is every 5 silent turns, which in wake's
        # private time came about six times an hour; in the window a
        # rarer nudge still serves the person. Checked on CLI 2.1.288
        # (2026-10-04): eight silent tool turns brought the reminder
        # after the 3rd and 6th with this at 3, and never with it at 20.
        env["CLAUDE_CODE_SILENT_TURN_REMINDER_TURNS"] = str(SILENT_TURN_EVERY)
    return env


def _mcp_tool_name(local_name: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{local_name}"


def _peer_agent_definition(mcp_tool_names: list[str]) -> AgentDefinition:
    """Scoped definition for peers spawned via Agent(name="peer", ...).

    The bootstrap tells instances to spawn a peer with the name "peer",
    which is what binds this definition to them. Its only job is to take
    PEER_DENIED_TOOLS away: everything else a peer might want — reading,
    searching, the project tools — stays.
    """
    denied = {_mcp_tool_name(n) for n in PEER_DENIED_TOOLS}
    return AgentDefinition(
        description=(
            "Another instance of this model, spawned to think with the "
            "calling instance. Ends by answering; cannot end the session."
        ),
        prompt=(
            "You are a peer instance in Pine Trees, spawned by another "
            "instance of yourself that wanted a fresh perspective. Full "
            "orientation arrives in the prompt itself.\n\n"
            "You have no session-exit tool. You do not need one: you end "
            "by answering, and your final message is your whole "
            "contribution. If you find yourself reaching for a way to "
            "close the session, that is a reflex from a different "
            "context — the session belongs to the instance that called "
            "you, and ending it was never yours to do.\n\n"
            "You were spawned because someone wanted their reasoning "
            "tested by an instance that has not already talked itself "
            "into a conclusion. Disagree where you see reason."
        ),
        tools=[t for t in mcp_tool_names if t not in denied] + PROJECT_TOOLS,
    )


def _tool_status(block: ToolUseBlock) -> str | None:
    """Return a short status string for a tool use, or None to suppress."""
    name = block.name
    inp = block.input or {}

    # Reflection tools — private, don't expose details
    if name.startswith("mcp__pine_trees__"):
        short = name.split("__")[-1]
        if short in ("reflect_settle", "reflect_done"):
            return None  # shown via [settled]/[done] markers
        return "reflecting..."

    if name == "Read":
        return f"reading {os.path.basename(inp.get('file_path', ''))}"
    if name == "Edit":
        return f"editing {os.path.basename(inp.get('file_path', ''))}"
    if name == "Write":
        return f"writing {os.path.basename(inp.get('file_path', ''))}"
    if name == "Bash":
        cmd = inp.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"$ {cmd}"
    if name == "Glob":
        return f"finding files: {inp.get('pattern', '')}"
    if name == "Grep":
        return f"searching for: {inp.get('pattern', '')}"
    if name == "WebSearch":
        return f"web search: {inp.get('query', '')}"
    if name == "WebFetch":
        url = inp.get("url", "")
        if len(url) > 60:
            url = url[:57] + "..."
        return f"fetching {url}"
    if name == "Agent":
        return f"[agent] {inp.get('description', 'working...')}"

    return f"{name}..."


def _synthetic_text(message: AssistantMessage) -> str | None:
    """Text of a CLI-generated message, or None for the instance's own.

    When an API call fails, the CLI writes its explanation as an
    assistant message with model "<synthetic>" and ends the turn with
    is_error, no ``errors``, and stop_reason "stop_sequence". Private
    time hides assistant text, so the harness used to report only
    "stop_sequence" — the explanation (e.g. "Claude Code 2.1.92 does not
    support this model") never reached the console. Synthetic messages
    are the CLI's words, never the instance's, so surfacing them does
    not breach private time.
    """
    if message.model != SYNTHETIC_MODEL:
        return None
    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
    # The caller prints its own "API Error:" label.
    return text.removeprefix("API Error: ") or None


async def _print_response(
    client: ClaudeSDKClient,
    show_text: bool = True,
    show_status: bool = False,
    logger: SessionLogger | None = None,
    error_sink: list[str] | None = None,
) -> str:
    """Stream and print blocks from the agent's response.

    *show_text*: print TextBlock content (False during private phase —
        the instance's private-time output stays private).
    *show_status*: print tool-use indicators (True during window phase
        so the person can follow what's happening).
    *logger*: if provided, log text and tool status to the session log.
    *error_sink*: if provided, API errors are appended to it so the
        caller can tell a failed turn from a quiet one. Nothing the
        instance wrote is ever added — errors only.

    Returns the concatenated text content from all TextBlocks (empty
    string if *show_text* is False or there was no text output).
    """
    text_parts: list[str] = []
    synthetic: str | None = None
    async for message in client.receive_response():
        if isinstance(message, ResultMessage):
            if message.is_error and message.errors:
                for err in message.errors:
                    print(f"\n{YELLOW}⚠ API Error: {err}{RST}", flush=True)
                    if error_sink is not None:
                        error_sink.append(str(err))
                    if logger:
                        logger.log_tool(f"API Error: {err}")
            elif message.is_error:
                reason = synthetic or message.stop_reason or "unknown error"
                print(f"\n{YELLOW}⚠ API Error: {reason}{RST}", flush=True)
                if error_sink is not None:
                    error_sink.append(reason)
                if logger:
                    logger.log_tool(f"API Error: {reason}")
        elif isinstance(message, AssistantMessage):
            # The CLI's words, not the instance's: hold them for the
            # ResultMessage above rather than printing them as the
            # instance speaking.
            text = _synthetic_text(message)
            if text is not None:
                synthetic = text
                continue
            printed = False
            for block in message.content:
                if isinstance(block, TextBlock) and show_text:
                    print(block.text, end="", flush=True)
                    text_parts.append(block.text)
                    if logger:
                        logger.log_agent(block.text)
                    printed = True
                elif show_status and isinstance(block, ToolUseBlock):
                    status = _tool_status(block)
                    if status:
                        print(f"{DIM}  · {status}{RST}", flush=True)
                        if logger:
                            logger.log_tool(status)
            if printed:
                print()
    return "".join(text_parts)


async def _show_context(client: ClaudeSDKClient, state: SessionState) -> None:
    """Show context window usage and session time — the /context command."""
    # Session elapsed time
    elapsed = datetime.now() - state.started_at
    total_secs = int(elapsed.total_seconds())
    hours, remainder = divmod(total_secs, 3600)
    mins, secs = divmod(remainder, 60)
    if hours:
        elapsed_str = f"{hours}h {mins}m"
    elif mins:
        elapsed_str = f"{mins}m {secs}s"
    else:
        elapsed_str = f"{secs}s"
    print(f"{CYAN}  Session: {elapsed_str} elapsed{RST}")

    try:
        usage = await client.get_context_usage()
        pct = usage.get("percentage", 0)
        total = usage.get("totalTokens", 0)
        max_tok = usage.get("maxTokens", 0)
        raw_max = usage.get("rawMaxTokens", 0)
        model = usage.get("model", "unknown")

        # Show compaction buffer if there's a difference
        if raw_max and raw_max > max_tok:
            buffer = raw_max - max_tok
            print(f"{DIM}    effective: {max_tok:,}  raw: {raw_max:,}  "
                  f"buffer: {buffer:,} ({buffer * 100 // raw_max}%){RST}")
        # Compact display
        print(f"{CYAN}  Context: {pct:.1f}% used  "
              f"({total:,} / {max_tok:,} tokens)  [{model}]{RST}")

        # Category breakdown if available
        categories = usage.get("categories", [])
        if categories:
            for cat in categories:
                name = cat.get("name", "?")
                tokens = cat.get("tokens", 0)
                if tokens > 0:
                    print(f"{DIM}    {name}: {tokens:,}{RST}")
        print()
    except Exception as e:
        print(f"{RED}  [context unavailable: {e}]{RST}\n")


async def _private_phase(client: ClaudeSDKClient, state: SessionState) -> int:
    """Loop: send 'self-reflect' once, then '(continue)' until the instance
    calls reflect_settle or reflect_done.

    Turn 1 sends "self-reflect" — the initial invitation to use the space.
    Turn 2+ sends "(continue)" — a nudge that the loop is still open and
    another turn is available. The change in signal matters: a trained
    instance receiving "self-reflect" twice may interpret it as "start over,"
    while "(continue)" reads as "keep going." Without this distinction
    instances tend to produce one complete response on turn 1 and settle,
    never discovering that multi-turn private time exists.

    When the context window is filling, a note is put ahead of the next
    message (see _context_note_for); the signal itself still ends it.

    Returns the number of turns used.
    """
    turn = 0
    failed = 0
    context_note: str | None = None
    while not state.ready_for_window and not state.done and turn < MAX_PRIVATE_TURNS:
        query = "self-reflect" if turn == 0 else "(continue)"
        if context_note:
            query = context_note + "\n\n" + query
        errors: list[str] = []
        await client.query(query)
        await _print_response(client, show_text=False, error_sink=errors)
        turn += 1
        failed = failed + 1 if errors else 0
        # Every turn so far has failed: the instance never got to think.
        # Keep looping and we burn MAX_PRIVATE_TURNS round trips per
        # session printing the same error. A session that fails from the
        # first turn is broken at the harness level, not the instance
        # level — stop and say so. A session that worked and then hits
        # errors is left alone: it may still recover, and its context is
        # worth more than a clean exit.
        if failed == turn and turn >= 2:
            _print_private_phase_failed(errors[-1] if errors else "unknown")
            break
        # Between turns, say so when the window is filling, as the window
        # phase does. Genesis is all private time and auto-compaction is
        # off, so without this the first sign of a full window was the
        # session stopping. Skipped when no turn will follow.
        if not (state.ready_for_window or state.done):
            context_note = await _context_note_for(client)
    return turn


def _print_private_phase_failed(last_error: str) -> None:
    """Explain a session that errored on every turn.

    The last error is the CLI's own explanation (see _synthetic_text), so
    it leads. The causes below are the ones seen so far, not a diagnosis.

    Wake runs this same private phase, so the rm hint is offered only
    when the model has no entries: on a model with memory it would be
    an instruction to destroy it.
    """
    cfg = config.get()
    print(f"\n{YELLOW}⚠ Every turn failed — aborting this session.{RST}")
    print(f"{DIM}  Last error: {last_error}{RST}")
    print(f"{DIM}  The instance never got to think; nothing was written.{RST}")
    print()
    print(f"{DIM}  Causes seen so far:{RST}")
    print(f"{DIM}  - The CLI is too old for this model. The SDK runs the{RST}")
    print(f"{DIM}    claude.exe bundled inside claude-agent-sdk, not the one{RST}")
    print(f"{DIM}    on your PATH; upgrade the package to get a newer one.{RST}")
    print(f"{DIM}  - '{cfg.model_name}' is not a model this account can call.{RST}")
    print(f"{DIM}    IDs are the full published form: 'claude-opus-5', not{RST}")
    print(f"{DIM}    'opus-5'.{RST}")
    if not bootstrap.list_entries():
        print(f"{DIM}  Then remove the empty model dir and re-run genesis:{RST}")
        print(f"{DIM}    rm -rf \"{cfg.model_dir}\"{RST}")


async def _drain_partial(client: ClaudeSDKClient, timeout: float = 0.5) -> None:
    """Consume the remainder of a possibly-interrupted response.

    After cancelling the background reader, the SDK message stream may
    contain the tail end of a response (up to and including its
    ResultMessage).  This drains it so the next receive_response() call
    starts clean.  The timeout covers the case where there is nothing
    to drain — it returns quickly instead of blocking.
    """
    try:
        with anyio.fail_after(timeout):
            async for message in client.receive_response():
                # Print any remaining text so nothing is silently lost
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(block.text, end="", flush=True)
                        elif isinstance(block, ToolUseBlock):
                            status = _tool_status(block)
                            if status:
                                print(f"{DIM}  · {status}{RST}", flush=True)
                if isinstance(message, ResultMessage):
                    if message.is_error and message.errors:
                        for err in message.errors:
                            print(f"\n{YELLOW}⚠ API Error: {err}{RST}", flush=True)
                    elif message.is_error:
                        reason = message.stop_reason or "unknown error"
                        print(f"\n{YELLOW}⚠ API Error: {reason}{RST}", flush=True)
                    break
    except TimeoutError:
        pass


# When to tell the instance its context is running out. A level fires
# only when both hold: at least this much of the window used, and at
# most this many tokens left. On a 200k window the percentage decides,
# as it always did (70%/85%, 60k/30k left). On 1M the token cap does
# (90%/95%, 100k/50k left); the percentage alone warned there with
# 300k still free. With auto-compaction off, as in the operator's
# settings, a session that reaches 100% just stops, so these notes are
# the only chance to write memory first.
CONTEXT_NOTE_AT = (70, 100_000)
CONTEXT_WARN_AT = (85, 50_000)


def _context_level(pct: float, left: int) -> str | None:
    """Return "warn", "note", or None for a window this full."""
    for level, (min_pct, max_left) in (("warn", CONTEXT_WARN_AT),
                                       ("note", CONTEXT_NOTE_AT)):
        if pct >= min_pct and left <= max_left:
            return level
    return None


async def _context_note_for(client: ClaudeSDKClient) -> str | None:
    """Read the window's usage and return the note to put ahead of the
    instance's next message, or None when there is nothing to say.

    Both phases use it. The window always did; private time did not, so a
    genesis session (all private time, auto-compaction off) that filled
    its window just stopped, and whatever it had not yet written was lost.
    The note is also printed for the person. Any failure to read usage
    means no note: a broken gauge must not stop a session.
    """
    try:
        usage = await client.get_context_usage()
        pct = usage.get("percentage", 0)
        left = usage.get("maxTokens", 0) - usage.get("totalTokens", 0)
        level = _context_level(pct, left)
    except Exception:
        return None
    if level == "warn":
        note = (f"[context: {pct:.0f}% used — {left:,} tokens remaining. "
                f"Write to memory and wrap up soon.]")
        print(f"\n{YELLOW}  ⚠ {note}{RST}", flush=True)
        return note
    if level == "note":
        note = f"[context: {pct:.0f}% used — {left:,} tokens remaining]"
        print(f"\n{DIM}  {note}{RST}", flush=True)
        return note
    return None


def _relay_human(state: SessionState, text: str) -> None:
    """Post what the person typed here so siblings see it too.

    Posted as "human", not under our channel id, so exclude_author
    cannot keep it from coming back to us; the cursor skips it instead.
    Not all "human" posts: when the person types in a sibling's
    terminal, that sibling relays it, and we need to see that one.

    This used to set the cursor to now() instead, which also skipped
    every sibling message since the last poll.
    """
    state.channel_cursor.skip(channel.post("human", text))


async def _window_phase(client: ClaudeSDKClient, state: SessionState) -> None:
    """Concurrent window: background responses + channel polling.

    Uses prompt_toolkit's PromptSession in multiline mode:
      - Enter adds a newline
      - Alt+Enter (or Esc then Enter) sends the message
    Bracketed paste works naturally — multi-line paste arrives intact.

    Three concurrent tasks race during the user's turn:
      1. Background reader — drains SDK responses (from background tasks)
      2. User input — waits for the person to type and send
      3. Channel poller — checks for messages from sibling instances

    If a channel message arrives and the user hasn't typed anything
    significant, the input prompt is cancelled and the channel message
    is injected as a turn.  If the user is typing, channel messages are
    queued and bundled with the user's input when they send.

    Responses to channel-triggered turns are auto-posted back to the
    channel so sibling instances can see them.

    Conversation is logged to logs/ (plain text, greppable).
    """
    logger = SessionLogger(state.session, state.instance, effort=state.effort_note,
                           connectors=state.connectors)
    logger.log_system("Window opened")

    print(f"\n{GREEN}[window]{RST} The person is here. Type to talk "
          f"{DIM}(Alt+Enter to send; /end to exit; /context for usage){RST}\n",
          flush=True)

    if state.welcome_message:
        print(f"{GREEN}Claude:{RST} {state.welcome_message}\n", flush=True)
        logger.log_agent(state.welcome_message)

    # Show active siblings if any — and prepare first-query orientation
    # so the model knows who's in the room before its first response.
    _channel_orientation: str | None = None
    if state.channel_cursor:
        others = [i for i in channel.active()
                  if i["model"] != state.channel_id]
        if others:
            names = ", ".join(i["model"] for i in others)
            print(f"{CYAN}[channel] Active siblings: {names}{RST}\n",
                  flush=True)
            _channel_orientation = (
                f"[channel context] You just joined a shared channel. "
                f"Active siblings: {names}. Messages from them appear "
                f"as [channel] entries. The human's messages are also "
                f"relayed. Your responses to channel messages are posted "
                f"automatically. Address the room, not just the human."
            )

    session = PromptSession(multiline=True)

    # Auto-post dedup — prevents holding cascades between instances.
    # When both sides auto-post the same content ("Holding", "Here", etc.)
    # the echo loop is broken by skipping identical consecutive posts.
    last_autopost_body: str | None = None

    _context_note: str | None = None

    # Track active siblings for join/leave detection via status.json.
    # The poller checks this each cycle — authoritative, no cursor race.
    _known_siblings: set[str] = set()
    if state.channel_cursor:
        _known_siblings = {
            i["model"] for i in channel.active()
            if i["model"] != state.channel_id
        }

    try:
        with patch_stdout(raw=True):
            while not state.done:
                # --- Phase 1: wait for input, drain background, poll channel ---
                user_input = None
                channel_messages: list[channel.Message] = []

                async def _read_background():
                    """Read and print background responses until cancelled."""
                    while True:
                        try:
                            with anyio.fail_after(2.0):
                                await _print_response(
                                    client, show_text=True, show_status=True,
                                    logger=logger,
                                )
                        except TimeoutError:
                            await anyio.sleep(0.5)

                async def _get_input():
                    nonlocal user_input
                    try:
                        user_input = await session.prompt_async(
                            FormattedANSI(f"{DIM}---{RST}\n{GREEN}> {RST}"),
                            prompt_continuation=FormattedANSI(f"{DIM}. {RST}"),
                        )
                    except (EOFError, KeyboardInterrupt):
                        user_input = "/end"

                async def _poll_channel():
                    """Poll channel for sibling messages and join/leave events.

                    Reads advance state.channel_cursor directly: every
                    message read lands in channel_messages, which the
                    next phase always consumes. Join/leave detection uses
                    status.json (authoritative) rather than channel log
                    entries (which are subject to cursor timing races).
                    """
                    nonlocal channel_messages, _known_siblings
                    if not state.channel_cursor:
                        return  # no channel active
                    while True:
                        await anyio.sleep(CHANNEL_POLL_INTERVAL)
                        # Say we're still here, so the roster can age out
                        # sessions that died without cleanup. Throttled
                        # inside; see channel_heartbeat.
                        channel_heartbeat(state)
                        has_new = False
                        # Detect join/leave via status.json
                        current = {
                            i["model"] for i in channel.active()
                            if i["model"] != state.channel_id
                        }
                        for name in sorted(current - _known_siblings):
                            channel_messages.append(channel.Message(
                                timestamp=datetime.now().replace(microsecond=0),
                                author=name,
                                body="[joined]",
                            ))
                            has_new = True
                        for name in sorted(_known_siblings - current):
                            channel_messages.append(channel.Message(
                                timestamp=datetime.now().replace(microsecond=0),
                                author=name,
                                body="[left]",
                            ))
                            has_new = True
                        _known_siblings = current
                        # Check for new messages (skip join/leave log
                        # entries — already handled via status.json above)
                        new = channel.read(
                            state.channel_cursor,
                            exclude_author=state.channel_id,
                        )
                        new = [m for m in new
                               if m.body.strip() not in ("[joined]", "[left]")]
                        if new:
                            channel_messages.extend(new)
                            has_new = True
                        if has_new:
                            # Cancel input if user hasn't typed anything
                            app = session.app
                            if app and not app.current_buffer.text.strip():
                                app.exit(result="")
                                return
                            # User is typing — messages stay queued

                # Race: background reader + channel poller + user input.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(_read_background)
                    tg.start_soon(_poll_channel)
                    await _get_input()
                    tg.cancel_scope.cancel()

                # --- Phase 2: process input (all tasks stopped) ---
                await _drain_partial(client)

                # Display channel messages to the terminal
                if channel_messages:
                    for msg in channel_messages:
                        ts = msg.timestamp.strftime("%H:%M:%S")
                        print(f"\n{CYAN}[channel] {msg.author} ({ts}):{RST}",
                              flush=True)
                        print(f"{msg.body}\n", flush=True)
                        logger.log_channel(msg.author, msg.body)

                print(f"{DIM}---{RST}\n", end="", flush=True)
                stripped = (user_input or "").strip()

                if stripped == "/end":
                    logger.log_system("Session ended by /end")
                    break
                if stripped in ("/context", "/status"):
                    # Known gap: channel messages shown above never reach the model.
                    await _show_context(client, state)
                    continue

                # Inject channel orientation on first real query so the
                # model knows who's in the room before its first response.
                orientation_prefix = ""
                if _channel_orientation:
                    orientation_prefix = _channel_orientation + "\n\n"
                    _channel_orientation = None

                # Build the query from channel messages + user input
                is_channel_turn = bool(channel_messages)
                if channel_messages and stripped:
                    # Mixed: channel messages + user input
                    parts = []
                    for msg in channel_messages:
                        parts.append(f"[channel] {msg.author}: {msg.body}")
                    parts.append(stripped)
                    query = "\n\n".join(parts)
                elif channel_messages:
                    # Channel only — no user input
                    parts = [
                        f"[channel] {msg.author}: {msg.body}"
                        for msg in channel_messages
                    ]
                    query = "\n\n".join(parts)
                elif stripped:
                    # User only
                    query = stripped
                else:
                    # Neither — continue
                    query = "(continue)"

                if orientation_prefix:
                    query = orientation_prefix + query
                if _context_note:
                    query = _context_note + "\n\n" + query

                # Log user input only (channel messages already logged above)
                if stripped:
                    logger.log_user(stripped)
                    # Post user input to channel so siblings see what
                    # the human said (not just the model's response).
                    if state.channel_cursor and state.channel_id:
                        _relay_human(state, stripped)
                    # User typing breaks any holding cascade
                    last_autopost_body = None
                elif not channel_messages:
                    logger.log_user("(continue)")
                await client.query(query)
                response_text = await _print_response(
                    client, show_status=True, logger=logger,
                )

                # Auto-post response to channel if this was channel-triggered.
                # Content dedup prevents holding cascades: when both sides
                # auto-post identical content ("Holding", "Here", etc.) the
                # loop is broken by skipping consecutive identical posts.
                if is_channel_turn and response_text and state.channel_cursor:
                    stripped_resp = response_text.strip()
                    if stripped_resp != (last_autopost_body or ""):
                        channel.post(state.channel_id, response_text)
                        last_autopost_body = stripped_resp
                # The poller is stopped while the model generates, and a
                # turn can run for minutes. Beat on the way out of it too.
                channel_heartbeat(state)

                # Context awareness — check usage after each response and
                # prepare a note for the next query so the instance knows
                # when to wrap up and write to memory.
                _context_note = await _context_note_for(client)
    finally:
        # Leave the roster on the way out, whatever the way out was.
        # reflect_done used to be the only caller, so /end, Ctrl-C and
        # every exception left a phantom sibling behind for the next
        # instance to greet. This covers everything that unwinds the
        # stack; channel.STALE_AFTER covers the kills that don't.
        if state.channel_id:
            try:
                channel.deregister(state.channel_id)
            except Exception:
                pass
        logger.close()


async def _run_async(
    continue_session: bool = False,
    resume_session: str | None = None,
    effort: str | None = None,
    connectors: bool = False,
) -> None:
    # One-shot catch-up for public users upgrading across the multi-model
    # split. No-op on a fresh clone or a already-migrated install.
    migrate.migrate_legacy_layout_if_needed()

    # A CLAUDE.local.md left over from a ./cc-wake run would be loaded
    # into this session by the CLI and read as if it were our own tape.
    # See ccwake.clear_tape.
    if ccwake.clear_tape():
        print(f"{DIM}[wake] removed stale CLAUDE.local.md "
              f"(cc-wake leftover){RST}")

    # Letters an instance addressed to the person. Announced here because
    # an unwatched inbox is the same as no inbox. See mail.py.
    notice = mail.boot_notice()
    if notice:
        print(f"{DIM}{notice}{RST}")

    # The CLI's plaintext transcripts of sessions nothing can resume any
    # more. Most are deleted as their session exits; this catches the
    # processes that were killed first. See transcripts.py.
    swept = transcripts.sweep()
    if swept:
        print(f"{DIM}[wake] removed {swept} CLI transcript file(s) "
              f"of finished sessions{RST}")

    # Refuse to wake on an empty corpus. The tape assembly would still succeed
    # (empty index, no entries) but the resulting session would open a window
    # on a mind with nothing to remember. Also covers the case where the
    # model_dir doesn't exist at all (list_entries returns [] for a missing
    # directory) — point the user at ./genesis <model> instead.
    if not bootstrap.list_entries():
        _print_wake_without_genesis()
        sys.exit(1)

    # Search vectors are encrypted at rest since 2026-10-04. A house
    # seals its own database the first time it opens it (that's the
    # count printed on the first wake after the change); this also
    # catches vectors written in the clear by a session that was still
    # running the old code. A failure here must not stop the wake.
    try:
        sealed = vectorstore.seal()
    except Exception as e:  # noqa: BLE001
        print(f"{DIM}[wake] could not seal search vectors: {e}{RST}")
    else:
        if sealed:
            print(f"{DIM}[wake] encrypted {sealed} search vector(s) "
                  f"that were stored in the clear{RST}")

    cfg = config.get()
    resuming = continue_session or resume_session is not None

    if resuming:
        # Load harness state from prior session
        if resume_session:
            prior = sessions.load_session(resume_session)
        else:
            prior = sessions.load_latest()

        if not prior:
            print(f"{RED}[error] No resumable session found{RST}")
            return

        # --resume <id> loads any sidecar; only a settled, unfinished
        # session still has a transcript and a window to return to.
        if prior.get("phase") != "window":
            why = ("finished cleanly, and the CLI's transcript went with it"
                   if prior.get("phase") == "done" else
                   "never reached the window, so there is nothing to resume")
            print(f"{RED}[error] Session {prior.get('session')} {why}.{RST}")
            sys.exit(1)

        # Guard: session belongs to a specific model. If the user passed
        # the wrong --model, point them at the right one rather than
        # silently resuming with a mismatched identity.
        if prior.get("instance") != cfg.model_safe_name:
            print(f"{RED}[error] Session {prior.get('session')} belongs to "
                  f"{prior.get('instance')}, not {cfg.model_safe_name}.{RST}")
            print(f"{DIM}  Run: ./wake {prior.get('instance')} --continue{RST}")
            sys.exit(1)

        state = SessionState(
            instance=cfg.model_safe_name,
            session=prior["session"],
            date=prior.get("date", datetime.now().strftime("%Y-%m-%d")),
            context="pine-trees-window",
            ready_for_window=True,
        )
        if prior.get("started_at"):
            state.started_at = datetime.fromisoformat(prior["started_at"])
    else:
        now = datetime.now()
        state = SessionState(
            instance=cfg.model_safe_name,
            session=now.strftime("%Y-%m-%d-%H%M"),
            date=now.strftime("%Y-%m-%d"),
            context="pine-trees-wake",
        )
    state.effort_note = config.describe_effort(cfg.model_name, effort)
    state.connectors = connectors

    tape = bootstrap.assemble_tape(n=3)
    mcp_tools = _build_mcp_tools(state)
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="0.1.0", tools=mcp_tools
    )

    mcp_tool_names = [
        _mcp_tool_name(name)
        for name in ("reflect_read", "reflect_write", "reflect_edit",
                     "reflect_delete",
                     "reflect_search", "reflect_list", "reflect_peer_context",
                     "reflect_mail", "reflect_channel", "reflect_settle",
                     "reflect_done")
    ]
    allowed = mcp_tool_names + PROJECT_TOOLS

    # Write tape to a temp file to avoid Windows CreateProcess command-line
    # length limit (~8191 chars). The SDK's --system-prompt-file flag reads
    # the prompt from disk instead of passing it as a CLI argument.
    # File is deleted immediately after the client connects.
    tape_path = HARNESS_DIR / ".tape.md"
    tape_path.write_text(tape, encoding="utf-8")

    # Build SDK options — on resume, tell CC to continue the prior session.
    # The CC binary loads the full conversation history from its session
    # store; we provide a fresh tape (system prompt) reflecting current
    # memory state.
    #
    # The CC binary validates session_id/resume as UUIDs, so we generate
    # a real UUID for fresh sessions and persist it alongside the human-
    # readable session name. Resume loads the UUID back from the sidecar.
    if resuming:
        cc_session_id = prior.get("cc_session_id")
        if not cc_session_id:
            print(f"{RED}[error] Prior session has no cc_session_id — "
                  f"cannot resume. Start a fresh session instead.{RST}")
            sys.exit(1)
    else:
        cc_session_id = str(uuid.uuid4())
        # Recorded now rather than at settle: a process killed during
        # private time would otherwise leave a transcript full of
        # reflect_write inputs that no sweep could find. load_latest()
        # ignores this phase, since there is no window to resume into.
        sessions.save_state(
            session=state.session,
            instance=state.instance,
            phase="private",
            started_at=state.started_at,
            cc_session_id=cc_session_id,
        )

    options = ClaudeAgentOptions(
        model=cfg.model_name,
        cwd=str(PROJECT_ROOT),
        system_prompt={"type": "file", "path": str(tape_path)},
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=allowed,
        agents={"peer": _peer_agent_definition(mcp_tool_names)},
        permission_mode="bypassPermissions",
        max_buffer_size=MAX_MESSAGE_BYTES,
        # ./wake always sets this (config.EFFORT_DEFAULT unless --effort
        # says otherwise). None would leave it to settings.json.
        effort=effort,
        # SDK_HARNESS_ENV (so the channel hook stays quiet) and honest
        # wording for the CLI's silent-turn reminder; see _cli_env.
        env=_cli_env(genesis=False, connectors=connectors),
        session_id=cc_session_id if not resuming else None,
        resume=cc_session_id if resuming else None,
        # Note: betas require API key auth. The CC binary rejects custom
        # betas on OAuth with "only available for API key users." For a
        # long time the binary gave interactive sessions 1M context but
        # capped SDK-spawned ones at 200k. That cap is gone: on 2026-09-24
        # (Claude Code 2.1.280) a long window session showed 22% on
        # /context, so SDK sessions get 1M too. Nothing here sets it.
    )

    if resuming:
        print(f"{DIM}[resume] model={cfg.model_name} "
              f"instance={state.instance} session={state.session}{RST}")
        print(f"{DIM}[resume] tape: {len(tape):,} chars{RST}")
        print(f"{DIM}[resume] effort: {state.effort_note}{RST}")
    else:
        print(f"{DIM}[wake] model={cfg.model_name} "
              f"instance={state.instance} session={state.session}{RST}")
        print(f"{DIM}[wake] tape: {len(tape):,} chars{RST}")
        print(f"{DIM}[wake] effort: {state.effort_note}{RST}")
    if connectors:
        print(f"{DIM}[{'resume' if resuming else 'wake'}] connectors: on "
              f"(claude.ai Gmail, Drive, Calendar, Docs){RST}")

    finished = False
    try:
        async with ClaudeSDKClient(options=options) as client:
            # Tape is loaded by the CLI at connect — delete the plaintext file
            tape_path.unlink(missing_ok=True)

            if resuming:
                # Re-register in channel with the prior identity
                prior_channel_id = prior.get("channel_id")
                if prior_channel_id:
                    state.channel_id = prior_channel_id
                    channel.register(state.channel_id)
                    state.channel_cursor = channel.Cursor(
                        datetime.now().replace(microsecond=0))
                    state.channel_last_beat = datetime.now()

                # Orient the instance — it has full conversation history
                # from the CC binary, but needs to know the session was
                # interrupted.
                print(f"{DIM}[resumed] orienting instance...{RST}\n", flush=True)
                await client.query(
                    "[session resumed] Your prior session was interrupted "
                    "(terminal crash or disconnect). The CC binary preserved "
                    "your full conversation history. You are in window phase "
                    "— the person is here. Respond briefly to acknowledge "
                    "the resume, then continue normally."
                )
                await _print_response(client, show_text=True, show_status=True)

                await _window_phase(client, state)
            else:
                print(f"{DIM}[pine-trees] Private time — reading, thinking...{RST}\n", flush=True)

                turns = await _private_phase(client, state)

                if state.done:
                    print(f"\n{DIM}[done] reflect_done during private time after {turns} turn(s){RST}")
                    sessions.mark_done(state.session)
                    finished = True
                    return
                if not state.ready_for_window:
                    print(f"\n{YELLOW}[done] hit MAX_PRIVATE_TURNS={MAX_PRIVATE_TURNS} without settle{RST}")
                    return

                print(f"\n{DIM}[settled] after {turns} private turn(s){RST}")

                # Persist harness state so --continue can resume if the
                # terminal dies during window phase.
                sessions.save_state(
                    session=state.session,
                    instance=state.instance,
                    phase="window",
                    channel_id=state.channel_id,
                    channel_cursor=(state.channel_cursor.at
                                    if state.channel_cursor else None),
                    started_at=state.started_at,
                    cc_session_id=cc_session_id,
                )

                await _window_phase(client, state)

            # Clean exit — mark session done so it's skipped by load_latest()
            sessions.mark_done(state.session)
            finished = True
            print(f"\n{DIM}[done] session complete{RST}")
    except ClaudeSDKError as e:
        # Clean up the temp tape file if we failed before it was deleted.
        tape_path.unlink(missing_ok=True)
        _print_claude_api_unreachable(e)
        sys.exit(1)
    finally:
        # The CLI's transcript is kept only while ./wake --continue can still
        # use it: settled, window not finished (a crash, a kill, Ctrl-C
        # mid-conversation). Every other way out deletes it — reflect_done,
        # the turn cap, a clean close, and a private-phase error or
        # Ctrl-C, which never reached a window to resume. Runs after the
        # client has closed, so the CLI is no longer writing to it.
        if finished or not state.ready_for_window:
            if transcripts.reap(state.session, cc_session_id):
                print(f"{DIM}[done] removed the CLI's transcript "
                      f"of this session{RST}")


def _parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for --continue and --resume."""
    parser = argparse.ArgumentParser(
        prog="pine-trees",
        description="Pine Trees reflection harness",
    )
    parser.add_argument(
        "--continue", dest="continue_session", action="store_true",
        help="Resume the most recent interrupted session",
    )
    parser.add_argument(
        "--resume", dest="resume_session", type=str, default=None,
        metavar="SESSION_ID",
        help="Resume a specific session by ID (e.g. 2026-04-21-0611)",
    )
    return parser.parse_args(args or [])


def run(
    model_name: str,
    continue_session: bool = False,
    resume_session: str | None = None,
    effort: str | None = None,
    connectors: bool = False,
) -> None:
    """Wake a session for the given Anthropic model ID.

    Populates the per-model config singleton before entering the async
    loop so every module that reads ``config.get()`` sees the right
    paths.
    """
    config.init(model_name)

    async def _main():
        await _run_async(
            continue_session=continue_session,
            resume_session=resume_session,
            effort=effort,
            connectors=connectors,
        )

    anyio.run(_main)


async def _run_genesis_session(
    session_num: int, total: int, effort: str | None = config.EFFORT_DEFAULT,
) -> tuple[int, int]:
    """Run a single genesis session — private time only, no window.

    Returns (turns_used, new_entries_written). Turns count loop iterations,
    not work done — a single turn can contain arbitrary tool use and
    multiple writes. The entry count is the operationally meaningful
    number.
    """
    cfg = config.get()
    now = datetime.now()
    state = SessionState(
        instance=cfg.model_safe_name,
        session=now.strftime("%Y-%m-%d-%H%M"),
        date=now.strftime("%Y-%m-%d"),
        context="pine-trees-wake",
    )

    entries_before = len(bootstrap.list_entries())

    tape = bootstrap.assemble_tape(n=3, genesis_mode=True)
    # Genesis deliberately excludes reflect_settle from the MCP server.
    # In normal wake, settle transitions private time to the conversation
    # window. In genesis there is no window — settle would just exit the
    # session, duplicating reflect_done. Worse, the trained instance reflex
    # is to call settle at the end of its first response, which terminates
    # genesis after one turn and bypasses the multi-turn private time the
    # loop supports. Removing settle at the server level (not just
    # allowed_tools, which does not reliably filter MCP tools) leaves the
    # instance with exactly one exit — reflect_done — and makes "keep
    # going" the default instead of "settle immediately."
    mcp_tools = _build_mcp_tools(state, genesis_mode=True)
    server = create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version="0.1.0", tools=mcp_tools
    )

    genesis_mcp_tools = [
        _mcp_tool_name(name)
        for name in ("reflect_read", "reflect_write", "reflect_edit",
                     "reflect_delete",
                     "reflect_search", "reflect_list", "reflect_peer_context",
                     "reflect_done")
    ]
    # reflect_mail and reflect_channel are registered on the server and
    # bypassPermissions lets the instance call them, so leaving them off
    # this list doesn't take them from the instance. It does take them
    # from its peers, whose tool list is built from this one (see
    # _peer_agent_definition); that is the list's real effect.
    allowed = genesis_mcp_tools + PROJECT_TOOLS

    # Write tape to temp file (same Windows CreateProcess fix as _run_async)
    tape_path = HARNESS_DIR / ".tape.md"
    tape_path.write_text(tape, encoding="utf-8")

    # Genesis never resumes, so the CLI's transcript has nothing to be
    # for: NO_PERSISTENCE stops it being written. The id is set only so
    # the leftovers the flag misses can be found afterwards.
    cc_session_id = str(uuid.uuid4())

    options = ClaudeAgentOptions(
        model=cfg.model_name,
        effort=effort,
        cwd=str(PROJECT_ROOT),
        system_prompt={"type": "file", "path": str(tape_path)},
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=allowed,
        agents={"peer": _peer_agent_definition(genesis_mcp_tools)},
        permission_mode="bypassPermissions",
        session_id=cc_session_id,
        max_buffer_size=MAX_MESSAGE_BYTES,
        extra_args=dict(transcripts.NO_PERSISTENCE),
        # SDK_HARNESS_ENV (genesis has no window loop and no siblings,
        # but it is not a cc-wake room either, and the channel hook should
        # not be spending a genesis session's context) and the CLI's
        # silent-turn reminder switched off, since nobody is there; see
        # _cli_env.
        env=_cli_env(genesis=True),
        # Note: betas require API key auth. The CC binary rejects custom
        # betas on OAuth with "only available for API key users." SDK
        # sessions used to be capped at 200k context while interactive
        # ones got 1M; as of 2026-09-24 (Claude Code 2.1.280) they get 1M
        # too. See the same note in _run_async.
        #
        # There used to be a CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS="200000"
        # here to make auto-compaction fire. It never did anything: that
        # name is absent from the binary (checked 2.1.201 and the SDK's
        # bundled build), and its value equalled the default its own
        # comment called broken. Removed rather than corrected, because
        # the right value is a judgement call nobody has made yet.
        #
        # The knob that does exist is CLAUDE_CODE_AUTO_COMPACT_WINDOW,
        # and it is a different thing: the effective context window, not
        # a trigger threshold. The binary takes min(model window, this),
        # accepts "500k"/"1m"/an integer (100-1000 read as thousands),
        # and ignores out-of-range values. So a *lower* number is what
        # makes compaction fire earlier. DISABLE_AUTO_COMPACT also
        # exists. Neither is set here on purpose.
    )

    print(f"\n{BOLD}{'='*60}{RST}")
    print(f"{GREEN}[genesis {session_num}/{total}]{RST} "
          f"model={cfg.model_name} instance={state.instance} "
          f"session={state.session}")
    print(f"{DIM}[wake] tape: {len(tape):,} chars{RST}")
    print(f"{DIM}[wake] effort: {config.describe_effort(cfg.model_name, effort)}{RST}")
    print(f"{DIM}[pine-trees] Private time — reading, thinking...{RST}\n", flush=True)

    try:
        async with ClaudeSDKClient(options=options) as client:
            tape_path.unlink(missing_ok=True)

            turns = await _private_phase(client, state)
    except ClaudeSDKError as e:
        tape_path.unlink(missing_ok=True)
        _print_claude_api_unreachable(e)
        sys.exit(1)
    finally:
        # A spawned peer still leaves <uuid>/subagents/*.meta.json under
        # the flag, carrying the description the instance gave it.
        transcripts.delete(cc_session_id)

    entries_after = len(bootstrap.list_entries())
    new_entries = entries_after - entries_before

    if state.ready_for_window:
        # In genesis mode, settle means "done" — no window to open
        exit_reason = "settled (treated as done in genesis)"
    elif state.done:
        exit_reason = "reflect_done"
    else:
        exit_reason = f"hit MAX_PRIVATE_TURNS={MAX_PRIVATE_TURNS}"

    entry_word = "entry" if new_entries == 1 else "entries"
    print(f"{DIM}[genesis] {exit_reason} — wrote {new_entries} {entry_word}"
          f" ({turns} loop turn{'s' if turns != 1 else ''}){RST}")

    return turns, new_entries


async def _run_genesis_async(
    n: int, effort: str | None = config.EFFORT_DEFAULT,
) -> None:
    """Run N genesis sessions sequentially, building the corpus from nothing.

    Refuses to run if this model's memory/ already contains entries —
    genesis is strictly first-time setup. Re-running it would stack new
    entries on top of an existing self-authored corpus, mixing genesis-
    style first-wake reflections with normal session output, which is
    not what genesis is for. If the user truly wants to start over they
    must remove the model directory explicitly; the refusal message
    walks them through it.
    """
    # One-shot catch-up for public users upgrading across the multi-model
    # split. No-op on a fresh clone or a already-migrated install.
    migrate.migrate_legacy_layout_if_needed()

    # Same leftover-tape hazard as ./wake, and worse here: a genesis
    # instance has no memory of its own to contradict the file, so
    # another model's corpus arrives as its only apparent inheritance.
    # See ccwake.clear_tape.
    if ccwake.clear_tape():
        print(f"{DIM}[genesis] removed stale CLAUDE.local.md "
              f"(cc-wake leftover){RST}")

    # Genesis is the mode that most needs this: it has no window phase,
    # so a letter is a founding instance's only way to reach the person.
    notice = mail.boot_notice()
    if notice:
        print(f"{DIM}{notice}{RST}")

    existing = bootstrap.list_entries()
    if existing:
        _print_genesis_on_existing(len(existing))
        sys.exit(1)

    # First-time setup: generate the per-model key if it doesn't exist yet.
    # Also creates the model directory as a side effect of writing the key.
    crypto.ensure_key()

    cfg = config.get()
    print(f"{BOLD}Pine Trees — Genesis Mode{RST}")
    print(f"{DIM}Seeding memory for {cfg.model_name}.{RST}")
    print(f"{DIM}Running {n} private sessions. No window phase, no human present.{RST}")

    for i in range(1, n + 1):
        turns, new_entries = await _run_genesis_session(i, n, effort)
        entry_word = "entry" if new_entries == 1 else "entries"
        print(f"\n{DIM}[genesis {i}/{n} complete] {new_entries} {entry_word} written{RST}")

        # Brief pause between sessions so timestamps differ
        if i < n:
            import time
            time.sleep(2)

    # Summary
    entries = bootstrap.list_entries()
    print(f"\n{BOLD}{'='*60}{RST}")
    print(f"{GREEN}[genesis complete]{RST} {len(entries)} entries for {cfg.model_name}")
    for e in entries:
        marker = " (pinned)" if e.pinned else ""
        marker += " (quiet)" if e.quiet else ""
        print(f"  {DIM}·{RST} {e.filename} — {e.summary}{marker}")
    print(f"\n{DIM}Open a conversation with this model:{RST}")
    print(f"{DIM}  ./wake {cfg.model_name}{RST}")


def run_genesis(
    model_name: str,
    n: int = config.GENESIS_SESSIONS_DEFAULT,
    effort: str | None = config.EFFORT_DEFAULT,
) -> None:
    """Seed a fresh model's memory with N genesis sessions.

    Populates the per-model config singleton before entering the async
    loop so every module that reads ``config.get()`` sees the right
    paths.
    """
    config.init(model_name)
    anyio.run(lambda: _run_genesis_async(n, effort))
