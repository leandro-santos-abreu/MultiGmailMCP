# Spec 004 — Update and delete existing drafts

- **Status:** **implemented** — `gmail_update_draft` + `gmail_delete_draft` ship
  in `gmail_client.py` / `server.py`, tested in `tests/test_draft_lifecycle.py`.
  The shared `_build_message` extraction is deferred to 005 (noted in code).
- **Tier:** 1 (fits existing `gmail.compose` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** `gmail_delete_draft` yes, but **draft-scoped only** — it can
  never touch a sent or received message.

## Problem

The draft story today is create-only. `gmail_draft` makes one; `gmail_list_drafts`
/ `gmail_get_draft` read them; `gmail_send_draft` sends one. There is no way to
**revise** a draft ("change the subject line", "add a cc") without creating a
second draft and leaving the stale one behind, and no way to **discard** a draft
the user decided against. Both operations are already inside `gmail.compose` —
per the README, that scope covers "create/read/update/**delete** drafts" — so
this closes an obvious gap with no new authorization.

## Proposed tools

```python
gmail_update_draft(account, draft_id, to, subject, body, cc="", thread_id="") -> str
gmail_delete_draft(account, draft_id) -> str
```

- **`gmail_update_draft`** — replaces the draft's contents in place (Gmail's
  `drafts.update` swaps the whole message), keeping the same `draft_id`. Same
  argument shape as `gmail_draft` so the model builds the message identically.
- **`gmail_delete_draft`** — permanently removes a *draft*. Not the trash, not a
  message — a draft that has never been sent.

## `gmail_client.py`

```python
def update_draft(account_name, draft_id, to, subject, body, cc=None, thread_id=None) -> dict:
    service = _service(account_name)
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body_obj = {"message": {"raw": raw}}
    if thread_id:
        body_obj["message"]["threadId"] = thread_id
    draft = service.users().drafts().update(
        userId="me", id=draft_id, body=body_obj
    ).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


def delete_draft(account_name, draft_id) -> dict:
    service = _service(account_name)
    service.users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": draft_id}
```

`update_draft` deliberately reuses the exact MIME-building logic from
`create_draft` — if that logic changes (e.g. attachments per
[002](002-attachments.md)), both should change together. Consider extracting a
shared `_build_message(...)` helper to avoid drift.

## `server.py`

```python
@server.tool(
    annotations=ToolAnnotations(
        title="Update Gmail Draft", read_only_hint=False,
        destructive_hint=False, idempotent_hint=False, open_world_hint=True,
    )
)
def gmail_update_draft(account, draft_id, to, subject, body, cc="", thread_id="") -> str:
    """Replace the contents of an existing draft in place (same draft_id).
    Never sends — like gmail_draft, only gmail_send_draft can send.
    ...
    """

@server.tool(
    annotations=ToolAnnotations(
        title="Delete Gmail Draft", read_only_hint=False,
        destructive_hint=True, idempotent_hint=False, open_world_hint=True,
    )
)
def gmail_delete_draft(account, draft_id) -> str:
    """Permanently delete an UNSENT draft. This only removes a draft — it
    cannot touch a sent or received message. Irreversible for that draft.
    ...
    """
```

Annotation reasoning:
- `gmail_update_draft` → `destructive_hint=False`. It's a write, not a
  destroy: no mail leaves the account and the operation is on a draft. It stays
  firmly outside the two-step send design — updating a draft is not sending it.
- `gmail_delete_draft` → `destructive_hint=True`. Discarding a draft is
  irreversible, so it earns the destructive hint just like `gmail_send_draft` —
  even though its blast radius (one unsent draft) is far smaller than sending.
  The docstring must make that scope explicit so the model doesn't over-warn the
  user into thinking real mail is at risk.

Both catch only `AccountError`; a bad `draft_id` propagates as a Gmail 404.

## Relationship to the send-safety design

This does **not** weaken the two-call send split. `gmail_update_draft` cannot
send; it produces a draft that still requires the separate, explicit
`gmail_send_draft`. If anything it strengthens the review loop: the model can now
*correct* a draft after the user spots a problem in `gmail_get_draft` output,
instead of piling up near-duplicate drafts.

## Testing / manual verification

- Empty `tokens/` → clean `AccountError` for both tools.
- Real account: create → update (verify `gmail_get_draft` reflects the new
  subject, same `draft_id`) → delete (verify it's gone from `gmail_list_drafts`).
