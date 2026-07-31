"""
Account/token management for the multi-account Gmail MCP server.

Each Gmail account gets one file in tokens/<account_name>.json, holding the
OAuth refresh token for that account. add_account.py creates these files;
server.py and gmail_client.py only ever read and (silently) refresh them.
"""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

BASE_DIR = Path(__file__).resolve().parent
TOKENS_DIR = BASE_DIR / "tokens"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"

# Kept intentionally minimal: read + search + create/list drafts.
# No gmail.modify and no gmail.send, so this server can never send mail or
# touch labels/trash, even by mistake — see README "Scopes" section if you
# want to extend this later.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
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
