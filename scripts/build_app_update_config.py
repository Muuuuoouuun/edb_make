#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, str] = {
    "appId": "ClassInEDBMVP",
    "appName": "ClassInEDBMVP",
    "version": "0.1.0",
    "updateFeedUrl": "",
    "downloadUrl": "",
    "releaseNotesUrl": "",
}

ALIAS_GROUPS = (
    ("appId", ("appId", "app_id")),
    ("appName", ("appName", "app_name")),
    ("updateFeedUrl", ("updateFeedUrl", "update_feed_url")),
    ("downloadUrl", ("downloadUrl", "download_url")),
    ("releaseNotesUrl", ("releaseNotesUrl", "release_notes_url")),
)

ENV_OVERRIDES = {
    "appId": "EDB_PACKAGE_APP_ID",
    "appName": "EDB_PACKAGE_APP_NAME",
    "version": "EDB_PACKAGE_APP_VERSION",
    "updateFeedUrl": "EDB_PACKAGE_UPDATE_FEED_URL",
    "downloadUrl": "EDB_PACKAGE_DOWNLOAD_URL",
    "releaseNotesUrl": "EDB_PACKAGE_RELEASE_NOTES_URL",
}


def first_nonempty(payload: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(payload.get(name) or "").strip()
        if value:
            return value
    return ""


def normalize_update_config(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {key: value for key, value in payload.items() if value is not None}
    for canonical, aliases in ALIAS_GROUPS:
        values = {
            alias: str(payload.get(alias) or "").strip()
            for alias in aliases
            if str(payload.get(alias) or "").strip()
        }
        if len(set(values.values())) > 1:
            details = ", ".join(f"{alias}={value!r}" for alias, value in values.items())
            raise ValueError(f"app_update_config.json {canonical} aliases conflict: {details}")
        value = first_nonempty(payload, aliases)
        for alias in aliases:
            if alias != canonical:
                normalized.pop(alias, None)
        if value:
            normalized[canonical] = value
    return normalized


def read_source_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        print(f"[app-update-config] ignoring invalid JSON in {path}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(payload, dict):
        print(f"[app-update-config] ignoring non-object JSON in {path}", file=sys.stderr)
        return {}
    return normalize_update_config(payload)


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, str]:
    environ = os.environ if environ is None else environ
    return {
        key: value
        for key, env_name in ENV_OVERRIDES.items()
        if (value := str(environ.get(env_name) or "").strip())
    }


def build_config(source_path: Path, environ: dict[str, str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = dict(DEFAULT_CONFIG)
    config.update(read_source_config(source_path))
    config.update(env_overrides(environ))
    return config


def write_config(output_path: Path, config: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canonical app_update_config.json metadata.")
    parser.add_argument("source", type=Path, help="Project app_update_config.json path")
    parser.add_argument("output", type=Path, help="Generated app_update_config.json path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        write_config(args.output, build_config(args.source))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
