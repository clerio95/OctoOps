"""Windows Task Scheduler registration (no-op on other platforms).

Generates a Task Scheduler XML definition (boot trigger, SYSTEM principal,
restart-on-failure 3x/1min) and registers it via `schtasks /Create /XML`. The
XML route is used because the restart policy can't be expressed with plain
`schtasks` flags. On non-Windows, register_task is a no-op.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK_NAME = "OctoOps"
_SYSTEM_SID = "S-1-5-18"  # NT AUTHORITY\SYSTEM

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


def build_task_xml(command: str, arguments: str, working_dir: str) -> str:
    """Build a Task Scheduler 1.2 XML definition for the OctoOps runtime."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>OctoOps bot runtime (auto-start at boot).</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{_SYSTEM_SID}</UserId>
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
      <Count>3</Count>
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


def register_task(
    python_exe: str,
    working_dir: str,
    task_name: str = TASK_NAME,
) -> tuple[bool, str]:
    """Register (or replace) the boot task. Returns (ok, message).

    The command is the running interpreter so the task uses the same venv;
    working_dir is the base directory (so OCTOOPS_HOME resolution works).
    """
    if not is_windows():
        return (False, "skipped: Task Scheduler registration is Windows-only")

    xml = build_task_xml(python_exe, "-m octoops", working_dir)
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
        return (True, f"Registered Task Scheduler task {task_name!r} (run as SYSTEM, at boot).")
    return (False, f"schtasks failed (exit {result.returncode}): {result.stderr.strip()}")


def write_uninstall_bat(home: Path, task_name: str = TASK_NAME) -> Path | None:
    """Write uninstall.bat to the OctoOps home directory. No-op on non-Windows."""
    if not is_windows():
        return None
    bat = home / "uninstall.bat"
    bat.write_text(_UNINSTALL_BAT.format(task_name=task_name), encoding="utf-8")
    return bat
