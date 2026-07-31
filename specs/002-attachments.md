# Spec 002 — List and save message attachments

- **Status:** proposed
- **Tier:** 1 (fits existing `gmail.readonly` scope — no reauthorization)
- **New scope required:** none
- **Destructive:** no, but `gmail_save_attachment` **writes a local file** —
  gated behind explicit user permission, see below.

## Problem

`_extract_body` ([gmail_client.py:58](../gmail_client.py)) walks the MIME tree
only for text parts; attachments are silently dropped. A user who asks *"grab
the invoice PDF from that email"* gets nothing — the server can't even tell them
an attachment exists. Attachment bytes live behind `gmail.readonly`, so this is
entirely in-scope; the only new surface is one read tool and one download tool.

## Proposed tools

```python
gmail_list_attachments(account: str, message_id: str) -> str
gmail_save_attachment(account: str, message_id: str, attachment_id: str, dest_dir: str = "") -> str
```

- **`gmail_list_attachments`** — returns a JSON array of `{filename, mime_type,
  size_bytes, attachment_id}` for each non-inline attachment in the message.
  Pure read. This is what the model calls first; nothing hits disk.
- **`gmail_save_attachment`** — downloads one attachment (by the `attachment_id`
  from the list call) and writes it to disk, returning the absolute path
  written. This is the only tool in the spec that touches the filesystem.

## `gmail_client.py`

```python
def list_attachments(account_name: str, message_id: str) -> list[dict]:
    service = _service(account_name)
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    found = []
    def walk(part):
        body = part.get("body", {})
        filename = part.get("filename") or ""
        if filename and body.get("attachmentId"):
            found.append({
                "filename": filename,
                "mime_type": part.get("mimeType", ""),
                "size_bytes": body.get("size", 0),
                "attachment_id": body["attachmentId"],
            })
        for sub in part.get("parts") or []:
            walk(sub)
    walk(msg["payload"])
    return found


def save_attachment(account_name, message_id, attachment_id, dest_dir, filename):
    service = _service(account_name)
    att = service.users().messages().attachments().get(
        userId="me", messageId=message_id, id=attachment_id
    ).execute()
    data = base64.urlsafe_b64decode(att["data"])
    # ... sanitize filename, resolve dest_dir, write bytes, return absolute path
```

## Security considerations (the important part)

This is the first feature that writes outside the repo, so it carries the most
risk and the tightest rules:

1. **`gmail_save_attachment` requires explicit user permission before writing.**
   The tool's docstring instructs the model to confirm filename + destination
   with the user first — mirroring the discipline already applied to sending.
   The attachment originates from *email content*, which the project already
   treats as untrusted (see the send-draft docstring's prompt-injection note);
   writing it to disk is an action the user should authorize.
2. **Filename sanitization is mandatory.** The `filename` comes from the email
   and is attacker-controllable. Strip any path separators and `..`, reduce to a
   basename, and reject/replace empties. Never let a crafted filename escape
   `dest_dir` (path traversal) or overwrite arbitrary files.
3. **Resolve and confine the destination.** Default `dest_dir` to a dedicated
   `attachments/` folder under `BASE_DIR` (add it to `.gitignore` alongside
   `tokens/`), never the repo root. If a caller passes `dest_dir`, resolve it and
   confirm it's a directory that exists; do not create arbitrary parent trees.
4. **Never overwrite silently.** If the sanitized target exists, suffix
   (` (1)`, ` (2)`) rather than clobber.
5. **Attachment bytes are not credentials, but treat the pipeline carefully** —
   the token-secrecy rule in CLAUDE.md is unaffected here (no token data flows
   through this path), but the "email content is untrusted" posture is exactly
   why the write step is gated.

## `server.py`

- `gmail_list_attachments`: `read_only_hint=True, idempotent_hint=True` — pure
  read.
- `gmail_save_attachment`: `read_only_hint=False`. It is **not** `destructive`
  in the email sense (nothing in Gmail changes), but its docstring must state it
  writes a file locally and to confirm the path with the user first. Reads from
  Gmail, writes to disk — annotate honestly as a non-read, non-destructive
  write.

Both catch only `AccountError`. A bad `message_id`/`attachment_id` propagates as
a Gmail error.

## Optional extension — attachments on *outgoing* drafts

Out of scope for the first cut, but worth recording: `create_draft` currently
builds the message with `MIMEText(body)`
([gmail_client.py:101](../gmail_client.py)), which cannot carry attachments.
Supporting *sending* files would require switching to `MIMEMultipart` and adding
file parts — and, more importantly, it means the server starts reading arbitrary
local files chosen by the model into outgoing mail, a meaningfully larger risk
surface than reading Gmail-hosted attachments. Spec that separately if wanted;
do not fold it in here.

## Testing / manual verification

- `list_attachments` on a message with no attachments returns `[]` → tool prints
  a clean "no attachments" message.
- Filename sanitization unit tests: `../../etc/passwd`, `C:\Windows\x`, empty,
  and unicode names all resolve to a safe basename inside `dest_dir`.
- Round-trip a small known attachment and byte-compare against the original.
