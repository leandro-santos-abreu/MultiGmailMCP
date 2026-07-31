"""Tests for spec 003 — searching every configured account in one call."""

import json

import pytest

import accounts
import gmail_client
import server


def test_search_all_groups_results_by_account(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["personal", "work"])
    monkeypatch.setattr(
        gmail_client, "search_messages",
        lambda name, q, n: [{"id": f"{name}-1", "subject": q}],
    )

    out = gmail_client.search_all("invoice", 5)

    assert set(out) == {"personal", "work"}
    assert out["work"][0]["id"] == "work-1"


def test_search_all_records_expired_account_without_sinking_others(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["good", "expired"])

    def per_account(name, q, n):
        if name == "expired":
            raise accounts.AccountError("'expired's authorization has expired")
        return [{"id": "good-1"}]

    monkeypatch.setattr(gmail_client, "search_messages", per_account)

    out = gmail_client.search_all("q", 5)

    assert out["good"] == [{"id": "good-1"}]
    assert "error" in out["expired"]
    assert "expired" in out["expired"]["error"]


def test_server_tool_no_accounts(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    assert "No accounts configured" in server.gmail_search_all("q")


def test_server_tool_returns_json(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["work"])
    monkeypatch.setattr(gmail_client, "search_messages", lambda name, q, n: [{"id": "m1"}])

    parsed = json.loads(server.gmail_search_all("q"))
    assert parsed["work"][0]["id"] == "m1"
