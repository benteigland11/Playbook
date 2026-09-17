"""Stdlib MCP server (JSON-RPC 2.0 over newline-delimited stdio)."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable, TextIO

from playbook import ops

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "playbook"
SERVER_VERSION = "0.1.0"

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    incoming = stdin or sys.stdin
    outgoing = stdout or sys.stdout
    while True:
        line = incoming.readline()
        if line == "":
            return
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(outgoing, _rpc_error(None, -32700, "parse error"))
            continue
        response = handle_message(message)
        if response is not None:
            _write(outgoing, response)


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "invalid request")
    method = message.get("method")
    msg_id = message.get("id")
    if not isinstance(method, str):
        if msg_id is None:
            return None
        return _rpc_error(msg_id, -32600, "invalid request")
    if msg_id is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        return _result(msg_id, _initialize(params if isinstance(params, dict) else {}))
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": _tool_list()})
    if method == "tools/call":
        if not isinstance(params, dict):
            return _rpc_error(msg_id, -32602, "invalid params")
        return _result(msg_id, _call_tool(params))
    return _rpc_error(msg_id, -32601, f"method not found: {method}")


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    version = params.get("protocolVersion") or PROTOCOL_VERSION
    return {
        "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def _tool_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "playbook_create",
            "description": "Create an empty procedure in the global store.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string", "description": "Short name shown in search hits."},
                    "description": {"type": "string", "description": "When to pick this procedure. Can be multiple sentences / verbose."},
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["id", "title", "description", "tags"],
            },
        },
        {
            "name": "playbook_edit",
            "description": "Edit a procedure's title, description, and/or tags. Id does not change. Tags replace the whole list.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                },
                "required": ["id"],
            },
        },
        {
            "name": "playbook_search",
            "description": "Search procedures by intent. Hits are id, title, and description only. Weak matches are dropped (word hit required, score at least half the top hit). Empty query lists cards. At most `limit` hits.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you want to do, or keywords. Omit or empty to list cards.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "Max hits. Default 8, cap 50.",
                    },
                },
            },
        },
        {
            "name": "playbook_open",
            "description": "Read a procedure. One call returns the whole thing: title, description, and every step with its do. This is how you follow a procedure — do not call once per step. Options: full=false for step titles only (an outline, when you just want to see if this is the right procedure); at=\"step title\" to re-read a single step you already loaded.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "at": {"type": "string", "description": "Re-read this one step by title. Omit to read the whole procedure."},
                    "full": {"type": "boolean", "description": "Default true (every step do). Pass false for titles only."},
                },
                "required": ["id"],
            },
        },
        {
            "name": "playbook_validate",
            "description": "Validate a procedure in the global store. Returns validity, errors, and step titles — not step bodies.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "playbook_step",
            "description": "Add, edit, or remove one serial step (lookup by unique title). add: title+do, optional after. edit: title plus rename and/or do (id unchanged). remove: title only; remaining steps fuse in order.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Procedure id in the global store."},
                    "op": {"type": "string", "enum": ["add", "edit", "remove"]},
                    "title": {"type": "string", "description": "New title on add; existing unique title on edit/remove."},
                    "do": {"type": "string"},
                    "after": {"type": "string", "description": "On add: insert after this unique title. Omit to append."},
                    "rename": {"type": "string", "description": "On edit: new title."},
                },
                "required": ["id", "op", "title"],
            },
        },
    ]


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return _tool_error("tool name is required")
    if not isinstance(arguments, dict):
        return _tool_error("arguments must be an object")
    handlers: dict[str, ToolHandler] = {
        "playbook_create": _tool_create,
        "playbook_edit": _tool_edit,
        "playbook_search": _tool_search,
        "playbook_open": _tool_open,
        "playbook_validate": _tool_validate,
        "playbook_step": _tool_step,
    }
    handler = handlers.get(name)
    if handler is None:
        return _tool_error(f"unknown tool: {name}")
    try:
        payload = handler(arguments)
    except Exception as exc:
        return _tool_error(str(exc))
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_create(arguments: dict[str, Any]) -> dict[str, Any]:
    tags = _str_list(arguments.get("tags"), "tags")
    if not tags:
        raise ValueError("tags must be a non-empty array of strings")
    path = ops.create_procedure(
        _require_str(arguments, "id"),
        _require_str(arguments, "title"),
        _require_str(arguments, "description"),
        tags,
    )
    return {"ok": True, "id": arguments["id"], "path": str(path)}


def _tool_edit(arguments: dict[str, Any]) -> dict[str, Any]:
    tags = arguments.get("tags")
    return ops.edit_meta(
        _require_str(arguments, "id"),
        title=str(arguments["title"]) if arguments.get("title") else None,
        description=str(arguments["description"]) if arguments.get("description") else None,
        tags=_str_list(tags, "tags") if tags is not None else None,
    )


def _tool_search(arguments: dict[str, Any]) -> dict[str, Any]:
    query = arguments.get("query") or ""
    if query is None:
        query = ""
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    limit = arguments.get("limit")
    if limit is not None and not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    return ops.search_procedures(query, limit=limit)


def _tool_open(arguments: dict[str, Any]) -> dict[str, Any]:
    procedure_id = _require_str(arguments, "id")
    at = arguments.get("at")
    full = arguments.get("full")
    if full is None:
        full = True
    if full is not True and full is not False:
        raise ValueError("full must be a boolean")
    if at not in (None, "") and arguments.get("full") is not None:
        raise ValueError("set at or full, not both")
    if at is None or at == "":
        return ops.load_procedure(procedure_id, full=full)
    if not isinstance(at, str):
        raise ValueError("at must be a string")
    return ops.start_procedure(procedure_id, at)


def _tool_validate(arguments: dict[str, Any]) -> dict[str, Any]:
    return ops.validate_procedure(_require_str(arguments, "id"))


def _tool_step(arguments: dict[str, Any]) -> dict[str, Any]:
    op = _require_str(arguments, "op")
    procedure_id = _require_str(arguments, "id")
    title = _require_str(arguments, "title")
    if op == "add":
        after = arguments.get("after")
        return ops.add_step(
            procedure_id,
            title,
            _require_str(arguments, "do"),
            after=str(after) if after else None,
        )
    if op == "edit":
        rename = arguments.get("rename")
        do = arguments.get("do")
        return ops.edit_step(
            procedure_id,
            title,
            new_title=str(rename) if rename else None,
            do=str(do) if do else None,
        )
    if op == "remove":
        return ops.remove_step(procedure_id, title)
    raise ValueError("op must be add, edit, or remove")


def _str_list(value: Any, field: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return value


def _require_str(arguments: dict[str, Any], field: str) -> str:
    value = arguments.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write(outgoing: TextIO, payload: dict[str, Any]) -> None:
    outgoing.write(json.dumps(payload, ensure_ascii=False) + "\n")
    outgoing.flush()
