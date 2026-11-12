# Playbook

Procedures live as JSON. This repo is a small CLI and an MCP server: search by intent, then load one procedure in a single call. Python 3.10+. The runtime uses the standard library only. pytest is the optional dev extra.

## Layout

`playbook/` is the application:

- `cli.py` parses commands and prints indented JSON, including errors.
- `mcp.py` serves MCP on stdio (JSON-RPC 2.0, one object per line).
- `ops.py` creates, edits, searches, loads, links, and walks procedures.
- `store.py` resolves the directory that holds procedure files.
- `library.py` keeps the decay ledger, `.library.json`, beside that directory.
- `widget_api.py` re-exports the procedure engine.

`tests/` covers the CLI, the MCP server, the store, and ops.

`cg/` holds four Cartograph widgets. Each has `src/`, `tests/`, `examples/`, and `widget.json`, and each has its own tests. `pyproject.toml` maps them into importable packages:

| Widget | Package | Role |
| --- | --- | --- |
| `cg/data_gofai_procedure_python` | `playbook.engine` | procedure documents |
| `cg/infra_app_paths_python` | `playbook.app_paths` | per-user paths |
| `cg/universal_bm25_ngram_python` | `playbook.bm25_ngram` | BM25 and character n-gram search |
| `cg/infra_usage_decay_ledger_python` | `playbook.usage_decay_ledger` | use-it-or-lose-it ledger |

A widget is self-contained. Logic it needs is copied into its `src/`.

## Hard rules

- Stay on Python 3.10 or newer. Keep the runtime on the standard library.
- Keep the MCP surface to `playbook_search`, `playbook_open`, and `playbook_write`. Extend one of those tools before adding another. Every tool schema is in context on every request.
- Leave the `mcp` subcommand and those three tool names in place. Ben's Antigravity runs `playbook mcp` from an editable install.
- Keep `playbook mcp` stdout pure JSON-RPC. MCP results stay compact. The CLI stays indented for people.
- Leave the old `widgets` field off procedures. Files that still have it load; the field is dropped on read and omitted on the next write.
- Write commit messages in plain English, with no em dashes.
- Leave `.playbook/` run state uncommitted (the search mark, open walks, and plan files) and leave secrets uncommitted. `.playbook/` and `.env` are gitignored.

## Tests

Use a throwaway venv and an editable install. Point `PLAYBOOK_STORE` at a temp directory so a run cannot touch a real store. Also set `XDG_DATA_HOME` to a temp directory: `tests/conftest.py` clears `PLAYBOOK_STORE` and redirects `XDG_CONFIG_HOME`, and the repo tests point `XDG_DATA_HOME` at their own `tmp_path`.

```bash
python3 -m venv /tmp/playbook-venv
/tmp/playbook-venv/bin/pip install -e ".[dev]"
PLAYBOOK_STORE="$(mktemp -d)" XDG_DATA_HOME="$(mktemp -d)" \
  /tmp/playbook-venv/bin/python -m pytest tests/ -q
for d in cg/*/; do
  (cd "$d" && PLAYBOOK_STORE="$(mktemp -d)" XDG_DATA_HOME="$(mktemp -d)" \
    /tmp/playbook-venv/bin/python -m pytest tests -q)
done
```

Run that widget loop from the repo root. pytest has to start inside each widget directory so `from src...` resolves. `cg/infra_app_paths_python` does not add itself to `sys.path`.

## Store

The CLI and `playbook mcp` share one resolver. There is no store flag.

1. `PLAYBOOK_STORE`, when it is set and not blank.
2. `store` in the config file, when that file exists and sets a non-blank string. On Linux the file is `~/.config/playbook/config.json` (`$XDG_CONFIG_HOME/playbook/config.json`). Playbook does not create it.
3. Otherwise `~/.local/share/playbook/procedures`, or `$XDG_DATA_HOME/playbook/procedures` when `XDG_DATA_HOME` is set.

`~` and environment variables in a path are expanded. A blank value is ignored and the next source is used. The directory is created on the first write. Looking up the path does not create it. On macOS the default directory is `~/Library/Application Support/playbook/procedures` and the config file sits beside it. On Windows the default directory is `%LOCALAPPDATA%\playbook\Data\procedures` and the config file is `%APPDATA%\playbook\config.json`.

## What earns a card

A procedure is worth writing when it holds something a model would not already know: inventory of what already exists, a constraint that is expensive to violate, or an ordering whose reason is not in the code in front of you. Improve an existing card when one is close. Keep unrelated steps off a card. `playbook create` in a working tree waits until `playbook search` has been run there, and it refuses a twin of a procedure already in the store.

## Using Playbook in any repo

At the start of non-trivial work, search with the task in your own words: `playbook search "..."` or the `playbook_search` tool. Open the best card with `playbook load <id>` or `playbook_open`. If a step was wrong or missing, improve that card before you finish.
