"""Steep's UGC service (steep-prod-pc.steep.olympus.ubi.com/ugc/v1): player-made content such as photos
(typeId 2) and trails (replays, the ghosts of challenges). Kept for the server's players, files in data/ugc/.

From recordings of Ubisoft's service: a list (GET /ugc/v1/?algo=Steep&type_id=&world_id=&profile_id=...)
of items {id, typeId, worldId, profileId, pubDate, metadata, likeCounter, downloadCounter, liked, tags,
links {creationTimestamp, expirationTimestamp, urls {Thumbnail, Data}}, ...}; PUT/DELETE {id}/like and
PUT {id}/download answer 204. The files were on Amazon S3 behind signed links; here they are on this
server (/_powder/ugc/<id>[_thumbnail]).

Seen live: a photo is created with POST /ugc/v1/ {typeId 2, worldId, metadata, tags, datas: the JPEG,
base64}; it is kept as the item's file (served as Data and as Thumbnail), not in the item. Deleting is
DELETE {id} followed by PUT {id}/hide. {id}/url comes from the game's code (no recording).
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import time
import uuid

from aiohttp import web

from .keys import DATA_DIR, HOW, SERVICES, SESSION
from .routes_ubi import json_response

log = logging.getLogger("powder.https")
FILE_NAME = re.compile(r"^[0-9a-f-]{36}(_thumbnail)?$")
LINK_LIFETIME_S = 3600


def _me(request: web.Request) -> str:
    session = request.get(SESSION)
    return session.profile_id if session else request.app[SERVICES].default_player_id()


def _links(request: web.Request, ugc_id: str) -> dict:
    now = int(time.time())
    base = f"https://{request.host}/_powder/ugc/{ugc_id}"
    return {"creationTimestamp": now, "expirationTimestamp": now + LINK_LIFETIME_S,
            "urls": {"Thumbnail": f"{base}_thumbnail", "Data": base}}


def _out(request: web.Request, item: dict) -> dict:
    extra = {k: v for k, v in item["extra"].items() if k != "datas"}        # a file, not part of the item
    return {**extra, "id": item["ugc_id"], "typeId": item["type_id"], "worldId": item["world_id"],
            "profileId": item["profile_id"], "pubDate": item["pub_date"], "metadata": item["metadata"],
            "likeCounter": item["likes"], "downloadCounter": item["downloads"],
            "liked": _me(request).lower() in item["liked_by"], "tags": item["tags"],
            "links": _links(request, item["ugc_id"]), "showcase": None, "reportCounter": 0, "isBanned": False,
            "isBanProtected": False, "platform": "uplay", "version": 1}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def ugc_list(request: web.Request) -> web.Response:
    q = request.query
    profiles = q.getall("profile_id", [])
    items = request.app[SERVICES].repos.ugc.find(_int(q.get("type_id")), _int(q.get("world_id")), profiles or None)
    return json_response(request, [_out(request, i) for i in items], f"ugc({len(items)})")


def _files(request: web.Request):
    return request.app[DATA_DIR] / "ugc"


async def ugc_create(request: web.Request) -> web.Response:
    raw = await request.read()
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    datas = body.pop("datas", None)
    log.info("ugc create from %s (%d B): %s", _me(request), len(raw), json.dumps(body)[:1500])
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    type_id = _int(body.get("typeId", body.get("type_id"))) or 0
    world_id = _int(body.get("worldId", body.get("world_id", metadata.get("worldId"))))
    tags = body.get("tags") if isinstance(body.get("tags"), list) else []
    known = {"metadata", "typeId", "type_id", "worldId", "world_id", "tags", "id", "profileId"}
    extra = {k: v for k, v in body.items() if k not in known}
    item = request.app[SERVICES].repos.ugc.create(str(uuid.uuid4()), type_id, world_id, _me(request), metadata,
                                                    tags, extra)
    if isinstance(datas, str):
        try:
            data = base64.b64decode(datas, validate=False)
        except (binascii.Error, ValueError):
            data = b""
        if data:
            _files(request).mkdir(parents=True, exist_ok=True)
            (_files(request) / item["ugc_id"]).write_bytes(data)
    log.info("ugc %s created (type %d, world %s) by %s", item["ugc_id"], type_id, world_id, _me(request))
    return json_response(request, _out(request, item), "ugc-created")


async def ugc_item(request: web.Request) -> web.Response:
    repo, ugc_id = request.app[SERVICES].repos.ugc, request.match_info["ugc_id"]
    item = repo.get(ugc_id)
    if item is None:
        return web.json_response({"error": "not found"}, status=404)
    if request.method == "DELETE":
        if item["profile_id"] == _me(request).lower():
            repo.delete(ugc_id)
            for name in (ugc_id, f"{ugc_id}_thumbnail"):
                (_files(request) / name).unlink(missing_ok=True)
            log.info("ugc %s deleted", ugc_id)
        request[HOW] = "ugc-deleted"
        return web.Response(status=204)
    return json_response(request, _out(request, item), "ugc-item")


async def ugc_action(request: web.Request) -> web.Response:
    repo, ugc_id, action = request.app[SERVICES].repos.ugc, request.match_info["ugc_id"], request.match_info["action"]
    item = repo.get(ugc_id)
    request[HOW] = f"ugc-{action}"
    if item is None:                    # e.g. the hide the game sends right after deleting
        return web.Response(status=204)
    if action == "like":
        repo.like(ugc_id, _me(request), request.method != "DELETE")
    elif action == "download":
        repo.downloaded(ugc_id)
    elif action == "hide" and item["profile_id"] == _me(request).lower():
        repo.hide(ugc_id)
    elif action == "url":
        return json_response(request, _links(request, ugc_id), "ugc-url")
    else:
        log.info("ugc %s %s/%s: not known, acknowledged", request.method, ugc_id, action)
    return web.Response(status=204)


async def ugc_file(request: web.Request) -> web.StreamResponse:
    """The files behind an item's links: the game uploads (PUT/POST) and downloads (GET) them here."""
    name = request.match_info["name"]
    item = request.app[SERVICES].repos.ugc.get(name.split("_")[0]) if FILE_NAME.match(name) else None
    if item is None:
        return web.Response(status=404)
    path = _files(request) / name
    original = _files(request) / item["ugc_id"]
    if not original.exists() and isinstance(item["extra"].get("datas"), str):     # stored before files were
        try:
            data = base64.b64decode(item["extra"]["datas"])
        except (binascii.Error, ValueError):
            data = b""
        if data:
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(data)
    if request.method == "GET" and name.endswith("_thumbnail") and not path.exists():
        path = _files(request) / name.split("_")[0]          # a photo is its own thumbnail
    if request.method in ("PUT", "POST"):
        data = await request.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log.info("ugc file %s uploaded (%d B, %s)", name, len(data), request.content_type)
        return web.Response(status=200)
    if not path.exists():
        return web.Response(status=404)
    request[HOW] = "ugc-file"
    return web.FileResponse(path, headers={"Content-Type": "application/octet-stream"})


def add_routes(app: web.Application) -> None:
    r = app.router
    r.add_get("/ugc/v1/", ugc_list)
    r.add_post("/ugc/v1/", ugc_create)
    r.add_post("/ugc/v1", ugc_create)
    r.add_route("*", "/ugc/v1/{ugc_id}/{action}", ugc_action)
    r.add_route("*", "/ugc/v1/{ugc_id}", ugc_item)
    r.add_route("*", "/_powder/ugc/{name}", ugc_file)
