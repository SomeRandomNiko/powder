"""What the launcher has set up on this PC, checked without admin rights. Used by Restore system
(and `powder --status`) to undo exactly that: the hosts-file block, the server's CA in the user's
trusted roots and the firewall rules for playing on the local network (group "Powder"). Ubisoft Connect
and Steep are listed because they must be closed first. Things the launcher never creates (e.g. firewall
rules of the dev tools) are not part of this."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from powder.server import trust

from . import attach, connect, dns, firewall, server
from .config import LauncherConfig


@dataclass
class Footprint:
    hosts_lines: list[str] = field(default_factory=list)       # our block in the hosts file
    cas: list[tuple[str, str]] = field(default_factory=list)   # (thumbprint, subject) of installed CAs
    firewall_rules: list[str] = field(default_factory=list)    # names of our firewall rules
    connect_running: bool = False
    connect_on_server: bool = False                            # signed in to the server
    game_running: bool = False

    @property
    def clean(self) -> bool:
        """Nothing of the launcher's left in Windows (running programs don't count)."""
        return not self.hosts_lines and not self.cas and not self.firewall_rules

    def lines(self) -> list[str]:
        entries = [ln for ln in self.hosts_lines if not ln.lstrip().startswith("#")]
        return [
            f"hosts file:      {len(entries)} redirect entr{'y' if len(entries) == 1 else 'ies'}"
            if self.hosts_lines else "hosts file:      clean",
            f"certificate:     {len(self.cas)} server CA(s) trusted" if self.cas else "certificate:     none installed",
            f"firewall:        {', '.join(self.firewall_rules)}" if self.firewall_rules else "firewall:        no rules",
            "Ubisoft Connect: " + ("signed in to the server" if self.connect_on_server else
                                   "running" if self.connect_running else "not running"),
            "Steep:           " + ("running" if self.game_running else "not running"),
        ]


def hosts_lines(path: Path = dns.HOSTS_PATH) -> list[str]:
    try:
        return dns.read_block(path.read_text(encoding="ascii", errors="replace"))
    except OSError:
        return []


def scan(cfg: LauncherConfig) -> Footprint:
    try:
        cas = trust.installed_cas()
    except trust.TrustError:                           # not Windows
        cas = []
    try:
        rules = sorted({r.name for r in firewall.ours()})
    except firewall.FirewallError:                          # no PowerShell (not Windows)
        rules = []
    running = connect.running()
    st = server.status(cfg, timeout=1.0) if running else None
    return Footprint(hosts_lines=hosts_lines(), cas=cas, firewall_rules=rules, connect_running=running,
                     connect_on_server=bool(server.mine(st, "connect", {"127.0.0.1"})),
                     game_running=bool(attach.game_processes()))
