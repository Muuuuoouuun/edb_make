# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


def resolve_icon() -> str | None:
    icon_name = "app_icon.icns" if sys.platform == "darwin" else "app_icon.ico"
    icon_path = Path("assets") / icon_name
    return str(icon_path) if icon_path.exists() else None


resolved_icon = resolve_icon()

UI_DATAS = [
    ("ui_prototype/index.html", "ui_prototype"),
    ("ui_prototype/board.html", "ui_prototype"),
    ("ui_prototype/reorder.js", "ui_prototype"),
    ("ui_prototype/review_filters.js", "ui_prototype"),
    ("ui_prototype/publish_summary.js", "ui_prototype"),
    ("ui_prototype/publish_guard.js", "ui_prototype"),
    ("ui_prototype/app.bundle.js", "ui_prototype"),
    ("ui_prototype/vendor/react.production.min.js", "ui_prototype/vendor"),
    ("ui_prototype/vendor/react-dom.production.min.js", "ui_prototype/vendor"),
]

ASSET_DATAS = [
    ("assets/app_icon.ico", "assets"),
    ("assets/app_icon.icns", "assets"),
    ("assets/app_icon.png", "assets"),
]

a = Analysis(
    ['app_server.py'],
    pathex=[],
    binaries=[],
    datas=[item for item in UI_DATAS + ASSET_DATAS if Path(item[0]).exists()],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClassInEDBMVP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=resolved_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClassInEDBMVP',
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name='ClassInEDBMVP.app',
        icon=resolved_icon,
        bundle_identifier='local.classin.edbmvp',
    )
