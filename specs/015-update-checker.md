# Spec 015 — Update checker for the packaged exe

- **Status:** proposed
- **New OAuth scope required:** none.
- **Network:** one outbound `GET` to the GitHub Releases API, **on demand**
  (setup UI / explicit tool call), never silently from `serve`.
- **Destructive:** no. **Checks and notifies only — never downloads or runs
  anything.**

## Why

The app now ships as a single `gmail-mcp.exe` via GitHub Releases (`v1.0.0`), and
more is coming (Calendar → v1.1.0). A user who downloaded the exe has no way to
learn a newer one exists. A lightweight "you're on v1.0.0, v1.1.0 is available —
here's the download" nudge closes that gap, without turning into an auto-updater.

## Hard boundaries (what this is NOT)

- **No auto-download and no auto-run.** Fetching and executing a new binary is
  exactly the "download/execute from an untrusted source" risk this project
  avoids, and auto-updating an **unsigned** exe is worse. This spec **only**
  compares versions and shows a link to the release page; the user downloads and
  replaces the exe themselves.
- **Nothing in `serve`'s stdout.** `serve` speaks MCP over stdout; the checker
  must never print there. No automatic phone-home on `serve` launch either.
- **Best-effort, never blocking.** No network / GitHub down / rate-limited →
  degrade silently ("couldn't check"); never crash, never delay startup.

## Prerequisite: a real version constant

There is no version in the code today (only the git tag). Add a single source of
truth and bake it into the build:

```python
# version.py
__version__ = "1.0.0"
```

- `main.py` / `setup_app.py` import it; PyInstaller bundles it, so the running exe
  knows its own version.
- **Cutting a release bumps `version.py` in lockstep with the tag** — document
  this in the release procedure (`build.py` → `gh release create vX.Y.Z`). A
  build assertion that `version.py` matches the intended tag prevents drift.

## The check

```python
REPO = "leandro-santos-abreu/MultiGmailMCP"

def fetch_latest_tag(timeout=4) -> str | None:
    # GET https://api.github.com/repos/<REPO>/releases/latest  (unauthenticated)
    # returns tag_name (e.g. "v1.1.0") or None on any failure.

def is_newer(latest: str, current: str) -> bool:
    # compare the leading vMAJOR.MINOR.PATCH numerically; tolerate the 'v'
    # prefix and ignore/So-order any pre-release suffix.
```

- **Use `/releases/latest`** — GitHub already excludes drafts and pre-releases
  from it, so pre-release tags (`v1.1.0-beta`) don't create false "update"
  nudges, and the comparator stays a simple numeric `MAJOR.MINOR.PATCH` compare
  (a tiny hand-rolled helper — no new dependency).
- **Unauthenticated**, `urllib.request` (stdlib), short timeout, a descriptive
  `User-Agent`. Sends no personal data — GitHub sees only the request (IP +
  user-agent), no account/token/mail data.
- **Cache** the result in `accounts.DATA_DIR/update_check.json`
  (`{"checked_at": ..., "latest": "v1.1.0"}`) with a TTL (e.g. 24h) so the UI
  doesn't hit GitHub on every load and stays within the 60/hr unauthenticated
  limit.

Result shape: `{"current": "1.0.0", "latest": "1.1.0", "update_available": true,
"url": "https://github.com/<REPO>/releases/latest"}` (or
`{"update_available": false}` / `{"error": "..."}`).

## Where it surfaces

1. **Setup UI banner (primary).** Extend `/api/status` with an `update` field
   (cached check). When an update is available, the page shows a small banner:
   *"Update available: v1.0.0 → v1.1.0 — Download →"* linking to the release page.
   This is the right home: it's user-facing, already has a browser, and the user
   is already there to run setup/reauthorize.
2. **Optional MCP tool `check_for_updates()`** so the user can ask Claude *"is
   there a new version?"*. Read-only, on **explicit** user request; returns a
   human string ("You're on v1.0.0; v1.1.0 is available at <url>" / "You're on the
   latest."). Its docstring notes it contacts GitHub. This runs inside `serve` but
   is fine because (a) it's user-triggered, not automatic, and (b) it returns a
   value, never writes stdout.
3. **Never** an automatic background check in `serve`.

## Privacy & consent

- The check contacts `api.github.com` — document this plainly (README/setup UI),
  since the project's whole posture is "nothing hosted, localhost only." It sends
  no user data and stores only a timestamp + the latest tag locally.
- On-demand only (UI load / tool call), cached, no telemetry. Consider a simple
  off switch (`GMAIL_MCP_NO_UPDATE_CHECK=1`) for users who want zero outbound
  calls beyond Google.

## Testing (all offline)

- `is_newer`: equal, newer, older, `v`-prefix on either side, missing/short
  components, malformed input (returns False, never raises).
- `fetch_latest_tag`: mock `urllib` → parses `tag_name`; on HTTP error / timeout /
  bad JSON returns `None` (best-effort).
- Cache: fresh cache skips the network; stale/absent triggers a fetch; a fetch
  failure falls back to the cached value if present.
- `check_for_updates` tool: given a mocked latest, returns the right string; on
  fetch failure returns a graceful "couldn't check" message (never an exception).
- Assert the checker is never invoked on a plain `serve` startup (no automatic
  network call, no stdout writes).

## Out of scope / open questions

- **Auto-download / auto-install / self-replace** — deliberately excluded (see
  Hard boundaries). If ever wanted, it needs code signing + verification first.
- **Signature/checksum verification** of the downloaded exe — worth doing once
  the release publishes checksums; tie to the signing work, not here.
- **Open question:** check automatically on setup-UI load (cached), or only when
  the user clicks a "Check for updates" button? Recommend **auto on load, cached
  24h** — it's the whole point (a passive nudge), the cache keeps it cheap and
  polite, and the off switch covers the privacy-strict case.
