# -*- mode: python ; coding: utf-8 -*-
# Committed PyInstaller spec (spec 013), used by build.py.
#
# ONEFILE build: produces a single dist/gmail-mcp.exe you can download and run
# (setup wizard by default; `serve` when Claude Desktop launches it). onefile is
# chosen for "download one file and run it" friendliness; it self-extracts to a
# temp dir each launch (slightly slower cold start) but accounts.resolve_data_dir
# keeps all state in a per-user dir, so the read-only extraction is fine.
#
# Slimming: googleapiclient bundles discovery documents for ~586 Google APIs
# (~99 MB); this server only ever builds the Gmail client, so we keep gmail.*
# and drop the rest — cutting the bundle from ~143 MB to ~44 MB. gmail.v1.json
# stays, so build("gmail","v1", ...) still resolves its static discovery doc
# with no behaviour change.

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['googleapiclient', 'googleapiclient.discovery', 'google.auth', 'google.oauth2.credentials', 'google_auth_oauthlib', 'google_auth_oauthlib.flow', 'google_auth_httplib2'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)


def _keep(dest):
    p = dest.replace('\\', '/')
    if 'googleapiclient/discovery_cache/documents/' in p:
        return '/gmail.' in p  # keep only Gmail's discovery doc(s)
    return True


a.datas = [d for d in a.datas if _keep(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='gmail-mcp',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # `serve` speaks stdio — must stay a console app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
