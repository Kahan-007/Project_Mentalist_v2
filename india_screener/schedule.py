"""Windows Task Scheduler setup for the daily job (weekdays; runs late if the PC was off or asleep)."""
import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import config

TASK = "IndiaScreener Daily"


def _wrapper():
    path = config.ROOT / "run_daily.cmd"
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    path.write_text("@echo off\r\n"
                    'cd /d "%~dp0"\r\n'
                    f'"{exe}" -m india_screener daily >> "data\\logs\\daily.log" 2>&1\r\n', encoding="utf-8")
    return path


def task_xml(command, workdir, at="18:30", user=None):
    start = (dt.date.today() + dt.timedelta(days=1)).isoformat() + f"T{at}:00"
    user_xml = f"<UserId>{escape(user)}</UserId>" if user else ""
    days = "".join(f"<{d}/>" for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>India value screener: daily prices, news and event check</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek><DaysOfWeek>{days}</DaysOfWeek><WeeksInterval>1</WeeksInterval></ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author">{user_xml}<LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(str(command))}</Command><WorkingDirectory>{escape(str(workdir))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def install(at="18:30"):
    if sys.platform != "win32":
        raise SystemExit("Scheduling is automated for Windows only. On Linux/macOS use cron: "
                         f"30 18 * * 1-5 cd {config.ROOT} && {sys.executable} -m india_screener daily")
    dt.time.fromisoformat(at)
    config.ensure_dirs()
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\") or None
    xml = task_xml(_wrapper(), config.ROOT, at, user)
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        subprocess.run(["schtasks", "/Create", "/TN", TASK, "/XML", tmp, "/F"], check=True)
    finally:
        os.unlink(tmp)
    print(f"Installed '{TASK}' for weekdays at {at} (runs as soon as possible if the PC was off).")


def remove():
    subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], check=False)


def show():
    subprocess.run(["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"], check=False)
