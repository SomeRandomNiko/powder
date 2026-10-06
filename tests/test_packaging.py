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
