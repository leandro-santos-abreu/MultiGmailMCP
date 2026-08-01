"""Tests for the gmail_add_account / gmail_add_account_status tools. The real
OAuth flow (_start_add_account) is monkeypatched, so no browser is opened."""

import pytest

import accounts
import server


def _creds_present(monkeypatch, tmp_path, exists=True):
    p = tmp_path / "credentials.json"
    if exists:
        p.write_text("{}")
    monkeypatch.setattr(accounts, "CREDENTIALS_FILE", p)


def test_add_account_rejects_bad_name(monkeypatch, tmp_path):
    _creds_present(monkeypatch, tmp_path)
    assert "Invalid account name" in server.gmail_add_account("../evil")


def test_add_account_requires_credentials(monkeypatch, tmp_path):
    _creds_present(monkeypatch, tmp_path, exists=False)
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    out = server.gmail_add_account("work")
    assert "credentials.json is missing" in out


def test_add_account_rejects_duplicate(monkeypatch, tmp_path):
    _creds_present(monkeypatch, tmp_path)
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["work"])
    assert "already configured" in server.gmail_add_account("work")


def test_add_account_starts_flow(monkeypatch, tmp_path):
    _creds_present(monkeypatch, tmp_path)
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    started = {}
    monkeypatch.setattr(server, "_start_add_account", lambda name: started.setdefault("name", name))

    out = server.gmail_add_account("work")

    assert started.get("name") == "work"
    assert "browser window is opening" in out.lower()


def test_add_account_status_reports_states(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    server._ADD_ACCOUNT_STATE.clear()

    assert "No add-account flow" in server.gmail_add_account_status("work")

    server._ADD_ACCOUNT_STATE["work"] = {"state": "pending", "message": ""}
    assert "Still waiting" in server.gmail_add_account_status("work")

    server._ADD_ACCOUNT_STATE["work"] = {"state": "error", "message": "boom"}
    assert "failed" in server.gmail_add_account_status("work") and "boom" in server.gmail_add_account_status("work")

    server._ADD_ACCOUNT_STATE["work"] = {"state": "done", "message": ""}
    assert "connected" in server.gmail_add_account_status("work")

    server._ADD_ACCOUNT_STATE.clear()
