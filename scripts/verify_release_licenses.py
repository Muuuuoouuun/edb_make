#!/usr/bin/env python3
"""Fail closed when optional copyleft runtime assets are bundled.

The application can discover a separately installed Upscayl runtime, so release
packages do not need to redistribute it.  Bundling is an explicit opt-in and
requires the minimum compliance artifacts below.  Passing this check is not a
substitute for legal review; it only prevents an accidental, undocumented
binary/model bundle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .build_release_metadata import (
        collect_locked_environment_errors,
        collect_release_policy_errors,
    )
except ImportError:  # pragma: no cover - direct script execution
    from build_release_metadata import (
        collect_locked_environment_errors,
        collect_release_policy_errors,
    )


UPSCAYL_RESOURCE_PATH = Path("resources/upscayl")
UPSCAYL_REQUIRED_COMPLIANCE_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "CORRESPONDING_SOURCE.txt",
)
UPSCAYL_MODEL_NAME = "upscayl-lite-4x"


def current_upscayl_platform() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


def collect_release_license_errors(
    project_root: Path,
    *,
    bundle_upscayl: bool,
    platform_name: str | None = None,
    require_release_policy: bool = False,
    require_locked_environment: bool = False,
    reject_unlocked_environment: bool = False,
) -> list[str]:
    root = project_root.expanduser().resolve()
    upscayl_root = root / UPSCAYL_RESOURCE_PATH
    errors: list[str] = []

    if require_release_policy:
        errors.extend(collect_release_policy_errors(root))
    if require_locked_environment:
        errors.extend(
            collect_locked_environment_errors(root, reject_unlocked=reject_unlocked_environment)
        )

    if not bundle_upscayl:
        return errors
    if not upscayl_root.is_dir():
        return [f"requested Upscayl bundle directory does not exist: {upscayl_root}"]

    for relative_name in UPSCAYL_REQUIRED_COMPLIANCE_FILES:
        candidate = upscayl_root / relative_name
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            errors.append(
                "Upscayl bundling requires a non-empty compliance file: "
                f"{UPSCAYL_RESOURCE_PATH.as_posix()}/{relative_name}"
            )
    platform = platform_name or current_upscayl_platform()
    binary_name = "upscayl-bin.exe" if platform == "win" else "upscayl-bin"
    binary_path = upscayl_root / platform / "bin" / binary_name
    if not binary_path.is_file() or binary_path.stat().st_size <= 0:
        errors.append(f"Upscayl bundling requires the platform binary: {binary_path}")
    for suffix in ("bin", "param"):
        model_path = upscayl_root / "models" / f"{UPSCAYL_MODEL_NAME}.{suffix}"
        if not model_path.is_file() or model_path.stat().st_size <= 0:
            errors.append(f"Upscayl bundling requires the Lite model asset: {model_path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify optional release runtime license artifacts.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root")
    parser.add_argument(
        "--bundle-upscayl",
        action="store_true",
        help="Validate the resources/upscayl runtime for redistribution",
    )
    parser.add_argument(
        "--require-release-policy",
        action="store_true",
        help="Require the exact release lock, reviewed inventory, and notices",
    )
    parser.add_argument(
        "--require-locked-environment",
        action="store_true",
        help="Require installed active dependencies to match the exact release lock",
    )
    parser.add_argument(
        "--reject-unlocked-environment",
        action="store_true",
        help="Fail if any installed distribution is absent from the release lock",
    )
    args = parser.parse_args(argv)

    errors = collect_release_license_errors(
        args.root,
        bundle_upscayl=args.bundle_upscayl,
        require_release_policy=args.require_release_policy,
        require_locked_environment=args.require_locked_environment,
        reject_unlocked_environment=args.reject_unlocked_environment,
    )
    if errors:
        for error in errors:
            print(f"[release-license] ERROR: {error}")
        return 1
    mode = "bundled and compliance files present" if args.bundle_upscayl else "external discovery only"
    print(f"[release-license] OK: Upscayl {mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
