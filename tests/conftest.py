import dataclasses
from pathlib import Path

import pytest

import synthetic
from steepserver.config import Config, ServerConfig
from steepserver.gamedata.build import build
from steepserver.services import Assets, Services
from steepserver.services.importer import import_recordings


@pytest.fixture(scope="session")
def recordings_dir(tmp_path_factory) -> Path:
    """The made-up recordings set (tests never depend on anyone's real recordings)."""
    return synthetic.build(tmp_path_factory.mktemp("recordings"))


@pytest.fixture(scope="session")
def pack_dir(recordings_dir, tmp_path_factory) -> Path:
    """A game-data pack built from the made-up recordings with the real builder."""
    out = tmp_path_factory.mktemp("packs") / "gamedata"
    report = build(recordings_dir, out)
    assert not report.findings, report.findings
    return out


@pytest.fixture(scope="session")
def assets(pack_dir) -> Assets:
    return Assets.load(pack_dir)


@pytest.fixture
def config(tmp_path: Path, recordings_dir: Path, pack_dir: Path) -> Config:
    return Config(server=ServerConfig(data_dir=tmp_path / "data", gamedata_dir=pack_dir,
                                      recordings_dir=recordings_dir, https_port=0, nex_port=0))


@pytest.fixture
def make_services(assets, config):
    opened = []

    def make(cfg: Config | None = None, **gameplay) -> Services:
        cfg = cfg or config
        if gameplay:
            cfg = dataclasses.replace(cfg, gameplay=dataclasses.replace(cfg.gameplay, **gameplay))
        s = Services.open(cfg, assets)
        opened.append(s)
        return s

    yield make
    for s in opened:
        s.db.close()


@pytest.fixture
def services(make_services) -> Services:
    return make_services()


@pytest.fixture
def player(services, recordings_dir) -> str:
    """The made-up recorded player, imported like `admin import` does (50,600 credits, level 41)."""
    import_recordings(services, recordings_dir)
    return synthetic.PLAYER
