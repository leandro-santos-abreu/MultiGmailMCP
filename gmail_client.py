"""
Thin wrapper around the Gmail API for one account at a time.
Every function here takes an account_name and looks up that account's
credentials via accounts.py — nothing is cached across accounts.
"""

import base64
from email.mime.text import MIMEText

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


def create_draft(
    account_name: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    thread_id: str | None = None,
) -> dict:
    service = _service(account_name)

    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


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
    """Replace an existing draft's contents in place (same draft_id). Builds the
    MIME message exactly as create_draft does — spec 005 will factor the shared
    construction into a _build_message helper once reply/forward needs it too."""
    service = _service(account_name)

    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    if cc:
        message["cc"] = cc

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

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
