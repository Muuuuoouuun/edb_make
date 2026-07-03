# -*- mode: python ; coding: utf-8 -*-

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(globals().get("SPECPATH", Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd())).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.verify_frontend_package import collect_errors


def project_path(rel_path: str) -> Path:
    return PROJECT_ROOT / rel_path


def pyinstaller_work_path() -> Path:
    return Path(globals().get("workpath", project_path("build/ClassInEDBMVP"))).resolve()


def verify_frontend_package() -> None:
    errors = collect_errors(PROJECT_ROOT)
    if errors:
        message = "\n".join(f"[frontend-package] ERROR: {error}" for error in errors)
        raise SystemExit(message)


def resolve_icon() -> str | None:
    icon_name = "app_icon.icns" if sys.platform == "darwin" else "app_icon.ico"
    icon_path = project_path(f"assets/{icon_name}")
    return str(icon_path) if icon_path.exists() else None


verify_frontend_package()
resolved_icon = resolve_icon()


def build_update_config() -> tuple[str, dict]:
    config = {
        "appId": "ClassInEDBMVP",
        "appName": "ClassInEDBMVP",
        "version": "0.1.0",
        "updateFeedUrl": "",
        "downloadUrl": "",
        "releaseNotesUrl": "",
    }
    source = project_path("app_update_config.json")
    if source.exists():
        try:
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                config.update({key: value for key, value in data.items() if value is not None})
        except json.JSONDecodeError:
            pass
    env_overrides = {
        "appId": os.environ.get("EDB_PACKAGE_APP_ID", os.environ.get("EDB_APP_ID", "")).strip(),
        "appName": os.environ.get("EDB_PACKAGE_APP_NAME", "").strip(),
        "version": os.environ.get("EDB_PACKAGE_APP_VERSION", os.environ.get("EDB_APP_VERSION", "")).strip(),
        "updateFeedUrl": os.environ.get("EDB_PACKAGE_UPDATE_FEED_URL", os.environ.get("EDB_UPDATE_FEED_URL", "")).strip(),
        "downloadUrl": os.environ.get("EDB_PACKAGE_DOWNLOAD_URL", os.environ.get("EDB_DOWNLOAD_URL", "")).strip(),
        "releaseNotesUrl": os.environ.get("EDB_PACKAGE_RELEASE_NOTES_URL", os.environ.get("EDB_RELEASE_NOTES_URL", "")).strip(),
    }
    config.update({key: value for key, value in env_overrides.items() if value})
    target = pyinstaller_work_path() / "app_update_config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target), config


generated_update_config, generated_update_config_payload = build_update_config()
bundle_version = str(generated_update_config_payload.get("version") or "0.1.0")
bundle_identifier = os.environ.get("EDB_MACOS_BUNDLE_ID", "local.classin.edbmvp").strip() or "local.classin.edbmvp"

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
    ("assets/app_icon.png", "assets"),
]

APP_CONFIG_DATAS = [
    (generated_update_config, "."),
]

SCRIPT_DATAS = [
    ("scripts/render_hwp_with_rhwp_core.mjs", "scripts"),
]

HIDDEN_IMPORTS = [
    "preprocess",
    "build_mvp_export",
    "build_problem_board_edb",
    "build_structured_page_json",
    "edb_builder",
    "page_repair",
    "image_reconstruction_backend",
]

a = Analysis(
    [str(project_path("app_server.py"))],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(project_path(source)), destination)
        for source, destination in UI_DATAS + ASSET_DATAS + SCRIPT_DATAS
        if project_path(source).exists()
    ] + APP_CONFIG_DATAS,
    hiddenimports=HIDDEN_IMPORTS,
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
        bundle_identifier=bundle_identifier,
        version=bundle_version,
        info_plist={
            "CFBundleVersion": bundle_version,
        },
    )
