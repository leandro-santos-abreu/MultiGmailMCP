"""Tests for the server.py convention: tools catch ONLY AccountError and return
str(e); every other exception must propagate rather than be swallowed."""

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


def test_list_accounts_names(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["personal", "work"])
    out = server.list_accounts()
    assert "personal" in out and "work" in out
