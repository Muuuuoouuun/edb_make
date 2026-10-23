"""Read-only Windows updater host/environment probes; no release modification."""
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

PINNED_SHA = '1c6e85a9a4be4640cd0490901754db9ec1938d0eca0ab53d0bfe6b91f73a9b53'
BASELINE_SOURCE = 'd5a3f37d984047875ae32bff22967ff625a4bfe2'


def run(command, env=None, timeout=30):
    try:
        p = subprocess.run(command, env=env, capture_output=True, timeout=timeout)
        return {'exit': p.returncode, 'stdout': p.stdout.decode('utf-8', 'replace')[-12000:], 'stderr': p.stderr.decode('utf-8', 'replace')[-12000:]}
    except subprocess.TimeoutExpired as error:
        return {'exit': 'timeout', 'stdout': str(error.stdout)[-2000:], 'stderr': str(error.stderr)[-2000:]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('installer', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Native Windows only')
    if hashlib.file_digest(args.installer.open('rb'), 'sha256').hexdigest() != PINNED_SHA:
        raise RuntimeError('Baseline checksum mismatch')
    system_root = Path(os.environ['SYSTEMROOT'])
    host = system_root / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    if not host.is_file():
        raise RuntimeError('Real OS Windows PowerShell5 host missing')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    source = subprocess.check_output(['git', 'show', BASELINE_SOURCE + ':app_server.py'], encoding='utf-8')
    helper_source = next(ast.literal_eval(node.value) for node in ast.parse(source).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'WINDOWS_UPDATE_HELPER' for target in node.targets))
    results = []
    with tempfile.TemporaryDirectory(prefix='edb-host-debug-') as raw:
        root = Path(raw); installed = root / 'installed baseline'; empty = root / 'empty PATH'; empty.mkdir()
        installation = run([str(args.installer.resolve()), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/DIR=' + str(installed)], timeout=120)
        if installation['exit'] != 0:
            raise RuntimeError('Baseline installation failed: ' + str(installation['exit']))
        dll_directory = installed / '_internal'
        if not dll_directory.is_dir():
            raise RuntimeError('Installed baseline _internal missing')
        helper = root / 'old-helper.ps1'; helper.write_text(helper_source, encoding='utf-8-sig')
        fixture = root / 'fixture.cs'
        fixture.write_text('using System; using System.IO; using System.Diagnostics; class Fixture { static int Main(string[] args) { File.AppendAllText(Environment.GetEnvironmentVariable("EDB_DEBUG_CALLS"), Path.GetFileName(Process.GetCurrentProcess().MainModule.FileName)+"\\n"); return 0; } }')
        compiler = system_root / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        installer_fixture = root / 'dummy-installer.exe'; app_fixture = root / 'dummy-app.exe'
        compiled = run([str(compiler), '/nologo', '/target:exe', '/out:' + str(installer_fixture), str(fixture)])
        if compiled['exit'] == 0:
            shutil.copyfile(installer_fixture, app_fixture)
        command = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); [pscustomobject]@{Version=$PSVersionTable.PSVersion.ToString(); GetProcess=(Get-Command Get-Process -ErrorAction Stop).Source; StartProcess=(Get-Command Start-Process -ErrorAction Stop).Source} | ConvertTo-Json -Compress"
        for isolated in (False, True):
            for inherit_dll in (False, True):
                for sanitize_modules in (False, True):
                    environment = {key.upper(): value for key, value in os.environ.items()}
                    if isolated:
                        environment['PATH'] = str(empty) + ';' + str(system_root / 'System32') + ';' + str(host.parent)
                        for key in ('VIRTUAL_ENV', 'PYTHONHOME', 'PYTHONPATH', 'EDB_PYMUPDF_PYTHON', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
                            environment.pop(key, None)
                    if sanitize_modules:
                        environment.pop('PSMODULEPATH', None); environment.pop('WINPSMODULEPATH', None)
                    calls = root / f'calls-{len(results)}.txt'; environment['EDB_DEBUG_CALLS'] = str(calls)
                    if not kernel.SetDllDirectoryW(str(dll_directory) if inherit_dll else None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    try:
                        probe = run([str(host), '-NoProfile', '-NonInteractive', '-Command', command], env=environment)
                        helper_result = None
                        if compiled['exit'] == 0:
                            helper_result = run([str(host), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-ParentPid', '99999999', '-Installer', str(installer_fixture), '-AppExecutable', str(app_fixture), '-InstallDir', str(root), '-LogFile', str(root / 'helper.log')], env=environment)
                            # Old helper always deletes its pending installer.
                            if not installer_fixture.exists():
                                shutil.copyfile(app_fixture, installer_fixture)
                            time.sleep(0.25)
                            helper_result['fixtureCalls'] = calls.read_text() if calls.exists() else ''
                        results.append({'isolatedPath': isolated, 'inheritedDllDirectory': inherit_dll, 'sanitizedModulePaths': sanitize_modules,
                                        'selectedEnvironment': {key: environment.get(key) for key in ('PATH', 'PSMODULEPATH', 'WINPSMODULEPATH')},
                                        'probe': probe, 'oldHelper': helper_result})
                    finally:
                        kernel.SetDllDirectoryW(None)
        report = {'baselineSha256': PINNED_SHA, 'baselineSource': BASELINE_SOURCE, 'powershellHost': str(host), 'fixtureCompiler': compiled, 'results': results}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        for result in results:
            print(json.dumps({key: result[key] for key in ('isolatedPath', 'inheritedDllDirectory', 'sanitizedModulePaths', 'probe', 'oldHelper')}))


if __name__ == '__main__':
    main()
