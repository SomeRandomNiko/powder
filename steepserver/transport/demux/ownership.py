"""ownership_service: what Ubisoft Connect learns about the player's games.

Initialize (request kind 2) answers with every owned product, each with its configuration as YAML
(field 17), which includes `cloud_saves: yes|no`. This server does not store cloud saves, so unless
[server] connect_cloud_saves is on, the recorded reply is sent with cloud saves switched off: Connect
then skips syncing entirely, instead of failing (and asking the player) at every launch and exit.

Reply layout: {1: {1 request_id, 2 initialize {1 result, 2 {1 product (repeated)}, ...}}}
"""
from __future__ import annotations

import logging

from . import pb
from .services import ServiceContext, ServiceRequest, handler, with_request_id

log = logging.getLogger("steep.demux")

SERVICE = "ownership_service"
INITIALIZE = 2
PRODUCT_CONFIG = 17


def without_cloud_saves(reply: bytes) -> tuple[bytes, int]:
    """The initialize reply with `cloud_saves: yes` turned off in every product configuration.
    -> (reply, number of products changed)"""
    top = pb.decode(reply)
    inner = pb.decode(pb.first(top, 1))
    init = pb.decode(pb.first(inner, INITIALIZE))
    products = pb.decode(pb.first(init, 2))
    changed = 0
    new_products = []
    for num, value, wt in products:
        fields = pb.decode(value)
        config = pb.first(fields, PRODUCT_CONFIG)
        if isinstance(config, bytes) and b"cloud_saves: yes" in config:
            fields = pb.replace(fields, PRODUCT_CONFIG, config.replace(b"cloud_saves: yes", b"cloud_saves: no"))
            changed += 1
            value = pb.encode(fields)
        new_products.append((num, value, wt))
    init = pb.replace(init, 2, pb.encode(new_products))
    inner = pb.replace(inner, INITIALIZE, pb.encode(init))
    return pb.encode(pb.replace(top, 1, pb.encode(inner))), changed


@handler(SERVICE, INITIALIZE)
def initialize(ctx: ServiceContext, req: ServiceRequest) -> bytes | None:
    if ctx.services.config.server.connect_cloud_saves:
        return None                                   # recorded reply as is
    recorded = ctx.services.assets.demux.reply(SERVICE, INITIALIZE, 0)
    if recorded is None:
        return None
    reply, changed = without_cloud_saves(recorded)
    log.info("demux owned games: cloud saves switched off for %d products", changed)
    return with_request_id(reply, req.request_id)
