#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_TYPE_SUFFIXES = {
    "dmg": (".dmg",),
    "setup-exe": (".exe",),
    "zip": (".zip",),
}


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_artifact_type_filename(artifact: Path, artifact_type: str) -> None:
    expected_suffixes = ARTIFACT_TYPE_SUFFIXES.get(str(artifact_type or "").strip().lower())
    if expected_suffixes and artifact.suffix.lower() not in expected_suffixes:
        expected = ", ".join(expected_suffixes)
        raise ValueError(
            f"{artifact_type} artifact must use expected file extension ({expected}): {artifact.name}"
        )


def artifact_metadata(path: str | None, *, artifact_type: str = "") -> dict[str, Any]:
    if not path:
        return {}
    artifact = Path(path).expanduser().resolve()
    if not artifact.exists():
        raise FileNotFoundError(f"artifact not found: {artifact}")
    if not artifact.is_file():
        raise ValueError(f"artifact path is not a file: {artifact}")
    if artifact.stat().st_size <= 0:
        raise ValueError(f"artifact file is empty: {artifact}")
    validate_artifact_type_filename(artifact, artifact_type)
    return {
        "fileName": artifact.name,
        "sizeBytes": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
    }


def validate_sha256(label: str, value: str) -> str:
    digest = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} must be a 64-character lowercase hex sha256")
    return digest


def validate_version(version: str) -> str:
    normalized = str(version or "").strip()
    if not normalized:
        raise ValueError("release version must not be empty")
    return normalized


def is_loopback_hostname(hostname: str | None) -> bool:
    host = (hostname or "").strip().lower()
    return host == "localhost" or host == "::1" or host.startswith("127.")


def validate_update_url(label: str, value: str) -> str:
    url = str(value or "").strip()
    if not url:
        raise ValueError(f"{label} must not be empty")
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"{label} must be an absolute URL")
    if parsed.scheme == "https" or (parsed.scheme == "http" and is_loopback_hostname(parsed.hostname)):
        return url
    raise ValueError(f"{label} must use https or loopback http")


def validate_distinct_artifact_files(platform_paths: dict[str, str]) -> None:
    seen: dict[Path, str] = {}
    for platform, raw_path in platform_paths.items():
        if not raw_path:
            continue
        artifact = Path(raw_path).expanduser().resolve()
        previous_platform = seen.get(artifact)
        if previous_platform:
            raise ValueError(
                f"{platform} and {previous_platform} artifacts point to the same file: {artifact}"
            )
        seen[artifact] = platform


def platform_payload(
    *,
    version: str,
    platform: str,
    download_url: str,
    artifact_path: str | None,
    release_notes_url: str,
    artifact_type: str,
    arch: str,
) -> dict[str, Any] | None:
    if not download_url and not artifact_path:
        return None
    if artifact_path and not download_url:
        raise ValueError(f"{platform} artifact requires a download URL")
    safe_download_url = validate_update_url(f"{platform} download URL", download_url) if download_url else ""
    safe_release_notes_url = (
        validate_update_url("release notes URL", release_notes_url) if release_notes_url else ""
    )
    payload: dict[str, Any] = {
        "version": version,
        "artifactType": artifact_type,
        "arch": arch,
    }
    if safe_download_url:
        payload["downloadUrl"] = safe_download_url
    if safe_release_notes_url:
        payload["releaseNotesUrl"] = safe_release_notes_url
    payload.update(artifact_metadata(artifact_path, artifact_type=artifact_type))
    if "fileName" not in payload and safe_download_url:
        payload["fileName"] = Path(safe_download_url.rstrip("/")).name or f"{platform}-{version}"
    return payload


def build_feed(args: argparse.Namespace) -> dict[str, Any]:
    validate_distinct_artifact_files({
        "macos": args.macos_file,
        "windows": args.windows_file,
    })
    platforms: dict[str, Any] = {}
    macos = platform_payload(
        version=args.version,
        platform="macos",
        download_url=args.macos_url,
        artifact_path=args.macos_file,
        release_notes_url=args.release_notes_url,
        artifact_type=args.macos_artifact_type,
        arch=args.macos_arch,
    )
    windows = platform_payload(
        version=args.version,
        platform="windows",
        download_url=args.windows_url,
        artifact_path=args.windows_file,
        release_notes_url=args.release_notes_url,
        artifact_type=args.windows_artifact_type,
        arch=args.windows_arch,
    )
    if macos:
        platforms["macos"] = macos
    if windows:
        platforms["windows"] = windows
    if not platforms:
        raise ValueError("at least one platform URL or artifact file is required")

    feed: dict[str, Any] = {
        "schemaVersion": 1,
        "appId": args.app_id,
        "appName": args.app_name,
        "channel": args.channel,
        "version": args.version,
        "publishedAt": utc_timestamp(),
        "platforms": platforms,
    }
    if args.summary:
        feed["summary"] = args.summary
    if args.release_notes_url:
        feed["releaseNotesUrl"] = args.release_notes_url
    if args.manifest_url:
        feed["manifestUrl"] = args.manifest_url
    if args.manifest_sha256:
        feed["manifestSha256"] = args.manifest_sha256
    return feed


def build_manifest(feed: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    artifacts = []
    platforms = feed.get("platforms")
    if isinstance(platforms, dict):
        for platform, payload in platforms.items():
            if not isinstance(payload, dict):
                continue
            artifacts.append({
                "platform": platform,
                "version": payload.get("version") or feed.get("version"),
                "downloadUrl": payload.get("downloadUrl", ""),
                "fileName": payload.get("fileName", ""),
                "artifactType": payload.get("artifactType", ""),
                "arch": payload.get("arch", ""),
                "sizeBytes": payload.get("sizeBytes"),
                "sha256": payload.get("sha256", ""),
            })

    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "appId": feed["appId"],
        "appName": feed["appName"],
        "channel": feed["channel"],
        "version": feed["version"],
        "publishedAt": feed["publishedAt"],
        "artifacts": artifacts,
    }
    if args.summary:
        manifest["summary"] = args.summary
    if args.release_notes_url:
        manifest["releaseNotesUrl"] = args.release_notes_url
    if args.update_feed_url:
        manifest["updateFeedUrl"] = args.update_feed_url
    if args.git_commit:
        manifest["gitCommit"] = args.git_commit
    if args.build_number:
        manifest["buildNumber"] = args.build_number
    return manifest


def json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json(path: str | None, payload: dict[str, Any]) -> str:
    if not path:
        return ""
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    data = json_bytes(payload)
    output.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_checksums(path: str | None, feed: dict[str, Any], manifest_path: str | None = None) -> None:
    if not path:
        return
    lines: list[str] = []
    platforms = feed.get("platforms")
    if isinstance(platforms, dict):
        for payload in platforms.values():
            if not isinstance(payload, dict):
                continue
            digest = str(payload.get("sha256") or "").strip()
            file_name = str(payload.get("fileName") or "").strip()
            if digest and file_name:
                lines.append(f"{digest}  {file_name}")
    if manifest_path:
        manifest = Path(manifest_path).expanduser()
        if manifest.exists():
            lines.append(f"{sha256_file(manifest)}  {manifest.name}")
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a ClassInEDBMVP update feed JSON file.",
    )
    parser.add_argument("--version", required=True, help="Release version, for example 0.1.1")
    parser.add_argument("--output", default="dist/update.json", help="Output JSON path")
    parser.add_argument("--app-id", default="ClassInEDBMVP", help="Stable application id in update metadata")
    parser.add_argument("--app-name", default="ClassInEDBMVP", help="Display application name in update metadata")
    parser.add_argument("--channel", default="stable", help="Release channel, for example stable or beta")
    parser.add_argument("--summary", default="", help="Short release summary")
    parser.add_argument("--release-notes-url", default="", help="Release notes URL")
    parser.add_argument("--update-feed-url", default="", help="Public URL where the generated update feed will be hosted")
    parser.add_argument("--manifest-url", default="", help="Public release manifest URL")
    parser.add_argument("--manifest-sha256", default="", help="Precomputed release manifest sha256")
    parser.add_argument("--manifest-output", default="", help="Optional release manifest JSON output path")
    parser.add_argument("--checksums-output", default="", help="Optional checksums.txt output path")
    parser.add_argument("--git-commit", default="", help="Optional source commit recorded in the release manifest")
    parser.add_argument("--build-number", default="", help="Optional CI build number recorded in the release manifest")
    parser.add_argument("--macos-url", default="", help="Public macOS DMG download URL")
    parser.add_argument("--macos-file", default="", help="Local macOS DMG path for sha256/size metadata")
    parser.add_argument("--macos-artifact-type", default="dmg", help="macOS artifact type")
    parser.add_argument("--macos-arch", default="arm64", help="macOS artifact architecture")
    parser.add_argument("--windows-url", default="", help="Public Windows Setup.exe download URL")
    parser.add_argument("--windows-file", default="", help="Local Windows Setup.exe path for sha256/size metadata")
    parser.add_argument("--windows-artifact-type", default="setup-exe", help="Windows artifact type")
    parser.add_argument("--windows-arch", default="x64", help="Windows artifact architecture")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        args.version = validate_version(args.version)
        expected_manifest_sha256 = ""
        if args.manifest_sha256:
            expected_manifest_sha256 = validate_sha256("manifestSha256", args.manifest_sha256)
            args.manifest_sha256 = expected_manifest_sha256

        feed = build_feed(args)
        if args.manifest_output:
            manifest = build_manifest(feed, args)
            manifest_sha256 = write_json(args.manifest_output, manifest)
            if expected_manifest_sha256 and expected_manifest_sha256 != manifest_sha256:
                raise ValueError(
                    "manifestSha256 does not match generated release manifest: "
                    f"expected {expected_manifest_sha256}, generated {manifest_sha256}"
                )
            if args.manifest_url:
                feed["manifestUrl"] = args.manifest_url
            feed["manifestSha256"] = expected_manifest_sha256 or manifest_sha256
        write_json(args.output, feed)
        write_checksums(args.checksums_output, feed, args.manifest_output)
        print(f"Wrote update feed: {Path(args.output).expanduser()}")
        if args.manifest_output:
            print(f"Wrote release manifest: {Path(args.manifest_output).expanduser()}")
        if args.checksums_output:
            print(f"Wrote checksums: {Path(args.checksums_output).expanduser()}")
        return 0
    except (FileNotFoundError, ValueError) as exc:
        print(f"[update-feed] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
