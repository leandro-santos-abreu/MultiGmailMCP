"""Tests for gmail_client — pure helpers and the API-shaped wrappers, with
``_service`` mocked so nothing touches the network or real tokens."""

import base64

import pytest

import gmail_client
from fakes import FakeGmail, message_with_headers


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode()


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


# --- _extract_body -------------------------------------------------------

def test_extract_body_prefers_plain_text():
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain wins")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        ],
    }
    assert gmail_client._extract_body(payload) == "plain wins"


def test_extract_body_falls_back_to_html_when_no_plain_part():
    payload = {"mimeType": "text/html", "body": {"data": _b64("<b>only html</b>")}}
    assert gmail_client._extract_body(payload) == "<b>only html</b>"


def test_extract_body_walks_nested_parts():
    payload = {
        "mimeType": "multipart/mixed",
        "body": {},
        "parts": [
            {"mimeType": "multipart/alternative", "body": {}, "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("deep text")}},
            ]},
        ],
    }
    assert gmail_client._extract_body(payload) == "deep text"


def test_extract_body_returns_empty_when_no_text():
    payload = {"mimeType": "application/pdf", "body": {"attachmentId": "a1"}}
    assert gmail_client._extract_body(payload) == ""


# --- search_messages -----------------------------------------------------

def test_search_messages_shape(monkeypatch):
    fake = FakeGmail(responses={
        "messages.list": {"messages": [{"id": "m1"}, {"id": "m2"}]},
        "messages.get": lambda kw: message_with_headers(
            msg_id=kw["id"], thread_id="t-" + kw["id"],
            headers={"From": "a@x.com", "Subject": "Hi " + kw["id"], "Date": "today"},
            snippet="snip",
        ),
    })
    _patch_service(monkeypatch, fake)

    out = gmail_client.search_messages("work", "is:unread", 2)

    assert [m["id"] for m in out] == ["m1", "m2"]
    assert out[0]["thread_id"] == "t-m1"
    assert out[0]["subject"] == "Hi m1"
    assert out[0]["from"] == "a@x.com"
    # list was asked with the query + max_results
    list_call = next(kw for key, kw in fake.calls if key == "messages.list")
    assert list_call["q"] == "is:unread"
    assert list_call["maxResults"] == 2


# --- create_draft --------------------------------------------------------

def test_create_draft_builds_headers_and_omits_threadid_when_absent(monkeypatch):
    fake = FakeGmail(responses={"drafts.create": {"id": "d1", "message": {"id": "m1"}}})
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_draft(
        "work", to="to@x.com", subject="Subj", body="Hello", cc="cc@x.com",
    )

    assert result == {"draft_id": "d1", "message_id": "m1"}
    sent_body = next(kw for key, kw in fake.calls if key == "drafts.create")["body"]
    assert "threadId" not in sent_body["message"]
    # MIMEText preserves the exact header keys the client sets (lowercase).
    raw = base64.urlsafe_b64decode(sent_body["message"]["raw"]).decode()
    assert "to: to@x.com" in raw
    assert "subject: Subj" in raw
    assert "cc: cc@x.com" in raw
    assert "Hello" in raw


def test_create_draft_sets_threadid_when_provided(monkeypatch):
    fake = FakeGmail(responses={"drafts.create": {"id": "d1", "message": {"id": "m1"}}})
    _patch_service(monkeypatch, fake)

    gmail_client.create_draft("work", "to@x.com", "S", "B", thread_id="THREAD9")

    sent_body = next(kw for key, kw in fake.calls if key == "drafts.create")["body"]
    assert sent_body["message"]["threadId"] == "THREAD9"


# --- send_draft (feeds the 007 audit trail) ------------------------------

def test_send_draft_returns_metadata_for_audit(monkeypatch):
    fake = FakeGmail(responses={
        "drafts.get": lambda kw: {"message": {"payload": {"headers": [
            {"name": "To", "value": "boss@x.com"},
            {"name": "Subject", "value": "Report"},
        ]}}},
        "drafts.send": {"id": "sent-123"},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.send_draft("work", "d1")

    assert result == {"message_id": "sent-123", "to": "boss@x.com", "subject": "Report"}


def test_send_draft_metadata_is_best_effort(monkeypatch):
    """If the pre-send metadata read fails, the send still happens and To/Subject
    come back blank — an audit gap must never block a send."""
    def boom(_kw):
        raise RuntimeError("metadata fetch failed")

    fake = FakeGmail(responses={"drafts.get": boom, "drafts.send": {"id": "sent-9"}})
    _patch_service(monkeypatch, fake)

    result = gmail_client.send_draft("work", "d1")

    assert result == {"message_id": "sent-9", "to": "", "subject": ""}
