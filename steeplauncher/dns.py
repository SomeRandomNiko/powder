"""Does the system resolve the Ubisoft hostnames to the server? If yes, nothing to do (the user may have
set up their own DNS server). If not, offer to add a marked block to the hosts file — with a backup first.

The actual hosts-file write runs elevated in hostsfix.py; this module only checks and builds the lines.
"""
from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from steepserver.hostnames import REDIRECT_HOSTS

HOSTS_PATH = Path(r"C:\Windows\System32\drivers\etc\hosts")
MARKER = "steep-re launcher redirect"


@dataclass(frozen=True)
class HostStatus:
    host: str
    resolved: list[str]
    ok: bool


def resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})
    except socket.gaierror:
        return []


def check(address: str, hosts: list[str] = REDIRECT_HOSTS) -> list[HostStatus]:
    """Each host's resolution vs. the wanted address. ok = it resolves only to `address`."""
    out = []
    for h in hosts:
        got = resolve(h)
        out.append(HostStatus(h, got, got == [address]))
    return out


def mismatches(address: str, hosts: list[str] = REDIRECT_HOSTS) -> list[HostStatus]:
    return [s for s in check(address, hosts) if not s.ok]


def hosts_block(address: str, hosts: list[str] = REDIRECT_HOSTS) -> str:
    lines = [f"# {MARKER} (remove this block to restore normal online play)"]
    lines += [f"{address} {h}" for h in hosts]
    return "\n".join(lines) + "\n"


def lines_for_display(address: str, hosts: list[str] = REDIRECT_HOSTS) -> str:
    """Just the entries, for the user to paste into a hosts file or their own DNS server."""
    return "\n".join(f"{address} {h}" for h in hosts)


def read_block(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if MARKER in ln or _is_redirect_line(ln)]


def _is_redirect_line(line: str) -> bool:
    parts = line.split()
    return len(parts) == 2 and parts[1] in REDIRECT_HOSTS


def strip_block(text: str) -> str:
    kept = [ln for ln in text.splitlines() if not (MARKER in ln or _is_redirect_line(ln))]
    return "\n".join(kept).rstrip("\n") + ("\n" if kept else "")


def flush_dns() -> None:
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
