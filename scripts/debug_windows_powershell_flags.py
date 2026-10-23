"""Probe OS PowerShell5 execution under exact native updater process flags."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    modes={'normal':0,'new_process_group':subprocess.CREATE_NEW_PROCESS_GROUP,
           'detached_and_group':subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP,
           'no_window_and_group':subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP}
    results=[]
    with tempfile.TemporaryDirectory(prefix='edb-ps5-flags-') as raw:
        root=Path(raw)
        for name,flags in modes.items():
            for devnull in (False,True):
                marker=root/f'{name}-{devnull}.txt';script=root/f'{name}-{devnull}.ps1'
                script.write_text("$ErrorActionPreference='Stop'\nSet-Content -LiteralPath '"+str(marker).replace("'","''")+"' -Value ('executed '+$PSVersionTable.PSVersion.ToString())\n",encoding='utf-8-sig')
                completed=subprocess.run([str(host),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL if devnull else subprocess.PIPE,stderr=subprocess.DEVNULL if devnull else subprocess.PIPE,creationflags=flags,timeout=20)
                result={'mode':name,'flags':flags,'allStdioDevnull':devnull,'exit':completed.returncode,'bodyExecuted':marker.is_file(),'marker':marker.read_text(encoding='utf-8-sig') if marker.exists() else None,
                        'stdout':completed.stdout.decode('utf-8','replace') if completed.stdout else '', 'stderr':completed.stderr.decode('utf-8','replace') if completed.stderr else ''}
                results.append(result);print(json.dumps(result))
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({'host':str(host),'results':results},indent=2),encoding='utf-8')

if __name__=='__main__':main()
