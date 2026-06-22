# MVP Packaging Guide

## What This Delivers
- Local HTTP app server for the ClassIn EDB MVP
- Browser UI connected to the real export pipeline
- Double-click local launch for development
- User-facing Windows `.exe` and macOS `.app` packaging with PyInstaller
- Bundled React/Babel browser runtime so the core UI can open without CDN access

## Main Entry Points
- Local app server: `app_server.py`
- Windows local launcher: `run_local_app.ps1`
- macOS local launcher: `run_local_app.command`
- Windows packaging script: `package_mvp.ps1`
- Windows installer script: `package_windows_installer.ps1`
- macOS packaging script: `package_macos_app.sh`

## Local Run
```powershell
cd C:\Projects\Class_project\edb_make
.\run_local_app.ps1 -InstallDeps
```

If dependencies are already installed:
```powershell
.\run_local_app.ps1
```

Default app URL:
```text
http://127.0.0.1:8765/
```

## macOS Double-Click Run
For day-to-day local use, packaging is not required. Double-click:

```text
run_local_app.command
```

The launcher requires Python 3.11 or newer because the current code uses `enum.StrEnum`. It checks the existing `.venv` before use; if `.venv` is missing or was created with Python older than 3.11, it recreates `.venv` with a suitable `python3` interpreter, installs `requirements-local.txt` only when needed, starts `app_server.py`, and opens the browser at `http://127.0.0.1:8765/`.

If Python 3.11+ is not installed, the launcher shows a Korean error message and waits for Enter before closing.

If the local server is already running, it only opens the browser instead of starting a duplicate server.

## In-App Flow
1. Start the local app server
2. Open the browser UI
3. Click `Choose source`
4. Pick an image or PDF
5. Set `subject`, `OCR`, and output folder name
6. Click `Run export`
7. Review source/problem/board previews
8. Open the generated `.edb` from the inspector or header

## PyInstaller Packaging
Windows packages must be built on Windows. macOS `.app` bundles must be built on macOS.

### Windows `.exe`
Build the installable Windows setup file on Windows:
```powershell
.\package_windows_installer.ps1 -Clean -InstallPyInstaller
```

With in-app update metadata:
```powershell
.\package_windows_installer.ps1 -Clean -InstallPyInstaller `
  -Version 0.1.1 `
  -UpdateFeedUrl "https://example.com/classin-edb/update.json" `
  -DownloadUrl "https://example.com/classin-edb/download"
```

Expected installer:
```text
dist\ClassInEDBMVP-Setup.exe
```

That installer creates a Start menu shortcut and can create a desktop shortcut. Clicking the installed app opens the browser at the local app.

For external Windows testing, sign both the packaged app binaries and the final installer with an Authenticode code-signing certificate:
```powershell
.\package_windows_installer.ps1 -Clean -InstallPyInstaller -Sign `
  -SignCertificatePath "C:\secure\ClassInEDB-CodeSigning.pfx" `
  -SignCertificatePassword $env:WINDOWS_CERT_PASSWORD
```

If the certificate is already installed in the Windows certificate store, you can sign by thumbprint, subject, or automatic selection:
```powershell
.\package_windows_installer.ps1 -Clean -InstallPyInstaller -Sign `
  -SignCertificateThumbprint "CERTIFICATE_THUMBPRINT"

.\package_windows_installer.ps1 -Clean -InstallPyInstaller -Sign `
  -SignCertificateSubject "Your Publisher Name"

.\package_windows_installer.ps1 -Clean -InstallPyInstaller -Sign `
  -SignCertificateAutoSelect
```

The script locates `signtool.exe` from `PATH` or the Windows SDK. It signs `.exe`, `.dll`, and `.pyd` files in the packaged app folder before building the installer, then signs and verifies `dist\ClassInEDBMVP-Setup.exe`.

If you only want the raw packaged app folder, install PyInstaller if needed:
```powershell
.\package_mvp.ps1 -InstallPyInstaller
```

Or package directly if PyInstaller is already installed:
```powershell
.\package_mvp.ps1
```

Useful options:
```powershell
# Create a single executable file instead of a directory
.\package_mvp.ps1 -OneFile

# Keep a console window for debugging
.\package_mvp.ps1 -Console

# Reuse an existing frontend bundle instead of running Node
.\package_mvp.ps1 -SkipFrontendBuild

# Clean previous builds and zip the output directory
.\package_mvp.ps1 -OutputDir .\dist_smoke -Clean -Zip
```

Expected output:
- **Default**: a folder containing the executable and dependencies: `dist\ClassInEDBMVP\`
- **Single file**: a standalone executable: `dist\ClassInEDBMVP.exe`

Typical packaged launch target:
- Default mode: `dist\ClassInEDBMVP\ClassInEDBMVP.exe`
- Standalone mode: `dist\ClassInEDBMVP.exe`

The default Windows build is windowed, so no console appears. Logs are written under:
```text
%USERPROFILE%\Documents\ClassInEDBMVP\.app_runtime\app.log
```

### macOS `.app`
Install PyInstaller if needed and build:
```zsh
./package_macos_app.sh --install-pyinstaller --clean --zip
```

With in-app update metadata:
```zsh
./package_macos_app.sh --install-pyinstaller --clean --dmg --zip \
  --version 0.1.1 \
  --bundle-id "local.classin.edbmvp" \
  --update-feed-url "https://example.com/classin-edb/update.json" \
  --download-url "https://example.com/classin-edb/download"
```

If PyInstaller is already installed:
```zsh
./package_macos_app.sh --clean --zip
```

Expected output:
```text
dist/ClassInEDBMVP.app
dist/ClassInEDBMVP-macOS.zip
```

The default macOS build is windowed and ad-hoc signed when `codesign` is available. Logs are written under:
```text
~/Documents/ClassInEDBMVP/.app_runtime/app.log
```

For external macOS testing without Gatekeeper blocking, build with a real Apple Developer ID Application certificate and notarize the app/DMG:
```zsh
./package_macos_app.sh --clean --dmg --zip \
  --version 0.1.1 \
  --bundle-id "com.yourcompany.classin-edb" \
  --sign-identity "Developer ID Application: Your Company (TEAMID)" \
  --notarize \
  --notary-key "/secure/AuthKey_KEYID.p8" \
  --notary-key-id "KEYID" \
  --notary-issuer "ISSUER_UUID"
```

If the Developer ID certificate is installed in Keychain, `--sign-identity auto` selects the first `Developer ID Application` identity. You can also use a saved notarytool profile:
```zsh
xcrun notarytool store-credentials "classin-edb-notary" \
  --apple-id "developer@example.com" \
  --team-id "TEAMID" \
  --password "APP_SPECIFIC_PASSWORD"

./package_macos_app.sh --clean --dmg --zip \
  --sign-identity auto \
  --notarize \
  --notary-profile "classin-edb-notary"
```

The unsigned/ad-hoc DMG is fine for internal development, but a downloaded public macOS app needs Developer ID signing, notarization, and stapling to open cleanly on other Macs.

## In-App Updates
The app uses a semi-automatic update flow:
1. The installed app keeps user settings and API keys under the user's app runtime folder.
2. `칠판 설정` shows the current app version and an `업데이트 확인` button.
3. If the configured update feed reports a newer version, the app opens the configured download page in the browser.
4. The user installs the new `.dmg` or `Setup.exe` over the previous app. Existing API keys and session data stay in the runtime folder.

Update feed and download URLs must use HTTPS. Plain HTTP is accepted only for loopback development URLs such as `http://127.0.0.1:9999/update.json`.

Default update metadata lives in:
```text
app_update_config.json
```

The packaged app reads `app_update_config.json` from bundled resources, then allows a local override at:
```text
Documents\ClassInEDBMVP\app_update_config.json
~/Documents/ClassInEDBMVP/app_update_config.json
```

Prefer the packaging scripts for release builds because they generate build-scoped update metadata. If you run `pyinstaller ClassInEDBMVP.spec` directly, set the same metadata through environment variables such as `EDB_PACKAGE_APP_VERSION`, `EDB_PACKAGE_UPDATE_FEED_URL`, and `EDB_PACKAGE_DOWNLOAD_URL`.

Update feed JSON shape:
```json
{
  "schemaVersion": 1,
  "appId": "ClassInEDBMVP",
  "appName": "ClassInEDBMVP",
  "channel": "stable",
  "version": "0.1.1",
  "publishedAt": "2026-06-19T00:00:00+00:00",
  "summary": "Bug fixes and packaging improvements.",
  "releaseNotesUrl": "https://example.com/releases/0.1.1",
  "manifestUrl": "https://example.com/releases/0.1.1/manifest.json",
  "manifestSha256": "manifest-sha256",
  "platforms": {
    "windows": {
      "version": "0.1.1",
      "downloadUrl": "https://example.com/ClassInEDBMVP-Setup.exe",
      "releaseNotesUrl": "https://example.com/releases/0.1.1",
      "fileName": "ClassInEDBMVP-Setup.exe",
      "artifactType": "setup-exe",
      "arch": "x64",
      "sizeBytes": 12345678,
      "sha256": "windows-installer-sha256"
    },
    "macos": {
      "version": "0.1.1",
      "downloadUrl": "https://example.com/ClassInEDBMVP-macOS.dmg",
      "releaseNotesUrl": "https://example.com/releases/0.1.1",
      "fileName": "ClassInEDBMVP-macOS.dmg",
      "artifactType": "dmg",
      "arch": "arm64",
      "sizeBytes": 12345678,
      "sha256": "macos-installer-sha256"
    }
  }
}
```

Generate the feed after building release artifacts:
```zsh
python3 scripts/build_update_feed.py \
  --version 0.1.1 \
  --channel stable \
  --summary "Packaging and updater fixes." \
  --update-feed-url "https://example.com/classin-edb/update.json" \
  --release-notes-url "https://example.com/releases/0.1.1" \
  --manifest-url "https://example.com/releases/0.1.1/manifest.json" \
  --macos-url "https://example.com/ClassInEDBMVP-macOS.dmg" \
  --macos-file dist/ClassInEDBMVP-macOS.dmg \
  --windows-url "https://example.com/ClassInEDBMVP-Setup.exe" \
  --windows-file dist/ClassInEDBMVP-Setup.exe \
  --manifest-output dist/manifest.json \
  --checksums-output dist/checksums.txt \
  --output dist/update.json
```

Upload `dist/update.json` to the URL used by `--update-feed-url`, and keep `dist/manifest.json` plus `dist/checksums.txt` with the same release assets.

The GitHub Actions workflow in `.github/workflows/build-installers.yml` builds the macOS DMG/zip and Windows Setup.exe on matching runners, then generates `update.json`, `manifest.json`, and `checksums.txt` from those artifacts.

For signed public builds, configure these repository secrets:
```text
MACOS_CERTIFICATE_P12_BASE64
MACOS_CERTIFICATE_PASSWORD
MACOS_CODESIGN_IDENTITY
APPLE_NOTARY_KEY_ID
APPLE_NOTARY_ISSUER_ID
APPLE_NOTARY_KEY_P8_BASE64
WINDOWS_CERTIFICATE_PFX_BASE64
WINDOWS_CERTIFICATE_PASSWORD
```

Optional repository variable:
```text
WINDOWS_SIGN_TIMESTAMP_URL
```

If signing secrets are missing, CI still produces internal-test installers. macOS then remains ad-hoc signed, and Windows remains unsigned.

## Included Runtime Assets
- `ui_prototype\index.html`
- `ui_prototype\board.html`
- `ui_prototype\reorder.js`
- `ui_prototype\review_filters.js`
- `ui_prototype\publish_summary.js`
- `ui_prototype\publish_guard.js`
- `ui_prototype\app.bundle.js`
- `ui_prototype\vendor\react.production.min.js`
- `ui_prototype\vendor\react-dom.production.min.js`
- `app_update_config.json`
- `assets\app_icon.ico`
- `assets\app_icon.icns`
- `assets\app_icon.png`

## Notes
- `ui_prototype\app.bundle.js` is generated from `art.jsx`, `tweaks-panel.jsx`, and `app.jsx` by `scripts\build_frontend_bundle.mjs`. Packaging scripts rebuild it when Node.js is available.
- Browser-side Babel is not included in packaged builds.
- Development runs write outputs into the project folder unless another output directory name is entered in the UI.
- Packaged runs write default outputs under `Documents\ClassInEDBMVP` unless another output directory name is entered in the UI.
- Uploaded files are cached in `.app_runtime\uploads` under the active app home.
- The browser UI talks to the local server over HTTP and does not call Python directly.
- Double-clicking the packaged app opens the browser automatically. If the app server is already running, it opens the browser instead of starting a duplicate server.
- API keys are entered in the app's `칠판 설정` panel and stored locally under the app runtime folder.
- Updates are semi-automatic: the app checks configured release metadata and opens the installer download page; it does not self-replace in the background.
- Use the top-bar power button to stop the local app server after use.
- The current `.edb` export is still the MVP image-based board export, not the final mixed text/image writer.

## Known Limits
- OCR quality depends on optional local OCR dependencies.
- Packaged builds still rely on Python-side native dependencies like Pillow, PyMuPDF, and OpenCV.
- HWP conversion can still depend on external converters such as LibreOffice, Chrome, `hwpilot`, or configured command-line tools depending on the input path.
- The UI is connected to the MVP pipeline, but it is not yet a full production desktop shell.
