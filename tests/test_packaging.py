"""Tests for spec 013 — the packaging-related code that CAN run offline: the
data-dir resolution (dev vs. frozen vs. override) and the serve/setup dispatcher.
The actual PyInstaller build is per-OS and stays a manual step."""

import pytest

import accounts
from main import main


# --- accounts.resolve_data_dir -------------------------------------------

def test_data_dir_dev_is_repo_dir():
    assert accounts.resolve_data_dir(env={}, frozen=False) == accounts.BASE_DIR


def test_data_dir_override_wins_even_when_frozen():
    got = accounts.resolve_data_dir(env={"GMAIL_MCP_DATA_DIR": "/custom/here"}, frozen=True, platform="win32")
    assert got.as_posix() == "/custom/here"


def test_data_dir_frozen_windows():
    got = accounts.resolve_data_dir(env={"APPDATA": "C:/Users/me/AppData/Roaming"}, frozen=True, platform="win32")
    assert got.as_posix().endswith("Roaming/GmailMCP")


def test_data_dir_frozen_macos():
    got = accounts.resolve_data_dir(env={"HOME": "/Users/me"}, frozen=True, platform="darwin")
    assert got.as_posix() == "/Users/me/Library/Application Support/GmailMCP"


def test_data_dir_frozen_linux_xdg():
    got = accounts.resolve_data_dir(
        env={"HOME": "/home/me", "XDG_CONFIG_HOME": "/home/me/.cfg"}, frozen=True, platform="linux"
    )
    assert got.as_posix() == "/home/me/.cfg/GmailMCP"


def test_data_dir_frozen_linux_default():
    got = accounts.resolve_data_dir(env={"HOME": "/home/me"}, frozen=True, platform="linux")
    assert got.as_posix() == "/home/me/.config/GmailMCP"


# --- main() dispatcher ----------------------------------------------------

def test_main_serve_runs_server(monkeypatch):
    import server

    called = {}
    monkeypatch.setattr(server.server, "run", lambda: called.setdefault("serve", True))
    assert main(["serve"]) == 0
    assert called.get("serve")


def test_main_defaults_to_serve(monkeypatch):
    import server

    called = {}
    monkeypatch.setattr(server.server, "run", lambda: called.setdefault("serve", True))
    assert main([]) == 0
    assert called.get("serve")


def test_main_setup_runs_setup_app(monkeypatch):
    import setup_app

    called = {}
    monkeypatch.setattr(setup_app, "run", lambda *a, **k: called.setdefault("setup", True))
    assert main(["setup"]) == 0
    assert called.get("setup")


def test_main_unknown_command():
    assert main(["bogus"]) == 2
