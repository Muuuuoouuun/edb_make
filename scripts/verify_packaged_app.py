#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from pathlib import Path

try:
    from .verify_frontend_package import (
        REQUIRED_RUNTIME_SOURCE_FILES,
        REQUIRED_UI_FILES,
        SOURCE_DIGEST_RE,
        bundle_cache_bust_digest,
    )
except ImportError:  # pragma: no cover - direct script execution
    from verify_frontend_package import (
        REQUIRED_RUNTIME_SOURCE_FILES,
        REQUIRED_UI_FILES,
        SOURCE_DIGEST_RE,
        bundle_cache_bust_digest,
    )


REQUIRED_RUNTIME_FILES = (
    "app_update_config.json",
    *REQUIRED_RUNTIME_SOURCE_FILES,
)

FORBIDDEN_PACKAGED_FRONTEND_FILES = (
    "ui_prototype/app.js",
    "ui_prototype/prototype_data.js",
    "ui_prototype/generated_session.js",
    "ui_prototype/vendor/babel.min.js",
    "ui_prototype/vendor/babel.min.js.map",
)

FORBIDDEN_PACKAGED_BUILD_TOOL_PATHS = (
    "scripts/build_frontend_bundle.mjs",
    "scripts/vendor",
    "scripts/verify_frontend_package.py",
    "scripts/verify_packaged_app.py",
)

FORBIDDEN_PACKAGED_SOURCE_ASSET_FILES = (
    "assets/app_icon.svg",
    "assets/brand_mark.svg",
    "assets/app_icon.iconset",
)

FORBIDDEN_BOARD_TOKENS = (
    "app.js?v=",
    "prototype_data.js",
    "generated_session.js",
    "vendor/babel.min.js",
    'type="text/babel"',
)

FORBIDDEN_PACKAGED_RUNTIME_PATHS = (
    ".app_runtime",
    "uploads",
    "outputs",
    "publish_output",
    "mutated_crops",
    "exports",
    "ai_retries",
    "ai_image_reconstructions",
    "latest_session.json",
    "session_history.json",
    "generated_session.js",
    "app.log",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _resource_root_candidates(package_root: Path) -> list[Path]:
    explicit_candidates = (
        package_root,
        package_root / "_internal",
        package_root / "Contents" / "Resources",
        package_root / "Contents" / "Resources" / "_internal",
        package_root / "Contents" / "Frameworks",
        package_root / "Contents" / "Frameworks" / "_internal",
    )
    candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in explicit_candidates:
        board_path = candidate / "ui_prototype" / "board.html"
        if not board_path.is_file():
            continue
        resolved = board_path.resolve().parent.parent
        if resolved in seen:
            continue
        seen.add(resolved)
        candidates.append(resolved)
    if candidates:
        return candidates

    for board_path in package_root.rglob("board.html"):
        if board_path.parent.name != "ui_prototype":
            continue
        candidate = board_path.resolve().parent.parent
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def collect_package_errors(
    package_root: Path,
    *,
    expected_app_name: str = "",
    expected_version: str = "",
    expected_bundle_id: str = "",
) -> list[str]:
    root = package_root.resolve()
    errors: list[str] = []
    if not root.exists():
        return [f"package root does not exist: {package_root}"]
    if root.is_file():
        return [f"package root must be an inspectable directory, not a file: {package_root}"]

    resource_roots = _resource_root_candidates(root)
    if not resource_roots:
        return [f"could not locate packaged ui_prototype/board.html under: {package_root}"]
    if len(resource_roots) > 1:
        labels = ", ".join(str(path.relative_to(root)) for path in resource_roots)
        errors.append(f"multiple packaged frontend roots found: {labels}")

    packaged_version = ""
    resource_root = resource_roots[0]
    for rel_path in (*REQUIRED_UI_FILES, *REQUIRED_RUNTIME_FILES):
        if not (resource_root / rel_path).is_file():
            errors.append(f"missing packaged runtime file: {rel_path}")

    update_config_path = resource_root / "app_update_config.json"
    if update_config_path.is_file():
        try:
            update_config = _read_json(update_config_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"packaged app_update_config.json is not valid JSON: {exc}")
        else:
            if not isinstance(update_config, dict):
                errors.append("packaged app_update_config.json must contain a JSON object")
            else:
                app_name = str(update_config.get("appName") or "").strip()
                version = str(update_config.get("version") or "").strip()
                if not app_name:
                    errors.append("packaged app_update_config.json is missing appName")
                if not version:
                    errors.append("packaged app_update_config.json is missing version")
                else:
                    packaged_version = version
                if expected_app_name and app_name != expected_app_name:
                    errors.append(
                        "packaged app_update_config.json appName mismatch: "
                        f"expected {expected_app_name!r}, found {app_name!r}"
                    )
                if expected_version and version != expected_version:
                    errors.append(
                        "packaged app_update_config.json version mismatch: "
                        f"expected {expected_version!r}, found {version!r}"
                    )

    for rel_path in FORBIDDEN_PACKAGED_FRONTEND_FILES:
        if (resource_root / rel_path).exists():
            errors.append(f"forbidden packaged frontend file exists: {rel_path}")
    for rel_path in FORBIDDEN_PACKAGED_SOURCE_ASSET_FILES:
        if (resource_root / rel_path).exists():
            errors.append(f"forbidden packaged source asset exists: {rel_path}")

    runtime_scan_roots = [root]
    for candidate in resource_roots:
        if candidate not in runtime_scan_roots:
            runtime_scan_roots.append(candidate)
    for scan_root in runtime_scan_roots:
        for rel_path in FORBIDDEN_PACKAGED_BUILD_TOOL_PATHS:
            candidate = scan_root / rel_path
            if candidate.exists():
                errors.append(f"forbidden packaged build-time tool exists: {rel_path}")
        for rel_path in FORBIDDEN_PACKAGED_SOURCE_ASSET_FILES:
            candidate = scan_root / rel_path
            if candidate.exists():
                errors.append(f"forbidden packaged source asset exists: {rel_path}")
        for rel_path in FORBIDDEN_PACKAGED_RUNTIME_PATHS:
            candidate = scan_root / rel_path
            if candidate.exists():
                errors.append(f"forbidden packaged runtime artifact exists: {candidate.relative_to(scan_root)}")

    packaged_bundle_digest = None
    bundle_path = resource_root / "ui_prototype" / "app.bundle.js"
    if bundle_path.is_file():
        bundle = _read(bundle_path)
        if "Generated by scripts/build_frontend_bundle.mjs" not in bundle:
            errors.append("packaged app.bundle.js is missing the generated bundle banner")
        digest_match = SOURCE_DIGEST_RE.search(bundle)
        if digest_match is None:
            errors.append("packaged app.bundle.js is missing the source digest")
        else:
            packaged_bundle_digest = digest_match.group(1)
        if "/* app.jsx */" not in bundle:
            errors.append("packaged app.bundle.js does not include the app.jsx section")

    board_path = resource_root / "ui_prototype" / "board.html"
    if board_path.is_file():
        board_html = _read(board_path)
        script_srcs = re.findall(r"<script\s+src=\"([^\"]+)\"", board_html)
        bundle_srcs = [src for src in script_srcs if src.startswith("app.bundle.js?v=frontend-bundle-")]
        if len(bundle_srcs) != 1:
            errors.append("packaged board.html must load exactly one cache-busted app.bundle.js")
        elif (board_digest := bundle_cache_bust_digest(bundle_srcs[0])) is None:
            errors.append("packaged board.html app.bundle.js cache bust must use the bundle source digest")
        elif packaged_bundle_digest and board_digest != packaged_bundle_digest:
            errors.append("packaged board.html app.bundle.js cache bust does not match packaged app.bundle.js")
        for token in FORBIDDEN_BOARD_TOKENS:
            if token in board_html:
                errors.append(f"packaged board.html still references legacy runtime token: {token}")

    info_plist_path = root / "Contents" / "Info.plist"
    if root.suffix == ".app" or info_plist_path.exists():
        if not info_plist_path.is_file():
            errors.append("missing macOS app bundle Info.plist: Contents/Info.plist")
        else:
            try:
                info_plist = plistlib.loads(info_plist_path.read_bytes())
            except (OSError, plistlib.InvalidFileException) as exc:
                errors.append(f"macOS app bundle Info.plist is not valid: {exc}")
            else:
                if not isinstance(info_plist, dict):
                    errors.append("macOS app bundle Info.plist must contain a dictionary")
                else:
                    bundle_id = str(info_plist.get("CFBundleIdentifier") or "").strip()
                    if not bundle_id:
                        errors.append("macOS app bundle Info.plist is missing CFBundleIdentifier")
                    elif expected_bundle_id and bundle_id != expected_bundle_id:
                        errors.append(
                            "macOS app bundle Info.plist CFBundleIdentifier mismatch: "
                            f"expected {expected_bundle_id!r}, found {bundle_id!r}"
                        )
                    expected_bundle_version = expected_version or packaged_version
                    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
                        value = str(info_plist.get(key) or "").strip()
                        if not value:
                            errors.append(f"macOS app bundle Info.plist is missing {key}")
                        elif expected_bundle_version and value != expected_bundle_version:
                            errors.append(
                                f"macOS app bundle Info.plist {key} mismatch: "
                                f"expected {expected_bundle_version!r}, found {value!r}"
                            )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a built app package layout.")
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--expected-app-name", default="", help="Expected appName in packaged update metadata")
    parser.add_argument("--expected-version", default="", help="Expected version in packaged update metadata")
    parser.add_argument("--expected-bundle-id", default="", help="Expected macOS CFBundleIdentifier")
    args = parser.parse_args(argv)

    errors = collect_package_errors(
        args.package_root,
        expected_app_name=args.expected_app_name,
        expected_version=args.expected_version,
        expected_bundle_id=args.expected_bundle_id,
    )
    if errors:
        for error in errors:
            print(f"[packaged-app] ERROR: {error}", file=sys.stderr)
        return 1
    print(f"[packaged-app] OK: {args.package_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
