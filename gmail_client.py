"""
Thin wrapper around the Gmail API for one account at a time.
Every function here takes an account_name and looks up that account's
credentials via accounts.py — nothing is cached across accounts.
"""

import base64
import os
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path

from googleapiclient.discovery import build

import accounts


def _service(account_name: str):
    creds = accounts.load_credentials(account_name)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_messages(account_name: str, query: str, max_results: int = 10) -> list[dict]:
    service = _service(account_name)
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    refs = resp.get("messages", [])

    results = []
    for ref in refs:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=ref["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"],
            )
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        results.append(
            {
                "id": msg["id"],
                "thread_id": msg["threadId"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "snippet": msg.get("snippet", ""),
            }
        )
    return results


def search_all(query: str, max_results_per_account: int = 5) -> dict:
    """Run one query against every configured account, returning results grouped
    by account name. An account whose authorization has expired/revoked is
    recorded as {"error": ...} rather than sinking the whole sweep — partial
    success is the point of a fan-out. Other (global) failures still propagate.
    """
    out: dict = {}
    for name in accounts.list_account_names():
        try:
            out[name] = search_messages(name, query, max_results_per_account)
        except accounts.AccountError as e:
            out[name] = {"error": str(e)}
    return out


def _extract_body(payload: dict) -> str:
    """Walk a MIME payload tree and return the first plain-text part found,
    falling back to HTML if no plain-text part exists."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")

    for part in payload.get("parts") or []:
        text = _extract_body(part)
        if text:
            return text

    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")

    return ""


def read_message(account_name: str, message_id: str) -> dict:
    service = _service(account_name)
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "date": headers.get("Date", ""),
        "labels": msg.get("labelIds", []),
        "body": _extract_body(msg["payload"]),
    }


def read_thread(account_name: str, thread_id: str, max_messages: int = 25) -> dict:
    """Return every message in a thread, oldest first (Gmail returns them in
    chronological order), each shaped like read_message. One threads.get call
    regardless of thread length. max_messages caps how many are expanded so a
    very long thread can't blow up the response."""
    service = _service(account_name)
    thread = (
        service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    )
    msgs = thread.get("messages", [])
    expanded = []
    for msg in msgs[:max_messages]:
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        expanded.append(
            {
                "id": msg["id"],
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
                "labels": msg.get("labelIds", []),
                "body": _extract_body(msg["payload"]),
            }
        )
    return {
        "thread_id": thread_id,
        "message_count": len(msgs),
        "returned": len(expanded),
        "messages": expanded,
    }


def _build_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> dict:
    """Build the drafts.create/update request body — a {"message": {...}} dict
    with the base64url-encoded MIME message, and threadId when given. Shared by
    create_draft, update_draft, and the reply/forward builders so the message
    construction lives in exactly one place."""
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id
    return draft_body


def create_draft(
    account_name: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
) -> dict:
    service = _service(account_name)
    draft_body = _build_message(to, subject, body, cc=cc, thread_id=thread_id)
    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


# --- Attachments (gmail.readonly) ----------------------------------------
# Attachment filenames and bytes are attacker-controllable email content, so
# the save path sanitises the filename to a bare basename and refuses to write
# anywhere but inside the caller-approved destination directory.


def list_attachments(account_name: str, message_id: str) -> list[dict]:
    """List a message's attachments as {filename, mime_type, size_bytes,
    attachment_id}. Pure read; nothing hits disk."""
    service = _service(account_name)
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    found: list[dict] = []
    stack = [msg["payload"]]
    while stack:
        part = stack.pop()
        body = part.get("body", {})
        filename = part.get("filename") or ""
        if filename and body.get("attachmentId"):
            found.append(
                {
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "size_bytes": body.get("size", 0),
                    "attachment_id": body["attachmentId"],
                }
            )
        stack.extend(part.get("parts") or [])
    return found


def _safe_filename(name: str) -> str:
    """Reduce an email-supplied filename to a safe bare basename — no directory
    parts, no traversal, no control characters."""
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    name = "".join(c for c in name if c.isprintable())
    if name in ("", ".", ".."):
        name = "attachment"
    return name


def _attachment_filename(payload: dict, attachment_id: str) -> str:
    stack = [payload]
    while stack:
        part = stack.pop()
        if part.get("body", {}).get("attachmentId") == attachment_id:
            return part.get("filename") or "attachment"
        stack.extend(part.get("parts") or [])
    return "attachment"


def save_attachment(account_name: str, message_id: str, attachment_id: str, dest_dir: str) -> dict:
    """Download one attachment and write it into dest_dir (which must already
    exist). The filename comes from the message but is sanitised to a basename
    and confined to dest_dir; an existing file is never overwritten (a numeric
    suffix is added). Returns the absolute path written and byte count."""
    service = _service(account_name)

    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    raw_name = _attachment_filename(msg["payload"], attachment_id)

    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = base64.urlsafe_b64decode(att["data"])

    dest = Path(dest_dir).resolve()
    safe = _safe_filename(raw_name)
    target = dest / safe
    # defence in depth: a bare basename can't escape dest, but verify anyway.
    if target.parent != dest:
        raise ValueError("refused to write attachment outside the destination directory")

    stem, suffix = os.path.splitext(safe)
    counter = 1
    while target.exists():
        target = dest / f"{stem} ({counter}){suffix}"
        counter += 1

    target.write_bytes(data)
    return {"path": str(target), "filename": target.name, "bytes": len(data)}


def _original_headers(payload: dict) -> dict:
    """Case-insensitive header lookup for a message payload."""
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _prefix_subject(subject: str, prefix: str) -> str:
    """Add 'Re: ' / 'Fwd: ' unless the subject already carries that prefix."""
    if subject.lower().startswith(prefix.lower()):
        return subject
    return f"{prefix}{subject}"


def _quote_reply(headers: dict, body_text: str) -> str:
    attribution = f"On {headers.get('date', '')}, {headers.get('from', '')} wrote:"
    quoted = "\n".join("> " + line for line in body_text.splitlines())
    return f"{attribution}\n{quoted}"


def create_reply_draft(
    account_name: str,
    message_id: str,
    body: str,
    reply_all: bool = False,
    quote_original: bool = True,
) -> dict:
    """Draft a reply to an existing message. Recipients, Re: subject, threadId,
    and In-Reply-To/References come only from the original's structured headers
    — never from anything in its body. Never sends."""
    service = _service(account_name)
    orig = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = _original_headers(orig["payload"])

    sender = h.get("reply-to") or h.get("from", "")
    to = sender
    subject = _prefix_subject(h.get("subject", ""), "Re: ")

    cc = None
    if reply_all:
        own_email = (
            service.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
        )
        seen = {own_email, parseaddr(sender)[1].lower(), ""}
        cc_addrs = []
        for name, email in getaddresses([h.get("to", ""), h.get("cc", "")]):
            e = email.lower()
            if e and e not in seen:
                seen.add(e)
                cc_addrs.append(formataddr((name, email)))
        cc = ", ".join(cc_addrs) or None

    message_id_hdr = h.get("message-id")
    references = (f"{h.get('references', '')} {message_id_hdr or ''}").strip() or None

    reply_body = body
    if quote_original:
        reply_body = f"{body}\n\n{_quote_reply(h, _extract_body(orig['payload']))}"

    draft_body = _build_message(
        to, subject, reply_body, cc=cc,
        thread_id=orig.get("threadId"),
        in_reply_to=message_id_hdr, references=references,
    )
    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return {
        "draft_id": draft["id"],
        "message_id": draft["message"]["id"],
        "to": to,
        "cc": cc or "",
        "subject": subject,
    }


def create_forward_draft(
    account_name: str,
    message_id: str,
    to: str,
    body: str,
    quote_original: bool = True,
) -> dict:
    """Draft a forward of an existing message to new recipients. A forward
    starts a new thread, so no threadId or threading headers are set. Never
    sends."""
    service = _service(account_name)
    orig = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = _original_headers(orig["payload"])

    subject = _prefix_subject(h.get("subject", ""), "Fwd: ")

    fwd_body = body
    if quote_original:
        header_block = (
            "---------- Forwarded message ----------\n"
            f"From: {h.get('from', '')}\n"
            f"Date: {h.get('date', '')}\n"
            f"Subject: {h.get('subject', '')}\n"
            f"To: {h.get('to', '')}\n\n"
        )
        fwd_body = f"{body}\n\n{header_block}{_extract_body(orig['payload'])}"

    draft_body = _build_message(to, subject, fwd_body)
    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return {
        "draft_id": draft["id"],
        "message_id": draft["message"]["id"],
        "to": to,
        "subject": subject,
    }


def get_draft(account_name: str, draft_id: str) -> dict:
    service = _service(account_name)
    draft = service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    msg = draft["message"]
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return {
        "draft_id": draft["id"],
        "to": headers.get("To", ""),
        "cc": headers.get("Cc", ""),
        "subject": headers.get("Subject", "(no subject)"),
        "body": _extract_body(msg["payload"]),
    }


def update_draft(
    account_name: str,
    draft_id: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
) -> dict:
    """Replace an existing draft's contents in place (same draft_id), using the
    shared _build_message helper (also used by create_draft and reply/forward)."""
    service = _service(account_name)
    draft_body = _build_message(to, subject, body, cc=cc, thread_id=thread_id)
    draft = service.users().drafts().update(userId="me", id=draft_id, body=draft_body).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


def delete_draft(account_name: str, draft_id: str) -> dict:
    """Permanently delete an unsent draft. Only ever removes a draft — it cannot
    touch a sent or received message."""
    service = _service(account_name)
    service.users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": draft_id}


def send_draft(account_name: str, draft_id: str) -> dict:
    """Send an existing draft as-is. Irreversible once called.

    Returns the sent message id plus the draft's To/Subject, captured before
    sending so server.py can write an audit line. The metadata read is
    best-effort: if it fails we still send and just record blank To/Subject —
    an audit gap must never be a reason to block or fail a send.
    """
    service = _service(account_name)

    to = subject = ""
    try:
        meta = (
            service.users()
            .drafts()
            .get(userId="me", id=draft_id, format="metadata")
            .execute()
        )
        headers = {h["name"]: h["value"] for h in meta["message"]["payload"]["headers"]}
        to = headers.get("To", "")
        subject = headers.get("Subject", "")
    except Exception:  # noqa: BLE001 — audit metadata only; never block the send
        pass

    result = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return {"message_id": result["id"], "to": to, "subject": subject}


def list_drafts(account_name: str, max_results: int = 10) -> list[dict]:
    service = _service(account_name)
    resp = service.users().drafts().list(userId="me", maxResults=max_results).execute()

    drafts = []
    for d in resp.get("drafts", []):
        full = (
            service.users()
            .drafts()
            .get(userId="me", id=d["id"], format="metadata")
            .execute()
        )
        headers = {h["name"]: h["value"] for h in full["message"]["payload"]["headers"]}
        drafts.append(
            {
                "draft_id": d["id"],
                "subject": headers.get("Subject", "(no subject)"),
                "to": headers.get("To", ""),
            }
        )
    return drafts


# --- Account & mailbox metadata (gmail.readonly) -------------------------


def account_info(account_name: str) -> dict:
    """Resolve a configured account name to the real Gmail address behind it,
    plus a couple of harmless totals. No token/credential material is returned."""
    service = _service(account_name)
    profile = service.users().getProfile(userId="me").execute()
    return {
        "account": account_name,
        "email_address": profile.get("emailAddress", ""),
        "messages_total": profile.get("messagesTotal", 0),
        "threads_total": profile.get("threadsTotal", 0),
    }


def list_labels(account_name: str) -> list[dict]:
    """List the account's labels as {id, name, type}. Use to translate the
    labelIds from gmail_read, or to find the id a modify tool needs."""
    service = _service(account_name)
    resp = service.users().labels().list(userId="me").execute()
    return [
        {"id": lbl["id"], "name": lbl["name"], "type": lbl.get("type", "")}
        for lbl in resp.get("labels", [])
    ]


# --- Mailbox organisation (gmail.modify) ---------------------------------
# Message-state changes all reduce to one messages.modify (adding/removing
# system or user label ids); trash/untrash and label CRUD are their own calls.
# None of this can permanently delete anything — that needs the deliberately
# excluded https://mail.google.com/ scope. See specs/008.


def modify_labels(
    account_name: str,
    message_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict:
    """Add and/or remove label ids on one message. System labels (UNREAD,
    INBOX, STARRED, ...) and user label ids both work."""
    service = _service(account_name)
    body = {"addLabelIds": add or [], "removeLabelIds": remove or []}
    msg = service.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return {"id": msg["id"], "labels": msg.get("labelIds", [])}


def trash_message(account_name: str, message_id: str) -> dict:
    """Move a message to Trash. Recoverable (~30 days) via untrash_message or
    Gmail's UI — this is NOT a permanent delete."""
    service = _service(account_name)
    msg = service.users().messages().trash(userId="me", id=message_id).execute()
    return {"id": msg["id"], "labels": msg.get("labelIds", [])}


def untrash_message(account_name: str, message_id: str) -> dict:
    """Restore a message from Trash."""
    service = _service(account_name)
    msg = service.users().messages().untrash(userId="me", id=message_id).execute()
    return {"id": msg["id"], "labels": msg.get("labelIds", [])}


def create_label(account_name: str, name: str) -> dict:
    service = _service(account_name)
    label = (
        service.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return {"id": label["id"], "name": label["name"]}


def rename_label(account_name: str, label_id: str, name: str) -> dict:
    service = _service(account_name)
    label = (
        service.users()
        .labels()
        .patch(userId="me", id=label_id, body={"name": name})
        .execute()
    )
    return {"id": label["id"], "name": label["name"]}


def delete_label(account_name: str, label_id: str) -> dict:
    """Delete a user label. The label (and its membership) is gone for good;
    the messages that carried it are untouched."""
    service = _service(account_name)
    service.users().labels().delete(userId="me", id=label_id).execute()
    return {"deleted": label_id}


# --- Account settings: filters, vacation, IMAP/POP (gmail.settings.basic) ---
# These change *persistent* configuration that keeps acting on future mail.
# A filter's action can forward or trash — that's an exfiltration/data-loss
# vector, so the server layer gates these behind the confirmation phrase and
# the "only on explicit user instruction" docstrings.


def list_filters(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.users().settings().filters().list(userId="me").execute()
    return resp.get("filter", [])


def create_filter(account_name: str, criteria: dict, action: dict) -> dict:
    """Create a filter from a Gmail Filter resource's criteria + action dicts.
    Note actions can include `forward` (to a verified address) and label edits
    that skip the inbox or trash mail."""
    service = _service(account_name)
    created = (
        service.users()
        .settings()
        .filters()
        .create(userId="me", body={"criteria": criteria, "action": action})
        .execute()
    )
    return {
        "filter_id": created.get("id", ""),
        "criteria": created.get("criteria", {}),
        "action": created.get("action", {}),
    }


def delete_filter(account_name: str, filter_id: str) -> dict:
    service = _service(account_name)
    service.users().settings().filters().delete(userId="me", id=filter_id).execute()
    return {"deleted": filter_id}


def get_vacation(account_name: str) -> dict:
    service = _service(account_name)
    return service.users().settings().getVacation(userId="me").execute()


def set_vacation(
    account_name: str,
    enabled: bool,
    subject: str = "",
    body_text: str = "",
    restrict_to_contacts: bool = True,
    restrict_to_domain: bool = True,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict:
    """Update the vacation auto-responder. Defaults are conservative
    (contacts-only, domain-only) so an enabled responder can't auto-reply to
    the whole internet unless the caller deliberately widens it."""
    service = _service(account_name)
    body: dict = {
        "enableAutoReply": enabled,
        "responseSubject": subject,
        "responseBodyPlainText": body_text,
        "restrictToContacts": restrict_to_contacts,
        "restrictToDomain": restrict_to_domain,
    }
    if start_ms:
        body["startTime"] = start_ms
    if end_ms:
        body["endTime"] = end_ms
    return service.users().settings().updateVacation(userId="me", body=body).execute()


def get_imap(account_name: str) -> dict:
    service = _service(account_name)
    return service.users().settings().getImap(userId="me").execute()


def set_imap(account_name: str, enabled: bool) -> dict:
    service = _service(account_name)
    return service.users().settings().updateImap(userId="me", body={"enabled": enabled}).execute()


def get_pop(account_name: str) -> dict:
    service = _service(account_name)
    return service.users().settings().getPop(userId="me").execute()


def set_pop(account_name: str, access_window: str, disposition: str = "leaveInInbox") -> dict:
    """Update POP access. access_window is one of disabled / fromNowOn / allMail."""
    service = _service(account_name)
    return (
        service.users()
        .settings()
        .updatePop(userId="me", body={"accessWindow": access_window, "disposition": disposition})
        .execute()
    )


# --- Forwarding, send-as, delegation --------------------------------------
# The exfiltration surface. Each of these can durably route or expose a user's
# mail. Google enforces natural gates the server relies on: a forwarding address
# and an external send-as must be *verified* (Google emails the destination)
# before they can be used, so add_forwarding_address only proposes a target.
# Delegation is typically Workspace-only and errors on consumer @gmail.com.
# forwardingAddresses/autoForwarding are settings.basic; sendAs/delegates are
# settings.sharing.


def list_forwarding_addresses(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.users().settings().forwardingAddresses().list(userId="me").execute()
    return resp.get("forwardingAddresses", [])


def add_forwarding_address(account_name: str, email: str) -> dict:
    """Register a forwarding address. This only *proposes* it — Google emails the
    destination a verification link, and auto-forwarding can't point at it until
    that's confirmed. Returns the (usually 'pending') verification status."""
    service = _service(account_name)
    fa = (
        service.users()
        .settings()
        .forwardingAddresses()
        .create(userId="me", body={"forwardingEmail": email})
        .execute()
    )
    return {
        "forwarding_email": fa.get("forwardingEmail", email),
        "verification_status": fa.get("verificationStatus", ""),
    }


def delete_forwarding_address(account_name: str, email: str) -> dict:
    service = _service(account_name)
    service.users().settings().forwardingAddresses().delete(
        userId="me", forwardingEmail=email
    ).execute()
    return {"deleted": email}


def get_auto_forwarding(account_name: str) -> dict:
    service = _service(account_name)
    return service.users().settings().getAutoForwarding(userId="me").execute()


def set_auto_forwarding(
    account_name: str, enabled: bool, email: str | None = None, disposition: str = "leaveInInbox"
) -> dict:
    """Enable/disable auto-forwarding. When enabling, `email` must already be a
    verified forwarding address (see add_forwarding_address)."""
    service = _service(account_name)
    body: dict = {"enabled": enabled}
    if enabled:
        body["emailAddress"] = email
        body["disposition"] = disposition
    return service.users().settings().updateAutoForwarding(userId="me", body=body).execute()


def list_send_as(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.users().settings().sendAs().list(userId="me").execute()
    return resp.get("sendAs", [])


def create_send_as(account_name: str, email: str, display_name: str = "") -> dict:
    """Add a send-as alias. For an address the user doesn't already own, Google
    requires a verification step before it can be used."""
    service = _service(account_name)
    body: dict = {"sendAsEmail": email}
    if display_name:
        body["displayName"] = display_name
    sa = service.users().settings().sendAs().create(userId="me", body=body).execute()
    return {
        "send_as_email": sa.get("sendAsEmail", email),
        "verification_status": sa.get("verificationStatus", ""),
        "is_default": sa.get("isDefault", False),
    }


def delete_send_as(account_name: str, email: str) -> dict:
    service = _service(account_name)
    service.users().settings().sendAs().delete(userId="me", sendAsEmail=email).execute()
    return {"deleted": email}


def list_delegates(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.users().settings().delegates().list(userId="me").execute()
    return resp.get("delegates", [])


def add_delegate(account_name: str, delegate_email: str) -> dict:
    """Grant another account delegate access (read + send). Typically
    Workspace-only; on a consumer @gmail.com account Google returns an error,
    which propagates rather than being masked."""
    service = _service(account_name)
    d = (
        service.users()
        .settings()
        .delegates()
        .create(userId="me", body={"delegateEmail": delegate_email})
        .execute()
    )
    return {
        "delegate_email": d.get("delegateEmail", delegate_email),
        "verification_status": d.get("verificationStatus", ""),
    }


def delete_delegate(account_name: str, delegate_email: str) -> dict:
    service = _service(account_name)
    service.users().settings().delegates().delete(
        userId="me", delegateEmail=delegate_email
    ).execute()
    return {"deleted": delegate_email}
