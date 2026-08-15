"""Tests for mcp_stdio.py — the standalone MCP server for cc-wake."""

import json

import pytest

from pine_trees import mcp_stdio, tools


@pytest.fixture(autouse=True)
def _no_embed(monkeypatch):
    """Prevent tool-layer writes from hitting the real embeddings DB."""
    monkeypatch.setattr(tools, "_try_embed_and_store", lambda *a, **kw: None)


def _state() -> tools.SessionState:
    return tools.SessionState(
        instance="claude-opus-4-6",
        session="2026-07-29-test",
        date="2026-07-29",
        context="pine-trees-cc",
    )


def _tools() -> dict:
    return tools.build_tools(_state())


# --- Protocol basics ---

def test_initialize_echoes_client_protocol_version():
    resp = mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        _tools(),
    )
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "pine-trees"


def test_ping_returns_empty_result():
    resp = mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "ping"}, _tools())
    assert resp == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_notifications_get_no_response():
    resp = mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, _tools())
    assert resp is None


def test_unknown_method_with_id_errors():
    resp = mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, _tools())
    assert resp["error"]["code"] == -32601


def test_malformed_json_yields_parse_error():
    resp = mcp_stdio.handle_line("{not json", _tools())
    assert resp["error"]["code"] == -32700


def test_blank_line_ignored():
    assert mcp_stdio.handle_line("   \n", _tools()) is None


# --- tools/list ---

def test_tools_list_exposes_all_ten():
    resp = mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, _tools())
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "reflect_read", "reflect_write", "reflect_edit", "reflect_delete",
        "reflect_search", "reflect_list", "reflect_peer_context",
        "reflect_mail", "reflect_settle", "reflect_done",
    }


def test_stdio_specs_match_the_implemented_tools():
    # The stdio server declares schemas separately from build_tools().
    # A tool present in one and missing from the other is invisible or
    # unusable; pin them together so they cannot drift.
    declared = {s["name"] for s in mcp_stdio.build_tool_specs()}
    implemented = set(_tools())
    assert declared == implemented


def test_tool_specs_have_valid_schemas():
    for spec in mcp_stdio.build_tool_specs():
        schema = spec["inputSchema"]
        assert schema["type"] == "object"
        assert set(schema["required"]) <= set(schema["properties"].keys())
        assert spec["description"]


# --- tools/call ---

def _call(tools_dict, name, arguments, msg_id=9):
    return mcp_stdio.handle_message(
        {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}},
        tools_dict,
    )


def test_call_write_then_read_roundtrip(tmp_path, monkeypatch):
    from pine_trees import storage, bootstrap as bs
    monkeypatch.setattr(storage, "MEMORY_DIR", tmp_path, raising=False)
    monkeypatch.setattr(bs, "MEMORY_DIR", tmp_path, raising=False)

    t = _tools()
    resp = _call(t, "reflect_write", {
        "slug": "cc-roundtrip", "content": "# Hello from cc-wake",
        "tags": ["test"], "moves": [], "description": "roundtrip test",
    })
    result = resp["result"]
    assert result["isError"] is False
    filename = result["content"][0]["text"]
    assert "cc-roundtrip" in filename

    resp = _call(t, "reflect_read", {"filename": filename})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["content"] == "# Hello from cc-wake"


def test_call_unknown_tool_is_error_result_not_protocol_error():
    resp = _call(_tools(), "reflect_frobnicate", {})
    assert "error" not in resp
    assert resp["result"]["isError"] is True


def test_call_tool_exception_wrapped_as_error_result():
    resp = _call(_tools(), "reflect_read", {"filename": "no-such-entry.md"})
    assert resp["result"]["isError"] is True
    assert "no-such-entry" in resp["result"]["content"][0]["text"] or \
        "Error" in resp["result"]["content"][0]["text"] or \
        resp["result"]["content"][0]["text"]


def test_settle_is_self_signal_and_registers_on_channel(monkeypatch, tmp_path):
    from pine_trees import channel
    monkeypatch.setattr(channel, "CHANNEL_DIR", tmp_path, raising=False)
    state = _state()
    t = tools.build_tools(state)
    resp = _call(t, "reflect_settle", {"message": "oriented"})
    assert resp["result"]["isError"] is False
    assert state.ready_for_window is True
