#!/usr/bin/env python3
"""Attach verified installer outputs to an existing draft; never publish a release."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

try:
    from .build_release_evidence import collect_release_evidence_errors
    from .build_release_metadata import collect_release_metadata_errors
except ImportError:  # direct script execution
    from build_release_evidence import collect_release_evidence_errors
    from build_release_metadata import collect_release_metadata_errors

REQUIRED_JOBS = {"Release quality gate", "Windows Setup", "macOS DMG", "Update Feed"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def gh_json(*args: str):
    return json.loads(subprocess.check_output(["gh", *args], text=True))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_build(run: dict, jobs: list[dict], workflow_id: int, repository: str) -> str:
    require(run.get("repository", {}).get("full_name") == repository, "Unexpected source repository")
    require(run.get("workflow_id") == workflow_id, "Source run is not the installer workflow")
    require(run.get("head_branch") == "master", "Source build must come from master")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "Source build has not passed")
    commit = str(run.get("head_sha", ""))
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "Invalid source commit")
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job.get("name") == name]
        require(len(matches) == 1 and matches[0].get("conclusion") == "success", f"Required job did not pass: {name}")
    return commit


def validate_draft(release: dict, commit: str, version: str) -> None:
    require(release.get("draft") is True, "Refusing to modify a public release")
    require(release.get("tag_name") == f"v{version}", "Release tag/version mismatch")
    require(release.get("target_commitish") == commit, "Draft target does not match the built commit")


def verify_artifacts(root: Path, commit: str, version: str, repository: str) -> tuple[list[Path], dict]:
    feed_dir = root / "update-feed"
    feed = json.loads((feed_dir / "update.json").read_text())
    manifest = json.loads((feed_dir / "manifest.json").read_text())
    base = f"https://github.com/{repository}/releases"
    require(feed.get("version") == manifest.get("version") == version, "Update version mismatch")
    require(feed.get("appId") == manifest.get("appId") == "ClassInEDBMVP", "Update app mismatch")
    require(manifest.get("gitCommit") == commit, "Manifest commit mismatch")
    require(manifest.get("updateFeedUrl") == f"{base}/latest/download/update.json", "Unexpected feed URL")
    require(feed.get("manifestUrl") == f"{base}/download/v{version}/manifest.json", "Unexpected manifest URL")
    require(feed.get("manifestSha256") == sha256(feed_dir / "manifest.json"), "Manifest digest mismatch")
    require(set(feed.get("platforms", {})) == {"macos", "windows"}, "Incomplete platform feed")
    require(len(manifest.get("artifacts", [])) == 2, "Incomplete artifact manifest")
    files, restored = [], []
    for platform, arch, filename in [("macos", "arm64", "ClassInEDBMVP-macOS.dmg"), ("windows", "x64", "ClassInEDBMVP-Setup.exe")]:
        directory = root / f"{platform}-installer"
        metadata = directory / "release-metadata"
        # GitHub artifact transport omits hidden files. Recover only the fixed
        # generator sentinel, requiring its ORIGINAL manifest size and digest.
        sentinel = metadata / ".edb-release-metadata-output"
        if not sentinel.exists():
            original = json.loads((metadata / "metadata-manifest.json").read_text())
            entry = next(row for row in original["files"] if row["path"] == sentinel.name)
            content = b"generated; safe to replace\r\n" if platform == "windows" else b"generated; safe to replace\n"
            require(len(content) == entry["sizeBytes"] and hashlib.sha256(content).hexdigest() == entry["sha256"], "Sentinel does not match original metadata")
            sentinel.write_bytes(content)
            restored.append({"platform": platform, "file": sentinel.name, "sha256": entry["sha256"]})
        errors = collect_release_metadata_errors(metadata, expected_version=version, expected_git_commit=commit)
        errors += collect_release_evidence_errors(directory / f"{platform}-release-evidence.json", directory, expected_version=version, expected_git_commit=commit)
        require(not errors, "\n".join(errors))
        path = directory / filename
        expected = {"version": version, "arch": arch, "fileName": filename, "sizeBytes": path.stat().st_size, "sha256": sha256(path), "downloadUrl": f"{base}/download/v{version}/{filename}"}
        entry = feed["platforms"][platform]
        rows = [row for row in manifest["artifacts"] if row.get("platform") == platform]
        require(len(rows) == 1, "Duplicate/missing manifest platform")
        for key, value in expected.items():
            require(entry.get(key) == rows[0].get(key) == value, f"Artifact mismatch: {platform}/{key}")
        files.append(path)
    checksum_files = {path.name: path for path in [*files, feed_dir / "manifest.json"]}
    lines = (feed_dir / "checksums.txt").read_text().splitlines()
    require(len(lines) == len(checksum_files), "Checksum count mismatch")
    seen = set()
    for line in lines:
        digest, name = line.split("  ", 1)
        require(name in checksum_files and name not in seen, "Unknown or duplicate checksum entry")
        require(digest == sha256(checksum_files[name]), f"Checksum mismatch: {name}")
        seen.add(name)
    files += [feed_dir / name for name in ["update.json", "manifest.json", "checksums.txt"]]
    return files, {"gitCommit": commit, "version": version, "metadataTransportRestorations": restored, "artifactAndMetadataVerification": "passed", "publicReleasePublished": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/staged"))
    args = parser.parse_args()
    require(bool(re.fullmatch(r"[0-9]+", args.run_id)), "Invalid run ID")
    require(bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version)), "Invalid version")
    require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository)), "Invalid repository")
    api = f"repos/{args.repository}"
    run = gh_json("api", f"{api}/actions/runs/{args.run_id}")
    jobs = gh_json("api", f"{api}/actions/runs/{args.run_id}/jobs?per_page=100")["jobs"]
    workflow_id = gh_json("api", f"{api}/actions/workflows/build-installers.yml")["id"]
    commit = validate_build(run, jobs, workflow_id, args.repository)
    releases = gh_json("api", f"{api}/releases?per_page=100")
    matches = [release for release in releases if release["tag_name"] == f"v{args.version}"]
    require(len(matches) == 1, "An existing draft release is required")
    release = matches[0]
    validate_draft(release, commit, args.version)
    root = args.output_dir.resolve()
    require(not root.exists(), "Use a fresh artifact output directory")
    subprocess.run(["gh", "run", "download", args.run_id, "--repo", args.repository, "--dir", str(root)], check=True)
    files, report = verify_artifacts(root, commit, args.version, args.repository)
    report["sourceRunId"] = args.run_id
    report["releaseUrl"] = release["html_url"]
    (root / "artifact-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    archive = root / "release-evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for platform in ["macos", "windows"]:
            for path in sorted((root / f"{platform}-installer").rglob("*")):
                if path.is_file() and path.suffix.lower() not in {".exe", ".dmg"}:
                    output.write(path, path.relative_to(root).as_posix())
        output.write(root / "artifact-verification.json", "artifact-verification.json")
    files.append(archive)
    # Recheck immediately before mutation. This tool never creates or publishes.
    release = gh_json("api", f"{api}/releases/{release['id']}")
    validate_draft(release, commit, args.version)
    subprocess.run(["gh", "release", "upload", f"v{args.version}", "--repo", args.repository, "--clobber", *map(str, files)], check=True)
    release = gh_json("api", f"{api}/releases/{release['id']}")
    validate_draft(release, commit, args.version)
    report["uploadedAssets"] = []
    for path in files:
        matches = [asset for asset in release["assets"] if asset["name"] == path.name]
        require(len(matches) == 1, f"Missing uploaded asset: {path.name}")
        asset = matches[0]
        digest = sha256(path)
        require(asset["state"] == "uploaded" and asset["size"] == path.stat().st_size, "Uploaded asset size/state mismatch")
        require(asset.get("digest") == f"sha256:{digest}", "GitHub server digest mismatch")
        report["uploadedAssets"].append({"name": path.name, "sha256": digest, "sizeBytes": path.stat().st_size})
    (root / "staging-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"draft": True, "releaseUrl": report["releaseUrl"], "verifiedAssets": len(files)}))


if __name__ == "__main__":
    main()
