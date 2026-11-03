"""Verify and stage the exact KMA internal installers without publishing a feed."""
from __future__ import annotations

import argparse
import hashlib
import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from scripts.build_release_evidence import collect_release_evidence_errors
from scripts.build_release_metadata import collect_release_metadata_errors
from scripts.stage_edb_031_test_installers import restore_transport_sentinel

ROOT = Path(os.environ["EDB_STAGING_ROOT"]).resolve()
REPOSITORY = "Muuuuoouuun/edb_make"
VERSION = "0.2.14"
COMMIT = "4b3a2ff5db0155feab7f14217d888a0783913856"
RUNS = (41551396654, 41551402432)
PACKAGES = (
    (41551396654, "windows", "x64", "ClassInEDBMVP-Setup.exe", "ClassInEDBMVP-Setup.exe"),
    (41551396654, "macos", "arm64", "ClassInEDBMVP-macOS.dmg", "ClassInEDBMVP-macOS-arm64.dmg"),
    (41551402432, "macos", "x64", "ClassInEDBMVP-macOS.dmg", "ClassInEDBMVP-macOS-x64.dmg"),
)
RELEASE_ID = 432108960
ARTIFACTS = (
    (41551396654, "windows-installer", 13193722866, "f400ffc982928de3b38129bb284f63ae715c546b273f21062843c8a877f24ab5"),
    (41551396654, "macos-installer", 13193166805, "f762947a5a2e87699fd6f29a4393f9aa398c26bddc0d014c55e73d562d992751"),
    (41551402432, "macos-installer", 13194180106, "a85d122672f2d9de7896a485f3c6ed807865ed5f44c7be4b597a3b1ffdd08dce"),
    (41551396654, "original-024-update-config-audit", 13192765279, "da6ce1bb7ef23467482ae81e4f5df6e85216b3e1ec79bc0af16d0b87dc859a1d"),
)
ASSET_NAMES = {
    "ClassInEDBMVP-Setup.exe", "ClassInEDBMVP-macOS-arm64.dmg", "ClassInEDBMVP-macOS-x64.dmg",
    "SHA256SUMS.txt", "artifact-verification.json", "release-evidence.zip",
    "INSTALL.md", "RELEASE_NOTES.md", "DEPLOYMENT_STATUS.md",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gh_json(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{endpoint}"], text=True))


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_draft(release):
    require(release.get("id") == RELEASE_ID, "Unexpected release ID")
    require(release.get("draft") is True and release.get("published_at") is None, "Refusing to modify a published release")
    require(release.get("tag_name") == f"v{VERSION}" and release.get("target_commitish") == COMMIT, "Draft tag or source mismatch")


def prepare_downloads():
    for run_id, name, artifact_id, digest in ARTIFACTS:
        remote = gh_json(f"actions/artifacts/{artifact_id}")
        require(remote.get("name") == name and remote.get("expired") is False, "Unexpected or expired source artifact")
        require(remote.get("workflow_run", {}).get("id") == run_id, "Artifact run mismatch")
        require(remote.get("workflow_run", {}).get("head_sha") == COMMIT, "Artifact commit mismatch")
        require(remote.get("digest") == f"sha256:{digest}", "Pinned ZIP digest mismatch")
        parent = ROOT / "artifacts" / str(run_id)
        parent.mkdir(parents=True, exist_ok=True)
        archive = parent / f"{name}.zip"
        if not archive.exists():
            with archive.open("wb") as stream:
                subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip"], stdout=stream, check=True)
        require(sha256(archive) == digest, "Downloaded ZIP digest mismatch")
        target = parent / name
        if not target.exists():
            with zipfile.ZipFile(archive) as zipped:
                for item in zipped.namelist():
                    require(not Path(item).is_absolute() and ".." not in Path(item).parts, "Unsafe ZIP member")
                zipped.extractall(target)
        transport = {"artifact_id": artifact_id, "artifact_name": name, "run_id": run_id, "sha256": digest, "zip_digest_verified": True}
        (parent / f"{name}-transport.json").write_text(json.dumps(transport, indent=2) + "\n")
    audit = ROOT / "artifacts" / str(RUNS[0]) / "original-024-update-config-audit"
    audit_files = list(audit.glob("*.json"))
    require(len(audit_files) == 1, "Unexpected original configuration audit")
    (ROOT / "audits").mkdir(exist_ok=True)
    shutil.copyfile(audit_files[0], ROOT / "audits/original-024-audit.json")
    for name in ("INSTALL.md", "RELEASE_NOTES.md", "DEPLOYMENT_STATUS.md"):
        shutil.copyfile(REPO_ROOT / "release" / VERSION / name, ROOT / name)


def upload_draft_assets(output):
    require({path.name for path in output.iterdir()} == ASSET_NAMES, "Unexpected draft asset set")
    uploaded = []
    for path in sorted(output.iterdir()):
        release = gh_json(f"releases/{RELEASE_ID}")
        validate_draft(release)
        matches = [asset for asset in release.get("assets", []) if asset.get("name") == path.name]
        require(len(matches) <= 1, "Duplicate release asset")
        if matches:
            require(matches[0].get("digest") == "sha256:" + sha256(path) and matches[0].get("size") == path.stat().st_size,
                    "Refusing to overwrite different release asset bytes")
        else:
            subprocess.run(["gh", "release", "upload", f"v{VERSION}", "--repo", REPOSITORY, str(path)], check=True)
        after = gh_json(f"releases/{RELEASE_ID}")
        validate_draft(after)
        found = [asset for asset in after.get("assets", []) if asset.get("name") == path.name]
        require(len(found) == 1 and found[0].get("state") == "uploaded", "Uploaded asset missing")
        require(found[0].get("digest") == "sha256:" + sha256(path) and found[0].get("size") == path.stat().st_size,
                "Uploaded asset digest or size mismatch")
        uploaded.append({"name": path.name, "sha256": sha256(path), "sizeBytes": path.stat().st_size})
    (ROOT / "upload-verification.json").write_text(json.dumps({"draft": True, "releaseId": RELEASE_ID, "assets": uploaded}, indent=2) + "\n")


def checked_run(run_id):
    run = gh_json(f"actions/runs/{run_id}")
    require(run["head_sha"] == COMMIT and run["head_branch"] == "master", "Unexpected build source")
    require(run["repository"]["full_name"] == REPOSITORY, "Unexpected repository")
    require(run["event"] == "workflow_dispatch" and run["path"] == ".github/workflows/build-installers.yml", "Unexpected workflow")
    require(run["status"] == "completed" and run["conclusion"] == "success", "Installer workflow has not passed")
    jobs = gh_json(f"actions/runs/{run_id}/jobs?per_page=100")["jobs"]
    for name in ("Release quality gate", "Windows Setup", "macOS DMG", "Original 0.2.4 update config audit"):
        matches = [job for job in jobs if job["name"] == name]
        require(len(matches) == 1 and matches[0]["conclusion"] == "success", f"Required job failed: {name}")
    for name in (
        "Verify actual PIN update from 0.2.8 on macOS",
        "Audit target PIN and update channel configuration",
        "Verify actual PIN update from the fixed Windows test bootstrap",
    ):
        steps = [step for job in jobs for step in job.get("steps", []) if step["name"] == name]
        require(len(steps) == 1 and steps[0]["conclusion"] == "success", f"Required native verification failed: {name}")
    return {"id": run_id, "gitCommit": COMMIT, "url": run["html_url"], "conclusion": "success"}


def checked_native_diagnostics(run_id, platform, arch, digest):
    directory = ROOT / "artifacts" / str(run_id) / f"{platform}-installer"
    path = directory / "update-smoke.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    require(data.get("targetVersion") == VERSION and data.get("targetSourceCommit") == COMMIT, "Native updater target mismatch")
    require(data.get("targetSha256") == digest, "Native updater tested different installer bytes")
    require(data.get("arch") == arch, "Native updater architecture mismatch")
    for key in ("wrongPinRejected", "realInstallApiAccepted", "newProcessHealthy", "settingsPreserved", "sessionPreserved", "unmodifiedInstallerBytes"):
        require(data.get(key) is True, f"Native updater assertion missing: {key}")
    return {"nativeUpdateVerified": True, "settingsPreserved": True, "sessionPreserved": True,
            "privateLoopbackFeed": data.get("privateLoopbackFeed"),
            "ephemeralPinVerifier": data.get("ephemeralPinVerifier"),
            "baselineKind": data.get("baselineKind"),
            "targetPackagedConfig": data.get("targetPackagedConfig")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    runs = [checked_run(run_id) for run_id in RUNS]
    prepare_downloads()
    releases = gh_json("releases?per_page=100")
    candidates = [release for release in releases if release["tag_name"] == f"v{VERSION}"]
    require(len(candidates) == 1, "Expected one existing release draft")
    release = candidates[0]
    validate_draft(release)
    output = ROOT / "verified-assets"
    output.mkdir(exist_ok=True)
    packages, restorations = [], []
    for run_id, platform, arch, source_name, output_name in PACKAGES:
        source = ROOT / "artifacts" / str(run_id) / f"{platform}-installer"
        transport = json.loads((source.parent / f"{platform}-installer-transport.json").read_text())
        remote = gh_json(f"actions/artifacts/{transport['artifact_id']}")
        require(remote.get("name") == f"{platform}-installer", "Unexpected GitHub artifact name")
        require(remote.get("workflow_run", {}).get("id") == run_id, "Unexpected GitHub artifact source run")
        require(remote.get("workflow_run", {}).get("head_sha") == COMMIT, "Unexpected GitHub artifact source commit")
        require(remote.get("digest") == "sha256:" + transport["sha256"], "GitHub ZIP digest differs from download evidence")
        require(sha256(source.parent / f"{platform}-installer.zip") == transport["sha256"], "Downloaded artifact ZIP changed")
        metadata = source / "release-metadata"
        restoration = restore_transport_sentinel(metadata, platform)
        if restoration:
            restorations.append({"runId": run_id, "platform": platform, **restoration})
        errors = collect_release_metadata_errors(metadata, expected_version=VERSION, expected_git_commit=COMMIT)
        errors += collect_release_evidence_errors(source / f"{platform}-release-evidence.json", source, expected_version=VERSION, expected_git_commit=COMMIT)
        require(not errors, "\n".join(errors))
        provenance = json.loads((metadata / "release-provenance.json").read_text())
        expected_environment = {
            ("windows", "x64"): "win32-amd64-cp311",
            ("macos", "arm64"): "darwin-arm64-cp311",
            ("macos", "x64"): "darwin-x86_64-cp311",
        }[(platform, arch)]
        require(provenance.get("environment") == expected_environment, "Installer provenance architecture mismatch")
        # Keep the original metadata intact; a named asset is a byte-for-byte copy.
        installer = source / source_name
        digest = sha256(installer)
        diagnostics = checked_native_diagnostics(run_id, platform, arch, digest)
        target = output / output_name
        shutil.copyfile(installer, target)
        require(sha256(target) == digest, "Installer changed while staging")
        packages.append({"fileName": output_name, "platform": platform, "arch": arch,
                         "sourceRunId": run_id, "sourceArtifactId": transport["artifact_id"],
                         "sourceArtifactZipSha256": transport["sha256"],
                         "sha256": digest, "sizeBytes": target.stat().st_size,
                         "gitCommit": COMMIT, "version": VERSION, **diagnostics})
    report = {"version": VERSION, "gitCommit": COMMIT, "mode": "internal-test", "draft": True,
              "sourceRuns": runs, "packages": packages, "metadataTransportRestorations": restorations,
              "publicReleasePublished": False, "updateFeedPublished": False,
              "productionQualitySigningAndLicenseApproval": "not asserted",
              "releaseUrl": release["html_url"]}
    (output / "artifact-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    (output / "SHA256SUMS.txt").write_text("".join(f"{package['sha256']}  {package['fileName']}\n" for package in packages))
    shutil.copyfile(ROOT / "RELEASE_NOTES.md", output / "RELEASE_NOTES.md")
    shutil.copyfile(ROOT / "INSTALL.md", output / "INSTALL.md")
    shutil.copyfile(ROOT / "DEPLOYMENT_STATUS.md", output / "DEPLOYMENT_STATUS.md")
    with zipfile.ZipFile(output / "release-evidence.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for run_id, platform, arch, _, _ in PACKAGES:
            source = ROOT / "artifacts" / str(run_id) / f"{platform}-installer"
            for path in sorted((source / "release-metadata").rglob("*")):
                if path.is_file():
                    archive.write(path, f"{platform}-{arch}/{path.relative_to(source).as_posix()}")
            archive.write(source / f"{platform}-release-evidence.json", f"{platform}-{arch}/release-evidence.json")
            diagnostics = ROOT / "artifacts" / str(run_id) / f"{platform}-installer" / "update-smoke.json"
            archive.write(diagnostics, f"{platform}-{arch}/update-smoke.json")
        archive.write(output / "artifact-verification.json", "artifact-verification.json")
        archive.write(ROOT / "audits/original-024-audit.json", "original-024-update-config.json")
    if not args.verify_only:
        upload_draft_assets(output)
    print(json.dumps({"draft": True, "version": VERSION, "gitCommit": COMMIT, "verifiedPackages": len(packages), "releaseUrl": release["html_url"]}))


if __name__ == "__main__":
    main()
