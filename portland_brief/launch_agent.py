"""Install/remove the user-owned macOS runner; no privileged daemon or cloud job."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = 'com.andyseubert.portland-daily-brief'


def specification(root):
    return {
        'Label': LABEL,
        'ProgramArguments': [str(root / '.venv/bin/python'), '-m', 'portland_brief.scheduling',
                             '--root', str(root), 'tick', '--run', '--scheduled'],
        'WorkingDirectory': str(root),
        'RunAtLoad': True,
        'StartInterval': 60,
        'ProcessType': 'Background',
        'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                                 'PYTHONUNBUFFERED': '1'},
        'StandardOutPath': str(root / 'data/scheduler/launchd.log'),
        'StandardErrorPath': str(root / 'data/scheduler/launchd-error.log'),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description='Install or remove the Portland daily user LaunchAgent')
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('action', choices=['install', 'remove', 'status'])
    args = p.parse_args(argv)
    if sys.platform != 'darwin':
        p.error('This installer is for macOS')
    root = args.root.resolve()
    config_path = root / 'config/tasks.json'
    config = json.loads(config_path.read_text())
    target = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    domain = f'gui/{os.getuid()}'
    service = domain + '/' + LABEL
    if args.action == 'status':
        result = subprocess.run(['/bin/launchctl', 'print', service], text=True, capture_output=True)
        print(result.stdout or result.stderr)
        return result.returncode
    if args.action == 'remove':
        config['schedule'].update(enabled=False, installed=False)
        config_path.write_text(json.dumps(config, indent=2) + '\n')
        subprocess.run(['/bin/launchctl', 'bootout', service], capture_output=True)
        target.unlink(missing_ok=True)
        print('Runner removed. Retained briefs, private evidence and Git history are unchanged.')
        return 0
    if not (root / '.venv/bin/python').exists():
        p.error('Install the Python environment first')
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / 'data/scheduler').mkdir(parents=True, exist_ok=True)
    target.write_bytes(plistlib.dumps(specification(root)))
    subprocess.run(['/bin/launchctl', 'bootout', service], capture_output=True)
    subprocess.run(['/bin/launchctl', 'enable', service], check=True)
    subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(target)], check=True)
    subprocess.run(['/bin/launchctl', 'print', service], check=True, capture_output=True)
    config['schedule'].update(enabled=True, installed=True)
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    print(json.dumps({'installed': True, 'label': LABEL, 'plist': str(target),
                      'daily_time': config['daily_brief']['daily_time'], 'timezone': config['timezone'],
                      'sleep_behavior': 'Catch up on wake/login; no wake or power-on is forced'}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
