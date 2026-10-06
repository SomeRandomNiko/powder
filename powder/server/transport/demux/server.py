"""Ubisoft Connect's demux connections (dmx.upc.ubisoft.com:443). The TLS listener is shared with HTTPS
(transport/tls_router.py routes by hostname); this module only runs a connection once it is handed over."""
from __future__ import annotations

import asyncio
import logging

from .connection import DemuxConnection

log = logging.getLogger("powder.demux")


def handler(services):
    """-> the stream handler the TLS router calls for each demux connection."""
    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = DemuxConnection(services, reader, writer)
        log.info("demux connection from %s", conn.peer)
        services.connect_clients.add(conn)
        try:
            await conn.run()
        except Exception:                                   # one bad client must not stop the server
            log.exception("demux connection %s failed", conn.peer)
        finally:
            services.connect_clients.discard(conn)
    return on_client
