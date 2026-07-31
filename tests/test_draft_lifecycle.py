"""Tests for spec 004 — updating and deleting existing drafts."""

import base64

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def test_update_draft_replaces_contents_same_id(monkeypatch):
    fake = FakeGmail(responses={"drafts.update": {"id": "d1", "message": {"id": "m2"}}})
    _patch_service(monkeypatch, fake)

    result = gmail_client.update_draft("work", "d1", "to@x.com", "New subj", "New body", cc="c@x.com")

    assert result == {"draft_id": "d1", "message_id": "m2"}
    call = next(kw for key, kw in fake.calls if key == "drafts.update")
    assert call["id"] == "d1"
    raw = base64.urlsafe_b64decode(call["body"]["message"]["raw"]).decode()
    assert "to: to@x.com" in raw
    assert "subject: New subj" in raw
    assert "cc: c@x.com" in raw
    assert "New body" in raw
    assert "threadId" not in call["body"]["message"]


def test_update_draft_sets_threadid(monkeypatch):
    fake = FakeGmail(responses={"drafts.update": {"id": "d1", "message": {"id": "m2"}}})
    _patch_service(monkeypatch, fake)

    gmail_client.update_draft("work", "d1", "t@x.com", "S", "B", thread_id="TH1")

    call = next(kw for key, kw in fake.calls if key == "drafts.update")
    assert call["body"]["message"]["threadId"] == "TH1"


def test_delete_draft(monkeypatch):
    fake = FakeGmail(responses={"drafts.delete": {}})
    _patch_service(monkeypatch, fake)

    assert gmail_client.delete_draft("work", "d1") == {"deleted": "d1"}
    assert [key for key, _ in fake.calls] == ["drafts.delete"]


def test_server_update_never_claims_sent(monkeypatch):
    monkeypatch.setattr(
        gmail_client, "update_draft",
        lambda *a, **k: {"draft_id": "d1", "message_id": "m2"},
    )
    out = server.gmail_update_draft("work", "d1", "to@x.com", "S", "B")
    assert "Not sent" in out
    assert "d1" in out


def test_server_tools_handle_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "delete_draft", boom)
    assert server.gmail_delete_draft("ghost", "d1") == "no account named 'ghost'"
