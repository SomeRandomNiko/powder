"""Ubisoft Connect demux replies built by the server (formats from the real service's replies).

  friends_service   initialize -> every other player of the server; presence updates and acks -> success
  playtime_service  -> success
  ownership_service Steam proof-of-purchase registration -> success; a game's Uplay ticket, ownership token
                    and signature -> fresh opaque values
                    (Connect only passes them on; this server is the only validator)
  ach_frontend      earned achievements per player: stored when Connect writes them, returned when it reads
  cloudsave_service links for uploading, downloading and deleting cloud saves on this server (services/cloudsaves.py)
Owned games (ownership_service initialize) are in ownership.py.
"""
from __future__ import annotations

import base64
import logging
import secrets
import time

from ...services.cloudsaves import CloudSaveError
from ...services.friends import friends_of
from . import pb
from .services import ServiceContext, ServiceRequest, handler, make_reply

log = logging.getLogger("powder.demux")


def _success(reply_kind: int):
    def fn(ctx: ServiceContext, req: ServiceRequest) -> bytes:
        return make_reply(req, reply_kind, [(1, 1)])
    return fn


# friends: 22 initialize (reply 22: {1 result, 2 friend...}), 13 / 19 presence and status updates
@handler("friends_service", 22)
def friends_initialize(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    """The friends list: every other player of the server (services/friends.py). An entry as recorded:
    {1: 0, 2: {1 profile id, 2 name, 3 empty, 4 0}, 3: 3} (a friend; type 1 entries without a name, state 0,
    are another relationship and not used)."""
    entries = [(2, [(1, 0), (2, [(1, f.profile_id), (2, f.name), (3, b""), (4, 0)]), (3, 3)])
               for f in friends_of(ctx.services, ctx.profile_id)]
    log.info("friends list for %s: %d friend(s)", ctx.profile_id, len(entries))
    return make_reply(req, 22, [(1, 1), *entries])


handler("friends_service", 13)(_success(13))
handler("friends_service", 19)(_success(19))

# cloud saves (mg.protocol.cloudsave_service, definitions from Connect itself): 2 = CloudsaveUrlReq {1 ownership
#   token, 2 uplay id (product), 3 method (1 Put, 2 Get, 3 Delete), 4*: Item {1 name, 2: {1 write length, 2 md5
#   base64}}} -> 2 CloudsaveUrlRsp {1 status (1 Ok, 2 InternalError, 3 Denied), 2*: {1 header text, 2 url}}.
#   Recorded for Put: header "Content-Length: N\nContent-MD5: x\n", url a presigned S3 link; here links on
#   this server.
CLOUD_FILES = "https://public-ubiservices.ubi.com/_powder/cloudsaves"
CLOUD_METHODS = {1: "PUT", 2: "GET", 3: "DELETE"}
CLOUD_OK, CLOUD_ERROR, CLOUD_DENIED = 1, 2, 3


@handler("cloudsave_service", 2)
def cloudsave_links(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    body = pb.try_decode(req.body) if isinstance(req.body, bytes) else None
    product, method = (pb.first(body, 2), CLOUD_METHODS.get(pb.first(body, 3))) if body else (None, None)
    if not ctx.profile_id or not isinstance(product, int) or method is None:
        return make_reply(req, 2, [(1, CLOUD_DENIED)])
    saves, entries = ctx.services.cloudsaves, []
    for _, raw, _ in [f for f in body if f[0] == 4]:
        item = pb.try_decode(raw) or []
        slot, meta = pb.text(item, 1), pb.try_decode(pb.first(item, 2, b"")) or []
        length, md5 = pb.first(meta, 1), pb.text(meta, 2)
        try:
            if method == "GET" and not saves.path(ctx.profile_id, product, slot).exists():
                log.warning("cloud save of %s: slot %s of product %d asked for but not stored", ctx.profile_id,
                            slot, product)
                return make_reply(req, 2, [(1, CLOUD_ERROR)])
            token = saves.link(ctx.profile_id, product, slot, md5 or None, method)
        except CloudSaveError as e:
            log.warning("cloud save links for %s: %s", ctx.profile_id, e)
            return make_reply(req, 2, [(1, CLOUD_ERROR)])
        headers = "" if method != "PUT" else (f"Content-Length: {length}\n" if isinstance(length, int) else "") + \
            (f"Content-MD5: {md5}\n" if md5 else "")
        entries.append((2, [(1, headers), (2, f"{CLOUD_FILES}/{product}/{slot}?t={token}")]))
    log.info("cloud save of %s: %d %s link(s) for product %d", ctx.profile_id, len(entries), method, product)
    return make_reply(req, 2, [(1, CLOUD_OK), *entries])


# ownership tokens and signatures (mg.protocol.ownership, definitions from Connect): 28 OwnershipTokenReq
#   {1 product} -> 27 OwnershipTokenRsp {1 success, 2 token, 3 expiration}; 26 SignOwnershipReq {1 product} ->
#   25 SignOwnershipRsp {1 success, 2 branch id, 3 expiration, 4 signature}. Connect needs them e.g. before a
#   cloud save sync; it only passes them on to this server, which accepts any.
OWNERSHIP_LIFETIME_S = 24 * 3600


@handler("ownership_service", 28)
def ownership_token(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    token = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    return make_reply(req, 27, [(1, 1), (2, token), (3, int(time.time()) + OWNERSHIP_LIFETIME_S)])


@handler("ownership_service", 26)
def ownership_signature(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    return make_reply(req, 25, [(1, 1), (2, 0), (3, int(time.time()) + OWNERSHIP_LIFETIME_S),
                                (4, secrets.token_bytes(64))])


# playtime: 2 = playtime report (reply 2: {1 result})
@handler("playtime_service", 2)
def playtime(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    return make_reply(req, 2, [(1, 0)])


# ownership: 19 = Steam proof-of-purchase registration (reply 18), 14 = GetUplayPCTicketReq (reply 13)
handler("ownership_service", 19)(_success(18))


@handler("ownership_service", 14)
def uplay_pc_ticket(ctx: ServiceContext, req: ServiceRequest) -> bytes:
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
