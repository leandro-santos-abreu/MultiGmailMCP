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
