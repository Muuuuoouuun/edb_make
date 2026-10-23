"""Execute detached-updater failure branches in real PowerShell when available."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app_server


@unittest.skipUnless(shutil.which('pwsh') or shutil.which('powershell'), 'PowerShell unavailable')
class WindowsHelperDiagnostics(unittest.TestCase):
    def run_helper(self, root, *, missing=False, installer_code=0, restart_error=False, parent_pid=99999999):
        installer = root / 'Setup.exe'; installer.write_bytes(b'installer')
        executable = root / 'App.exe'; executable.write_bytes(b'old app')
        helper = root / 'helper.ps1'
        helper.write_text(app_server.WINDOWS_UPDATE_HELPER.replace('WaitForExit(120000)', 'WaitForExit(25)'), encoding='utf-8-sig')
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        restart = "throw 'restart fixture failure'" if restart_error else 'return'
        remove = f'Remove-Item -LiteralPath {quote(executable)}' if missing else ''
        command = f'''
function Start-Process {{
 param($FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru, $ErrorAction)
 Add-Content -LiteralPath {quote(root / 'calls')} -Value $FilePath
 if ($PassThru) {{ {remove}; return [pscustomobject]@{{ ExitCode = {installer_code} }} }}
 {restart}
}}
& {quote(helper)} -ParentPid {parent_pid} -Installer {quote(installer)} -AppExecutable {quote(executable)} -InstallDir {quote(root)} -LogFile {quote(root / 'helper.log')}
'''
        result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'), '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True, timeout=15)
        return result, installer, (root / 'helper.log').read_text(encoding='utf-8-sig'), (root / 'calls').read_text().splitlines() if (root / 'calls').exists() else []

    def test_missing_target_is_failure_logged_and_never_restarted(self):
        with TemporaryDirectory(prefix='edb update ') as temp:
            result, installer, log, calls = self.run_helper(Path(temp), missing=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('updated executable is missing', log)
            self.assertFalse(installer.exists())
            self.assertEqual([str(installer)], calls)

    def test_installer_and_restart_failures_are_persisted(self):
        for kwargs, message in [({'installer_code': 42}, 'code 42'), ({'restart_error': True}, 'restart fixture failure')]:
            with self.subTest(kwargs=kwargs), TemporaryDirectory() as temp:
                result, installer, log, calls = self.run_helper(Path(temp), **kwargs)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, log)
                self.assertFalse(installer.exists())
                self.assertNotIn('restart requested', log)
                if kwargs.get('installer_code'):
                    self.assertEqual([str(installer)], calls)

    def test_timeout_logs_failure_without_stopping_parent_or_installing(self):
        parent = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
        try:
            with TemporaryDirectory() as temp:
                result, installer, log, calls = self.run_helper(Path(temp), parent_pid=parent.pid)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('update cancelled', log)
                self.assertEqual([], calls)
                self.assertFalse(installer.exists())
                self.assertIsNone(parent.poll())
        finally:
            parent.terminate(); parent.wait(timeout=5)

    def test_success_and_reboot_required_exit_restart_once(self):
        for code in (0, 3010):
            with self.subTest(code=code), TemporaryDirectory() as temp:
                root = Path(temp)
                result, installer, log, calls = self.run_helper(root, installer_code=code)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual([str(installer), str(root / 'App.exe')], calls)
                self.assertIn('restart requested', log)
                self.assertFalse(installer.exists())


class WindowsHelperLaunchLogging(unittest.TestCase):
    def test_detached_process_output_is_persistent_and_stream_closes(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); executable = root / 'App.exe'; executable.write_bytes(b'app')
            artifact = root / 'Setup.exe'; artifact.write_bytes(b'setup')
            captured = {}
            def launch(command, **kwargs):
                captured.update(kwargs)
                kwargs['stdout'].write(b'PowerShell startup failure\n')
            with patch.object(app_server, 'is_frozen_app', return_value=True), patch.object(app_server.sys, 'executable', str(executable)), patch.object(app_server.subprocess, 'Popen', side_effect=launch):
                result = app_server._launch_windows_update_helper(artifact, 'setup-exe')
            self.assertTrue(result['restartScheduled'])
            self.assertTrue(captured['stdout'].closed)
            self.assertEqual(subprocess.STDOUT, captured['stderr'])
            self.assertIn(b'startup failure', (root / 'update-helper-process.log').read_bytes())
