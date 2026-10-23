"""Guard rails for real native updater verification (no mocked install claim)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import urllib.request

from scripts.smoke_packaged_update import PrivateFeed, owned_macos_home, packaged_config_audit, pin_verifier, require_artifact, snapshot_files, verify_snapshot, verify_changed_confirmation, copy_from_dmg, wait_for_ready


class NativeUpdateHarness(unittest.TestCase):
    def test_pinned_bytes_required_before_install(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Setup.exe'; path.write_bytes(b'original')
            self.assertEqual(path.resolve(), require_artifact(path, hashlib.sha256(b'original').hexdigest()))
            path.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'pinned'):
                require_artifact(path, hashlib.sha256(b'original').hexdigest())

    def test_feed_is_loopback_and_counts_only_artifact_requests(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Setup.exe'; path.write_bytes(b'original installer')
            with PrivateFeed(path, 'windows', 'x64', '0.2.9') as server:
                with urllib.request.urlopen(server.base_url + '/update.json') as response:
                    metadata = json.load(response)['platforms']['windows']
                self.assertTrue(metadata['downloadUrl'].startswith('http://127.0.0.1:'))
                self.assertEqual(0, server.artifact_requests)
                with urllib.request.urlopen(metadata['downloadUrl']) as response:
                    self.assertEqual(path.read_bytes(), response.read())
                self.assertEqual(1, server.artifact_requests)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata['sha256'])

    def test_rejection_check_can_never_serve_an_installer(self):
        import urllib.error
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'Setup.exe'; path.write_bytes(b'never install')
            with PrivateFeed(path, 'windows', 'x64', '0.2.9') as server:
                server.artifact_downloads_enabled = False
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(server.metadata['downloadUrl'])
                self.assertEqual(503, caught.exception.code)
                self.assertEqual(1, server.artifact_requests)

    def test_private_feed_is_accepted_by_real_update_metadata_validation(self):
        import app_server
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'ClassInEDBMVP-Setup.exe'; path.write_bytes(b'original installer')
            with PrivateFeed(path, 'windows', 'x64', '0.2.9') as server:
                config = {'appId': 'ClassInEDBMVP', 'platform': 'windows', 'version': '0.2.8',
                          'updateFeedUrl': server.base_url + '/update.json', 'updatePinHash': pin_verifier('15382940')}
                with patch.object(app_server, 'load_app_update_config', return_value=config), patch.object(app_server, 'is_frozen_app', return_value=True), patch.object(app_server.platform, 'machine', return_value='AMD64'):
                    app_server.clear_app_update_status_cache()
                    status = app_server.build_app_update_status()
                self.assertTrue(status['automaticUpdateReady'], status)
                self.assertEqual('0.2.9', status['latest']['version'])
                self.assertEqual(0, server.artifact_requests)

    def test_macos_default_home_refuses_nondisposable_environment(self):
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted'}):
            with self.assertRaisesRegex(RuntimeError, 'disposable'):
                with owned_macos_home():
                    self.fail('must reject')

    def test_macos_never_adopts_or_deletes_existing_data(self):
        with TemporaryDirectory() as temp, patch('pathlib.Path.home', return_value=Path(temp)), patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            home = Path(temp) / 'Documents/ClassInEDBMVP'; home.mkdir(parents=True)
            data = home / 'keep'; data.write_text('user data')
            with self.assertRaises(FileExistsError):
                with owned_macos_home():
                    self.fail('must reject')
            self.assertEqual('user data', data.read_text())

    def test_macos_cleans_only_its_owned_new_home(self):
        with TemporaryDirectory() as temp, patch('pathlib.Path.home', return_value=Path(temp)), patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted'}):
            parent = Path(temp) / 'Documents'; parent.mkdir()
            keep = parent / 'keep'; keep.write_text('unrelated')
            with owned_macos_home() as home:
                (home / 'runtime').write_text('fixture')
            self.assertFalse(home.exists())
            self.assertEqual('unrelated', keep.read_text())

    def test_config_audit_reports_only_booleans_and_version(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); internal = root / '_internal'; internal.mkdir()
            pin = '65390428'; verifier = pin_verifier(pin)
            (internal / 'app_update_config.json').write_text(json.dumps({'version': '0.2.9', 'updatePinHash': verifier, 'updateFeedUrl': 'https://example.test/private'}))
            result = packaged_config_audit(root / 'App.exe', pin)
            self.assertTrue(result['matchesProvidedPinSecret'])
            self.assertFalse(packaged_config_audit(root / 'App.exe', '00000000')['matchesProvidedPinSecret'])
            self.assertNotIn(pin, json.dumps(result))
            self.assertNotIn(verifier, json.dumps(result))
            self.assertNotIn('https:', json.dumps(result))

    def test_saved_work_mutation_or_deletion_fails(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); workspace = root / 'work'; workspace.mkdir()
            file = workspace / 'saved.edb'; file.write_bytes(b'saved')
            snapshot = snapshot_files(root, workspace); verify_snapshot(snapshot)
            file.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError, 'changed or removed'):
                verify_snapshot(snapshot)
            file.unlink()
            with self.assertRaisesRegex(RuntimeError, 'changed or removed'):
                verify_snapshot(snapshot)


class NativeRestartConfirmationGuards(unittest.TestCase):
    def feed(self):
        return SimpleNamespace(artifact_requests=1, metadata={
            'version': '0.2.9', 'downloadUrl': 'http://127.0.0.1:9001/installer.exe',
            'sha256': 'a' * 64, 'sizeBytes': 123, 'arch': 'x64', 'artifactType': 'setup-exe'})

    def test_new_backend_must_refresh_and_reject_changed_confirmation(self):
        feed = self.feed()
        replies = [(200, {'automaticUpdateReady': True, 'latest': {'version': '0.2.10'}}),
                   (409, {'code': 'update_target_changed'}), (200, {'pid': 1234}), (200, {})]
        with patch('scripts.smoke_packaged_update.api', side_effect=replies) as request, patch('scripts.smoke_packaged_update.pid_alive', return_value=True), patch('scripts.smoke_packaged_update.time.sleep'):
            result = verify_changed_confirmation(feed, '12345678', 1234, '0.2.9')
        self.assertTrue(result['rejected'])
        self.assertEqual('/api/app/update?refresh=1', request.call_args_list[0].args[0])
        payload = request.call_args_list[1].args[1]
        self.assertEqual('12345678', payload['pin'])
        self.assertNotEqual('a' * 64, payload['expectedUpdate']['sha256'])
        self.assertEqual('0.2.9', feed.metadata['version'])
        self.assertEqual('/api/app/update?refresh=1', request.call_args_list[-1].args[0])

    def test_guard_rejects_false_failure_response_that_still_downloads(self):
        feed = self.feed()
        def request(path, payload=None):
            if path.endswith('refresh=1'):
                return 200, {'automaticUpdateReady': True, 'latest': {'version': feed.metadata['version']}}
            if path.endswith('/install'):
                feed.artifact_requests += 1
                return 409, {'code': 'update_target_changed'}
            return 200, {'pid': 1234}
        with patch('scripts.smoke_packaged_update.api', side_effect=request), patch('scripts.smoke_packaged_update.pid_alive', return_value=True), patch('scripts.smoke_packaged_update.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'downloaded an artifact'):
                verify_changed_confirmation(feed, '12345678', 1234, '0.2.9')
        self.assertEqual('0.2.9', feed.metadata['version'])

    def test_guard_rejects_missing_new_backend_confirmation_check(self):
        feed = self.feed()
        replies = [(200, {'automaticUpdateReady': True, 'latest': {'version': '0.2.10'}}),
                   (202, {'restartScheduled': True}), (200, {})]
        with patch('scripts.smoke_packaged_update.api', side_effect=replies), patch('scripts.smoke_packaged_update.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'accepted a changed update confirmation'):
                verify_changed_confirmation(feed, '12345678', 1234, '0.2.9')
        self.assertEqual('0.2.9', feed.metadata['version'])

    def test_dmg_mount_copy_detach_are_bounded_even_when_copy_fails(self):
        import subprocess
        with TemporaryDirectory() as temp:
            root = Path(temp); mount = root / 'mount'; destination = root / 'installed'; destination.mkdir()
            def run(command, **kwargs):
                self.assertEqual(120, kwargs['timeout'])
                if command[0].endswith('hdiutil') and command[1] == 'attach':
                    (mount / 'ClassInEDBMVP.app').mkdir()
                if command[0].endswith('ditto'):
                    raise subprocess.TimeoutExpired(command, 120)
            with patch('scripts.smoke_packaged_update.subprocess.run', side_effect=run) as call:
                with self.assertRaises(subprocess.TimeoutExpired):
                    copy_from_dmg(root / 'original.dmg', destination, mount)
            self.assertEqual('detach', call.call_args.args[0][1])


class NativeOldProcessReaping(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX zombie reaping regression')
    def test_old_direct_child_is_reaped_while_waiting_for_native_restart(self):
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        def request(path, **_kwargs):
            # Model the native helper: the new server cannot start until the
            # old direct child is reaped and kill -0 stops seeing its PID.
            reaped = child.returncode is not None
            if path == '/api/health':
                return 200, {'ok': True, 'pid': child.pid + 1 if reaped else child.pid}
            return 200, {'currentVersion': '0.2.9' if reaped else '0.2.8'}
        try:
            with patch('scripts.smoke_packaged_update.api', side_effect=request):
                health, status = wait_for_ready('0.2.9', previous_pid=child.pid, previous_process=child, timeout=3)
            self.assertEqual(0, child.returncode)
            self.assertNotEqual(child.pid, health['pid'])
            self.assertEqual('0.2.9', status['currentVersion'])
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        finally:
            child.wait(timeout=3)


def test_packaged_source_commit_comes_from_embedded_provenance(tmp_path):
    internal = tmp_path / "_internal"
    metadata = internal / "release_metadata"
    metadata.mkdir(parents=True)
    (internal / "app_update_config.json").write_text('{"version":"0.2.9"}')
    embedded = "a" * 40
    (metadata / "release-provenance.json").write_text(json.dumps({"gitCommit": embedded}))
    audit = packaged_config_audit(tmp_path / "App.exe")
    assert audit["version"] == "0.2.9"
    assert audit["gitCommit"] == embedded
