"""Windows Task Scheduler registration (no-op on other platforms).

Generates a Task Scheduler XML definition (logon trigger, interactive-user
principal, restart-on-failure 10x/1min) and registers it via `schtasks
/Create /XML`. The XML route is used because the restart policy can't be
expressed with plain `schtasks` flags. On non-Windows, register_task is a no-op.

The task runs as the **logged-in user** (LogonTrigger + InteractiveToken), not
as SYSTEM. SYSTEM (S-1-5-18) executes in Session 0 with no user profile, no
per-user mapped drives, and reaches network shares only via the machine
account — so anything user-scoped (mapped drive, `\\\\share` path, the paired
WhatsApp session) raised "cannot find the path specified" at startup.
InteractiveToken needs no stored password: the task runs in the user's own
session when they log on.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "OctoOps"

# run.bat is written to OCTOOPS_HOME and called by the scheduled task.
# It creates the logs\ directory (so the redirect never fails on a fresh install)
# and captures both stdout and stderr to a log file alongside octoops.log.
# Single '>' truncates on each start so this raw capture can't grow unbounded on a
# 24/7 box — it only holds the current run's startup output; the durable, rotated
# history lives in logs\octoops.log.
# Self-restarting loop (mirrors ZamOS's run_forever.bat): on any exit it waits 5s
# and relaunches, so a crash recovers without leaning solely on the scheduler's
# RestartOnFailure policy. The truncating '>' runs at the top of each iteration.
_RUN_BAT = """\
@echo off
cd /d "%~dp0"
mkdir logs 2>nul
:loop
"{python_exe}" -m octoops > logs\\octoops-stdout.log 2>&1
timeout /t 5 /nobreak >nul
goto loop
"""

_UNINSTALL_BAT = """\
@echo off
setlocal
echo OctoOps Uninstaller
echo ====================
echo.
echo Removing Task Scheduler task "{task_name}"...
schtasks /Delete /TN "{task_name}" /F 2^>nul
if %ERRORLEVEL% == 0 (
    echo   Task removed.
) else (
    echo   Task not found ^(may not have been registered^).
)
echo.
echo OctoOps has been unregistered from Windows autostart.
echo To fully remove OctoOps, delete this folder manually:
echo   %~dp0
echo.
pause
"""


def current_user_id() -> str:
    """Return the current user as ``DOMAIN\\User`` (or bare user if no domain).

    Used as the LogonTrigger/Principal identity so the task runs in this user's
    own interactive session — the account WhatsApp was paired under, with its
    profile, mapped drives and share credentials.
    """
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def build_task_xml(command: str, arguments: str, working_dir: str, user_id: str) -> str:
    """Build a Task Scheduler 1.2 XML definition for the OctoOps runtime.

    Runs as ``user_id`` via a LogonTrigger + InteractiveToken (no stored
    password) instead of SYSTEM — see the module docstring for why.
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>OctoOps bot runtime (auto-start at user logon).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user_id)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user_id)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>10</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
      <WorkingDirectory>{escape(working_dir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def is_windows() -> bool:
    return sys.platform.startswith("win")


def write_run_bat(home: Path, python_exe: str) -> Path | None:
    """Write run.bat to OCTOOPS_HOME (Windows only).

    run.bat is called by the scheduled task. It creates logs\\ before redirecting
    stdout/stderr so the log file is always available even on a fresh install where
    configure_logging hasn't run yet. This captures Python tracebacks and pre-logging
    startup errors that would otherwise vanish under SYSTEM's headless session.

    Log files produced:
      logs\\octoops.log        — structured app log (created by configure_logging)
      logs\\octoops-stdout.log — raw stdout/stderr captured by run.bat
    """
    if not is_windows():
        return None
    bat = home / "run.bat"
    bat.write_text(_RUN_BAT.format(python_exe=python_exe), encoding="utf-8")
    return bat


def register_task(
    python_exe: str,
    working_dir: str,
    task_name: str = TASK_NAME,
) -> tuple[bool, str]:
    """Register (or replace) the boot task. Returns (ok, message).

    Writes run.bat to working_dir to capture stdout/stderr, then registers a
    Task Scheduler task (logged-in user, LogonTrigger, restart 10x/1min) that
    calls it.
    """
    if not is_windows():
        return (False, "skipped: Task Scheduler registration is Windows-only")

    write_run_bat(Path(working_dir), python_exe)
    run_bat = str(Path(working_dir) / "run.bat")
    xml = build_task_xml(run_bat, "", working_dir, current_user_id())
    tmp = Path(tempfile.gettempdir()) / "octoops-task.xml"
    tmp.write_text(xml, encoding="utf-16")
    try:
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/XML", str(tmp), "/F"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return (False, "schtasks not found on PATH")
    finally:
        tmp.unlink(missing_ok=True)

    if result.returncode == 0:
        return (True, f"Registered Task Scheduler task {task_name!r} (run as {current_user_id()}, at logon).")
    return (False, f"schtasks failed (exit {result.returncode}): {result.stderr.strip()}")


def write_uninstall_bat(home: Path, task_name: str = TASK_NAME) -> Path | None:
    """Write uninstall.bat to the OctoOps home directory. No-op on non-Windows."""
    if not is_windows():
        return None
    bat = home / "uninstall.bat"
    bat.write_text(_UNINSTALL_BAT.format(task_name=task_name), encoding="utf-8")
    return bat
