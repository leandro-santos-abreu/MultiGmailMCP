"""
Local setup & account-management UI for the Gmail multi-account MCP server.

Run it with:  python setup_app.py

It serves a small web page on 127.0.0.1 (only) that walks you through the
one-time Google Cloud step, dropping in credentials.json, adding / reauthorizing
/ removing accounts, and generating the Claude Desktop config entry. See
specs/012-setup-ui.md for the design and specs/013 for the packaging plan.

This module is standalone tooling — it is NOT imported by server.py, so the
running MCP server gains no web surface. It reuses accounts.py and
gmail_client.py rather than duplicating any of their logic, and it never renders,
logs, or returns the contents of tokens/*.json or credentials.json.
"""

import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import accounts
import gmail_client


# --------------------------------------------------------------------------
# Pure logic (unit-tested directly, no sockets / no real OAuth involved)
# --------------------------------------------------------------------------


def validate_credentials_json(text: str) -> tuple[bool, str]:
    """Check an uploaded OAuth client file is a Desktop-app client, without
    storing or echoing its contents. Returns (ok, human message)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False, "That file isn't valid JSON. Re-download the OAuth client JSON from Google Cloud Console."
    if not isinstance(data, dict):
        return False, "Unexpected JSON shape — download the OAuth client JSON for a Desktop app."
    if "installed" in data:
        info = data.get("installed") or {}
        if info.get("client_id") and info.get("client_secret"):
            return True, "Looks good — a Desktop OAuth client."
        return False, "This client JSON is missing client_id/client_secret. Re-download the Desktop OAuth client."
    if "web" in data:
        return False, "This is a *Web* OAuth client. Create a *Desktop app* client instead and download that JSON."
    return False, "Unrecognized file. In Google Cloud Console create an OAuth client of type 'Desktop app' and download its JSON."


def build_config_snippet(command: str, server_script: str, phrase: str = "") -> dict:
    """The mcpServers['gmail-multi-account'] entry for claude_desktop_config.json."""
    entry: dict = {"command": command, "args": [server_script]}
    if phrase:
        entry["env"] = {"GMAIL_MCP_SEND_PHRASE": phrase}
    return entry


def merge_claude_config(existing_text: str, entry: dict, server_name: str = "gmail-multi-account") -> str:
    """Merge our server entry into an existing claude_desktop_config.json's text,
    preserving any other mcpServers. Raises ValueError if the existing text is
    present but not valid JSON (so the caller can refuse rather than clobber)."""
    if existing_text and existing_text.strip():
        try:
            cfg = json.loads(existing_text)
        except json.JSONDecodeError as e:
            raise ValueError(f"the existing config is not valid JSON ({e}); fix or replace it by hand first")
        if not isinstance(cfg, dict):
            raise ValueError("the existing config is not a JSON object")
    else:
        cfg = {}
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[server_name] = entry
    cfg["mcpServers"] = servers
    return json.dumps(cfg, indent=2)


def default_claude_config_path(platform: str | None = None, env: dict | None = None) -> Path:
    """Best-guess path to claude_desktop_config.json for this OS."""
    platform = platform or sys.platform
    env = env if env is not None else os.environ
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    if platform == "win32":
        base = Path(env.get("APPDATA") or (home / "AppData/Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def valid_account_name(name: str) -> bool:
    """A token filename stem, not a path — reject anything that could escape."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in "/\\") and "\0" not in name


def account_statuses() -> list[dict]:
    """Per-account {name, status, email, detail}. Never returns token contents.
    Catches per-account errors so one bad account can't blank the whole table."""
    rows: list[dict] = []
    for name in accounts.list_account_names():
        try:
            accounts.load_credentials(name)  # refreshes; raises AccountError if expired/revoked
        except accounts.AccountError as e:
            rows.append({"name": name, "status": "expired", "email": "", "detail": str(e)})
            continue
        except Exception as e:  # noqa: BLE001 — status view stays useful even if one account errors
            rows.append({"name": name, "status": "error", "email": "", "detail": str(e)})
            continue
        try:
            info = gmail_client.account_info(name)
            rows.append({"name": name, "status": "valid", "email": info.get("email_address", ""), "detail": ""})
        except accounts.AccountError as e:
            rows.append({"name": name, "status": "expired", "email": "", "detail": str(e)})
        except Exception as e:  # noqa: BLE001
            rows.append({"name": name, "status": "error", "email": "", "detail": str(e)})
    return rows


def _default_flow_factory():
    """Build the InstalledAppFlow used by add_account.py — imported lazily so the
    module loads even where google_auth_oauthlib isn't needed."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    return InstalledAppFlow.from_client_secrets_file(str(accounts.CREDENTIALS_FILE), accounts.SCOPES)


# --------------------------------------------------------------------------
# The app: request handling decoupled from sockets for testability
# --------------------------------------------------------------------------


class SetupApp:
    """Holds the one-time token and in-flight auth state, and turns a parsed
    request into (status, content_type, body_bytes). The HTTP handler below is a
    thin adapter; tests call .handle(...) directly."""

    def __init__(self, token: str | None = None, flow_factory=None):
        self.token = token or secrets.token_urlsafe(16)
        self.flow_factory = flow_factory or _default_flow_factory
        self._auth: dict[str, dict] = {}
        self._threads: dict[str, threading.Thread] = {}

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _json(obj, status: int = 200):
        return status, "application/json; charset=utf-8", json.dumps(obj).encode("utf-8")

    @staticmethod
    def _body_json(body: bytes) -> dict:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _server_script(self) -> str:
        return str(Path(__file__).resolve().parent / "server.py")

    # -- OAuth (background thread; the browser step can't be synchronous) ---

    def _start_auth(self, name: str) -> None:
        self._auth[name] = {"state": "pending", "message": ""}

        def worker():
            try:
                flow = self.flow_factory()
                creds = flow.run_local_server(port=0)  # opens the browser, as add_account.py does
                accounts.save_credentials(name, creds)
                self._auth[name] = {"state": "done", "message": ""}
            except Exception as e:  # noqa: BLE001 — surface the flow's error to the UI
                self._auth[name] = {"state": "error", "message": str(e)}

        t = threading.Thread(target=worker, daemon=True)
        self._threads[name] = t
        t.start()

    # -- status payload ----------------------------------------------------

    def _status_payload(self) -> dict:
        entry = build_config_snippet(sys.executable, self._server_script())
        return {
            "prereqs": {
                "python_version": sys.version.split()[0],
                "python_ok": sys.version_info >= (3, 10),
                "credentials_present": accounts.CREDENTIALS_FILE.exists(),
            },
            "accounts": account_statuses(),
            "scopes": accounts.SCOPES,
            "config": {
                "path": str(default_claude_config_path()),
                "snippet": {"mcpServers": {"gmail-multi-account": entry}},
            },
        }

    def _apply_config(self, payload: dict):
        phrase = (payload.get("phrase") or "").strip()
        entry = build_config_snippet(sys.executable, self._server_script(), phrase)
        cfg_path = Path(payload["path"]) if payload.get("path") else default_claude_config_path()
        existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
        try:
            merged = merge_claude_config(existing, entry)
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        backup = None
        if cfg_path.exists():
            backup = cfg_path.with_name(f"{cfg_path.name}.bak-{int(time.time())}")
            backup.write_text(existing, encoding="utf-8")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(merged, encoding="utf-8")
        return self._json({"ok": True, "path": str(cfg_path), "backup": str(backup) if backup else None})

    # -- routing -----------------------------------------------------------

    def handle(self, method: str, path: str, query: dict, headers: dict, body: bytes):
        # DNS-rebinding guard: only answer to a localhost Host header.
        host = (headers.get("Host") or "").split(":")[0].lower()
        if host and host not in ("127.0.0.1", "localhost"):
            return 403, "text/plain; charset=utf-8", b"Forbidden: this tool only serves localhost."

        token_ok = (headers.get("X-Setup-Token") or query.get("token")) == self.token

        if path == "/" and method == "GET":
            if not token_ok:
                return 403, "text/html; charset=utf-8", ACCESS_DENIED_HTML.encode("utf-8")
            return 200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8")

        # every API route requires the one-time token (in a custom header for
        # POSTs, which forces a CORS preflight and blocks drive-by cross-site calls)
        if not token_ok:
            return self._json({"error": "invalid or missing setup token"}, 403)

        if path == "/api/status" and method == "GET":
            return self._json(self._status_payload())

        if path == "/api/credentials" and method == "POST":
            text = body.decode("utf-8", "replace")
            ok, msg = validate_credentials_json(text)
            if ok:
                accounts.CREDENTIALS_FILE.write_text(text, encoding="utf-8")
            return self._json({"ok": ok, "message": msg}, 200 if ok else 400)

        if path == "/api/accounts/add" and method == "POST":
            name = (self._body_json(body).get("name") or "").strip()
            if not valid_account_name(name):
                return self._json({"error": "invalid account name"}, 400)
            if not accounts.CREDENTIALS_FILE.exists():
                return self._json({"error": "credentials.json isn't set up yet"}, 400)
            self._start_auth(name)
            return self._json({"state": "pending"})

        if path == "/api/accounts/authstatus" and method == "GET":
            return self._json(self._auth.get(query.get("name", ""), {"state": "none"}))

        if path == "/api/accounts/test" and method == "GET":
            name = query.get("name", "")
            try:
                info = gmail_client.account_info(name)
                return self._json({"ok": True, "email": info.get("email_address", "")})
            except accounts.AccountError as e:
                return self._json({"ok": False, "error": str(e)})

        if path == "/api/accounts/remove" and method == "POST":
            name = (self._body_json(body).get("name") or "").strip()
            if not valid_account_name(name):
                return self._json({"error": "invalid account name"}, 400)
            token_path = accounts.TOKENS_DIR / f"{name}.json"
            if token_path.exists():
                token_path.unlink()
            return self._json({"ok": True})

        if path == "/api/config/apply" and method == "POST":
            return self._apply_config(self._body_json(body))

        return self._json({"error": "not found"}, 404)


# --------------------------------------------------------------------------
# HTTP glue
# --------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    app: SetupApp = None  # type: ignore[assignment]  (set per-server below)

    def _dispatch(self, method: str):
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k: v for k, v in self.headers.items()}
        status, ctype, out = self.app.handle(method, parsed.path, query, headers, body)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def log_message(self, *args):  # keep the console quiet
        pass


def run(open_browser: bool = True, port: int = 0) -> None:
    app = SetupApp()
    handler = type("_BoundHandler", (_Handler,), {"app": app})
    httpd = HTTPServer(("127.0.0.1", port), handler)
    actual_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/?token={app.token}"
    print(f"\nGmail MCP setup UI: {url}")
    print("Leave this window open while you use it; press Ctrl+C when you're done.\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — a headless box just uses the printed URL
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


# --------------------------------------------------------------------------
# Page (embedded so there are no external assets — matters for packaging, 013)
# --------------------------------------------------------------------------

ACCESS_DENIED_HTML = """<!doctype html><meta charset="utf-8">
<title>Gmail MCP setup</title>
<body style="font-family:system-ui;background:#f7f4ec;color:#17263a;padding:3rem;max-width:40rem;margin:auto">
<h1 style="color:#14293f">Open the link from your terminal</h1>
<p>For safety this page only opens from the one-time link printed in the window
where you ran <code>python setup_app.py</code> (it carries a token). Copy that
full URL into your browser.</p>
</body>"""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gmail MCP — Setup</title>
<style>
  :root{
    --navy:#14293f; --navy-2:#1f3d5c; --blue:#2f6fb0; --blue-2:#3f80c4;
    --blue-soft:#e9f1f9; --vanilla:#f7f4ec; --card:#ffffff; --ink:#17263a;
    --muted:#5f6f84; --line:#e6e2d6; --ok:#2f855a; --warn:#b7791f; --bad:#c0392b;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--vanilla);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.5}
  .wrap{max-width:860px;margin:0 auto;padding:2.5rem 1.25rem 4rem}
  header h1{font-size:1.6rem;margin:.2rem 0;color:var(--navy);letter-spacing:-.01em}
  header p{margin:.2rem 0 0;color:var(--muted)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
    padding:1.25rem 1.35rem;margin:1.1rem 0;box-shadow:0 1px 2px rgba(20,41,63,.04)}
  .eyebrow{font-size:.72rem;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
    color:var(--blue);margin:0 0 .3rem}
  h2{font-size:1.12rem;margin:.1rem 0 .6rem;color:var(--navy)}
  p.help{color:var(--muted);margin:.3rem 0 .8rem;font-size:.94rem}
  .row{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
  .grow{flex:1 1 auto;min-width:8rem}
  ul.checks{list-style:none;padding:0;margin:.2rem 0}
  ul.checks li{display:flex;align-items:center;gap:.55rem;padding:.28rem 0}
  .dot{width:.7rem;height:.7rem;border-radius:50%;flex:0 0 auto}
  .dot.ok{background:var(--ok)} .dot.bad{background:var(--bad)} .dot.warn{background:var(--warn)}
  input[type=text],input[type=password]{font:inherit;padding:.55rem .7rem;border:1px solid var(--line);
    border-radius:8px;background:#fff;color:var(--ink)}
  input:focus{outline:2px solid var(--blue-soft);border-color:var(--blue)}
  button{font:inherit;font-weight:600;padding:.55rem .95rem;border-radius:8px;border:1px solid transparent;
    background:var(--navy);color:#fff;cursor:pointer;transition:background .15s}
  button:hover{background:var(--navy-2)}
  button.ghost{background:#fff;color:var(--navy);border-color:var(--line)}
  button.ghost:hover{background:var(--blue-soft)}
  button.danger{background:#fff;color:var(--bad);border-color:#eccfcb}
  button.danger:hover{background:#fbeeec}
  table{width:100%;border-collapse:collapse;margin-top:.4rem}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--line);vertical-align:middle}
  th{font-size:.74rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
  .badge{font-size:.74rem;font-weight:700;padding:.16rem .5rem;border-radius:999px;white-space:nowrap}
  .badge.valid{background:#e6f4ec;color:var(--ok)}
  .badge.expired{background:#fdecea;color:var(--bad)}
  .badge.error{background:#fbf1e3;color:var(--warn)}
  code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  pre{background:#0f2540;color:#eaf1f8;padding:.9rem 1rem;border-radius:10px;overflow:auto;font-size:.82rem}
  a{color:var(--blue)} a:hover{color:var(--navy)}
  .muted{color:var(--muted)} .small{font-size:.86rem}
  .note{margin-top:.6rem;font-size:.9rem;padding:.5rem .7rem;border-radius:8px;background:var(--blue-soft);color:var(--navy-2)}
  .spacer{height:.4rem}
  ol.steps{margin:.2rem 0 .2rem 1.1rem;padding:0}
  ol.steps li{margin:.28rem 0}
  .flash{margin:.5rem 0;font-size:.9rem}
  .flash.ok{color:var(--ok)} .flash.bad{color:var(--bad)}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Gmail MCP — Setup</h1>
    <p>Connect your Gmail accounts and point Claude Desktop at this server.</p>
  </header>

  <section class="card" id="prereq-card">
    <p class="eyebrow">Status</p>
    <h2>Prerequisites</h2>
    <ul class="checks" id="prereqs"><li class="muted">Loading…</li></ul>
  </section>

  <section class="card">
    <p class="eyebrow">Step 1 · one time</p>
    <h2>Google Cloud credentials</h2>
    <p class="help">This is the fiddly part, done once. Create a free OAuth client, then drop its JSON here.
      The UI can't click through Google for you, but here's the exact path:</p>
    <ol class="steps small">
      <li><a href="https://console.cloud.google.com/" target="_blank" rel="noopener">console.cloud.google.com</a> → create a project.</li>
      <li>APIs &amp; Services → Library → enable <b>Gmail API</b>.</li>
      <li>Google Auth platform → Branding → set up the consent screen (Audience: <b>External</b>).</li>
      <li>Audience → Test users → add every Gmail address you'll connect.</li>
      <li>Clients → Create client → type <b>Desktop app</b> → download the JSON.</li>
    </ol>
    <div class="row">
      <input type="file" id="credfile" accept="application/json,.json">
      <span id="cred-flash" class="flash"></span>
    </div>
  </section>

  <section class="card">
    <p class="eyebrow">Accounts</p>
    <h2>Connected Gmail accounts</h2>
    <p class="help">Add an account (opens Google in your browser), or reauthorize one that expired —
      Google resets unverified-app access about every 7 days.</p>
    <table id="acct-table">
      <thead><tr><th>Name</th><th>Email</th><th>Status</th><th></th></tr></thead>
      <tbody id="acct-body"></tbody>
    </table>
    <div class="spacer"></div>
    <div class="row">
      <input type="text" id="newname" class="grow" placeholder="account nickname, e.g. work">
      <button id="addbtn">Add account</button>
    </div>
    <div id="add-flash" class="flash"></div>
  </section>

  <section class="card">
    <p class="eyebrow">Step 2</p>
    <h2>Point Claude Desktop at the server</h2>
    <p class="help">Add this to <code id="cfgpath" class="small"></code>. Use <b>Apply</b> to merge it in
      automatically (a timestamped backup is made first, and other servers are left untouched), or copy it in yourself.</p>
    <div class="row">
      <input type="password" id="phrase" class="grow" placeholder="optional: GMAIL_MCP_SEND_PHRASE (confirm-before-send/settings)">
    </div>
    <div class="spacer"></div>
    <pre id="snippet">…</pre>
    <div class="row">
      <button id="copybtn" class="ghost">Copy</button>
      <button id="applybtn">Apply to config</button>
      <span id="cfg-flash" class="flash"></span>
    </div>
    <p class="note small">After applying, fully restart Claude Desktop so it re-reads the config.</p>
  </section>

  <p class="muted small">Setup tool only — stop it (Ctrl+C) when you're done. Nothing here is hosted; it serves localhost only.</p>
</div>

<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
async function api(path, method="GET", body=null){
  const opts={method,headers:{"X-Setup-Token":TOKEN}};
  if(body){opts.headers["Content-Type"]="application/json";opts.body=JSON.stringify(body);}
  const r=await fetch(path,opts);
  let data=null; try{data=await r.json();}catch(e){}
  return {ok:r.ok,status:r.status,data};
}
function el(id){return document.getElementById(id);}
function flash(id,msg,good){const e=el(id);e.textContent=msg||"";e.className="flash "+(good?"ok":"bad");}

function renderPrereqs(p){
  const items=[
    [p.python_ok, `Python ${p.python_version}`, "Python 3.10+ required"],
    [p.credentials_present, "credentials.json in place", "Upload your Desktop OAuth client JSON above"],
  ];
  el("prereqs").innerHTML=items.map(([ok,good,bad])=>
    `<li><span class="dot ${ok?'ok':'bad'}"></span>${ok?good:bad}</li>`).join("");
}
function renderAccounts(rows){
  const body=el("acct-body");
  if(!rows.length){body.innerHTML=`<tr><td colspan="4" class="muted">No accounts yet — add one below.</td></tr>`;return;}
  body.innerHTML=rows.map(r=>{
    const badge=`<span class="badge ${r.status}">${r.status}</span>`;
    const reauth=r.status!=="valid"?`<button class="ghost" data-add="${r.name}">Reauthorize</button>`:
      `<button class="ghost" data-test="${r.name}">Test</button>`;
    return `<tr><td><b>${r.name}</b></td><td class="small muted">${r.email||"—"}</td>`+
      `<td>${badge}</td><td class="row" style="justify-content:flex-end">`+
      `${reauth} <button class="danger" data-remove="${r.name}">Remove</button></td></tr>`;
  }).join("");
}
function renderConfig(c){
  el("cfgpath").textContent=c.path;
  el("snippet").textContent=JSON.stringify(c.snippet,null,2);
}
async function refresh(){
  const {data}=await api("/api/status");
  if(!data)return;
  renderPrereqs(data.prereqs); renderAccounts(data.accounts); renderConfig(data.config);
}

el("credfile").addEventListener("change", async e=>{
  const f=e.target.files[0]; if(!f)return;
  const text=await f.text();
  const res=await fetch("/api/credentials",{method:"POST",headers:{"X-Setup-Token":TOKEN},body:text});
  const data=await res.json().catch(()=>({}));
  flash("cred-flash",data.message||"",res.ok);
  refresh();
});

el("addbtn").addEventListener("click", ()=>startAdd(el("newname").value.trim()));
async function startAdd(name){
  if(!name){flash("add-flash","Enter a nickname first.",false);return;}
  flash("add-flash","Opening Google in your browser… complete the consent, then this updates.",true);
  const {data,ok}=await api("/api/accounts/add","POST",{name});
  if(!ok){flash("add-flash",(data&&data.error)||"Could not start.",false);return;}
  poll(name);
}
async function poll(name){
  const {data}=await api("/api/accounts/authstatus?name="+encodeURIComponent(name));
  if(!data){return;}
  if(data.state==="pending"){setTimeout(()=>poll(name),1500);return;}
  if(data.state==="done"){flash("add-flash","Added "+name+".",true);el("newname").value="";refresh();return;}
  if(data.state==="error"){flash("add-flash","Failed: "+(data.message||"unknown"),false);refresh();return;}
}
document.addEventListener("click", async e=>{
  const add=e.target.getAttribute&&e.target.getAttribute("data-add");
  const rem=e.target.getAttribute&&e.target.getAttribute("data-remove");
  const tst=e.target.getAttribute&&e.target.getAttribute("data-test");
  if(add){startAdd(add);}
  if(tst){const {data}=await api("/api/accounts/test?name="+encodeURIComponent(tst));
    flash("add-flash",data&&data.ok?(tst+" → "+data.email):("Test failed: "+((data&&data.error)||"")),!!(data&&data.ok));}
  if(rem){
    if(!confirm("Remove '"+rem+"'? This deletes its local token (you'd reauthorize to use it again). "+
      "It does NOT revoke access on Google's side — do that at myaccount.google.com/permissions."))return;
    await api("/api/accounts/remove","POST",{name:rem});refresh();
  }
});

el("copybtn").addEventListener("click", ()=>{
  navigator.clipboard.writeText(el("snippet").textContent).then(()=>flash("cfg-flash","Copied.",true));
});
el("applybtn").addEventListener("click", async ()=>{
  const phrase=el("phrase").value.trim();
  const {data,ok}=await api("/api/config/apply","POST",{phrase});
  if(ok&&data.ok){flash("cfg-flash","Applied"+(data.backup?" (backup saved)":"")+". Restart Claude Desktop.",true);}
  else{flash("cfg-flash",(data&&data.error)||"Could not apply — copy it in manually.",false);}
});

refresh();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    run()
