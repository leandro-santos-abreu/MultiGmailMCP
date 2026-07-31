# Spec 006 — Account identity and label listing

- **Status:** **implemented** — `gmail_account_info` + `gmail_list_labels` ship
  in `gmail_client.py` / `server.py`, tested in `tests/test_metadata.py`.
- **Tier:** 1 (fits existing `gmail.readonly` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** no

## Problem

Two small blind spots, both trivially fixable within `gmail.readonly`:

1. **A name is not an address.** `list_accounts` returns the *nicknames* the
   user chose (`work`, `personal`) — but nothing maps those to the real Gmail
   address behind each token. The user (and the model) can't confirm that `work`
   is actually `me@company.com` and not the wrong inbox, and features like
   reply-all ([005](005-reply-forward-helper.md)) need the account's own address
   to exclude it from recipients.
2. **Labels are opaque IDs.** `gmail_read` already returns `labelIds`
   ([gmail_client.py:86](../gmail_client.py)) like `Label_12` or `CATEGORY_PROMO`
   — but there's no way to list what those mean, and no way to discover a custom
   label's exact name so it can be used in a `label:` search query. Building a
   correct label-scoped search is currently guesswork.

## Proposed tools

```python
gmail_account_info(account: str) -> str
gmail_list_labels(account: str) -> str
```

- **`gmail_account_info`** — returns the account's real email address plus a
  couple of harmless profile counts, resolving nickname → identity.
- **`gmail_list_labels`** — returns the account's labels as `{id, name, type}`,
  so the model can translate `labelIds` from a read, or find the exact name to
  put in a `label:"..."` search.

## `gmail_client.py`

```python
def account_info(account_name: str) -> dict:
    service = _service(account_name)
    p = service.users().getProfile(userId="me").execute()
    return {
        "account": account_name,
        "email_address": p.get("emailAddress", ""),
        "messages_total": p.get("messagesTotal", 0),
        "threads_total": p.get("threadsTotal", 0),
    }


def list_labels(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.users().labels().list(userId="me").execute()
    return [
        {"id": l["id"], "name": l["name"], "type": l.get("type", "")}
        for l in resp.get("labels", [])
    ]
```

`getProfile` and `labels.list` are both covered by `gmail.readonly`. Neither
returns anything sensitive beyond the email address the user already owns —
**note explicitly**: the account's email address is fine to surface; the
token/refresh-token secrecy rule in CLAUDE.md is unaffected because none of that
material is read or returned here.

## `server.py`

Both are pure reads:

```python
@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
)
def gmail_account_info(account: str) -> str:
    """Show which real Gmail address a configured account name maps to, plus
    total message/thread counts. Use to confirm you're about to act on the
    right inbox.
    ...
    """

@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
)
def gmail_list_labels(account: str) -> str:
    """List an account's labels as {id, name, type}. Use to translate the
    labelIds returned by gmail_read, or to find a label's exact name for a
    `label:"Name"` search query.
    ...
    """
```

Both catch only `AccountError`.

## Why this pairs well with everything else

- **005 (reply-all)** needs the account's own address; `account_info` provides
  the canonical source instead of a second ad-hoc `getProfile` call.
- **003 (cross-account search)** becomes more legible when the model can show
  the user "found in `work` (me@company.com)" rather than just a nickname.
- Label listing turns Gmail's powerful `label:` search operator from guesswork
  into something the model can use reliably.

## Testing / manual verification

- Empty `tokens/` → clean `AccountError` for both.
- Real account: `gmail_account_info` returns the address you authorized;
  `gmail_list_labels` includes the system labels (`INBOX`, `SENT`, `STARRED`,
  `CATEGORY_*`) plus any custom ones, and a `labelIds` value from a `gmail_read`
  resolves against it.
