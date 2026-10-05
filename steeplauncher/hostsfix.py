"""The only admin step: add or remove the redirect block in the hosts file.

Run elevated by elevate.py:  python -m steeplauncher.hostsfix add|remove --address A --backup-dir D
Never truncates: backs the file up, writes a temp file, swaps it in (retrying while another process
holds the file), then verifies the content. Exit code 0 = done.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
import time
from pathlib import Path

from . import dns


def _write_verified(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".steep-tmp")
    tmp.write_text(text, encoding="ascii", errors="replace", newline="\r\n")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)
    if path.read_text(encoding="ascii", errors="replace").replace("\r\n", "\n") != text.replace("\r\n", "\n"):
        raise RuntimeError("hosts verification failed")


def backup(path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"hosts-{datetime.datetime.now():%Y%m%d-%H%M%S}.bak"
    shutil.copy2(path, dst)
    return dst


def apply(action: str, address: str, path: Path, backup_dir: Path) -> Path:
    original = path.read_text(encoding="ascii", errors="replace") if path.exists() else ""
    saved = backup(path, backup_dir) if path.exists() else None
    base = dns.strip_block(original)
    text = base if action == "remove" else (base.rstrip("\n") + "\n\n" + dns.hosts_block(address)).lstrip("\n")
    _write_verified(path, text)
    dns.flush_dns()
    return saved


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("add", "remove"))
    p.add_argument("--address", default="127.0.0.1")
    p.add_argument("--hosts", default=str(dns.HOSTS_PATH))
    p.add_argument("--backup-dir", required=True)
    a = p.parse_args(argv)
    try:
        saved = apply(a.action, a.address, Path(a.hosts), Path(a.backup_dir))
        print(f"{a.action} ok; backup {saved}")
        return 0
    except Exception as e:                       # reported back through the exit code + output
        print(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
