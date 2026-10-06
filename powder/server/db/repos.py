"""Thin repositories over the shared Database. All game logic lives in services/, not here."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .database import Database, utc_now


@dataclass(frozen=True)
class PlayerRow:
    profile_id: str
    user_id: str
    name: str
    created_at: str
    settings: dict


@dataclass(frozen=True)
class InventoryRow:
    item_id: str
    quantity: int
    expiration_date: str | None
    last_modified: str


@dataclass(frozen=True)
class StatRow:
    key: str
    type: int
    value: bytes            # raw 8 bytes as on the wire


class PlayerRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, profile_id: str) -> PlayerRow | None:
        r = self.db.execute("SELECT * FROM players WHERE profile_id = ?", (profile_id.lower(),)).fetchone()
        return PlayerRow(r["profile_id"], r["user_id"], r["name"], r["created_at"],
                         json.loads(r["settings_json"])) if r else None

    def all(self) -> list[PlayerRow]:
        return [PlayerRow(r["profile_id"], r["user_id"], r["name"], r["created_at"],
                          json.loads(r["settings_json"]))
                for r in self.db.execute("SELECT * FROM players ORDER BY created_at")]

    def create(self, profile_id: str, user_id: str, name: str) -> PlayerRow:
        self.db.execute("INSERT INTO players(profile_id, user_id, name, created_at) VALUES (?,?,?,?)",
                        (profile_id.lower(), user_id.lower(), name, utc_now()))
        return self.get(profile_id)

    def delete(self, profile_id: str) -> None:
        self.db.execute("DELETE FROM players WHERE profile_id = ?", (profile_id.lower(),))


class InventoryRepo:
    def __init__(self, db: Database):
        self.db = db

    def all(self, profile_id: str) -> list[InventoryRow]:
        return [InventoryRow(r["item_id"], r["quantity"], r["expiration_date"], r["last_modified"])
                for r in self.db.execute("SELECT * FROM inventory WHERE profile_id = ? ORDER BY item_id DESC",
                                         (profile_id.lower(),))]

    def get(self, profile_id: str, item_id: str) -> InventoryRow | None:
        r = self.db.execute("SELECT * FROM inventory WHERE profile_id = ? AND item_id = ?",
                            (profile_id.lower(), item_id.lower())).fetchone()
        return InventoryRow(r["item_id"], r["quantity"], r["expiration_date"], r["last_modified"]) if r else None

    def quantity(self, profile_id: str, item_id: str) -> int:
        row = self.get(profile_id, item_id)
        return row.quantity if row else 0

    def set(self, profile_id: str, item_id: str, quantity: int, last_modified: str | None = None,
            expiration_date: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO inventory(profile_id, item_id, quantity, expiration_date, last_modified) "
            "VALUES (?,?,?,?,?) ON CONFLICT(profile_id, item_id) DO UPDATE SET "
            "quantity = excluded.quantity, expiration_date = excluded.expiration_date, "
            "last_modified = excluded.last_modified",
            (profile_id.lower(), item_id.lower(), quantity, expiration_date, last_modified or utc_now()))

    def clear(self, profile_id: str) -> None:
        self.db.execute("DELETE FROM inventory WHERE profile_id = ?", (profile_id.lower(),))


class StatsRepo:
    def __init__(self, db: Database):
        self.db = db

    def group(self, profile_id: str, group_name: str) -> list[StatRow]:
        return [StatRow(r["key"], r["type"], bytes(r["value"]))
                for r in self.db.execute("SELECT key, type, value FROM stats WHERE profile_id = ? AND "
                                         "group_name = ? ORDER BY rowid", (profile_id.lower(), group_name))]

    def set(self, profile_id: str, group_name: str, key: str, type_: int, value: bytes) -> None:
        if len(value) != 8:
            raise ValueError(f"stat value must be 8 bytes, got {len(value)}")
        self.db.execute(
            "INSERT INTO stats(profile_id, group_name, key, type, value) VALUES (?,?,?,?,?) "
            "ON CONFLICT(profile_id, group_name, key) DO UPDATE SET type = excluded.type, value = excluded.value",
            (profile_id.lower(), group_name, key, type_, value))

    def get(self, profile_id: str, group_name: str, key: str) -> StatRow | None:
        r = self.db.execute("SELECT key, type, value FROM stats WHERE profile_id = ? AND group_name = ? "
                            "AND key = ?", (profile_id.lower(), group_name, key)).fetchone()
        return StatRow(r["key"], r["type"], bytes(r["value"])) if r else None

    def clear(self, profile_id: str) -> None:
        self.db.execute("DELETE FROM stats WHERE profile_id = ?", (profile_id.lower(),))


class StatEventsRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, profile_id: str, name: str, fields: dict) -> None:
        self.db.execute("INSERT INTO stat_events(profile_id, ts, name, fields_json) VALUES (?,?,?,?)",
                        (profile_id.lower(), utc_now(), name, json.dumps(fields)))

    def recent(self, profile_id: str, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM stat_events WHERE profile_id = ? ORDER BY id DESC LIMIT ?", (profile_id.lower(), limit))]


class ChallengeScoresRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, profile_id: str, challenge_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM challenge_scores WHERE profile_id = ? AND challenge_id = ?",
                            (profile_id.lower(), challenge_id)).fetchone()
        return dict(r) if r else None

    def set(self, profile_id: str, challenge_id: str, direction: str, score: float, replay: str | None) -> None:
        self.db.execute(
            "INSERT INTO challenge_scores(profile_id, challenge_id, direction, best_score, replay, updated) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(profile_id, challenge_id) DO UPDATE SET direction = "
            "excluded.direction, best_score = excluded.best_score, replay = excluded.replay, updated = excluded.updated",
            (profile_id.lower(), challenge_id, direction, score, replay, utc_now()))

    def all(self, profile_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM challenge_scores WHERE profile_id = ? ORDER BY challenge_id", (profile_id.lower(),))]


class EntitiesRepo:
    """Rows <-> UbiServices entity dicts (entityId, profileId, spaceId, type, editable, name, tags,
    obj, lastModified, revision[, extendedStorage])."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _entity(r) -> dict:
        e = {"entityId": r["entity_id"], "profileId": r["profile_id"], "spaceId": r["space_id"],
             "type": r["type"], "editable": bool(r["editable"]), "name": r["name"],
             "tags": json.loads(r["tags_json"]), "obj": json.loads(r["obj_json"]),
             "lastModified": r["last_modified"], "revision": r["revision"]}
        if r["extended_json"] is not None:
            e["extendedStorage"] = json.loads(r["extended_json"])
        return e

    def get(self, entity_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM profile_entities WHERE entity_id = ?", (entity_id.lower(),)).fetchone()
        return self._entity(r) if r else None

    def for_profile(self, profile_id: str, type_: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM profile_entities WHERE profile_id = ?", [profile_id.lower()]
        if type_:
            sql += " AND type = ?"
            args.append(type_)
        return [self._entity(r) for r in self.db.execute(sql + " ORDER BY last_modified", args)]

    def put(self, e: dict) -> None:
        self.db.execute(
            "INSERT INTO profile_entities(entity_id, profile_id, space_id, type, name, editable, tags_json, "
            "obj_json, extended_json, revision, last_modified) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(entity_id) DO UPDATE SET profile_id = excluded.profile_id, space_id = excluded.space_id, "
            "type = excluded.type, name = excluded.name, editable = excluded.editable, tags_json = excluded.tags_json, "
            "obj_json = excluded.obj_json, extended_json = excluded.extended_json, revision = excluded.revision, "
            "last_modified = excluded.last_modified",
            (e["entityId"].lower(), e["profileId"].lower(), e["spaceId"], e["type"], e.get("name") or "",
             int(bool(e.get("editable", True))), json.dumps(e.get("tags") or []), json.dumps(e.get("obj") or {}),
             json.dumps(e["extendedStorage"]) if "extendedStorage" in e else None,
             int(e.get("revision") or 1), e.get("lastModified") or utc_now()))

    def delete(self, entity_id: str) -> bool:
        return self.db.execute("DELETE FROM profile_entities WHERE entity_id = ?",
                               (entity_id.lower(),)).rowcount > 0

    def clear(self, profile_id: str) -> None:
        self.db.execute("DELETE FROM profile_entities WHERE profile_id = ?", (profile_id.lower(),))


class LedgerRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, profile_id: str, item_id: str, delta: int, balance: int, reason: str,
            offer_id: str | None = None) -> None:
        self.db.execute("INSERT INTO ledger(profile_id, ts, offer_id, item_id, delta, balance, reason) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (profile_id.lower(), utc_now(), offer_id, item_id.lower(), delta, balance, reason))

    def recent(self, profile_id: str, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM ledger WHERE profile_id = ? ORDER BY id DESC LIMIT ?", (profile_id.lower(), limit))]


class KvRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, scope: str, key: str, default=None):
        r = self.db.execute("SELECT value_json FROM kv WHERE scope = ? AND key = ?", (scope, key)).fetchone()
        return json.loads(r["value_json"]) if r else default

    def set(self, scope: str, key: str, value) -> None:
        self.db.execute("INSERT INTO kv(scope, key, value_json) VALUES (?,?,?) ON CONFLICT(scope, key) "
                        "DO UPDATE SET value_json = excluded.value_json", (scope, key, json.dumps(value)))


class Repos:
    """All repositories over one database."""

    def __init__(self, db: Database):
        self.db = db
        self.players = PlayerRepo(db)
        self.inventory = InventoryRepo(db)
        self.stats = StatsRepo(db)
        self.ledger = LedgerRepo(db)
        self.kv = KvRepo(db)
        self.stat_events = StatEventsRepo(db)
        self.challenge_scores = ChallengeScoresRepo(db)
        self.entities = EntitiesRepo(db)
