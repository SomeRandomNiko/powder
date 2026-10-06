"""Bring a player's own recorded progress into the database (once):

    powder-server admin import <recordings dir> [--replace]

From the recordings of their own sessions it takes:
  * identity      the recorded login reply (profile id, user id, name)
  * inventory     the recorded GET /v1/profiles/{id}/inventory snapshot, then every recorded purchase/
                  reward reply after it (inventory-updates.json): the state at the END of the capture
  * statistics    the latest recorded NEX GetStatisticGroup_V1 reply (level, XP, medals)
  * entities      the player's recorded profile entities (rider card, favorites)
and makes that player the default one. The server never reads the recordings while running.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..constants import CREDITS_ITEM
from ..fixtures.http import HttpFixtures
from ..fixtures.rmc import RmcFixtures
from .stats import PROFILE_GROUP

log = logging.getLogger("powder.import")


class ImportError_(RuntimeError):
    pass


@dataclass
class ImportResult:
    profile_id: str
    name: str
    items: int
    credits: int
    stats: int
    entities: int


def _entities_in(body: bytes) -> list[dict]:
    try:
        d = json.loads(body)
    except ValueError:
        return []
    if isinstance(d, dict) and isinstance(d.get("entities"), list):
        return d["entities"]
    return [d] if isinstance(d, dict) and "entityId" in d else []


def import_recordings(services, recordings: Path, *, replace: bool = False) -> ImportResult:
    http = HttpFixtures.load(recordings / "fixtures.json")
    rmc = RmcFixtures.load(recordings / "fixtures-rmc.json")
    login = next((e.json() for e in http.find("POST", r"/v3/profiles/sessions$")), None)
    if not login:
        raise ImportError_(f"no recorded login in {recordings / 'fixtures.json'}")
    pid = login["profileId"].lower()
    repos = services.repos
    if repos.players.get(pid):
        if not replace:
            raise ImportError_(f"player {login['nameOnPlatform']} ({pid}) exists; use --replace to overwrite")
        with services.db.transaction():
            repos.players.delete(pid)
            repos.entities.clear(pid)

    with services.db.transaction():
        repos.players.create(pid, login.get("userId") or pid, login.get("nameOnPlatform") or f"Player-{pid[:4]}")
        # inventory: snapshot (prefer the space-scoped one the game requests), then recorded updates
        snaps = sorted(http.find("GET", rf"/v1/profiles/{pid}/inventory$"), key=lambda e: "spaceId=" not in e.path)
        for it in (snaps[0].json()["items"] if snaps else []):
            repos.inventory.set(pid, it["itemId"], it["quantity"], last_modified=it.get("lastModified"),
                                expiration_date=it.get("expirationDate"))
        updates = recordings / "inventory-updates.json"
        if updates.exists():
            for u in json.loads(updates.read_text(encoding="utf-8")):
                if u.get("profileId", "").lower() == pid:
                    for it in u["reply"].get("items", []):
                        repos.inventory.set(pid, it["itemId"], it["quantity"], last_modified=it.get("lastModified"))
        credits = repos.inventory.quantity(pid, CREDITS_ITEM)
        repos.ledger.add(pid, CREDITS_ITEM, credits, credits, "import")
        # entities
        n_entities = 0
        for e in http.entries:
            if e.method in ("GET", "PUT") and e.path.startswith("/v2/profiles") and "/entities" in e.path:
                for ent in _entities_in(e.body):
                    if (ent.get("profileId") or "").lower() == pid and repos.entities.get(ent["entityId"]) is None:
                        repos.entities.put(ent)
                        n_entities += 1
        repos.kv.set("server", "default_player", pid)
    body = rmc.latest("PlayerStats2Protocol::GetStatisticGroup_V1")
    n_stats = services.stats.import_group(pid, PROFILE_GROUP, body) if body else 0
    result = ImportResult(pid, login.get("nameOnPlatform", ""), len(repos.inventory.all(pid)), credits,
                          n_stats, n_entities)
    log.info("imported %s (%s): %d items, %d credits, %d stats, %d entities", result.name, pid,
             result.items, credits, n_stats, n_entities)
    return result
