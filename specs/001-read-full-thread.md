# Spec 001 — Read an entire thread in order

- **Status:** proposed
- **Tier:** 1 (fits existing `gmail.readonly` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** no

## Problem

`gmail_read(account, message_id)` returns exactly one message. But email is
threaded, and the common ask — *"what's the back-and-forth on this?"* — forces
the model to call `gmail_read` once per message after fishing message ids out
of `gmail_search`. That's N round-trips, it's easy to miss messages, and the
model has to reconstruct chronological order itself. `gmail_search` already
returns `thread_id` for every hit (see [gmail_client.py:47](../gmail_client.py)),
so the identifier needed to fetch a whole thread is already in the model's
hands — there's just no tool that consumes it.

## Proposed tool

```python
gmail_read_thread(account: str, thread_id: str, max_messages: int = 25) -> str
```

Returns a JSON array of the messages in the thread, oldest first, each with the
same shape `gmail_read` already returns (`id`, `from`, `to`, `subject`, `date`,
`labels`, `body`), plus the thread's `id` at the top level.

`max_messages` caps how many messages are expanded, protecting against
pathologically long threads blowing up the context window; if the thread is
longer, the response notes how many were omitted.

## `gmail_client.py`

Add one function that mirrors the existing `read_message` shape so the return
contract is identical per message:

```python
def read_thread(account_name: str, thread_id: str, max_messages: int = 25) -> dict:
    service = _service(account_name)
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()

    msgs = thread.get("messages", [])
    total = len(msgs)
    out = []
    for msg in msgs[:max_messages]:
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        out.append({
            "id": msg["id"],
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", ""),
            "labels": msg.get("labelIds", []),
            "body": _extract_body(msg["payload"]),
        })
    return {"thread_id": thread_id, "message_count": total, "messages": out}
```

Notes:
- `threads().get(format="full")` returns every message in one API call, so this
  is *one* round-trip regardless of thread length — strictly better than N
  `gmail_read` calls.
- Reuses `_extract_body` unchanged, so plain-text/HTML fallback behaves
  identically to `gmail_read`.
- Gmail returns thread messages already in chronological order; no client-side
  sort needed. (Worth a test assertion so a future API change can't silently
  break ordering.)

## `server.py`

```python
@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
)
def gmail_read_thread(account: str, thread_id: str, max_messages: int = 25) -> str:
    """Read every message in one email thread, oldest first — the full
    back-and-forth of a conversation in a single call.

    Args:
        account: name of a configured account
        thread_id: a thread_id from gmail_search or gmail_read
        max_messages: cap on messages expanded (default 25)
    """
    try:
        thread = gmail_client.read_thread(account, thread_id, max_messages)
    except accounts.AccountError as e:
        return str(e)
    return json.dumps(thread, indent=2)
```

Same annotation set as `gmail_read`. Only `AccountError` is caught; a bad
`thread_id` yields a Gmail 404 that propagates, per convention.

## Testing / manual verification

- With an empty `tokens/`, the tool returns the clean "no account configured"
  `AccountError` string (the existing manual loop).
- With a real account: search for a threaded conversation, pass its `thread_id`,
  confirm messages come back oldest-first and count matches Gmail's UI.
- Assert chronological ordering and the `max_messages` truncation note in the
  automated suite (see [009](009-test-harness.md)).

## Out of scope

- Collapsing quoted-reply text ("On <date> X wrote:") out of each body — nice,
  but a separate parsing concern that would also affect `gmail_read`.
- Attachments within thread messages — covered by [002](002-attachments.md).
