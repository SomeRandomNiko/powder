"""ownership_service initialize: the games Ubisoft Connect thinks the player owns.

The reply lists products, each with its configuration as YAML (field 17). They come from the game-data
pack (connect/demux.json: Steep's Steam entry 3279, product 3280 and its add-ons). The YAML includes
`cloud_saves: yes|no`; this server does not store cloud saves, so unless [server] connect_cloud_saves is
on, it is switched off: Connect then skips syncing instead of failing (and asking) at launch and exit.

Reply layout: {1: {1 request_id, 2 initialize {1 result, 2 {1 product (repeated)}, 5 0, 6 0}}}
"""
from __future__ import annotations

import logging

from . import pb
from .services import ServiceContext, ServiceRequest, handler, make_reply

log = logging.getLogger("steep.demux")

SERVICE = "ownership_service"
INITIALIZE = 2
PRODUCT_CONFIG = 17


def without_cloud_saves(products: list[bytes]) -> tuple[list[bytes], int]:
    """Products with `cloud_saves: yes` turned off in their configuration -> (products, number changed)."""
    out, changed = [], 0
    for value in products:
        fields = pb.decode(value)
        config = pb.first(fields, PRODUCT_CONFIG)
        if isinstance(config, bytes) and b"cloud_saves: yes" in config:
            fields = pb.replace(fields, PRODUCT_CONFIG, config.replace(b"cloud_saves: yes", b"cloud_saves: no"))
            value = pb.encode(fields)
            changed += 1
        out.append(value)
    return out, changed


def initialize_body(products: list[bytes]) -> list:
    return [(1, 1), (2, [(1, p) for p in products]), (5, 0), (6, 0)]


@handler(SERVICE, INITIALIZE)
def initialize(ctx: ServiceContext, req: ServiceRequest) -> bytes | None:
    products = ctx.services.assets.products.products
    if not products:
        log.warning("demux owned games: no products in the game-data pack; Connect will show no games")
    if not ctx.services.config.server.connect_cloud_saves:
        products, changed = without_cloud_saves(products)
        log.info("demux owned games: %d products, cloud saves switched off for %d", len(products), changed)
    return make_reply(req, INITIALIZE, initialize_body(products))
