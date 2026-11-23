# CLAUDE.md

See @README.md for what this project is and the full end-user setup
walkthrough. This file is for whoever — human or Claude — edits the code;
it's deliberately not a duplicate of the README.

## Layout

- `accounts.py` — token storage/loading/refresh, defines `SCOPES`
- `gmail_client.py` — thin Gmail API wrapper; every function takes `account_name` first and resolves credentials itself via `accounts.load_credentials`
- `calendar_client.py` — thin Google Calendar v3 wrapper, same pattern as `gmail_client`; backs the `calendar_*` tools (specs/014). Notifying attendees (`send_updates="all"`) is the Gmail-"send" analog: gated by `GMAIL_MCP_SEND_PHRASE` and audited.
- `server.py` — MCP tool definitions (stdio transport); thin wrappers around `gmail_client`. Also sends the server `instructions` every session: `ACCOUNT_ROUTING_INSTRUCTIONS` (all accounts are live at once; map company/domain/address to a nickname via `list_accounts`; fall back to `gmail_search_all` before saying "not found"; prefer this server over a single-account Gmail connector), plus the update note when there is one
- `add_account.py` — one-time interactive OAuth CLI, not imported by the server
- `setup_app.py` — optional local web UI (stdlib `http.server`) for setup/account management; standalone tooling, **not imported by the server**; reuses `accounts`/`gmail_client`; localhost-only + one-time-token gated; never renders token/credentials contents (specs/012)
- `main.py` — packaging entry point with `serve` (stdio server) / `setup` (UI) subcommands; what the built executable runs (specs/013). `build.py` packages it with PyInstaller. `serve` hides the console window on Windows so it runs headless when Claude Desktop launches it; a no-argument launch (double-click) opens `setup`.
- `version.py` — single source of truth for `__version__`; bump it in lockstep with the release tag (specs/015)
- `updates.py` — best-effort GitHub-release update check (notify-only, stdlib, cached in `DATA_DIR`). Feeds the setup-UI banner, the `check_for_updates` tool, and the session-start "update available" note in the server `instructions`. `GMAIL_MCP_NO_UPDATE_CHECK=1` disables it. Never on `serve`'s stdout (specs/015)
- `tests/` — pytest suite (170 tests) that mocks `gmail_client._service`, so it runs offline with no real credentials or token files. Covers the pure helpers, the `AccountError` convention, every tool group (read/draft/organize/settings/forwarding/attachments), the send audit + confirm gate, the setup UI's request handling, and the packaging data-dir/dispatch logic. The live-account manual loop below still covers what a fake can't (real OAuth/refresh, an actual send, a real PyInstaller build).

## Commands

- Install deps: `venv/bin/pip install -r requirements.txt`
- Install dev/test deps: `venv/bin/pip install -r requirements-dev.txt`
- Compile-check after any edit: `venv/bin/python -m py_compile *.py`
- Run the test suite (fast, offline, no credentials needed): `venv/bin/python -m pytest -q`
  (the checked-in venv is actually `.venv/` on Windows: `.venv/Scripts/python -m pytest -q`)
- Authorize an account (opens a real browser — must run locally, can't run headless): `venv/bin/python add_account.py <name>` — or `python setup_app.py` for the web UI
- Run the packaged entry points in dev: `python main.py serve` (stdio server) / `python main.py setup` (UI)
- Build a standalone binary (per-OS; PyInstaller can't cross-compile): `venv/bin/pip install -r requirements-build.txt` then `python build.py` → single-file `dist/gmail-mcp.exe`. Builds from the committed `gmail-mcp.spec` (onefile), which drops googleapiclient's bundled discovery docs for ~585 unused APIs (keeps only `gmail.v1.json`) to cut the bundle ~143 MB → ~44 MB. `build.py` refuses to run if `credentials.json`/`tokens/`/logs are in the tree (never bundle secrets); `main.py serve` must keep stdout free of anything but MCP protocol.
- Exercise tools directly, without Claude Desktop or a live Gmail account:
  ```python
  import asyncio, server
  asyncio.run(server.server.call_tool("list_accounts", {}))
  ```
  This was the actual verification method used while building this — no mocks, just real calls that hit the clean "no account configured" error path when `tokens/` is empty.
- After any change to `server.py`, fully restart Claude Desktop — it spawned this as a subprocess at launch and won't pick up edits otherwise.

## Conventions

- `server.py` tool functions catch `accounts.AccountError` only and return `str(e)`. Everything else (bad `message_id`, Google API errors, network failures) should propagate rather than being swallowed — a visible error is more useful here than a silently wrong result.
- Every tool needs `annotations=ToolAnnotations(...)`: `read_only_hint=True` for anything that doesn't write, `destructive_hint=True` for anything irreversible. An unannotated tool makes Claude Desktop treat it as maximally risky by default — safe, but needless approval friction for what should be a plain read.
- Mutable state (tokens, credentials.json, sent.log, settings.log, attachments/) lives under `accounts.DATA_DIR` — the repo dir in a source run, a per-user config dir when frozen (spec 013). Don't hardcode `Path(__file__)` for state; use `accounts.DATA_DIR` so packaged builds write to a stable, writable location. `GMAIL_MCP_DATA_DIR` overrides it (and is the test seam).

## Hard constraints — read the reasoning before changing these

- **`mcp==2.0.0` is pinned on purpose.** This SDK renamed `FastMCP` (`mcp.server.fastmcp`) to `MCPServer` (`mcp.server.mcpserver`) at some point between versions. Before bumping this dependency, re-check the installed version's actual import path and `.tool()`/`.run()` signatures rather than assuming older `FastMCP`-style examples still apply.
- **Scopes: Gmail (`gmail.readonly` + `gmail.compose` + `gmail.modify` + `gmail.settings.basic` + `gmail.settings.sharing`) + Calendar (`calendar.readonly` + `calendar.events`).** The old readonly+compose-only minimalism was deliberately retired (see `specs/README.md`). `gmail.modify` powers organize-mail; `gmail.settings.basic`/`.sharing` power filters/vacation/IMAP-POP/forwarding/send-as/delegation; the Calendar scopes power the `calendar_*` tools (read + create/update/delete/RSVP events). The remaining hard line: **do NOT add `https://mail.google.com/`** — permanent deletion is excluded on purpose, so `gmail_trash` stays recoverable and nothing this server does is irreversible. Principle for any *future* scope: don't request one before a tool uses it. Any scope change means rerunning `add_account.py` per account (or the setup UI's Reauthorize).
- **Sending is, and must stay, two calls.** `gmail_draft` only ever creates a draft; `gmail_send_draft(account, draft_id)` is the only thing that can send, and only operates on a draft that already exists. Don't add a `send=True` shortcut to `gmail_draft` or otherwise merge these — the split, plus the docstring instructing the calling model to confirm with the user first, is the entire safety design here (README "Sending mail").
- **Never log, print, or return the contents of `tokens/*.json` or `credentials.json`** in any new code — both hold live credentials and are gitignored for a reason.
- **The 7-day reauthorization cycle is Google's policy for unverified apps using restricted Gmail scopes — it is not a bug to fix in this code.** See README "Why do I have to reauthorize so often?" before spending time on it; the real fix (Google app verification) is disproportionate for a single-user tool.

## Adding a new Gmail operation

Add the API call to `gmail_client.py` (taking `account_name` first, resolving its own credentials), then a matching thin tool in `server.py`: try/except `AccountError`, JSON-dump list/dict results, and annotations that honestly reflect whether it reads, writes, or destroys. Follow an existing tool as the template rather than inventing a new error-handling or return-shape convention.
