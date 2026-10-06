# PyInstaller spec: one program folder with two executables sharing the same files.
#   Powder.exe       the launcher window (no console)
#   powder-cli.exe   the same program with a console: server, admin and gamedata commands, and what the
#                    launcher starts itself (the local server, the elevated hosts-file step)
# Built by packaging/build.ps1 (POWDER_VERSION and the version resource come from there).
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION_FILE = os.environ.get("POWDER_VERSION_RESOURCE")       # Windows version resource, from build.ps1

datas = [
    (str(ROOT / "native" / "bin" / "hook_guard.dll"), "native"),
    (str(ROOT / "native" / "bin" / "armer.exe"), "native"),
    (str(ROOT / "powder" / "server" / "db" / "migrations"), "powder/server/db/migrations"),
]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    excludes=["pytest", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)


def exe(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name, console=console,
               version=VERSION_FILE, upx=False)


coll = COLLECT(exe("Powder", console=False), exe("powder-cli", console=True),
               a.binaries, a.datas, name="Powder", upx=False)
