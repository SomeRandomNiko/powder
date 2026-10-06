"""Before the launcher starts the local server: are its ports free? If not, name the program holding them
(instead of a server that silently fails to start)."""
from __future__ import annotations

import csv
import io
import re
import socket
import subprocess
from dataclasses import dataclass

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Port:
    number: int
    proto: str                          # "TCP" or "UDP"
    what: str


def is_free(bind: str, port: Port) -> bool:
    kind = socket.SOCK_STREAM if port.proto == "TCP" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((bind, port.number))
            return True
        except OSError:
            return False


def owner_pid(netstat: str, port: Port) -> int | None:
    """PID holding `port` in `netstat -ano` output (a listening TCP socket, or a bound UDP one)."""
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != port.proto:
            continue
        if not re.search(rf":{port.number}$", parts[1]):
            continue
        if port.proto == "TCP" and (len(parts) < 5 or parts[3].upper() != "LISTENING"):
            continue
        return int(parts[-1]) if parts[-1].isdigit() else None
    return None


def process_name(pid: int) -> str:
    out = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, errors="replace", creationflags=NO_WINDOW).stdout
    rows = [r for r in csv.reader(io.StringIO(out)) if len(r) > 1]
    return rows[0][0] if rows else f"process {pid}"


def busy(bind: str, ports: list[Port]) -> list[str]:
    """One line per port that is in use: 'port 443 (TCP, HTTPS) is used by xyz.exe (pid 123)'."""
    taken = [p for p in ports if not is_free(bind, p)]
    if not taken:
        return []
    netstat = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace",
                             creationflags=NO_WINDOW).stdout
    lines = []
    for p in taken:
        pid = owner_pid(netstat, p)
        who = f"{process_name(pid)} (pid {pid})" if pid else "another program"
        lines.append(f"port {p.number} ({p.proto}, {p.what}) is used by {who}")
    return lines
