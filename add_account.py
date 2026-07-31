"""
One-time authorization script — run this once per Gmail account you want
the MCP server to know about.

Usage:
    python add_account.py work
    python add_account.py personal

This opens your browser to Google's consent screen. Sign in with the
account you're naming (e.g. "work" should be signed in with your work
Gmail), approve access, and the refresh token gets saved to
tokens/<name>.json. Close the "authentication flow has completed" tab
afterwards and you're done — no need to run this again unless you
delete that token file or revoke access.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

import accounts


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python add_account.py <account_name>")
        print("Example: python add_account.py work")
        sys.exit(1)

    account_name = sys.argv[1]

    if not accounts.CREDENTIALS_FILE.exists():
        print(f"Missing {accounts.CREDENTIALS_FILE}.")
        print("Download your OAuth client (Desktop app type) from Google Cloud Console")
        print(f"and save it as credentials.json next to this script. See README.md.")
        sys.exit(1)

    existing = accounts.list_account_names()
    if account_name in existing:
        print(f"'{account_name}' is already configured. Re-authorizing will overwrite its token.")
        if input("Continue? [y/N] ").strip().lower() != "y":
            sys.exit(0)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(accounts.CREDENTIALS_FILE), accounts.SCOPES
    )
    print(f"\nOpening your browser to authorize '{account_name}'...")
    print("Sign in with THAT account's Google login when prompted.\n")
    creds = flow.run_local_server(port=0)

    accounts.save_credentials(account_name, creds)
    print(f"\nDone. '{account_name}' saved to tokens/{account_name}.json")
    print("Configured accounts:", ", ".join(accounts.list_account_names()))


if __name__ == "__main__":
    main()
