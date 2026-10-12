"""Per-user macOS launchd setup. No administrator account or broker connection required."""
import datetime as dt
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import config

LABELS = ('org.mentalist.screener', 'org.mentalist.daily')


def definitions(port=8766):
    common = {'WorkingDirectory': str(config.ROOT),
              'EnvironmentVariables': {'SCREENER_HOME': str(config.ROOT)},
              'ProcessType': 'Background'}
    dashboard = {**common, 'Label': LABELS[0],
                 'ProgramArguments': [sys.executable, '-m', 'india_screener', 'serve', '--no-browser', '--port', str(port)],
                 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 20,
                 'StandardOutPath': str(config.LOGS/'monitor-service.log'),
                 'StandardErrorPath': str(config.LOGS/'monitor-service-error.log')}
    daily = {**common, 'Label': LABELS[1],
             'ProgramArguments': [sys.executable, '-m', 'india_screener', 'daily'],
             'StartCalendarInterval': [{'Weekday': day, 'Hour':18, 'Minute':30} for day in range(1,6)],
             'StandardOutPath': str(config.LOGS/'daily-service.log'),
             'StandardErrorPath': str(config.LOGS/'daily-service-error.log')}
    return [dashboard, daily]


def manage(action, port=8766):
    if sys.platform != 'darwin':
        raise SystemExit('This service command is for macOS. On Windows use the dashboard or the monitor command and Task Scheduler.')
    directory = Path.home()/'Library'/'LaunchAgents'
    domain = f'gui/{os.getuid()}'
    if action == 'show':
        for label in LABELS:
            subprocess.run(['launchctl','print',f'{domain}/{label}'], check=False)
        return
    if action == 'remove':
        for label in LABELS:
            subprocess.run(['launchctl','bootout',f'{domain}/{label}'], check=False, capture_output=True)
            (directory/(label+'.plist')).unlink(missing_ok=True)
        print('Mentalist login services removed. Project data and Telegram pairing kept.')
        return
    if action != 'install':
        raise ValueError('Unknown service action')
    config.ensure_dirs()
    directory.mkdir(parents=True, exist_ok=True)
    for definition in definitions(port):
        path = directory/(definition['Label']+'.plist')
        if path.exists():
            backup = config.DATA/(path.name+'.backup-'+dt.datetime.now().strftime('%Y%m%d%H%M%S'))
            backup.write_bytes(path.read_bytes())
            subprocess.run(['launchctl','bootout',f"{domain}/{definition['Label']}"], check=False, capture_output=True)
        path.write_bytes(plistlib.dumps(definition))
        path.chmod(0o600)
        subprocess.run(['launchctl','bootstrap',domain,str(path)], check=True)
    print(f'Installed dashboard/monitor at http://127.0.0.1:{port}/ and weekday daily job at 18:30 in the Mac system timezone. Monitoring pauses while asleep/offline.')
