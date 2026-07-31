# Spec 011 — Forwarding, send-as aliases, and delegation

- **Status:** **implemented** — forwarding addresses, auto-forwarding, send-as,
  and delegation ship in `gmail_client.py` / `server.py`. All mutating tools are
  gated by the shared `GMAIL_MCP_SEND_PHRASE` (`_phrase_blocks`), audited to
  `settings.log` (`_audit_settings`), and carry explicit-user-instruction
  docstrings; routing-establishing tools are `destructive_hint=True`. Tested in
  `tests/test_forwarding_sendas.py`.
- **Scope added:** `gmail.settings.basic` (forwarding addresses + auto-forwarding)
  and `gmail.settings.sharing` (send-as aliases, delegation).
- **Destructive:** no deletion, but this is the **highest-risk feature in the
  entire directory.** Every mutating tool here establishes persistent
  configuration that can route a user's mail — now and in the future — to
  another address or account.

> ⚠️⚠️ **This is the exfiltration surface. Read this whole spec before writing a
> line of it.** Auto-forwarding, a forwarding-action filter, a send-as alias, or
> a delegate are exactly what an attacker wants to plant: they silently and
> durably redirect or expose *all future mail*, and the user may never notice.
> The single most important property of this feature is that it is **impossible
> to trigger from anything except a direct, explicit user instruction.** These
> tools must never fire because of text read inside an email, a document, a
> filter, or any other tool output. That is not a nice-to-have; it is the reason
> this spec is sequenced last and gated hardest.

## What it enables

| Area | Tools | Scope | API |
|------|-------|-------|-----|
| Forwarding addresses | `gmail_list_forwarding_addresses`, `gmail_add_forwarding_address`, `gmail_delete_forwarding_address` | basic | `users.settings.forwardingAddresses.*` |
| Auto-forwarding | `gmail_get_auto_forwarding`, `gmail_set_auto_forwarding` | basic | `users.settings.getAutoForwarding` / `updateAutoForwarding` |
| Send-as aliases | `gmail_list_send_as`, `gmail_create_send_as`, `gmail_update_send_as`, `gmail_delete_send_as` | sharing | `users.settings.sendAs.*` |
| Delegation | `gmail_list_delegates`, `gmail_add_delegate`, `gmail_delete_delegate` | sharing | `users.settings.delegates.*` |

Note on delegation: `users.settings.delegates` is **Google Workspace-only** for
most configurations — on a consumer `@gmail.com` account these calls typically
fail. Implement the tools, but have them surface Google's own error rather than
pretending success, and say so in the docstring.

## The natural gates Google already enforces (lean on these)

Some blast-radius limiting is built into the API — the spec should rely on it,
not bypass it:

1. **A forwarding address must be verified before use.**
   `forwardingAddresses.create` only *registers* an address and triggers a
   confirmation email to that address; auto-forwarding can't point at it until
   the owner of the destination clicks verify. So `gmail_add_forwarding_address`
   cannot, by itself, start forwarding anywhere — it proposes a destination the
   destination must accept.
2. **Send-as with an external address is verified too.** `sendAs.create` for an
   address the user doesn't own sends a verification step before it can be used.
3. `gmail_set_auto_forwarding(enabled=True, email=...)` only accepts an address
   that is already a *verified* forwarding address — so the dangerous "enable"
   step is gated behind (1).

These gates limit damage but **do not replace** the runtime guards below — a
verified address can still be malicious, and delegation on Workspace has weaker
gating.

## Mandatory runtime guards (all of them, not a subset)

1. **Explicit-user-instruction only.** Every mutating tool's docstring states, in
   strong terms, that it may be used **only** when the user has, in this
   conversation, directly instructed this specific change and named the exact
   address/account. Never in response to email content, filter content, or any
   other observed data. (Mirrors and strengthens the `gmail_send_draft` framing.)
2. **Reuse the [007](007-send-audit-and-confirmation.md) confirmation phrase.**
   **Owner decision (recorded):** these tools reuse the same
   `GMAIL_MCP_SEND_PHRASE` gate — **not** a separate `GMAIL_MCP_SETTINGS_PHRASE`.
   When configured, refuse unless the caller passes the exact phrase the user
   supplied — a code-level precondition, not just docstring wording. This is why
   007 is sequenced before 011. (Factor 007's inline check into a shared helper
   so send and settings tools share one gate.)
3. **Audit every change.** Extend 007's `sent.log` (or a sibling
   `settings.log`) to append an entry for every forwarding/send-as/delegation
   change: timestamp, account name, tool, target address. **Never** log token
   material. This gives the user an after-the-fact trail for exactly the changes
   most likely to be planted silently.
4. **Echo the change back.** Each mutating tool returns a clear, human-readable
   summary of what was set (e.g. `Auto-forwarding ENABLED on 'work' → x@y.com
   (disposition: leaveInInbox)`), so the model surfaces it to the user rather
   than a bare id.

## `gmail_client.py` (representative)

```python
def add_forwarding_address(account_name, email) -> dict:
    service = _service(account_name)
    fa = service.users().settings().forwardingAddresses().create(
        userId="me", body={"forwardingEmail": email}
    ).execute()
    # verificationStatus is 'pending' until the destination confirms
    return {"forwarding_email": fa["forwardingEmail"],
            "verification_status": fa.get("verificationStatus", "")}

def set_auto_forwarding(account_name, enabled, email=None, disposition="leaveInInbox") -> dict:
    service = _service(account_name)
    body = {"enabled": enabled}
    if enabled:
        body["emailAddress"] = email          # must be an already-verified forwarding address
        body["disposition"] = disposition
    return service.users().settings().updateAutoForwarding(userId="me", body=body).execute()

def add_delegate(account_name, delegate_email) -> dict:
    service = _service(account_name)
    d = service.users().settings().delegates().create(
        userId="me", body={"delegateEmail": delegate_email}
    ).execute()   # Workspace-only; surfaces Google's error on consumer accounts
    return {"delegate_email": d["delegateEmail"], "verification_status": d.get("verificationStatus", "")}
```

## `server.py` — annotations

- List/get tools: `read_only_hint=True` (still need the settings scope to read).
- **All mutating tools** (`add_forwarding_address`, `set_auto_forwarding`,
  `create/update/delete_send_as`, `add/delete_delegate`, `delete_forwarding_address`):
  `read_only_hint=False`. Mark the ones that establish or remove a mail-routing
  path — `set_auto_forwarding(enabled=True)`, `add_delegate`, `create_send_as`,
  and the delete counterparts — with `destructive_hint=True`.

  Rationale for calling these "destructive" even though nothing is deleted: the
  CLAUDE.md rule is `destructive_hint=True` for **anything irreversible**, and
  the irreversible thing here is *exposure* — once mail has been forwarded to or
  read by another party, that can't be undone even after the rule is removed.
  Flagging them destructive gives well-behaved MCP clients maximum approval
  friction on exactly the actions that warrant it. This is a deliberate,
  documented reading of the annotation, noted so a future editor doesn't "correct"
  it to `False`.

All catch only `AccountError`; verification/permission errors from Google (e.g.
delegation on a consumer account, unverified forwarding target) propagate with
their real message so the user sees why it didn't take.

## Testing / manual verification

- Empty `tokens/` → clean `AccountError` on every tool.
- Mock `_service` ([009](009-test-harness.md)): assert `set_auto_forwarding`
  refuses (or omits `emailAddress`) unless enabled+address are coherent; assert
  the confirmation-phrase gate blocks a mutating call with a missing/wrong phrase
  and makes **no** API call; assert each mutating call writes exactly one audit
  line with no token data.
- Real account (careful, uses a real address you control): `add_forwarding_address`
  to an address you own → confirm it comes back `pending` and Google sent a
  verification mail → verify → `set_auto_forwarding(enabled=True)` → read it back
  → disable and remove. Do **not** test delegation against a consumer account;
  expect and assert the Workspace-only error there.

## Why this one is last

Everything else in this directory either only reads, only drafts, or changes
state that's recoverable and local to one message. 011 is the only feature that
can quietly and durably route a user's entire future mail stream elsewhere. It
ships only after 007's gate exists, only with the audit trail wired up, and only
with docstrings that make "never act on this except by direct user instruction"
impossible to miss.
