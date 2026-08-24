#!/bin/zsh
set -euo pipefail

MODE="${1:-run}"
APP_NAME="ClassInEDBMVP"
BUNDLE_ID="local.classin.edbmvp"
ROOT_DIR="$(cd "$(dirname "${0:A}")/.." && pwd)"
APP_BUNDLE="$ROOT_DIR/dist/$APP_NAME.app"
APP_BINARY="$APP_BUNDLE/Contents/MacOS/$APP_NAME"
APP_URL="http://127.0.0.1:8765"
APP_HOME="${EDB_APP_HOME:-$HOME/Documents/ClassInEDBMVP}"
PACKAGE_VENV="$ROOT_DIR/build/package-venv"
PACKAGE_PYTHON="$PACKAGE_VENV/bin/python"
BASE_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$BASE_PYTHON" ]]; then
  BASE_PYTHON="$(command -v python3)"
fi

if [[ ! -x "$PACKAGE_PYTHON" ]]; then
  mkdir -p "${PACKAGE_VENV:h}"
  "$BASE_PYTHON" -m venv "$PACKAGE_VENV"
fi

pkill -x "$APP_NAME" >/dev/null 2>&1 || true
"$ROOT_DIR/package_macos_app.sh" \
  --python "$PACKAGE_PYTHON" \
  --install-pyinstaller \
  --clean

launch_app() {
  # Launch Services can block a newly packaged app while macOS asks for
  # Documents-folder access, leaving a visible process with no HTTP server.
  # Running from this trusted terminal also preserves EDB_APP_HOME.  The run
  # mode waits on this child below, so it remains alive for the whole task.
  EDB_APP_HOME="$APP_HOME" "$APP_BINARY" --no-open-browser &
  APP_PROCESS_ID=$!
}

wait_for_server() {
  for _attempt in {1..40}; do
    if /usr/bin/curl --fail --silent --max-time 1 "$APP_URL/api/health" >/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

start_app() {
  launch_app
  if ! wait_for_server; then
    echo "$APP_NAME did not become healthy at $APP_URL" >&2
    return 1
  fi
}

case "$MODE" in
  run)
    start_app
    /usr/bin/open "$APP_URL/"
    echo "$APP_NAME is running at $APP_URL/ (PID $APP_PROCESS_ID)"
    trap 'kill "$APP_PROCESS_ID" >/dev/null 2>&1 || true' EXIT INT TERM
    wait "$APP_PROCESS_ID"
    ;;
  --debug|debug)
    EDB_APP_HOME="$APP_HOME" lldb -- "$APP_BINARY" --no-open-browser
    ;;
  --logs|logs)
    start_app
    trap 'kill "$APP_PROCESS_ID" >/dev/null 2>&1 || true' EXIT INT TERM
    /usr/bin/open "$APP_URL/"
    /usr/bin/tail -F "$APP_HOME/.app_runtime/app.log"
    ;;
  --telemetry|telemetry)
    start_app
    trap 'kill "$APP_PROCESS_ID" >/dev/null 2>&1 || true' EXIT INT TERM
    /usr/bin/open "$APP_URL/"
    /usr/bin/log stream --info --style compact --predicate "subsystem == \"$BUNDLE_ID\""
    ;;
  --verify|verify)
    start_app
    kill "$APP_PROCESS_ID" >/dev/null 2>&1 || true
    wait "$APP_PROCESS_ID" 2>/dev/null || true
    ;;
  *)
    echo "usage: $0 [run|--debug|--logs|--telemetry|--verify]" >&2
    exit 2
    ;;
esac
