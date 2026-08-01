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
SPEC = ROOT / "gmail-mcp.spec"  # committed spec: hidden imports + discovery-doc slimming

# Never let a real credential get swept into a distributable bundle. An empty
# tokens/ (just its .gitkeep) is fine — we only refuse on actual token files.
FORBIDDEN_FILES = ["credentials.json", "sent.log", "settings.log"]


def _guard_no_secrets() -> None:
    present = [name for name in FORBIDDEN_FILES if (ROOT / name).exists()]
    token_files = list((ROOT / "tokens").glob("*.json"))
    if token_files:
        present.append(f"tokens/ ({len(token_files)} token file(s))")
    if present:
        raise SystemExit(
            "Refusing to build — these hold secrets and must not be bundled; move "
            f"them out of the project dir first: {', '.join(present)}"
        )


def main() -> int:
    _guard_no_secrets()
    # Build from the committed spec (not CLI flags) so the discovery-doc slimming
    # and hidden imports live in one reproducible, reviewable place.
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode == 0:
        print(f"\nBuilt dist/{APP_NAME}/ — double-click the exe (or run `{APP_NAME} setup`) for the wizard.")
        print("IMPORTANT: verify `<exe> serve` prints nothing to stdout but MCP protocol.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
