# MVP Packaging Guide

## What This Delivers
- Local HTTP app server for the ClassIn EDB MVP
- Browser UI connected to the real export pipeline
- One-click-ish local launch via PowerShell
- First-pass desktop packaging flow with PyInstaller

## Main Entry Points
- Local app server: `app_server.py`
- Local launcher: `run_local_app.ps1`
- Packaging script: `package_mvp.ps1`

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
Install PyInstaller if needed:
```powershell
.\package_mvp.ps1 -InstallPyInstaller
```

Or package directly if PyInstaller is already installed:
```powershell
.\package_mvp.ps1
```

Useful options:
```powershell
.\package_mvp.ps1 -OutputDir .\dist_smoke -Clean -Zip
```

Expected output:
```text
dist\ClassInEDBMVP\
```

Typical packaged launch target:
```text
dist\ClassInEDBMVP\ClassInEDBMVP.exe
```

If PyInstaller is not installed, the script falls back to a source bundle:
```text
dist\source-package\
```

## Included Runtime Assets
- `ui_prototype\index.html`
- `ui_prototype\app.js`
- `ui_prototype\styles.css`
- `ui_prototype\generated_session.js` if present at build time

## Notes
- Export outputs are written into the project folder unless another output directory name is entered in the UI.
- Uploaded files are cached in `.app_runtime\uploads`.
- The browser UI talks to the local server over HTTP and does not call Python directly.
- The current `.edb` export is still the MVP image-based board export, not the final mixed text/image writer.
- `package_mvp.ps1` can create either a PyInstaller onedir build or a source-package fallback.

## Known Limits
- OCR quality depends on optional local OCR dependencies.
- Packaged builds still rely on Python-side native dependencies like Pillow, PyMuPDF, and OpenCV.
- The UI is connected to the MVP pipeline, but it is not yet a full production desktop shell.
