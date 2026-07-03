#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PATTERNS = (
    "build",
    "dist*",
    "tmp_validation_*",
)
EDB_EXPORT_PATTERNS = (
    "generated_edb_pair*",
)
RUNTIME_PATTERNS = (
    ".app_runtime",
)
LEGACY_UI_FILE_PATHS = (
    Path(".app_runtime") / "generated_session.js",
    Path("ui_prototype") / "generated_session.js",
    Path("ui_prototype") / "prototype_data.js",
)


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    category: str


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _safe_root_child(root: Path, path: Path) -> bool:
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve(strict=False)
    except OSError:
        return False
    return resolved_path != resolved_root and resolved_path.parent == resolved_root


def _is_legacy_ui_file(root: Path, path: Path) -> bool:
    try:
        relative_path = path.resolve(strict=False).relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return relative_path in LEGACY_UI_FILE_PATHS


def _covered_by_existing_candidate(path: Path, candidates: list[CleanupCandidate]) -> bool:
    try:
        resolved_path = path.resolve(strict=False)
    except OSError:
        return False
    for candidate in candidates:
        try:
            resolved_candidate = candidate.path.resolve(strict=False)
        except OSError:
            continue
        if resolved_path == resolved_candidate or resolved_candidate in resolved_path.parents:
            return True
    return False


def collect_cleanup_candidates(
    root: Path,
    *,
    include_edb_exports: bool = False,
    include_runtime: bool = False,
) -> list[CleanupCandidate]:
    root = root.resolve()
    candidates: list[CleanupCandidate] = []
    categories: list[tuple[str, tuple[str, ...]]] = [("packaging", DEFAULT_PATTERNS)]
    if include_edb_exports:
        categories.append(("edb-export", EDB_EXPORT_PATTERNS))
    if include_runtime:
        categories.append(("runtime", RUNTIME_PATTERNS))

    for child in root.iterdir():
        if not _safe_root_child(root, child):
            continue
        for category, patterns in categories:
            if _matches(child.name, patterns):
                candidates.append(CleanupCandidate(path=child, category=category))
                break

    for relative_path in LEGACY_UI_FILE_PATHS:
        path = root / relative_path
        if (
            _is_legacy_ui_file(root, path)
            and not _covered_by_existing_candidate(path, candidates)
            and (path.is_file() or path.is_symlink())
        ):
            candidates.append(CleanupCandidate(path=path, category="legacy-ui"))

    return sorted(candidates, key=lambda candidate: (candidate.category, candidate.path.name))


def path_size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        try:
            return path.lstat().st_size
        except OSError:
            return 0

    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
        for name in filenames:
            candidate = Path(dirpath) / name
            try:
                total += candidate.lstat().st_size
            except OSError:
                continue
    return total


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def remove_candidate(root: Path, candidate: CleanupCandidate) -> None:
    path = candidate.path
    root_child = _safe_root_child(root, path)
    legacy_ui_file = (
        candidate.category == "legacy-ui"
        and (path.is_file() or path.is_symlink())
        and _is_legacy_ui_file(root, path)
    )
    if not (root_child or legacy_ui_file):
        raise ValueError(f"refusing to remove non-root child: {path}")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Remove ignored local packaging artifacts that can be mistaken for the "
            "current app build."
        )
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--yes", action="store_true", help="Actually remove files. Without this, only prints a dry run.")
    parser.add_argument(
        "--include-edb-exports",
        action="store_true",
        help="Also remove generated_edb_pair* export folders.",
    )
    parser.add_argument(
        "--include-runtime",
        action="store_true",
        help="Also remove the local .app_runtime folder.",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    candidates = collect_cleanup_candidates(
        root,
        include_edb_exports=args.include_edb_exports,
        include_runtime=args.include_runtime,
    )
    if not candidates:
        print("[cleanup] no stale local artifacts found")
        return 0

    action = "removing" if args.yes else "would remove"
    total_size = 0
    for candidate in candidates:
        size = path_size(candidate.path)
        total_size += size
        rel_path = candidate.path.relative_to(root)
        print(f"[cleanup] {action} {candidate.category}: {rel_path} ({format_size(size)})")
        if args.yes:
            try:
                remove_candidate(root, candidate)
            except OSError as exc:
                print(f"[cleanup] ERROR: failed to remove {rel_path}: {exc}", file=sys.stderr)
                return 1

    if args.yes:
        print(f"[cleanup] removed {len(candidates)} artifact(s), {format_size(total_size)}")
    else:
        print(f"[cleanup] dry run only; rerun with --yes to remove {len(candidates)} artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
