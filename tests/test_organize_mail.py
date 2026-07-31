"""Tests for spec 008 — mailbox organisation (gmail.modify): label changes,
archive/read-state/star, trash/untrash, and label CRUD. Everything is mocked at
gmail_client._service, so no network and no real credentials."""

import json

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def _modify_ok():
    # messages.modify echoes back the message with its (post-change) labels
    return FakeGmail(responses={"messages.modify": {"id": "m1", "labelIds": ["INBOX"]}})


# --- gmail_client level: correct request bodies ---------------------------

def test_modify_labels_sends_add_and_remove(monkeypatch):
    fake = _modify_ok()
    _patch_service(monkeypatch, fake)

    out = gmail_client.modify_labels("work", "m1", add=["STARRED"], remove=["UNREAD"])

    body = next(kw for key, kw in fake.calls if key == "messages.modify")["body"]
    assert body == {"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]}
    assert out == {"id": "m1", "labels": ["INBOX"]}


def test_trash_and_untrash_hit_the_right_endpoints(monkeypatch):
    fake = FakeGmail(responses={
        "messages.trash": {"id": "m1", "labelIds": ["TRASH"]},
        "messages.untrash": {"id": "m1", "labelIds": ["INBOX"]},
    })
    _patch_service(monkeypatch, fake)

    gmail_client.trash_message("work", "m1")
    gmail_client.untrash_message("work", "m1")

    called = [key for key, _ in fake.calls]
    assert called == ["messages.trash", "messages.untrash"]


def test_label_crud(monkeypatch):
    fake = FakeGmail(responses={
        "labels.create": {"id": "Label_9", "name": "Receipts"},
        "labels.patch": {"id": "Label_9", "name": "Invoices"},
        "labels.delete": {},
    })
    _patch_service(monkeypatch, fake)

    assert gmail_client.create_label("work", "Receipts") == {"id": "Label_9", "name": "Receipts"}
    assert gmail_client.rename_label("work", "Label_9", "Invoices") == {"id": "Label_9", "name": "Invoices"}
    assert gmail_client.delete_label("work", "Label_9") == {"deleted": "Label_9"}

    create_body = next(kw for key, kw in fake.calls if key == "labels.create")["body"]
    assert create_body["name"] == "Receipts"
    patch_body = next(kw for key, kw in fake.calls if key == "labels.patch")["body"]
    assert patch_body == {"name": "Invoices"}


# --- server level: derived helpers map to the right label edits -----------

@pytest.mark.parametrize("tool, add, remove", [
    (server.gmail_mark_read, [], ["UNREAD"]),
    (server.gmail_mark_unread, ["UNREAD"], []),
    (server.gmail_archive, [], ["INBOX"]),
    (server.gmail_unarchive, ["INBOX"], []),
    (server.gmail_star, ["STARRED"], []),
    (server.gmail_unstar, [], ["STARRED"]),
])
def test_derived_helpers_send_expected_label_change(monkeypatch, tool, add, remove):
    fake = _modify_ok()
    _patch_service(monkeypatch, fake)

    tool("work", "m1")

    body = next(kw for key, kw in fake.calls if key == "messages.modify")["body"]
    assert body == {"addLabelIds": add, "removeLabelIds": remove}


def test_gmail_modify_labels_parses_comma_separated(monkeypatch):
    fake = _modify_ok()
    _patch_service(monkeypatch, fake)

    out = server.gmail_modify_labels("work", "m1", add="Label_1, Label_2", remove="UNREAD")

    body = next(kw for key, kw in fake.calls if key == "messages.modify")["body"]
    assert body == {"addLabelIds": ["Label_1", "Label_2"], "removeLabelIds": ["UNREAD"]}
    assert json.loads(out) == {"id": "m1", "labels": ["INBOX"]}


# --- convention: AccountError is returned as a string ---------------------

def test_mutating_tool_returns_account_error_string(monkeypatch):
    def raise_account_error(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "trash_message", raise_account_error)
    assert server.gmail_trash("ghost", "m1") == "no account named 'ghost'"


def test_mutating_tool_propagates_other_errors(monkeypatch):
    def raise_value_error(*a, **k):
        raise ValueError("bad id")

    monkeypatch.setattr(gmail_client, "delete_label", raise_value_error)
    with pytest.raises(ValueError):
        server.gmail_delete_label("work", "Label_x")
