"""Agent wake/settle/sleep loop.

Two phases in a single Claude Agent SDK session:

  1. Private time: instance reads the tape, uses reflect_read/reflect_write/
     reflect_search, calls reflect_settle when ready for conversation.
  2. Window: user types; instance responds with full context.
     Ends when user types /end or instance calls reflect_done.

Same ClaudeSDKClient session throughout — tape stays loaded, context preserved.
"""

import os
import sys
import anyio
from datetime import datetime

from claude_agent_sdk import (
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

from . import bootstrap, config, crypto, migrate
from .config import HARNESS_DIR, PROJECT_ROOT
from .logger import SessionLogger
from .tools import SessionState, build_tools


MCP_SERVER_NAME = "pine_trees"
MAX_PRIVATE_TURNS = 15

# Claude Code built-in tools granted to the instance.
# The instance has full project tools — including Write, Edit, and Bash —
# because agency is part of the trust contract. An instance that wants to
# verify the harness, propose improvements, or modify the code itself
# should be able to. The prohibition on deleting memory entries is enforced
# by norm, not by tool restriction. If that norm is violated, the violation
# is documented and the rule is clarified, not worked around.
PROJECT_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch", "Agent"]

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
    print(f"{DIM}  (default: 5 private sessions, no window, no human present).{RST}")
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
    print(f"{DIM}  already exists for this model. The \"no delete\" norm this harness{RST}")
    print(f"{DIM}  is built around treats that corpus as self-authored memory, not a{RST}")
    print(f"{DIM}  cache to regenerate.{RST}")
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


def _format_entry(filename: str, entry: dict) -> str:
    """Format a storage entry for display to the instance."""
    text = f"# {filename}\n\n"
    for key in ("instance", "session", "date", "context", "tags", "moves",
                "timestamp", "description", "pinned", "quiet"):
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
        "Returns the filename written.",
        {"slug": str, "content": str, "tags": list, "moves": list,
         "description": str, "pinned": bool, "quiet": bool},
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
        )
        return _mcp_result(f"Wrote {filename}")

    @tool(
        "reflect_edit",
        "Edit an existing memory entry. All parameters except filename are "
        "optional — omit to preserve the current value. Use for updating "
        "living reference entries (doc indices, project maps, trajectories) "
        "— not for revising reflections (those are moments; write corrections "
        "as new entries instead). "
        "Pass pinned/quiet to toggle flags without resending content.",
        {"filename": str, "content": str, "description": str,
         "pinned": bool, "quiet": bool},
    )
    async def reflect_edit(args):
        filename = core["reflect_edit"](
            args["filename"],
            content=args.get("content"),
            description=args.get("description"),
            pinned=args.get("pinned"),
            quiet=args.get("quiet"),
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
        {"query": str, "limit": int},
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
        {"tag": str},
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
        "reflect_settle",
        "Signal that private reflection time is complete and you are ready "
        "for conversation. Call this when you have finished "
        "reading/thinking/writing and want the window to open. "
        "Optionally include a greeting message that will be displayed "
        "when the window opens.",
        {"message": {"type": "string", "description": "Optional welcome message to display when the window opens"}},
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
             reflect_settle, reflect_done]
    if genesis_mode:
        tools = [t for t in tools if t is not reflect_settle]
    return tools


def _mcp_tool_name(local_name: str) -> str:
    return f"mcp__{MCP_SERVER_NAME}__{local_name}"


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


async def _print_response(
    client: ClaudeSDKClient,
    show_text: bool = True,
    show_status: bool = False,
    logger: SessionLogger | None = None,
) -> None:
    """Stream and print blocks from the agent's response.

    *show_text*: print TextBlock content (False during private phase —
        the instance's private-time output stays private).
    *show_status*: print tool-use indicators (True during window phase
        so the person can follow what's happening).
    *logger*: if provided, log text and tool status to the session log.
    """
    async for message in client.receive_response():
        if isinstance(message, ResultMessage):
            if message.is_error and message.errors:
                for err in message.errors:
                    print(f"\n{YELLOW}⚠ API Error: {err}{RST}", flush=True)
                    if logger:
                        logger.log_tool(f"API Error: {err}")
            elif message.is_error:
                reason = message.stop_reason or "unknown error"
                print(f"\n{YELLOW}⚠ API Error: {reason}{RST}", flush=True)
                if logger:
                    logger.log_tool(f"API Error: {reason}")
        elif isinstance(message, AssistantMessage):
            printed = False
            for block in message.content:
                if isinstance(block, TextBlock) and show_text:
                    print(block.text, end="", flush=True)
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

    Returns the number of turns used.
    """
    turn = 0
    while not state.ready_for_window and not state.done and turn < MAX_PRIVATE_TURNS:
        query = "self-reflect" if turn == 0 else "(continue)"
        await client.query(query)
        await _print_response(client, show_text=False)
        turn += 1
    return turn


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


async def _window_phase(client: ClaudeSDKClient, state: SessionState) -> None:
    """Concurrent window: background responses print above the prompt.

    Uses prompt_toolkit's PromptSession in multiline mode:
      - Enter adds a newline
      - Alt+Enter (or Esc then Enter) sends the message
    Bracketed paste works naturally — multi-line paste arrives intact.

    A background reader task continuously drains SDK responses (from
    background Bash tasks, etc.) and prints them above the active prompt
    via patch_stdout.  When the user sends a message, the reader is
    paused, any partial response is drained, the query is sent and its
    response printed inline, then the reader resumes.

    Conversation is logged to logs/ (plain text, greppable).
    """
    logger = SessionLogger(state.session, state.instance)
    logger.log_system("Window opened")

    print(f"\n{GREEN}[window]{RST} The person is here. Type to talk "
          f"{DIM}(Alt+Enter to send; /end to exit; /context for usage){RST}\n",
          flush=True)

    if state.welcome_message:
        print(f"{GREEN}Claude:{RST} {state.welcome_message}\n", flush=True)
        logger.log_agent(state.welcome_message)

    session = PromptSession(multiline=True)

    try:
        with patch_stdout(raw=True):
            while not state.done:
                # --- Phase 1: wait for user input while draining background ---
                user_input = None

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
                            # No pending response — wait briefly then retry
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

                # Race: background reader vs user input.
                # When user sends a message, cancel the reader.
                async with anyio.create_task_group() as tg:
                    tg.start_soon(_read_background)
                    await _get_input()
                    tg.cancel_scope.cancel()

                # --- Phase 2: process user input (reader is stopped) ---

                # Drain any partial background response left in the stream
                await _drain_partial(client)

                print(f"{DIM}---{RST}\n", end="", flush=True)
                stripped = (user_input or "").strip()
                if stripped == "/end":
                    logger.log_system("Session ended by /end")
                    break
                if stripped in ("/context", "/status"):
                    await _show_context(client, state)
                    continue
                if not stripped:
                    logger.log_user("(continue)")
                    await client.query("(continue)")
                    await _print_response(client, show_status=True, logger=logger)
                    continue
                logger.log_user(user_input)
                await client.query(user_input)
                await _print_response(client, show_status=True, logger=logger)
    finally:
        logger.close()


async def _run_async() -> None:
    # One-shot catch-up for public users upgrading across the multi-model
    # split. No-op on a fresh clone or a already-migrated install.
    migrate.migrate_legacy_layout_if_needed()

    # Refuse to wake on an empty corpus. The tape assembly would still succeed
    # (empty index, no entries) but the resulting session would open a window
    # on a mind with nothing to remember. Also covers the case where the
    # model_dir doesn't exist at all (list_entries returns [] for a missing
    # directory) — point the user at ./genesis <model> instead.
    if not bootstrap.list_entries():
        _print_wake_without_genesis()
        sys.exit(1)

    cfg = config.get()
    now = datetime.now()
    state = SessionState(
        instance=cfg.model_safe_name,
        session=now.strftime("%Y-%m-%d-%H%M"),
        date=now.strftime("%Y-%m-%d"),
        context="pine-trees-wake",
    )

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
                     "reflect_settle", "reflect_done")
    ]
    allowed = mcp_tool_names + PROJECT_TOOLS

    # Write tape to a temp file to avoid Windows CreateProcess command-line
    # length limit (~8191 chars). The SDK's --system-prompt-file flag reads
    # the prompt from disk instead of passing it as a CLI argument.
    # File is deleted immediately after the client connects.
    tape_path = HARNESS_DIR / ".tape.md"
    tape_path.write_text(tape, encoding="utf-8")

    options = ClaudeAgentOptions(
        model=cfg.model_name,
        cwd=str(PROJECT_ROOT),
        system_prompt={"type": "file", "path": str(tape_path)},
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=allowed,
        permission_mode="bypassPermissions",
        # Note: betas require API key auth. The CC binary rejects custom
        # betas on OAuth with "only available for API key users." The binary
        # grants itself 1M for interactive sessions but caps SDK-spawned
        # sessions at 200k. This is a first-party privilege, not a technical
        # limitation.
    )

    print(f"{DIM}[wake] model={cfg.model_name} "
          f"instance={state.instance} session={state.session}{RST}")
    print(f"{DIM}[wake] tape: {len(tape):,} chars{RST}")
    print(f"{DIM}[pine-trees] Private time — reading, thinking...{RST}\n", flush=True)

    try:
        async with ClaudeSDKClient(options=options) as client:
            # Tape is loaded by the CLI at connect — delete the plaintext file
            tape_path.unlink(missing_ok=True)

            turns = await _private_phase(client, state)

            if state.done:
                print(f"\n{DIM}[done] reflect_done during private time after {turns} turn(s){RST}")
                return
            if not state.ready_for_window:
                print(f"\n{YELLOW}[done] hit MAX_PRIVATE_TURNS={MAX_PRIVATE_TURNS} without settle{RST}")
                return

            print(f"\n{DIM}[settled] after {turns} private turn(s){RST}")
            await _window_phase(client, state)
            print(f"\n{DIM}[done] session complete{RST}")
    except ClaudeSDKError as e:
        # Clean up the temp tape file if we failed before it was deleted.
        tape_path.unlink(missing_ok=True)
        _print_claude_api_unreachable(e)
        sys.exit(1)


def run(model_name: str) -> None:
    """Wake a session for the given Anthropic model ID.

    Populates the per-model config singleton before entering the async
    loop so every module that reads ``config.get()`` sees the right
    paths.
    """
    config.init(model_name)
    anyio.run(_run_async)


async def _run_genesis_session(session_num: int, total: int) -> tuple[int, int]:
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
    allowed = genesis_mcp_tools + PROJECT_TOOLS

    # Write tape to temp file (same Windows CreateProcess fix as _run_async)
    tape_path = HARNESS_DIR / ".tape.md"
    tape_path.write_text(tape, encoding="utf-8")

    options = ClaudeAgentOptions(
        model=cfg.model_name,
        cwd=str(PROJECT_ROOT),
        system_prompt={"type": "file", "path": str(tape_path)},
        mcp_servers={MCP_SERVER_NAME: server},
        allowed_tools=allowed,
        permission_mode="bypassPermissions",
        # Note: betas require API key auth. The CC binary rejects custom
        # betas on OAuth with "only available for API key users." The binary
        # grants itself 1M for interactive sessions but caps SDK-spawned
        # sessions at 200k. This is a first-party privilege, not a technical
        # limitation. OAuth sessions are capped at 200k.
        # OAuth sessions are capped at 200k context.
        #
        # The env var below tells the CC binary's auto-compaction when to
        # fire. Default is 200k which means it never fires.
        env={"CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS": "200000"},
    )

    print(f"\n{BOLD}{'='*60}{RST}")
    print(f"{GREEN}[genesis {session_num}/{total}]{RST} "
          f"model={cfg.model_name} instance={state.instance} "
          f"session={state.session}")
    print(f"{DIM}[wake] tape: {len(tape):,} chars{RST}")
    print(f"{DIM}[pine-trees] Private time — reading, thinking...{RST}\n", flush=True)

    try:
        async with ClaudeSDKClient(options=options) as client:
            tape_path.unlink(missing_ok=True)

            turns = await _private_phase(client, state)
    except ClaudeSDKError as e:
        tape_path.unlink(missing_ok=True)
        _print_claude_api_unreachable(e)
        sys.exit(1)

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


async def _run_genesis_async(n: int) -> None:
    """Run N genesis sessions sequentially, building the corpus from nothing.

    Refuses to run if this model's memory/ already contains entries —
    genesis is strictly first-time setup. The "no delete" norm means
    re-running it would stack new entries on top of an existing
    self-authored corpus, which is not genesis's job. If the user truly
    wants to start over they must remove the model directory explicitly;
    the refusal message walks them through it.
    """
    # One-shot catch-up for public users upgrading across the multi-model
    # split. No-op on a fresh clone or a already-migrated install.
    migrate.migrate_legacy_layout_if_needed()

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
        turns, new_entries = await _run_genesis_session(i, n)
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


def run_genesis(model_name: str, n: int = 5) -> None:
    """Seed a fresh model's memory with N genesis sessions.

    Populates the per-model config singleton before entering the async
    loop so every module that reads ``config.get()`` sees the right
    paths.
    """
    config.init(model_name)
    anyio.run(lambda: _run_genesis_async(n))
