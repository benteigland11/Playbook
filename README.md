# Playbook

Procedures as JSON. Search them by intent, then load one in a single call.

Playbook is a small CLI and MCP server so an agent (or you) can **write, find, and follow** how-tos without dumping the whole playbook into context. Search narrows the store to one procedure; reading that procedure is a single call, not one call per step. CLI verbs stay split. MCP compresses related verbs into fewer tools. Everything is JSON.

Python 3.10+, **stdlib only** at runtime.

## What earns a procedure

**If it is outside the bounds of the neural net, it is a procedure.**

A model given a clear goal will derive `a → b → c` on its own, and derive it
better than a written procedure can, because it can see the actual code.
Writing that down buys nothing and costs tokens every time it is read. What a
model cannot derive is anything true about *your* world and false by default in
its own:

- **Inventory** — what already exists that would otherwise be rebuilt from
  scratch. A library, an internal service, a widget catalog.
- **Constraints** — invariants that are non-obvious and expensive to violate.
- **Orderings with a reason** — sequences where the order matters for a cause
  that cannot be inferred from the code in front of you.

The test is recomputability, not length or importance. "Roll back by pinning the
last good build" is a procedure if finding the last good build is peculiar to
your setup, and is noise if it is obvious from the deploy tool.

This bound is also what keeps a store finite. Treat procedures as step
sequences and every task appears to need one, so the store grows without ever
converging. Treat them as the things a model cannot know, and the set is capped
by how many such things you actually have. New procedures getting *rarer* over
time is the sign a playbook is working.

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

## Pointing the store somewhere else

The files stay a plain directory. Sync is a separate layer (git, Syncthing, anything else that copies a folder). Point Playbook at that folder; it does not sync on its own.

The CLI and `playbook mcp` use one resolver. Nothing is set means the platform directory above, exactly. Precedence:

1. `PLAYBOOK_STORE` (environment variable), when it is non-blank
2. `store` in the config file, when the file exists and sets it
3. the platform default

There is no CLI flag. A flag on one command would not match a server started as `playbook mcp`.

The value is the directory that holds the procedure JSON files (the same directory the default `.../procedures` path is). `~` and environment variables are expanded, so `~/notes/procedures`, `$HOME/notes/procedures`, and `${PLAYBOOK_ROOT}/procedures` all work. A relative path is relative to the process working directory, so prefer `~` or an absolute path in the config file. A blank value is ignored and the next source is used. The directory is created on the first write if it is missing, and looking up the path does not create it.

Config file (JSON). Playbook does not create this file. `store` is the procedure directory. `machine` and `usage` are for the [usage log](#usage-log) and do not change where procedures are stored.

| OS | File |
| --- | --- |
| Linux | `~/.config/playbook/config.json` (`XDG_CONFIG_HOME`) |
| macOS | `~/Library/Application Support/playbook/config.json` |
| Windows | `%APPDATA%\playbook\config.json` |

```json
{
  "store": "~/notes/procedures"
}
```

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

Every command prints JSON (including errors). `playbook usage list` prints a table unless `--json` is set.

```bash
playbook create demo --title "Demo" --description "when to pick this" --tags demo \
  --steps '[{"title": "First", "do": "Do the first thing."}]'
playbook add-steps demo --steps '[{"title": "Next", "do": "Do the next thing."}]'
playbook add-step demo --title "First" --do "Do the first thing."
playbook add-step demo --title "Middle" --do "Do the middle." --after "First"
playbook edit demo --title "Better title"
playbook search "I want to follow a procedure"
playbook load demo
playbook load demo --titles
playbook start demo --title "First"
playbook validate demo
playbook usage list
playbook mcp
```

Write a whole procedure in one call with `--steps` (a JSON array, or `-` to read it from stdin); `add-steps` appends or inserts a batch. The single-step `add-step` is still there for one-off edits.

`search` is BM25 plus character n-grams. Hits are `id`, `title`, `description` only (default 8, cap 50). Weak matches are dropped — but if that leaves nothing, the nearest few come back tagged `"weak": true` with a note, so a natural-language ask that shares no vocabulary with your titles returns candidates instead of a silent empty list.

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
| `playbook_write` | `op`: create \| append \| edit \| remove \| meta |

Three tools. A tool's schema is in context on every request, whether or not it
is called, so the surface is kept to find, read, and write.

Reads validate on their own — `playbook_open` fails with every schema error —
so a separate validate tool would only add schema to every request. The CLI
keeps `playbook validate` for when you want a report instead of an error.

The write verbs are one tool rather than three for the same reason, and because
a tool description is the only place the rule above is stated *at the moment of
the decision*. This README is not in context when a procedure gets minted;
`playbook_write` is. That is where "outside the bounds of the neural net" has to
live to have any effect.

The CLI keeps every verb split. Local verbs cost nothing per request, and
`create` / `add-step` / `edit-step` / `remove-step` read better in a shell than
a single command with a mode flag.

## Usage log

Playbook appends a local log of searches, opens, walks, ticks, and writes. The log does not change search hits, procedure text, or errors. A failure to write it does not fail the command.

The file is `<store>/.usage/<machine>.jsonl`: one JSON object per line, one file per machine. `PLAYBOOK_USAGE` points the directory somewhere else. Each line has a version, a UTC time, a run id, a machine id, `real` or `gym`, and a place. In a git checkout the place is the origin reduced to `host/owner/repo` (lowercase, no `.git`) plus `sub`, the path inside the repo. Outside a repo it is a home-relative path (`~/notes`), never an absolute `/home/<user>` path. `PLAYBOOK_PLACE` replaces that with a label such as `grokbot:routine:ai-news`.

The machine id is `PLAYBOOK_MACHINE`, or the `machine` string in the config file, or a hostname slug plus a short suffix saved once in `.usage/machines.json`. A run id is `PLAYBOOK_RUN` when that is set. Otherwise the MCP server mints one id at startup and reuses it, and the CLI mints a new id each invocation. `PLAYBOOK_RUN_KIND=gym` marks a gym run. `PLAYBOOK_HARNESS` is recorded only when it is set.

Free text (the query, a walk's purpose, a skip reason, a tick note) is cut to 200 characters. Token-shaped strings are stored as `[redacted]`.

Turn recording off with `"usage": false` in the config file, or `PLAYBOOK_USAGE_OFF=1`.

```bash
playbook usage list
playbook usage list --card follow-playbook --ev write --since 2026-11-01 --limit 20
playbook usage list --json
```

If the store is committed, `.usage/` stays out of git. When Playbook creates `.usage/` and the store directory already has a `.gitignore`, it appends a `.usage/` line if that line is missing. It does not create a `.gitignore` that was not already there.

## Tests

```bash
pip install -e .
python -m pytest tests/ -q
```

## License

MIT
