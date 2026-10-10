"""Server configuration from a TOML file (see config.example.toml). Every key is optional."""
from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    bind: str = "127.0.0.1"             # this PC only (no firewall prompt); "0.0.0.0" for friends on a LAN/VPN
    public_host: str = "127.0.0.1"      # address the game uses to reach us (NEX StationURLs)
    https_port: int = 443
    nex_port: int = 6005
    storm_port: int = 11000             # Storm (UDP): NAT detection on this port to +3, traversal on +5
    crl_port: int = 80                  # plain HTTP: the local CA's revocation list Ubisoft Connect checks
    connect_cloud_saves: bool = False   # False: tell Ubisoft Connect the games have no cloud saves, so it
                                        # never tries (and fails) to sync them at launch and exit
    data_dir: Path = Path("data")       # powder.db, logs/, TLS cert, NEX key  (relative to the config file)
    gamedata_dir: Path = Path("gamedata")       # game-data pack (store catalogue, configs), see gamedata/
    use_gamedata: bool = True           # False: run as without a pack even if one is installed
    recordings_dir: Path = Path("recordings")   # your own captures: only read by 'gamedata build' and
                                                # 'admin import', never while the server runs
    country: str = "US"                 # what Ubisoft Connect is told about the player's location
    region: str = "NA"
    log_level: str = "INFO"
    strict_tickets: bool = False        # True: reject unknown UbiServices/NEX tickets


@dataclass(frozen=True)
class Toggles:
    """Gameplay settings; read per call via Config.toggles(profile_id), never cached."""
    unlimited_heli_tickets: bool = False
    starting_credits: int = 0           # credits granted to a newly created player
    unlock_all: bool = False
    xp_multiplier: float = 1.0
    free_purchases: bool = False        # shop offers grant items without deducting credits
    reward_credit_multiplier: float = 1.0


@dataclass(frozen=True)
class PlayersConfig:
    default: str | None = None          # player used when a login cannot be told apart; unset = the
                                        # recorded account, else a locally generated one
    bind: dict[str, str] = field(default_factory=dict)   # client IP -> profileId


@dataclass(frozen=True)
class MultiplayerConfig:
    storm: str = "powder"               # "powder": this server is the games' peer-to-peer helper (Storm: NAT
                                        # detection and traversal, transport/storm.py). "off": none (not even
                                        # a pack's Ubisoft hosts); the game then switches multiplayer off


@dataclass(frozen=True)
class PluginsConfig:
    enabled: tuple[str, ...] = ("*",)   # plugin file stems, "*" = all
    disabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    server: ServerConfig = ServerConfig()
    players: PlayersConfig = PlayersConfig()
    plugins: PluginsConfig = PluginsConfig()
    multiplayer: MultiplayerConfig = MultiplayerConfig()
    gameplay: Toggles = Toggles()
    gameplay_per_player: dict[str, dict] = field(default_factory=dict)
    path: Path | None = None            # the file this was loaded from

    def toggles(self, profile_id: str | None = None) -> Toggles:
        override = self.gameplay_per_player.get(profile_id or "", {})
        return dataclasses.replace(self.gameplay, **override) if override else self.gameplay

    @property
    def data_dir(self) -> Path:
        return self.server.data_dir

    @property
    def gamedata_dir(self) -> Path:
        return self.server.gamedata_dir

    @classmethod
    def load(cls, path: Path | None) -> "Config":
        if path is None or not path.exists():                # defaults, next to where the file would be
            base = path.resolve().parent if path is not None else Path.cwd()
            return cls(server=ServerConfig(data_dir=base / "data", gamedata_dir=base / "gamedata",
                                           recordings_dir=base / "recordings"), path=path)
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        base = path.resolve().parent

        srv = _build(ServerConfig, raw.get("server", {}))
        for name in ("data_dir", "gamedata_dir", "recordings_dir"):
            value = getattr(srv, name)
            if not value.is_absolute():
                srv = dataclasses.replace(srv, **{name: (base / value).resolve()})

        players_raw = dict(raw.get("players", {}))
        players = PlayersConfig(default=players_raw.get("default"),
                                bind=dict(players_raw.get("bind", {})))

        plugins_raw = raw.get("plugins", {})
        plugins = PluginsConfig(enabled=tuple(plugins_raw.get("enabled", ("*",))),
                                disabled=tuple(plugins_raw.get("disabled", ())))

        multiplayer = _build(MultiplayerConfig, raw.get("multiplayer", {}))
        if multiplayer.storm == "pack":                     # before Powder had its own: now the same
            multiplayer = dataclasses.replace(multiplayer, storm="powder")
        if multiplayer.storm not in ("off", "powder"):
            raise ValueError(f'[multiplayer] storm must be "powder" or "off", not {multiplayer.storm!r}')

        gameplay_raw = dict(raw.get("gameplay", {}))
        per_player = gameplay_raw.pop("player", {})
        gameplay = _build(Toggles, gameplay_raw)
        for pid, over in per_player.items():
            _check_keys(Toggles, over, f"gameplay.player.{pid}")
        return cls(server=srv, players=players, plugins=plugins, multiplayer=multiplayer, gameplay=gameplay,
                   gameplay_per_player=per_player, path=path)


def _check_keys(dc, values: dict, section: str) -> None:
    known = {f.name for f in dataclasses.fields(dc)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"[{section}] unknown setting(s): {', '.join(sorted(unknown))}")


def _build(dc, values: dict):
    _check_keys(dc, values, dc.__name__)
    kwargs = {}
    for f in dataclasses.fields(dc):
        if f.name in values:
            v = values[f.name]
            kwargs[f.name] = Path(v) if f.type in ("Path", Path) else v
    return dc(**kwargs)
