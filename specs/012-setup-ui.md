# Spec 012 — Setup & account-management UI

- **Status:** **implemented** — `setup_app.py` (stdlib `http.server`, navy/vanilla
  UI embedded so there are no external assets) ships the prerequisites view,
  credentials upload + validation, account add/reauthorize/remove/test (driving
  the existing `InstalledAppFlow` in a background thread), and Claude Desktop
  config generation with backup-and-merge. Localhost-bound, one-time-token +
  `Host`-header gated. Tested in `tests/test_setup_app.py` (pure helpers, the
  access gate, config merge, and add-account wiring with a fake flow). The
  un-fakeable paths — a real Google consent round-trip and a real Claude Desktop
  reading the written config — stay in the manual checklist.
- **New OAuth scope required:** none — it drives the *existing* `add_account.py`
  OAuth flow and reads/writes the same `tokens/` files.
- **Destructive:** can delete a `tokens/<name>.json` (revokes local access,
  reversible by reauthorizing) and can write `claude_desktop_config.json` (backed
  up first). Both are gated behind an explicit confirm.

## Problem

Everything this server *does* is now easy; everything about *setting it up* is
still the fiddly part, and the README says as much. The friction, in order of
how often a user hits it:

1. **Ongoing reauthorization (worst).** Google expires test-user refresh tokens
   every ~7 days (see README "Why do I have to reauthorize so often?"). Today the
   fix is: notice the `invalid_grant` error in a Claude conversation, open a
   terminal, remember the account name, run `add_account.py <name>`. That's a
   context switch into the CLI every week, per account.
2. **First-run setup is terminal-only.** `python -m venv`, `pip install`, then
   `add_account.py <name>` once per account — fine for a developer, a wall for
   everyone else.
3. **Hand-editing `claude_desktop_config.json`.** Absolute paths, escaped
   backslashes on Windows, valid JSON. The README's own troubleshooting section
   is mostly "you got this file wrong."
4. **No visibility.** Nothing shows which accounts are configured, which real
   address each maps to, or which are expired *until* a tool call fails.

None of this needs a new capability — it needs a friendlier front door onto the
`accounts.py` / `add_account.py` machinery that already exists.

## Goals / non-goals

**Goals**

- One command (`python setup_app.py`) opens a local web page that handles
  install-check, credentials, account add/remove/**reauthorize**, status, and
  Claude Desktop config generation.
- Make the weekly reauthorization a single click with the account already named.
- Surface account status (real email via `getProfile`, valid / expiring / expired)
  at a glance.

**Non-goals**

- **It cannot create the Google Cloud OAuth client for you.** Enabling the Gmail
  API, configuring the consent screen, adding test users, and downloading
  `credentials.json` happen in Google Cloud Console and can't be automated from a
  local app. The UI *guides* Step 1 (links, a checklist, and validation of the
  resulting file) but does not replace it. The spec must not pretend otherwise.
- It does not remove the 7-day Google limitation — only makes living with it a
  click instead of a terminal trip.
- Not hosted, not multi-user, no cloud component. Local only, same as the server.

## Why a local web app (vs. desktop GUI / TUI)

The OAuth flow already needs a browser and already runs a temporary localhost
redirect server (`InstalledAppFlow.run_local_server`, used by `add_account.py`).
A small local web app is the least-surprising fit: no new GUI toolkit, works
identically on every OS, and reuses the browser the OAuth step needs anyway. A
Tkinter desktop app adds cross-platform packaging pain; a TUI is friendlier than
today but still a terminal. **Recommendation: a localhost web app.**

## Proposed shape

A new, self-contained `setup_app.py` (a sibling of `add_account.py` — **never
imported by `server.py`**). Running it:

```
$ python setup_app.py
Setup UI running at http://127.0.0.1:7864/?token=<random>
(opening your browser…)
```

It binds to **127.0.0.1 only**, prints and auto-opens a URL carrying a
one-time token (see Security), and serves a single page plus a few JSON
endpoints. Screens:

1. **Prerequisites** — Python version, whether deps are installed, whether
   `credentials.json` is present and is a *Desktop-app* OAuth client. Each item
   is a green check or an actionable red message (e.g. "credentials.json looks
   like a Web client, not Desktop — re-download as Desktop app").
2. **Step 1 helper** — the Google Cloud checklist from the README, inline, with
   deep links, and a **credentials.json** drop zone / file picker that validates
   the file shape before saving it next to `server.py`.
3. **Accounts** — a table of configured accounts: nickname, real email address
   (from `gmail_client.account_info`), and a status badge:
   - **valid** (token refreshes fine),
   - **expiring** (valid now; show days until the ~7-day window if derivable),
   - **expired / revoked** (refresh fails → the `AccountError` case).
   Row actions: **Reauthorize** (one click, name pre-filled), **Remove**,
   **Test** (calls `getProfile`, confirms the real address).
   Plus an **Add account** field (nickname → button).
4. **Claude Desktop config** — shows the exact `mcpServers` snippet with correct
   absolute paths for *this* install, a **Copy** button, and an **Apply** button
   that merges the entry into the real `claude_desktop_config.json` (backup
   first; never clobbers other servers). Also an optional field to set the
   `GMAIL_MCP_SEND_PHRASE` env var (spec 007) into the generated config.

## Driving the OAuth flow from a button

Reuse the existing flow rather than reinventing it. On **Add / Reauthorize**:

- The backend starts `InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE,
  accounts.SCOPES).run_local_server(port=0)` **in a background thread** — this
  opens the user's browser to Google's consent screen exactly as
  `add_account.py` does today.
- The page polls a `GET /accounts/<name>/status` endpoint; when the flow thread
  finishes it calls `accounts.save_credentials(name, creds)` and the row flips to
  **valid**.
- If the flow errors (wrong client type, user not a test user → the README's
  "Access blocked" case), surface Google's message verbatim in the row.

This keeps `accounts.SCOPES` as the single source of truth for scopes, so the UI
automatically requests whatever the server currently needs (readonly + compose +
modify + settings.basic + settings.sharing) with no duplication.

## Status / expiry detection (without exposing secrets)

Reuse `accounts.load_credentials(name)`:

- It already refreshes and re-saves, and raises `AccountError` when a token is
  expired/revoked. Catch that → **expired**. Success → **valid**.
- `Credentials.expiry` gives the *access*-token expiry (minutes), not the 7-day
  *refresh* window, so "expiring in N days" can only be estimated from the token
  file's mtime, not read exactly — the spec should label it an estimate, not
  claim precision.
- **Never** read out, render, or log the contents of `tokens/*.json` or
  `credentials.json` — only derived status and the email address from
  `getProfile`. This is the CLAUDE.md hard constraint and it applies fully here.

## Tech choice: stdlib, no new runtime dependency

Implement with the standard library `http.server` (a `BaseHTTPRequestHandler`
with a handful of routes) plus one static HTML/JS page. Rationale: it keeps the
pinned runtime `requirements.txt` untouched — `setup_app.py` is optional tooling
and should not add a Flask/FastAPI pin to a user who only runs the server. If the
handler grows unwieldy, Flask behind a new `requirements-setup.txt` (kept out of
the runtime set, like `requirements-dev.txt`) is the fallback — but start stdlib.

## Security considerations (this is a local server that can trigger OAuth)

A localhost web app is reachable by any process on the machine and, via the
browser, is a target for CSRF / DNS-rebinding from any web page the user has
open. Because this one can start OAuth flows, delete token files, and write the
Claude Desktop config, treat it carefully:

- **Bind to `127.0.0.1` only** — never `0.0.0.0`.
- **One-time token** generated at launch, embedded in the opened URL and required
  on every request (state-changing ones require it in a custom header, which
  forces a CORS preflight and blocks drive-by cross-site POSTs). Printed only to
  the console.
- **Validate the `Host` header** is `127.0.0.1`/`localhost` to blunt DNS
  rebinding; reject anything else.
- **State changes are POST** with the token; reads are GET. No directory listing;
  only known routes.
- **Confirm destructive actions.** Removing an account deletes its local token but
  does **not** revoke the grant on Google's side — the UI must say so and link to
  [myaccount.google.com/permissions](https://myaccount.google.com/permissions),
  consistent with README "Security".
- **Back up `claude_desktop_config.json`** (timestamped copy) before writing;
  parse-merge only the `gmail-multi-account` entry; if the existing file is
  invalid JSON, refuse to write and fall back to the copy/paste snippet.
- **No secrets in responses or logs**, per the hard constraint.
- The app is short-lived — it's a setup tool, not a daemon. Document stopping it
  (Ctrl-C) when done; it should not be left running or added to startup.

## Alignment with existing conventions

- `setup_app.py` is a standalone entry point like `add_account.py`, **not
  imported by `server.py`** — the running MCP server gains no web surface.
- Reuses `accounts.py` (`SCOPES`, `load_credentials`, `save_credentials`,
  `list_account_names`, `AccountError`) and `gmail_client.account_info` rather
  than duplicating any of it.
- CLAUDE.md "Layout" gets one line for `setup_app.py`; the hard constraint about
  never exposing token/credentials contents extends to it explicitly.

## Testing / manual verification

- The routing/handler logic is testable offline the same way the server is:
  factor the request handlers to call small functions that take injected fakes,
  and mock the OAuth flow (`InstalledAppFlow`) and `_service` so no real browser
  or credentials are needed. Add `tests/test_setup_app.py`.
- Assert: token requirement rejects requests without the one-time token; `Host`
  validation rejects foreign hosts; the config-merge preserves an existing
  unrelated `mcpServers` entry and writes a backup; no endpoint ever returns file
  contents of `tokens/`/`credentials.json`.
- The genuinely un-fakeable paths (a real Google consent round-trip, a real
  Claude Desktop reading the written config) stay in a short manual checklist, as
  with the rest of the project.

## Out of scope / open questions

- **Bundling as a double-click app** (PyInstaller, an `.app`/`.exe`) so a
  non-developer never opens a terminal at all — a real usability win, but a
  separate packaging effort; this spec assumes `python setup_app.py`.
- **Auto-launching the setup UI from a failed tool call** (e.g. the server's
  `AccountError` message could deep-link to it) — nice, but couples the server to
  the tool; leave for later.
- **Open question:** should "Reauthorize" be promoted to a tiny background helper
  that notices expiry and notifies the user, or stay purely on-demand in the UI?
  On-demand is simpler and matches the tool's no-daemon posture; a notifier is a
  bigger commitment. Recommend on-demand first.
