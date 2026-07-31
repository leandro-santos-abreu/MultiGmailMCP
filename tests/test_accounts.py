"""Tests for accounts.py — the documented 'clean error path' and the account
listing, exercised against a temp tokens dir so no real credentials are touched."""

import pytest

import accounts


def test_load_credentials_unknown_account_raises_account_error(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "TOKENS_DIR", tmp_path)

    with pytest.raises(accounts.AccountError) as excinfo:
        accounts.load_credentials("nope")

    msg = str(excinfo.value)
    assert "nope" in msg
    assert "add_account.py" in msg  # tells the user how to fix it


def test_list_account_names_sorted_stems(monkeypatch, tmp_path):
    (tmp_path / "work.json").write_text("{}")
    (tmp_path / "personal.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("ignored")  # non-json ignored
    monkeypatch.setattr(accounts, "TOKENS_DIR", tmp_path)

    assert accounts.list_account_names() == ["personal", "work"]


def test_list_account_names_creates_dir_when_absent(monkeypatch, tmp_path):
    target = tmp_path / "tokens"
    monkeypatch.setattr(accounts, "TOKENS_DIR", target)

    assert accounts.list_account_names() == []
    assert target.is_dir()
