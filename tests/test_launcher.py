"""Launcher pieces that need no game, Steam or Connect: DNS check, hosts editing, Steam library lookup,
config, and the session's ask/choose plumbing."""
import threading
import time
from pathlib import Path

import pytest

from powder.server.hostnames import REDIRECT_HOSTS
from powder.launcher import dns, hostsfix, locate
from powder.launcher.config import LauncherConfig
from powder.launcher.session import ACTION, Session, launch_url, play_buttons


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


def test_hosts_block_from_before_the_rename_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(dns, "flush_dns", lambda: None)
    hosts = tmp_path / "hosts"
    hosts.write_text("# sample\n# steep-re launcher redirect (remove this block to restore normal online play)\n"
                     "127.0.0.1 public-ubiservices.ubi.com\n")
    assert len(dns.read_block(hosts.read_text())) == 2
    hostsfix.apply("remove", "127.0.0.1", hosts, tmp_path / "bak")
    assert hosts.read_text().strip() == "# sample"


def test_hosts_block_lines():
    block = dns.hosts_block("127.0.0.1")
    assert block.splitlines()[0].startswith("# " + dns.MARKER)
    assert dns.lines_for_display("127.0.0.1").splitlines() == [f"127.0.0.1 {h}" for h in REDIRECT_HOSTS]


class FakeRegistry:
    """{(root, key): {value: data}}; subkeys are derived from the keys."""

    def __init__(self, monkeypatch, entries: dict):
        self.entries = entries
        monkeypatch.setattr(locate, "_reg", lambda root, key, value: entries.get((root, key), {}).get(value))
        monkeypatch.setattr(locate, "_subkeys", self.subkeys)

    def subkeys(self, root, key):
        prefix = key + "\\"
        return sorted({k[len(prefix):].split("\\")[0] for r, k in self.entries if r == root and k.startswith(prefix)})


def _exe(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Steep.exe").write_bytes(b"MZ")
    return folder / "Steep.exe"


def _steam(tmp_path, library: Path, installdir: str) -> dict:
    """A Steam install whose second library (another 'drive') has Steep under `installdir`."""
    steam = tmp_path / "Steam"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(library).replace("\\", "\\\\")))
    (library / "steamapps").mkdir(parents=True, exist_ok=True)
    (library / "steamapps" / "appmanifest_460920.acf").write_text(
        '"AppState"\n{\n "appid" "460920"\n "installdir" "%s"\n}\n' % installdir)
    return {("HKCU", r"Software\Valve\Steam"): {"SteamPath": str(steam)}}


def _connect(folder: Path, product: str = "3279") -> dict:
    return {("HKLM", rf"{locate.UBI_INSTALLS}\{product}"): {"InstallDir": str(folder).replace("\\", "/") + "/"},
            ("HKLM", rf"{locate.UBI_INSTALLS}\5487"): {"InstallDir": "C:/elsewhere/OtherGame/"}}


def test_finds_steam_version_in_any_library(tmp_path, monkeypatch):
    lib = tmp_path / "D" / "Games"
    exe = _exe(lib / "steamapps" / "common" / "Steep Winter")         # not the default folder name
    FakeRegistry(monkeypatch, _steam(tmp_path, lib, "Steep Winter"))
    assert locate.find_installs() == [locate.Install(locate.STEAM, exe)]


def test_finds_connect_version_in_any_folder(tmp_path, monkeypatch):
    exe = _exe(tmp_path / "my games" / "ubi" / "Steep")
    FakeRegistry(monkeypatch, _connect(exe.parent))
    (inst,) = locate.find_installs()
    assert inst.kind == locate.CONNECT and inst.product_id == 3279 and locate.same_exe(inst.exe, exe)


def test_connect_version_from_uninstall_entry_only(tmp_path, monkeypatch):
    exe = _exe(tmp_path / "Steep")
    FakeRegistry(monkeypatch, {("HKLM", rf"{locate.UNINSTALL[0]}\Uplay Install 3279"): {"InstallLocation": str(exe.parent)},
                               ("HKLM", rf"{locate.UNINSTALL[0]}\Something Else"): {"InstallLocation": "C:/x"}})
    assert [(i.kind, i.product_id) for i in locate.find_installs()] == [(locate.CONNECT, 3279)]


def test_both_versions_and_no_duplicates(tmp_path, monkeypatch):
    lib = tmp_path / "SteamLib"
    steam_exe = _exe(lib / "steamapps" / "common" / "Steep")
    connect_exe = _exe(tmp_path / "Ubisoft" / "Steep")
    reg = {**_steam(tmp_path, lib, "Steep"), **_connect(connect_exe.parent),
           ("HKLM", rf"{locate.UNINSTALL[0]}\Uplay Install 3279"): {"InstallLocation": str(connect_exe.parent)},
           # Connect also records the Steam copy: it stays the Steam version, listed once
           ("HKLM", rf"{locate.UBI_INSTALLS}\3280"): {"InstallDir": str(steam_exe.parent)}}
    FakeRegistry(monkeypatch, reg)
    installs = locate.find_installs()
    assert [i.kind for i in installs] == [locate.STEAM, locate.CONNECT]
    assert [label for label, _ in play_buttons(installs)] == ["Play (Steam)", "Play (Ubisoft Connect)"]


def test_nothing_installed_and_override(tmp_path, monkeypatch):
    FakeRegistry(monkeypatch, {})
    assert locate.find_installs() == []
    assert play_buttons([]) == [("Play", None)]
    exe = _exe(tmp_path / "Portable" / "Steep")
    (inst,) = locate.find_installs(override=str(exe))
    assert inst.kind == locate.STEAM and play_buttons([inst]) == [("Play", inst)]


def test_launch_url_per_version():
    assert launch_url(locate.Install(locate.STEAM, Path("s")), 460920) == "steam://rungameid/460920"
    assert launch_url(locate.Install(locate.CONNECT, Path("c"), 3279), 460920) == "uplay://launch/3279/0"


def test_native_helpers_are_looked_up_in_the_repository():
    from powder.launcher import attach
    assert attach.NATIVE_BIN == Path(__file__).resolve().parents[1] / "native" / "bin"


def test_real_game_is_the_one_connect_started(monkeypatch):
    from powder.launcher import attach
    # (pid, parent pid, threads, exe) as in a real launch: Steam's starter, then Connect's real game
    procs = [(1, 0, 90, "steam.exe"), (2, 0, 30, "UbisoftGameLauncher.exe"),
             (10, 1, 6, "Steep.exe"), (11, 2, 6, "Steep.exe")]
    monkeypatch.setattr(attach, "_snapshot", lambda: procs)
    assert attach.real_game_pid() == 11                 # by parent, even before it has many threads
    procs[:] = [(1, 0, 90, "steam.exe"), (10, 1, 6, "Steep.exe")]
    assert attach.real_game_pid() is None               # only the starter: not the game
    procs[:] = [(3, 0, 60, "upc.exe"), (13, 3, 6, "Steep.exe")]
    assert attach.real_game_pid() is None               # Connect version's starter (started by upc.exe)
    procs[:] = [(5, 0, 9, "renamedlauncher.exe"), (12, 5, 37, "Steep.exe")]
    assert attach.real_game_pid() == 12                 # fallback: thread count
    # both versions running: only the one from the chosen install counts
    procs[:] = [(2, 0, 30, "UbisoftGameLauncher.exe"), (20, 2, 40, "Steep.exe"), (21, 2, 40, "Steep.exe")]
    paths = {20: r"C:\Program Files (x86)\Steam\steamapps\common\Steep\Steep.exe", 21: r"D:\Ubi\Steep\Steep.exe"}
    monkeypatch.setattr(attach, "image_path", lambda pid: paths.get(pid, ""))
    assert attach.real_game_pid(exe_dir=Path("D:/Ubi/Steep")) == 21
    assert attach.real_game_pid(exe_dir=Path(r"C:\Program Files (x86)\Steam\steamapps\common\Steep")) == 20


def test_config(tmp_path):
    p = tmp_path / "launcher.toml"
    p.write_text('server_address = "192.168.1.10"\nremove_hosts_on_exit = true\n')
    cfg = LauncherConfig.load(p)
    assert cfg.server_address == "192.168.1.10" and cfg.remove_hosts_on_exit
    assert cfg.server_config_path() == tmp_path / "config.toml"
    assert cfg.status_url == "https://192.168.1.10:443/_powder/status"
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
Issuer: O=Powder (Steep), CN=Powder (Steep) local CA
Subject: O=Powder (Steep), CN=Powder (Steep) local CA
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
    from powder.server import trust
    assert trust.parse_store(STORE)[1] == ("11223344", "CN=Some Public Root, O=Example")

    class R:
        stdout, returncode = STORE, 0
    monkeypatch.setattr(trust, "_certutil", lambda *a: R)
    # only the server's CAs: the current name and one generated before the rename to Powder
    assert [t for t, _ in trust.installed_cas()] == ["AABBCCDD", "EEFF0011"]


class FakePC:
    """Everything the restore flow touches, in memory."""

    def __init__(self, hosts=True, cas=2, connect=True, game=False, rules=()):
        self.hosts = ["# marker", "127.0.0.1 public-ubiservices.ubi.com"] if hosts else []
        self.cas = [(f"T{i}", "CN=Powder (Steep) local CA") for i in range(cas)]
        self.connect, self.game, self.calls = connect, game, []
        self.rules = list(rules)                         # our firewall rules (names)
        self.game_rules: list = []                       # anyone's rules for Steep.exe

    def install(self, monkeypatch, uac_ok=True):
        from powder.server import trust
        from powder.launcher import attach, connect, elevate, firewall, footprint, server

        def add_rules(needed, what):
            self.calls.append(("firewall add", tuple(what)))
            if uac_ok:
                for name in what:
                    if name == firewall.SERVER_RULE:
                        self.rules += [f"{name} (TCP)", f"{name} (UDP)"]
                    else:
                        self.game_rules.append(firewall.Rule(name, "Allow", True, str(needed.game_exe), "Powder"))
                        self.rules.append(name)
            return 0 if uac_ok else -1

        def remove_rules():
            self.calls.append(("firewall remove",))
            if uac_ok:
                self.rules = []
            return 0 if uac_ok else -1

        monkeypatch.setattr(firewall, "ours", lambda: [firewall.Rule(n, "Allow", True, "", "Powder") for n in self.rules])
        monkeypatch.setattr(firewall, "for_program", lambda exe: list(self.game_rules))
        monkeypatch.setattr(firewall, "add", add_rules)
        monkeypatch.setattr(firewall, "remove_ours", remove_rules)

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
    from powder.launcher import footprint
    from powder.launcher.session import restore_steps
    pc = FakePC()
    pc.install(monkeypatch)
    assert not footprint.scan(LauncherConfig()).clean
    s = Scripted(LauncherConfig(), tmp_path, answers=["Quit Ubisoft Connect", "Remove entries", "Remove certificate",
                                                       "Start Ubisoft Connect"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [("quit connect",), ("hosts", "remove"), ("flush",), ("cert", "T0"), ("cert", "T1"),
                        ("start connect",)]
    # every Windows prompt was announced: the step asked (and was answered) before the call
    assert [k for k, _ in s.asked] == ["connect", "hosts", "cert", "check"]
    assert all(st.state == "ok" for st in s.steps) and footprint.scan(LauncherConfig()).clean


def test_restore_skip_means_no_windows_prompt(tmp_path, monkeypatch):
    from powder.launcher.session import FAILED, restore_steps
    pc = FakePC(connect=False)
    pc.install(monkeypatch)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Skip", "Skip"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [] and s.step("hosts").state == FAILED and s.step("cert").state == FAILED


def test_restore_on_a_clean_pc_changes_nothing(tmp_path, monkeypatch):
    from powder.launcher.session import restore_steps
    pc = FakePC(hosts=False, cas=0, connect=True)
    pc.install(monkeypatch)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Leave it running"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [] and all(st.state == "ok" for st in s.steps)


def test_restore_declined_uac_is_reported(tmp_path, monkeypatch):
    from powder.launcher.session import FAILED, restore_steps
    pc = FakePC(cas=0, connect=False)
    pc.install(monkeypatch, uac_ok=False)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Remove entries", "Try again", "Skip"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert [c for c in pc.calls if c[0] == "hosts"] == [("hosts", "remove")] * 2
    assert s.step("hosts").state == FAILED and s.step("check").state == FAILED


def test_console_restore_on_a_clean_pc(tmp_path, monkeypatch):
    from powder.launcher.__main__ import main
    FakePC(hosts=False, cas=0, connect=False).install(monkeypatch)
    (tmp_path / "launcher.toml").write_text("")
    monkeypatch.setattr("builtins.input", lambda *a: "2")           # "Done", not "Start Ubisoft Connect"
    assert main(["-c", str(tmp_path / "launcher.toml"), "--restore"]) == 0


def test_run_game_skips_a_starter_that_exits(tmp_path, monkeypatch):
    """If the attached process turns out to be a starter (exits within seconds), wait for the real game."""
    from powder.launcher import attach, server
    import powder.launcher.session as session_mod
    alive: set[int] = set()
    found = iter([101, 202])                         # the starter first, then the game
    attached = []

    def wait_for_real_game(*a, **k):
        pid = next(found)
        alive.add(pid)
        return pid

    def load_dll(pid):
        attached.append(pid)
        if pid == 101:
            alive.discard(101)                       # the starter exits right after the attach
        return True

    def status(*a, **k):
        if 202 in alive:
            alive.discard(202)                       # the game goes online, then the player quits
            return {"game": [1]}
        return {"game": []}

    monkeypatch.setattr(session_mod.os, "startfile", lambda url: None, raising=False)
    monkeypatch.setattr(attach, "wait_for_real_game", wait_for_real_game)
    monkeypatch.setattr(attach, "wait_unpacked", lambda pid, *a, **k: attach.READY)
    monkeypatch.setattr(attach, "load_dll", load_dll)
    monkeypatch.setattr(attach, "log_has", lambda text: True)
    monkeypatch.setattr(attach, "start_armer", lambda pid, log=None: None)
    monkeypatch.setattr(attach, "game_processes", lambda: [attach.GameProcess(p, 40) for p in sorted(alive)])
    monkeypatch.setattr(server, "status", status)
    s = Session(LauncherConfig(), tmp_path)
    s.wait = lambda seconds: None
    s.install = locate.Install(locate.CONNECT, tmp_path / "Steep.exe", 3279)
    s.steps = session_mod.play_steps()
    assert s.run_game()
    assert attached == [101, 202] and s.step("game").state == "ok"


# ---- attach only once the game code is unpacked -------------------------------------------------------
def test_unpacked_needs_final_protection_and_known_code():
    import hashlib
    from powder.launcher import attach
    code = b"\x48\x89\x5c\x24" * 16
    want = hashlib.sha256(code).hexdigest()
    PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE, PAGE_READWRITE = 0x20, 0x40, 0x04
    assert attach.unpacked((PAGE_EXECUTE_READ, code), 64, want)
    assert not attach.unpacked((PAGE_EXECUTE_READWRITE, code), 64, want)   # protector still writing
    assert not attach.unpacked((PAGE_READWRITE, code), 64, want)
    assert not attach.unpacked((PAGE_EXECUTE_READ, b"\x00" * 64), 64, want)  # not unpacked yet
    assert not attach.unpacked((PAGE_EXECUTE_READ, b""), 64, want)          # unreadable
    assert not attach.unpacked(None, 64, want)


def test_wait_unpacked_follows_the_protector(monkeypatch):
    import hashlib
    from powder.launcher import attach
    code = bytes(range(64))
    want = hashlib.sha256(code).hexdigest()
    states = iter([None, (0x04, b""), (0x40, code), (0x20, code)])     # unreadable, writing, written, done
    monkeypatch.setattr(attach, "game_processes", lambda: [attach.GameProcess(7, 40)])
    monkeypatch.setattr(attach, "code_state", lambda pid, rva, n: next(states))
    lines = []
    assert attach.wait_unpacked(7, 0x1000, 64, want, timeout=5, poll=0,
                                log=lambda *a: lines.append(a[0] % a[1:])) == attach.READY
    assert len(lines) == 4 and "0x20, code known" in lines[-1]
    monkeypatch.setattr(attach, "game_processes", lambda: [])
    assert attach.wait_unpacked(7, 0x1000, 64, want, timeout=5, poll=0) == attach.GONE
    monkeypatch.setattr(attach, "game_processes", lambda: [attach.GameProcess(7, 40)])
    monkeypatch.setattr(attach, "code_state", lambda pid, rva, n: (0x40, b""))
    assert attach.wait_unpacked(7, 0x1000, 64, want, timeout=0.05, poll=0.01) == attach.TIMEOUT


def test_no_attach_when_the_code_never_appears(tmp_path, monkeypatch):
    from powder.launcher import attach
    import powder.launcher.session as session_mod
    loaded = []
    monkeypatch.setattr(attach, "wait_unpacked", lambda *a, **k: attach.TIMEOUT)
    monkeypatch.setattr(attach, "load_dll", lambda pid: loaded.append(pid) or True)
    s = Session(LauncherConfig(), tmp_path)
    s.steps = session_mod.play_steps()
    assert s.attach_and_watch(5, observer=None) == session_mod.OFFLINE
    assert loaded == [] and s.step("game").state == "failed"         # never risks crashing the game


def test_a_crash_of_the_real_game_is_reported_not_taken_for_a_starter(tmp_path, monkeypatch):
    from powder.launcher import attach, server
    import powder.launcher.session as session_mod
    procs = [attach.GameProcess(301, 40, 9, "UbisoftGameLauncher.exe")]           # the real game
    found = iter([301])
    calls = {"n": 0}

    def game_processes():
        calls["n"] += 1
        if calls["n"] == 3:                     # it starts its crash reporter, then both are gone
            procs.append(attach.GameProcess(302, 0, 301, "steep.exe"))
        if calls["n"] >= 5:
            procs.clear()
        return list(procs)

    monkeypatch.setattr(session_mod.os, "startfile", lambda url: None, raising=False)
    monkeypatch.setattr(attach, "wait_for_real_game", lambda *a, **k: next(found))
    monkeypatch.setattr(attach, "wait_unpacked", lambda *a, **k: attach.READY)
    monkeypatch.setattr(attach, "load_dll", lambda pid: True)
    monkeypatch.setattr(attach, "log_has", lambda text: True)
    monkeypatch.setattr(attach, "start_armer", lambda pid, log=None: None)
    monkeypatch.setattr(attach, "game_processes", game_processes)
    monkeypatch.setattr(server, "status", lambda *a, **k: {"game": []})
    s = Session(LauncherConfig(), tmp_path)
    s.wait = lambda seconds: None
    s.install = locate.Install(locate.CONNECT, tmp_path / "Steep.exe", 3279)
    s.steps = session_mod.play_steps()
    assert s.run_game()
    step = s.step("game")
    assert step.state == "failed" and "crashed" in step.text and "starter" not in step.text


def test_patch_spot_hash_matches_the_unpacked_steam_build():
    """Only where the unpacked dump exists (it stays outside the repository)."""
    import hashlib
    from pathlib import Path
    import pytest
    from powder.launcher import game
    dump = Path(r"C:\dev\steep-re\bin\Steep_unpacked.exe")
    if not dump.exists():
        pytest.skip("no unpacked Steep.exe dump on this machine")
    with open(dump, "rb") as f:
        f.seek(game.TARGET_RVA)
        assert hashlib.sha256(f.read(game.PATCH_SPOT_LEN)).hexdigest() == game.PATCH_SPOT_SHA256
