"""Attach the TLS helper (native/bin/hook_guard.dll + armer.exe) to the running game.

Port of tools/inject.ps1 and the arm-when-ready step: wait for the real Steep.exe (Steam first starts a
short-lived stub), load hook_guard.dll into it (LoadLibraryW in a remote thread; same user, no admin),
wait until the DLL reports "attach" in its log, then run armer.exe for that pid. See native/*.c.
"""
from __future__ import annotations

import ctypes
import hashlib
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from .. import paths

NATIVE_BIN = paths.native_dir()                 # <repo>/native/bin, or bundled in the installed program
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
    parent_pid: int = 0
    parent_name: str = ""


# ---- is the game code unpacked yet? (read-only: query + read access) ----------------------------------
PROCESS_QUERY_READ = 0x0400 | 0x0010                           # QUERY_INFORMATION | VM_READ
PAGE_WRITABLE = 0x04 | 0x08 | 0x40 | 0x80                       # (EXECUTE_)READWRITE, (EXECUTE_)WRITECOPY
PAGE_NOACCESS_OR_GUARD = 0x01 | 0x100
READY, GONE, TIMEOUT = "ready", "gone", "timeout"


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


def _reader():
    k = _k32()
    k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_size_t)]
    k.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION),
                                 ctypes.c_size_t]
    k.VirtualQueryEx.restype = ctypes.c_size_t
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    return k, psapi


def code_state(pid: int, rva: int, length: int) -> tuple[int, bytes] | None:
    """(page protection, first `length` bytes at image base + rva) of the running game; None if the process
    is gone or not readable yet."""
    k, psapi = _reader()
    h = k.OpenProcess(PROCESS_QUERY_READ, False, pid)
    if not h:
        return None
    try:
        mods = (ctypes.c_void_p * 1)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModulesEx(h, mods, ctypes.sizeof(mods), ctypes.byref(needed), 3) or not mods[0]:
            return None
        addr = mods[0] + rva
        mbi = MEMORY_BASIC_INFORMATION()
        if not k.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            return None
        buf = ctypes.create_string_buffer(length)
        got = ctypes.c_size_t()
        if mbi.Protect & PAGE_NOACCESS_OR_GUARD or not k.ReadProcessMemory(h, addr, buf, length, ctypes.byref(got)):
            return mbi.Protect, b""
        return mbi.Protect, buf.raw[:got.value]
    finally:
        k.CloseHandle(h)


def unpacked(state: tuple[int, bytes] | None, length: int, sha256: str) -> bool:
    """The protector is done with the trap's page: no longer writable, and the code there is the known one."""
    if state is None:
        return False
    protect, data = state
    return not protect & PAGE_WRITABLE and len(data) == length and hashlib.sha256(data).hexdigest() == sha256


def wait_unpacked(pid: int, rva: int, length: int, sha256: str, timeout: float,
                  stop=lambda: False, poll: float = 0.05, log=None) -> str:
    """READY once the game code at the trap is unpacked, GONE if the process exits first, else TIMEOUT.
    `log` gets each change of (page protection, code known) while waiting."""
    start = time.monotonic()
    deadline = start + timeout
    seen = None
    while time.monotonic() < deadline and not stop():
        if pid not in {p.pid for p in game_processes()}:
            return GONE
        state = code_state(pid, rva, length)
        now = (state[0] if state else None,
               bool(state and state[1] and hashlib.sha256(state[1]).hexdigest() == sha256))
        if log and now != seen:
            log("pid %d after %.2f s: page protection %s, code %s", pid, time.monotonic() - start,
                hex(now[0]) if now[0] is not None else "unreadable", "known" if now[1] else "not yet")
            seen = now
        if unpacked(state, length, sha256):
            return READY
        time.sleep(poll)
    return TIMEOUT


def _snapshot() -> list[tuple[int, int, int, str]]:
    """(pid, parent pid, threads, exe name) for every process."""
    k = _k32()
    snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    out = []
    ok = k.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        out.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.cntThreads, entry.szExeFile))
        ok = k.Process32NextW(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    return out


def game_processes(name: str = "Steep.exe") -> list[GameProcess]:
    procs = _snapshot()
    names = {pid: exe for pid, _, _, exe in procs}
    return [GameProcess(pid, threads, ppid, names.get(ppid, "?"))
            for pid, ppid, threads, exe in procs if exe.lower() == name.lower()]


def image_path(pid: int) -> str:
    """Full path of the process's exe (query-only access), or '' if denied."""
    k = _k32()
    h = k.OpenProcess(0x1000, False, pid)                     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        return buf.value if k.QueryFullProcessImageNameW(wintypes.HANDLE(h), 0, buf, ctypes.byref(size)) else ""
    finally:
        k.CloseHandle(h)


def _in_dir(pid: int, exe_dir: Path | None) -> bool:
    if exe_dir is None:
        return True
    path = image_path(pid).replace("/", "\\").lower()
    return path.startswith(str(exe_dir).replace("/", "\\").lower().rstrip("\\") + "\\")


def command_line(pid: int) -> str:
    """The process's command line (NtQueryInformationProcess class 60, Windows 8.1+), or '' if denied."""
    k = _k32()
    h = k.OpenProcess(0x1000, False, pid)                     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = ctypes.c_ulong(0)
        ntdll = ctypes.WinDLL("ntdll")
        buf = ctypes.create_string_buffer(8192)
        if ntdll.NtQueryInformationProcess(wintypes.HANDLE(h), 60, buf, len(buf), ctypes.byref(size)) != 0:
            return ""
        length = ctypes.c_ushort.from_buffer(buf, 0).value      # UNICODE_STRING.Length (bytes)
        ptr = ctypes.c_void_p.from_buffer(buf, 8).value          # UNICODE_STRING.Buffer (inside buf)
        return ctypes.wstring_at(ptr, length // 2) if ptr else ""
    finally:
        k.CloseHandle(h)


class ProcessObserver:
    """Logs every Steep.exe once when it appears (parent, threads, command line) and when it exits
    (lifetime), to learn how to tell the real game from Steam's short-lived starter."""
    def __init__(self, log):
        self.log = log
        self.seen: dict[int, float] = {}

    def poll(self) -> None:
        now = time.monotonic()
        current = {p.pid: p for p in game_processes()}
        for pid, p in current.items():
            if pid not in self.seen:
                self.seen[pid] = now
                self.log.info("game process appeared: pid %d, parent %d (%s), %d threads, cmdline %r",
                              pid, p.parent_pid, p.parent_name, p.threads, command_line(pid))
        for pid in [pid for pid in self.seen if pid not in current and self.seen[pid] is not None]:
            self.log.info("game process exited: pid %d after %.1f s", pid, now - self.seen[pid])
            self.seen[pid] = None
        # thread counts change while starting: log the first time each pid crosses the threshold
        for pid, p in current.items():
            key = -pid
            if p.threads >= 20 and key not in self.seen:
                self.seen[key] = None
                self.log.info("game process pid %d reached %d threads after %.1f s", pid, p.threads,
                              now - self.seen[pid])


# Both versions start the game in two hops: a short-lived Steep.exe (started by steam.exe for the Steam
# version, by upc.exe for the Ubisoft Connect version) hands over and exits after ~2-3 s; Connect's game
# launcher then starts the real game. Same exe and command line, so the parent decides.
CONNECT_PARENTS = {"ubisoftgamelauncher.exe", "ubisoftgamelauncher64.exe"}


def started_by_connect(pid: int) -> bool:
    """The game itself (started by Ubisoft Connect's game launcher), not a starter process."""
    return any(p.pid == pid and p.parent_name.lower() in CONNECT_PARENTS for p in game_processes())


def real_game_pid(min_threads: int = 20, exe_dir: Path | None = None) -> int | None:
    """The real game, not Steam's starter: the Steep.exe started by Ubisoft Connect. Fallback if no
    parent matches (e.g. Connect renamed its processes): the Steep.exe with the most threads.
    With `exe_dir`, only a Steep.exe from that install counts (both versions may be installed)."""
    procs = [p for p in game_processes() if _in_dir(p.pid, exe_dir)]
    by_connect = [p for p in procs if p.parent_name.lower() in CONNECT_PARENTS]
    if by_connect:
        return by_connect[-1].pid
    busy = [p for p in procs if p.threads >= min_threads]
    return max(busy, key=lambda p: p.threads).pid if busy else None


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


def wait_for_real_game(timeout: float, stop=lambda: False, settle: float = 1.0,
                       observer: "ProcessObserver | None" = None, exe_dir: Path | None = None) -> int | None:
    """The real game pid once it has been up for `settle` seconds (a moment to finish starting up).
    Attaching early matters: the player can press a key at the title screen right away."""
    deadline = time.monotonic() + timeout
    first_seen: dict[int, float] = {}
    while time.monotonic() < deadline and not stop():
        if observer:
            observer.poll()
        pid = real_game_pid(exe_dir=exe_dir)
        if pid:
            first_seen.setdefault(pid, time.monotonic())
            if time.monotonic() - first_seen[pid] >= settle:
                return pid
        time.sleep(0.25)
    return None
