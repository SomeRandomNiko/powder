"""Game logic. Transports, the admin CLI and plugins all go through a Services instance."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.events import EventBus
from ..core.sessions import SessionRegistry
from ..db.database import Database
from ..db.repos import Repos
from ..fixtures.catalog import OfferCatalog
from ..fixtures.demux import DemuxFixtures
from ..fixtures.http import HttpFixtures
from ..fixtures.rmc import RmcFixtures
from .accounts import AccountService
from .economy import EconomyService
from .entities import EntityService
from .matchmaking import MatchmakingService
from .seed import captured_session, seed_player
from .stats import StatsService


@dataclass
class Assets:
    """Read-only replies recorded from real sessions (the recordings directory; personal data,
    kept out of version control). Every part is optional: without recordings the server still runs,
    it just has nothing to replay."""
    http: HttpFixtures
    rmc: RmcFixtures
    catalog: OfferCatalog
    inventory_updates: Path | None = None
    recorded_profile_id: str | None = None       # whose account the recordings are from
    connect_http: HttpFixtures = field(default_factory=lambda: HttpFixtures([]))   # Ubisoft Connect's
    demux: DemuxFixtures = field(default_factory=DemuxFixtures)                     # own traffic

    @classmethod
    def load(cls, recordings_dir: Path) -> "Assets":
        http = HttpFixtures.load(recordings_dir / "fixtures.json")
        session = captured_session(http)
        return cls(http=http, rmc=RmcFixtures.load(recordings_dir / "fixtures-rmc.json"),
                   catalog=OfferCatalog.from_fixtures(http),
                   inventory_updates=recordings_dir / "inventory-updates.json",
                   recorded_profile_id=(session or {}).get("profileId", "").lower() or None,
                   connect_http=HttpFixtures.load(recordings_dir / "connect-fixtures.json"),
                   demux=DemuxFixtures.load(recordings_dir / "demux.json"))

    @property
    def empty(self) -> bool:
        return not self.http.entries and not self.rmc.data


@dataclass
class Services:
    config: Config
    db: Database
    assets: Assets
    repos: Repos = field(init=False)
    events: EventBus = field(default_factory=EventBus)
    sessions: SessionRegistry = field(default_factory=SessionRegistry)

    def __post_init__(self):
        self.repos = Repos(self.db)
        self.accounts = AccountService(self.config, self.repos, self.sessions, self.seed,
                                       self.default_player_id)
        self.economy = EconomyService(self.config, self.repos, self.assets.catalog, self.events)
        self.stats = StatsService(self.repos)
        self.entities = EntityService(self.repos, self.assets.http)
        self.matchmaking = MatchmakingService()

    def default_player_id(self) -> str:
        """Configured default, else the recorded account, else a local id created once and kept."""
        if self.config.players.default:
            return self.config.players.default.lower()
        if self.assets.recorded_profile_id:
            return self.assets.recorded_profile_id
        pid = self.repos.kv.get("server", "default_player")
        if pid is None:
            pid = str(uuid.uuid4())
            self.repos.kv.set("server", "default_player", pid)
        return pid

    def seed(self, profile_id: str) -> None:
        seed_player(profile_id, config=self.config, repos=self.repos, http=self.assets.http,
                    rmc=self.assets.rmc, updates_file=self.assets.inventory_updates,
                    recorded_profile_id=self.assets.recorded_profile_id)

    def reseed(self, profile_id: str) -> None:
        """Wipe a player and recreate them from the recorded data."""
        with self.db.transaction():
            self.repos.players.delete(profile_id)
            self.repos.entities.clear(profile_id)
            self.repos.kv.set(profile_id.lower(), "entities_seeded", False)
            self.seed(profile_id)

    @classmethod
    def open(cls, config: Config, assets: Assets | None = None) -> "Services":
        db = Database(config.data_dir / "steep.db")
        db.migrate()
        return cls(config=config, db=db, assets=assets or Assets.load(config.server.recordings_dir))
