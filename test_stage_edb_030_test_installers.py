from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import stage_edb_030_test_installers as stage


class TestExactTestInstallerStaging(unittest.TestCase):
    def build(self):
        return {"id": stage.RUN_IDS[0], "repository": {"full_name": stage.REPOSITORY, "id": stage.REPOSITORY_ID},
                "workflow_id": stage.WORKFLOW_ID, "path": ".github/workflows/build-installers.yml",
                "head_sha": stage.COMMIT, "head_branch": stage.SOURCE_BRANCH,
                "event": "workflow_dispatch", "status": "completed", "conclusion": "success"}

    def jobs(self):
        return [{"name": name, "status": "completed", "conclusion": "success"} for name in stage.REQUIRED_JOBS]

    def draft(self):
        return {"id": stage.RELEASE_ID, "draft": True, "published_at": None,
                "tag_name": "v0.2.10", "target_commitish": stage.COMMIT}

    def test_only_two_exact_successful_feature_builds_are_accepted(self):
        stage.validate_build(self.build(), self.jobs(), stage.RUN_IDS[0])
        second = self.build()
        second["id"] = stage.RUN_IDS[1]
        stage.validate_build(second, self.jobs(), stage.RUN_IDS[1])
        for key, value in (("id", 1), ("workflow_id", 1), ("path", "different.yml"),
                           ("head_sha", "a" * 40), ("head_branch", "master"),
                           ("event", "pull_request"), ("status", "in_progress"), ("conclusion", "failure"),
                           ("repository", {"full_name": "other/repo", "id": stage.REPOSITORY_ID})):
            with self.subTest(key=key):
                run = self.build()
                run[key] = value
                with self.assertRaises(ValueError):
                    stage.validate_build(run, self.jobs(), stage.RUN_IDS[0])

    def test_skipped_failed_missing_or_duplicated_required_job_is_rejected(self):
        for conclusion in ("skipped", "failure", "cancelled"):
            jobs = self.jobs()
            jobs[0]["conclusion"] = conclusion
            with self.subTest(conclusion=conclusion), self.assertRaisesRegex(ValueError, "Required job"):
                stage.validate_build(self.build(), jobs, stage.RUN_IDS[0])
        for jobs in (self.jobs()[1:], self.jobs() + [self.jobs()[0]]):
            with self.assertRaisesRegex(ValueError, "Required job"):
                stage.validate_build(self.build(), jobs, stage.RUN_IDS[0])

    def test_only_existing_unpublished_exact_draft_is_accepted(self):
        stage.validate_draft(self.draft())
        for key, value in (("id", 1), ("draft", False), ("published_at", "2026-10-23T12:00:00Z"),
                           ("tag_name", "v0.2.11"), ("target_commitish", "master")):
            with self.subTest(key=key):
                release = self.draft()
                release[key] = value
                with self.assertRaises(ValueError):
                    stage.validate_draft(release)

    def test_artifact_id_run_commit_expiry_and_repository_are_bound(self):
        package = stage.PACKAGES["windows"]
        artifact = {"id": package["artifactId"], "name": package["artifactName"], "expired": False,
                    "workflow_run": {"id": package["runId"], "head_sha": stage.COMMIT,
                                     "repository_id": stage.REPOSITORY_ID, "head_repository_id": stage.REPOSITORY_ID}}
        stage.validate_artifact(artifact, package)
        for field, value in (("id", 1), ("expired", True), ("name", "different")):
            changed = copy.deepcopy(artifact)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                stage.validate_artifact(changed, package)
        changed = copy.deepcopy(artifact)
        changed["workflow_run"]["head_sha"] = "b" * 40
        with self.assertRaises(ValueError):
            stage.validate_artifact(changed, package)

    def test_actual_bytes_and_github_server_digest_are_both_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "installer.exe"
            path.write_bytes(b"original")
            digest = hashlib.sha256(b"original").hexdigest()
            stage.verify_file(path, 8, digest)
            asset = {"name": path.name, "state": "uploaded", "size": 8, "digest": f"sha256:{digest}"}
            stage.validate_server_asset(asset, path)
            for key, value in (("state", "new"), ("size", 9), ("digest", "sha256:" + "0" * 64)):
                changed = dict(asset, **{key: value})
                with self.subTest(key=key), self.assertRaises(ValueError):
                    stage.validate_server_asset(changed, path)
            path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                stage.verify_file(path, 8, digest)

    def test_only_fixed_sentinel_matching_original_manifest_is_restored(self):
        for platform, payload in (("windows", b"generated; safe to replace\r\n"),
                                  ("macos", b"generated; safe to replace\n")):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original = {"files": [{"path": ".edb-release-metadata-output", "sizeBytes": len(payload),
                                       "sha256": hashlib.sha256(payload).hexdigest()}]}
                manifest = root / "metadata-manifest.json"
                manifest.write_text(json.dumps(original))
                previous = manifest.read_bytes()
                stage.restore_transport_sentinel(root, platform)
                self.assertEqual(payload, (root / ".edb-release-metadata-output").read_bytes())
                self.assertEqual(previous, manifest.read_bytes())
                (root / ".edb-release-metadata-output").unlink()
                original["files"][0]["sha256"] = "0" * 64
                manifest.write_text(json.dumps(original))
                with self.assertRaisesRegex(ValueError, "original metadata"):
                    stage.restore_transport_sentinel(root, platform)
                self.assertFalse((root / ".edb-release-metadata-output").exists())


    def test_native_update_proof_requires_real_restart_and_preservation(self):
        package = stage.PACKAGES["windows"]
        proof = {"baselineVersion": "0.2.9",
                 "baselineKind": "fixed-updater-test-bootstrap", "baselineSourceCommit": stage.COMMIT, "targetSourceCommit": stage.COMMIT, "targetVersion": stage.VERSION,
                 "platform": "windows", "arch": "x64", "targetSha256": package["sha256"],
                 "artifactDownloads": 1, "savedFilesPreserved": 5, "oldPid": 10, "newPid": 11,
                 "wrongPinDownloadedArtifact": False, "wrongPinStoppedApp": False,
                 "changedConfirmationCheck": {"rejected": True, "artifactDownloaded": False, "appStopped": False},
                 "targetPackagedConfig": {"version": stage.VERSION},
                 "beforeUpdatePdfExport": {"recordCount": 1, "partCount": 1},
                 "afterUpdatePdfExport": {"recordCount": 1, "partCount": 1}}
        success_fields = ("unmodifiedInstallerBytes", "privateLoopbackFeed", "ephemeralPinVerifier",
                          "wrongPinRejected", "realInstallApiAccepted", "oldProcessExited", "newProcessHealthy",
                          "settingsPreserved", "sessionPreserved")
        proof.update({field: True for field in success_fields})
        stage.validate_update_proof(proof, package)
        for field in success_fields:
            bad = copy.deepcopy(proof)
            bad[field] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                stage.validate_update_proof(bad, package)
        for field, value in (("baselineKind", "original-0.2.8"), ("baselineVersion", "0.2.8"),
                             ("baselineSourceCommit", "0"*40), ("targetSourceCommit", "0"*40),
                             ("targetSha256", "0"*64), ("arch", "arm64"), ("artifactDownloads", 0),
                             ("newPid", 10), ("savedFilesPreserved", 0),
                             ("changedConfirmationCheck", {"rejected": False}),
                             ("afterUpdatePdfExport", {"recordCount": 0, "partCount": 0})):
            bad = copy.deepcopy(proof)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                stage.validate_update_proof(bad, package)
