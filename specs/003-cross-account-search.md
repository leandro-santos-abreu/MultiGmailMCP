# Spec 003 — Search every account at once

- **Status:** **implemented** — `gmail_search_all` ships in `gmail_client.py` /
  `server.py`, tested in `tests/test_cross_account_search.py`. Per-account
  handling catches `AccountError` only (see note below), not all exceptions.
- **Tier:** 1 (fits existing `gmail.readonly` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** no

## Problem

The headline value of this server is *multi-account* Gmail, yet every read tool
is single-account: `gmail_search(account, query, ...)` searches exactly one
inbox. The natural multi-account question — *"did anyone from Acme email any of
my accounts this week?"* — forces the model to loop `gmail_search` over each
name from `list_accounts` and stitch results together itself. That's fine when
it remembers to; it silently misses accounts when it doesn't.

## Proposed tool

```python
gmail_search_all(query: str, max_results_per_account: int = 5) -> str
```

Runs the same Gmail query against **every** configured account and returns a
JSON object keyed by account name, each value being that account's result list
(same per-message shape `gmail_search` already produces), plus a per-account
error string where an account failed instead of aborting the whole call.

```json
{
  "personal": [ { "id": "...", "from": "...", "subject": "...", ... } ],
  "work":     [ ... ],
  "old":      { "error": "'old's authorization has expired ... rerun add_account.py old" }
}
```

## `gmail_client.py`

Reuse `search_messages` per account rather than reimplementing the list+get
walk:

```python
def search_all(query: str, max_results_per_account: int = 5) -> dict:
    out = {}
    for name in accounts.list_account_names():
        try:
            out[name] = search_messages(name, query, max_results_per_account)
        except accounts.AccountError as e:
            out[name] = {"error": str(e)}
    return out
```

Design decisions:
- **One expired/revoked account must not sink the whole search.** Each account
  is tried independently; an `AccountError` (the expected 7-day-expiry case) is
  captured per account, not raised. Non-`AccountError` Gmail failures also get
  captured here so a single flaky account can't abort a five-account sweep — a
  deliberate, *local* exception to the "let it propagate" convention, justified
  because the fan-out's whole point is partial success.
- **Default `max_results_per_account` is low (5).** `search_messages` already
  does a metadata `get` per hit (N+1), and this multiplies that by the account
  count. Keeping the per-account default small bounds total API calls; callers
  can raise it deliberately.

## `server.py`

```python
@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
)
def gmail_search_all(query: str, max_results_per_account: int = 5) -> str:
    """Search ALL configured Gmail accounts with one query and return matches
    grouped by account. Use for cross-account questions ("anything from Acme in
    any inbox?"); use gmail_search when the user names a specific account.

    Args:
        query: a Gmail search query (same syntax as gmail_search)
        max_results_per_account: cap on messages returned per account (default 5)
    """
    results = gmail_client.search_all(query, max_results_per_account)
    if not results:
        return "No accounts configured yet. Run `python add_account.py <name>` first."
    return json.dumps(results, indent=2)
```

Note there's no top-level `try/except AccountError` here: per-account errors are
already embedded in the result dict by `search_all`, and the empty-dict case
(no accounts at all) is handled explicitly.

## Testing / manual verification

- Empty `tokens/` → "No accounts configured yet." (the clean path).
- With two real accounts and one deliberately expired: the expired one shows an
  `{"error": ...}` entry while the healthy ones return results — the whole call
  still succeeds.

## Out of scope

- Merging/ranking results into a single chronological list across accounts.
  Grouping by account keeps provenance obvious (which inbox a message is in
  matters for every follow-up action) and avoids inventing a cross-account sort
  key. A unified timeline could be a later, separate tool.
