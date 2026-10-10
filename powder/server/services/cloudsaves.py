"""Cloud saves: Ubisoft Connect keeps a game's own save files (Steep: savegames\\<profile id>\\3280\\N.save)
on the server, so they follow the account to another PC.

How Connect does it (recorded): at game start it reads the "cloudSave" entity upcCloudSaveUplayId_<product>
(GET /v1/profiles/<pid>/entities?name=...) and compares its slots with the local files; at exit it writes
the entity (PUT /v1/profiles/entities/<id>: obj {slots [{id, filename, hash, size, lastModified}],
timestamp, pc_name}) and then asks the demux cloudsave_service for upload links, one per slot, and uploads
each file there. The files are stored as they come (Connect transforms them; it reads them back itself).

Safety: an entity is only shown while every slot it lists has its file here, so Connect never tries to
download a save that is not complete; an upload replaces a stored file only when it arrived whole (MD5 as
announced) and is written to a temporary file first.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("powder.cloudsaves")

TYPE = "cloudSave"
NAME = re.compile(r"^upcCloudSaveUplayId_(\d{1,10})$")
SLOT = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
LINK_LIFETIME_S = 3600


def entity_name(product: int) -> str:
    return f"upcCloudSaveUplayId_{product}"


@dataclass(frozen=True)
class Link:
    profile_id: str
    product: int
    slot: str
    expires: float
    md5: str | None = None              # base64 MD5 announced for an upload


class CloudSaveError(Exception):
    """An upload or download that can't be done (unknown link, bad slot, MD5 mismatch)."""


class CloudSaveService:
    def __init__(self, repos, entities, data_dir: Path, clock=time.time):
        self.repos, self.entities, self.clock = repos, entities, clock
        self.root = Path(data_dir) / "cloudsaves"
        self.links: dict[str, Link] = {}

    # ---- the entity -------------------------------------------------------------------------------
    def entity(self, profile_id: str, product: int) -> dict | None:
        name = entity_name(product)
        return next((e for e in self.repos.entities.for_profile(profile_id.lower(), TYPE) if e.get("name") == name),
                    None)

    @staticmethod
    def slots(entity: dict) -> list[str]:
        obj = entity.get("obj") if isinstance(entity.get("obj"), dict) else {}
        out = []
        for s in obj.get("slots") or []:
            name = str(s.get("filename", s.get("id", ""))) if isinstance(s, dict) else ""
            if SLOT.match(name):
                out.append(name)
        return out

    def complete(self, profile_id: str, product: int, entity: dict) -> bool:
        slots = self.slots(entity)
        return bool(slots) and all(self.path(profile_id, product, s).exists() for s in slots)

    def offered(self, profile_id: str, product: int) -> dict | None:
        """The cloud save Connect gets at game start: only a complete one."""
        e = self.entity(profile_id, product)
        if e is None:
            return None
        if not self.complete(profile_id, product, e):
            log.warning("cloud save of %s (product %d) is incomplete (files missing): not offered", profile_id, product)
            return None
        return e

    # ---- files ------------------------------------------------------------------------------------
    def path(self, profile_id: str, product: int, slot: str) -> Path:
        if not SLOT.match(slot):
            raise CloudSaveError(f"bad slot name {slot!r}")
        return self.root / profile_id.lower() / str(int(product)) / f"{slot}.savegame"

    def link(self, profile_id: str, product: int, slot: str, md5: str | None = None) -> str:
        """A one-hour token for uploading (md5 given) or downloading one slot of this player's save."""
        self.path(profile_id, product, slot)                 # validates the slot name
        now = self.clock()
        for t in [t for t, l in self.links.items() if l.expires < now]:
            del self.links[t]
        token = secrets.token_urlsafe(24)
        self.links[token] = Link(profile_id.lower(), int(product), slot, now + LINK_LIFETIME_S, md5)
        return token

    def resolve(self, token: str, product: int, slot: str) -> Link:
        link = self.links.get(token or "")
        if link is None or link.expires < self.clock() or link.product != int(product) or link.slot != slot:
            raise CloudSaveError("unknown or expired link")
        return link

    def store(self, link: Link, data: bytes, content_md5: str | None) -> None:
        expected = content_md5 or link.md5
        if expected:
            got = base64.b64encode(hashlib.md5(data).digest()).decode()
            if got != expected:
                raise CloudSaveError(f"upload of slot {link.slot} is damaged (MD5 {got}, expected {expected})")
        path = self.path(link.profile_id, link.product, link.slot)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        log.info("cloud save of %s: slot %s of product %d stored (%d B)", link.profile_id, link.slot, link.product,
                 len(data))

    def read(self, link: Link) -> bytes:
        path = self.path(link.profile_id, link.product, link.slot)
        if not path.exists():
            raise CloudSaveError(f"slot {link.slot} not stored")
        return path.read_bytes()

    # ---- admin ------------------------------------------------------------------------------------
    def all(self, profile_id: str | None = None) -> list[dict]:
        players = [profile_id.lower()] if profile_id else [p.profile_id for p in self.repos.players.all()]
        out = []
        for pid in players:
            for e in self.repos.entities.for_profile(pid, TYPE):
                m = NAME.match(e.get("name") or "")
                if m:
                    out.append({"profile_id": pid, "product": int(m.group(1)), "entity": e,
                                "complete": self.complete(pid, int(m.group(1)), e)})
        return out

    def delete(self, profile_id: str, product: int | None = None) -> int:
        n = 0
        for item in self.all(profile_id):
            if product is not None and item["product"] != product:
                continue
            self.repos.entities.delete(item["entity"]["entityId"])
            folder = self.root / profile_id.lower() / str(item["product"])
            for f in folder.glob("*") if folder.exists() else ():
                f.unlink()
            n += 1
        return n
