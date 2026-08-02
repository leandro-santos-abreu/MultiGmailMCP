"""Best-effort "is there a newer release?" check against the GitHub Releases API
(spec 015).

Notify-only: this NEVER downloads or runs anything — it compares the running
version against the latest published release and hands back a link. Everything is
best-effort: no network / GitHub down / rate-limited just degrades quietly, never
raises, never blocks. Uses only the standard library (no new dependency).
"""

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

import accounts
from version import __version__

REPO = "leandro-santos-abreu/MultiGmailMCP"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

_CACHE_TTL_SECONDS = 24 * 3600
_DISABLE_ENV = "GMAIL_MCP_NO_UPDATE_CHECK"


def disabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get(_DISABLE_ENV))


# --- version comparison (tiny, no dependency) ----------------------------


def _parse(v: str) -> tuple:
    """Parse a leading vMAJOR.MINOR.PATCH into an int tuple; ignore any 'v'
    prefix and pre-release/build suffix. Non-numeric → stop (best-effort)."""
    core = (v or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for piece in core.split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    lp, cp = _parse(latest), _parse(current)
    if not lp:
        return False
    n = max(len(lp), len(cp))
    lp += (0,) * (n - len(lp))
    cp += (0,) * (n - len(cp))
    return lp > cp


# --- fetch + cache -------------------------------------------------------


def fetch_latest_tag(timeout: float = 4.0, opener=None) -> str | None:
    """GET the latest (non-draft, non-prerelease) release tag from GitHub, or
    None on any failure. GitHub's /releases/latest already excludes drafts and
    pre-releases, so we don't have to."""
    try:
        req = urllib.request.Request(
            LATEST_API,
            headers={
                "User-Agent": f"gmail-mcp/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        _open = opener or urllib.request.urlopen
        with _open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name")
        return tag or None
    except Exception:  # noqa: BLE001 — best-effort; any failure means "unknown"
        return None


def _cache_path() -> Path:
    return accounts.DATA_DIR / "update_check.json"


def read_cache() -> dict | None:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_cache(latest: str) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"checked_at": int(time.time()), "latest": latest}), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def status_from_cache(current: str = __version__) -> dict:
    """Update status using ONLY the local cache — no network. Safe to call on the
    hot path (server startup instructions)."""
    latest = (read_cache() or {}).get("latest")
    available = bool(latest) and is_newer(latest, current)
    return {"current": current, "latest": latest, "update_available": available, "url": RELEASES_PAGE}


def check(current: str = __version__, ttl: int = _CACHE_TTL_SECONDS, force: bool = False,
          env: dict | None = None) -> dict:
    """Return the update status, refreshing the cache from GitHub when it's stale
    or `force`d. Honours the off switch (then it's cache-only, no network)."""
    if not disabled(env):
        cache = read_cache()
        fresh = bool(cache) and (time.time() - cache.get("checked_at", 0) < ttl)
        if force or not fresh:
            tag = fetch_latest_tag()
            if tag:
                write_cache(tag)
    return status_from_cache(current)


def refresh_in_background(env: dict | None = None) -> None:
    """Kick a throttled cache refresh onto a daemon thread — never blocks, never
    touches stdout. No-op when disabled. Used by `serve` so the *next* session's
    startup instructions are current."""
    if disabled(env):
        return
    threading.Thread(target=lambda: check(force=False), daemon=True).start()


# --- surfacing helpers ---------------------------------------------------


def instructions_note(current: str = __version__) -> str:
    """A one-line note for the MCP server `instructions` when the cache shows an
    update, else "". Cache-only (no network) so it's safe at startup."""
    st = status_from_cache(current)
    if not st["update_available"]:
        return ""
    return (
        f"A newer version of this Gmail MCP server is available (you're on "
        f"v{st['current']}, latest is {st['latest']}). If this is the start of the "
        f"conversation, let the user know once that they can update at {st['url']}, "
        f"then don't mention it again."
    )


def human_summary(current: str = __version__, force: bool = True) -> str:
    """Human-readable result for the check_for_updates tool (does a fresh check
    by default, since the user explicitly asked)."""
    st = check(current, force=force)
    if st.get("update_available"):
        return f"You're on v{st['current']}. A newer version {st['latest']} is available — download it at {st['url']}."
    if st.get("latest"):
        return f"You're on the latest version (v{st['current']})."
    return f"Couldn't check for updates right now (no network, or GitHub unavailable). You're on v{current}."
