"""Windows Defender Firewall rules for playing with others on the local network.

  host:      "Powder server": Powder's server program (its HTTPS, game server and Storm ports)
  every PC:  "Powder: Steep peer-to-peer": the game itself (the players' games connect to each other)

Rules are per program, not per port: Windows asks about a program that starts listening unless a rule
names that program, and port rules don't count. Both rules allow only the local network (RemoteAddress
LocalSubnet; a VPN with its own subnet is not covered) and carry the group "Powder", so Restore system
removes exactly these. Reading rules needs no admin rights; adding and removing them does (UAC): announce
it first and only call add/remove after the user pressed a button for it (see session.py).
Uses PowerShell's NetSecurity cmdlets, whose output is not translated (netsh's is).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import elevate

log = logging.getLogger("powder.launcher")

GROUP = "Powder"
SERVER_RULE = "Powder server"
GAME_RULE = "Powder: Steep peer-to-peer"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CATEGORY_PROFILE = {"Private": "Private", "Public": "Public", "DomainAuthenticated": "Domain"}


class FirewallError(Exception):
    """The firewall rules could not be read (PowerShell failed or timed out)."""


@dataclass(frozen=True)
class Rule:
    name: str
    action: str                 # "Allow" / "Block"
    enabled: bool
    program: str = ""           # normalized: environment variables expanded, lower case
    group: str = ""
    profile: str = "Any"        # "Any" or e.g. "Private, Public"
    protocol: str = "Any"       # "Any", "TCP", "UDP", ...


@dataclass(frozen=True)
class Program:
    """A program the other PCs must reach, and the rule that allows it."""
    exe: Path
    rule: str
    protocols: tuple[str, ...]  # what it needs (an Allow rule for "Any" covers everything)


def norm(path: str | Path) -> str:
    return os.path.normcase(os.path.expandvars(str(path)))


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _ps(script: str, timeout: float = 90) -> str:
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise FirewallError(f"PowerShell could not read the firewall rules ({e})") from e
    if r.returncode != 0:
        raise FirewallError(f"PowerShell could not read the firewall rules ({r.stderr.strip()[:200]})")
    return r.stdout


_RULE_FIELDS = ("[pscustomobject]@{ name = $r.DisplayName; action = [string]$r.Action; enabled = [string]$r.Enabled; "
                "group = $r.Group; profile = [string]$r.Profile; "
                "program = [Environment]::ExpandEnvironmentVariables([string]$a.Program).ToLowerInvariant(); "
                "protocol = [string]($r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue).Protocol }")


def _parse(out: str) -> dict:
    try:
        data = json.loads(out) if out.strip() else {}
    except ValueError as e:
        raise FirewallError(f"unexpected PowerShell output: {out[:200]}") from e
    rules = data.get("rules") or []
    if isinstance(rules, dict):
        rules = [rules]
    cats = data.get("categories") or []
    if isinstance(cats, str):
        cats = [cats]
    return {"categories": cats,
            "rules": [Rule(d.get("name") or "", d.get("action") or "", str(d.get("enabled")) == "True",
                           os.path.normcase(d.get("program") or ""), d.get("group") or "", d.get("profile") or "Any",
                           d.get("protocol") or "Any") for d in rules]}


def ours() -> list[Rule]:
    """The rules the launcher added (group "Powder"); FirewallError if they can't be read."""
    out = _ps(f"$res = @(Get-NetFirewallRule -Group {_q(GROUP)} -ErrorAction SilentlyContinue | ForEach-Object "
              f"{{ $r = $_; $a = $r | Get-NetFirewallApplicationFilter; {_RULE_FIELDS} }}); "
              "[pscustomobject]@{ rules = $res } | ConvertTo-Json -Depth 4 -Compress")
    return _parse(out)["rules"]


def rules_for(programs: list[Program]) -> tuple[list[Rule], list[str]]:
    """Inbound rules (anyone's) that name one of these programs, and the current network categories."""
    targets = ", ".join(_q(norm(p.exe).lower()) for p in programs)
    out = _ps(f"$t = @({targets}); "
              "$cats = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | ForEach-Object { [string]$_.NetworkCategory }); "
              "$res = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -and "
              "($t -contains [Environment]::ExpandEnvironmentVariables($_.Program).ToLowerInvariant()) } | ForEach-Object { "
              "$a = $_; $a | Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Direction -eq 'Inbound' } | "
              f"ForEach-Object {{ $r = $_; {_RULE_FIELDS} }} }}); "
              "[pscustomobject]@{ categories = $cats; rules = $res } | ConvertTo-Json -Depth 4 -Compress")
    data = _parse(out)
    return data["rules"], data["categories"]


def _applies(rule: Rule, categories: list[str]) -> bool:
    """Enabled and active for the network this PC is on now (any network if that can't be told)."""
    if not rule.enabled:
        return False
    profiles = {p.strip() for p in rule.profile.split(",")}
    wanted = {CATEGORY_PROFILE.get(c, c) for c in categories}
    return "Any" in profiles or not wanted or bool(profiles & wanted)


def blocked(program: Program, rules: list[Rule], categories: list[str]) -> list[Rule]:
    return [r for r in rules if r.program == norm(program.exe).lower() and r.action == "Block"
            and _applies(r, categories)]


def allowed(program: Program, rules: list[Rule], categories: list[str]) -> bool:
    protocols = {r.protocol for r in rules if r.program == norm(program.exe).lower() and r.action == "Allow"
                 and _applies(r, categories)}
    return "Any" in protocols or set(program.protocols) <= protocols


def add_script(programs: list[Program]) -> str:
    common = (f"-Group {_q(GROUP)} -Direction Inbound -Action Allow -RemoteAddress LocalSubnet -Profile Any "
              f"-Description {_q('Added by the Powder launcher for playing on the local network')}")
    parts = []
    for p in programs:
        exe = str(p.exe)
        # an older rule of ours for the same program (e.g. switched off) is replaced, not duplicated
        parts.append(f"Get-NetFirewallRule -Group {_q(GROUP)} -ErrorAction SilentlyContinue | Where-Object {{ "
                     f"$_.DisplayName -eq {_q(p.rule)} -and ($_ | Get-NetFirewallApplicationFilter).Program -eq "
                     f"{_q(exe)} }} | Remove-NetFirewallRule")
        parts.append(f"New-NetFirewallRule -DisplayName {_q(p.rule)} {common} -Program {_q(exe)} | Out-Null")
    return "; ".join(parts)


def _elevated(script: str) -> int:
    return elevate.run_elevated(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 f"$ErrorActionPreference = 'Stop'; {script}; exit 0"])


def add(programs: list[Program]) -> int:
    """Add allow rules for these programs (elevated): exit code, -1 if UAC was declined."""
    return _elevated(add_script(programs))


def remove_ours() -> int:
    """Remove every rule of the group "Powder" (elevated)."""
    return _elevated(f"Remove-NetFirewallRule -Group {_q(GROUP)} -ErrorAction SilentlyContinue")
