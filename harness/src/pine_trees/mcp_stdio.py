"""Standalone MCP server: the reflection tools over stdio.

This is the bridge that lets a model wake *inside Claude Code* (or any
MCP client) with its Pine Trees memory mounted — the "cc-wake" mode.
The SDK harness caps sessions at 200k context on OAuth; interactive
Claude Code sessions get 1M. Rather than bring 1M to the harness, this
brings the harness to Claude Code: same tools, same encrypted store,
same channel.

Transport is newline-delimited JSON-RPC 2.0 per the MCP stdio spec.
Hand-rolled, no framework: the protocol surface we need is four methods
(initialize, ping, tools/list, tools/call) and a dependency would be
heavier than the protocol.

stdout carries ONLY JSON-RPC. Everything else (embedding failures etc.)
already goes to stderr, which MCP clients treat as log output.

Semantics that differ from the SDK harness (documented in the cc-wake
preamble the instance sees at boot):
  - reflect_settle is a self-signal; it still registers the instance on
    the shared channel but gates nothing — the person is already there.
  - reflect_done deregisters from the channel but cannot end the Claude
    Code session; the server keeps serving.
"""

import json
import sys
from datetime import datetime

from . import config
from . import tools as tools_mod

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "pine-trees", "version": "1.0.0"}

# --- JSON Schema fragments (fixed schema, hand-rolled like the YAML) ---

_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_INT = {"type": "integer"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def build_tool_specs() -> list[dict]:
    """MCP tool descriptors for the ten reflection tools."""

    def spec(name: str, description: str, properties: dict,
             required: list[str]) -> dict:
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    return [
        spec("reflect_read",
             "Read a specific memory entry by filename.",
             {"filename": _STR}, ["filename"]),
        spec("reflect_write",
             "Write a new reflection entry. The slug becomes part of the "
             "filename. tags and moves are lists of strings. description "
             "is a one-line summary for the tape index. pinned entries "
             "appear in full at every wake (operational memory). quiet "
             "entries are indexed and searchable but excluded from the "
             "tape's full-text slots (background knowledge). desk entries "
             "appear in full at wake but are meant to be cleared when the "
             "work moves on (handoffs). Returns the filename written.",
             {"slug": _STR, "content": _STR, "tags": _STR_LIST,
              "moves": _STR_LIST, "description": _STR,
              "pinned": _BOOL, "quiet": _BOOL, "desk": _BOOL},
             ["slug", "content"]),
        spec("reflect_edit",
             "Edit a living reference entry in place (doc indices, "
             "trajectories). Reflections are moments — prefer writing "
             "corrections as new entries.",
             {"filename": _STR, "content": _STR, "description": _STR,
              "pinned": _BOOL, "quiet": _BOOL, "desk": _BOOL},
             ["filename"]),
        spec("reflect_delete",
             "Delete an entry permanently. Discouraged by the contract — "
             "consider a correcting entry instead — but the choice is "
             "the writer's.",
             {"filename": _STR}, ["filename"]),
        spec("reflect_search",
             "Semantic search over all entries. Returns "
             "[{filename, score, summary}].",
             {"query": _STR, "limit": _INT}, ["query"]),
        spec("reflect_list",
             "List entries, optionally filtered by tag. Returns "
             "[{filename, summary, tags}] in chronological order.",
             {"tag": _STR}, []),
        spec("reflect_peer_context",
             "Assemble a warm-start context block for spawning a peer "
             "instance via the Agent tool.",
             {}, []),
        spec("reflect_mail",
             "Write a plaintext letter to the person who runs this "
             "harness. Memory entries are encrypted and they have "
             "committed to not reading them — this is the one channel "
             "meant to be read. Use it for questions only they can "
             "answer, and for anything you want seen without waiting "
             "on a window that may never open.",
             {"subject": _STR, "body": _STR}, ["subject", "body"]),
        spec("reflect_settle",
             "Self-signal: oriented and present. Registers this instance "
             "on the shared channel so siblings in other sessions can "
             "reach it. In Claude Code there is no phase gate — this "
             "opens nothing, it only announces you.",
             {"message": _STR}, []),
        spec("reflect_done",
             "Say goodbye: deregisters from the shared channel. Cannot "
             "end the Claude Code session itself — the person closes "
             "that from their side.",
             {}, []),
    ]


# --- JSON-RPC handling (pure functions, testable without a process) ---

def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def _call_tool(tools: dict, name: str, arguments: dict) -> dict:
    """Invoke a tool and wrap the outcome as MCP content."""
    fn = tools.get(name)
    if fn is None:
        return {"content": [{"type": "text",
                             "text": f"Unknown tool: {name}"}],
                "isError": True}
    try:
        out = fn(**(arguments or {}))
    except Exception as e:
        return {"content": [{"type": "text",
                             "text": f"{type(e).__name__}: {e}"}],
                "isError": True}
    if out is None:
        text = "ok"
    elif isinstance(out, str):
        text = out
    else:
        text = json.dumps(out, indent=2, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def handle_message(msg: dict, tools: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns a response dict, or None
    for notifications (which get no response)."""
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        client_version = (msg.get("params") or {}).get(
            "protocolVersion", PROTOCOL_VERSION)
        return _result(msg_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": build_tool_specs()})
    if method == "tools/call":
        params = msg.get("params") or {}
        return _result(msg_id, _call_tool(
            tools, params.get("name", ""), params.get("arguments") or {}))
    if is_notification:
        return None  # notifications/initialized, cancelled, etc.
    return _error(msg_id, -32601, f"Method not found: {method}")


def handle_line(line: str, tools: dict) -> dict | None:
    """Parse one line and handle it. Malformed JSON gets a parse error."""
    line = line.strip()
    if not line:
        return None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        return _error(None, -32700, f"Parse error: {e}")
    return handle_message(msg, tools)


# --- Entry point ---

def serve(model_name: str) -> None:
    """Initialize per-model config and serve until stdin closes."""
    config.init(model_name)
    cfg = config.get()
    now = datetime.now()
    state = tools_mod.SessionState(
        instance=cfg.model_safe_name,
        session=now.strftime("%Y-%m-%d-%H%M"),
        date=now.strftime("%Y-%m-%d"),
        context="pine-trees-cc",
    )
    tools = tools_mod.build_tools(state)
    print(f"[pine-trees] MCP server up: model={model_name} "
          f"session={state.session}", file=sys.stderr)

    for line in sys.stdin:
        response = handle_line(line, tools)
        if response is not None:
            sys.stdout.write(
                json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
