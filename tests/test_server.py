"""Tests for the server.py convention: tools catch ONLY AccountError and return
str(e); every other exception must propagate rather than be swallowed."""

import json

import pytest

import accounts
import gmail_client
import server


def test_gmail_search_returns_account_error_string(monkeypatch):
    def raise_account_error(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "search_messages", raise_account_error)

    assert server.gmail_search("ghost", "q") == "no account named 'ghost'"


def test_gmail_search_propagates_other_errors(monkeypatch):
    def raise_value_error(*a, **k):
        raise ValueError("bad message id")

    monkeypatch.setattr(gmail_client, "search_messages", raise_value_error)

    # NOT swallowed — a real error is more useful than a silently wrong result.
    with pytest.raises(ValueError):
        server.gmail_search("work", "q")


def test_list_accounts_empty(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    assert "No accounts configured" in server.list_accounts()


def _fake_account_info(emails):
    def account_info(name):
        if emails[name] is None:
            raise accounts.AccountError(f"'{name}'s authorization has expired")
        return {"account": name, "email_address": emails[name]}
    return account_info


def test_list_accounts_shows_email_behind_each_nickname(monkeypatch):
    emails = {"acme": "ops@acme.example", "globex": "me@globex.example"}
    monkeypatch.setattr(accounts, "list_account_names", lambda: sorted(emails))
    monkeypatch.setattr(gmail_client, "account_info", _fake_account_info(emails))

    out = json.loads(server.list_accounts())

    assert out == [
        {"account": "acme", "email_address": "ops@acme.example"},
        {"account": "globex", "email_address": "me@globex.example"},
    ]


def test_list_accounts_expired_account_does_not_hide_the_others(monkeypatch):
    emails = {"good": "me@good.example", "stale": None}
    monkeypatch.setattr(accounts, "list_account_names", lambda: sorted(emails))
    monkeypatch.setattr(gmail_client, "account_info", _fake_account_info(emails))

    out = {row["account"]: row for row in json.loads(server.list_accounts())}

    assert out["good"]["email_address"] == "me@good.example"
    assert "expired" in out["stale"]["error"]
    assert "email_address" not in out["stale"]


def test_list_accounts_propagates_non_account_errors(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["work"])

    def boom(name):
        raise ConnectionError("network down")

    monkeypatch.setattr(gmail_client, "account_info", boom)

    with pytest.raises(ConnectionError):
        server.list_accounts()


def test_instructions_always_explain_multi_account_routing():
    text = server.server.instructions
    assert text.startswith(server.ACCOUNT_ROUTING_INSTRUCTIONS)
    for must_mention in ("list_accounts", "gmail_search_all", "never ask the user to disconnect"):
        assert must_mention in text


def test_instructions_keep_update_note_after_routing_guidance(monkeypatch):
    monkeypatch.setattr(server.updates, "instructions_note", lambda: "UPDATE NOTE")
    text = server._build_instructions()
    assert text == server.ACCOUNT_ROUTING_INSTRUCTIONS + "\n\nUPDATE NOTE"

    monkeypatch.setattr(server.updates, "instructions_note", lambda: "")
    assert server._build_instructions() == server.ACCOUNT_ROUTING_INSTRUCTIONS
