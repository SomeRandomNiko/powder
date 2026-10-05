"""The Play flow as a list of steps, run in a worker thread. Each step checks first and only changes
something when needed; when it needs the user (hosts entries, certificate, restarting Connect) it shows
the choices and waits for one. The UI only renders steps and forwards button clicks.

Steps: game files -> server -> DNS -> certificate -> Ubisoft Connect -> game -> (cleanup on exit)
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import attach, certs, connect, dns, elevate, game, locate, server
from .config import LauncherConfig

log = logging.getLogger("steeplauncher")

PENDING, RUNNING, OK, ACTION, FAILED = "pending", "running", "ok", "action", "failed"


@dataclass
class Step:
    key: str
    title: str
    state: str = PENDING
    text: str = ""
    actions: list[str] = field(default_factory=list)   # button labels while state == ACTION
    details: str = ""                                    # copyable text (e.g. hosts lines)


class Cancelled(Exception):
    pass


class Session:
    def __init__(self, cfg: LauncherConfig, data_dir: Path, on_update: Callable[[], None] = lambda: None):
        self.cfg, self.data_dir, self.on_update = cfg, data_dir, on_update
        self.steps = [Step("files", "Game files"), Step("server", "Server"), Step("dns", "DNS / hosts"),
                      Step("cert", "Certificate"), Step("connect", "Ubisoft Connect"), Step("game", "Steep")]
        self.choices: queue.Queue[str] = queue.Queue()
        self.cancel = threading.Event()
        self.server_proc: server.ServerProcess | None = None
        self.armer = None
        self.hosts_added = False
        self.thread: threading.Thread | None = None
        self.paths: dict[str, Path] = {}

    # ---- plumbing ---------------------------------------------------------------------------------
    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)

    def set(self, key: str, state: str, text: str = "", actions: list[str] | None = None, details: str = "") -> None:
        s = self.step(key)
        changed = (s.state, s.text) != (state, text)
        s.state, s.text, s.actions, s.details = state, text, actions or [], details
        if changed:                                      # waiting loops re-set the same text
            log.info("%s: %s %s", key, state, text)
        self.on_update()

    def ask(self, key: str, text: str, actions: list[str], details: str = "") -> str:
        """Show choices on a step and block until the user picks one."""
        while not self.choices.empty():
            self.choices.get_nowait()
        self.set(key, ACTION, text, actions, details)
        while True:
            if self.cancel.is_set():
                raise Cancelled
            try:
                choice = self.choices.get(timeout=0.3)
            except queue.Empty:
                continue
            if choice in actions:
                self.set(key, RUNNING, f"{choice}...")
                return choice

    def choose(self, label: str) -> None:            # called by the UI
        self.choices.put(label)

    def wait(self, seconds: float) -> None:
        if self.cancel.wait(seconds):
            raise Cancelled

    # ---- run --------------------------------------------------------------------------------------
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.cancel.clear()
        for s in self.steps:
            s.state, s.text, s.actions, s.details = PENDING, "", [], ""
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            for fn in (self.check_files, self.check_server, self.check_dns, self.check_cert,
                       self.check_connect, self.run_game):
                if not fn():
                    return
        except Cancelled:
            log.info("cancelled")
        except Exception as e:                          # show it instead of dying silently
            log.exception("launcher step failed")
            running = next((s for s in self.steps if s.state in (RUNNING, ACTION)), self.steps[-1])
            self.set(running.key, FAILED, f"error: {e}")

    # ---- steps ------------------------------------------------------------------------------------
    def check_files(self) -> bool:
        self.set("files", RUNNING, "looking for Steam, Steep and Ubisoft Connect")
        steam = Path(self.cfg.steam_exe) if self.cfg.steam_exe else locate.steam_exe()
        steep = Path(self.cfg.steep_exe) if self.cfg.steep_exe else locate.steep_exe(self.cfg.steam_app_id)
        upc = Path(self.cfg.connect_exe) if self.cfg.connect_exe else locate.connect_exe()
        missing = [n for n, p in (("Steam", steam), ("Steep.exe", steep), ("Ubisoft Connect", upc)) if not p]
        if missing:
            self.set("files", FAILED, f"not found: {', '.join(missing)} (set the path in launcher.toml)")
            return False
        build = game.supported_build(steep)
        if not build:
            self.set("files", FAILED, f"{steep} is a Steep.exe build the TLS helper does not support")
            return False
        problem = attach.ready()
        if problem:
            self.set("files", FAILED, problem)
            return False
        self.paths = {"steam": steam, "steep": steep, "connect": upc}
        self.set("files", OK, f"{build}; Ubisoft Connect at {upc.parent}")
        return True

    def check_server(self) -> bool:
        self.set("server", RUNNING, f"reaching {self.cfg.server_address}")
        st = server.status(self.cfg)
        if st is None and self.cfg.server_mode == "local":
            self.server_proc = server.ServerProcess(self.cfg)
            self.server_proc.start(self.data_dir / "logs")
            self.set("server", RUNNING, "starting the local server")
            st = server.wait_up(self.cfg, 30, alive=self.server_proc.running)
        if st is None:
            where = "local server did not start (see logs)" if self.cfg.server_mode == "local" else \
                f"no server answers at {self.cfg.server_address}:{self.cfg.server_https_port}"
            self.set("server", FAILED, where)
            return False
        started = " (started)" if self.server_proc else " (already running)"
        self.set("server", OK, f"{self.cfg.server_address}{started}, {st.get('players', 0)} player(s)")
        return True

    def check_dns(self) -> bool:
        addr = self.cfg.server_address
        while True:
            self.set("dns", RUNNING, "resolving the Ubisoft hostnames")
            bad = dns.mismatches(addr)
            if not bad:
                self.set("dns", OK, f"all hostnames resolve to {addr}")
                return True
            wrong = ", ".join(f"{s.host} -> {', '.join(s.resolved) or 'nothing'}" for s in bad[:3])
            choice = self.ask("dns", f"{len(bad)} hostname(s) do not point at the server ({wrong}"
                              f"{', ...' if len(bad) > 3 else ''}). Add these lines to your hosts file or "
                              "to your own DNS server:", ["Add to hosts file", "Check again"],
                              details=dns.lines_for_display(addr))
            if choice == "Add to hosts file":
                code = elevate.hosts("add", addr, str(self.data_dir / "hosts-backups"))
                if code != 0:
                    self.set("dns", RUNNING, "hosts file not changed" + (" (declined)" if code == -1 else ""))
                    self.wait(1.5)
                    continue
                self.hosts_added = True
            dns.flush_dns()

    def check_cert(self) -> bool:
        self.set("cert", RUNNING, "getting the server's certificate authority")
        pem = server.ca_pem(self.cfg)
        if not pem:
            self.set("cert", FAILED, "the server did not provide its CA certificate")
            return False
        ca = certs.save_ca(pem, self.data_dir)
        while not certs.is_trusted(ca):
            self.ask("cert", certs.EXPLANATION, ["Install certificate"])
            try:
                certs.install(ca)
            except Exception as e:
                self.set("cert", RUNNING, f"not installed: {e}")
                self.wait(1.5)
        self.set("cert", OK, "trusted for this Windows user")
        return True

    def check_connect(self) -> bool:
        st = server.status(self.cfg)
        if connect.logged_in(st):
            self.set("connect", OK, f"signed in ({st['connect'][0]['player']})")
            return True
        if connect.running():
            # it reconnects by itself after a server restart; give it a moment before asking
            for _ in range(10):
                self.set("connect", RUNNING, "Ubisoft Connect is running; waiting for it to reconnect")
                self.wait(2)
                st = server.status(self.cfg)
                if connect.logged_in(st):
                    self.set("connect", OK, f"signed in ({st['connect'][0]['player']})")
                    return True
            # started before the redirect (or signed in to Ubisoft): it has to restart against our server
            self.ask("connect", "Ubisoft Connect is running but not signed in to this server. It needs a restart.",
                     ["Restart Ubisoft Connect"])
            connect.quit_connect()
        connect.start(self.paths["connect"])
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            self.set("connect", RUNNING, "waiting for Ubisoft Connect to sign in (log in if it asks; "
                                         "any email/password works)")
            self.wait(2)
            st = server.status(self.cfg)
            if connect.logged_in(st):
                self.set("connect", OK, f"signed in ({st['connect'][0]['player']})")
                return True
        self.set("connect", FAILED, "Ubisoft Connect did not sign in within 10 minutes")
        return False

    def run_game(self) -> bool:
        self.set("game", RUNNING, "starting Steep through Steam")
        try:
            attach.LOG.unlink(missing_ok=True)
        except OSError:
            pass
        os.startfile(f"steam://rungameid/{self.cfg.steam_app_id}")
        observer = attach.ProcessObserver(log)
        pid = attach.wait_for_real_game(180, stop=self.cancel.is_set, observer=observer)
        if pid is None:
            self.set("game", FAILED, "Steep did not start")
            return False
        self.set("game", RUNNING, f"attaching the TLS helper (pid {pid})")
        if not attach.load_dll(pid):
            self.set("game", FAILED, "could not load the TLS helper into the game")
            return False
        for _ in range(100):
            if attach.log_has("attach"):
                break
            self.wait(0.1)
        self.armer = attach.start_armer(pid)
        self.set("game", RUNNING, "press any key at the title screen; waiting for the game to go online")
        online = False
        while pid in {p.pid for p in attach.game_processes()}:
            observer.poll()
            st = server.status(self.cfg)
            if not online and st and st.get("game"):
                online = True
                self.set("game", OK, "online on the server - have fun")
            self.wait(2)
        self.set("game", OK if online else FAILED, "game closed" if online else "game closed before going online")
        self.cleanup()
        return True

    # ---- cleanup -----------------------------------------------------------------------------------
    def cleanup(self) -> None:
        if self.armer and self.armer.poll() is None:
            self.armer.terminate()
        if self.cfg.remove_hosts_on_exit and self.hosts_added:
            elevate.hosts("remove", self.cfg.server_address, str(self.data_dir / "hosts-backups"))
            self.hosts_added = False
        if self.server_proc:
            self.server_proc.stop()
            self.server_proc = None

    def stop(self) -> None:
        self.cancel.set()
        self.cleanup()

    def open_logs(self) -> None:
        webbrowser.open((self.data_dir / "logs").as_uri())
