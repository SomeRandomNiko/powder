"""Initial player state from the recorded data (first run, or `admin reseed`).

For the account the recordings belong to (the profileId of the recorded login) this copies:
  * identity      from the recorded login reply (nameOnPlatform, userId)
  * inventory     from the recorded GET /v1/profiles/{id}/inventory snapshot, then every recorded
                  purchase/reward reply after it (recordings/inventory-updates.json), so the player
                  starts at the state from the END of the capture
  * statistics    from the recorded NEX GetStatisticGroup_V1 reply (level, XP, medals)
Any other player starts empty (plus the starting_credits toggle).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import Config
from ..constants import CREDITS_ITEM
from ..db.repos import Repos
from ..fixtures.http import HttpFixtures
from ..fixtures.rmc import RmcFixtures
from .stats import PROFILE_GROUP, StatsService

log = logging.getLogger("steep.seed")


def captured_session(http: HttpFixtures) -> dict | None:
    for e in http.find("POST", r"/v3/profiles/sessions$"):
        try:
            return e.json()
        except ValueError:
            pass
    return None


def seed_player(profile_id: str, *, config: Config, repos: Repos, http: HttpFixtures, rmc: RmcFixtures,
                updates_file: Path | None = None, recorded_profile_id: str | None = None) -> None:
    """Create the player row and initial state. Assumes the player does not exist yet."""
    profile_id = profile_id.lower()
    captured = profile_id == recorded_profile_id
    session = captured_session(http) if captured else None
    name = (session or {}).get("nameOnPlatform") or f"Player-{profile_id[:4]}"
    user_id = (session or {}).get("userId") or profile_id

    with repos.db.transaction():
        repos.players.create(profile_id, user_id, name)
        if captured:
            n_items = _seed_inventory(profile_id, repos, http, updates_file)
            body = rmc.latest("PlayerStats2Protocol::GetStatisticGroup_V1")
            n_stats = StatsService(repos).import_group(profile_id, PROFILE_GROUP, body) if body else 0
            log.info("seeded %s (%s) from recordings: %d items, %d stats, %d credits", name, profile_id,
                     n_items, n_stats, repos.inventory.quantity(profile_id, CREDITS_ITEM))
        else:
            start = config.toggles(profile_id).starting_credits
            if start:
                repos.inventory.set(profile_id, CREDITS_ITEM, start)
                repos.ledger.add(profile_id, CREDITS_ITEM, start, start, "seed")
            log.info("created new player %s (%s) with %d credits", name, profile_id, start)


def _seed_inventory(profile_id: str, repos: Repos, http: HttpFixtures, updates_file: Path | None) -> int:
    snapshot = [e for e in http.find("GET", rf"/v1/profiles/{profile_id}/inventory$")]
    # prefer the space-scoped snapshot the game itself requests
    snapshot.sort(key=lambda e: "spaceId=" not in e.path)
    items = snapshot[0].json()["items"] if snapshot else []
    for it in items:
        repos.inventory.set(profile_id, it["itemId"], it["quantity"], last_modified=it.get("lastModified"),
                            expiration_date=it.get("expirationDate"))
    applied = 0
    if updates_file and updates_file.exists():
        for u in json.loads(updates_file.read_text(encoding="utf-8")):
            if u.get("profileId", "").lower() != profile_id:
                continue
            for it in u["reply"].get("items", []):
                repos.inventory.set(profile_id, it["itemId"], it["quantity"], last_modified=it.get("lastModified"))
            applied += 1
    credits = repos.inventory.quantity(profile_id, CREDITS_ITEM)
    repos.ledger.add(profile_id, CREDITS_ITEM, credits, credits, "seed")
    if applied:
        log.info("applied %d recorded purchase/reward replies on top of the inventory snapshot", applied)
    return len(repos.inventory.all(profile_id))
