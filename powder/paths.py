"""Where Powder's files are, from a repo checkout (dev) or from the installed program (frozen build).

  user_root()    settings and everything Powder generates: config.toml, launcher.toml, data\, gamedata\
                 installed: %LOCALAPPDATA%\\Powder (kept apart from the program, so updates and the
                 uninstaller never touch progress unless asked); dev: the current folder
  native_dir()   the TLS helper (hook_guard.dll, armer.exe)
  self_command() how Powder starts itself for the server and the elevated hosts-file step. The installed
                 build uses powder-cli.exe (a console program, started with its window hidden), because
                 the windowed Powder.exe has no stdout for logs and results.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
REPO = Path(__file__).resolve().parents[1]
CLI_EXE = "powder-cli.exe"


def install_dir() -> Path:
    return Path(sys.executable).resolve().parent


def user_root() -> Path:
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Powder"
    return Path.cwd()


def native_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", install_dir())) / "native"
    return REPO / "native" / "bin"


def self_command(*args: str) -> list[str]:
    if FROZEN:
        return [str(install_dir() / CLI_EXE), *args]
    return [sys.executable, "-m", "powder", *args]
