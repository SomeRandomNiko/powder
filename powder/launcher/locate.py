"""Find Steam, Ubisoft Connect and every installed Steep (Steam and/or Ubisoft Connect version) without the
user configuring paths. A path set in launcher.toml wins; otherwise we read where Steam and Ubisoft
Connect record their installs, so games in non-default folders or on other drives are found too:

  Ubisoft Connect  HKLM\\...\\Ubisoft\\Launcher\\Installs\\<product id>\\InstallDir, and the uninstall entry
                   "Uplay Install <product id>" (InstallLocation)
  Steam            each library in libraryfolders.vdf -> steamapps/appmanifest_460920.acf -> installdir,
                   and the uninstall entry "Steam App 460920" (InstallLocation)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg
except ImportError:                     # non-Windows (tests)
    winreg = None

STEAM_APP_ID = 460920
HKLM, HKCU = "HKLM", "HKCU"
UBI_INSTALLS = r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"
UNINSTALL = (r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
             r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
STEAM, CONNECT = "steam", "connect"


@dataclass(frozen=True)
class Install:
    kind: str                           # STEAM or CONNECT: decides how the game is started
    exe: Path
    product_id: int | None = None       # Ubisoft Connect product (for uplay://launch/<id>/0)

    @property
    def label(self) -> str:
        return "Steam" if self.kind == STEAM else "Ubisoft Connect"


# ---- registry (two helpers, replaced by a fake in the tests) ------------------------------------------
def _root(name: str):
    return winreg.HKEY_LOCAL_MACHINE if name == HKLM else winreg.HKEY_CURRENT_USER


def _reg(root: str, key: str, value: str) -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(_root(root), key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def _subkeys(root: str, key: str) -> list[str]:
    if winreg is None:
        return []
    try:
        with winreg.OpenKey(_root(root), key) as k:
            return [winreg.EnumKey(k, i) for i in range(winreg.QueryInfoKey(k)[0])]
    except OSError:
        return []


# ---- Steam -------------------------------------------------------------------------------------------
def steam_path() -> Path | None:
    p = _reg(HKCU, r"Software\Valve\Steam", "SteamPath") or _reg(HKLM, r"SOFTWARE\WOW6432Node\Valve\Steam",
                                                                 "InstallPath")
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


def _steam_dirs(app_id: int) -> list[Path]:
    """Install folders of the Steam app: from each library's app manifest, then the uninstall entry."""
    found = []
    steam = steam_path()
    for lib in _library_dirs(steam) if steam else []:
        manifest = lib / "steamapps" / f"appmanifest_{app_id}.acf"
        if manifest.exists():
            m = re.search(r'"installdir"\s*"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
            if m:
                found.append(lib / "steamapps" / "common" / m.group(1))
        found.append(lib / "steamapps" / "common" / "Steep")          # manifest missing or unreadable
    for base in UNINSTALL:
        loc = _reg(HKLM, rf"{base}\Steam App {app_id}", "InstallLocation")
        if loc:
            found.append(Path(loc))
    return found


def _steam_libraries() -> list[Path]:
    steam = steam_path()
    return _library_dirs(steam) if steam else []


# ---- Ubisoft Connect ------------------------------------------------------------------------------------
def connect_dir() -> Path | None:
    p = _reg(HKLM, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher", "InstallDir")
    return Path(p) if p else None


def connect_exe() -> Path | None:
    d = connect_dir()
    for name in ("UbisoftConnect.exe", "upc.exe"):
        exe = d / name if d else None
        if exe and exe.exists():
            return exe
    return None


def _connect_dirs() -> list[tuple[int, Path]]:
    """(product id, folder) of every game Ubisoft Connect has installed; Steep is picked by its exe."""
    found = []
    for pid in _subkeys(HKLM, UBI_INSTALLS):
        loc = _reg(HKLM, rf"{UBI_INSTALLS}\{pid}", "InstallDir")
        if loc and pid.isdigit():
            found.append((int(pid), Path(loc)))
    for base in UNINSTALL:
        for name in _subkeys(HKLM, base):
            m = re.fullmatch(r"Uplay Install (\d+)", name)
            loc = _reg(HKLM, rf"{base}\{name}", "InstallLocation") if m else None
            if loc:
                found.append((int(m.group(1)), Path(loc)))
    return found


# ---- all installs --------------------------------------------------------------------------------------
def _key(exe: Path) -> str:
    try:
        return str(exe.resolve()).lower()
    except OSError:
        return str(exe).lower()


def same_exe(a: Path, b: Path) -> bool:
    return _key(a) == _key(b)


def _inside(path: Path, folder: Path) -> bool:
    return _key(path).startswith(_key(folder).rstrip("\\/") + "\\") or \
        _key(path).startswith(_key(folder).rstrip("\\/") + "/")


def find_installs(app_id: int = STEAM_APP_ID, override: str = "") -> list[Install]:
    """Every installed Steep, Steam first. `override` (launcher.toml steep_exe) replaces the search."""
    libraries = _steam_libraries()
    connect_records = _connect_dirs()

    def classify(exe: Path) -> Install:
        for pid, folder in connect_records:
            if _key(folder / "Steep.exe") == _key(exe) and not any(_inside(exe, lib) for lib in libraries):
                return Install(CONNECT, exe, pid)
        return Install(STEAM, exe)

    if override:
        return [classify(Path(override))] if Path(override).exists() else []
    installs: dict[str, Install] = {}
    for folder in _steam_dirs(app_id):
        exe = folder / "Steep.exe"
        if exe.exists():
            installs.setdefault(_key(exe), Install(STEAM, exe))
    for pid, folder in connect_records:
        exe = folder / "Steep.exe"
        if exe.exists() and _key(exe) not in installs and not any(_inside(exe, lib) for lib in libraries):
            installs[_key(exe)] = Install(CONNECT, exe, pid)
    return sorted(installs.values(), key=lambda i: i.kind != STEAM)
