#!/usr/bin/env python3
"""Validate cross-platform release inputs before expensive installer jobs start."""

from __future__ import annotations

import argparse
import re

try:
    from .build_update_feed import validate_download_url_filename, validate_optional_update_url
except ImportError:  # pragma: no cover - direct script execution
    from build_update_feed import validate_download_url_filename, validate_optional_update_url


APPLE_WINDOWS_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RELEASE_MODES = ("internal-test", "public")


def collect_release_input_errors(
    *,
    version: str,
    release_mode: str,
    update_feed_url: str = "",
    release_notes_url: str = "",
    manifest_url: str = "",
    macos_download_url: str = "",
    windows_download_url: str = "",
    license_compliance_approved: bool = False,
) -> list[str]:
    errors: list[str] = []
    normalized_version = str(version or "").strip()
    normalized_mode = str(release_mode or "").strip()
    if not APPLE_WINDOWS_VERSION_RE.fullmatch(normalized_version):
        errors.append("version must contain exactly three numeric components, for example 1.3.0")
    if normalized_mode not in RELEASE_MODES:
        errors.append(f"release mode must be one of: {', '.join(RELEASE_MODES)}")

    urls = (
        ("update feed URL", update_feed_url),
        ("release notes URL", release_notes_url),
        ("manifest URL", manifest_url),
        ("macOS download URL", macos_download_url),
        ("Windows download URL", windows_download_url),
    )
    for label, value in urls:
        try:
            validate_optional_update_url(label, value)
        except ValueError as exc:
            errors.append(str(exc))

    has_macos_url = bool(str(macos_download_url or "").strip())
    has_windows_url = bool(str(windows_download_url or "").strip())
    if has_macos_url != has_windows_url:
        errors.append("macOS and Windows download URLs must be supplied together")
    if has_macos_url:
        for platform, value, artifact_type in (
            ("macos", macos_download_url, "dmg"),
            ("windows", windows_download_url, "setup-exe"),
        ):
            try:
                validate_download_url_filename(platform, value, artifact_type)
            except ValueError as exc:
                errors.append(str(exc))

    if normalized_mode == "public":
        if not license_compliance_approved:
            errors.append(
                "production release requires explicit license compliance approval for bundled dependencies"
            )
        if not str(update_feed_url or "").strip():
            errors.append("public release requires an update feed URL")
        if not str(release_notes_url or "").strip():
            errors.append("public release requires a release notes URL")
        if not str(manifest_url or "").strip():
            errors.append("public release requires a release manifest URL")
        if not (has_macos_url and has_windows_url):
            errors.append("public release requires both platform download URLs")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate installer workflow release inputs.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-mode", choices=RELEASE_MODES, default="internal-test")
    parser.add_argument("--update-feed-url", default="")
    parser.add_argument("--release-notes-url", default="")
    parser.add_argument("--manifest-url", default="")
    parser.add_argument("--macos-download-url", default="")
    parser.add_argument("--windows-download-url", default="")
    parser.add_argument("--license-compliance-approved", action="store_true")
    args = parser.parse_args(argv)

    errors = collect_release_input_errors(
        version=args.version,
        release_mode=args.release_mode,
        update_feed_url=args.update_feed_url,
        release_notes_url=args.release_notes_url,
        manifest_url=args.manifest_url,
        macos_download_url=args.macos_download_url,
        windows_download_url=args.windows_download_url,
        license_compliance_approved=args.license_compliance_approved,
    )
    if errors:
        for error in errors:
            print(f"[release-input] ERROR: {error}")
        return 1
    print(f"[release-input] OK: {args.release_mode} {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
