"""Command-line interface for playbook procedure files."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from playbook import ops


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    if payload is not None:
        print(json.dumps(payload, indent=2))
    return 0 if _ok(payload) else 2


class _JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps({"ok": False, "error": message}, indent=2))
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonParser(prog="playbook", description="Create and grow playbook procedure JSON files.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="write a new procedure in the global store")
    create.add_argument("id")
    create.add_argument("--title", required=True, help="short name shown in search hits")
    create.add_argument("--description", required=True, help="when to pick this procedure (can be verbose)")
    create.add_argument("--tags", required=True, help="comma-separated tags (at least one)")
    create.add_argument(
        "--steps",
        default="",
        help='JSON array of {"title","do"} objects, or - to read it from stdin; writes the whole procedure at once',
    )
    create.set_defaults(handler=_cmd_create)

    edit = sub.add_parser("edit", help="edit procedure title, description, and/or tags")
    edit.add_argument("id")
    edit.add_argument("--title", default="", help="new procedure title")
    edit.add_argument("--description", default="", help="new when-to-pick text (can be verbose)")
    edit.add_argument("--tags", default="", help="replacement comma-separated tags")
    edit.set_defaults(handler=_cmd_edit)

    search = sub.add_parser("search", help="BM25 + char n-gram search over id, tags, description, and step titles")
    search.add_argument("query", nargs="*", help="tokens to match (omit to list all)")
    search.add_argument(
        "--limit",
        type=int,
        default=ops.DEFAULT_SEARCH_LIMIT,
        help=f"max hits (default {ops.DEFAULT_SEARCH_LIMIT}, max {ops.MAX_SEARCH_LIMIT})",
    )
    search.set_defaults(handler=_cmd_search)

    load = sub.add_parser("load", help="print the whole procedure: title, description, and every step do")
    load.add_argument("id")
    load.add_argument("--titles", action="store_true", help="outline only: step titles without their do")
    load.add_argument("--full", action="store_true", help="deprecated; full output is the default")
    load.set_defaults(handler=_cmd_load)

    start = sub.add_parser("start", help="re-read one step by title; returns that do plus its neighbours")
    start.add_argument("id")
    start.add_argument("--title", required=True, help="unique step title to start at")
    start.set_defaults(handler=_cmd_start)

    validate = sub.add_parser("validate", help="validate a procedure in the global store")
    validate.add_argument("id")
    validate.set_defaults(handler=_cmd_validate)

    add_step = sub.add_parser("add-step", help="append a serial step (title + do)")
    add_step.add_argument("id")
    add_step.add_argument("--title", required=True, help="short trail label")
    add_step.add_argument("--do", dest="do_text", required=True, help="imperative: do this")
    add_step.add_argument("--after", default="", help="insert after this unique title (default: append)")
    add_step.set_defaults(handler=_cmd_add_step)

    add_steps = sub.add_parser("add-steps", help="append several steps at once from a JSON array")
    add_steps.add_argument("id")
    add_steps.add_argument(
        "--steps",
        required=True,
        help='JSON array of {"title","do"} objects, or - to read it from stdin',
    )
    add_steps.add_argument("--after", default="", help="insert after this unique title (default: append)")
    add_steps.set_defaults(handler=_cmd_add_steps)

    edit_step = sub.add_parser("edit-step", help="edit a step by its unique title")
    edit_step.add_argument("id")
    edit_step.add_argument("--title", required=True, help="current unique title to target")
    edit_step.add_argument("--rename", default="", help="new title (id stays the same)")
    edit_step.add_argument("--do", dest="do_text", default="", help="new imperative")
    edit_step.set_defaults(handler=_cmd_edit_step)

    remove_step = sub.add_parser("remove-step", help="remove a step by its unique title; remaining steps stay in order")
    remove_step.add_argument("id")
    remove_step.add_argument("--title", required=True, help="unique title to remove")
    remove_step.set_defaults(handler=_cmd_remove_step)

    mcp = sub.add_parser("mcp", help="run the MCP server on stdio")
    mcp.set_defaults(handler=_cmd_mcp)
    return parser


def _cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    tags = _csv(args.tags)
    if not tags:
        raise ValueError("--tags must include at least one tag")
    steps = _steps_json(args.steps)
    path = ops.create_procedure(args.id, args.title, args.description, tags, steps=steps)
    return {"ok": True, "id": args.id, "path": str(path), "steps": len(steps or [])}


def _cmd_edit(args: argparse.Namespace) -> dict[str, Any]:
    title = args.title.strip() or None
    description = args.description.strip() or None
    tags = _csv(args.tags) or None
    if title is None and description is None and tags is None:
        raise ValueError("edit requires --title, --description, and/or --tags")
    return ops.edit_meta(args.id, title=title, description=description, tags=tags)


def _cmd_search(args: argparse.Namespace) -> dict[str, Any]:
    return ops.search_procedures(" ".join(args.query), limit=args.limit)


def _cmd_load(args: argparse.Namespace) -> dict[str, Any]:
    if args.titles and args.full:
        raise ValueError("pass --titles or --full, not both")
    return ops.load_procedure(args.id, full=not args.titles)


def _cmd_start(args: argparse.Namespace) -> dict[str, Any]:
    return ops.start_procedure(args.id, args.title)


def _cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    return ops.validate_procedure(args.id)


def _cmd_add_step(args: argparse.Namespace) -> dict[str, Any]:
    after = args.after.strip() or None
    return ops.add_step(args.id, args.title, args.do_text, after=after)


def _cmd_add_steps(args: argparse.Namespace) -> dict[str, Any]:
    steps = _steps_json(args.steps)
    if not steps:
        raise ValueError("--steps must be a non-empty JSON array")
    return ops.add_steps(args.id, steps, after=args.after.strip() or None)


def _cmd_edit_step(args: argparse.Namespace) -> dict[str, Any]:
    new_title = args.rename.strip() or None
    do_text = args.do_text.strip() or None
    if new_title is None and do_text is None:
        raise ValueError("edit-step requires --rename and/or --do")
    return ops.edit_step(args.id, args.title, new_title=new_title, do=do_text)


def _cmd_remove_step(args: argparse.Namespace) -> dict[str, Any]:
    return ops.remove_step(args.id, args.title)


def _cmd_mcp(_args: argparse.Namespace) -> None:
    from playbook.mcp import serve

    serve()
    return None


def _steps_json(value: str) -> list[dict[str, Any]] | None:
    """Parse a JSON array of steps. `-` reads it from stdin."""
    text = value.strip()
    if not text:
        return None
    if text == "-":
        text = sys.stdin.read().strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--steps is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ValueError('--steps must be a JSON array of {"title", "do"} objects')
    return parsed


def _csv(value: str) -> list[str]:
    if not value.strip():
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _ok(payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return True
    if "valid" in payload:
        return bool(payload["valid"])
    return bool(payload.get("ok", True))
