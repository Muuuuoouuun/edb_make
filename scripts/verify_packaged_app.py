#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from .verify_frontend_package import REQUIRED_UI_FILES, SOURCE_DIGEST_RE
except ImportError:  # pragma: no cover - direct script execution
    from verify_frontend_package import REQUIRED_UI_FILES, SOURCE_DIGEST_RE


REQUIRED_RUNTIME_FILES = (
    "app_update_config.json",
    "scripts/render_hwp_with_rhwp_core.mjs",
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


def collect_package_errors(package_root: Path) -> list[str]:
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

    resource_root = resource_roots[0]
    for rel_path in (*REQUIRED_UI_FILES, *REQUIRED_RUNTIME_FILES):
        if not (resource_root / rel_path).is_file():
            errors.append(f"missing packaged runtime file: {rel_path}")

    for rel_path in FORBIDDEN_PACKAGED_FRONTEND_FILES:
        if (resource_root / rel_path).exists():
            errors.append(f"forbidden packaged frontend file exists: {rel_path}")

    runtime_scan_roots = [root]
    for candidate in resource_roots:
        if candidate not in runtime_scan_roots:
            runtime_scan_roots.append(candidate)
    for scan_root in runtime_scan_roots:
        for rel_path in FORBIDDEN_PACKAGED_BUILD_TOOL_PATHS:
            candidate = scan_root / rel_path
            if candidate.exists():
                errors.append(f"forbidden packaged build-time tool exists: {rel_path}")
        for rel_path in FORBIDDEN_PACKAGED_RUNTIME_PATHS:
            candidate = scan_root / rel_path
            if candidate.exists():
                errors.append(f"forbidden packaged runtime artifact exists: {candidate.relative_to(scan_root)}")

    board_path = resource_root / "ui_prototype" / "board.html"
    if board_path.is_file():
        board_html = _read(board_path)
        script_srcs = re.findall(r"<script\s+src=\"([^\"]+)\"", board_html)
        bundle_srcs = [src for src in script_srcs if src.startswith("app.bundle.js?v=frontend-bundle-")]
        if len(bundle_srcs) != 1:
            errors.append("packaged board.html must load exactly one cache-busted app.bundle.js")
        for token in FORBIDDEN_BOARD_TOKENS:
            if token in board_html:
                errors.append(f"packaged board.html still references legacy runtime token: {token}")

    bundle_path = resource_root / "ui_prototype" / "app.bundle.js"
    if bundle_path.is_file():
        bundle = _read(bundle_path)
        if "Generated by scripts/build_frontend_bundle.mjs" not in bundle:
            errors.append("packaged app.bundle.js is missing the generated bundle banner")
        if SOURCE_DIGEST_RE.search(bundle) is None:
            errors.append("packaged app.bundle.js is missing the source digest")
        if "/* app.jsx */" not in bundle:
            errors.append("packaged app.bundle.js does not include the app.jsx section")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a built app package layout.")
    parser.add_argument("package_root", type=Path)
    args = parser.parse_args(argv)

    errors = collect_package_errors(args.package_root)
    if errors:
        for error in errors:
            print(f"[packaged-app] ERROR: {error}", file=sys.stderr)
        return 1
    print(f"[packaged-app] OK: {args.package_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
