"""Ubisoft Connect demux replies built by the server (formats from the real service's replies).

  friends_service   initialize -> no friends; presence updates and acks -> success
  playtime_service  -> success
  ownership_service Steam proof-of-purchase registration -> success; ownership token for a product ->
                    a fresh opaque token (Connect only passes it on; this server is the only validator)
  ach_frontend      -> no achievements unlocked
Owned games (ownership_service initialize) are in ownership.py.
"""
from __future__ import annotations

import base64
import secrets

from . import pb
from .services import ServiceContext, ServiceRequest, handler, make_reply


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


# achievements (flat style): 4 = subscribe (reply 4: empty), 3 = unlocked achievements per product
@handler("ach_frontend", 4)
def ach_subscribe(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    return make_reply(req, 4, b"")


@handler("ach_frontend", 3)
def ach_unlocked(ctx: ServiceContext, req: ServiceRequest) -> bytes:
    """{3: {1 profile id, 2: {1: {1: {1 product code, 2 platform}, 2: {1: {1 achievement id, 2 time}...}}}}}:
    the product key is echoed from the request, with nothing unlocked. The earned-list message (2) must
    be present even when empty, or Connect reports "Achievements is missing for product" and fails to
    sync."""
    product = None
    fields = pb.try_decode(req.body) if isinstance(req.body, bytes) else None
    if fields:                                          # request {2: {1 product code, 2 platform}}
        product = pb.first(fields, 2)
    key = product if isinstance(product, bytes) else pb.encode([(1, "STP"), (2, "PC")])
    return make_reply(req, 3, [(1, ctx.profile_id or ""), (2, [(1, [(1, key), (2, b"")])])])
