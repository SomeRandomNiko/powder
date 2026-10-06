"""Launcher pieces that need no game, Steam or Connect: DNS check, hosts editing, Steam library lookup,
config, and the session's ask/choose plumbing."""
import threading
import time

import pytest

from steepserver.hostnames import REDIRECT_HOSTS
from steeplauncher import dns, hostsfix, locate
from steeplauncher.config import LauncherConfig
from steeplauncher.session import ACTION, Session


def test_dns_mismatches(monkeypatch):
    table = {h: ["127.0.0.1"] for h in REDIRECT_HOSTS}
    table["dmx.upc.ubisoft.com"] = ["203.0.113.5"]
    table["static3.cdn.ubi.com"] = []
    monkeypatch.setattr(dns, "resolve", lambda h: table[h])
    bad = dns.mismatches("127.0.0.1")
    assert {s.host for s in bad} == {"dmx.upc.ubisoft.com", "static3.cdn.ubi.com"}
    assert dns.mismatches("127.0.0.1", ["public-ubiservices.ubi.com"]) == []


def test_hosts_add_remove_keeps_other_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(dns, "flush_dns", lambda: None)
    hosts = tmp_path / "hosts"
    original = "# sample\n10.0.0.5 nas.local\n"
    hosts.write_text(original)
    backups = tmp_path / "bak"

    hostsfix.apply("add", "192.168.1.10", hosts, backups)
    text = hosts.read_text()
    assert "10.0.0.5 nas.local" in text and "192.168.1.10 dmx.upc.ubisoft.com" in text
    assert len(list(backups.iterdir())) == 1                       # backup of the original
    assert list(backups.iterdir())[0].read_text() == original

    hostsfix.apply("add", "192.168.1.10", hosts, backups)           # idempotent: one block only
    assert hosts.read_text().count("dmx.upc.ubisoft.com") == 1

    hostsfix.apply("remove", "192.168.1.10", hosts, backups)
    assert hosts.read_text().strip() == original.strip()


def test_hosts_replaces_legacy_block(tmp_path, monkeypatch):
    """A block written by tools/redirect.ps1 (other marker, demux on 127.0.0.2) is replaced cleanly."""
    monkeypatch.setattr(dns, "flush_dns", lambda: None)
    hosts = tmp_path / "hosts"
    hosts.write_text("# sample\n# steep-re emulator redirect (remove this block to restore online play)\n"
                     "127.0.0.1 public-ubiservices.ubi.com\n127.0.0.2 dmx.upc.ubisoft.com\n")
    hostsfix.apply("add", "127.0.0.1", hosts, tmp_path / "bak")
    text = hosts.read_text()
    assert "emulator redirect" not in text and "127.0.0.2" not in text
    assert text.splitlines().count("127.0.0.1 public-ubiservices.ubi.com") == 1
    assert "127.0.0.1 dmx.upc.ubisoft.com" in text.splitlines()


def test_hosts_block_lines():
    block = dns.hosts_block("127.0.0.1")
    assert block.splitlines()[0].startswith("# " + dns.MARKER)
    assert dns.lines_for_display("127.0.0.1").splitlines() == [f"127.0.0.1 {h}" for h in REDIRECT_HOSTS]


def test_steam_library_lookup(tmp_path, monkeypatch):
    steam = tmp_path / "Steam"
    other = tmp_path / "Games"
    (steam / "steamapps").mkdir(parents=True)
    exe = other / "steamapps" / "common" / "Steep" / "Steep.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(other).replace("\\", "\\\\")))
    monkeypatch.setattr(locate, "steam_path", lambda: steam)
    assert locate.steep_exe() == exe


def test_real_game_is_the_one_connect_started(monkeypatch):
    from steeplauncher import attach
    # (pid, parent pid, threads, exe) as in a real launch: Steam's starter, then Connect's real game
    procs = [(1, 0, 90, "steam.exe"), (2, 0, 30, "UbisoftGameLauncher.exe"),
             (10, 1, 6, "Steep.exe"), (11, 2, 6, "Steep.exe")]
    monkeypatch.setattr(attach, "_snapshot", lambda: procs)
    assert attach.real_game_pid() == 11                 # by parent, even before it has many threads
    procs[:] = [(1, 0, 90, "steam.exe"), (10, 1, 6, "Steep.exe")]
    assert attach.real_game_pid() is None               # only the starter: not the game
    procs[:] = [(5, 0, 9, "renamedlauncher.exe"), (12, 5, 37, "Steep.exe")]
    assert attach.real_game_pid() == 12                 # fallback: thread count


def test_config(tmp_path):
    p = tmp_path / "launcher.toml"
    p.write_text('server_address = "192.168.1.10"\nremove_hosts_on_exit = true\n')
    cfg = LauncherConfig.load(p)
    assert cfg.server_address == "192.168.1.10" and cfg.remove_hosts_on_exit
    assert cfg.server_config_path() == tmp_path / "config.toml"
    assert cfg.status_url == "https://192.168.1.10:443/_steep/status"
    p.write_text('typo = 1\n')
    with pytest.raises(ValueError):
        LauncherConfig.load(p)


def test_session_ask_waits_for_choice(tmp_path):
    s = Session(LauncherConfig(), tmp_path)
    got = {}
    t = threading.Thread(target=lambda: got.setdefault("c", s.ask("dns", "pick", ["A", "B"])))
    t.start()
    for _ in range(50):
        if s.step("dns").state == ACTION:
            break
        time.sleep(0.02)
    assert s.step("dns").actions == ["A", "B"]
    s.choose("nope")                     # not offered: ignored
    s.choose("B")
    t.join(2)
    assert got["c"] == "B"


# ---- restore system ------------------------------------------------------------------------------
STORE = """Root "Trusted Root Certification Authorities"
================ Certificate 0 ================
Serial Number: 01
Issuer: O=steepserver (local), CN=steepserver local CA
Subject: O=steepserver (local), CN=steepserver local CA
Cert Hash(sha1): aa bb cc dd
================ Certificate 1 ================
Serial Number: 02
Subject: CN=Some Public Root, O=Example
Cert Hash(sha1): 11223344
================ Certificate 2 ================
Serial Number: 03
Subject: O=steepserver (local), CN=steepserver local CA
Cert Hash(sha1): eeff0011
CertUtil: -store command completed successfully.
"""


def test_certutil_store_parsing(monkeypatch):
    from steepserver import trust
    assert trust.parse_store(STORE)[1] == ("11223344", "CN=Some Public Root, O=Example")

    class R:
        stdout, returncode = STORE, 0
    monkeypatch.setattr(trust, "_certutil", lambda *a: R)
    assert [t for t, _ in trust.installed_cas()] == ["AABBCCDD", "EEFF0011"]    # only the server's CAs


class FakePC:
    """Everything the restore flow touches, in memory."""

    def __init__(self, hosts=True, cas=2, connect=True, game=False):
        self.hosts = ["# marker", "127.0.0.1 public-ubiservices.ubi.com"] if hosts else []
        self.cas = [(f"T{i}", "CN=steepserver local CA") for i in range(cas)]
        self.connect, self.game, self.calls = connect, game, []

    def install(self, monkeypatch, uac_ok=True):
        from steepserver import trust
        from steeplauncher import attach, connect, elevate, footprint, server

        def hosts(action, address, backup_dir):
            self.calls.append(("hosts", action))
            if uac_ok:
                self.hosts = []
            return 0 if uac_ok else -1

        def remove(thumb):
            self.calls.append(("cert", thumb))
            self.cas = [c for c in self.cas if c[0] != thumb]

        def quit_connect():
            self.calls.append(("quit connect",))
            self.connect = False

        monkeypatch.setattr(attach, "game_processes", lambda: [1] if self.game else [])
        monkeypatch.setattr(connect, "running", lambda: self.connect)
        monkeypatch.setattr(connect, "quit_connect", quit_connect)
        monkeypatch.setattr(connect, "start", lambda exe: self.calls.append(("start connect",)))
        monkeypatch.setattr(footprint, "hosts_lines", lambda *a: list(self.hosts))
        monkeypatch.setattr(elevate, "hosts", hosts)
        monkeypatch.setattr(dns, "flush_dns", lambda: self.calls.append(("flush",)))
        monkeypatch.setattr(trust, "installed_cas", lambda *a: list(self.cas))
        monkeypatch.setattr(trust, "remove_thumbprint", remove)
        monkeypatch.setattr(server, "status", lambda *a, **k: None)
        monkeypatch.setattr(locate, "connect_exe", lambda: "upc.exe")


class Scripted(Session):
    def __init__(self, *a, answers=()):
        super().__init__(*a)
        self.answers, self.asked = list(answers), []

    def ask(self, key, text, actions, details=""):
        self.asked.append((key, actions))
        return self.answers.pop(0)


def test_restore_undoes_hosts_and_certificates(tmp_path, monkeypatch):
    from steeplauncher import footprint
    from steeplauncher.session import restore_steps
    pc = FakePC()
    pc.install(monkeypatch)
    assert not footprint.scan(LauncherConfig()).clean
    s = Scripted(LauncherConfig(), tmp_path, answers=["Quit Ubisoft Connect", "Start Ubisoft Connect"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [("quit connect",), ("hosts", "remove"), ("flush",), ("cert", "T0"), ("cert", "T1"),
                        ("start connect",)]
    assert all(st.state == "ok" for st in s.steps) and footprint.scan(LauncherConfig()).clean


def test_restore_on_a_clean_pc_changes_nothing(tmp_path, monkeypatch):
    from steeplauncher.session import restore_steps
    pc = FakePC(hosts=False, cas=0, connect=True)
    pc.install(monkeypatch)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Leave it running"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [] and all(st.state == "ok" for st in s.steps)


def test_restore_declined_uac_is_reported(tmp_path, monkeypatch):
    from steeplauncher.session import FAILED, restore_steps
    pc = FakePC(cas=0, connect=False)
    pc.install(monkeypatch, uac_ok=False)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Try again", "Skip"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert [c for c in pc.calls if c[0] == "hosts"] == [("hosts", "remove")] * 2
    assert s.step("hosts").state == FAILED and s.step("check").state == FAILED


def test_console_restore_on_a_clean_pc(tmp_path, monkeypatch):
    from steeplauncher.__main__ import main
    FakePC(hosts=False, cas=0, connect=False).install(monkeypatch)
    (tmp_path / "launcher.toml").write_text("")
    assert main(["-c", str(tmp_path / "launcher.toml"), "--restore"]) == 0
