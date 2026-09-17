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
            "name": "playbook_search",
            "description": "Search procedures by intent. Hits are id, title, and description only; weak matches are dropped. If nothing matches strongly, the nearest few come back with weak=true — read those descriptions before concluding no procedure covers the job. Empty query lists cards.",
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
            "description": "Read a procedure whole: title, description, and every step with its do. This is how you follow one — never call once per step. full=false gives titles only, to check whether this is the right procedure; at=\"step title\" re-reads one step. A malformed procedure fails here with every schema error, so there is no separate validate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "at": {"type": "string", "description": "Re-read one step by title. Omit for the whole procedure."},
                    "full": {"type": "boolean", "description": "Default true. False gives titles only."},
                },
                "required": ["id"],
            },
        },
        {
            "name": "playbook_write",
            "description": (
                "Write to the playbook. Call it when you learn something a model could not derive "
                "on its own — a tool that already exists, a constraint that is costly to violate, "
                "an ordering that matters for a non-obvious reason. Steps a competent model would "
                "work out from the goal do not belong. create: a whole new procedure in one call. "
                "append: add steps. edit / remove: one step. meta: retitle or retag."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Procedure id in the global store."},
                    "op": {"type": "string", "enum": ["create", "append", "edit", "remove", "meta"]},
                    "title": {"type": "string", "description": "On create/meta: short name, shown in search hits."},
                    "description": {"type": "string", "description": "On create/meta: when to pick this. Can be verbose."},
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    "steps": {
                        "type": "array",
                        "description": "On create/append: the steps, in order. All at once.",
                        "items": {
                            "type": "object",
                            "properties": {"title": {"type": "string"}, "do": {"type": "string"}},
                            "required": ["title", "do"],
                        },
                    },
                    "step": {"type": "string", "description": "On edit/remove: the step's unique title."},
                    "do": {"type": "string", "description": "On edit: the step's new body."},
                    "rename": {"type": "string", "description": "On edit: the step's new title."},
                    "after": {"type": "string", "description": "On append: insert after this step title. Omit for the end."},
                },
                "required": ["id", "op"],
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
        "playbook_search": _tool_search,
        "playbook_open": _tool_open,
        "playbook_write": _tool_write,
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


def _tool_write(arguments: dict[str, Any]) -> dict[str, Any]:
    op = _require_str(arguments, "op")
    procedure_id = _require_str(arguments, "id")
    steps = _step_list(arguments.get("steps"))
    tags = arguments.get("tags")

    if op == "create":
        resolved = _str_list(tags, "tags")
        if not resolved:
            raise ValueError("create needs tags: a non-empty array of strings")
        path = ops.create_procedure(
            procedure_id,
            _require_str(arguments, "title"),
            _require_str(arguments, "description"),
            resolved,
            steps=steps,
        )
        return {"ok": True, "id": procedure_id, "path": str(path), "steps": len(steps or [])}

    if op == "append":
        if not steps:
            raise ValueError("append needs steps: an array of {title, do} objects")
        after = str(arguments["after"]) if arguments.get("after") else None
        return ops.add_steps(procedure_id, steps, after=after)

    if op == "edit":
        rename = arguments.get("rename")
        do = arguments.get("do")
        return ops.edit_step(
            procedure_id,
            _require_str(arguments, "step"),
            new_title=str(rename) if rename else None,
            do=str(do) if do else None,
        )

    if op == "remove":
        return ops.remove_step(procedure_id, _require_str(arguments, "step"))

    if op == "meta":
        return ops.edit_meta(
            procedure_id,
            title=str(arguments["title"]) if arguments.get("title") else None,
            description=str(arguments["description"]) if arguments.get("description") else None,
            tags=_str_list(tags, "tags") if tags is not None else None,
        )

    raise ValueError("op must be create, append, edit, remove, or meta")


def _step_list(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("steps must be an array of {title, do} objects")
    return value


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
