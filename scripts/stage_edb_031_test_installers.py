#!/usr/bin/env python3
"""One-off attachment of exact 0.2.11 test installers to one existing draft.

This is intentionally separate from stage_draft_release.py's production gates.
It cannot create releases, publish releases, or produce an update feed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import quote
import zipfile

try:
    from .build_release_evidence import collect_release_evidence_errors
    from .build_release_metadata import collect_release_metadata_errors
except ImportError:
    from build_release_evidence import collect_release_evidence_errors
    from build_release_metadata import collect_release_metadata_errors


REPOSITORY = "Muuuuoouuun/edb_make"
REPOSITORY_ID = 1205623457
WORKFLOW_ID = 298672098
COMMIT = "72d58cfd7ffd5f0c3b6c494f1fb34a118f5f608c"
SOURCE_BRANCH = "fix/update-safety-20261023"
STAGING_BRANCH = "chore/stage-edb-031-test-installers"
VERSION = "0.2.11"
RELEASE_ID = 421912949
RUN_IDS = (39968065001, 39968072007)
REQUIRED_JOBS = {"Release quality gate", "Windows Setup", "macOS DMG", "Original 0.2.4 update config audit"}
WINDOWS_ZIP_SHA256 = "a707116a63192597f172a41971dfbda617c8acd17d271f42e746fa83ee9ec24f"
WINDOWS_ZIP_SIZE = 49002851
CONFIG_AUDIT = {"runId": 39968072007, "artifactId": 12506794482, "artifactName": "original-024-update-config-audit"}
CONFIG_AUDIT_SHA256 = "8972a3363fd252c65a30544544580c92a32b86b6ae8eeec3e2b3ba3aafaff435"
PACKAGES = {'windows': {'runId': 39968065001,
             'artifactId': 12508048204,
             'artifactName': 'windows-installer',
             'platform': 'windows',
             'environment': 'win32-amd64-cp311',
             'sourceName': 'ClassInEDBMVP-Setup.exe',
             'outputName': 'ClassInEDBMVP-Setup.exe',
             'sizeBytes': 49002707,
             'sha256': '43cc9872df85f57b1a96833fb30967c70f90b08e95c73f73e41af14238790787',
             'metadataManifestSha256': 'bc4b561645890cfdb6b8c7dd3bd59a981e97d6dd76f91377e169805f2ed2d31c',
             'evidenceSha256': '63cd086b01de708f54171abb50a3ac99fec3a90c169f28d45789fd1c58333f3c'},
 'macos-arm64': {'runId': 39968065001,
                 'artifactId': 12508373780,
                 'artifactName': 'macos-installer',
                 'platform': 'macos',
                 'environment': 'darwin-arm64-cp311',
                 'sourceName': 'ClassInEDBMVP-macOS.dmg',
                 'outputName': 'ClassInEDBMVP-macOS-arm64.dmg',
                 'sizeBytes': 52975496,
                 'sha256': '095abfcba74869b5378da2f77158db51dd6eeaed08e6aca4536800a9312fc731',
                 'metadataManifestSha256': '5b59717ad667eb904c8fa9b5adae1ad0a8c6dd410f8550be2444dd5d71d86dfe',
                 'evidenceSha256': 'b52b0eed8dec9511e08768a8f912a4e1907d8989bd379184a1d24b7c4e97f8ba'},
 'macos-x64': {'runId': 39968072007,
               'artifactId': 12509570052,
               'artifactName': 'macos-installer',
               'platform': 'macos',
               'environment': 'darwin-x86_64-cp311',
               'sourceName': 'ClassInEDBMVP-macOS.dmg',
               'outputName': 'ClassInEDBMVP-macOS-x64.dmg',
               'sizeBytes': 72244083,
               'sha256': '6a0ee5696665d860144167fca8a2bbc33f3acdffb145520d050719d87ca2388b',
               'metadataManifestSha256': 'd6466feb5a040649fe88e8510120a3cf00f0fe2a8c7036a5db05d53138d82937',
               'evidenceSha256': '002bf70d81745c71b7c522c143a6289ff121ce522c4d61f71880a35aa26c7f1f'}}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def gh_json(*arguments: str):
    return json.loads(subprocess.check_output(["gh", *arguments], text=True))


def validate_build(run: dict, jobs: list[dict], expected_id: int) -> None:
    require(expected_id in RUN_IDS and run.get("id") == expected_id, "Unexpected source run ID")
    require(run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("repository", {}).get("id") == REPOSITORY_ID, "Unexpected source repository")
    require(run.get("workflow_id") == WORKFLOW_ID
            and run.get("path") == ".github/workflows/build-installers.yml", "Unexpected installer workflow")
    require(run.get("head_sha") == COMMIT and run.get("head_branch") == SOURCE_BRANCH, "Unexpected source commit/branch")
    require(run.get("event") == "workflow_dispatch", "Unexpected source workflow event")
    require(run.get("status") == "completed" and run.get("conclusion") == "success", "Source run has not passed")
    for name in REQUIRED_JOBS:
        matches = [job for job in jobs if job.get("name") == name]
        require(len(matches) == 1 and matches[0].get("status") == "completed"
                and matches[0].get("conclusion") == "success", f"Required job did not pass: {name}")


def validate_draft(release: dict) -> None:
    require(release.get("id") == RELEASE_ID, "Unexpected release ID")
    require(release.get("draft") is True and release.get("published_at") is None, "Refusing to modify a public release")
    require(release.get("tag_name") == f"v{VERSION}", "Unexpected release tag/version")
    require(release.get("target_commitish") == COMMIT, "Draft target differs from the built commit")


def validate_artifact(artifact: dict, package: dict) -> None:
    require(artifact.get("id") == package["artifactId"] and artifact.get("name") == package["artifactName"], "Unexpected source artifact")
    require(artifact.get("expired") is False, "Source artifact has expired")
    run = artifact.get("workflow_run") or {}
    require(run.get("id") == package["runId"] and run.get("head_sha") == COMMIT
            and run.get("repository_id") == REPOSITORY_ID and run.get("head_repository_id") == REPOSITORY_ID,
            "Artifact provenance does not match the verified run")


def verify_file(path: Path, size: int, digest: str) -> None:
    require(path.is_file() and not path.is_symlink(), f"Missing or unsafe artifact: {path.name}")
    require(path.stat().st_size == size, f"Artifact size mismatch: {path.name}")
    require(sha256(path) == digest, f"Artifact SHA-256 mismatch: {path.name}")


def restore_transport_sentinel(metadata: Path, platform: str) -> dict | None:
    sentinel = metadata / ".edb-release-metadata-output"
    if sentinel.exists():
        return None
    manifest = json.loads((metadata / "metadata-manifest.json").read_text())
    matches = [row for row in manifest.get("files", []) if row.get("path") == sentinel.name]
    require(len(matches) == 1, "Original metadata sentinel entry missing or duplicated")
    original = matches[0]
    content = b"generated; safe to replace\r\n" if platform == "windows" else b"generated; safe to replace\n"
    require(len(content) == original.get("sizeBytes") and hashlib.sha256(content).hexdigest() == original.get("sha256"),
            "Sentinel does not match the original metadata manifest")
    sentinel.write_bytes(content)
    return {"file": sentinel.name, "sha256": original["sha256"]}


def make_windows_wrapper(executable: Path, output: Path) -> None:
    info = zipfile.ZipInfo("ClassInEDBMVP-Setup.exe", (2026, 10, 23, 0, 0, 0))
    info.create_system = 0
    info.external_attr = 0x20
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, executable.read_bytes())
    verify_file(output, WINDOWS_ZIP_SIZE, WINDOWS_ZIP_SHA256)
    with zipfile.ZipFile(output) as archive:
        require(archive.namelist() == ["ClassInEDBMVP-Setup.exe"], "Unexpected Windows ZIP contents")
        require(hashlib.sha256(archive.read("ClassInEDBMVP-Setup.exe")).hexdigest() == PACKAGES["windows"]["sha256"],
                "Windows ZIP changed executable bytes")


def validate_update_proof(proof: dict, package: dict) -> None:
    windows = package["platform"] == "windows"
    require(proof.get("baselineVersion") == ("0.2.9" if windows else "0.2.8")
            and proof.get("targetVersion") == VERSION, "Unexpected native updater version transition")
    require(proof.get("baselineKind") == ("fixed-updater-test-bootstrap" if windows else "original-0.2.8"),
            "Native updater baseline kind is mislabeled")
    require(proof.get("baselineSourceCommit") == (COMMIT if windows else "d5a3f37d984047875ae32bff22967ff625a4bfe2")
            and proof.get("targetSourceCommit") == COMMIT, "Native updater embedded source commits differ")
    require(proof.get("platform") == package["platform"] and proof.get("targetSha256") == package["sha256"],
            "Native update proof belongs to another installer")
    expected_arch = "arm64" if package["environment"] == "darwin-arm64-cp311" else "x64"
    require(proof.get("arch") == expected_arch, "Native updater architecture mismatch")
    for field in ("unmodifiedInstallerBytes", "privateLoopbackFeed", "ephemeralPinVerifier",
                  "wrongPinRejected", "realInstallApiAccepted", "oldProcessExited", "newProcessHealthy",
                  "settingsPreserved", "sessionPreserved"):
        require(proof.get(field) is True, f"Missing native updater success evidence: {field}")
    for field in ("wrongPinDownloadedArtifact", "wrongPinStoppedApp"):
        require(proof.get(field) is False, f"Native updater rejection failed: {field}")
    require(proof.get("artifactDownloads") == 1 and proof.get("savedFilesPreserved", 0) >= 3,
            "Native updater download/persistence evidence missing")
    require(type(proof.get("oldPid")) is int and type(proof.get("newPid")) is int
            and proof["oldPid"] != proof["newPid"], "Native updater did not replace the process")
    changed = proof.get("changedConfirmationCheck") or {}
    require(changed.get("rejected") is True and changed.get("artifactDownloaded") is False
            and changed.get("appStopped") is False, "Newly installed backend confirmation guard failed")
    for field in ("beforeUpdatePdfExport", "afterUpdatePdfExport"):
        require((proof.get(field) or {}).get("recordCount") == 1
                and (proof.get(field) or {}).get("partCount") == 1, "Native PDF export evidence missing")
    require((proof.get("targetPackagedConfig") or {}).get("version") == VERSION,
            "Installed resources do not match target version")


def prepare_assets(inputs: Path, output: Path) -> tuple[list[Path], dict]:
    require(not output.exists(), "Use a fresh staging output directory")
    output.mkdir(parents=True)
    report = {"appVersion": VERSION, "gitCommit": COMMIT, "sourceRunIds": list(RUN_IDS),
              "signedForPublicDistribution": False, "publicUpdateFeedPublished": False,
              "releaseId": RELEASE_ID, "existingWindows028RequiresManualInstallation": True, "artifacts": [], "metadataTransportRestorations": []}
    original_config = inputs / "original-update-config/update-config-0.2.4.json"
    require(sha256(original_config) == CONFIG_AUDIT_SHA256, "Original configuration audit changed")
    report["originalPackagedConfig"] = json.loads(original_config.read_text())
    report["originalConfigAuditSource"] = CONFIG_AUDIT
    files = []
    for label, package in PACKAGES.items():
        directory = inputs / label
        metadata = directory / "release-metadata"
        require(sha256(metadata / "metadata-manifest.json") == package["metadataManifestSha256"], "Original metadata manifest changed")
        evidence = directory / f"{package['platform']}-release-evidence.json"
        require(sha256(evidence) == package["evidenceSha256"], "Original artifact evidence changed")
        restored = restore_transport_sentinel(metadata, package["platform"])
        if restored:
            report["metadataTransportRestorations"].append({"platform": label, **restored})
        errors = collect_release_metadata_errors(metadata, expected_version=VERSION, expected_git_commit=COMMIT)
        errors += collect_release_evidence_errors(evidence, directory, expected_version=VERSION, expected_git_commit=COMMIT)
        require(not errors, "\n".join(errors))
        provenance = json.loads((metadata / "release-provenance.json").read_text())
        require(provenance.get("environment") == package["environment"], f"Unexpected architecture: {label}")
        source = directory / package["sourceName"]
        verify_file(source, package["sizeBytes"], package["sha256"])
        target = output / package["outputName"]
        shutil.copyfile(source, target)
        verify_file(target, package["sizeBytes"], package["sha256"])
        proof = json.loads((directory / "update-smoke.json").read_text())
        validate_update_proof(proof, package)
        report.setdefault("nativeUpdateVerification", {})[label] = proof
        files.append(target)
        report["artifacts"].append({"fileName": target.name, "platform": label, "sourceRunId": package["runId"],
                                    "sourceArtifactId": package["artifactId"], "sizeBytes": package["sizeBytes"], "sha256": package["sha256"]})
    wrapper = output / "ClassInEDBMVP-Setup.zip"
    make_windows_wrapper(output / "ClassInEDBMVP-Setup.exe", wrapper)
    files.append(wrapper)
    report["artifacts"].append({"fileName": wrapper.name, "sizeBytes": WINDOWS_ZIP_SIZE, "sha256": WINDOWS_ZIP_SHA256,
                                "wrapperOf": "ClassInEDBMVP-Setup.exe", "platform": "windows"})
    verification = output / "artifact-verification.json"
    verification.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files))
    archive_path = output / "release-evidence.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for label in PACKAGES:
            for path in sorted((inputs / label).rglob("*")):
                if path.is_file() and path.suffix.lower() not in {".exe", ".dmg"}:
                    info = zipfile.ZipInfo(path.relative_to(inputs).as_posix(), (2026, 10, 23, 0, 0, 0))
                    info.create_system, info.external_attr, info.compress_type = 0, 0x20, zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo("artifact-verification.json", (2026, 10, 23, 0, 0, 0))
        info.create_system, info.external_attr, info.compress_type = 0, 0x20, zipfile.ZIP_DEFLATED
        archive.writestr(info, verification.read_bytes())
    return [*files, verification, checksums, archive_path], report


def validate_server_asset(asset: dict, path: Path) -> None:
    require(asset.get("name") == path.name and asset.get("state") == "uploaded", "Uploaded asset name/state mismatch")
    require(asset.get("size") == path.stat().st_size and asset.get("digest") == f"sha256:{sha256(path)}",
            f"GitHub server size/digest mismatch: {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/staged-edb-031"))
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Unexpected staging repository")
    require(os.environ.get("GITHUB_REF") == f"refs/heads/{STAGING_BRANCH}", "Unexpected staging branch")
    require(os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Manual staging dispatch required")
    api = f"repos/{REPOSITORY}"
    for run_id in RUN_IDS:
        run = gh_json("api", f"{api}/actions/runs/{run_id}")
        jobs = gh_json("api", f"{api}/actions/runs/{run_id}/jobs?per_page=100")
        require(jobs.get("total_count", 0) <= 100, "Unexpected source job pagination")
        validate_build(run, jobs["jobs"], run_id)
    release_endpoint = f"{api}/releases/{RELEASE_ID}"
    validate_draft(gh_json("api", release_endpoint))
    root = args.output_dir.resolve()
    require(not root.exists(), "Use a fresh staging directory")
    inputs = root / "inputs"
    for label, package in PACKAGES.items():
        artifact = gh_json("api", f"{api}/actions/artifacts/{package['artifactId']}")
        validate_artifact(artifact, package)
        subprocess.run(["gh", "run", "download", str(package["runId"]), "--repo", REPOSITORY,
                        "--name", package["artifactName"], "--dir", str(inputs / label)], check=True)
    validate_artifact(gh_json("api", f"{api}/actions/artifacts/{CONFIG_AUDIT['artifactId']}"), CONFIG_AUDIT)
    subprocess.run(["gh", "run", "download", str(CONFIG_AUDIT["runId"]), "--repo", REPOSITORY,
                    "--name", CONFIG_AUDIT["artifactName"], "--dir", str(inputs / "original-update-config")], check=True)
    files, report = prepare_assets(inputs, root / "assets")
    report["uploadedAssets"] = []
    for path in files:
        release = gh_json("api", release_endpoint)
        validate_draft(release)
        matches = [asset for asset in release.get("assets", []) if asset.get("name") == path.name]
        require(len(matches) <= 1, "Duplicate draft asset names")
        if matches:
            validate_server_asset(matches[0], path)
        else:
            # Exact release ID and fixed upload host; no tag lookup or clobber.
            uploaded = gh_json("api", "--method", "POST", "--input", str(path), "-H", "Content-Type: application/octet-stream",
                               f"https://uploads.github.com/repos/{REPOSITORY}/releases/{RELEASE_ID}/assets?name={quote(path.name)}")
            validate_server_asset(uploaded, path)
        validate_draft(gh_json("api", release_endpoint))
        report["uploadedAssets"].append({"name": path.name, "sizeBytes": path.stat().st_size, "sha256": sha256(path)})
    final = gh_json("api", release_endpoint)
    validate_draft(final)
    for path in files:
        matches = [asset for asset in final.get("assets", []) if asset.get("name") == path.name]
        require(len(matches) == 1, f"Missing final asset: {path.name}")
        validate_server_asset(matches[0], path)
    report.update(draft=True, publicReleasePublished=False, releaseUrl=final["html_url"])
    (root / "staging-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"draft": True, "releaseId": RELEASE_ID, "verifiedAssets": len(files)}))


if __name__ == "__main__":
    main()
