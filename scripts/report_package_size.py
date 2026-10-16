#!/usr/bin/env python3
"""Measure a packaged app without following bundle symlinks or double-counting hardlinks."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import stat


def build_size_report(root: Path) -> dict:
    root = root.absolute()
    if root.is_symlink() or not root.exists():
        raise ValueError(f"Package must be a real file or directory: {root}")
    components: dict[str, int] = defaultdict(int)
    largest: list[dict] = []
    seen: set[tuple[int, int]] = set()
    symlinks = 0
    hardlinks = 0
    file_count = 0
    total = 0
    entries = [root]
    while entries:
        path = entries.pop()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            symlinks += 1
            continue
        if stat.S_ISDIR(info.st_mode):
            entries.extend(sorted(path.iterdir(), reverse=True))
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        identity = (info.st_dev, info.st_ino)
        if identity in seen:
            hardlinks += 1
            continue
        seen.add(identity)
        relative = path.relative_to(root) if path != root else Path(root.name)
        parts = list(relative.parts)
        if parts[:1] == ["Contents"] and len(parts) > 2:
            parts = parts[2:]
        elif parts[:1] == ["_internal"] and len(parts) > 1:
            parts = parts[1:]
        component = parts[0] if len(parts) > 1 else "(root files)"
        components[component] += info.st_size
        total += info.st_size
        file_count += 1
        largest.append({"path": relative.as_posix(), "sizeBytes": info.st_size})
    return {
        "schemaVersion": 1,
        "packageName": root.name,
        "sizeBytes": total,
        "fileCount": file_count,
        "symlinkCount": symlinks,
        "duplicateHardlinkCount": hardlinks,
        "components": [
            {"name": name, "sizeBytes": size}
            for name, size in sorted(components.items(), key=lambda item: (-item[1], item[0]))
        ],
        "largestFiles": sorted(largest, key=lambda item: (-item["sizeBytes"], item["path"]))[:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, help="Optional limit for installed payload size")
    args = parser.parse_args(argv)
    if args.max_bytes is not None and args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    # Avoid feeding a previous report into its own package measurement.
    if args.output.resolve().is_relative_to(args.package.resolve()):
        parser.error("--output must be outside the measured package")
    try:
        report = build_size_report(args.package)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[package-size] {report['packageName']}: {report['sizeBytes'] / 1024**2:.1f} MiB ({report['fileCount']} files)")
    for entry in report["components"][:5]:
        print(f"[package-size]   {entry['name']}: {entry['sizeBytes'] / 1024**2:.1f} MiB")
    if args.max_bytes is not None and report["sizeBytes"] > args.max_bytes:
        print(f"[package-size] ERROR: payload exceeds {args.max_bytes} bytes")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
