"""Windows Defender Firewall rules for playing with others on the local network.

  host:      "Powder server": the server's ports (HTTPS/TCP, certificate list/TCP, game server/UDP)
  every PC:  "Powder: Steep peer-to-peer": the game's own UDP traffic between the players' PCs

Both allow only the local network (RemoteAddress LocalSubnet) and carry the group "Powder", so Restore
system removes exactly these. Reading rules needs no admin rights; adding and removing them does (UAC):
announce it first and only call add/remove after the user pressed a button for it (see session.py).
Uses PowerShell's NetSecurity cmdlets, whose output is not translated (netsh's is).
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import elevate

log = logging.getLogger("powder.launcher")

GROUP = "Powder"
SERVER_RULE = "Powder server"
GAME_RULE = "Powder: Steep peer-to-peer"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Rule:
    name: str
    action: str                 # "Allow" / "Block"
    enabled: bool
    program: str = ""
    group: str = ""


def _ps(script: str, timeout: float = 30) -> str:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return r.stdout


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _rules(script: str) -> list[Rule]:
    """Rules from a PowerShell pipeline ending in Get-NetFirewallRule objects (inbound only)."""
    out = _ps(script + " | Where-Object { $_.Direction -eq 'Inbound' } | ForEach-Object { [pscustomobject]@{ "
              "name = $_.DisplayName; action = [string]$_.Action; enabled = [string]$_.Enabled; group = $_.Group; "
              "program = ($_ | Get-NetFirewallApplicationFilter).Program } } | ConvertTo-Json -Compress")
    try:
        data = json.loads(out) if out.strip() else []
    except ValueError:
        log.warning("firewall: unexpected PowerShell output: %s", out[:200])
        return []
    if isinstance(data, dict):
        data = [data]
    return [Rule(d.get("name") or "", d.get("action") or "", str(d.get("enabled")) == "True",
                 d.get("program") or "", d.get("group") or "") for d in data]


def ours() -> list[Rule]:
    """The rules the launcher added (group "Powder")."""
    return _rules(f"Get-NetFirewallRule -Group {_q(GROUP)} -ErrorAction SilentlyContinue")


def for_program(exe: Path) -> list[Rule]:
    """Inbound rules (anyone's) for this program: an enabled Block rule wins over every Allow rule."""
    return _rules(f"Get-NetFirewallApplicationFilter -Program {_q(str(exe))} -ErrorAction SilentlyContinue "
                  "| Get-NetFirewallRule -ErrorAction SilentlyContinue")


@dataclass(frozen=True)
class Needed:
    server_ports: tuple[tuple[int, str], ...] = ()     # (port, "TCP"/"UDP") when hosting
    game_exe: Path | None = None

    def missing(self, existing: list[Rule], game_rules: list[Rule]) -> list[str]:
        """What is missing, as names for the user."""
        out = []
        names = {r.name for r in existing if r.enabled and r.action == "Allow"}
        if any(f"{SERVER_RULE} ({proto})" not in names for proto in {kind for _, kind in self.server_ports}):
            out.append(SERVER_RULE)
        if self.game_exe and not any(r.enabled and r.action == "Allow" for r in game_rules):
            out.append(GAME_RULE)
        return out


def blocked(game_rules: list[Rule]) -> list[Rule]:
    return [r for r in game_rules if r.enabled and r.action == "Block"]


def add_script(needed: Needed, what: list[str]) -> str:
    parts = []
    common = (f"-Group {_q(GROUP)} -Direction Inbound -Action Allow -RemoteAddress LocalSubnet -Profile Any "
              f"-Description {_q('Added by the Powder launcher for playing on the local network')}")
    if SERVER_RULE in what:
        for proto in ("TCP", "UDP"):
            ports = ",".join(str(p) for p, kind in needed.server_ports if kind == proto)
            if ports:
                parts.append(f"Remove-NetFirewallRule -DisplayName {_q(f'{SERVER_RULE} ({proto})')} "
                             f"-ErrorAction SilentlyContinue")
                parts.append(f"New-NetFirewallRule -DisplayName {_q(f'{SERVER_RULE} ({proto})')} {common} "
                             f"-Protocol {proto} -LocalPort {ports} | Out-Null")
    if GAME_RULE in what and needed.game_exe:
        parts.append(f"New-NetFirewallRule -DisplayName {_q(GAME_RULE)} {common} -Protocol UDP "
                     f"-Program {_q(str(needed.game_exe))} | Out-Null")
    return "; ".join(parts)


def _elevated(script: str) -> int:
    return elevate.run_elevated(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 f"$ErrorActionPreference = 'Stop'; {script}; exit 0"])


def add(needed: Needed, what: list[str]) -> int:
    """Add the rules (elevated): exit code, -1 if UAC was declined."""
    return _elevated(add_script(needed, what))


def remove_ours() -> int:
    """Remove every rule of the group "Powder" (elevated)."""
    return _elevated(f"Remove-NetFirewallRule -Group {_q(GROUP)} -ErrorAction SilentlyContinue")
