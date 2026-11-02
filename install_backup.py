#!/usr/bin/env python3
"""Install the catalog's daily local backup job on macOS."""
import argparse
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin':
        parser.error('launchd installer supports macOS; use backup command with your scheduler elsewhere')
    root = Path(__file__).resolve().parent
    destination = args.destination.expanduser().resolve()
    if destination == root or root in destination.parents:
        parser.error('destination must be outside the repository')
    destination.mkdir(parents=True, exist_ok=True)
    (root / 'data').mkdir(exist_ok=True)
    label = 'com.xabarov.science.backup'
    job = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    config = {'Label': label, 'ProgramArguments': [str(Path(sys.executable).resolve()), str(root/'science.py'),
               'backup', '--destination', str(destination)], 'WorkingDirectory': str(root),
              'StartCalendarInterval': {'Hour':23,'Minute':30},
              'StandardOutPath': str(root/'data/backup.log'), 'StandardErrorPath': str(root/'data/backup-error.log')}
    if job.exists():
        old=plistlib.loads(job.read_bytes())
        if str(root/'science.py') not in old.get('ProgramArguments',[]):
            parser.error('existing job belongs to another installation')
    job.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True)
    job.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(job)],check=True)
    print(f'Installed {label}: daily at 23:30, destination={destination}')


if __name__=='__main__':
    main()
