"""Launcher: hosting for / joining other PCs, firewall rules, the account (no Windows calls: all faked)."""
import json
from pathlib import Path

import pytest

from powder.launcher import account, firewall, network, server
from powder.launcher.config import LauncherConfig
from powder.launcher.session import ACTION, HOST, JOIN, LOCAL, OK, Session
from test_launcher import FakePC, Scripted

GAME = Path("C:/Games/Steep/Steep.exe")


def status(you="127.0.0.1", connect=(), game=(), **extra):
    return {"server": "powder", "version": "0", "you": you, "public_host": extra.pop("public_host", "127.0.0.1"),
            "accounts": True, "connect": list(connect), "game": list(game), **extra}


def test_status_entries_of_this_pc_only():
    st = status(you="192.0.2.20", connect=[{"player": "Host", "peer": "127.0.0.1:5000"},
                                           {"player": "Guest", "peer": "192.0.2.20:5001"}],
                game=[{"player": "Host", "peer": "192.0.2.10:9103"}])
    assert [c["player"] for c in server.mine(st, "connect")] == ["Guest"]
    assert server.mine(st, "game") == []
    # on the server PC: its own game reaches the game server at the public address
    st["you"] = "127.0.0.1"
    assert [g["player"] for g in server.mine(st, "game", {"127.0.0.1", "192.0.2.10"})] == ["Host"]
    # an older server without "you": every entry
    assert len(server.mine({"connect": [{"player": "x", "peer": "a:1"}]}, "connect")) == 1


def test_modes_change_the_effective_config(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.0.2.10", "198.51.100.7"])
    s = Session(LauncherConfig(), tmp_path)
    assert s.mode() == LOCAL and s.cfg.server_mode == "local" and s.cfg.server_address == "127.0.0.1"
    s.set_mode(JOIN, join_address=" 192.0.2.10 ")
    assert s.cfg.server_mode == "remote" and s.cfg.server_address == "192.0.2.10"
    s.set_mode(HOST, host_address="198.51.100.7")
    assert s.cfg.server_mode == "local" and s.cfg.server_address == "127.0.0.1"
    assert s.host_address() == "198.51.100.7"
    assert Session(LauncherConfig(), tmp_path).mode() == HOST                     # remembered
    # launcher.toml's remote server is the default until the row is used
    assert Session(LauncherConfig(server_mode="remote", server_address="192.0.2.30"), tmp_path / "x").mode() == JOIN


class FakeProcess:
    started = []

    def __init__(self, cfg):
        self.host_for = None

    def start(self, log_dir, use_pack=True, host_for=None):
        self.host_for = host_for
        FakeProcess.started.append(host_for)

    def running(self):
        return True

    def stop(self):
        FakeProcess.started.append("stopped")


def test_hosting_starts_the_server_for_the_network(tmp_path, monkeypatch):
    from powder.launcher import ports
    FakeProcess.started = []
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setattr(server, "ServerProcess", FakeProcess)
    monkeypatch.setattr(ports, "busy", lambda bind, needed: [])
    up = {"st": None}
    monkeypatch.setattr(server, "status", lambda *a, **k: up["st"])
    monkeypatch.setattr(server, "wait_up", lambda *a, **k: status(public_host="192.0.2.10"))
    s = Scripted(LauncherConfig(), tmp_path)
    s.set_mode(HOST)
    assert s.check_server() and FakeProcess.started == ["192.0.2.10"]
    assert "others join with 192.0.2.10" in s.step("server").text
    # back to this PC only: the running server is restarted without hosting
    up["st"] = status(public_host="192.0.2.10")
    s.set_mode(LOCAL)
    assert s.check_server() and FakeProcess.started == ["192.0.2.10", "stopped", None]


def test_join_needs_an_address(tmp_path):
    s = Scripted(LauncherConfig(), tmp_path)
    s.set_mode(JOIN, join_address="")
    assert not s.check_server() and "address" in s.step("server").text


def test_firewall_rules_are_added_only_after_the_button(tmp_path, monkeypatch):
    pc = FakePC()
    pc.install(monkeypatch)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Add firewall rules"])
    s.install = type("I", (), {"exe": GAME})()
    s.set_mode(HOST)
    assert s.check_firewall()
    assert s.asked == [("firewall", ["Add firewall rules", "Skip"])]
    assert pc.calls == [("firewall add", (firewall.SERVER_RULE, firewall.GAME_RULE))]
    assert s.step("firewall").state == OK
    # next time everything is there: no question, no prompt
    s2 = Scripted(LauncherConfig(), tmp_path)
    s2.install = s.install
    assert s2.check_firewall() and s2.asked == [] and len(pc.calls) == 1


def test_joining_needs_only_the_game_rule_and_this_pc_only_none(tmp_path, monkeypatch):
    pc = FakePC()
    pc.install(monkeypatch)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Add firewall rules"])
    s.install = type("I", (), {"exe": GAME})()
    s.set_mode(JOIN, join_address="192.0.2.10")
    assert s.check_firewall() and pc.calls == [("firewall add", (firewall.GAME_RULE,))]
    s.set_mode(LOCAL)
    s.answers = []
    assert s.check_firewall() and len(pc.calls) == 1


def test_a_block_rule_for_steep_is_reported(tmp_path, monkeypatch):
    pc = FakePC()
    pc.install(monkeypatch)
    pc.game_rules = [firewall.Rule("Steep", "Block", True, firewall.norm(GAME).lower())]
    s = Scripted(LauncherConfig(), tmp_path, answers=["Continue anyway", "Skip"])
    s.install = type("I", (), {"exe": GAME})()
    s.set_mode(JOIN, join_address="192.0.2.10")
    assert s.check_firewall()
    assert [k for k, _ in s.asked] == ["firewall", "firewall"] and pc.calls == []


def test_restore_removes_our_firewall_rules(tmp_path, monkeypatch):
    from powder.launcher import footprint
    from powder.launcher.session import restore_steps
    pc = FakePC(hosts=False, cas=0, connect=False, rules=[firewall.GAME_RULE])
    pc.install(monkeypatch)
    assert not footprint.scan(LauncherConfig()).clean
    s = Scripted(LauncherConfig(), tmp_path, answers=["Remove rules", "Done"])
    s.steps = restore_steps()
    s._run(s.restore_flow())
    assert pc.calls == [("firewall remove",)] and footprint.scan(LauncherConfig()).clean
    assert all(st.state == OK for st in s.steps)


def test_firewall_script_allows_only_the_local_network():
    script = firewall.add_script([firewall.Program(GAME, firewall.GAME_RULE, ("UDP",))])
    assert script.count("New-NetFirewallRule") == 1 and "-RemoteAddress LocalSubnet" in script
    assert "-Group 'Powder'" in script and f"-Program '{GAME}'" in script and "Remove-NetFirewallRule" in script


def test_firewall_rule_matching():
    steep = firewall.Program(GAME, firewall.GAME_RULE, ("UDP",))
    exe = firewall.norm(GAME).lower()
    tcp_only = firewall.Rule("x", "Allow", True, exe, protocol="TCP")
    assert not firewall.allowed(steep, [tcp_only], [])
    assert firewall.allowed(steep, [tcp_only, firewall.Rule("y", "Allow", True, exe, protocol="UDP")], [])
    public_only = firewall.Rule("z", "Allow", True, exe, profile="Public")
    assert firewall.allowed(steep, [public_only], ["Public"])
    assert not firewall.allowed(steep, [public_only], ["Private"])
    assert not firewall.allowed(steep, [firewall.Rule("off", "Allow", False, exe)], [])
    assert firewall.blocked(steep, [firewall.Rule("b", "Block", True, exe, profile="Private")], ["Private"])


def test_play_keeps_its_server_setup_while_the_row_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "lan_addresses", lambda: ["192.0.2.10"])
    s = Scripted(LauncherConfig(), tmp_path)
    s.set_mode(HOST)
    setup = s.setup
    s.thread = type("T", (), {"is_alive": lambda self: True})()       # a Play run in progress
    s.set_mode(JOIN, join_address="192.0.2.99")
    assert s.setup == setup and s.cfg.server_mode == "local"


def test_account_step_registers_and_remembers_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "status", lambda *a, **k: status())
    monkeypatch.setattr(account, "takes_over", lambda cfg, ca: "OldRider")
    calls = []

    def register(cfg, ca, email, password, name):
        calls.append((email, password, name))
        if len(calls) == 1:
            raise account.AccountError("An account with this email already exists.")
        return account.Account(email, name or "OldRider")
    monkeypatch.setattr(account, "register", register)
    s = Scripted(LauncherConfig(), tmp_path, answers=["Register…", "Register…"])
    asked_dialogs = []
    s.dialog = lambda kind, info: asked_dialogs.append((kind, info)) or {
        "email": "rider@example.com", "password": "secret password", "name": ""}
    assert s.check_account()
    assert asked_dialogs[0] == ("register", {"takes_over": "OldRider"})
    assert len(calls) == 2 and s.step("account").text == "OldRider (rider@example.com)"
    saved = (tmp_path / "settings.json").read_text()
    assert "rider@example.com" in saved and "secret password" not in saved
    # the next Play: already set up
    s2 = Scripted(LauncherConfig(), tmp_path)
    assert s2.check_account() and s2.asked == []


def test_connect_step_only_counts_this_pcs_connect(tmp_path, monkeypatch):
    from powder.launcher import connect
    st = status(you="192.0.2.20", connect=[{"player": "Host", "peer": "192.0.2.10:5000"}])
    monkeypatch.setattr(server, "status", lambda *a, **k: st)
    monkeypatch.setattr(connect, "running", lambda: False)
    started = []
    monkeypatch.setattr(connect, "start", lambda exe: started.append(exe) or st["connect"].append(
        {"player": "Guest", "peer": "192.0.2.20:5001"}))
    monkeypatch.setattr(Session, "wait", lambda self, seconds: None)
    s = Scripted(LauncherConfig(), tmp_path)
    s.set_mode(JOIN, join_address="192.0.2.10")
    s.paths = {"connect": "upc.exe"}
    assert s.check_connect() and started == ["upc.exe"] and s.step("connect").text == "signed in (Guest)"
