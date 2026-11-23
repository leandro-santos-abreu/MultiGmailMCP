"""Tests for spec 012 — the setup UI's deterministic core: pure helpers, the
localhost/token access gate, config merge, and the account-add wiring (with a
fake OAuth flow). No sockets and no real browser/OAuth are involved."""

import json

import pytest

import accounts
import gmail_client
import setup_app
from setup_app import SetupApp


# --- pure helpers ---------------------------------------------------------

def test_validate_credentials_desktop_ok():
    text = json.dumps({"installed": {"client_id": "x", "client_secret": "y", "redirect_uris": ["http://localhost"]}})
    ok, msg = setup_app.validate_credentials_json(text)
    assert ok and "Desktop" in msg


def test_validate_credentials_rejects_web_client():
    ok, msg = setup_app.validate_credentials_json(json.dumps({"web": {"client_id": "x"}}))
    assert not ok and "Web" in msg


def test_validate_credentials_rejects_garbage():
    ok, _ = setup_app.validate_credentials_json("not json")
    assert not ok


def test_build_config_snippet_shape():
    entry = setup_app.build_config_snippet("py", "/path/server.py", phrase="SEND")
    assert entry == {"command": "py", "args": ["/path/server.py"], "env": {"GMAIL_MCP_SEND_PHRASE": "SEND"}}
    assert "env" not in setup_app.build_config_snippet("py", "/path/server.py")


def test_merge_config_preserves_other_servers():
    existing = json.dumps({"mcpServers": {"other": {"command": "x"}}})
    merged = json.loads(setup_app.merge_claude_config(existing, {"command": "py", "args": ["s.py"]}))
    assert merged["mcpServers"]["other"] == {"command": "x"}
    assert merged["mcpServers"]["gmail-multi-account"]["command"] == "py"


def test_merge_config_refuses_invalid_json():
    with pytest.raises(ValueError):
        setup_app.merge_claude_config("{not valid", {"command": "py", "args": []})


def test_merge_config_from_empty():
    merged = json.loads(setup_app.merge_claude_config("", {"command": "py", "args": ["s.py"]}))
    assert "gmail-multi-account" in merged["mcpServers"]


def test_merge_config_preserves_claude_own_settings():
    # Current Claude Desktop keeps its own preferences in the same file.
    existing = json.dumps({"preferences": {"theme": "dark"}, "coworkUserFilesPath": "x"})
    merged = json.loads(setup_app.merge_claude_config(existing, {"command": "py", "args": ["s.py"]}))
    assert merged["preferences"] == {"theme": "dark"} and merged["coworkUserFilesPath"] == "x"
    assert "gmail-multi-account" in merged["mcpServers"]


@pytest.mark.parametrize("platform, env, expected_tail", [
    ("darwin", {"HOME": "/Users/me"}, "Application Support/Claude/claude_desktop_config.json"),
    ("linux", {"HOME": "/home/me"}, ".config/Claude/claude_desktop_config.json"),
])
def test_default_config_path(platform, env, expected_tail):
    p = setup_app.default_claude_config_path(platform=platform, env=env).as_posix()
    assert p.endswith(expected_tail)


def _win_env(tmp_path):
    return {"APPDATA": str(tmp_path / "Roaming"), "LOCALAPPDATA": str(tmp_path / "Local")}


def test_default_config_path_windows_older_claude_uses_appdata(tmp_path):
    p = setup_app.default_claude_config_path(platform="win32", env=_win_env(tmp_path))
    assert p == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"


def test_default_config_path_windows_current_claude_uses_claude_data(tmp_path):
    # Current Claude Desktop reads %LOCALAPPDATA%\Claude-Data\<package>\ and
    # ignores %APPDATA%\Claude, even when an old config is still there.
    pkg = tmp_path / "Local" / "Claude-Data" / "claude_pzs8sxrjxfjjc"
    pkg.mkdir(parents=True)
    (pkg.parent / "README.txt").write_text("not a folder", encoding="utf-8")
    old = tmp_path / "Roaming" / "Claude"
    old.mkdir(parents=True)
    (old / "claude_desktop_config.json").write_text("{}", encoding="utf-8")

    p = setup_app.default_claude_config_path(platform="win32", env=_win_env(tmp_path))

    assert p == pkg / "claude_desktop_config.json"


def test_default_config_path_windows_prefers_folder_with_a_config(tmp_path):
    root = tmp_path / "Local" / "Claude-Data"
    used = root / "claude_used"
    used.mkdir(parents=True)
    (used / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    (root / "claude_empty").mkdir()

    p = setup_app.default_claude_config_path(platform="win32", env=_win_env(tmp_path))

    assert p == used / "claude_desktop_config.json"


@pytest.mark.parametrize("name, ok", [
    ("work", True), ("personal-2", True),
    ("", False), (".", False), ("..", False), ("a/b", False), ("a\\b", False),
])
def test_valid_account_name(name, ok):
    assert setup_app.valid_account_name(name) is ok


# --- access gate (host + one-time token) ---------------------------------

def test_index_requires_token():
    app = SetupApp(token="secret")
    status, ctype, _ = app.handle("GET", "/", {}, {"Host": "127.0.0.1:7864"}, b"")
    assert status == 403 and "html" in ctype

    status, _, body = app.handle("GET", "/", {"token": "secret"}, {"Host": "127.0.0.1"}, b"")
    assert status == 200 and b"Gmail MCP" in body


def test_foreign_host_forbidden():
    app = SetupApp(token="secret")
    status, _, _ = app.handle("GET", "/api/status", {"token": "secret"}, {"Host": "evil.example.com"}, b"")
    assert status == 403


def test_api_requires_token():
    app = SetupApp(token="secret")
    status, _, _ = app.handle("GET", "/api/status", {}, {"Host": "localhost"}, b"")
    assert status == 403


# --- status payload never leaks secrets ----------------------------------

def test_status_payload_shape(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: [])
    app = SetupApp(token="t")
    status, _, body = app.handle("GET", "/api/status", {"token": "t"}, {"Host": "127.0.0.1"}, b"")
    data = json.loads(body)
    assert status == 200
    assert "gmail-multi-account" in data["config"]["snippet"]["mcpServers"]
    assert "prereqs" in data and "accounts" in data


def test_account_statuses_marks_expired(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["good", "old"])

    def load(name):
        if name == "old":
            raise accounts.AccountError("'old's authorization has expired")

    monkeypatch.setattr(accounts, "load_credentials", load)
    monkeypatch.setattr(gmail_client, "account_info", lambda n: {"email_address": "me@x.com"})

    rows = {r["name"]: r for r in setup_app.account_statuses()}
    assert rows["good"]["status"] == "valid" and rows["good"]["email"] == "me@x.com"
    assert rows["old"]["status"] == "expired"


def test_account_statuses_flags_disabled_gmail_api(monkeypatch):
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["t"])
    monkeypatch.setattr(accounts, "load_credentials", lambda name: None)

    def boom(name):
        raise RuntimeError(
            'HttpError 403 ... "Gmail API has not been used in project 783278571351 '
            "before or it is disabled ... accessNotConfigured"
        )

    monkeypatch.setattr(gmail_client, "account_info", boom)

    row = setup_app.account_statuses()[0]
    assert row["status"] == "error"
    assert "Gmail API" in row["detail"]
    assert "project=783278571351" in row["help_url"]


# --- credentials upload writes only after validation ---------------------

def test_credentials_upload_validates_before_saving(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    app = SetupApp(token="t")

    bad = app.handle("POST", "/api/credentials", {"token": "t"}, {"Host": "127.0.0.1"}, b'{"web":{}}')
    assert bad[0] == 400
    assert not (tmp_path / "credentials.json").exists()

    good_body = json.dumps({"installed": {"client_id": "a", "client_secret": "b"}}).encode()
    ok = app.handle("POST", "/api/credentials", {"token": "t"}, {"Host": "127.0.0.1"}, good_body)
    assert ok[0] == 200
    assert (tmp_path / "credentials.json").exists()


# --- add account drives the (faked) OAuth flow ---------------------------

class _FakeFlow:
    def run_local_server(self, port=0):
        return object()  # stand-in credentials


def test_add_account_runs_flow_and_saves(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    (tmp_path / "credentials.json").write_text("{}")
    saved = {}
    monkeypatch.setattr(accounts, "save_credentials", lambda name, creds: saved.setdefault("name", name))

    app = SetupApp(token="t", flow_factory=lambda: _FakeFlow())
    status, _, body = app.handle("POST", "/api/accounts/add", {"token": "t"}, {"Host": "127.0.0.1"},
                                 json.dumps({"name": "work"}).encode())
    assert status == 200 and json.loads(body)["state"] == "pending"

    app._threads["work"].join(timeout=2)
    assert saved.get("name") == "work"
    assert app._auth["work"]["state"] == "done"


def test_add_account_rejects_bad_name(monkeypatch, tmp_path):
    monkeypatch.setattr(accounts, "CREDENTIALS_FILE", tmp_path / "credentials.json")
    app = SetupApp(token="t")
    status, _, _ = app.handle("POST", "/api/accounts/add", {"token": "t"}, {"Host": "127.0.0.1"},
                              json.dumps({"name": "../evil"}).encode())
    assert status == 400


# --- apply config writes a backup and merges -----------------------------

def test_apply_config_backs_up_and_merges(monkeypatch, tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    app = SetupApp(token="t")

    status, _, body = app.handle("POST", "/api/config/apply", {"token": "t"}, {"Host": "127.0.0.1"},
                                 json.dumps({"path": str(cfg)}).encode())
    data = json.loads(body)
    assert status == 200 and data["ok"]
    written = json.loads(cfg.read_text())
    assert "other" in written["mcpServers"] and "gmail-multi-account" in written["mcpServers"]
    assert data["backup"] and list(tmp_path.glob("*.bak-*"))
