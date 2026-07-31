"""Tests for spec 001 — reading a whole thread in chronological order."""

import base64
import json

import pytest

import gmail_client
import server
from fakes import FakeGmail, message_with_headers


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def _thread(n):
    msgs = [
        message_with_headers(
            msg_id=f"m{i}", thread_id="t1",
            headers={"From": f"p{i}@x.com", "Subject": "Re: hi", "Date": f"day {i}"},
            body_data=base64.urlsafe_b64encode(f"body {i}".encode()).decode(),
        )
        for i in range(1, n + 1)
    ]
    return {"id": "t1", "messages": msgs}


def test_read_thread_returns_all_messages_in_order(monkeypatch):
    fake = FakeGmail(responses={"threads.get": _thread(3)})
    _patch_service(monkeypatch, fake)

    out = gmail_client.read_thread("work", "t1")

    assert out["message_count"] == 3
    assert out["returned"] == 3
    assert [m["id"] for m in out["messages"]] == ["m1", "m2", "m3"]
    assert out["messages"][0]["body"] == "body 1"
    assert out["messages"][2]["from"] == "p3@x.com"


def test_read_thread_caps_at_max_messages(monkeypatch):
    fake = FakeGmail(responses={"threads.get": _thread(5)})
    _patch_service(monkeypatch, fake)

    out = gmail_client.read_thread("work", "t1", max_messages=2)

    assert out["message_count"] == 5      # true length reported
    assert out["returned"] == 2           # but only 2 expanded
    assert [m["id"] for m in out["messages"]] == ["m1", "m2"]


def test_server_tool_json_and_single_api_call(monkeypatch):
    fake = FakeGmail(responses={"threads.get": _thread(2)})
    _patch_service(monkeypatch, fake)

    parsed = json.loads(server.gmail_read_thread("work", "t1"))

    assert parsed["thread_id"] == "t1"
    assert len(parsed["messages"]) == 2
    # exactly one threads.get, regardless of thread length
    assert [key for key, _ in fake.calls] == ["threads.get"]
