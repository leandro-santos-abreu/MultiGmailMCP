"""Tests for spec 010 — filters, vacation responder, IMAP/POP, and the
confirmation-phrase gate now shared with sending."""

import json

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail

PHRASE_ENV = "GMAIL_MCP_SEND_PHRASE"


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


# --- filters -------------------------------------------------------------

def test_create_filter_builds_criteria_and_action(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    fake = FakeGmail(responses={"filters.create": {"id": "f1", "criteria": {}, "action": {}}})
    _patch_service(monkeypatch, fake)

    server.gmail_create_filter(
        "work", from_addr="alice@x.com", subject="report",
        add_labels="Label_1", remove_labels="INBOX,UNREAD",
    )

    body = next(kw for key, kw in fake.calls if key == "filters.create")["body"]
    assert body["criteria"] == {"from": "alice@x.com", "subject": "report"}
    assert body["action"] == {"addLabelIds": ["Label_1"], "removeLabelIds": ["INBOX", "UNREAD"]}


def test_create_filter_with_forward(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    fake = FakeGmail(responses={"filters.create": {"id": "f1"}})
    _patch_service(monkeypatch, fake)

    server.gmail_create_filter("work", from_addr="a@x.com", forward_to="me@other.com")

    body = next(kw for key, kw in fake.calls if key == "filters.create")["body"]
    assert body["action"]["forward"] == "me@other.com"


def test_create_filter_requires_criteria_and_action(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    # No API call should happen when the filter is underspecified.
    fake = FakeGmail(responses={})
    _patch_service(monkeypatch, fake)

    assert "at least one criterion" in server.gmail_create_filter("work")
    assert "at least one action" in server.gmail_create_filter("work", from_addr="a@x.com")
    assert fake.calls == []


def test_delete_filter(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    fake = FakeGmail(responses={"filters.delete": {}})
    _patch_service(monkeypatch, fake)

    out = server.gmail_delete_filter("work", "f1")
    assert "f1" in out
    assert [key for key, _ in fake.calls] == ["filters.delete"]


# --- vacation ------------------------------------------------------------

def test_set_vacation_defaults_are_conservative(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    fake = FakeGmail(responses={"settings.updateVacation": {"enableAutoReply": True}})
    _patch_service(monkeypatch, fake)

    server.gmail_set_vacation("work", enabled=True, subject="Away", body="back soon")

    body = next(kw for key, kw in fake.calls if key == "settings.updateVacation")["body"]
    assert body["restrictToContacts"] is True
    assert body["restrictToDomain"] is True
    assert body["enableAutoReply"] is True


# --- imap/pop ------------------------------------------------------------

def test_set_imap_and_pop(monkeypatch):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    fake = FakeGmail(responses={
        "settings.updateImap": {"enabled": True},
        "settings.updatePop": {"accessWindow": "allMail"},
    })
    _patch_service(monkeypatch, fake)

    server.gmail_set_imap("work", enabled=True)
    server.gmail_set_pop("work", access_window="allMail")

    imap_body = next(kw for key, kw in fake.calls if key == "settings.updateImap")["body"]
    pop_body = next(kw for key, kw in fake.calls if key == "settings.updatePop")["body"]
    assert imap_body == {"enabled": True}
    assert pop_body == {"accessWindow": "allMail", "disposition": "leaveInInbox"}


# --- confirmation-phrase gate (shared with sending) ----------------------

def test_settings_change_blocked_without_phrase(monkeypatch):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    fake = FakeGmail(responses={})  # no response configured -> any API call would error
    _patch_service(monkeypatch, fake)

    out = server.gmail_set_vacation("work", enabled=True, confirm="wrong")
    assert "Change blocked" in out
    assert fake.calls == []  # gate ran before any API call


def test_settings_change_allowed_with_phrase(monkeypatch):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    fake = FakeGmail(responses={"settings.updateImap": {"enabled": False}})
    _patch_service(monkeypatch, fake)

    out = server.gmail_set_imap("work", enabled=False, confirm="SEND-NOW")
    assert json.loads(out) == {"enabled": False}


def test_create_filter_blocked_without_phrase(monkeypatch):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    fake = FakeGmail(responses={})
    _patch_service(monkeypatch, fake)

    out = server.gmail_create_filter("work", from_addr="a@x.com", forward_to="evil@x.com", confirm="")
    assert "Change blocked" in out
    assert fake.calls == []


# --- AccountError convention still holds ---------------------------------

def test_list_filters_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "list_filters", boom)
    assert server.gmail_list_filters("ghost") == "no account named 'ghost'"
