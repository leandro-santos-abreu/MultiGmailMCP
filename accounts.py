"""
Account/token management for the multi-account Gmail MCP server.

Each Gmail account gets one file in tokens/<account_name>.json, holding the
OAuth refresh token for that account. add_account.py creates these files;
server.py and gmail_client.py only ever read and (silently) refresh them.
"""

import os
import sys
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

BASE_DIR = Path(__file__).resolve().parent


def resolve_data_dir(env: dict | None = None, frozen: bool | None = None, platform: str | None = None) -> Path:
    """Where mutable state (tokens/, credentials.json, sent.log, settings.log,
    attachments/) lives.

    In a normal dev/source run this is the repo dir (BASE_DIR), unchanged. In a
    packaged build (`sys.frozen`, spec 013) BASE_DIR points inside a temporary,
    possibly read-only bundle, so state goes to a stable per-user config dir
    instead. `GMAIL_MCP_DATA_DIR` overrides everything (and is the test seam)."""
    env = os.environ if env is None else env
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform

    override = env.get("GMAIL_MCP_DATA_DIR")
    if override:
        return Path(override)
    if not frozen:
        return BASE_DIR

    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    if platform == "win32":
        return Path(env.get("APPDATA") or (home / "AppData" / "Roaming")) / "GmailMCP"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "GmailMCP"
    return Path(env.get("XDG_CONFIG_HOME") or (home / ".config")) / "GmailMCP"


DATA_DIR = resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_DIR = DATA_DIR / "tokens"
CREDENTIALS_FILE = DATA_DIR / "credentials.json"

# Search/read, draft/send, organise mail (labels, archive, read-state, star,
# trash), and manage settings — filters/vacation/IMAP/POP (settings.basic) plus
# forwarding, send-as, and delegation (settings.sharing). Trash is recoverable —
# permanent deletion is deliberately NOT here: the https://mail.google.com/
# scope is excluded on purpose, so nothing this server does is truly
# irreversible. The forwarding/send-as/delegation tools are the exfiltration
# surface and carry the heaviest confirm-before-acting guards. See README
# "Scopes".
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/gmail.settings.sharing",
]


class AccountError(Exception):
    """Raised when a tool is called with an account name that isn't configured."""


def list_account_names() -> list[str]:
    TOKENS_DIR.mkdir(exist_ok=True)
    return sorted(p.stem for p in TOKENS_DIR.glob("*.json"))


def _token_path(account_name: str) -> Path:
    return TOKENS_DIR / f"{account_name}.json"


def save_credentials(account_name: str, creds: Credentials) -> None:
    TOKENS_DIR.mkdir(exist_ok=True)
    _token_path(account_name).write_text(creds.to_json())


def load_credentials(account_name: str) -> Credentials:
    """Load an account's credentials, refreshing the access token if needed."""
    path = _token_path(account_name)
    if not path.exists():
        known = list_account_names()
        raise AccountError(
            f"No account named '{account_name}'. "
            f"Configured accounts: {known if known else 'none yet'}. "
            f"Run `python add_account.py {account_name}` first, then restart Claude Desktop."
        )

    creds = Credentials.from_authorized_user_file(str(path), SCOPES)

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            raise AccountError(
                f"'{account_name}'s authorization has expired or was revoked ({e}). "
                "This is expected roughly every 7 days while your Google Cloud OAuth app is in "
                "'Testing' publishing status (Google's limitation on unverified apps, not a bug "
                f"here) — just run `python add_account.py {account_name}` again to reauthorize. "
                "See README.md 'Why do I have to reauthorize so often?'"
            ) from None
        save_credentials(account_name, creds)  # persist the refreshed access token

    return creds
