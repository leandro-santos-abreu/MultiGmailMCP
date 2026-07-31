"""Tests for spec 006 — account identity (getProfile) and label listing."""

import json

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def test_account_info_maps_name_to_address(monkeypatch):
    fake = FakeGmail(responses={"users.getProfile": {
        "emailAddress": "me@company.com", "messagesTotal": 4210, "threadsTotal": 1200,
    }})
    _patch_service(monkeypatch, fake)

    out = gmail_client.account_info("work")

    assert out == {
        "account": "work",
        "email_address": "me@company.com",
        "messages_total": 4210,
        "threads_total": 1200,
    }


def test_list_labels_shape(monkeypatch):
    fake = FakeGmail(responses={"labels.list": {"labels": [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_9", "name": "Receipts", "type": "user"},
    ]}})
    _patch_service(monkeypatch, fake)

    out = gmail_client.list_labels("work")

    assert out == [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_9", "name": "Receipts", "type": "user"},
    ]


def test_server_tools_return_json_and_handle_account_error(monkeypatch):
    def raise_account_error(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "account_info", raise_account_error)
    assert server.gmail_account_info("ghost") == "no account named 'ghost'"

    monkeypatch.setattr(gmail_client, "list_labels", lambda a: [{"id": "INBOX", "name": "INBOX", "type": "system"}])
    assert json.loads(server.gmail_list_labels("work"))[0]["id"] == "INBOX"


def test_list_labels_empty(monkeypatch):
    monkeypatch.setattr(gmail_client, "list_labels", lambda a: [])
    assert "No labels found" in server.gmail_list_labels("work")
