"""The server, from the launcher's side: start it locally (local mode) or just reach it (remote mode),
and read /_powder/status. Status is fetched over the server's TLS port by IP with the server's own CA
(the hostnames may not resolve yet when we first check)."""
from __future__ import annotations

import json
import ssl
import subprocess
import time
import urllib.request
from pathlib import Path

from .. import paths
from .config import LauncherConfig


class ServerProcess:
    def __init__(self, cfg: LauncherConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self.host_for: str | None = None

    def start(self, log_dir: Path, use_pack: bool = True, host_for: str | None = None) -> None:
        """host_for: this PC's address in the local network, to host for other PCs (listens on every
        network); None: as config.toml says (this PC only by default)."""
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = log_dir / "server-console.log"
        out = open(self.log_path, "ab")
        self.host_for = host_for
        args = ("server", "-c", str(self.cfg.server_config_path()), "run") + (() if use_pack else ("--no-gamedata",))
        if host_for:
            args += ("--bind", "0.0.0.0", "--public-host", host_for)
        self.proc = subprocess.Popen(
            paths.self_command(*args),
            stdout=out, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _context() -> ssl.SSLContext:
    # we connect by address, not by a Ubisoft hostname, so the name check is off; this endpoint carries
    # no secrets and is only used to see what the server reports
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def status(cfg: LauncherConfig, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(cfg.status_url, timeout=timeout, context=_context()) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def ca_pem(cfg: LauncherConfig) -> bytes | None:
    url = cfg.status_url.replace("/_powder/status", "/_powder/ca.pem")
    try:
        with urllib.request.urlopen(url, timeout=3, context=_context()) as r:
            return r.read()
    except OSError:
        return None


def mine(st: dict | None, key: str, also: set[str] = frozenset()) -> list[dict]:
    """The status entries ("connect" or "game") of this PC: those from the address the server sees this
    launcher at ("you"), or from `also` (the server PC's own addresses). A server without "you" (older
    Powder): all of them."""
    entries = list((st or {}).get(key) or [])
    you = (st or {}).get("you")
    if not you:
        return entries
    ips = {you} | set(also)
    return [e for e in entries if str(e.get("peer", "")).rsplit(":", 1)[0] in ips]


def wait_up(cfg: LauncherConfig, seconds: float = 20, alive=lambda: True) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and alive():
        s = status(cfg)
        if s:
            return s
        time.sleep(0.5)
    return None
