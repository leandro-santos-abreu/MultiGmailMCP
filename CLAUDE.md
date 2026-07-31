# CLAUDE.md

See @README.md for what this project is and the full end-user setup
walkthrough. This file is for whoever — human or Claude — edits the code;
it's deliberately not a duplicate of the README.

## Layout

- `accounts.py` — token storage/loading/refresh, defines `SCOPES`
- `gmail_client.py` — thin Gmail API wrapper; every function takes `account_name` first and resolves credentials itself via `accounts.load_credentials`
- `server.py` — MCP tool definitions (stdio transport); thin wrappers around `gmail_client`
- `add_account.py` — one-time interactive OAuth CLI, not imported by the server
- `tests/` — pytest suite that mocks `gmail_client._service`, so it runs offline with no real credentials or token files. Covers the pure helpers, the `AccountError` convention, and the send audit/confirm gate. The live-account manual loop below still covers what a fake can't (real OAuth/refresh, an actual send).

## Commands

- Install deps: `venv/bin/pip install -r requirements.txt`
- Install dev/test deps: `venv/bin/pip install -r requirements-dev.txt`
- Compile-check after any edit: `venv/bin/python -m py_compile *.py`
- Run the test suite (fast, offline, no credentials needed): `venv/bin/python -m pytest -q`
  (the checked-in venv is actually `.venv/` on Windows: `.venv/Scripts/python -m pytest -q`)
- Authorize an account (opens a real browser — must run locally, can't run headless): `venv/bin/python add_account.py <name>`
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

## Hard constraints — read the reasoning before changing these

- **`mcp==2.0.0` is pinned on purpose.** This SDK renamed `FastMCP` (`mcp.server.fastmcp`) to `MCPServer` (`mcp.server.mcpserver`) at some point between versions. Before bumping this dependency, re-check the installed version's actual import path and `.tool()`/`.run()` signatures rather than assuming older `FastMCP`-style examples still apply.
- **Scopes are `gmail.readonly` + `gmail.compose` only.** No `gmail.modify`, no `gmail.send`. This is deliberate minimalism (README "Scopes"), not an oversight. Adding `gmail.modify` — e.g. for archive/mark-read tools — means every existing user must rerun `add_account.py` per account; treat that as a breaking change, not a quick add.
- **Sending is, and must stay, two calls.** `gmail_draft` only ever creates a draft; `gmail_send_draft(account, draft_id)` is the only thing that can send, and only operates on a draft that already exists. Don't add a `send=True` shortcut to `gmail_draft` or otherwise merge these — the split, plus the docstring instructing the calling model to confirm with the user first, is the entire safety design here (README "Sending mail").
- **Never log, print, or return the contents of `tokens/*.json` or `credentials.json`** in any new code — both hold live credentials and are gitignored for a reason.
- **The 7-day reauthorization cycle is Google's policy for unverified apps using restricted Gmail scopes — it is not a bug to fix in this code.** See README "Why do I have to reauthorize so often?" before spending time on it; the real fix (Google app verification) is disproportionate for a single-user tool.

## Adding a new Gmail operation

Add the API call to `gmail_client.py` (taking `account_name` first, resolving its own credentials), then a matching thin tool in `server.py`: try/except `AccountError`, JSON-dump list/dict results, and annotations that honestly reflect whether it reads, writes, or destroys. Follow an existing tool as the template rather than inventing a new error-handling or return-shape convention.
