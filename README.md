# Gmail Multi-Account MCP Server (for Claude Desktop)

A local MCP server that lets Claude Desktop search, read, draft, and send
email across as many Gmail accounts as you configure — "check my work
inbox for anything from Sarah" / "draft a reply from my personal account"
— all running on your own machine. Nothing is hosted; Claude Desktop
launches this script itself.

**Scope, on purpose:** this can search, read, draft, send, and organize
mail (labels, archive, read/unread, star, trash) — but sending stays a
deliberate two-step confirm-then-send, and it deliberately **cannot
permanently delete** anything: trash is recoverable, and Gmail's
hard-delete scope is left out on purpose. See "Scopes" below.

---

## What you'll need

- Python 3.10+
- A Google account for each Gmail inbox you want to connect
- Claude Desktop
- About 15 minutes for the one-time setup

---

## Step 1 — Create a Google Cloud OAuth client

This is the only fiddly part, and you only do it once (it covers *all* the
accounts you'll add later, not one client per account).

1. Go to **[console.cloud.google.com](https://console.cloud.google.com/)** and create a new project (top-left project dropdown → New Project). Any name is fine.
2. Enable the Gmail API: **Menu ☰ → APIs & Services → Library**, search "Gmail API," click it, click **Enable**.
3. Configure the consent screen: **Menu ☰ → Google Auth platform → Branding**. Click **Get started**.
   - App name: anything (e.g. "Personal Gmail MCP")
   - User support email: your email
   - Audience: **External** (this is the right choice even for personal use — "Internal" only exists if you're on a Google Workspace account)
   - Contact email: your email → **Create**
4. Add test users: still in **Google Auth platform**, click the **Audience** tab → **Test users** → **Add users**. Add **every Gmail address you plan to connect** (your personal *and* your work address, if both apply). Unverified apps can only be authorized by addresses on this list.
5. Create the OAuth client: **Google Auth platform → Clients → Create Client**.
   - Application type: **Desktop app**
   - Name: anything (e.g. "gmail-mcp-local")
   - Click **Create**, then download the JSON.
6. Rename the downloaded file to `credentials.json` and place it in this folder (next to `server.py`).

You'll see an "unverified app" warning when you actually authorize each
account in Step 3 — that's expected for a personal project like this one.
Click **Advanced → Go to [your app name] (unsafe)** to proceed; it's your
own app, so this is safe.

---

## Step 2 — Install dependencies

```bash
cd gmail-mcp-server
python3 -m venv venv
venv/bin/pip install -r requirements.txt   # Windows: venv\Scripts\pip install -r requirements.txt
```

---

## Step 3 — Authorize each Gmail account

Run this once per account, using whatever name you want Claude to call it by:

```bash
venv/bin/python add_account.py personal
venv/bin/python add_account.py work
```

Each run opens your browser to Google's sign-in. Log in with *that*
account, approve, and a token is saved to `tokens/<name>.json`. You can
add more accounts later the same way, any time.

---

## Step 4 — Point Claude Desktop at the server

1. Open Claude Desktop → **Settings → Developer → Edit Config**. This opens (or creates) `claude_desktop_config.json`.
2. Add an entry under `mcpServers`, using the **absolute path** to this folder:

```json
{
  "mcpServers": {
    "gmail-multi-account": {
      "command": "/absolute/path/to/gmail-mcp-server/venv/bin/python",
      "args": ["/absolute/path/to/gmail-mcp-server/server.py"]
    }
  }
}
```

(Windows: use the venv's `python.exe`, e.g.
`"C:\\Users\\you\\gmail-mcp-server\\venv\\Scripts\\python.exe"`, and escape
backslashes like that.)

3. Save the file and fully restart Claude Desktop.

That's it — ask Claude something like *"what accounts do you have access
to?"* to confirm it can see the server, then try *"search my work email
for invoices from this month."*

---

## Tools this server exposes

| Tool | What it does |
|---|---|
| `list_accounts` | Lists configured account names |
| `gmail_search(account, query, max_results)` | Searches using Gmail's own query syntax (`from:`, `is:unread`, `after:`, etc.) |
| `gmail_read(account, message_id)` | Returns full headers + body of one message |
| `gmail_draft(account, to, subject, body, cc, thread_id)` | Creates a draft — never sends |
| `gmail_list_drafts(account, max_results)` | Lists saved drafts (subject + recipient only) |
| `gmail_get_draft(account, draft_id)` | Fetches a draft's full contents, for a last check before sending |
| `gmail_send_draft(account, draft_id)` | Sends an existing draft. Irreversible — see "Sending mail" below |
| `gmail_modify_labels(account, message_id, add, remove)` | Adds/removes labels on a message (by label id) |
| `gmail_mark_read` / `gmail_mark_unread(account, message_id)` | Toggles the read state |
| `gmail_archive` / `gmail_unarchive(account, message_id)` | Removes from / returns to the inbox |
| `gmail_star` / `gmail_unstar(account, message_id)` | Toggles the star |
| `gmail_trash` / `gmail_untrash(account, message_id)` | Moves to / restores from Trash (recoverable — **not** a permanent delete) |
| `gmail_create_label` / `gmail_rename_label` / `gmail_delete_label(account, ...)` | Manages your user labels |

---

## Sending mail

Sending is split into two tools on purpose: `gmail_draft` only ever saves
a draft, and a separate `gmail_send_draft(account, draft_id)` sends one
that already exists. There's no single tool that composes and sends in
one call.

This matters because a model that can send email autonomously is a
different risk profile than one that can only draft it — a
misunderstood request, a hallucinated detail, or (worst case) an
instruction smuggled inside an email it just read become irreversible
instead of catchable. Splitting send into its own explicit step means:

- Claude has to show you what a draft says (via `gmail_draft`'s or
  `gmail_get_draft`'s output) before it can act on sending it.
- `gmail_send_draft`'s tool description explicitly tells the calling
  model to only use it after you've clearly said to send *that* draft —
  never automatically right after creating one, and never because of
  something read inside an email rather than from you.
- It's marked `destructive_hint=True` in its MCP annotations, which
  well-behaved MCP clients (Claude Desktop included) use to decide how
  cautiously to treat a tool — worth knowing this is a hint clients can
  use for their own approval UI, not an enforced restriction on its own.

None of this is a hard technical guarantee — it relies on whatever Claude
you're running behaving as instructed, the same as any tool. If you want
zero ambiguity, just don't ask Claude to send anything you haven't
reviewed yourself; nothing stops you from also just sending drafts
manually from Gmail once Claude's written them, which sidesteps this
entirely.

## Scopes

This server requests three OAuth scopes:

- `gmail.readonly` — search and read
- `gmail.compose` — create/read/update/delete drafts, **and send them**
  (this is Google's scope, not a design choice here — `gmail.compose`
  bundles sending in with draft management, there's no narrower scope
  that offers drafts without send).
- `gmail.modify` — organize mail: add/remove labels, mark read/unread,
  archive, star, and move messages to **Trash**. This covers all
  read/write on mail *except* permanent deletion.

**What it deliberately does *not* request:**

- `https://mail.google.com/` (permanent delete / empty Trash) — left out
  on purpose. `gmail_trash` moves messages to Trash, where they're
  recoverable for ~30 days, so nothing this server does is truly
  irreversible. There is no hard-delete tool because the token can't
  hard-delete.
- `gmail.settings.*` (filters, forwarding, send-as, delegation) — not
  yet. Those arrive with later features; today the token cannot change
  any account settings.

Because the token can now send mail **and** reorganize or trash it, treat
`tokens/*.json` as at least as sensitive as the password to that inbox.
See Security below.

## Why do I have to reauthorize so often?

Google restricts unverified ("Testing" mode) apps that use Gmail scopes:
**refresh tokens for test users expire after 7 days**, regardless of how
you built the app. This isn't a bug here — it's Google's policy for any
personal script like this one. When it expires, you'll see a message
telling you to rerun `add_account.py <name>` for that account; that's all
you need to do. Getting Google to lift this (moving your OAuth app to
"Production") requires their formal app-verification process, including a
security assessment for these particular scopes — real weeks of process
for a tool only you use, so it's not worth doing here.

## Security

- Each account's refresh token lives in plain text in `tokens/<name>.json`. Anyone with that file can read, send, and now also reorganize or trash that account's mail until it's revoked (trashing is recoverable; permanent deletion is not possible — see Scopes). Don't sync this folder anywhere shared; `.gitignore` already excludes `tokens/` and `credentials.json`.
- Everything runs locally — Claude Desktop spawns this script as a subprocess on your machine. Your email data is included in whatever conversation you ask about it in (same as any Claude tool result), but nothing is hosted or exposed to the internet by this server itself.
- To revoke access entirely, go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions) on the relevant account and remove the app, then delete its `tokens/<name>.json` file here.

---

## Troubleshooting

- **"No module named mcp"** — you're not using the venv's Python. Double check the `command` path in your Claude Desktop config.
- **Server doesn't show up in Claude** — config JSON is probably invalid (missing comma, wrong path). Check Claude Desktop's MCP logs (same Developer settings page) for the exact error.
- **`invalid_grant` / "authorization has expired"** — see "Why do I have to reauthorize so often?" above; just rerun `add_account.py <name>`.
- **"Access blocked: this app is blocked"** instead of the usual warning — you likely forgot to add that Google address as a test user in Step 1.4.

---

## Extending this later

- **Organizing mail (built in):** labels, archive, read/unread, star, and Trash now ship via `gmail.modify` (see the tools table and Scopes). Permanent deletion is intentionally excluded. If you set this server up before these existed, rerun `add_account.py` once per account to pick up the new scope.
- **Account settings (filters, forwarding, send-as, delegation):** not yet — planned via `gmail.settings.basic` / `gmail.settings.sharing`. When added they'll need another `add_account.py` pass per account, and carry heavier confirm-before-acting guards (forwarding/delegation are data-exfiltration vectors).
- **More accounts:** just run `add_account.py <new-name>` again — no config changes needed.
- **Stricter send confirmation (built in):** two harder gates than docstring wording now ship in `gmail_send_draft`:
  - **Audit log** — every successful send appends one line (timestamp, account, draft/message id, recipient, subject — never any token data) to `sent.log` next to `server.py` (gitignored). It's best-effort: a logging error never blocks or undoes a send.
  - **Confirmation phrase** — set the env var `GMAIL_MCP_SEND_PHRASE` (e.g. in your Claude Desktop server config's `env`) to any phrase, and `gmail_send_draft` will refuse to send unless it's called with that exact phrase in its `confirm` argument. This makes "confirm before sending" a code-level precondition, not just an instruction the model is asked to follow. Unset (the default), sending behaves exactly as before.
