"""Launcher settings (launcher.toml next to the server's config.toml). All optional; paths are
auto-detected when blank (see locate.py)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LauncherConfig:
    server_mode: str = "local"            # "local" = start steepserver here; "remote" = just connect
    server_address: str = "127.0.0.1"     # where the Ubisoft hostnames must resolve (this host, or a LAN/VPN IP)
    server_https_port: int = 443
    server_config: str = ""               # steepserver config.toml (blank = ../config.toml next to launcher.toml)
    steam_exe: str = ""
    steep_exe: str = ""
    connect_exe: str = ""
    steam_app_id: int = 460920
    remove_hosts_on_exit: bool = False    # take the hosts entries out again when the launcher closes
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None) -> "LauncherConfig":
        if path is None or not path.exists():
            return cls(path=path)
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        fields = {f for f in cls.__dataclass_fields__ if f != "path"}
        unknown = set(raw) - fields
        if unknown:
            raise ValueError(f"unknown launcher.toml keys: {', '.join(sorted(unknown))}")
        return cls(path=path, **{k: v for k, v in raw.items() if k in fields})

    def server_config_path(self) -> Path:
        if self.server_config:
            return Path(self.server_config)
        base = self.path.resolve().parent if self.path else Path.cwd()
        return base / "config.toml"

    @property
    def status_url(self) -> str:
        return f"https://{self.server_address}:{self.server_https_port}/_steep/status"
