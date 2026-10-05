"""Game logic. Transports, the admin CLI and plugins all go through a Services instance."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.events import EventBus
from ..core.sessions import SessionRegistry
from ..db.database import Database
from ..db.repos import Repos
from ..fixtures.catalog import OfferCatalog
from ..fixtures.http import HttpFixtures
from ..fixtures.rmc import RmcFixtures
from ..gamedata import pack
from .accounts import AccountService
from .economy import EconomyService
from .entities import EntityService
from .matchmaking import MatchmakingService
from .seed import create_player
from .stats import StatsService

log = logging.getLogger("steep")


@dataclass
class Assets:
    """The game-data pack (gamedata_dir): non-personal server data the game needs, read-only. Every
    part is optional: without a pack the server runs, but the store is empty and configs are missing."""
    http: HttpFixtures = field(default_factory=lambda: HttpFixtures([]))
    rmc: RmcFixtures = field(default_factory=lambda: RmcFixtures({}))
    catalog: OfferCatalog = field(default_factory=lambda: OfferCatalog([]))
    actions: list[dict] = field(default_factory=list)                    # Ubisoft Club action definitions
    connect_http: HttpFixtures = field(default_factory=lambda: HttpFixtures([]))
    products: pack.ConnectProducts = field(default_factory=pack.ConnectProducts)
    manifest: dict | None = None

    @classmethod
    def load(cls, root: Path) -> "Assets":
        try:
            manifest = pack.read_manifest(root)
        except pack.PackError as e:
            log.warning("%s", e)
            return cls()
        changed = pack.verify_files(root, manifest)
        if changed:
            log.warning("game-data pack files changed since it was built: %s", ", ".join(changed))
        actions_path = root / "actions.json"
        return cls(http=HttpFixtures.load(root / "http.json"), rmc=RmcFixtures.load(root / "rmc.json"),
                   catalog=OfferCatalog.load(root / "offers.json"),
                   actions=json.loads(actions_path.read_text(encoding="utf-8")).get("actions", [])
                   if actions_path.exists() else [],
                   connect_http=HttpFixtures.load(root / "connect" / "http.json"),
                   products=pack.ConnectProducts.load(root / "connect" / "demux.json"),
                   manifest=manifest)

    @property
    def empty(self) -> bool:
        return self.manifest is None


@dataclass
class Services:
    config: Config
    db: Database
    assets: Assets
    repos: Repos = field(init=False)
    events: EventBus = field(default_factory=EventBus)
    sessions: SessionRegistry = field(default_factory=SessionRegistry)
    connect_clients: set = field(default_factory=set)     # live Ubisoft Connect demux connections

    def __post_init__(self):
        self.repos = Repos(self.db)
        self.accounts = AccountService(self.config, self.repos, self.sessions, self.seed,
                                       self.default_player_id)
        self.economy = EconomyService(self.config, self.repos, self.assets.catalog, self.events)
        self.stats = StatsService(self.repos)
        self.entities = EntityService(self.repos)
        self.matchmaking = MatchmakingService()

    def default_player_id(self) -> str:
        """Configured default, else the one stored (imported or created once) and kept. A database from
        before this was stored keeps its existing player (the first one created)."""
        if self.config.players.default:
            return self.config.players.default.lower()
        pid = self.repos.kv.get("server", "default_player")
        if pid is None:
            existing = sorted(self.repos.players.all(), key=lambda p: p.created_at)
            pid = existing[0].profile_id if existing else str(uuid.uuid4())
            self.repos.kv.set("server", "default_player", pid)
        return pid

    def seed(self, profile_id: str) -> None:
        create_player(profile_id, config=self.config, repos=self.repos)

    def reset(self, profile_id: str) -> None:
        """Wipe a player; they start again as a new player on their next login."""
        with self.db.transaction():
            self.repos.players.delete(profile_id)
            self.repos.entities.clear(profile_id)

    @classmethod
    def open(cls, config: Config, assets: Assets | None = None) -> "Services":
        db = Database(config.data_dir / "steep.db")
        db.migrate()
        return cls(config=config, db=db, assets=assets or Assets.load(config.gamedata_dir))
