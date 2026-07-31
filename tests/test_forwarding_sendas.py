"""Tests for spec 011 — forwarding, send-as, delegation: the endpoints, the
shared phrase gate, and the settings.log audit trail. All mocked at _service."""

import json

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail

PHRASE_ENV = "GMAIL_MCP_SEND_PHRASE"


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def _use_settings_log(monkeypatch, tmp_path):
    log = tmp_path / "settings.log"
    monkeypatch.setattr(server, "SETTINGS_LOG", log)
    return log


# --- endpoints & request bodies ------------------------------------------

def test_add_forwarding_address_reports_pending(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={"forwardingAddresses.create": {
        "forwardingEmail": "me@other.com", "verificationStatus": "pending",
    }})
    _patch_service(monkeypatch, fake)

    out = server.gmail_add_forwarding_address("work", "me@other.com")

    body = next(kw for key, kw in fake.calls if key == "forwardingAddresses.create")["body"]
    assert body == {"forwardingEmail": "me@other.com"}
    assert "pending" in out and "cannot" in out.lower()


def test_set_auto_forwarding_enable_requires_email(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={})
    _patch_service(monkeypatch, fake)

    out = server.gmail_set_auto_forwarding("work", enabled=True, email="")
    assert "needs `email`" in out
    assert fake.calls == []  # no API call made


def test_set_auto_forwarding_enable_sends_full_body(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={"settings.updateAutoForwarding": {"enabled": True}})
    _patch_service(monkeypatch, fake)

    server.gmail_set_auto_forwarding("work", enabled=True, email="me@other.com", disposition="archive")

    body = next(kw for key, kw in fake.calls if key == "settings.updateAutoForwarding")["body"]
    assert body == {"enabled": True, "emailAddress": "me@other.com", "disposition": "archive"}


def test_create_send_as_and_add_delegate_bodies(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={
        "sendAs.create": {"sendAsEmail": "alias@x.com", "verificationStatus": "pending"},
        "delegates.create": {"delegateEmail": "d@x.com", "verificationStatus": "accepted"},
    })
    _patch_service(monkeypatch, fake)

    server.gmail_create_send_as("work", "alias@x.com", display_name="Me")
    server.gmail_add_delegate("work", "d@x.com")

    sa_body = next(kw for key, kw in fake.calls if key == "sendAs.create")["body"]
    dg_body = next(kw for key, kw in fake.calls if key == "delegates.create")["body"]
    assert sa_body == {"sendAsEmail": "alias@x.com", "displayName": "Me"}
    assert dg_body == {"delegateEmail": "d@x.com"}


def test_delete_endpoints_use_path_params(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={
        "forwardingAddresses.delete": {}, "sendAs.delete": {}, "delegates.delete": {},
    })
    _patch_service(monkeypatch, fake)

    server.gmail_delete_forwarding_address("work", "me@other.com")
    server.gmail_delete_send_as("work", "alias@x.com")
    server.gmail_delete_delegate("work", "d@x.com")

    calls = {key: kw for key, kw in fake.calls}
    assert calls["forwardingAddresses.delete"]["forwardingEmail"] == "me@other.com"
    assert calls["sendAs.delete"]["sendAsEmail"] == "alias@x.com"
    assert calls["delegates.delete"]["delegateEmail"] == "d@x.com"


# --- the phrase gate blocks every mutating tool --------------------------

def test_all_mutating_tools_blocked_without_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    log = _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={})  # any API call would raise
    _patch_service(monkeypatch, fake)

    blocked = [
        server.gmail_add_forwarding_address("work", "me@other.com"),
        server.gmail_delete_forwarding_address("work", "me@other.com"),
        server.gmail_set_auto_forwarding("work", enabled=True, email="me@other.com"),
        server.gmail_create_send_as("work", "alias@x.com"),
        server.gmail_delete_send_as("work", "alias@x.com"),
        server.gmail_add_delegate("work", "d@x.com"),
        server.gmail_delete_delegate("work", "d@x.com"),
    ]
    assert all("Change blocked" in out for out in blocked)
    assert fake.calls == []        # nothing hit the API
    assert not log.exists()        # and nothing was audited


# --- audit trail ---------------------------------------------------------

def test_successful_change_writes_audit_line(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    log = _use_settings_log(monkeypatch, tmp_path)
    fake = FakeGmail(responses={"settings.updateAutoForwarding": {"enabled": True}})
    _patch_service(monkeypatch, fake)

    server.gmail_set_auto_forwarding("work", enabled=True, email="me@other.com", confirm="")

    line = log.read_text(encoding="utf-8").splitlines()[0]
    assert "account=work" in line
    assert "action=set_auto_forwarding" in line
    assert "me@other.com" in line
    assert "token" not in line.lower() and "refresh" not in line.lower()


# --- AccountError convention ---------------------------------------------

def test_list_send_as_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "list_send_as", boom)
    assert server.gmail_list_send_as("ghost") == "no account named 'ghost'"
