#!/usr/bin/env python3
"""Verify an actual pinned installer -> PIN/API updater -> restart transition.

Only private loopback feed/configuration is synthesized; installer bytes remain
unchanged. macOS requires a disposable GitHub-hosted runner because LaunchServices
uses the app's default Documents home rather than inheriting a shell environment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.smoke_packaged_app import _ProcessTreeGuard, _isolated_smoke_environment, _smoke_edb_export


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def require_artifact(path, expected):
    path = Path(path).resolve()
    if not path.is_file() or len(expected) != 64 or sha256(path) != expected:
        raise RuntimeError('Installer does not match its pinned SHA256')
    return path


def pin_verifier(pin):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', pin.encode('ascii'), salt, 210000)
    return f'pbkdf2_sha256$210000${salt.hex()}${digest.hex()}'


def packaged_config_audit(executable, expected_pin=None):
    executable = Path(executable)
    candidates = [executable.parent / '_internal/app_update_config.json']
    if executable.parent.name == 'MacOS':
        candidates = [executable.parent.parent / 'Resources/app_update_config.json', executable.parent.parent / 'Frameworks/app_update_config.json']
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        raise RuntimeError('Packaged update configuration is missing')
    config = json.loads(existing[0].read_text(encoding='utf-8-sig'))
    verifier = str(config.get('updatePinHash') or '')
    result = {'version': config.get('version'), 'pinConfigured': bool(verifier),
              'feedConfigured': bool(config.get('updateFeedUrl')), 'downloadConfigured': bool(config.get('downloadUrl'))}
    provenance = existing[0].parent / "release_metadata/release-provenance.json"
    result["gitCommit"] = json.loads(provenance.read_text(encoding="utf-8-sig")).get("gitCommit", "") if provenance.is_file() else ""
    if expected_pin is not None:
        matched = False
        try:
            kind, rounds, salt, digest = verifier.split('$')
            matched = kind == 'pbkdf2_sha256' and secrets.compare_digest(hashlib.pbkdf2_hmac('sha256', expected_pin.encode('ascii'), bytes.fromhex(salt), int(rounds)).hex(), digest)
        except (ValueError, UnicodeError):
            pass
        result['matchesProvidedPinSecret'] = matched
    return result


@contextmanager
def owned_macos_home():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise RuntimeError('macOS default-home smoke requires a disposable GitHub-hosted runner')
    home = Path.home() / 'Documents/ClassInEDBMVP'
    # mkdir(exist_ok=False) rejects symlinks and any pre-existing user data.
    home.mkdir(parents=True, exist_ok=False)
    marker = home / '.owned-updater-smoke'
    token = secrets.token_hex(24)
    marker.write_text(token)
    try:
        yield home
    finally:
        if marker.is_file() and not marker.is_symlink() and marker.read_text() == token and not home.is_symlink():
            shutil.rmtree(home)
        else:
            raise RuntimeError('Refusing cleanup: disposable app-home ownership changed')


class PrivateFeed:
    def __init__(self, artifact, platform, arch, version):
        self.artifact = Path(artifact)
        self.artifact_requests = 0
        self.artifact_downloads_enabled = True
        self.feed_requests = 0
        self.metadata = {'version': version, 'fileName': 'installer' + self.artifact.suffix,
                         'artifactType': 'setup-exe' if platform == 'windows' else 'dmg',
                         'arch': arch, 'sha256': sha256(self.artifact), 'sizeBytes': self.artifact.stat().st_size}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                if self.path == '/update.json':
                    owner.feed_requests += 1
                    data = json.dumps({'appId': 'ClassInEDBMVP', 'platforms': {platform: owner.metadata}}).encode()
                    self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                elif self.path == '/installer' + owner.artifact.suffix:
                    owner.artifact_requests += 1
                    if not owner.artifact_downloads_enabled:
                        self.send_error(503, 'Artifact disabled during rejection verification')
                        return
                    self.send_response(200); self.send_header('Content-Length', str(owner.artifact.stat().st_size)); self.end_headers()
                    with owner.artifact.open('rb') as stream:
                        shutil.copyfileobj(stream, self.wfile)
                else:
                    self.send_error(404)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base_url = f'http://127.0.0.1:{self.server.server_port}'
        self.metadata['downloadUrl'] = self.base_url + '/installer' + self.artifact.suffix
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    def __enter__(self):
        self.thread.start(); return self
    def __exit__(self, *_args):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=5)


def api(path, payload=None, *, timeout=30):
    base = 'http://127.0.0.1:8765'
    request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                                    headers={'Origin': base, 'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def wait_for_ready(version, *, previous_pid=None, previous_process=None, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The old macOS updater waits with kill -0. Reap our direct child as
        # soon as it exits so its zombie PID cannot block the native helper.
        if previous_process is not None:
            previous_process.poll()
        try:
            _, health = api('/api/health', timeout=2)
            _, status = api('/api/app/update', timeout=4)
            if health.get('ok') and status.get('currentVersion') == version and (previous_pid is None or health.get('pid') != previous_pid):
                return health, status
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f'Native app did not become healthy at expected version {version}')


def pid_alive(pid):
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            return code.value == 259
        finally:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0); return True
    except ProcessLookupError:
        return False


def copy_from_dmg(artifact, destination, mountpoint):
    mountpoint.mkdir()
    attached = False
    try:
        subprocess.run(['/usr/bin/hdiutil', 'attach', str(artifact), '-nobrowse', '-readonly', '-mountpoint', str(mountpoint)], check=True, capture_output=True, timeout=120)
        attached = True
        apps = list(mountpoint.glob('*.app'))
        if len(apps) != 1:
            raise RuntimeError('Expected exactly one application in baseline DMG')
        target = destination / apps[0].name
        subprocess.run(['/usr/bin/ditto', str(apps[0]), str(target)], check=True, timeout=120)
        executable = target / 'Contents/MacOS/ClassInEDBMVP'
        if not executable.is_file():
            raise RuntimeError('Baseline DMG app executable is missing')
        return executable
    finally:
        if attached or os.path.ismount(mountpoint):
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mountpoint)], check=True, capture_output=True, timeout=120)


def make_pdf(path):
    import pymupdf
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=800)
        page.insert_text((60, 120), 'Updater persistence PDF page', fontsize=16)
        document.save(path)


def snapshot_files(app_home, workspace):
    files = [app_home / '.app_runtime/user_settings.json', app_home / '.app_runtime/latest_session.json']
    files.extend(path for path in workspace.rglob('*') if path.is_file())
    return {str(path): sha256(path) for path in files if path.is_file()}


def verify_snapshot(snapshot):
    if not snapshot:
        raise RuntimeError('Missing persistence fixture')
    for raw, digest in snapshot.items():
        path = Path(raw)
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError('Updater changed or removed saved settings/session/work files')



def verify_changed_confirmation(feed, pin, current_pid, current_version):
    """Prove the new installed backend rejects stale confirmation before download."""
    previous_version = feed.metadata['version']
    components = current_version.split('.')
    components[-1] = str(int(components[-1]) + 1)
    advertised_version = '.'.join(components)
    before_downloads = feed.artifact_requests
    downloads_were_enabled = getattr(feed, 'artifact_downloads_enabled', True)
    # Even a broken rejection guard cannot install the advertised test version.
    feed.artifact_downloads_enabled = False
    feed.metadata['version'] = advertised_version
    try:
        code, status = api('/api/app/update?refresh=1')
        if code != 200 or not status.get('automaticUpdateReady') or status.get('latest', {}).get('version') != advertised_version:
            raise RuntimeError('Installed backend did not refresh the private update feed')
        expected = {key: feed.metadata[key] for key in ('version', 'downloadUrl', 'sha256', 'sizeBytes', 'arch', 'artifactType')}
        # A valid PIN must not authorize an artifact the caller never confirmed.
        expected['sha256'] = ('0' if expected['sha256'][0] != '0' else '1') + expected['sha256'][1:]
        code, rejected = api('/api/app/update/install', {'pin': pin, 'expectedUpdate': expected})
        if code != 409 or rejected.get('code') != 'update_target_changed':
            raise RuntimeError('Installed backend accepted a changed update confirmation')
        time.sleep(0.75)
        _, health = api('/api/health')
        if health.get('pid') != current_pid or not pid_alive(current_pid) or feed.artifact_requests != before_downloads:
            raise RuntimeError('Rejected changed confirmation downloaded an artifact or stopped the app')
        return {'advertisedVersion': advertised_version, 'rejected': True, 'artifactDownloaded': False, 'appStopped': False}
    finally:
        feed.artifact_downloads_enabled = downloads_were_enabled
        feed.metadata['version'] = previous_version
        api('/api/app/update?refresh=1')


def run_transition(args, temp, app_home):
    baseline = require_artifact(args.baseline_artifact, args.baseline_sha256)
    target = require_artifact(args.target_artifact, args.target_sha256)
    empty_path = temp / 'no external Python'; empty_path.mkdir()
    install_dir = temp / '설치 경로 with spaces'; install_dir.mkdir()
    workspace = app_home / 'saved work 한글'; workspace.mkdir(parents=True)
    environment = _isolated_smoke_environment(app_home, empty_path)
    # Packaged versions are never overridden: version proof comes from resources.
    for key in list(environment):
        if key.startswith('EDB_') and key != 'EDB_APP_HOME':
            del environment[key]
    if args.platform == 'windows':
        # Baseline 0.2.8 invokes bare powershell.exe; include only the OS host,
        # keeping external Python/venvs absent from the packaged app's PATH.
        system_root = environment.get('SYSTEMROOT') or environment.get('SystemRoot')
        if not system_root:
            raise RuntimeError('Windows SystemRoot is missing')
        environment['PATH'] += ';' + str(Path(system_root) / 'System32/WindowsPowerShell/v1.0')
    if args.platform == 'macos':
        environment.pop('EDB_APP_HOME', None)
        executable = copy_from_dmg(baseline, install_dir, temp / 'baseline mount')
    else:
        subprocess.run([str(baseline), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS', '/DIR=' + str(install_dir)], check=True, timeout=180)
        executable = install_dir / 'ClassInEDBMVP.exe'
    original_audit = packaged_config_audit(executable, os.environ.get(args.expected_pin_env) if args.expected_pin_env else None)
    if original_audit['version'] != args.baseline_version:
        raise RuntimeError('Baseline package resource version does not match expectation')
    expected_baseline_commit = getattr(args, "baseline_source_commit", "")
    if expected_baseline_commit and original_audit.get("gitCommit") != expected_baseline_commit:
        raise RuntimeError("Baseline embedded source commit does not match expectation")
    # Runtime override is limited to feed/verifier, never application version.
    pin = ''.join(str(secrets.randbelow(10)) for _ in range(8))
    wrong_pin = ('1' if pin[0] != '1' else '2') + pin[1:]
    process_tree = None
    new_pid = None
    verifier = pin_verifier(pin)
    with PrivateFeed(target, args.platform, args.arch, args.target_version) as feed:
        config = app_home / 'app_update_config.json'
        config.write_text(json.dumps({'updateFeedUrl': feed.base_url + '/update.json', 'updatePinHash': verifier}))
        with (temp / 'packaged-app.log').open('wb') as log:
            process_tree = _ProcessTreeGuard.launch([str(executable), '--host', '127.0.0.1', '--port', '8765', '--no-open-browser'], cwd=install_dir, env=environment, stdout=log)
            try:
                old_health, status = wait_for_ready(args.baseline_version)
                old_pid = int(old_health['pid'])
                if not status.get('automaticUpdateReady') or status.get('latest', {}).get('version') != args.target_version:
                    raise RuntimeError('Private update is not available/ready')
                settings_code, settings = api('/api/user-settings', {'aiEnabled': False})
                if settings_code != 200 or settings.get('settings', {}).get('aiEnabled') is not False:
                    raise RuntimeError('Could not persist a valid AI-disabled setting')
                pdf = workspace / 'saved PDF.pdf'; make_pdf(pdf)
                before_export = _smoke_edb_export('http://127.0.0.1:8765', pdf, workspace / 'before update', expected_records=1)
                _, before_session = api('/api/session/latest')
                before_problem_ids = [item.get('id') for item in before_session.get('session', {}).get('problems', [])]
                if not before_problem_ids or not all(before_problem_ids):
                    raise RuntimeError('Baseline active session has no persistent problem identifiers')
                snapshot = snapshot_files(app_home, workspace)
                required = app_home / '.app_runtime/latest_session.json'
                if str(required) not in snapshot or str(app_home / '.app_runtime/user_settings.json') not in snapshot:
                    raise RuntimeError('Valid session/settings files were not persisted before updating')
                expected = {key: feed.metadata[key] for key in ('version', 'downloadUrl', 'sha256', 'sizeBytes', 'arch', 'artifactType')}
                code, rejected = api('/api/app/update/install', {'pin': wrong_pin, 'expectedUpdate': expected})
                if code != 403 or rejected.get('code') != 'invalid_update_pin':
                    raise RuntimeError('Incorrect PIN was not rejected with invalid_update_pin')
                time.sleep(0.75)
                _, alive = api('/api/health')
                if feed.artifact_requests != 0 or alive.get('pid') != old_pid or not pid_alive(old_pid):
                    raise RuntimeError('Incorrect PIN downloaded an installer or stopped the app')
                code, accepted = api('/api/app/update/install', {'pin': pin, 'expectedUpdate': expected}, timeout=180)
                if code != 202 or not accepted.get('restartScheduled'):
                    raise RuntimeError('Correct PIN did not schedule native installation/restart')
                new_health, new_status = wait_for_ready(args.target_version, previous_pid=old_pid, previous_process=process_tree.process, timeout=240)
                new_pid = int(new_health['pid'])
                deadline = time.monotonic() + 15
                while pid_alive(old_pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                if pid_alive(old_pid):
                    raise RuntimeError('Previous native application process survived update')
                process_tree.process.wait(timeout=15)
                verify_snapshot(snapshot)
                _, after_settings = api('/api/user-settings')
                _, after_session = api('/api/session/latest')
                if after_settings.get('settings', {}).get('aiEnabled') is not False:
                    raise RuntimeError('Saved setting did not survive restart')
                after_problem_ids = [item.get('id') for item in after_session.get('session', {}).get('problems', [])]
                if before_problem_ids != after_problem_ids:
                    raise RuntimeError('Saved active session did not survive restart')
                updated_audit = packaged_config_audit(executable, os.environ.get(args.expected_pin_env) if args.expected_pin_env else None)
                if getattr(args, "target_source_commit", "") and updated_audit.get("gitCommit") != args.target_source_commit:
                    raise RuntimeError("Target embedded source commit does not match expectation")
                if updated_audit['version'] != args.target_version:
                    raise RuntimeError('Installed package resource version is incorrect')
                after_export = _smoke_edb_export('http://127.0.0.1:8765', pdf, workspace / 'after update', expected_records=1)
                if feed.artifact_requests != 1:
                    raise RuntimeError('Successful native update did not use exactly one verified artifact download')
                confirmation_check = verify_changed_confirmation(feed, pin, new_pid, args.target_version)
                return {'platform': args.platform, 'arch': args.arch, 'baselineVersion': args.baseline_version, 'targetVersion': args.target_version,
                        'baselineSha256': args.baseline_sha256, 'targetSha256': args.target_sha256,
                        'baselineKind': getattr(args, 'baseline_kind', 'original-0.2.8'),
                        'baselineSourceCommit': original_audit.get('gitCommit', ''), 'targetSourceCommit': updated_audit.get('gitCommit', ''),
                        'unmodifiedInstallerBytes': True, 'privateLoopbackFeed': True, 'ephemeralPinVerifier': True,
                        'wrongPinRejected': True, 'wrongPinDownloadedArtifact': False, 'wrongPinStoppedApp': False,
                        'artifactDownloads': feed.artifact_requests, 'realInstallApiAccepted': True, 'oldPid': old_pid, 'newPid': new_pid,
                        'oldProcessExited': True, 'newProcessHealthy': True, 'savedFilesPreserved': len(snapshot),
                        'settingsPreserved': True, 'sessionPreserved': True, 'changedConfirmationCheck': confirmation_check, 'baselinePackagedConfig': original_audit,
                        'targetPackagedConfig': updated_audit, 'beforeUpdatePdfExport': before_export, 'afterUpdatePdfExport': after_export}
            finally:
                try:
                    _, cleanup_health = api('/api/health', timeout=2)
                    new_pid = int(cleanup_health['pid'])
                except (OSError, ValueError, KeyError, urllib.error.URLError):
                    pass
                try:
                    api('/api/system/shutdown', {}, timeout=3)
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                if new_pid:
                    deadline = time.monotonic() + 15
                    while pid_alive(new_pid) and time.monotonic() < deadline:
                        time.sleep(0.1)
                    if pid_alive(new_pid):
                        if args.platform == 'macos':
                            os.kill(new_pid, 15)
                            stop_deadline = time.monotonic() + 5
                            while pid_alive(new_pid) and time.monotonic() < stop_deadline:
                                time.sleep(0.1)
                            if pid_alive(new_pid):
                                os.kill(new_pid, 9)
                        else:
                            subprocess.run(['taskkill', '/PID', str(new_pid), '/T', '/F'], capture_output=True, timeout=15)
                process_tree.terminate(timeout=5)
                process_tree.close()
                # Preserve diagnostic files before disposable homes are removed.
                evidence = args.output.parent / (args.output.stem + '-logs')
                evidence.mkdir(parents=True, exist_ok=True)
                logs = [temp / 'packaged-app.log', app_home / '.app_runtime/app.log'] + list((app_home / '.app_runtime/updates').rglob('*.log'))
                for index, source in enumerate(logs):
                    if source.is_file():
                        text = source.read_text(encoding='utf-8', errors='replace')[-65536:]
                        text = text.replace(verifier, '[ephemeral verifier redacted]').replace(pin, '[ephemeral PIN redacted]')
                        (evidence / f'{index}-{source.name}').write_text(text, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('windows', 'macos'), required=True)
    parser.add_argument('--arch', choices=('x64', 'arm64'), required=True)
    for name in ('baseline-artifact', 'target-artifact', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('baseline-sha256', 'target-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--baseline-version', default='0.2.8')
    parser.add_argument('--target-version', default='0.2.10')
    parser.add_argument('--baseline-kind', choices=('original-0.2.8', 'fixed-updater-test-bootstrap'), default='original-0.2.8')
    parser.add_argument('--baseline-source-commit', default='')
    parser.add_argument('--target-source-commit', default='')
    parser.add_argument('--expected-pin-env', help='Optional environment variable containing the packaged PIN secret; never printed')
    args = parser.parse_args(argv)
    if (args.platform == 'windows') != (os.name == 'nt') or (args.platform == 'macos' and sys.platform != 'darwin'):
        raise RuntimeError('Native update smoke must run on its target OS')
    if args.baseline_kind == "fixed-updater-test-bootstrap" and (args.platform != "windows" or not args.baseline_source_commit or not args.target_source_commit):
        raise RuntimeError("A fixed-updater bootstrap must identify its Windows source commit")
    for value in (args.baseline_source_commit, args.target_source_commit):
        if value and (len(value) != 40 or any(char not in "0123456789abcdef" for char in value)):
            raise RuntimeError("Expected source commits must be full lowercase Git SHAs")
    # Never attach to an unrelated app already occupying the default update port.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8765))
    with tempfile.TemporaryDirectory(prefix='edb-real-update-') as raw:
        temp = Path(raw)
        if args.platform == 'macos':
            with owned_macos_home() as app_home:
                result = run_transition(args, temp, app_home)
        else:
            app_home = temp / '사용자 데이터'; app_home.mkdir()
            result = run_transition(args, temp, app_home)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print('[packaged-update-smoke] OK: ' + json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
