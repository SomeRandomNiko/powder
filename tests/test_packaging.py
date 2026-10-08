"""Running from an installed folder: paths, the one-program dispatcher, versions, config defaults."""
import sys
from pathlib import Path

from powder import __main__ as dispatcher
from powder import paths, version
from powder.server.config import Config


def test_paths_in_a_checkout():
    assert not paths.FROZEN
    assert paths.native_dir() == paths.REPO / "native" / "bin"
    assert paths.self_command("server", "run") == [sys.executable, "-m", "powder", "server", "run"]


def test_paths_when_installed(monkeypatch, tmp_path):
    exe = tmp_path / "Programs" / "Powder" / "Powder.exe"
    monkeypatch.setattr(paths, "FROZEN", True)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(exe.parent / "_internal"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.user_root() == tmp_path / "Local" / "Powder"
    assert paths.native_dir() == exe.parent / "_internal" / "native"
    # the server and the elevated hosts step run through the console variant
    assert paths.self_command("hostsfix", "add") == [str(exe.parent.resolve() / "powder-cli.exe"), "hostsfix", "add"]


def test_dispatcher_routes(monkeypatch, capsys):
    calls = []
    import powder.launcher.__main__ as launcher_main
    import powder.launcher.hostsfix as hostsfix
    import powder.server.__main__ as server_main
    monkeypatch.setattr(server_main, "main", lambda argv: calls.append(("server", argv)) or 0)
    monkeypatch.setattr(hostsfix, "main", lambda argv: calls.append(("hostsfix", argv)) or 0)
    monkeypatch.setattr(launcher_main, "main", lambda argv: calls.append(("launcher", argv)) or 0)
    dispatcher.main(["server", "admin", "players"])
    dispatcher.main(["hostsfix", "remove", "--backup-dir", "x"])
    dispatcher.main([])
    dispatcher.main(["--restore", "--window"])
    assert calls == [("server", ["admin", "players"]), ("hostsfix", ["remove", "--backup-dir", "x"]),
                     ("launcher", []), ("launcher", ["--restore", "--window"])]
    assert dispatcher.main(["--version"]) == 0 and "Powder" in capsys.readouterr().out


def test_versions():
    assert version.major("v1.4.2") == 1 and version.major("0.1.0+abc") == 0 and version.major("x") is None
    assert version.compatible("0.1.0", "0.9.3") and not version.compatible("1.0.0", "0.9.3")
    assert version.compatible("0.0.0+dev", "3.0.0")


def test_config_defaults_live_next_to_the_missing_file(tmp_path):
    cfg = Config.load(tmp_path / "Powder" / "config.toml")
    assert cfg.data_dir == tmp_path / "Powder" / "data"
    assert cfg.gamedata_dir == tmp_path / "Powder" / "gamedata"


# ---- game-data pack on a new PC ------------------------------------------------------------------
def test_pack_zip_and_install_round_trip(pack_dir, tmp_path):
    from powder.server.gamedata import pack
    z = tmp_path / "steep-gamedata.zip"
    pack.zip_pack(pack_dir, z)
    target = tmp_path / "Powder" / "gamedata"
    manifest = pack.install(z, target)
    assert pack.check(target) == manifest and not (tmp_path / "Powder" / "gamedata.importing").exists()
    pack.install(pack_dir, target)                       # a folder works too, replacing the old pack
    assert pack.check(target)["files"] == manifest["files"]


def test_bad_packs_are_rejected_and_keep_the_old_one(pack_dir, tmp_path):
    import warnings
    import zipfile

    import pytest
    from powder.server.gamedata import pack
    target = tmp_path / "gamedata"
    pack.install(pack_dir, target)
    not_zip = tmp_path / "notes.zip"
    not_zip.write_text("hello")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../outside.txt", "x")
    changed = tmp_path / "changed.zip"
    pack.zip_pack(pack_dir, changed)
    with zipfile.ZipFile(changed, "a") as z, warnings.catch_warnings():
        warnings.simplefilter("ignore")                   # the duplicate entry is the point
        z.writestr("gamedata/offers.json", "{}")          # tampered: hash no longer matches
    for bad in (not_zip, evil, changed, tmp_path / "missing.zip"):
        with pytest.raises(pack.PackError):
            pack.install(bad, target)
    assert pack.check(target) and not (tmp_path / "outside.txt").exists()


def test_launcher_pack_is_chosen_once_and_switched_on_and_off(pack_dir, tmp_path):
    """Play never asks for a pack: it is installed apart from Play ("Choose pack…") and then switched on
    and off with a checkbox, remembered across launcher starts."""
    from powder.launcher.config import LauncherConfig
    from powder.launcher.session import Session, play_steps
    from powder.server.gamedata import pack
    z = tmp_path / "pack.zip"
    pack.zip_pack(pack_dir, z)
    (tmp_path / "launcher.toml").write_text("")
    cfg, data = LauncherConfig.load(tmp_path / "launcher.toml"), tmp_path / "data" / "launcher"

    s = Session(cfg, data)
    assert "pack" not in [st.key for st in play_steps()]
    assert s.pack_info().installed is False and not s.wanted_pack()
    assert s.install_pack(tmp_path / "missing.zip")                       # an error text
    assert s.install_pack(z) is None and pack.check(tmp_path / "gamedata")
    info = s.pack_info()
    assert info.installed and info.enabled and info.built and s.wanted_pack()
    s.set_pack_enabled(False)
    again = Session(cfg, data)                                            # remembered
    assert again.pack_info().installed and not again.pack_info().enabled and not again.wanted_pack()


def test_launcher_pack_is_the_servers_business_in_remote_mode(tmp_path):
    from powder.launcher.config import LauncherConfig
    from powder.launcher.session import Session
    (tmp_path / "launcher.toml").write_text('server_mode = "remote"\nserver_address = "10.0.0.2"\n')
    assert Session(LauncherConfig.load(tmp_path / "launcher.toml"), tmp_path / "data").pack_info() is None


def test_server_can_ignore_an_installed_pack(config, tmp_path):
    import dataclasses

    from powder.server.services import Services
    off = dataclasses.replace(config, server=dataclasses.replace(config.server, use_gamedata=False))
    services = Services.open(off)
    try:
        assert services.assets.empty
    finally:
        services.db.close()


# ---- ports ---------------------------------------------------------------------------------------
NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:80             0.0.0.0:0              LISTENING       4
  TCP    127.0.0.1:4430         0.0.0.0:0              LISTENING       77
  TCP    127.0.0.1:443          127.0.0.1:50000        ESTABLISHED     88
  TCP    [::]:443               [::]:0                 LISTENING       1234
  UDP    0.0.0.0:6005           *:*                                    999
"""


def test_port_owner_from_netstat():
    from powder.launcher.ports import Port, owner_pid
    assert owner_pid(NETSTAT, Port(80, "TCP", "x")) == 4
    assert owner_pid(NETSTAT, Port(443, "TCP", "x")) == 1234       # the listening one, not 4430/established
    assert owner_pid(NETSTAT, Port(6005, "UDP", "x")) == 999
    assert owner_pid(NETSTAT, Port(6006, "UDP", "x")) is None


def test_busy_port_is_detected(monkeypatch):
    import socket
    from powder.launcher import ports
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        taken = s.getsockname()[1]
        monkeypatch.setattr(ports, "process_name", lambda pid: "test.exe")
        lines = ports.busy("127.0.0.1", [ports.Port(taken, "TCP", "HTTPS")])
    assert len(lines) == 1 and f"port {taken} (TCP, HTTPS)" in lines[0]


# ---- release workflow: next tag ------------------------------------------------------------------
def test_next_release_tag():
    import importlib.util
    spec = importlib.util.spec_from_file_location("next_version", paths.REPO / "packaging" / "next_version.py")
    nv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nv)
    assert nv.next_tag([], "major") == "v0.1.0"                          # first release
    tags = ["v0.1.0", "v0.1.1", "v0.10.0", "v0.9.9", "v1.0.0-rc1", "notes", "v0.2.0"]
    assert nv.next_tag(tags, "patch") == "v0.10.1"                       # numeric, not text order
    assert nv.next_tag(tags, "minor") == "v0.11.0"
    assert nv.next_tag(tags, "major") == "v1.0.0"
    import pytest
    with pytest.raises(ValueError):
        nv.next_tag(tags, "huge")
