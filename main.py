"""
Single entry point for the packaged build (spec 013).

    gmail-mcp          # no argument (e.g. double-click) -> open the setup UI
    gmail-mcp setup    # open the local setup UI
    gmail-mcp serve    # run the MCP server over stdio (what Claude Desktop launches)

The default with no argument is `setup`, because the only no-argument launch
in practice is a human double-clicking the app — they want the wizard, not a
silent stdio server. Claude Desktop always launches with an explicit `serve`
(the config the setup UI writes says `"args": ["serve"]`), so it never gets the
UI by accident.

Kept tiny and side-effect-free at import so `serve` never writes anything to
stdout except the MCP protocol — a stray print would corrupt Claude Desktop's
JSON-RPC stream. In development you can still run `python server.py` /
`python setup_app.py` directly; this dispatcher exists mainly so one built
binary can do both via a subcommand.
"""

import sys


def _hide_console_on_windows() -> None:
    """Make the stdio server windowless. When a client (Claude Desktop) launches
    a console exe, Windows may allocate a console window; hide it. stdio flows
    through inherited pipes, which are unaffected by the window being hidden, so
    the MCP stream keeps working. No-op on non-Windows or when no console is
    attached. Never writes to stdout (that would corrupt the MCP stream)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "setup"

    if cmd == "serve":
        _hide_console_on_windows()  # run the stdio server headless (no window)
        import server
        import updates

        updates.refresh_in_background()  # throttled cache refresh for next session (spec 015)
        server.server.run()  # stdio transport
        return 0
    if cmd == "setup":
        import setup_app

        setup_app.run()
        return 0
    if cmd in ("-h", "--help", "help"):
        print("usage: gmail-mcp [setup|serve]   (no argument = setup)")
        return 0

    print(f"unknown command: {cmd!r} (expected 'serve' or 'setup')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
