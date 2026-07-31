"""Tests for spec 005 — reply/forward draft scaffolding, plus a guard that the
shared _build_message refactor didn't change create_draft's output."""

import base64

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail, message_with_headers


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def _orig(headers, body="original body", thread_id="t1"):
    return message_with_headers(
        msg_id="m1", thread_id=thread_id, headers=headers,
        body_data=base64.urlsafe_b64encode(body.encode()).decode(),
    )


def _drafted_raw(fake):
    body = next(kw for key, kw in fake.calls if key == "drafts.create")["body"]
    return base64.urlsafe_b64decode(body["message"]["raw"]).decode(), body["message"]


# --- refactor guard -------------------------------------------------------

def test_build_message_keeps_create_draft_output(monkeypatch):
    fake = FakeGmail(responses={"drafts.create": {"id": "d1", "message": {"id": "m1"}}})
    _patch_service(monkeypatch, fake)

    gmail_client.create_draft("work", "to@x.com", "Subj", "Hi", cc="c@x.com", thread_id="T9")

    raw, message = _drafted_raw(fake)
    assert "to: to@x.com" in raw and "subject: Subj" in raw and "cc: c@x.com" in raw
    assert message["threadId"] == "T9"


# --- reply ----------------------------------------------------------------

def test_reply_sets_recipient_subject_and_threading(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({
            "From": "Alice <alice@x.com>", "To": "me@company.com",
            "Subject": "Project update", "Date": "Mon, 1 Jul 2026",
            "Message-ID": "<abc@mail>",
        }),
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_reply_draft("work", "m1", "Thanks!")

    raw, message = _drafted_raw(fake)
    assert result["to"] == "Alice <alice@x.com>"
    assert result["subject"] == "Re: Project update"
    assert message["threadId"] == "t1"
    assert "In-Reply-To: <abc@mail>" in raw
    assert "References: <abc@mail>" in raw
    assert "Thanks!" in raw
    assert "> original body" in raw  # quoted original


def test_reply_does_not_double_prefix_subject(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({"From": "a@x.com", "Subject": "Re: hi", "Message-ID": "<1@m>"}),
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_reply_draft("work", "m1", "ok", quote_original=False)
    assert result["subject"] == "Re: hi"


def test_reply_prefers_reply_to_header(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({
            "From": "bot@x.com", "Reply-To": "human@x.com", "Subject": "s", "Message-ID": "<1@m>",
        }),
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_reply_draft("work", "m1", "ok", quote_original=False)
    assert result["to"] == "human@x.com"


def test_reply_all_excludes_self_and_sender(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({
            "From": "Alice <alice@x.com>",
            "To": "me@company.com, Bob <bob@x.com>",
            "Cc": "carol@x.com, alice@x.com",
            "Subject": "s", "Message-ID": "<1@m>",
        }),
        "users.getProfile": {"emailAddress": "me@company.com"},
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_reply_draft("work", "m1", "ok", reply_all=True, quote_original=False)

    cc = result["cc"].lower()
    assert "bob@x.com" in cc and "carol@x.com" in cc
    assert "me@company.com" not in cc   # our own address dropped
    assert cc.count("alice@x.com") == 0  # sender is in To, not duplicated into Cc


def test_reply_quote_can_be_disabled(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({"From": "a@x.com", "Subject": "s", "Message-ID": "<1@m>"}),
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    gmail_client.create_reply_draft("work", "m1", "just this", quote_original=False)
    raw, _ = _drafted_raw(fake)
    assert "just this" in raw
    assert "> original body" not in raw


# --- forward --------------------------------------------------------------

def test_forward_new_thread_and_header_block(monkeypatch):
    fake = FakeGmail(responses={
        "messages.get": _orig({
            "From": "alice@x.com", "To": "me@company.com",
            "Subject": "Invoice", "Date": "Mon, 1 Jul 2026", "Message-ID": "<abc@mail>",
        }),
        "drafts.create": {"id": "d1", "message": {"id": "m2"}},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.create_forward_draft("work", "m1", "fwd@y.com", "FYI")

    raw, message = _drafted_raw(fake)
    assert result["subject"] == "Fwd: Invoice"
    assert result["to"] == "fwd@y.com"
    assert "threadId" not in message          # forward starts a new thread
    assert "In-Reply-To" not in raw
    assert "Forwarded message" in raw
    assert "From: alice@x.com" in raw
    assert "original body" in raw


# --- server tools ---------------------------------------------------------

def test_server_reply_reports_not_sent(monkeypatch):
    monkeypatch.setattr(
        gmail_client, "create_reply_draft",
        lambda *a, **k: {"draft_id": "d1", "message_id": "m2", "to": "a@x.com", "cc": ""},
    )
    out = server.gmail_draft_reply("work", "m1", "hi")
    assert "Not sent" in out and "to=a@x.com" in out


def test_server_forward_handles_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "create_forward_draft", boom)
    assert server.gmail_draft_forward("ghost", "m1", "x@y.com", "b") == "no account named 'ghost'"
