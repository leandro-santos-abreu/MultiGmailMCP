# Spec 009 — Automated test suite

- **Status:** proposed
- **Tier:** infra (no user-facing scope impact)
- **New scope required:** none
- **Destructive:** no

## Problem

CLAUDE.md states plainly: *"No automated test suite yet."* Verification today is
a manual loop — `py_compile` for syntax, then calling tools by hand against the
empty-`tokens/` "no account configured" error path, and against a live Gmail
account for the happy path. That's fine for one careful maintainer, but every
feature in this directory adds surface area (filename sanitization in
[002](002-attachments.md), recipient derivation in [005](005-reply-forward-helper.md),
per-account error capture in [003](003-cross-account-search.md), the phrase gate
in [007](007-send-audit-and-confirmation.md)) that is easy to regress and
tedious to re-verify by hand. A lightweight suite lets new tools land *with*
coverage.

## Approach: mock at the Gmail service boundary

The clean seam is `gmail_client._service(account_name)`
([gmail_client.py:15](../gmail_client.py)) — the single place credentials turn
into a Google API client. Every other function calls it. Tests replace it with a
fake so **nothing hits the network, no real credentials are needed, and no token
files are touched.**

```python
# tests/fakes.py — a minimal stand-in for the Gmail service object
class FakeGmail:
    def __init__(self, messages=None, drafts=None, labels=None, profile=None): ...
    def users(self): return self          # chainable, mirrors googleapiclient shape
    def messages(self): return _Messages(self)
    def drafts(self): return _Drafts(self)
    # .get().execute() etc. return canned payloads in the real API's JSON shape
```

```python
# tests/test_gmail_client.py
def test_extract_body_prefers_plain_text():
    payload = {...}  # multipart with text/plain + text/html
    assert gmail_client._extract_body(payload) == "the plain text"

def test_search_returns_metadata_shape(monkeypatch):
    monkeypatch.setattr(gmail_client, "_service", lambda name: FakeGmail(messages=[...]))
    out = gmail_client.search_messages("work", "is:unread", 5)
    assert out[0]["thread_id"] and out[0]["subject"]
```

## What to cover first (highest regression risk, no live account needed)

| Target | Why it needs a test |
|--------|---------------------|
| `_extract_body` | recursive MIME walk with plain/HTML fallback — pure function, trivial to test, easy to break |
| `accounts.load_credentials` missing-account path | the documented "clean error path"; assert it raises `AccountError` with the reauth guidance, no file access |
| `accounts.list_account_names` | sorted, `.stem` only, creates `tokens/` if absent |
| `create_draft` / `_build_message` | correct `To`/`Subject`/`Cc` headers, base64url raw, `threadId` set only when provided |
| `server.py` `AccountError` handling | each tool returns `str(e)` on `AccountError` and does **not** swallow other exceptions (the core convention) |
| 002 filename sanitization | path-traversal inputs resolve to a safe basename |
| 003 `search_all` | one account raising `AccountError` yields a per-account `{"error": ...}` while others still return results |

## Tooling

- `pytest` as the runner (add to a `requirements-dev.txt`, kept out of the
  pinned runtime `requirements.txt` so the server's dependency set is unchanged).
- Update CLAUDE.md "Commands": add `venv/bin/pytest -q` next to the existing
  `py_compile` check as the new fast verification step. Keep the live-account
  manual check documented for the paths a fake can't meaningfully exercise
  (real OAuth, real refresh).
- Optionally a GitHub Actions workflow running `py_compile` + `pytest` on push —
  but **no real credentials in CI** (the whole point of mocking `_service`), so
  CI exercises logic, never a live inbox.

## Explicitly out of scope

- Testing real OAuth / real token refresh — that path genuinely needs Google and
  a browser (`add_account.py` "can't run headless" per CLAUDE.md). Leave it to
  the manual loop; don't fake `google.oauth2` internals just to claim coverage.
- Any test that reads or writes real `tokens/*.json` — fixtures use throwaway
  temp dirs and fake credential objects only.

## Payoff

Once `_service` is the mock seam, every Tier-1 spec here becomes testable
offline, and the manual loop shrinks to the two things that actually require a
human and a browser: initial authorization and confirming a real send.
