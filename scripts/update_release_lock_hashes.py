#!/usr/bin/env python3
"""Refresh PyPI artifact SHA-256 hashes in requirements-release.lock.

This maintenance command is intentionally explicit: it reads the reviewed
exact versions already present in the lock, fetches official PyPI release JSON,
and rewrites only hash continuations. Review the resulting diff before use.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from packaging.requirements import Requirement


RELEASE_HEADER = """# ClassInEDBMVP release/build dependency lock with PyPI SHA-256 hashes.
#
# The complete runtime dependency closure plus PyInstaller build dependencies
# is pinned for CPython 3.11+. Release installation MUST use --require-hashes.
# Refresh only with scripts/update_release_lock_hashes.py --write and review the
# full diff, dependency inventory, licensing disposition, and CI on both OSes.

"""

CI_HEADER = """# ClassInEDBMVP CI-only test dependency closure with PyPI SHA-256 hashes.
# Installation MUST use --require-hashes. Runtime/build packages remain in
# requirements-release.lock and its bootstrap lock.

"""


def _requirement_lines(lock_path: Path) -> list[str]:
    logical_lines: list[str] = []
    pending = ""
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("--hash="):
            continue
        if stripped.endswith("\\"):
            stripped = stripped[:-1].rstrip()
        if pending:
            pending += " " + stripped
        else:
            pending = stripped
        if "--hash=" not in pending:
            logical_lines.append(pending)
            pending = ""
    if pending:
        logical_lines.append(pending)
    return logical_lines


def _release_hashes(requirement: Requirement) -> list[str]:
    version = next(iter(requirement.specifier)).version
    url = f"https://pypi.org/pypi/{requirement.name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.load(response)
    hashes = {
        str(item.get("digests", {}).get("sha256") or "").lower()
        for item in payload.get("urls", [])
    }
    hashes.discard("")
    if not hashes:
        raise RuntimeError(f"PyPI returned no SHA-256 artifacts for {requirement.name}=={version}")
    return sorted(hashes)


def render_lock(lock_path: Path) -> str:
    blocks: list[str] = []
    for requirement_line in _requirement_lines(lock_path):
        requirement = Requirement(requirement_line)
        hashes = _release_hashes(requirement)
        lines = [requirement_line + " \\"]
        for index, digest in enumerate(hashes):
            suffix = " \\" if index < len(hashes) - 1 else ""
            lines.append(f"    --hash=sha256:{digest}{suffix}")
        blocks.append("\n".join(lines))
    header = CI_HEADER if lock_path.name == "requirements-ci.lock" else RELEASE_HEADER
    return header + "\n\n".join(blocks) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh hashes for exact PyPI release pins.")
    parser.add_argument("--lock", type=Path, default=Path("requirements-release.lock"))
    parser.add_argument("--write", action="store_true", help="Rewrite the lock instead of printing it")
    args = parser.parse_args()
    rendered = render_lock(args.lock)
    if args.write:
        args.lock.write_text(rendered, encoding="utf-8")
        print(f"Updated {args.lock}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
