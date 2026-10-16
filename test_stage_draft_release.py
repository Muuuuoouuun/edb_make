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
