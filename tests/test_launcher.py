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
