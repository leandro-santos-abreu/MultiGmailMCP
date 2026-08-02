"""Tests for spec 015 — the update checker. All offline: the network fetch is
injected/monkeypatched, and the cache is redirected to a temp dir."""

import json
import time

import pytest

import accounts
import server
import updates


# --- version comparison ---------------------------------------------------

@pytest.mark.parametrize("latest, current, newer", [
    ("v1.1.0", "1.0.0", True),
    ("1.0.1", "1.0.0", True),
    ("v2.0.0", "v1.9.9", True),
    ("1.0.0", "1.0.0", False),
    ("v1.0.0", "1.1.0", False),
    ("1.0", "1.0.0", False),
    ("v1.1", "1.0.5", True),
    ("garbage", "1.0.0", False),
    ("", "1.0.0", False),
])
def test_is_newer(latest, current, newer):
    assert updates.is_newer(latest, current) is newer


# --- fetch (injected opener) ---------------------------------------------

class _Resp:
    def __init__(self, body):
        self._b = body.encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_latest_tag_ok():
    opener = lambda req, timeout: _Resp(json.dumps({"tag_name": "v1.2.0"}))
    assert updates.fetch_latest_tag(opener=opener) == "v1.2.0"


def test_fetch_latest_tag_failure_returns_none():
    def boom(req, timeout):
        raise OSError("no network")

    assert updates.fetch_latest_tag(opener=boom) is None


# --- cache + check --------------------------------------------------------

def _use_tmp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path)


def test_status_from_cache_no_cache(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    st = updates.status_from_cache("1.0.0")
    assert st["update_available"] is False and st["latest"] is None


def test_status_from_cache_with_update(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    updates.write_cache("v1.4.0")
    st = updates.status_from_cache("1.0.0")
    assert st == {"current": "1.0.0", "latest": "v1.4.0", "update_available": True, "url": updates.RELEASES_PAGE}


def test_check_disabled_never_fetches(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    calls = {"n": 0}
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: calls.__setitem__("n", calls["n"] + 1) or "v9")
    updates.check("1.0.0", env={"GMAIL_MCP_NO_UPDATE_CHECK": "1"})
    assert calls["n"] == 0


def test_check_fresh_cache_skips_fetch(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    updates.write_cache("v1.0.0")  # checked_at = now -> fresh
    calls = {"n": 0}
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: calls.__setitem__("n", calls["n"] + 1) or "v9")
    updates.check("1.0.0", ttl=3600, env={})
    assert calls["n"] == 0


def test_check_stale_cache_fetches_and_writes(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    # stale cache: checked_at far in the past
    (tmp_path / "update_check.json").write_text(json.dumps({"checked_at": 0, "latest": "v1.0.0"}))
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v1.5.0")
    st = updates.check("1.0.0", ttl=3600, env={})
    assert st["latest"] == "v1.5.0" and st["update_available"] is True
    assert json.loads((tmp_path / "update_check.json").read_text())["latest"] == "v1.5.0"


# --- surfacing helpers ----------------------------------------------------

def test_instructions_note_present_and_absent(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    assert updates.instructions_note("1.0.0") == ""       # no cache
    updates.write_cache("v2.0.0")
    note = updates.instructions_note("1.0.0")
    assert "newer version" in note and "v2.0.0" in note and "once" in note


def test_human_summary(monkeypatch, tmp_path):
    _use_tmp_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v1.3.0")
    assert "1.3.0 is available" in updates.human_summary("1.0.0")
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: "v1.0.0")
    assert "latest version" in updates.human_summary("1.0.0")
    monkeypatch.setattr(updates, "fetch_latest_tag", lambda: None)
    # cache still holds v1.0.0 from the previous call -> "latest"; force a clean dir
    monkeypatch.setattr(accounts, "DATA_DIR", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    assert "Couldn't check" in updates.human_summary("1.0.0")


# --- server tool ----------------------------------------------------------

def test_check_for_updates_tool(monkeypatch):
    monkeypatch.setattr(updates, "human_summary", lambda: "You're on the latest version (v1.0.0).")
    assert "latest version" in server.check_for_updates()
