"""The Play flow as a list of steps, run in a worker thread. Each step checks first and only changes
something when needed; when it needs the user (hosts entries, certificate, restarting Connect) it shows
the choices and waits for one. The UI only renders steps and forwards button clicks.

Play:            game files -> server -> DNS -> certificate -> Ubisoft Connect -> game -> (cleanup on exit)
Restore system:  Steep closed -> server stopped -> Connect quit -> hosts entries removed -> CA removed -> check
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

from steepserver import trust

from . import attach, certs, connect, dns, elevate, footprint, game, locate, server
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


def play_steps() -> list[Step]:
    return [Step("files", "Game files"), Step("server", "Server"), Step("dns", "DNS / hosts"),
            Step("cert", "Certificate"), Step("connect", "Ubisoft Connect"), Step("game", "Steep")]


def restore_steps() -> list[Step]:
    return [Step("game", "Steep"), Step("server", "Server"), Step("connect", "Ubisoft Connect"),
            Step("hosts", "Hosts file"), Step("cert", "Certificate"), Step("check", "Result")]


class Session:
    def __init__(self, cfg: LauncherConfig, data_dir: Path, on_update: Callable[[], None] = lambda: None):
        self.cfg, self.data_dir, self.on_update = cfg, data_dir, on_update
        self.steps = play_steps()
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
        """Play: set up what is missing and start the game."""
        self._launch(play_steps(), (self.check_files, self.check_server, self.check_dns, self.check_cert,
                                    self.check_connect, self.run_game))

    def restore(self) -> None:
        """Restore system: undo what the launcher set up (see footprint.py)."""
        self._launch(restore_steps(), self.restore_flow())

    def restore_flow(self) -> tuple:
        return (self.restore_game, self.restore_server, self.restore_connect, self.restore_hosts,
                self.restore_cert, self.restore_check)

    def _launch(self, steps: list[Step], flow) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.cancel.clear()
        self.steps = steps
        self.on_update()
        self.thread = threading.Thread(target=self._run, args=(flow,), daemon=True)
        self.thread.start()

    def _run(self, flow) -> None:
        try:
            for fn in flow:
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
        started = " (run by the launcher)" if self.server_proc else " (already running)"
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
            # started before the redirect, signed in to Ubisoft, or connected to a server that has since
            # stopped (it does not reconnect by itself): it has to restart against this server
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
        for attempt in range(20):                        # right after start-up it may not be ready yet
            if attach.load_dll(pid):
                break
            self.wait(0.5)
        else:
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
        observer.poll()
        self.set("game", OK if online else FAILED,
                 "game closed - press Play to start it again" if online else
                 "game closed before going online - press Play to try again")
        self.end_game()
        return True

    # ---- restore system ----------------------------------------------------------------------------
    def restore_game(self) -> bool:
        while attach.game_processes():
            self.ask("game", "Steep is running. Close it first.", ["Check again"])
        self.end_game()
        self.set("game", OK, "not running")
        return True

    def restore_server(self) -> bool:
        if self.server_proc and self.server_proc.running():
            self.set("server", RUNNING, "stopping the local server")
            self.server_proc.stop()
            self.server_proc = None
            self.set("server", OK, "stopped the server the launcher started (its data is kept)")
        elif server.status(self.cfg, timeout=1.0):
            self.set("server", OK, "a server the launcher did not start is running; left as it is")
        else:
            self.set("server", OK, "not running")
        return True

    def restore_connect(self) -> bool:
        if not connect.running():
            self.set("connect", OK, "not running")
            return True
        choice = self.ask("connect", "Ubisoft Connect is running and stays connected to the server until it "
                                     "restarts. Quit it now? Next time it starts it connects to Ubisoft and asks "
                                     "for your Ubisoft login.", ["Quit Ubisoft Connect", "Leave it running"])
        if choice == "Leave it running":
            self.set("connect", OK, "left running (restart it yourself to reach Ubisoft)")
            return True
        connect.quit_connect()
        self.set("connect", OK if not connect.running() else FAILED,
                 "closed" if not connect.running() else "could not close it; please quit it from the tray")
        return True

    def restore_hosts(self) -> bool:
        while footprint.hosts_lines():
            self.set("hosts", RUNNING, "removing the redirect entries (Windows asks for admin rights)")
            backups = self.data_dir / "hosts-backups"
            code = elevate.hosts("remove", self.cfg.server_address, str(backups))
            if code == 0:
                dns.flush_dns()
                self.hosts_added = False
                self.set("hosts", OK, f"redirect entries removed (previous file backed up to {backups})")
                return True
            choice = self.ask("hosts", "The hosts file was not changed" + (" (admin rights declined)." if code == -1
                              else f" (error {code})."), ["Try again", "Skip"])
            if choice == "Skip":
                self.set("hosts", FAILED, "redirect entries still in the hosts file")
                return True
        self.set("hosts", OK, "no redirect entries")
        return True

    def restore_cert(self) -> bool:
        cas = trust.installed_cas()
        if not cas:
            self.set("cert", OK, "no server certificate installed")
            return True
        for thumb, _ in cas:
            while True:
                self.set("cert", RUNNING, "removing the server's certificate (Windows asks to confirm)")
                try:
                    trust.remove_thumbprint(thumb)
                    break
                except trust.TrustError as e:
                    if self.ask("cert", f"Not removed: {e}.", ["Try again", "Skip"]) == "Skip":
                        break
        left = trust.installed_cas()
        self.set("cert", FAILED if left else OK, f"{len(left)} server certificate(s) still trusted" if left else
                 f"removed {len(cas)} server certificate(s)")
        return True

    def restore_check(self) -> bool:
        fp = footprint.scan(self.cfg)
        if not fp.clean:
            self.set("check", FAILED, "not everything could be undone: " + "; ".join(fp.lines()[:2]))
            return True
        upc = Path(self.cfg.connect_exe) if self.cfg.connect_exe else locate.connect_exe()
        if fp.connect_running or not upc:
            self.set("check", OK, "everything the launcher set up is undone. Press Play to set it up again.")
            return True
        if self.ask("check", "Everything the launcher set up is undone. Press Play to set it up again.",
                    ["Start Ubisoft Connect", "Done"]) == "Start Ubisoft Connect":
            connect.start(upc)
        self.set("check", OK, "everything the launcher set up is undone. Press Play to set it up again.")
        return True

    # ---- cleanup -----------------------------------------------------------------------------------
    def end_game(self) -> None:
        """After the game exits. The server keeps running while the launcher is open, so Ubisoft Connect
        stays signed in and the next Play goes straight to the game."""
        if self.armer and self.armer.poll() is None:
            self.armer.terminate()
        self.armer = None

    def stop(self) -> None:
        """The launcher closes: undo what it set up."""
        self.cancel.set()
        self.end_game()
        if self.cfg.remove_hosts_on_exit and self.hosts_added:
            elevate.hosts("remove", self.cfg.server_address, str(self.data_dir / "hosts-backups"))
            self.hosts_added = False
        if self.server_proc:
            self.server_proc.stop()
            self.server_proc = None

    def open_logs(self) -> None:
        webbrowser.open((self.data_dir / "logs").as_uri())
