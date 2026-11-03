import copy
import unittest

from scripts.stage_draft_release import REQUIRED_JOBS, validate_build, validate_draft


class TestDraftReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.commit = 'a' * 40
        self.repo = 'owner/edb_make'
        self.run = {'repository': {'full_name': self.repo}, 'workflow_id': 123, 'head_branch': 'master', 'status': 'completed', 'conclusion': 'success', 'head_sha': self.commit}
        self.jobs = [{'name': name, 'conclusion': 'success'} for name in REQUIRED_JOBS]
        self.release = {'draft': True, 'tag_name': 'v0.2.4', 'target_commitish': self.commit}

    def test_accepts_exact_successful_installer_build(self):
        self.assertEqual(self.commit, validate_build(self.run, self.jobs, 123, self.repo))
        validate_draft(self.release, self.commit, '0.2.4')

    def test_rejects_skipped_failed_or_missing_feed_even_when_run_succeeds(self):
        for conclusion in ['skipped', 'failure', 'cancelled']:
            with self.subTest(conclusion=conclusion):
                jobs = copy.deepcopy(self.jobs)
                next(job for job in jobs if job['name'] == 'Update Feed')['conclusion'] = conclusion
                with self.assertRaises(ValueError):
                    validate_build(self.run, jobs, 123, self.repo)
        with self.assertRaises(ValueError):
            validate_build(self.run, [job for job in self.jobs if job['name'] != 'Update Feed'], 123, self.repo)

    def test_rejects_wrong_repository_workflow_branch_or_unfinished_run(self):
        for key, value in [('repository', {'full_name': 'elsewhere/repo'}), ('workflow_id', 456), ('head_branch', 'feature'), ('status', 'in_progress'), ('conclusion', 'failure'), ('head_sha', 'short')]:
            with self.subTest(key=key):
                run = {**self.run, key: value}
                with self.assertRaises(ValueError):
                    validate_build(run, self.jobs, 123, self.repo)

    def test_never_replaces_public_release_assets(self):
        with self.assertRaisesRegex(ValueError, 'public release'):
            validate_draft({**self.release, 'draft': False}, self.commit, '0.2.4')

    def test_rejects_target_commit_or_version_mismatch(self):
        for key, value in [('target_commitish', 'b' * 40), ('tag_name', 'v0.2.3')]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_draft({**self.release, key: value}, self.commit, '0.2.4')


class TestDraftReleaseArtifacts(unittest.TestCase):
    def test_stages_both_macs_and_windows_under_their_release_names(self):
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import scripts.stage_draft_release as stage

        commit, version, repo = 'a' * 40, '0.2.15', 'owner/edb_make'
        base = f'https://github.com/{repo}/releases'
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            feed_input = root / 'feed-input'
            feed_input.mkdir()
            for _, _, artifact_dir, _, built_name, release_name in stage.INSTALLERS:
                metadata = root / artifact_dir / 'release-metadata'
                metadata.mkdir(parents=True)
                (metadata / '.edb-release-metadata-output').write_bytes(b'generated')
                body = f'{artifact_dir} bytes'.encode()
                (root / artifact_dir / built_name).write_bytes(body)
                (feed_input / release_name).write_bytes(body)
            feed_dir = root / 'update-feed'
            subprocess.run([
                sys.executable, 'scripts/build_update_feed.py', '--version', version,
                '--update-feed-url', f'{base}/latest/download/update.json',
                '--manifest-url', f'{base}/download/v{version}/manifest.json',
                '--git-commit', commit,
                '--macos-url', f'{base}/download/v{version}/ClassInEDBMVP-macOS-arm64.dmg',
                '--macos-file', str(feed_input / 'ClassInEDBMVP-macOS-arm64.dmg'),
                '--macos-x64-url', f'{base}/download/v{version}/ClassInEDBMVP-macOS-x64.dmg',
                '--macos-x64-file', str(feed_input / 'ClassInEDBMVP-macOS-x64.dmg'),
                '--windows-url', f'{base}/download/v{version}/ClassInEDBMVP-Setup.exe',
                '--windows-file', str(feed_input / 'ClassInEDBMVP-Setup.exe'),
                '--manifest-output', str(feed_dir / 'manifest.json'),
                '--checksums-output', str(feed_dir / 'checksums.txt'),
                '--output', str(feed_dir / 'update.json'),
            ], cwd=Path(__file__).resolve().parent, check=True, capture_output=True)

            with patch.object(stage, 'collect_release_metadata_errors', return_value=[]), \
                    patch.object(stage, 'collect_release_evidence_errors', return_value=[]):
                files, report = stage.verify_artifacts(root, commit, version, repo)

            self.assertEqual(
                ['ClassInEDBMVP-macOS-arm64.dmg', 'ClassInEDBMVP-macOS-x64.dmg', 'ClassInEDBMVP-Setup.exe',
                 'update.json', 'manifest.json', 'checksums.txt'],
                [path.name for path in files],
            )
            self.assertTrue(all(path.is_file() for path in files))
            self.assertEqual('passed', report['artifactAndMetadataVerification'])
