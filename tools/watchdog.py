#!/usr/bin/env python3
"""
Optional detached keeper for unattended downloads.

Runs invisibly. Every 60 seconds it checks that the supervisor and the
headless log mirror are still alive and starts them if not. It never kills a
running process. Both children have their own single-instance port locks.

    pythonw tools/watchdog.py

Drop a file named ``NO_LOG_WINDOW`` in the repo root to stop it reopening the
log terminal after you close it.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOG = BASE / "watchdog.log"
CHECK_INTERVAL = 60


def log(msg):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}\n")
    except Exception:
        pass


def running(needle, name="python*"):
    """PIDs of live processes whose command line contains needle."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { $_.Name -like '%s' -and $_.CommandLine -like '*%s*' } | "
             "Select-Object -ExpandProperty ProcessId" % (name, needle)],
            text=True, timeout=20,
        )
        return [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        return []


def spawn(command, hidden=True):
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if hidden:
        flags |= subprocess.CREATE_NO_WINDOW
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    if hidden:
        si.wShowWindow = 0
    return subprocess.Popen(
        command, cwd=BASE, creationflags=flags, startupinfo=si,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    log("watchdog started")
    while True:
        try:
            if not running("supervisor.py"):
                log("supervisor not running - starting it")
                spawn([sys.executable, str(BASE / "tools" / "supervisor.py")])
                time.sleep(5)

            if not running("live_monitor.py"):
                log("log mirror not running - starting it")
                spawn([sys.executable, str(BASE / "tools" / "live_monitor.py"), "--headless"])
                time.sleep(5)

            if (not (BASE / "NO_LOG_WINDOW").exists()
                    and not running("start_monitor.bat", "cmd.exe")):
                log("log window not running - opening it")
                spawn(["cmd.exe", "/c", str(BASE / "tools" / "start_monitor.bat")], hidden=False)
                time.sleep(5)
        except Exception as exc:
            log(f"watchdog error: {exc}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
