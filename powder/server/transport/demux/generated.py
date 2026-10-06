"""Ubisoft Connect demux replies built by the server (formats from the real service's replies).

  friends_service   initialize -> no friends; presence updates and acks -> success
  playtime_service  -> success
  ownership_service Steam proof-of-purchase registration -> success; ownership token for a product ->
                    a fresh opaque token (Connect only passes it on; this server is the only validator)
  ach_frontend      earned achievements per player: stored when Connect writes them, returned when it reads
Owned games (ownership_service initialize) are in ownership.py.
"""
from __future__ import annotations

import base64
import logging
import secrets

from . import pb
from .services import ServiceContext, ServiceRequest, handler, make_reply

log = logging.getLogger("powder.demux")


def _success(reply_kind: int):
    def fn(ctx: ServiceContext, req: ServiceRequest) -> bytes:
        return make_reply(req, reply_kind, [(1, 1)])
    return fn


# friends: 22 initialize (reply 22: {1 result, 2 friend...}), 13 / 19 presence and status updates
handler("friends_service", 22)(_success(22))
handler("friends_service", 13)(_success(13))
handler("friends_service", 19)(_success(19))

# playtime: 2 = playtime report (reply 2: {1 result})
@handler("playtime_service", 2)
def playtime(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    return make_reply(req, 2, [(1, 0)])


# ownership: 19 = Steam proof-of-purchase registration (reply 18), 14 = ownership token (reply 13)
handler("ownership_service", 19)(_success(18))


@handler("ownership_service", 14)
def ownership_token(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    token = base64.urlsafe_b64encode(secrets.token_bytes(420)).decode().rstrip("=")
    return make_reply(req, 13, [(1, 1), (2, token)])


# achievements (flat style). Connect keeps the earned achievements locally and syncs them:
#   4 = write: {1: {1: {1: {1 product code, 2 platform, 3 space id}, 2: {1: {1 id, 2 unix time}...}}...}}
#       (its whole known list for each product; reply 4: empty)
#   3 = read:  {2: {1 product code, 2 platform}} -> the stored list, same shape (see ach_earned)
def product_name(key: bytes) -> str | None:
    """'STP:PC' from a product key message {1 code, 2 platform, ...}."""
    fields = pb.try_decode(key) if isinstance(key, bytes) else None
    code, platform = (pb.text(fields, 1), pb.text(fields, 2)) if fields else ("", "")
    return f"{code}:{platform}" if code and platform else None


def earned_from(entries: bytes) -> dict[int, int]:
    """{achievement id: unix time} from {1: {1 id, 2 time}, 1: ...}."""
    out = {}
    for num, value, _ in pb.decode(entries) if isinstance(entries, bytes) and entries else []:
        e = pb.try_decode(value) if num == 1 and isinstance(value, bytes) else None
        if e and isinstance(pb.first(e, 1), int):
            out[pb.first(e, 1)] = pb.first(e, 2, 0)
    return out


def earned_by_product(body: bytes) -> dict[str, dict[int, int]]:
    """The products and their achievements in a write (kind 4) request."""
    result: dict[str, dict[int, int]] = {}
    for num, products, _ in pb.decode(body) if isinstance(body, bytes) and body else []:
        if num != 1 or not isinstance(products, bytes):
            continue
        for pnum, entry, _ in pb.try_decode(products) or []:
            fields = pb.try_decode(entry) if pnum == 1 and isinstance(entry, bytes) else None
            name = product_name(pb.first(fields, 1)) if fields else None
            if name:
                result.setdefault(name, {}).update(earned_from(pb.first(fields, 2, b"")))
    return result


@handler("ach_frontend", 4)
def ach_write(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    if ctx.profile_id:
        for product, earned in earned_by_product(req.body).items():
            new = ctx.services.repos.achievements.earn(ctx.profile_id, product, earned)
            if new:
                log.info("achievements: %d new for %s (%s), %d in total", new, ctx.profile_id, product,
                         len(ctx.services.repos.achievements.all(ctx.profile_id, product)))
    return make_reply(req, 4, b"")


@handler("ach_frontend", 3)
def ach_earned(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    """{3: {1 profile id, 2: {1: {1: {1 product code, 2 platform}, 2: {1: {1 achievement id, 2 time}...}}}}}:
    the product key is echoed from the request, with the player's stored achievements. The earned-list
    message (2) must be present even when empty, or Connect reports "Achievements is missing for product"
    and fails to sync."""
    product = None
    fields = pb.try_decode(req.body) if isinstance(req.body, bytes) else None
    if fields:                                          # request {2: {1 product code, 2 platform}}
        product = pb.first(fields, 2)
    key = product if isinstance(product, bytes) else pb.encode([(1, "STP"), (2, "PC")])
    name = product_name(key)
    earned = ctx.services.repos.achievements.all(ctx.profile_id, name) if ctx.profile_id and name else {}
    entries = pb.encode([(1, [(1, ach_id), (2, ts)]) for ach_id, ts in earned.items()])
    return make_reply(req, 3, [(1, ctx.profile_id or ""), (2, [(1, [(1, key), (2, entries)])])])
