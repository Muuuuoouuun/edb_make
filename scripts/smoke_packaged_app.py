#!/usr/bin/env python3
"""Launch a packaged executable and verify real HTTP startup/diagnostics/shutdown."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_request(url: str, *, method: str = "GET", timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        method=method,
        headers={"Accept": "application/json", "Origin": url.split("/api/", 1)[0]},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return payload


def smoke_packaged_executable(
    executable: Path,
    *,
    startup_timeout: float = 45.0,
    shutdown_timeout: float = 15.0,
) -> dict[str, Any]:
    resolved_executable = executable.expanduser().resolve()
    if not resolved_executable.is_file():
        raise FileNotFoundError(f"packaged executable does not exist: {resolved_executable}")
    port = _free_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    command = [
        str(resolved_executable),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-open-browser",
    ]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="edb-packaged-smoke-") as raw_temp:
        temp_root = Path(raw_temp)
        log_path = temp_root / "packaged-app.log"
        environment = dict(os.environ)
        environment["EDB_APP_HOME"] = str(temp_root / "app-home")
        environment.pop("GEMINI_API_KEY", None)
        environment.pop("GOOGLE_API_KEY", None)
        environment.pop("OPENAI_API_KEY", None)
        with log_path.open("wb") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=temp_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            try:
                health: dict[str, Any] | None = None
                deadline = time.monotonic() + startup_timeout
                while time.monotonic() < deadline:
                    exit_code = process.poll()
                    if exit_code is not None:
                        raise RuntimeError(
                            f"packaged app exited before health check with code {exit_code}"
                        )
                    try:
                        health = _json_request(f"{base_url}/api/health")
                    except (OSError, ValueError, urllib.error.URLError, RuntimeError):
                        time.sleep(0.2)
                        continue
                    break
                if health is None:
                    raise RuntimeError(f"packaged app did not become healthy within {startup_timeout:.1f}s")
                if health.get("ok") is not True or health.get("app") != "ClassIn EDB MVP Local App":
                    raise RuntimeError(f"unexpected packaged health payload: {health}")

                diagnostics = _json_request(f"{base_url}/api/runtime-diagnostics")
                if diagnostics.get("ok") is not True:
                    raise RuntimeError(f"runtime diagnostics did not report ok=true: {diagnostics}")
                with urllib.request.urlopen(f"{base_url}/", timeout=3.0) as response:
                    ui_bytes = response.read()
                if response.status != 200 or b"<!doctype html" not in ui_bytes[:512].lower():
                    raise RuntimeError("packaged UI root did not return the expected HTML shell")

                shutdown = _json_request(f"{base_url}/api/system/shutdown", method="POST")
                if shutdown.get("ok") is not True:
                    raise RuntimeError(f"shutdown endpoint did not report ok=true: {shutdown}")
                try:
                    exit_code = process.wait(timeout=shutdown_timeout)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        f"packaged app did not stop within {shutdown_timeout:.1f}s"
                    ) from exc
                if exit_code != 0:
                    raise RuntimeError(f"packaged app stopped with non-zero code {exit_code}")
                return {
                    "executable": str(resolved_executable),
                    "startupSeconds": round(time.monotonic() - started, 3),
                    "health": health,
                    "diagnosticsStatus": "ok",
                    "cleanShutdown": True,
                }
            except Exception as exc:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                log_handle.flush()
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise RuntimeError(f"{exc}\n--- packaged app log tail ---\n{log_tail}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a packaged ClassInEDBMVP executable.")
    parser.add_argument("executable", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    parser.add_argument("--shutdown-timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        result = smoke_packaged_executable(
            args.executable,
            startup_timeout=args.startup_timeout,
            shutdown_timeout=args.shutdown_timeout,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[packaged-smoke] ERROR: {exc}", file=sys.stderr)
        return 1
    print("[packaged-smoke] OK: " + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
