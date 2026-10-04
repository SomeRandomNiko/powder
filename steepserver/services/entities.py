"""UbiServices profile entities: small JSON documents a player owns, e.g. the rider card (type PROFILE,
obj = RIDER_CARD / PLAYER_STATUS / GLOBAL_VERSION) that others see, favorite challenges and replays.

Local players' entities live in the database; anyone else's come from an index built from every
recorded entity reply (so any combination of friends can be answered, not just recorded queries).
"""
from __future__ import annotations

import json
import logging
import uuid

from ..constants import SPACE_ID
from ..db.repos import Repos
from ..fixtures.http import HttpFixtures
from ..util import iso_now

log = logging.getLogger("steep.entities")
EDITABLE_FIELDS = ("type", "name", "tags", "obj", "spaceId", "editable", "extendedStorage")


def _entities_in(body: bytes) -> list[dict]:
    try:
        d = json.loads(body)
    except ValueError:
        return []
    if isinstance(d, dict) and isinstance(d.get("entities"), list):
        return d["entities"]
    return [d] if isinstance(d, dict) and "entityId" in d else []


class EntityService:
    def __init__(self, repos: Repos, http: HttpFixtures):
        self.repos = repos
        self.recorded_by_profile: dict[str, dict[str, dict]] = {}
        self.recorded_by_id: dict[str, dict] = {}
        for e in http.entries:
            if e.method in ("GET", "PUT") and "/entities" in e.path and e.path.startswith("/v2/profiles"):
                for ent in _entities_in(e.body):
                    pid = (ent.get("profileId") or "").lower()
                    self.recorded_by_id[ent["entityId"].lower()] = ent
                    if pid:
                        self.recorded_by_profile.setdefault(pid, {})[ent["entityId"].lower()] = ent

    # ---- reads ---------------------------------------------------------------------------------
    def is_local(self, profile_id: str) -> bool:
        return self.repos.players.get(profile_id) is not None

    def for_profile(self, profile_id: str, type_: str | None = None, tags: str | None = None,
                    offset: int = 0, limit: int = 100) -> list[dict]:
        pid = profile_id.lower()
        if self.is_local(pid):
            self.ensure_seeded(pid)
            items = self.repos.entities.for_profile(pid, type_)
        else:
            items = [e for e in self.recorded_by_profile.get(pid, {}).values()
                     if not type_ or e.get("type") == type_]
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
        return self.repos.entities.get(entity_id) or self.recorded_by_id.get(entity_id.lower())

    # ---- writes --------------------------------------------------------------------------------
    def put(self, entity_id: str, body: dict, profile_id: str | None) -> dict:
        """Create or replace (the game PUTs its whole rider card); revision is bumped."""
        existing = self.repos.entities.get(entity_id) or self.recorded_by_id.get(entity_id.lower()) or {}
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

    # ---- seeding -------------------------------------------------------------------------------
    def ensure_seeded(self, profile_id: str) -> None:
        """Copy the recorded entities of a local player into the database once (also covers
        databases created before entities were stored)."""
        pid = profile_id.lower()
        if self.repos.kv.get(pid, "entities_seeded"):
            return
        with self.repos.db.transaction():
            for ent in self.recorded_by_profile.get(pid, {}).values():
                if self.repos.entities.get(ent["entityId"]) is None:
                    self.repos.entities.put(ent)
            self.repos.kv.set(pid, "entities_seeded", True)
