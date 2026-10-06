"""Ubisoft Connect: it must start (or restart) after the redirect is in place, so it signs in to our
server. We detect whether it is running, can restart it, and wait until the server reports its login."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

PROCESS_NAMES = ("upc.exe", "UbisoftConnect.exe", "UplayWebCore.exe", "UbisoftGameLauncher.exe",
                 "UbisoftGameLauncher64.exe", "UbisoftExtension.exe", "UplayService.exe")


def _tasklist() -> str:
    return subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout


def running() -> bool:
    out = _tasklist().lower()
    return '"upc.exe"' in out


def quit_connect() -> None:
    for name in PROCESS_NAMES:
        subprocess.run(["taskkill", "/im", name, "/f"], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(20):
        if not running():
            return
        time.sleep(0.5)


def start(exe: Path) -> None:
    # started through explorer so it runs as the normal (non-elevated) user like a desktop launch
    subprocess.Popen(["explorer.exe", str(exe)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def logged_in(status: dict | None) -> bool:
    return bool(status and status.get("connect"))
