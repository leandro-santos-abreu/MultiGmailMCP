"""
Single entry point for the packaged build (spec 013).

    gmail-mcp serve   # run the MCP server over stdio (what Claude Desktop launches)
    gmail-mcp setup   # open the local setup UI

Kept tiny and side-effect-free at import so `serve` never writes anything to
stdout except the MCP protocol — a stray print would corrupt Claude Desktop's
JSON-RPC stream. In development you can still run `python server.py` /
`python setup_app.py` directly; this dispatcher exists mainly so one built
binary can do both via a subcommand.
"""

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "serve"

    if cmd == "serve":
        import server

        server.server.run()  # stdio transport
        return 0
    if cmd == "setup":
        import setup_app

        setup_app.run()
        return 0
    if cmd in ("-h", "--help", "help"):
        print("usage: gmail-mcp [serve|setup]")
        return 0

    print(f"unknown command: {cmd!r} (expected 'serve' or 'setup')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
