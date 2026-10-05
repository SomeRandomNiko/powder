"""Find Steam, Steep.exe and Ubisoft Connect without the user configuring paths. Any value set in
launcher.toml wins; otherwise we read the registry and Steam's library index."""
from __future__ import annotations

import re
from pathlib import Path

try:
    import winreg
except ImportError:                     # non-Windows (tests)
    winreg = None

STEAM_APP_ID = 460920


def _reg(root, key: str, value: str) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def steam_path() -> Path | None:
    p = _reg(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath") if winreg else None
    return Path(p) if p else None


def steam_exe() -> Path | None:
    s = steam_path()
    exe = s / "steam.exe" if s else None
    return exe if exe and exe.exists() else None


def _library_dirs(steam: Path) -> list[Path]:
    """Steam libraries from steamapps/libraryfolders.vdf (games may live on another drive)."""
    dirs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        for m in re.finditer(r'"path"\s*"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            dirs.append(Path(m.group(1).replace("\\\\", "\\")))
    return dirs


def steep_exe(app_id: int = STEAM_APP_ID) -> Path | None:
    steam = steam_path()
    if steam is None:
        return None
    for lib in _library_dirs(steam):
        exe = lib / "steamapps" / "common" / "Steep" / "Steep.exe"
        if exe.exists():
            return exe
    return None


def connect_dir() -> Path | None:
    p = _reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher", "InstallDir") if winreg else None
    return Path(p) if p else None


def connect_exe() -> Path | None:
    d = connect_dir()
    exe = d / "UbisoftConnect.exe" if d else None
    if exe and exe.exists():
        return exe
    exe = d / "upc.exe" if d else None
    return exe if exe and exe.exists() else None
