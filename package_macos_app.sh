#!/bin/zsh
set -euo pipefail

APP_NAME="ClassInEDBMVP"
OUTPUT_DIR="dist"
CLEAN=0
ZIP=0
DMG=0
CONSOLE=0
INSTALL_PYINSTALLER=0
SKIP_FRONTEND_BUILD=0
PYTHON_EXE=""

usage() {
  cat <<'EOF'
Usage: ./package_macos_app.sh [options]

Options:
  --name NAME              App bundle name. Default: ClassInEDBMVP
  --output-dir DIR         Output directory. Default: dist
  --python PATH            Python executable to use. Default: .venv/bin/python or python3
  --clean                  Remove previous build output first
  --zip                    Create a zip archive next to the app bundle
  --dmg                    Create a drag-and-drop DMG installer
  --console                Keep a console build for debugging
  --install-pyinstaller    Install PyInstaller before packaging
  --skip-frontend-build     Use existing ui_prototype/app.bundle.js
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      APP_NAME="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_EXE="${2:-}"
      shift 2
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    --zip)
      ZIP=1
      shift
      ;;
    --dmg)
      DMG=1
      shift
      ;;
    --console)
      CONSOLE=1
      shift
      ;;
    --install-pyinstaller)
      INSTALL_PYINSTALLER=1
      shift
      ;;
    --skip-frontend-build)
      SKIP_FRONTEND_BUILD=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

if [[ -z "$PYTHON_EXE" ]]; then
  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    PYTHON_EXE="$PROJECT_ROOT/.venv/bin/python"
  else
    PYTHON_EXE="$(command -v python3)"
  fi
fi

RESOLVED_OUTPUT_DIR="$OUTPUT_DIR"
if [[ "$RESOLVED_OUTPUT_DIR" != /* ]]; then
  RESOLVED_OUTPUT_DIR="$PROJECT_ROOT/$RESOLVED_OUTPUT_DIR"
fi

if [[ "$CLEAN" == "1" ]]; then
  rm -rf "$RESOLVED_OUTPUT_DIR" build
fi
mkdir -p "$RESOLVED_OUTPUT_DIR"
SPEC_DIR="$RESOLVED_OUTPUT_DIR/_pyinstaller_spec"

if [[ "$INSTALL_PYINSTALLER" == "1" ]]; then
  "$PYTHON_EXE" -m pip install pyinstaller
fi

FRONTEND_BUNDLE="$PROJECT_ROOT/ui_prototype/app.bundle.js"
if [[ "$SKIP_FRONTEND_BUILD" == "0" ]]; then
  if command -v node >/dev/null 2>&1; then
    node "$PROJECT_ROOT/scripts/build_frontend_bundle.mjs"
  elif [[ ! -f "$FRONTEND_BUNDLE" ]]; then
    echo "Node.js is required to build ui_prototype/app.bundle.js. Install Node or pass --skip-frontend-build after creating the bundle." >&2
    exit 1
  else
    echo "Node.js was not found; using existing ui_prototype/app.bundle.js." >&2
  fi
fi

if ! "$PYTHON_EXE" -m PyInstaller --version >/dev/null 2>&1; then
  echo "PyInstaller가 설치되어 있지 않습니다. --install-pyinstaller 옵션으로 다시 실행하세요." >&2
  exit 1
fi

WINDOW_ARG="--windowed"
if [[ "$CONSOLE" == "1" ]]; then
  WINDOW_ARG="--console"
fi

ICON_ARGS=()
if [[ -f "$PROJECT_ROOT/assets/app_icon.icns" ]]; then
  ICON_ARGS=(--icon "$PROJECT_ROOT/assets/app_icon.icns")
fi

DATA_ARGS=()
add_data() {
  local src="$1"
  local dest="$2"
  if [[ -e "$PROJECT_ROOT/$src" ]]; then
    DATA_ARGS+=(--add-data "$PROJECT_ROOT/$src:$dest")
  fi
}

add_data "ui_prototype/index.html" "ui_prototype"
add_data "ui_prototype/board.html" "ui_prototype"
add_data "ui_prototype/reorder.js" "ui_prototype"
add_data "ui_prototype/review_filters.js" "ui_prototype"
add_data "ui_prototype/publish_summary.js" "ui_prototype"
add_data "ui_prototype/publish_guard.js" "ui_prototype"
add_data "ui_prototype/app.bundle.js" "ui_prototype"
add_data "ui_prototype/vendor/react.production.min.js" "ui_prototype/vendor"
add_data "ui_prototype/vendor/react-dom.production.min.js" "ui_prototype/vendor"
add_data "assets/app_icon.ico" "assets"
add_data "assets/app_icon.icns" "assets"
add_data "assets/app_icon.png" "assets"

"$PYTHON_EXE" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  "$WINDOW_ARG" \
  --distpath "$RESOLVED_OUTPUT_DIR" \
  --specpath "$SPEC_DIR" \
  --name "$APP_NAME" \
  "${DATA_ARGS[@]}" \
  "${ICON_ARGS[@]}" \
  app_server.py

APP_PATH="$RESOLVED_OUTPUT_DIR/$APP_NAME.app"
if [[ -d "$APP_PATH" ]] && command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true
fi

if [[ "$ZIP" == "1" && -d "$APP_PATH" ]]; then
  (cd "$RESOLVED_OUTPUT_DIR" && /usr/bin/ditto -c -k --keepParent "$APP_NAME.app" "$APP_NAME-macOS.zip")
fi

if [[ "$DMG" == "1" && -d "$APP_PATH" ]]; then
  DMG_PATH="$RESOLVED_OUTPUT_DIR/$APP_NAME-macOS.dmg"
  STAGING_DIR="$(mktemp -d "$RESOLVED_OUTPUT_DIR/${APP_NAME}.dmg.XXXXXX")"
  rm -f "$DMG_PATH"
  /usr/bin/ditto "$APP_PATH" "$STAGING_DIR/$APP_NAME.app"
  ln -s /Applications "$STAGING_DIR/Applications"
  hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDZO \
    "$DMG_PATH" >/dev/null
  rm -rf "$STAGING_DIR"
fi

echo "Packaging complete."
if [[ -d "$APP_PATH" ]]; then
  echo "App bundle: $APP_PATH"
  if [[ "$ZIP" == "1" ]]; then
    echo "Zip archive: $RESOLVED_OUTPUT_DIR/$APP_NAME-macOS.zip"
  fi
  if [[ "$DMG" == "1" ]]; then
    echo "DMG installer: $RESOLVED_OUTPUT_DIR/$APP_NAME-macOS.dmg"
  fi
else
  echo "Output folder: $RESOLVED_OUTPUT_DIR"
fi
