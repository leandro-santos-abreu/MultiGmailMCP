"""Tests for spec 002 — listing attachments and saving them to disk, with the
filename sanitization / path-confinement that keeps hostile filenames safe."""

import base64

import pytest

import accounts
import gmail_client
import server
from fakes import FakeGmail


def _patch_service(monkeypatch, fake):
    monkeypatch.setattr(gmail_client, "_service", lambda account_name: fake)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _msg_with_attachment(filename="report.pdf", att_id="att1", size=11):
    return {
        "id": "m1", "threadId": "t1",
        "payload": {
            "mimeType": "multipart/mixed", "headers": [], "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(b"hi")}},
                {"mimeType": "application/pdf", "filename": filename,
                 "body": {"attachmentId": att_id, "size": size}},
            ],
        },
    }


# --- list -----------------------------------------------------------------

def test_list_attachments_shape_and_ignores_non_attachments(monkeypatch):
    fake = FakeGmail(responses={"messages.get": _msg_with_attachment()})
    _patch_service(monkeypatch, fake)

    out = gmail_client.list_attachments("work", "m1")

    assert out == [{
        "filename": "report.pdf", "mime_type": "application/pdf",
        "size_bytes": 11, "attachment_id": "att1",
    }]


def test_list_attachments_none(monkeypatch):
    fake = FakeGmail(responses={"messages.get": {
        "id": "m1", "threadId": "t1",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": _b64(b"hi")}},
    }})
    _patch_service(monkeypatch, fake)
    assert "no attachments" in server.gmail_list_attachments("work", "m1").lower()


# --- filename sanitization (the security core) ---------------------------

@pytest.mark.parametrize("hostile, expected", [
    ("../../etc/passwd", "passwd"),
    ("..\\..\\Windows\\system32\\x", "x"),
    ("/abs/path/report.pdf", "report.pdf"),
    ("", "attachment"),
    ("..", "attachment"),
    (".", "attachment"),
    ("normal.pdf", "normal.pdf"),
])
def test_safe_filename(hostile, expected):
    assert gmail_client._safe_filename(hostile) == expected


# --- save -----------------------------------------------------------------

def test_save_attachment_writes_bytes_into_dest(monkeypatch, tmp_path):
    fake = FakeGmail(responses={
        "messages.get": _msg_with_attachment(),
        "attachments.get": {"data": _b64(b"hello world"), "size": 11},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.save_attachment("work", "m1", "att1", str(tmp_path))

    written = tmp_path / "report.pdf"
    assert written.read_bytes() == b"hello world"
    assert result["path"] == str(written)
    assert result["bytes"] == 11


def test_save_attachment_confines_hostile_filename_to_dest(monkeypatch, tmp_path):
    fake = FakeGmail(responses={
        "messages.get": _msg_with_attachment(filename="../../evil.sh"),
        "attachments.get": {"data": _b64(b"x"), "size": 1},
    })
    _patch_service(monkeypatch, fake)

    result = gmail_client.save_attachment("work", "m1", "att1", str(tmp_path))

    # Written inside tmp_path as a bare name, never one level up.
    assert (tmp_path / "evil.sh").exists()
    assert not (tmp_path.parent / "evil.sh").exists()
    assert result["filename"] == "evil.sh"


def test_save_attachment_does_not_overwrite(monkeypatch, tmp_path):
    fake = FakeGmail(responses={
        "messages.get": _msg_with_attachment(),
        "attachments.get": {"data": _b64(b"data"), "size": 4},
    })
    _patch_service(monkeypatch, fake)

    first = gmail_client.save_attachment("work", "m1", "att1", str(tmp_path))
    second = gmail_client.save_attachment("work", "m1", "att1", str(tmp_path))

    assert first["filename"] == "report.pdf"
    assert second["filename"] == "report (1).pdf"
    assert (tmp_path / "report.pdf").exists()
    assert (tmp_path / "report (1).pdf").exists()


# --- server tool ----------------------------------------------------------

def test_server_save_rejects_missing_dest_dir(monkeypatch, tmp_path):
    fake = FakeGmail(responses={})
    _patch_service(monkeypatch, fake)

    missing = tmp_path / "nope"
    out = server.gmail_save_attachment("work", "m1", "att1", dest_dir=str(missing))
    assert "not found" in out
    assert fake.calls == []  # nothing fetched or written


def test_server_list_handles_account_error(monkeypatch):
    def boom(*a, **k):
        raise accounts.AccountError("no account named 'ghost'")

    monkeypatch.setattr(gmail_client, "list_attachments", boom)
    assert server.gmail_list_attachments("ghost", "m1") == "no account named 'ghost'"
