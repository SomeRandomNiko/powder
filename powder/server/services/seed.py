"""A new player: what someone gets the first time they log in. Like a new Ubisoft account: no items,
level 1, plus the `starting_credits` toggle. (A player's own recorded progress can be brought in with
`powder-server admin import`, see services/importer.py.)
"""
from __future__ import annotations

import logging

from ..config import Config
from ..constants import CREDITS_ITEM
from ..db.repos import Repos

log = logging.getLogger("powder.seed")


def create_player(profile_id: str, *, config: Config, repos: Repos, name: str | None = None,
                  user_id: str | None = None) -> None:
    """Create the player row and initial state. Assumes the player does not exist yet."""
    profile_id = profile_id.lower()
    name = name or f"Player-{profile_id[:4]}"
    with repos.db.transaction():
        repos.players.create(profile_id, user_id or profile_id, name)
        start = config.toggles(profile_id).starting_credits
        if start:
            repos.inventory.set(profile_id, CREDITS_ITEM, start)
            repos.ledger.add(profile_id, CREDITS_ITEM, start, start, "seed")
    log.info("created new player %s (%s) with %d credits", name, profile_id, start)
