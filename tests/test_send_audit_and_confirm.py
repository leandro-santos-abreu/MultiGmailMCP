"""Tests for spec 007 — the send confirmation-phrase gate and the audit log,
both living in server.gmail_send_draft."""

import pytest

import gmail_client
import server

PHRASE_ENV = "GMAIL_MCP_SEND_PHRASE"


class _SendSpy:
    """Stand-in for gmail_client.send_draft that records whether it ran."""

    def __init__(self, result=None):
        self.called = False
        self.result = result or {"message_id": "sent-1", "to": "boss@x.com", "subject": "Report"}

    def __call__(self, account, draft_id):
        self.called = True
        return self.result


def _use_audit_file(monkeypatch, tmp_path):
    log = tmp_path / "sent.log"
    monkeypatch.setattr(server, "AUDIT_LOG", log)
    return log


# --- confirmation-phrase gate -------------------------------------------

def test_send_without_phrase_configured_proceeds(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    _use_audit_file(monkeypatch, tmp_path)
    spy = _SendSpy()
    monkeypatch.setattr(gmail_client, "send_draft", spy)

    out = server.gmail_send_draft("work", "d1")

    assert spy.called
    assert "Sent from 'work'" in out


def test_send_blocked_when_phrase_set_and_confirm_wrong(monkeypatch, tmp_path):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    log = _use_audit_file(monkeypatch, tmp_path)
    spy = _SendSpy()
    monkeypatch.setattr(gmail_client, "send_draft", spy)

    out = server.gmail_send_draft("work", "d1", confirm="wrong")

    assert "Send blocked" in out
    assert spy.called is False          # no Gmail call was made
    assert not log.exists()             # and nothing was logged


def test_send_proceeds_when_phrase_matches(monkeypatch, tmp_path):
    monkeypatch.setenv(PHRASE_ENV, "SEND-NOW")
    _use_audit_file(monkeypatch, tmp_path)
    spy = _SendSpy()
    monkeypatch.setattr(gmail_client, "send_draft", spy)

    out = server.gmail_send_draft("work", "d1", confirm="SEND-NOW")

    assert spy.called
    assert "Sent from 'work'" in out


# --- audit log -----------------------------------------------------------

def test_successful_send_writes_one_audit_line(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    log = _use_audit_file(monkeypatch, tmp_path)
    monkeypatch.setattr(gmail_client, "send_draft", _SendSpy())

    server.gmail_send_draft("work", "d1")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = lines[0]
    assert "account=work" in entry
    assert "message_id=sent-1" in entry
    assert "to=boss@x.com" in entry
    assert 'subject="Report"' in entry
    # never leak credential material into the audit trail
    assert "refresh" not in entry.lower()
    assert "token" not in entry.lower()


def test_audit_logging_failure_does_not_break_send(monkeypatch, tmp_path):
    monkeypatch.delenv(PHRASE_ENV, raising=False)
    # A path whose parent dir does not exist -> open() raises -> must be swallowed.
    monkeypatch.setattr(server, "AUDIT_LOG", tmp_path / "missing_dir" / "sent.log")
    monkeypatch.setattr(gmail_client, "send_draft", _SendSpy())

    out = server.gmail_send_draft("work", "d1")

    assert "Sent from 'work'" in out  # send still reported as successful
