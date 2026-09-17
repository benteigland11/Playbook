# Playbook

Procedures as JSON. Search them by intent, then load one in a single call.

Playbook is a small CLI and MCP server so an agent (or you) can **write, find, and follow** how-tos without dumping the whole playbook into context. Search narrows the store to one procedure; reading that procedure is a single call, not one call per step. CLI verbs stay split. MCP compresses related verbs into fewer tools. Everything is JSON.

Python 3.10+, **stdlib only** at runtime.

## Install

```bash
pip install -e .
playbook search
```

PyPI is not published yet.

## Where files live

One JSON file per procedure, in the platform data dir:

| OS | Directory |
| --- | --- |
| Linux | `~/.local/share/playbook/procedures/` |
| macOS | `~/Library/Application Support/playbook/procedures/` |
| Windows | `%LOCALAPPDATA%\playbook\Data\procedures\` |

Honor `XDG_DATA_HOME` / `APPDATA` / `LOCALAPPDATA`. Commands take a procedure **id**, not a path.

## Shape

```json
{
  "id": "follow-playbook",
  "title": "Follow a playbook procedure",
  "description": "When to pick this file. Can be verbose.",
  "tags": ["playbook", "follow"],
  "steps": [
    {
      "id": "a1b2c3…",
      "title": "Search by intent",
      "do": "Call playbook_search with a sentence for the job."
    }
  ]
}
```

- **title** — short name on search cards
- **description** — when to pick this procedure
- **steps** — serial. Unique titles. Random step ids. `do` is the work.

## CLI

Every command prints JSON (including errors).

```bash
playbook create demo --title "Demo" --description "when to pick this" --tags demo
playbook add-step demo --title "First" --do "Do the first thing."
playbook add-step demo --title "Middle" --do "Do the middle." --after "First"
playbook edit demo --title "Better title"
playbook search "I want to follow a procedure"
playbook load demo
playbook load demo --titles
playbook start demo --title "First"
playbook validate demo
playbook mcp
```

`search` is BM25 plus character n-grams. Hits are `id`, `title`, `description` only (default 8, cap 50). Weak matches are dropped.

`load` prints the whole procedure — every step with its `do` — in one call. `--titles` gives the outline only, for checking whether a procedure is the right one before reading it. `start` re-reads a single step: its `do`, its `position`, and the `prev` / `next` titles.

## MCP (Grok)

```bash
grok mcp add --scope user playbook -- playbook mcp
```

Use an absolute path to `playbook` if the spawned process will not have your shell `PATH`. Then `/mcps` and `r`, or a new session.

MCP results are compact JSON (no pretty-printing) to keep them cheap in context; the CLI stays indented for humans.

| Tool | Role |
| --- | --- |
| `playbook_search` | Intent search |
| `playbook_open` | Read a procedure whole (default), `full: false` for the outline, `at` for one step |
| `playbook_create` | New procedure |
| `playbook_edit` | Title / description / tags |
| `playbook_step` | `op`: add \| edit \| remove |
| `playbook_validate` | Validate |

## Tests

```bash
pip install -e .
python -m pytest tests/ -q
```

## License

MIT
