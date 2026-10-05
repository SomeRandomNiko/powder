"""Run one command elevated (UAC) and wait for it. Used only for the hosts-file change."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

SEE_MASK_NOCLOSEPROCESS = 0x40
SW_HIDE = 0


class SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def run_elevated(args: list[str]) -> int:
    """Run `python <args>` elevated; returns its exit code, or -1 if the user declined UAC."""
    info = SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = subprocess.list2cmdline(args)
    info.nShow = SW_HIDE
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return -1                                  # declined (ERROR_CANCELLED) or failed to start
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return code.value


def hosts(action: str, address: str, backup_dir: str) -> int:
    return run_elevated(["-m", "steeplauncher.hostsfix", action, "--address", address, "--backup-dir", backup_dir])
