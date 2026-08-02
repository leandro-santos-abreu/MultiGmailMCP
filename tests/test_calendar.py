"""Tests for spec 014 — Calendar client + tools, mocked at calendar_client._service."""

import json

import pytest

import accounts
import calendar_client
import server
from fakes import FakeCalendar

PHRASE_ENV = "GMAIL_MCP_SEND_PHRASE"


def _patch(monkeypatch, fake):
    monkeypatch.setattr(calendar_client, "_service", lambda account_name: fake)


def _event(eid="e1", timed=True, attendees=None):
    start = {"dateTime": "2026-08-05T13:00:00-03:00"} if timed else {"date": "2026-08-05"}
    end = {"dateTime": "2026-08-05T14:00:00-03:00"} if timed else {"date": "2026-08-06"}
    e = {"id": eid, "summary": "Lunch", "start": start, "end": end, "htmlLink": "http://x",
         "organizer": {"email": "me@x.com"}, "status": "confirmed"}
    if attendees:
        e["attendees"] = attendees
    return e


# --- reads ---------------------------------------------------------------

def test_list_events_shape_and_params(monkeypatch):
    fake = FakeCalendar(responses={"events.list": {"items": [_event(), _event("e2", timed=False)]}})
    _patch(monkeypatch, fake)

    out = calendar_client.list_events("work", time_min="A", time_max="B")

    assert out[0]["all_day"] is False and out[0]["start"].startswith("2026-08-05T13")
    assert out[1]["all_day"] is True and out[1]["start"] == "2026-08-05"
    call = next(kw for key, kw in fake.calls if key == "events.list")
    assert call["singleEvents"] is True and call["orderBy"] == "startTime"


def test_freebusy(monkeypatch):
    fake = FakeCalendar(responses={"freebusy.query": {"calendars": {"primary": {"busy": [{"start": "s", "end": "e"}]}}}})
    _patch(monkeypatch, fake)

    out = calendar_client.freebusy("work", "A", "B")
    assert out == {"primary": [{"start": "s", "end": "e"}]}
    body = next(kw for key, kw in fake.calls if key == "freebusy.query")["body"]
    assert body["items"] == [{"id": "primary"}]


def test_list_events_all_captures_account_error(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["good", "old"])

    def per(name, **kw):
        if name == "old":
            raise accounts.AccountError("'old' expired")
        return [{"id": "g1"}]

    monkeypatch.setattr(calendar_client, "list_events", per)
    out = calendar_client.list_events_all("A", "B")
    assert out["good"] == [{"id": "g1"}] and "error" in out["old"]


# --- writes (client) -----------------------------------------------------

def test_create_event_body_timed_vs_all_day(monkeypatch):
    fake = FakeCalendar(responses={"events.insert": _event()})
    _patch(monkeypatch, fake)

    calendar_client.create_event("work", "Lunch", "2026-08-05T13:00:00-03:00",
                                 "2026-08-05T14:00:00-03:00", attendees=["a@x.com"],
                                 time_zone="America/Sao_Paulo", send_updates="all")

    call = next(kw for key, kw in fake.calls if key == "events.insert")
    body = call["body"]
    assert body["start"] == {"dateTime": "2026-08-05T13:00:00-03:00", "timeZone": "America/Sao_Paulo"}
    assert body["attendees"] == [{"email": "a@x.com"}]
    assert call["sendUpdates"] == "all"


def test_create_event_all_day(monkeypatch):
    fake = FakeCalendar(responses={"events.insert": _event(timed=False)})
    _patch(monkeypatch, fake)
    calendar_client.create_event("work", "Holiday", "2026-08-05", "2026-08-06")
    body = next(kw for key, kw in fake.calls if key == "events.insert")["body"]
    assert body["start"] == {"date": "2026-08-05"}


def test_update_event_only_changed_fields(monkeypatch):
    fake = FakeCalendar(responses={"events.patch": _event()})
    _patch(monkeypatch, fake)
    calendar_client.update_event("work", "e1", summary="New title")
    body = next(kw for key, kw in fake.calls if key == "events.patch")["body"]
    assert body == {"summary": "New title"}


def test_delete_and_quick_add(monkeypatch):
    fake = FakeCalendar(responses={"events.delete": {}, "events.quickAdd": _event()})
    _patch(monkeypatch, fake)
    assert calendar_client.delete_event("work", "e1") == {"deleted": "e1"}
    calendar_client.quick_add("work", "Lunch tue 1pm")
    assert "events.quickAdd" in [k for k, _ in fake.calls]


def test_respond_patches_self_attendee(monkeypatch):
    ev = _event(attendees=[{"email": "me@x.com", "self": True, "responseStatus": "needsAction"},
                           {"email": "other@x.com"}])
    fake = FakeCalendar(responses={"events.get": ev, "events.patch": ev})
    _patch(monkeypatch, fake)

    calendar_client.respond_to_event("work", "e1", "accepted")
    body = next(kw for key, kw in fake.calls if key == "events.patch")["body"]
    selves = [a for a in body["attendees"] if a.get("self")]
    assert selves[0]["responseStatus"] == "accepted"


# --- server tools: the notify gate ---------------------------------------

def test_create_default_none_is_not_gated(monkeypatch):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")  # phrase set, but send_updates defaults to none
    fake = FakeCalendar(responses={"events.insert": _event()})
    _patch(monkeypatch, fake)

    out = server.calendar_create_event("work", "Lunch", "2026-08-05T13:00:00-03:00", "2026-08-05T14:00:00-03:00")
    assert json.loads(out)["id"] == "e1"  # proceeded, nobody notified


def test_notify_blocked_without_phrase(monkeypatch, tmp_path):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    monkeypatch.setattr(server, "SETTINGS_LOG", tmp_path / "settings.log")
    fake = FakeCalendar(responses={})  # any API call would raise
    _patch(monkeypatch, fake)

    out = server.calendar_create_event(
        "work", "Lunch", "2026-08-05T13:00:00-03:00", "2026-08-05T14:00:00-03:00",
        attendees="a@x.com", send_updates="all", confirm="wrong",
    )
    assert "Change blocked" in out
    assert fake.calls == []  # gate ran before any API call


def test_notify_allowed_with_phrase_audits(monkeypatch, tmp_path):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    log = tmp_path / "settings.log"
    monkeypatch.setattr(server, "SETTINGS_LOG", log)
    fake = FakeCalendar(responses={"events.insert": _event()})
    _patch(monkeypatch, fake)

    server.calendar_create_event(
        "work", "Lunch", "2026-08-05T13:00:00-03:00", "2026-08-05T14:00:00-03:00",
        attendees="a@x.com", send_updates="all", confirm="SEND-NOW",
    )
    assert "calendar_create_event(notify)" in log.read_text(encoding="utf-8")


def test_server_read_handles_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(calendar_client, "list_calendars", boom)
    assert server.calendar_list_calendars("ghost") == "no account named 'ghost'"
