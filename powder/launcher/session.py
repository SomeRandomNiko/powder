"""The Play flow as a list of steps, run in a worker thread. Each step checks first and only changes
something when needed; when it needs the user (hosts entries, certificate, restarting Connect) it shows
the choices and waits for one. The UI only renders steps and forwards button clicks.

Play:            game files -> server -> firewall -> DNS -> certificate -> account -> Ubisoft Connect -> game
Restore system:  Steep closed -> server stopped -> Connect quit -> hosts entries removed -> CA removed ->
                 firewall rules removed -> check

The server row of the window picks where the server is (remembered in settings.json, applied at the next
Play): "Just this PC" (a local server for this PC only), "Host" (a local server other PCs on the network
can join) or "Join" (a server on another PC).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import webbrowser
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from powder.server import trust
from powder.server.config import Config as ServerSettings
from powder.server.gamedata import pack

from ..version import __version__, compatible
from . import account, attach, certs, connect, dns, elevate, firewall, footprint, game, locate, network, ports, server
from .config import LauncherConfig

log = logging.getLogger("powder.launcher")

PENDING, RUNNING, OK, ACTION, FAILED = "pending", "running", "ok", "action", "failed"
# how a game process ended (attach_and_watch)
ONLINE, OFFLINE, STARTER, CRASHED, CLOSED_EARLY = "online", "offline", "starter", "crashed", "closed early"

# Rule: every Windows prompt (UAC, certificate dialogs) is announced first with why it is needed, and only
# appears after the user presses a button for it.
HOSTS_ADMIN_WHY = ("Changing the hosts file needs admin rights because it is a protected Windows system file: "
                   "when you click \"{button}\", Windows asks for permission (UAC). Only these lines are {verb}, "
                   "and the current file is backed up first.")
FIREWALL_WHY = ("Windows Firewall lets other PCs reach this one only for programs and ports it was told about. "
                "{what} Both rules allow only PCs in your local network. Adding them needs admin rights: when you "
                "click \"Add firewall rules\", Windows asks for permission (UAC). Restore system removes them again.")
LOCAL, HOST, JOIN = "local", "host", "join"      # where the server is (the server row)


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
    return [Step("files", "Game files"), Step("server", "Server"), Step("firewall", "Firewall"),
            Step("dns", "DNS / hosts"), Step("cert", "Certificate"), Step("account", "Account"),
            Step("connect", "Ubisoft Connect"), Step("game", "Steep")]


@dataclass
class PackInfo:
    """The local server's game-data pack, as the launcher shows it (None in remote mode)."""
    installed: bool
    built: str = ""                       # the pack's build date
    enabled: bool = True                  # the checkbox: does the server use it
    problem: str = ""                     # why an installed pack can't be used


def play_buttons(installs: list[locate.Install]) -> list[tuple[str, locate.Install | None]]:
    """(label, install) per Play button: one plain "Play" unless several versions are installed."""
    if len(installs) <= 1:
        return [("Play", installs[0] if installs else None)]
    return [(f"Play ({i.label})", i) for i in installs]


def launch_url(install: locate.Install, steam_app_id: int) -> str:
    """How the game is started: through Steam, or directly through Ubisoft Connect."""
    if install.kind == locate.STEAM:
        return f"steam://rungameid/{steam_app_id}"
    return f"uplay://launch/{install.product_id}/0"


def restore_steps() -> list[Step]:
    return [Step("game", "Steep"), Step("server", "Server"), Step("connect", "Ubisoft Connect"),
            Step("hosts", "Hosts file"), Step("cert", "Certificate"), Step("firewall", "Firewall"),
            Step("check", "Result")]


class Session:
    def __init__(self, cfg: LauncherConfig, data_dir: Path, on_update: Callable[[], None] = lambda: None):
        self.base_cfg, self.data_dir, self.on_update = cfg, data_dir, on_update
        self.cfg = self.effective_cfg()                 # launcher.toml + the server row's choice
        # the UI's way to ask for input (account dialogs): (kind, info) -> answers or None if cancelled
        self.dialog: Callable[[str, dict], dict | None] | None = None
        self.steps = play_steps()
        self.choices: queue.Queue[str] = queue.Queue()
        self.cancel = threading.Event()
        self.server_proc: server.ServerProcess | None = None
        self.armer = None
        self.hosts_added = False
        self.pack_changed = False                       # pack installed/removed since our server started
        self.thread: threading.Thread | None = None
        self.paths: dict[str, Path] = {}
        self.installs: list[locate.Install] = self.detect()   # which Steep versions are installed
        self.chosen: locate.Install | None = None              # the one the user pressed Play for
        self.install: locate.Install | None = None             # the one being played (after check_files)

    def detect(self) -> list[locate.Install]:
        self.installs = locate.find_installs(self.cfg.steam_app_id, self.cfg.steep_exe)
        return self.installs

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
    def start(self, install: locate.Install | None = None) -> None:
        """Play: set up what is missing and start the game (`install`: which version; None = the only one)."""
        if self.thread and self.thread.is_alive():
            return
        self.chosen = install
        self.cfg = self.effective_cfg()
        self._launch(play_steps(), (self.check_files, self.check_server, self.check_firewall, self.check_dns,
                                    self.check_cert, self.check_account, self.check_connect, self.run_game))

    def restore(self) -> None:
        """Restore system: undo what the launcher set up (see footprint.py)."""
        self._launch(restore_steps(), self.restore_flow())

    def restore_flow(self) -> tuple:
        return (self.restore_game, self.restore_server, self.restore_connect, self.restore_hosts,
                self.restore_cert, self.restore_firewall, self.restore_check)

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
    def pick_install(self) -> locate.Install | None:
        """The install to play: the one chosen (if still installed), else the first one found."""
        self.set("files", RUNNING, "looking for Steep (Steam and Ubisoft Connect)")
        while True:
            installs = self.detect()
            if self.chosen:
                same = [i for i in installs if locate.same_exe(i.exe, self.chosen.exe)]
                if same:
                    return same[0]
            elif installs:
                return installs[0]
            what = f"Steep ({self.chosen.label}) is no longer at {self.chosen.exe.parent}." if self.chosen else \
                "Steep was not found: neither Steam nor Ubisoft Connect lists it as installed."
            self.ask("files", what + " If it is installed in a place they don't record, set steep_exe in "
                     "launcher.toml to its Steep.exe.", ["Check again"])

    def check_files(self) -> bool:
        install = self.pick_install()
        steam = Path(self.cfg.steam_exe) if self.cfg.steam_exe else locate.steam_exe()
        upc = Path(self.cfg.connect_exe) if self.cfg.connect_exe else locate.connect_exe()
        needed = [("Ubisoft Connect", upc)] + ([("Steam", steam)] if install.kind == locate.STEAM else [])
        missing = [n for n, p in needed if not p]
        if missing:
            self.set("files", FAILED, f"not found: {', '.join(missing)} (set the path in launcher.toml)")
            return False
        build = game.supported_build(install.exe)
        if not build:
            self.set("files", FAILED, f"{install.exe} is a Steep.exe build the launcher does not support yet")
            return False
        problem = attach.ready()
        if problem:
            self.set("files", FAILED, problem)
            return False
        self.install = install
        self.paths = {"steam": steam, "steep": install.exe, "connect": upc}
        self.set("files", OK, f"{build} in {install.exe.parent}")
        return True

    def server_config(self) -> ServerSettings:
        return ServerSettings.load(self.cfg.server_config_path())

    # ---- game-data pack (local server only; set up apart from Play, remembered) --------------------------
    def _prefs_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _prefs(self) -> dict:
        try:
            return json.loads(self._prefs_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def use_pack(self) -> bool:
        return bool(self._prefs().get("use_pack", True))

    def pack_info(self) -> PackInfo | None:
        if self.cfg.server_mode != "local":
            return None
        target = self.server_config().gamedata_dir
        try:
            manifest = pack.check(target)
        except pack.PackError as e:
            return PackInfo(installed=False, problem=str(e) if target.exists() else "")
        return PackInfo(installed=True, built=str(manifest.get("built", "?")), enabled=self.use_pack())

    def install_pack(self, source: Path) -> str | None:
        """Install the chosen .zip (or folder) as the local server's pack and switch it on; an error text
        if it can't be used."""
        try:
            pack.install(source, self.server_config().gamedata_dir)
        except pack.PackError as e:
            return str(e)
        self.pack_changed = True                         # a running server still holds the old one
        self.set_pack_enabled(True)
        return None

    def remove_pack(self) -> str | None:
        """Delete the local server's pack; an error text if it can't be."""
        try:
            pack.remove(self.server_config().gamedata_dir)
        except (pack.PackError, OSError) as e:
            return str(e)
        self.pack_changed = True
        log.info("game-data pack removed")
        return None

    def _save_prefs(self, **changes) -> None:
        prefs = self._prefs()
        prefs.update(changes)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._prefs_path().write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def set_pack_enabled(self, enabled: bool) -> None:
        """The checkbox. A server this launcher runs picks it up when it is next (re)started (see
        check_server)."""
        self._save_prefs(use_pack=enabled)
        log.info("game-data pack %s", "on" if enabled else "off")

    # ---- where the server is (the server row; remembered, applied at the next Play) --------------------
    def mode(self) -> str:
        """LOCAL, HOST or JOIN; without a choice yet, what launcher.toml says."""
        mode = self._prefs().get("mode")
        if mode in (LOCAL, HOST, JOIN):
            return mode
        return LOCAL if self.base_cfg.server_mode == "local" else JOIN

    def join_address(self) -> str:
        return str(self._prefs().get("join_address") or
                   (self.base_cfg.server_address if self.base_cfg.server_mode != "local" else ""))

    def host_address(self) -> str:
        """This PC's address for the other players (the chosen one if it still exists, else the main one)."""
        addresses = network.lan_addresses()
        chosen = self._prefs().get("host_address")
        return chosen if chosen in addresses else (addresses[0] if addresses else "")

    def set_mode(self, mode: str, join_address: str | None = None, host_address: str | None = None) -> None:
        changes: dict = {"mode": mode}
        if join_address is not None:
            changes["join_address"] = join_address.strip()
        if host_address is not None:
            changes["host_address"] = host_address
        self._save_prefs(**changes)
        if not (self.thread and self.thread.is_alive()):
            self.cfg = self.effective_cfg()
        log.info("server: %s %s", mode, changes.get("join_address") or changes.get("host_address") or "")

    def effective_cfg(self) -> LauncherConfig:
        mode = self.mode()
        if mode == JOIN:
            return replace(self.base_cfg, server_mode="remote", server_address=self.join_address() or "127.0.0.1")
        address = self.base_cfg.server_address if self.base_cfg.server_mode == "local" else "127.0.0.1"
        return replace(self.base_cfg, server_mode="local", server_address=address)

    def own_addresses(self, st: dict | None) -> set[str]:
        """Addresses this PC's game and Connect may show up with in the status (besides "you"): on the server
        PC also the server's public address (the game reaches the game server there)."""
        if self.cfg.server_mode != "local":
            return set()
        return {"127.0.0.1", str((st or {}).get("public_host") or "")} - {""}

    # ---- the account on the server ---------------------------------------------------------------------
    def _account_key(self) -> str:
        return f"{self.cfg.server_address}:{self.cfg.server_https_port}"

    def account(self) -> account.Account | None:
        saved = (self._prefs().get("accounts") or {}).get(self._account_key())
        return account.Account(saved["email"], saved["name"]) if saved else None

    def _remember_account(self, acc: account.Account | None) -> None:
        accounts = dict(self._prefs().get("accounts") or {})
        if acc:
            accounts[self._account_key()] = {"email": acc.email, "name": acc.name}
        else:
            accounts.pop(self._account_key(), None)
        self._save_prefs(accounts=accounts)

    @property
    def ca_path(self) -> Path:
        return self.data_dir / "server-ca.pem"

    def account_action(self, what: str, **kw) -> account.Account:
        """register / sign_in / change_email / change_password / rename (for the account row and step); raises
        account.AccountError with a text for the player. The result is remembered (not the password)."""
        acc = getattr(account, what)(self.cfg, self.ca_path, **kw)
        self._remember_account(acc)
        log.info("account: %s done (%s)", what, acc.name)
        return acc

    def sign_out(self) -> None:
        self._remember_account(None)

    def request(self, kind: str, info: dict | None = None) -> dict | None:
        """Ask the UI for input (blocks the worker thread until answered); None if cancelled or no UI."""
        return self.dialog(kind, info or {}) if self.dialog else None

    def wanted_pack(self) -> bool:
        info = self.pack_info()
        return bool(info and info.installed and info.enabled)

    def check_server(self) -> bool:
        if self.mode() == JOIN and not network.valid_address(self.join_address()):
            self.set("server", FAILED, "enter the address of the PC that hosts the server (Join, above)")
            return False
        host_for = None
        if self.mode() == HOST:
            host_for = self.host_address()
            if not host_for:
                self.set("server", FAILED, "this PC has no address in a local network, so nobody can join it")
                return False
        self.set("server", RUNNING, f"reaching {self.cfg.server_address}")
        st = server.status(self.cfg)
        if st is not None and self.server_proc and self.server_proc.running():
            why = "game-data pack changed" if self.pack_changed or bool(st.get("gamedata")) != self.wanted_pack() \
                else "hosting changed" if self.server_proc.host_for != host_for else ""
            if why:
                self.set("server", RUNNING, f"restarting the local server ({why})")
                self.server_proc.stop()
                self.server_proc = None
                st = None
        elif st is not None and self.cfg.server_mode == "local" and host_for \
                and st.get("public_host") != host_for:
            self.set("server", FAILED, "a server the launcher did not start is running here, not set up for "
                                       "hosting: stop it (or set bind and public_host in its config.toml)")
            return False
        self.pack_changed = False
        if st is None and self.cfg.server_mode == "local":
            srv = self.server_config().server
            needed = [ports.Port(srv.https_port, "TCP", "HTTPS"), ports.Port(srv.crl_port, "TCP", "certificate list"),
                      ports.Port(srv.nex_port, "UDP", "game server")]
            while taken := ports.busy("0.0.0.0" if host_for else srv.bind, needed):
                self.ask("server", "The local server cannot start: " + "; ".join(taken) + ". Close that program "
                         "(or change the port in config.toml), then check again.", ["Check again"])
            self.server_proc = server.ServerProcess(self.cfg)
            self.server_proc.start(self.data_dir / "logs", use_pack=self.wanted_pack(), host_for=host_for)
            self.set("server", RUNNING, "starting the local server" + (f" for this network ({host_for})"
                                                                      if host_for else ""))
            st = server.wait_up(self.cfg, 30, alive=self.server_proc.running)
        if st is None:
            where = "local server did not start (see logs)" if self.cfg.server_mode == "local" else \
                f"no server answers at {self.cfg.server_address}:{self.cfg.server_https_port} (is it running " \
                "there, and is its PC hosting with the firewall rule added?)"
            self.set("server", FAILED, where)
            return False
        theirs = str(st.get("version", "?"))
        if not compatible(theirs, __version__):
            self.ask("server", f"The server runs Powder {theirs}, this launcher is Powder {__version__}. Different "
                     "major versions may not work together; update the older one.", ["Continue anyway"])
        started = " (run by the launcher)" if self.server_proc else " (already running)"
        hosting = f"; others join with {host_for}" if host_for else ""
        self.set("server", OK, f"{self.cfg.server_address}{started}, Powder {theirs}, "
                               f"{st.get('players', 0)} player(s){hosting}")
        return True

    def check_firewall(self) -> bool:
        mode = self.mode()
        if mode == LOCAL:
            self.set("firewall", OK, "nothing needed (only this PC plays on the server)")
            return True
        srv = self.server_config().server
        needed = firewall.Needed(
            server_ports=((srv.https_port, "TCP"), (srv.crl_port, "TCP"), (srv.nex_port, "UDP")) if mode == HOST
            else (), game_exe=self.install.exe if self.install else None)
        ignore_blocks = False
        while True:
            self.set("firewall", RUNNING, "checking Windows Firewall")
            game_rules = firewall.for_program(needed.game_exe) if needed.game_exe else []
            blocks = firewall.blocked(game_rules)
            if blocks and not ignore_blocks:
                names = ", ".join(f'"{r.name}"' for r in blocks[:3])
                if self.ask("firewall", f"Windows Firewall blocks Steep for incoming connections (rule {names}, "
                            "e.g. from a Windows prompt answered with Cancel). A block rule wins over any allow rule, "
                            "so the other players' games can't reach this one. Turn that rule off in Windows "
                            "Security > Firewall & network protection > Advanced settings > Inbound Rules, then "
                            "check again.", ["Check again", "Continue anyway"]) == "Check again":
                    continue
                ignore_blocks = True
            missing = needed.missing(firewall.ours(), game_rules)
            if not missing:
                self.set("firewall", OK, "other PCs in the network can reach " +
                         ("the server and the game" if mode == HOST else "the game"))
                return True
            what = []
            if firewall.SERVER_RULE in missing:
                what.append("The server's ports (" + ", ".join(f"{p}/{k}" for p, k in needed.server_ports) +
                            ") let the other PCs reach the server.")
            if firewall.GAME_RULE in missing:
                what.append("Steep itself (UDP) lets the games connect to each other directly.")
            choice = self.ask("firewall", FIREWALL_WHY.format(what=" ".join(what)), ["Add firewall rules", "Skip"])
            if choice == "Skip":
                self.set("firewall", OK, "skipped: other PCs may not be able to reach this one")
                return True
            code = firewall.add(needed, missing)
            if code != 0:
                self.set("firewall", RUNNING, "firewall not changed" + (" (declined)" if code == -1 else
                                                                         f" (error {code})"))
                self.wait(1.5)

    def check_account(self) -> bool:
        st = server.status(self.cfg)
        if st is not None and "accounts" not in st:
            self.set("account", OK, "this server has no accounts (an older Powder)")
            return True
        acc = self.account()
        if acc:
            self.set("account", OK, f"{acc.name} ({acc.email})")
            return True
        try:
            takes = account.takes_over(self.cfg, self.ca_path)
        except account.AccountError:
            takes = None
        intro = ("Every player on this server has their own account (email and password), also used to sign in to "
                 "Ubisoft Connect. Register one, or sign in if you already have one on this server.")
        if takes:
            intro += f" The first account on this server takes over the existing player {takes} and its progress."
        text = intro
        while True:
            choice = self.ask("account", text, ["Register…", "Sign in…"])
            kind = "register" if choice == "Register…" else "sign_in"
            answers = self.request(kind, {"takes_over": takes})
            if not answers:
                text = intro
                continue
            try:
                acc = self.account_action(kind, **answers)
            except account.AccountError as e:
                text = f"{e} {intro}"
                continue
            self.set("account", OK, f"{acc.name} ({acc.email})")
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
                              f"{', ...' if len(bad) > 3 else ''}). Add these lines to your own DNS server, or let "
                              "the launcher add them to the hosts file. "
                              + HOSTS_ADMIN_WHY.format(button="Add to hosts file", verb="added"),
                              ["Add to hosts file", "Check again"],
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

    def my_connect(self, st: dict | None) -> list[dict]:
        """This PC's Ubisoft Connect clients signed in to the server (not another player's)."""
        return server.mine(st, "connect", self.own_addresses(st))

    def check_connect(self) -> bool:
        st = server.status(self.cfg)
        acc = self.account()
        mine = self.my_connect(st)
        if mine and (not acc or any(c.get("player") == acc.name for c in mine)):
            self.set("connect", OK, f"signed in ({mine[0]['player']})")
            return True
        sign_in = (f"sign in with {acc.email} and your account's password" if acc else
                   "log in if it asks; any email/password works")
        if mine:
            # signed in as another player (e.g. the account was switched): sign in again with this one
            self.ask("connect", f"Ubisoft Connect is signed in as {mine[0]['player']}, not as {acc.name}. It needs "
                     f"a restart; then sign in with {acc.email} (if it signs in by itself as {mine[0]['player']}, "
                     "sign out in Ubisoft Connect first).", ["Restart Ubisoft Connect"])
            connect.quit_connect()
        elif connect.running():
            # started before the redirect, signed in to Ubisoft, or connected to a server that has since
            # stopped (it does not reconnect by itself): it has to restart against this server
            self.ask("connect", "Ubisoft Connect is running but not signed in to this server. It needs a restart.",
                     ["Restart Ubisoft Connect"])
            connect.quit_connect()
        connect.start(self.paths["connect"])
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            self.set("connect", RUNNING, f"waiting for Ubisoft Connect to sign in ({sign_in})")
            self.wait(2)
            mine = self.my_connect(server.status(self.cfg))
            if mine:
                self.set("connect", OK, f"signed in ({mine[0]['player']})")
                return True
        self.set("connect", FAILED, "Ubisoft Connect did not sign in within 10 minutes")
        return False

    def run_game(self) -> bool:
        inst = self.install
        self.set("game", RUNNING, f"starting Steep through {inst.label}")
        try:
            attach.LOG.unlink(missing_ok=True)
        except OSError:
            pass
        os.startfile(launch_url(inst, self.cfg.steam_app_id))
        observer = attach.ProcessObserver(log)
        timeout = 180
        result = OFFLINE
        for _ in range(3):              # a process that exits right away was a starter: wait for the next one
            pid = attach.wait_for_real_game(timeout, stop=self.cancel.is_set, observer=observer,
                                            exe_dir=inst.exe.parent)
            if pid is None:
                self.set("game", FAILED, "Steep did not start")
                return False
            started = time.monotonic()
            result = self.attach_and_watch(pid, observer)
            if result != STARTER:
                break
            log.info("game pid %d exited after %.1f s without going online: waiting for the next one",
                     pid, time.monotonic() - started)
            self.set("game", RUNNING, "that was a starter process; waiting for the game itself")
            timeout = 60
        if result == ONLINE:
            self.set("game", OK, "game closed - press Play to start it again")
        elif result in (CRASHED, CLOSED_EARLY):
            log.warning("game %s while starting", "crashed" if result == CRASHED else "closed")
            self.set("game", FAILED, "Steep closed while it was starting" + (
                " - it crashed (Windows started its crash report)" if result == CRASHED else "") +
                ". Press Play to try again; if it keeps happening, Open logs shows more.")
        elif self.step("game").state != FAILED:
            self.set("game", FAILED, "game closed before going online - press Play to try again")
        return True

    def attach_and_watch(self, pid: int, observer) -> str:
        """Attach the helper to `pid` and wait until it exits: ONLINE / OFFLINE (it ran, never went online),
        STARTER (a process that exits within seconds and wasn't started as the game: wait for the next),
        CRASHED / CLOSED_EARLY (the real game, started by Ubisoft Connect, ended within 30 s; CRASHED when
        it started a child process - Windows' crash report - before)."""
        started = time.monotonic()
        real = attach.started_by_connect(pid)
        children: set[int] = set()

        def early_end() -> str:
            if not real:
                return STARTER
            log.info("game pid %d (started by Ubisoft Connect) ended after %.1f s; child processes: %s",
                     pid, time.monotonic() - started, sorted(children) or "none")
            return CRASHED if children else CLOSED_EARLY
        # the protector unpacks the game code first (seconds on a hard disk): attaching earlier crashes it
        self.set("game", RUNNING, f"waiting for Steep to finish loading (pid {pid})")
        state = attach.wait_unpacked(pid, game.TARGET_RVA, game.PATCH_SPOT_LEN, game.PATCH_SPOT_SHA256,
                                     timeout=60, stop=self.cancel.is_set, log=log.info)
        log.info("game pid %d code %s after %.1f s", pid, state, time.monotonic() - started)
        if state == attach.GONE:
            return early_end()                           # ended while loading
        if state != attach.READY:
            if self.cancel.is_set():
                raise Cancelled
            self.set("game", FAILED, "Steep's code did not finish loading as expected within a minute; the TLS "
                                     "helper was not attached (it could crash the game). See the logs.")
            return OFFLINE
        self.set("game", RUNNING, f"attaching the TLS helper (pid {pid})")
        for attempt in range(20):                        # right after start-up it may not be ready yet
            if attach.load_dll(pid):
                break
            self.wait(0.5)
        else:
            if pid not in {p.pid for p in attach.game_processes()}:
                return early_end()
            self.set("game", FAILED, "could not load the TLS helper into the game")
            return OFFLINE
        for _ in range(100):
            if attach.log_has("attach"):
                break
            self.wait(0.1)
        self.armer = attach.start_armer(pid, log=log.info)
        self.set("game", RUNNING, "press any key at the title screen; waiting for the game to go online")
        online = False
        while True:
            procs = attach.game_processes()
            if pid not in {p.pid for p in procs}:
                break
            children |= {p.pid for p in procs if p.parent_pid == pid}
            observer.poll()
            st = server.status(self.cfg)
            if not online and server.mine(st, "game", self.own_addresses(st)):
                online = True
                self.set("game", OK, "online on the server - have fun")
            self.wait(1 if time.monotonic() - started < 30 else 2)
        observer.poll()
        self.end_game()
        if online:
            return ONLINE
        return early_end() if time.monotonic() - started < 30 else OFFLINE

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
        lines = footprint.hosts_lines()
        if lines and self.ask("hosts", "The hosts file has the launcher's redirect entries, which point the Ubisoft "
                              "hostnames at the server. "
                              + HOSTS_ADMIN_WHY.format(button="Remove entries", verb="removed"),
                              ["Remove entries", "Skip"], details="\n".join(lines)) == "Skip":
            self.set("hosts", FAILED, "skipped: the redirect entries are still in the hosts file")
            return True
        while footprint.hosts_lines():
            self.set("hosts", RUNNING, "removing the redirect entries (waiting for the Windows admin prompt)")
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
        if self.ask("cert", f"Windows trusts the server's certificate for your user ({len(cas)} installed). Removing "
                    "it needs no admin rights, but when you click \"Remove certificate\", Windows asks you to confirm "
                    f"in its own dialog ({'once per certificate; ' if len(cas) > 1 else ''}answer Yes).",
                    ["Remove certificate", "Skip"]) == "Skip":
            self.set("cert", FAILED, "skipped: the server's certificate is still trusted")
            return True
        for thumb, _ in cas:
            while True:
                self.set("cert", RUNNING, "removing the server's certificate (waiting for the Windows dialog)")
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

    def restore_firewall(self) -> bool:
        rules = firewall.ours()
        if not rules:
            self.set("firewall", OK, "no firewall rules of the launcher")
            return True
        names = sorted({r.name for r in rules})
        while firewall.ours():
            if self.ask("firewall", "Windows Firewall has the launcher's rules for playing on the local network ("
                        + ", ".join(names) + "). Removing them needs admin rights: when you click \"Remove rules\", "
                        "Windows asks for permission (UAC). Only these rules are removed.",
                        ["Remove rules", "Skip"]) == "Skip":
                self.set("firewall", FAILED, "skipped: the launcher's firewall rules are still there")
                return True
            code = firewall.remove_ours()
            if code != 0:
                self.set("firewall", RUNNING, "not removed" + (" (declined)" if code == -1 else f" (error {code})"))
                self.wait(1.5)
        self.set("firewall", OK, f"removed {len(names)} firewall rule(s)")
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

    @property
    def hosts_removal_on_exit(self) -> bool:
        """Closing would take the hosts entries out again (needs a UAC prompt, so the UI asks first)."""
        return self.cfg.remove_hosts_on_exit and self.hosts_added

    def stop(self, remove_hosts: bool = True) -> None:
        """The launcher closes: undo what it set up (the hosts entries only if the user agreed)."""
        self.cancel.set()
        self.end_game()
        if remove_hosts and self.hosts_removal_on_exit:
            elevate.hosts("remove", self.cfg.server_address, str(self.data_dir / "hosts-backups"))
            self.hosts_added = False
        if self.server_proc:
            self.server_proc.stop()
            self.server_proc = None

    def open_logs(self) -> None:
        webbrowser.open((self.data_dir / "logs").as_uri())
