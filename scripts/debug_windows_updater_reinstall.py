"""Diagnostic only: original 0.2.8 API reinstalls identical 0.2.8 bytes advertised as 0.2.9."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.smoke_packaged_app import _ProcessTreeGuard, _isolated_smoke_environment
from scripts.debug_windows_updater_host import PINNED_SHA, run

MONITOR = r'''
param([string]$Output, [string]$Ready, [string]$Stop)
$ErrorActionPreference = 'Stop'
Register-WmiEvent -Class Win32_ProcessStartTrace -SourceIdentifier EdbStart | Out-Null
Register-WmiEvent -Class Win32_ProcessStopTrace -SourceIdentifier EdbStop | Out-Null
Set-Content -LiteralPath $Ready -Value 'ready'
try {
 while (-not (Test-Path -LiteralPath $Stop)) {
  Wait-Event -Timeout 1 | Out-Null
  foreach ($event in @(Get-Event)) {
   $process = $event.SourceEventArgs.NewEvent
   $name = [string]$process.ProcessName
   if ($name -match '^(powershell|pwsh|ClassInEDBMVP|installer|ClassInEDBMVP-Setup)\.exe$' -or $name -like '*.tmp') {
    $item = [ordered]@{Event=$event.SourceIdentifier; ProcessName=$name; ProcessId=[int]$process.ProcessID; ParentProcessId=[int]$process.ParentProcessID; Time=(Get-Date).ToUniversalTime().ToString('o')}
    if ($event.SourceIdentifier -eq 'EdbStop') { $item.ExitStatus = [uint32]$process.ExitStatus }
    if ($event.SourceIdentifier -eq 'EdbStart') {
     $live = Get-WmiObject Win32_Process -Filter ('ProcessId=' + $process.ProcessID) -ErrorAction SilentlyContinue
     if ($live) { $item.ExecutablePath=$live.ExecutablePath; $item.CommandLine=$live.CommandLine }
    }
    $item | ConvertTo-Json -Compress | Add-Content -LiteralPath $Output -Encoding UTF8
   }
   Remove-Event -EventIdentifier $event.EventIdentifier
  }
 }
} finally { Unregister-Event EdbStart -ErrorAction SilentlyContinue; Unregister-Event EdbStop -ErrorAction SilentlyContinue }
'''


def api(path, payload=None, timeout=5):
    base = 'http://127.0.0.1:8765'
    request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers={'Origin': base, 'Content-Type': 'application/json'})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('installer', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    installer = args.installer.resolve()
    if hashlib.file_digest(installer.open('rb'), 'sha256').hexdigest() != PINNED_SHA:
        raise RuntimeError('Pinned baseline hash mismatch')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8765))
    evidence = args.output.parent; evidence.mkdir(parents=True, exist_ok=True)
    host = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    report = {'diagnosticOnly': True, 'actualInstallerVersion': '0.2.8', 'advertisedFeedVersion': '0.2.9', 'samePinnedInstallerBytes': PINNED_SHA, 'polls': []}
    pin = ''.join(str(secrets.randbelow(10)) for _ in range(8)); salt = secrets.token_bytes(16)
    verifier = 'pbkdf2_sha256$210000$' + salt.hex() + '$' + hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 210000).hex()
    with tempfile.TemporaryDirectory(prefix='edb-api-diagnostic-') as raw:
        root = Path(raw); installed = root / '설치 경로 with spaces'; app_home = root / '사용자 데이터'; app_home.mkdir()
        empty = root / 'empty PATH'; empty.mkdir()
        installation = run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/DIR=' + str(installed)], timeout=120)
        if installation['exit'] != 0: raise RuntimeError('Baseline install failed')
        executable = installed / 'ClassInEDBMVP.exe'
        artifact_requests = []
        class Feed(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_GET(self):
                if self.path == '/update.json':
                    data = json.dumps({'appId': 'ClassInEDBMVP', 'platforms': {'windows': {'version': '0.2.9', 'arch': 'x64', 'downloadUrl': f'http://127.0.0.1:{self.server.server_port}/installer.exe', 'fileName': 'installer.exe', 'artifactType': 'setup-exe', 'sha256': PINNED_SHA, 'sizeBytes': installer.stat().st_size}}}).encode()
                    self.send_response(200); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                elif self.path == '/installer.exe':
                    artifact_requests.append(time.monotonic())
                    self.send_response(200); self.send_header('Content-Length',str(installer.stat().st_size)); self.end_headers()
                    with installer.open('rb') as stream: shutil.copyfileobj(stream,self.wfile)
                else: self.send_error(404)
        server = ThreadingHTTPServer(('127.0.0.1',0),Feed); thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        (app_home / 'app_update_config.json').write_text(json.dumps({'updateFeedUrl': f'http://127.0.0.1:{server.server_port}/update.json','updatePinHash':verifier}))
        environment = _isolated_smoke_environment(app_home,empty)
        for key in list(environment):
            if key.startswith('EDB_') and key != 'EDB_APP_HOME': environment.pop(key)
        environment['PATH'] += ';' + str(host.parent)
        monitor_script=root/'monitor.ps1'; monitor_script.write_text(MONITOR,encoding='utf-8-sig')
        ready=root/'monitor.ready'; stop=root/'monitor.stop'; traces=evidence/'process-traces.jsonl'
        monitor_log=(evidence/'process-monitor.log').open('wb')
        monitor = subprocess.Popen([str(host),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(monitor_script),'-Output',str(traces.resolve()),'-Ready',str(ready),'-Stop',str(stop)],stdout=monitor_log,stderr=subprocess.STDOUT)
        process_tree=None
        try:
            deadline=time.monotonic()+15
            while not ready.exists() and monitor.poll() is None and time.monotonic()<deadline:time.sleep(.1)
            if not ready.exists():raise RuntimeError('WMI monitor did not become ready')
            with (evidence/'captured-stdout.log').open('wb') as stdout:
                process_tree=_ProcessTreeGuard.launch([str(executable),'--host','127.0.0.1','--port','8765','--no-open-browser'],cwd=installed,env=environment,stdout=stdout)
                deadline=time.monotonic()+45; health=None
                while time.monotonic()<deadline:
                    try:
                        _,health=api('/api/health');break
                    except (OSError,ValueError):time.sleep(.2)
                if not health:raise RuntimeError('Baseline app startup failed')
                old_pid=health['pid'];report['oldHealth']=health
                _,status=api('/api/app/update');report['beforeStatus']={key:status.get(key) for key in ('currentVersion','updateAvailable','automaticUpdateReady','channelStatus')}
                code,accepted=api('/api/app/update/install',{'pin':pin},timeout=60);report['installResponse']={'http':code,**accepted}
                started=time.monotonic();deadline=started+90;new_health=None;helper_exit_at=None
                while time.monotonic()<deadline:
                    return_code=process_tree.process.poll()
                    try:
                        _,current=api('/api/health',timeout=1)
                        report['polls'].append({'seconds':round(time.monotonic()-started,2),'healthPid':current.get('pid'),'oldPopenExit':return_code})
                        if current.get('pid')!=old_pid:
                            new_health=current;break
                    except (OSError,ValueError):
                        report['polls'].append({'seconds':round(time.monotonic()-started,2),'healthPid':None,'oldPopenExit':return_code})
                    if traces.exists():
                        events=[json.loads(line.lstrip('\ufeff')) for line in traces.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
                        if any(item['Event']=='EdbStop' and item['ProcessName'].lower()=='powershell.exe' for item in events):
                            if helper_exit_at is None:helper_exit_at=time.monotonic()
                            if time.monotonic()-helper_exit_at>10:break
                    time.sleep(.3)
                report['newHealth']=new_health
                if new_health:
                    _,status=api('/api/app/update');report['afterStatus']={key:status.get(key) for key in ('currentVersion','updateAvailable','automaticUpdateReady','channelStatus')}
                report['oldPopenExit']=process_tree.process.poll();report['artifactDownloads']=len(artifact_requests)
                config=json.loads((installed/'_internal/app_update_config.json').read_text(encoding='utf-8-sig'));report['installedResourceVersion']=config.get('version')
                report['pendingFiles']=[{'path':str(path.relative_to(app_home)),'size':path.stat().st_size,'sha256':hashlib.file_digest(path.open('rb'),'sha256').hexdigest()} for path in (app_home/'.app_runtime/updates').rglob('*') if path.is_file()]
        except Exception as error:
            report['diagnosticError']=str(error)
        finally:
            try:api('/api/system/shutdown',{},timeout=2)
            except (OSError,ValueError):pass
            if process_tree:
                process_tree.terminate(timeout=5);process_tree.close()
            time.sleep(1);stop.write_text('stop')
            try:monitor.wait(timeout=10)
            except subprocess.TimeoutExpired:monitor.kill();monitor.wait(timeout=5)
            monitor_log.close()
            report['monitorExit']=monitor.returncode
            for index,path in enumerate(app_home.rglob('*.log')):
                text=path.read_text(encoding='utf-8',errors='replace').replace(verifier,'[verifier redacted]').replace(pin,'[PIN redacted]')
                (evidence/f'runtime-{index}-{path.name}').write_text(text,encoding='utf-8')
            event_query="$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false); Get-WinEvent -FilterHashtable @{LogName='Application';Id=1000,1001;StartTime=(Get-Date).AddMinutes(-10)} | Where-Object {$_.Message -match 'powershell|ClassInEDBMVP|installer|Setup'} | Select-Object -First 12 TimeCreated,ProviderName,Id,Message | ConvertTo-Json -Depth 4"
            report['applicationEvents']=run([str(host),'-NoProfile','-NonInteractive','-Command',event_query],timeout=15)
            server.shutdown();server.server_close();thread.join(timeout=5)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
