import dataclasses
from pathlib import Path

import pytest

import synthetic
from steepserver.config import Config, ServerConfig
from steepserver.services import Assets, Services


@pytest.fixture(scope="session")
def recordings_dir(tmp_path_factory) -> Path:
    """The made-up recordings set (tests never depend on anyone's real recordings)."""
    return synthetic.build(tmp_path_factory.mktemp("recordings"))


@pytest.fixture(scope="session")
def assets(recordings_dir) -> Assets:
    return Assets.load(recordings_dir)


@pytest.fixture
def config(tmp_path: Path, recordings_dir: Path) -> Config:
    return Config(server=ServerConfig(data_dir=tmp_path / "data", recordings_dir=recordings_dir,
                                      https_port=0, nex_port=0))


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
def player(services) -> str:
    services.accounts.player(synthetic.PLAYER)          # seeds on first use
    return synthetic.PLAYER
