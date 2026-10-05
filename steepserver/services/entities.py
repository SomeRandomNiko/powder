"""UbiServices profile entities: small JSON documents a player owns, e.g. the rider card (type PROFILE,
obj = RIDER_CARD / PLAYER_STATUS / GLOBAL_VERSION) that others see, favorite challenges and replays.
They live in the database; players this server does not know have none.
"""
from __future__ import annotations

import logging
import uuid

from ..constants import SPACE_ID
from ..db.repos import Repos
from ..util import iso_now

log = logging.getLogger("steep.entities")
EDITABLE_FIELDS = ("type", "name", "tags", "obj", "spaceId", "editable", "extendedStorage")


class EntityService:
    def __init__(self, repos: Repos):
        self.repos = repos

    # ---- reads ---------------------------------------------------------------------------------
    def is_local(self, profile_id: str) -> bool:
        return self.repos.players.get(profile_id) is not None

    def for_profile(self, profile_id: str, type_: str | None = None, tags: str | None = None,
                    offset: int = 0, limit: int = 100) -> list[dict]:
        pid = profile_id.lower()
        items = self.repos.entities.for_profile(pid, type_) if self.is_local(pid) else []
        want = set(filter(None, (tags or "").split(",")))
        if want:
            items = [e for e in items if want <= set(e.get("tags") or ())]
        return items[offset:offset + limit]

    def for_profiles(self, profile_ids: list[str], type_: str | None = None,
                     offset: int = 0, limit: int = 100) -> list[dict]:
        out = []
        for pid in profile_ids:
            out.extend(self.for_profile(pid, type_, limit=10_000))
        return out[offset:offset + limit]

    def get(self, entity_id: str) -> dict | None:
        return self.repos.entities.get(entity_id)

    # ---- writes --------------------------------------------------------------------------------
    def put(self, entity_id: str, body: dict, profile_id: str | None) -> dict:
        """Create or replace (the game PUTs its whole rider card); revision is bumped."""
        existing = self.repos.entities.get(entity_id) or {}
        entity = dict(existing)
        entity.update({k: body[k] for k in EDITABLE_FIELDS if k in body})
        entity["entityId"] = entity_id.lower()
        entity["profileId"] = (existing.get("profileId") or body.get("profileId") or profile_id or "").lower()
        entity.setdefault("spaceId", SPACE_ID)
        entity.setdefault("type", "UNKNOWN")
        entity.setdefault("editable", True)
        entity["revision"] = int(existing.get("revision") or 0) + 1
        entity["lastModified"] = iso_now()
        if not entity["profileId"]:
            raise ValueError("entity without an owner")
        self.repos.entities.put(entity)
        return self.repos.entities.get(entity_id)

    def create(self, body: dict, profile_id: str) -> dict:
        log.info("new %s entity for %s", body.get("type"), profile_id)
        return self.put(str(uuid.uuid4()), body, profile_id)

    def delete(self, entity_id: str) -> bool:
        return self.repos.entities.delete(entity_id)
