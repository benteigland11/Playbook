"""Command-line interface for playbook procedure files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from playbook import library, ops

# The working tree remembers that the playbook was searched from here (create checks for it), the way
# Cartograph's cg/.searched does: a method is looked for before it is written.
SEARCH_MARK = os.path.join(".playbook", ".searched")


def _note_search(query: str) -> None:
    try:
        os.makedirs(".playbook", exist_ok=True)
        with open(SEARCH_MARK, "a") as handle:
            handle.write(query.replace("\n", " ")+"\n")
    except OSError:
        pass


def _searched_here() -> bool:
    return os.path.exists(SEARCH_MARK)


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
    create.add_argument("--unsearched", action="store_true", help="create without a prior `playbook search` from this tree")
    create.set_defaults(handler=_cmd_create)

    edit = sub.add_parser("edit", help="edit procedure title, description, and/or tags")
    edit.add_argument("id")
    edit.add_argument("--title", default="", help="new procedure title")
    edit.add_argument("--description", default="", help="new when-to-pick text (can be verbose)")
    edit.add_argument("--tags", default="", help="replacement comma-separated tags")
    edit.add_argument("--widgets", default=None, help="replacement comma-separated widget ids the method calls (installed before a walk); '' clears")
    edit.set_defaults(handler=_cmd_edit)

    search = sub.add_parser("search", help="BM25 + char n-gram search over id, tags, description, and step titles")
    search.add_argument("query", nargs="*", help="tokens to match (omit to list all)")
    search.add_argument(
        "--limit",
        type=int,
        default=ops.DEFAULT_SEARCH_LIMIT,
        help=f"max hits (default {ops.DEFAULT_SEARCH_LIMIT}, max {ops.MAX_SEARCH_LIMIT})",
    )
    search.add_argument("--dir", default=".", help="working tree whose open walks come first (default: .)")
    search.add_argument("--all", dest="include_retired", action="store_true",
                        help="include decayed procedures (a person looking, not a worker)")
    search.set_defaults(handler=_cmd_search)

    load = sub.add_parser("load", aliases=["show", "get"], help="print the whole procedure: title, description, and every step do")
    load.add_argument("id")
    load.add_argument("--titles", action="store_true", help="outline only: step titles without their do")
    load.add_argument("--full", action="store_true", help="deprecated; full output is the default")
    load.set_defaults(handler=_cmd_load)

    start = sub.add_parser("start", help="re-read one step by title; returns that do plus its neighbours")
    start.add_argument("id")
    start.add_argument("--title", default=None, help="unique step title to start at (default: the first step)")
    start.add_argument("--step", type=int, default=None, help="1-based step number to start at (the reply's `then` gives the next one)")
    start.set_defaults(handler=_cmd_start)

    open_ = sub.add_parser("open", help="write the whole procedure as a checklist to .playbook/open/<id>--<for>.md; read it once, follow it")
    open_.add_argument("id")
    open_.add_argument("--for", dest="purpose", required=True, help="what this walk is for (an unknown, an artifact, a source); names the file")
    open_.add_argument("--dir", default=".", help="working tree to write under (default: .)")
    open_.add_argument("--again", action="store_true", help="a second walk for a second thing; without it an unfinished walk is handed back")
    open_.add_argument("--nested", action="store_true", help="the procedure alone, linked steps as links to open (default: flat — links inlined, one straight list)")
    open_.add_argument("--limit", type=int, default=0, help=f"steps in this walk (default {ops.FLAT_LIMIT}); the rest continues in the next walk")
    open_.add_argument("--from", dest="start", type=int, default=0, help="0-based step of the flattened method to start at (the next walk of a long method)")
    open_.set_defaults(handler=_cmd_open)

    reach = sub.add_parser("reach", help="how long a method really is: steps through its links, and how many walks that is")
    reach.add_argument("id")
    reach.set_defaults(handler=_cmd_reach)

    upstream = sub.add_parser("upstream", help="how far up the library this procedure sits: the chains of procedures that link down to it, root first; orphan when none does")
    upstream.add_argument("id")
    upstream.set_defaults(handler=_cmd_upstream)

    tick = sub.add_parser("tick", help="mark steps of an open walk: --done N,M get [x]; --skip N,M --because ... get [-] with the reason under each; with the walk's plan written, any number at once")
    tick.add_argument("target", help="the walk file under .playbook/open/, or the procedure id of its one unfinished walk")
    tick.add_argument("--done", nargs="*", default=[], help="step numbers done: --done 1,3 or --done 1 3")
    tick.add_argument("--skip", nargs="*", default=[], help="steps that do not apply here, with --because (several need the walk's plan)")
    tick.add_argument("--because", default="", help="why the skipped step does not apply (written under it)")
    tick.add_argument("--note", default="", help="with --done: what the step found or changed, one line (written under it)")
    tick.add_argument("--dir", default=".", help="working tree the walk is under (default: .)")
    tick.set_defaults(handler=_cmd_tick)


    validate = sub.add_parser("validate", help="validate a procedure in the global store")
    validate.add_argument("id")
    validate.set_defaults(handler=_cmd_validate)

    add_step = sub.add_parser("add-step", help="append a serial step (title + do; --procedure links another procedure as the step)")
    add_step.add_argument("id", nargs="?", default="", help="procedure id (or --walk FILE --after N to add where you stand)")
    add_step.add_argument("--walk", default="", help="an open flat walk: the new step goes into the procedure the --after step came from")
    add_step.add_argument("--title", required=True, help="short trail label")
    add_step.add_argument("--do", dest="do_text", required=True, help="imperative: do this")
    add_step.add_argument("--after", default="", help="insert after this unique title (default: append)")
    add_step.add_argument("--procedure", default="", help="link: walking this step means opening this other procedure")
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
    edit_step.add_argument("id", nargs="?", default="", help="procedure id (or --walk FILE --step N to edit where you stand)")
    edit_step.add_argument("--walk", default="", help="an open flat walk: the edit writes through to the procedure the step came from")
    edit_step.add_argument("--step", type=int, default=0, help="with --walk: the step number in that walk")
    edit_step.add_argument("--title", default="", help="current unique title to target (without --walk)")
    edit_step.add_argument("--rename", default="", help="new title (id stays the same)")
    edit_step.add_argument("--do", dest="do_text", default="", help="new imperative")
    edit_step.add_argument("--procedure", default=None, help="link another procedure to this step ('' to unlink)")
    edit_step.set_defaults(handler=_cmd_edit_step)

    remove_step = sub.add_parser("remove-step", help="remove a step by its unique title; remaining steps stay in order")
    remove_step.add_argument("id", nargs="?", default="", help="procedure id (or --walk FILE --step N)")
    remove_step.add_argument("--walk", default="", help="an open flat walk: the step is removed from the procedure it came from")
    remove_step.add_argument("--step", type=int, default=0, help="with --walk: the step number in that walk")
    remove_step.add_argument("--title", default="", help="unique title to remove (without --walk)")
    remove_step.set_defaults(handler=_cmd_remove_step)

    move_step = sub.add_parser("move-step", help="move a step by its unique title to a 1-based position; others keep their order")
    move_step.add_argument("id")
    move_step.add_argument("--title", required=True, help="unique title to move")
    move_step.add_argument("--to", type=int, required=True, help="new 1-based position")
    move_step.set_defaults(handler=_cmd_move_step)

    delete = sub.add_parser("delete", help="remove a procedure from the store; refused while another procedure links it")
    delete.add_argument("id")
    delete.set_defaults(handler=_cmd_delete)

    lib = sub.add_parser("library", help="use it or lose it: the ledger of touches, pins and decay beside the store")
    lib_sub = lib.add_subparsers(dest="library_command", required=True)
    st = lib_sub.add_parser("status", help="clock, policy and every procedure's standing (or one id's)")
    st.add_argument("id", nargs="?", default=None)
    st.set_defaults(handler=lambda a: library.status(a.id))
    ls = sub.add_parser("list", help="every procedure in the library and its standing (library status)")
    ls.add_argument("id", nargs="?", default=None)
    ls.set_defaults(handler=lambda a: library.status(a.id))
    tc = lib_sub.add_parser("touch", help="record a touch by hand (the app opening a procedure counts as a search)")
    tc.add_argument("id")
    tc.add_argument("kind", choices=library.KINDS)
    tc.set_defaults(handler=_cmd_library_touch)
    pn = lib_sub.add_parser("pin", help="never retire this procedure by decay")
    pn.add_argument("id")
    pn.set_defaults(handler=lambda a: library.pin(a.id, True))
    up = lib_sub.add_parser("unpin", help="let it decay again")
    up.add_argument("id")
    up.set_defaults(handler=lambda a: library.pin(a.id, False))
    rt = lib_sub.add_parser("decay", help="decay it by hand: hidden from search and from workers until revived")
    rt.add_argument("id")
    rt.set_defaults(handler=lambda a: library.retire(a.id, "manual"))
    rv = lib_sub.add_parser("revive", help="bring a decayed procedure back with full grace")
    rv.add_argument("id")
    rv.set_defaults(handler=lambda a: library.revive(a.id))
    tk = lib_sub.add_parser("tick", help="one more green gate: see new procedures, decay what ran out of grace")
    tk.add_argument("--protect", action="append", default=None, metavar="PREFIX",
                    help="id prefixes that never retire (replaces the policy's list when given)")
    tk.set_defaults(handler=lambda a: library.tick(a.protect))
    pl = lib_sub.add_parser("policy", help="turn decay on or off and set how many gates a touch lasts")
    pl.add_argument("--on", dest="enabled", action="store_true", default=None)
    pl.add_argument("--off", dest="enabled", action="store_false")
    pl.add_argument("--grace", type=int, default=None, help="gates a search-touch lasts; use lasts 2x, edit 3x")
    pl.add_argument("--protect", action="append", default=None, metavar="PREFIX")
    pl.set_defaults(handler=lambda a: library.set_policy(enabled=a.enabled, grace=a.grace, protect=a.protect))

    return parser


def _cmd_library_touch(args: argparse.Namespace) -> dict[str, Any]:
    library.touch([args.id], args.kind)
    return {"ok": True, **library.standing(args.id)}


def _cmd_create(args: argparse.Namespace) -> dict[str, Any]:
    tags = _csv(args.tags)
    if not tags:
        raise ValueError("--tags must include at least one tag")
    if not args.unsearched and not _searched_here():
        raise ValueError("No playbook search has been run from this tree. Search first — "
                         "`playbook search \"<what the method does>\" --limit 3` — and improve the closest procedure "
                         "(add-step / edit-step) when one nearly fits; create only if nothing does "
                         "(or pass --unsearched to skip this check).")
    steps = _steps_json(args.steps)
    path = ops.create_procedure(args.id, args.title, args.description, tags, steps=steps)
    return {"ok": True, "id": args.id, "path": str(path), "steps": len(steps or [])}


def _cmd_edit(args: argparse.Namespace) -> dict[str, Any]:
    title = args.title.strip() or None
    description = args.description.strip() or None
    tags = _csv(args.tags) or None
    widgets = None if args.widgets is None else _csv(args.widgets)
    if title is None and description is None and tags is None and widgets is None:
        raise ValueError("edit requires --title, --description, --tags and/or --widgets")
    return ops.edit_meta(args.id, title=title, description=description, tags=tags, widgets=widgets)


def _cmd_search(args: argparse.Namespace) -> dict[str, Any]:
    _note_search(" ".join(args.query))
    return ops.search_procedures(" ".join(args.query), limit=args.limit, include_retired=args.include_retired,
                                 target_dir=args.dir)


def _cmd_load(args: argparse.Namespace) -> dict[str, Any]:
    if args.titles and args.full:
        raise ValueError("pass --titles or --full, not both")
    return ops.load_procedure(args.id, full=not args.titles)


def _cmd_open(args: argparse.Namespace) -> dict[str, Any]:
    return ops.open_procedure(args.id, args.purpose, args.dir, again=args.again, nested=args.nested,
                              limit=args.limit or ops.FLAT_LIMIT, start=args.start)


def _cmd_upstream(args: argparse.Namespace) -> dict[str, Any]:
    return ops.upstream(args.id)


def _cmd_reach(args: argparse.Namespace) -> dict[str, Any]:
    return ops.reach(args.id)


def _cmd_tick(args: argparse.Namespace) -> dict[str, Any]:
    def numbers(tokens: list[str]) -> list[int]:
        # `--done 1,3` and `--done 1 3` both: the first worker to tick wrote the numbers with spaces and was refused.
        return [int(x) for token in tokens for x in token.replace(" ", "").split(",") if x]
    return ops.tick_walk(args.target, numbers(args.done), numbers(args.skip), args.dir, because=args.because, note=args.note)


def _cmd_start(args: argparse.Namespace) -> dict[str, Any]:
    return ops.start_procedure(args.id, args.title, step=args.step)


def _cmd_validate(args: argparse.Namespace) -> dict[str, Any]:
    return ops.validate_procedure(args.id)


def _cmd_add_step(args: argparse.Namespace) -> dict[str, Any]:
    after = args.after.strip() or None
    if args.walk:
        if not after or not after.isdigit():
            raise ValueError("with --walk, --after is the step number in that walk the new step follows")
        return ops.walk_add_step(args.walk, int(after), args.title, args.do_text, procedure=args.procedure.strip() or None)
    if not args.id:
        raise ValueError("add-step needs a procedure id, or --walk FILE --after N")
    return ops.add_step(args.id, args.title, args.do_text, after=after, procedure=args.procedure.strip() or None)


def _cmd_add_steps(args: argparse.Namespace) -> dict[str, Any]:
    steps = _steps_json(args.steps)
    if not steps:
        raise ValueError("--steps must be a non-empty JSON array")
    return ops.add_steps(args.id, steps, after=args.after.strip() or None)


def _cmd_edit_step(args: argparse.Namespace) -> dict[str, Any]:
    new_title = args.rename.strip() or None
    do_text = args.do_text.strip() or None
    if new_title is None and do_text is None and args.procedure is None:
        raise ValueError("edit-step requires --rename, --do and/or --procedure")
    if args.walk:
        if not args.step:
            raise ValueError("with --walk, --step N names the step in that walk")
        return ops.walk_edit_step(args.walk, args.step, new_title=new_title, do=do_text, procedure=args.procedure)
    if not args.id or not args.title:
        raise ValueError("edit-step needs a procedure id and --title, or --walk FILE --step N")
    return ops.edit_step(args.id, args.title, new_title=new_title, do=do_text, procedure=args.procedure)


def _cmd_delete(args: argparse.Namespace) -> dict[str, Any]:
    return ops.delete_procedure(args.id)


def _cmd_move_step(args: argparse.Namespace) -> dict[str, Any]:
    return ops.move_step(args.id, args.title, args.to)


def _cmd_remove_step(args: argparse.Namespace) -> dict[str, Any]:
    if args.walk:
        if not args.step:
            raise ValueError("with --walk, --step N names the step in that walk")
        return ops.walk_remove_step(args.walk, args.step)
    if not args.id or not args.title:
        raise ValueError("remove-step needs a procedure id and --title, or --walk FILE --step N")
    return ops.remove_step(args.id, args.title)



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
