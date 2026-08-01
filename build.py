"""
Package the app into a standalone executable (spec 013).

    pip install -r requirements-build.txt
    python build.py            # builds dist/gmail-mcp/ (onedir)

Design decisions (see specs/013-packaging-distribution.md):
- onedir, not onefile: onefile extracts to a temp dir each launch, which is
  slower and worsens data-path handling. accounts.resolve_data_dir already keeps
  mutable state out of the bundle (a per-user config dir when frozen), so the
  bundle can be read-only.
- Ships NO secrets: this script refuses to build if credentials.json or tokens/
  are present in the project dir, so they can never be swept into the bundle.
- One binary, two subcommands (main.py -> serve|setup). The setup UI writes a
  Claude Desktop config that points at this exe with args ["serve"].

Build per-OS — PyInstaller can't cross-compile. This wraps PyInstaller; it is
not run by the server or the tests.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "gmail-mcp"

# Google client libraries load some parts dynamically; make sure PyInstaller
# keeps them. cache_discovery=False (gmail_client) already avoids the discovery
# cache data-file issue.
HIDDEN_IMPORTS = [
    "googleapiclient",
    "googleapiclient.discovery",
    "google.auth",
    "google.oauth2.credentials",
    "google_auth_oauthlib",
    "google_auth_oauthlib.flow",
    "google_auth_httplib2",
]

# Never let a real credential get swept into a distributable bundle.
FORBIDDEN = ["credentials.json", "tokens", "sent.log", "settings.log"]


def _guard_no_secrets() -> None:
    present = [name for name in FORBIDDEN if (ROOT / name).exists()]
    if present:
        raise SystemExit(
            "Refusing to build: these must not be bundled — move them out of the "
            f"project dir first: {', '.join(present)}"
        )


def main() -> int:
    _guard_no_secrets()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--onedir",
        "--console",  # serve speaks stdio; must stay a console app
    ]
    for hidden in HIDDEN_IMPORTS:
        cmd += ["--hidden-import", hidden]
    cmd += [str(ROOT / "main.py")]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print(f"\nBuilt dist/{APP_NAME}/ — test it with:  dist/{APP_NAME}/{APP_NAME} setup")
        print("IMPORTANT: verify `<exe> serve` prints nothing to stdout but MCP protocol.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
