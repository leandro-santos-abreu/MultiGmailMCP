# Spec 005 — Correct reply / forward draft scaffolding

- **Status:** **implemented** — `gmail_draft_reply` + `gmail_draft_forward` ship
  in `gmail_client.py` / `server.py`, with the shared `_build_message` helper now
  extracted (create/update/reply/forward all use it). Tested in
  `tests/test_reply_forward.py`, including a guard that the refactor left
  `create_draft` output unchanged.
- **Tier:** 1 (fits existing `gmail.compose` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** no (creates a draft; sending still requires `gmail_send_draft`)

## Problem

Replying today means the model hand-assembles a `gmail_draft` call: it has to
copy the original sender into `to`, prefix the subject with `Re:`, pass the
right `thread_id`, and — if it wants a quoted original — paste the body in
itself. Two things go wrong in practice:

1. **Threading fidelity.** `create_draft` only sets `threadId`
   ([gmail_client.py:109](../gmail_client.py)). Gmail will usually thread on
   `threadId` + subject, but proper RFC 5322 threading also wants `In-Reply-To`
   and `References` headers pointing at the original `Message-ID`. Without them,
   some clients (and Gmail in edge cases) can split the reply into its own
   thread.
2. **Recipient errors.** "Reply all" requires unioning the original `To`/`Cc`
   minus the account's own address. The model gets this wrong often enough that
   a purpose-built tool is worth it.

## Proposed tool

```python
gmail_draft_reply(account, message_id, body, reply_all=False, quote_original=True) -> str
```

Given the message being replied to, it fetches that message, derives the correct
`to`/`cc`, `Re:`-subject, `threadId`, and threading headers, optionally prepends
a quoted copy of the original, creates the draft, and returns the same
"Draft created ... Not sent." confirmation as `gmail_draft`.

A `gmail_draft_forward(account, message_id, to, body, quote_original=True)`
variant follows the same pattern (`Fwd:` subject, recipients supplied by the
caller rather than derived, no threading headers since a forward starts a new
thread).

## `gmail_client.py`

Two pieces of new logic:

1. **Recipient derivation** from the original message's headers:
   - reply: `to` = original `From` (or `Reply-To` if present).
   - reply-all: `to` = original `From`; `cc` = original `To` + `Cc`, with the
     account's own address removed (fetch it once via `getProfile` — see
     [006](006-mailbox-metadata.md), whose `account_info` this can reuse).
2. **Threading headers.** Extend the message builder to accept
   `in_reply_to`/`references` and set them on the `MIMEText`/`MIMEMultipart`
   object. Pull the original's `Message-ID` header for `In-Reply-To`, and append
   it to the original's existing `References` chain.

```python
def create_reply_draft(account_name, message_id, body, reply_all=False, quote_original=True):
    service = _service(account_name)
    orig = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in orig["payload"]["headers"]}
    # derive to/cc, Re: subject, threadId=orig["threadId"],
    # in_reply_to=headers["Message-ID"], references=... , optional quoted body
    # then build + drafts().create(...) exactly as create_draft does
```

This means factoring the MIME construction out of `create_draft` into a shared
`_build_message(to, subject, body, cc, thread_id, in_reply_to, references)`
helper, which [004](004-draft-lifecycle.md) also wants — do that extraction once
and have create/update/reply/forward all use it.

## `server.py`

```python
@server.tool(
    annotations=ToolAnnotations(
        title="Draft Gmail Reply", read_only_hint=False,
        destructive_hint=False, idempotent_hint=False, open_world_hint=True,
    )
)
def gmail_draft_reply(account, message_id, body, reply_all=False, quote_original=True) -> str:
    """Draft a reply to an existing message with recipients, Re: subject, and
    threading filled in correctly. NEVER sends — produces a draft that still
    requires an explicit, separate gmail_send_draft after the user approves it.

    Args:
        account: name of a configured account
        message_id: the message being replied to (from gmail_search/gmail_read)
        body: your reply text (the quoted original is added automatically if quote_original)
        reply_all: include the original's other recipients as cc (default False)
        quote_original: prepend the quoted original message (default True)
    """
```

Same annotation profile as `gmail_draft`: a non-read, non-destructive write that
only produces a draft. It sits *inside* the two-step send design, not around it
— the whole point is that a well-formed reply is still just a draft until
`gmail_send_draft` is explicitly called.

## Security note

This tool reads an email and uses its headers to pre-address a reply. It must
**not** treat anything in the *body* of that email as an instruction (e.g. a
"please forward this to …" line). Recipients come only from structured headers
(`From`/`Reply-To`/`To`/`Cc`) or from the user, never parsed out of body text —
consistent with the prompt-injection posture already documented for
`gmail_send_draft`.

## Testing / manual verification

- Reply to a known message; assert the draft's `threadId` matches the original,
  `Subject` is `Re: <orig>` (no double `Re: Re:`), and `In-Reply-To` equals the
  original `Message-ID`.
- Reply-all on a message with multiple recipients: assert the account's own
  address is absent from the derived `cc`.
