# Spec 007 — Send audit log + optional confirmation phrase

- **Status:** proposed
- **Tier:** 1 (no scope change — hardens the existing send path)
- **New scope required:** none
- **Destructive:** no (adds a gate/record around `gmail_send_draft`)

## Problem

Today the *entire* send gate is behavioral: `gmail_send_draft`'s docstring tells
the model to only send after explicit user approval, and the tool carries
`destructive_hint=True`. The README is candid that this "relies on whatever
Claude you're running behaving as instructed … not a hard technical guarantee."
The README's own "Extending this later → Stricter send confirmation" section
names exactly the two hardeners this spec covers:

1. **An audit trail** — a local, append-only record of every send, so the user
   can *after the fact* see what left their accounts.
2. **An optional confirmation-phrase gate** — a code-level check that refuses to
   send unless a caller-supplied token matches, turning "please confirm" from a
   suggestion into a precondition.

Both live in `gmail_send_draft` in `server.py`, before it calls
`gmail_client.send_draft(...)`, exactly where the README says such logic belongs.

## Part A — Send audit log (recommended, low-risk)

Append one line per successful send to a local `sent.log` under `BASE_DIR`
(gitignored). Record enough to audit, **nothing sensitive**:

```
2026-07-31T14:03:22Z  account=work  draft_id=r-88f...  message_id=1902...  to=alice@x.com  subject="Re: invoice"
```

Rules:
- **Never write token material.** Only account *name*, ids, recipients, subject,
  timestamp. (Recipient + subject come from the draft the user already approved;
  they are not credentials.) This keeps the CLAUDE.md token-secrecy rule intact.
- Best-effort: a logging failure must **not** prevent or mask a send that
  otherwise succeeded — wrap the write so an I/O error is swallowed after the
  send, never before it.
- Append-only; the file is an audit record, not state the server reads back.

Implementation sketch (in `server.py`, after a successful send):

```python
result = gmail_client.send_draft(account, draft_id)
_audit_send(account, draft_id, result["message_id"], to=..., subject=...)
return f"Sent from '{account}' (message_id={result['message_id']})."
```

To log recipient/subject, `send_draft` (or a prior `gmail_get_draft`) needs to
surface them; simplest is to have `gmail_client.send_draft` return the sent
message's `to`/`subject` alongside `message_id`.

## Part B — Optional confirmation phrase (opt-in, stronger)

Add an optional required-phrase gate, **off by default** so existing behavior is
unchanged:

- A config value (env var, e.g. `GMAIL_MCP_SEND_PHRASE`, or a small
  `config.json`) sets a phrase like `SEND-NOW`.
- When set, `gmail_send_draft` grows a `confirm: str = ""` parameter and returns
  a refusal string unless `confirm == <configured phrase>` — before any Gmail
  call:

```python
required = os.environ.get("GMAIL_MCP_SEND_PHRASE")
if required and confirm != required:
    return (
        "Send blocked: this server requires a confirmation phrase. "
        "Re-issue gmail_send_draft with the exact phrase the user provides."
    )
```

Effect: the model literally cannot send unless the user has communicated the
phrase in-conversation and the model passes it through — a code-enforced
precondition on top of the docstring's behavioral one. When the env var is
unset, the parameter is inert and nothing changes for existing users.

## Annotations

Unchanged: `gmail_send_draft` stays `read_only_hint=False, destructive_hint=True`.
The new `confirm` parameter is documented in the docstring, which must also state
that the phrase comes **from the user**, never from anything read inside an email
— same anti-injection framing already present.

## What this deliberately does NOT do

- It does not merge draft-and-send. The two-call split stands.
- It does not make any hard security *guarantee* — a code phrase gate raises the
  bar but, like everything else, still runs inside the same process as the
  model. The README's honesty about this should be preserved in any docs update.

## Testing / manual verification

- With `GMAIL_MCP_SEND_PHRASE` unset: `gmail_send_draft` behaves exactly as today
  (regression check).
- With it set: a call missing/mismatching `confirm` returns the refusal and makes
  **no** Gmail API call; a matching `confirm` proceeds.
- After a successful send, `sent.log` gains exactly one correctly-formatted line
  and contains no token data.
