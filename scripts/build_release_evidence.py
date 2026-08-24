#!/usr/bin/env python3
"""Create or verify a version/commit-bound SHA-256 manifest for release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .build_release_metadata import collect_release_metadata_errors
except ImportError:  # pragma: no cover - direct script execution
    from build_release_metadata import collect_release_metadata_errors


EXPECTED_ARTIFACT_NAMES = {
    "macos": {"ClassInEDBMVP-macOS.dmg", "ClassInEDBMVP-macOS.zip"},
    "windows": {"ClassInEDBMVP-Setup.exe"},
}
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_release_evidence(
    *,
    platform_name: str,
    version: str,
    git_commit: str,
    metadata_root: Path,
    artifacts: list[Path],
) -> dict[str, Any]:
    expected_names = EXPECTED_ARTIFACT_NAMES.get(platform_name)
    if expected_names is None:
        raise ValueError(f"unsupported release evidence platform: {platform_name}")
    commit = git_commit.strip().lower()
    if not GIT_COMMIT_RE.fullmatch(commit):
        raise ValueError("release evidence requires a full 40-character lowercase git commit")
    metadata_errors = collect_release_metadata_errors(
        metadata_root, expected_version=version, expected_git_commit=commit
    )
    if metadata_errors:
        raise ValueError("\n".join(metadata_errors))
    resolved_artifacts = [path.expanduser().resolve() for path in artifacts]
    names = {path.name for path in resolved_artifacts}
    if names != expected_names or len(resolved_artifacts) != len(expected_names):
        raise ValueError(
            f"{platform_name} evidence requires exact artifacts {sorted(expected_names)}, found {sorted(names)}"
        )
    entries = []
    for artifact in sorted(resolved_artifacts, key=lambda path: path.name):
        if not artifact.is_file() or artifact.stat().st_size <= 0:
            raise ValueError(f"release artifact is missing or empty: {artifact}")
        entries.append(
            {
                "fileName": artifact.name,
                "sizeBytes": artifact.stat().st_size,
                "sha256": _sha256(artifact),
            }
        )
    return {
        "schemaVersion": 1,
        "platform": platform_name,
        "appVersion": version,
        "gitCommit": commit,
        "artifacts": entries,
    }


def collect_release_evidence_errors(
    evidence_path: Path,
    artifact_root: Path,
    *,
    expected_version: str = "",
    expected_git_commit: str = "",
) -> list[str]:
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid release evidence JSON: {exc}"]
    errors: list[str] = []
    if evidence.get("schemaVersion") != 1:
        errors.append("release evidence schemaVersion mismatch")
    platform_name = str(evidence.get("platform") or "")
    expected_names = EXPECTED_ARTIFACT_NAMES.get(platform_name)
    if expected_names is None:
        errors.append(f"unsupported release evidence platform: {platform_name}")
        expected_names = set()
    version = str(evidence.get("appVersion") or "")
    commit = str(evidence.get("gitCommit") or "")
    if expected_version and version != expected_version:
        errors.append(f"release evidence version mismatch: expected {expected_version}, found {version}")
    if expected_git_commit and commit != expected_git_commit:
        errors.append(f"release evidence commit mismatch: expected {expected_git_commit}, found {commit}")
    entries = evidence.get("artifacts")
    if not isinstance(entries, list):
        return errors + ["release evidence artifacts must be an array"]
    seen_names: set[str] = set()
    root = artifact_root.expanduser().resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("release evidence contains a non-object artifact")
            continue
        file_name = str(entry.get("fileName") or "")
        if Path(file_name).name != file_name or file_name in seen_names:
            errors.append(f"unsafe or duplicate release evidence filename: {file_name!r}")
            continue
        seen_names.add(file_name)
        artifact = root / file_name
        if not artifact.is_file():
            errors.append(f"release evidence artifact is missing: {file_name}")
            continue
        if artifact.stat().st_size != entry.get("sizeBytes"):
            errors.append(f"release evidence size mismatch: {file_name}")
        if _sha256(artifact) != entry.get("sha256"):
            errors.append(f"release evidence SHA-256 mismatch: {file_name}")
    if seen_names != expected_names:
        errors.append(
            f"release evidence artifact set mismatch: expected {sorted(expected_names)}, found {sorted(seen_names)}"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify exact release artifact evidence.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--platform", choices=sorted(EXPECTED_ARTIFACT_NAMES), required=True)
    create_parser.add_argument("--version", required=True)
    create_parser.add_argument("--git-commit", required=True)
    create_parser.add_argument("--metadata-root", type=Path, required=True)
    create_parser.add_argument("--artifact", type=Path, action="append", required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("evidence", type=Path)
    verify_parser.add_argument("--artifact-root", type=Path, required=True)
    verify_parser.add_argument("--expected-version", default="")
    verify_parser.add_argument("--expected-git-commit", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            payload = create_release_evidence(
                platform_name=args.platform,
                version=args.version,
                git_commit=args.git_commit,
                metadata_root=args.metadata_root,
                artifacts=args.artifact,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"[release-evidence] OK: {args.output}")
            return 0
        errors = collect_release_evidence_errors(
            args.evidence,
            args.artifact_root,
            expected_version=args.expected_version,
            expected_git_commit=args.expected_git_commit,
        )
    except (OSError, ValueError) as exc:
        errors = str(exc).splitlines()
    if errors:
        for error in errors:
            print(f"[release-evidence] ERROR: {error}", file=sys.stderr)
        return 1
    print(f"[release-evidence] OK: {args.evidence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
