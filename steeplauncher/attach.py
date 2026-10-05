"""Attach the TLS helper (native/bin/hook_guard.dll + armer.exe) to the running game.

Port of tools/inject.ps1 and the arm-when-ready step: wait for the real Steep.exe (Steam first starts a
short-lived stub), load hook_guard.dll into it (LoadLibraryW in a remote thread; same user, no admin),
wait until the DLL reports "attach" in its log, then run armer.exe for that pid. See native/*.c.
"""
from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

NATIVE_BIN = Path(__file__).resolve().parent.parent / "native" / "bin"
DLL = NATIVE_BIN / "hook_guard.dll"
ARMER = NATIVE_BIN / "armer.exe"
LOG = NATIVE_BIN / "hook_guard.log"

PROCESS_ACCESS = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400   # CREATE_THREAD|VM_OPERATION|VM_READ|VM_WRITE|QUERY_INFO
MEM_COMMIT_RESERVE, PAGE_READWRITE = 0x3000, 0x04
TH32CS_SNAPPROCESS = 0x2


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _k32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.VirtualAllocEx.restype = ctypes.c_void_p
    k.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    k.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                     ctypes.POINTER(ctypes.c_size_t)]
    k.GetModuleHandleW.restype = wintypes.HMODULE
    k.GetProcAddress.restype = ctypes.c_void_p
    k.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
    k.CreateRemoteThread.restype = wintypes.HANDLE
    k.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                     ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    return k


@dataclass
class GameProcess:
    pid: int
    threads: int


def game_processes(name: str = "Steep.exe") -> list[GameProcess]:
    k = _k32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = k.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        if entry.szExeFile.lower() == name.lower():
            out.append(GameProcess(entry.th32ProcessID, entry.cntThreads))
        ok = k.Process32NextW(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    return out


def real_game_pid(min_threads: int = 20) -> int | None:
    """The real game, not Steam's launcher stub: the Steep.exe with the most threads (>= min_threads)."""
    procs = [p for p in game_processes() if p.threads >= min_threads]
    return max(procs, key=lambda p: p.threads).pid if procs else None


def load_dll(pid: int, dll: Path = DLL) -> bool:
    """LoadLibraryW(dll) inside process `pid`. True when the DLL was loaded."""
    k = _k32()
    h = k.OpenProcess(PROCESS_ACCESS, False, pid)
    if not h:
        return False
    try:
        path = (str(dll) + "\0").encode("utf-16-le")
        remote = k.VirtualAllocEx(h, None, len(path), MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not remote:
            return False
        written = ctypes.c_size_t()
        if not k.WriteProcessMemory(h, remote, path, len(path), ctypes.byref(written)):
            return False
        loadlib = k.GetProcAddress(k.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        tid = wintypes.DWORD()
        th = k.CreateRemoteThread(h, None, 0, loadlib, remote, 0, ctypes.byref(tid))
        if not th:
            return False
        k.WaitForSingleObject(th, 15000)
        code = wintypes.DWORD()
        k.GetExitCodeThread(th, ctypes.byref(code))
        k.CloseHandle(th)
        return code.value != 0
    finally:
        k.CloseHandle(h)


def log_has(text: str) -> bool:
    try:
        return text in LOG.read_text(errors="replace")
    except OSError:
        return False


def start_armer(pid: int) -> subprocess.Popen:
    return subprocess.Popen([str(ARMER), "--pid", str(pid), "--log", str(LOG)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ready() -> str | None:
    """None if the helpers are built, else what is missing."""
    missing = [p.name for p in (DLL, ARMER) if not p.exists()]
    return f"missing {', '.join(missing)}: run native\\build.ps1" if missing else None


def wait_for_real_game(timeout: float, stop=lambda: False, settle: float = 3.0) -> int | None:
    """The real game pid once it has been up for `settle` seconds. The thread count already tells the
    real game from Steam's short-lived starter, so a short settle is enough; attaching early matters,
    because the player can press a key at the title screen right away."""
    deadline = time.monotonic() + timeout
    first_seen: dict[int, float] = {}
    while time.monotonic() < deadline and not stop():
        pid = real_game_pid()
        if pid:
            first_seen.setdefault(pid, time.monotonic())
            if time.monotonic() - first_seen[pid] >= settle:
                return pid
        time.sleep(0.25)
    return None
