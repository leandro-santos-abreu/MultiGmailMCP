# Spec 013 — Packaging as an executable + distribution model

- **Status:** **partially implemented** — the code parts ship: `accounts.DATA_DIR`
  (dev/frozen/`GMAIL_MCP_DATA_DIR`, via `resolve_data_dir`) with `server.py` logs
  and the attachments default moved onto it; `main.py` `serve`/`setup` dispatcher;
  `setup_app.config_entry` emits a `["serve"]` config when frozen; `build.py` +
  `requirements-build.txt` (PyInstaller onefile, hidden-imports, refuses to bundle
  secrets). Tested in `tests/test_packaging.py`. **A Windows build has been run
  and verified:** `python build.py` → a single `dist/gmail-mcp.exe`, **~44 MB**. A
  committed `gmail-mcp.spec` drops `googleapiclient`'s bundled discovery docs for
  ~585 unused Google APIs (~99 MB), keeping only `gmail.v1.json` — which still
  resolves `build("gmail","v1", cache_discovery=False)` offline (verified), so no
  behaviour change. `gmail-mcp.exe serve` starts clean with **0 bytes on stdout**
  (MCP-safe) and no import errors; a no-argument launch (double-click) opens the
  setup wizard; the frozen `resolve_data_dir` created `%APPDATA%\GmailMCP` as
  intended. **Still manual / per-OS:** macOS/Linux builds, code signing /
  notarization, and the clean-machine checklist below.
- **Depends on:** [012](012-setup-ui.md) — the guided per-user onboarding this
  distribution model relies on.
- **New OAuth scope required:** none.
- **Touches real code:** `accounts.py` path handling (mandatory, see below), plus
  new build tooling. `server.py` behaviour over stdio must stay byte-clean.

## Problem

[012](012-setup-ui.md) makes setup *friendly* but still assumes a developer
environment (`python setup_app.py`, a `venv`, `pip`). To reach a non-developer
you need a **double-clickable binary**. And the moment you hand a binary to
someone else, a second question appears that is *not* a packaging question:
**how do you distribute it without tripping Google's app-verification wall?**
This spec answers both, and is clear-eyed about what packaging does and doesn't
remove.

## Decision 1 — the distribution model (the non-obvious part)

**Ship the binary with NO `credentials.json`. Each user creates their own Google
Cloud OAuth client on first run, through 012's Step-1 helper.**

Why this, and not "bundle one client so it just works":

- Bundling a single OAuth client forces every user onto **your** Cloud project.
  That project is an unverified "Testing" app using restricted Gmail scopes — so
  it's limited to test users you manually allowlist, and making it usable by the
  public requires Google's formal verification/security assessment (the exact
  thing the README calls disproportionate for a personal tool).
- With **per-user clients**, every user is the sole user of *their own*
  unverified app — precisely the personal setup the README already endorses.
  No shared project, no allowlist to manage, **no verification wall.**

So the artifact you distribute is the *app*, never the credentials. This also
keeps faith with the hard constraint: `credentials.json` (and `tokens/`) are
secrets, gitignored for a reason — **the build must never bundle either**, and
this spec extends "never log/return their contents" to "never package them."

### What this model does NOT remove (say it plainly)

Packaging buys *reach and convenience*, not *fewer steps*. Every user still:

- does the ~10-minute Google Cloud walkthrough (guided by 012, not automated),
- clicks through the one-time "unverified app → Advanced → Go to (unsafe)" warning,
- adds themselves as a test user in their own project,
- lives with the **7-day reauthorization** (Google policy for unverified apps;
  012 makes it one click, but only Google verification would remove it, and that
  stays out of scope).

A pop-up makes these *followable*, not *automatic*. That's enough to reach
non-developers, but the spec must not imply a zero-setup experience.

## Decision 2 — how to package

- **One binary, two entry points via subcommands.** A thin `main.py` dispatches:
  - `app serve` — runs `server.run()` over stdio (this is what Claude Desktop
    launches),
  - `app setup` — runs the 012 local web UI.
  One artifact to build, sign, and ship; 012's config generator points Claude
  Desktop's `command` at the exe with `args: ["serve"]` (no `venv`/`python`).
- **PyInstaller** as the tool (mature, per-OS). Nuitka is a fallback if startup
  time or size matters. Build **onefile** — a single `gmail-mcp.exe` the user can
  download and run, chosen for that friendliness. onefile self-extracts to a temp
  dir each launch (slightly slower cold start), but the data-path problem that
  normally makes onefile awkward is already solved: `resolve_data_dir` keeps all
  state in a per-user dir, not the temp extraction. (onedir — a folder — starts
  faster and is the fallback if the cold-start cost ever matters.)
- **Per-OS builds** — PyInstaller can't cross-compile: build the `.exe` on
  Windows, the `.app` on macOS, an AppImage/tarball on Linux.

## Mandatory code change — relocate the data dir when frozen

Today `accounts.py` sets `BASE_DIR = Path(__file__).resolve().parent`, and
`tokens/`, `credentials.json`, plus `server.py`'s `sent.log` / `settings.log`
and `gmail_client`'s default `attachments/` all hang off it. Under a frozen
build, `__file__` lives inside the (temporary, possibly read-only) bundle — so
**token writes would vanish and `credentials.json` wouldn't be found.** Fix it
once, in `accounts.py`, and have everything else consume it:

```python
# accounts.py
import os, sys
from pathlib import Path

def _default_data_dir() -> Path:
    if getattr(sys, "frozen", False):            # packaged build
        if sys.platform == "win32":
            root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        elif sys.platform == "darwin":
            root = Path.home() / "Library/Application Support"
        else:
            root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return root / "GmailMCP"
    return Path(__file__).resolve().parent        # dev: repo dir, unchanged

DATA_DIR = Path(os.environ.get("GMAIL_MCP_DATA_DIR", _default_data_dir()))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_DIR = DATA_DIR / "tokens"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"
```

- All state co-locates in a stable, user-writable dir, so an app installed
  read-only (Program Files) still works.
- `server.py` (`sent.log`, `settings.log`) and `gmail_client` (default
  `attachments/`) switch from `Path(__file__).resolve().parent` to
  `accounts.DATA_DIR`.
- The `GMAIL_MCP_DATA_DIR` env override is also the seam that makes the path
  logic **unit-testable** (frozen vs. dev vs. override) without a real build.

## PyInstaller specifics / gotchas

- **stdio purity for `serve`.** Claude Desktop speaks JSON-RPC over stdout;
  *anything* the frozen app prints to stdout corrupts the stream. Ensure the
  bootloader and our code write only MCP protocol to stdout — route any logging
  to stderr or a file. Verify this explicitly after building.
- **Hidden imports** for the dynamic google stack (`googleapiclient`,
  `google_auth_oauthlib`, the `google.*` / `google.auth` namespace packages).
  The existing `cache_discovery=False` in `gmail_client._service` already
  sidesteps the discovery-cache data-file issue.
- **No bundled data files** — because `credentials.json` is user-provided at
  runtime (Decision 1), there is nothing secret to embed at build time. The
  build spec should *assert* `tokens/` and `credentials.json` are excluded.
- **Antivirus false positives** are more common with onefile than onedir — an
  accepted cost of the single-file convenience; code signing is the real fix.

## Security considerations

- **Never bundle `credentials.json` or `tokens/`.** Both are secrets and
  gitignored; the build script must exclude them and ideally fail if present in
  the staging dir. This is the packaging form of the token-secrecy hard constraint.
- **Unsigned binaries** trip SmartScreen / Gatekeeper "unidentified developer,"
  and draw AV false positives. For personal use, click through. For real
  distribution you'd want **code signing** (Windows cert) and **Apple
  notarization** (paid developer account) — cost + identity, so left to whoever
  actually distributes; publish a checksum at minimum.
- **Token files land in a user dir** (`DATA_DIR/tokens/`) still in plaintext, as
  today. Optionally tighten permissions (POSIX `0o600`, Windows ACL) — a
  nice-to-have, documented, not a security guarantee.
- No new network surface: `serve` is stdio; `setup` is localhost-only per 012.

## Testing / verification

- **Unit-testable:** the `accounts.DATA_DIR` resolution (frozen flag mocked,
  `GMAIL_MCP_DATA_DIR` override, per-platform roots) — add to the suite.
- **Build-time check:** the packaging script asserts the staged bundle contains
  no `credentials.json` / `tokens/` / `*.log`.
- **Manual "clean machine" checklist** (the un-fakeable part): on a fresh
  Windows/macOS box with no Python — double-click → guided Google Cloud setup →
  drop in `credentials.json` → add an account → confirm Claude Desktop launches
  `app serve` and sees the tools. Keep this alongside the project's existing
  live-account manual loop.

## Conventions alignment

- `main.py` (dispatcher) and the build script are standalone tooling, like
  `add_account.py` / `setup_app.py` — **not imported by `server.py`**.
- The single source of data-dir truth is `accounts.DATA_DIR`; nothing
  recomputes `Path(__file__)` for state anymore.
- CLAUDE.md gains: the frozen-vs-dev data-dir behaviour, the `GMAIL_MCP_DATA_DIR`
  override, the stdio-purity requirement, and the build command.

## Out of scope / open questions

- **Auto-update** for the binary — a real feature, its own effort; not here.
- **Installers** (MSI, `.pkg`, Homebrew cask, AppImage integration) — the
  single-file exe already gives "download and run"; revisit installers only if
  there's demand (e.g. Start-menu entries, auto-update).
- **Code-signing certificates / notarization** — needs money and a verified
  identity; recommend leaving to whoever distributes, with checksums as the
  interim trust signal.
- **Open question:** keep the CLI (`add_account.py`) as a supported path for
  advanced users, or fold it entirely into `app setup`? Recommend keeping the
  CLI — it's tiny, scriptable, and already the tested happy path — with the UI
  as the friendly default rather than a replacement.
