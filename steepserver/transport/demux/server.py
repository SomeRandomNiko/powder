"""TLS listener for Ubisoft Connect's demux (dmx.upc.ubisoft.com:443; the hosts file maps it to a
loopback address of its own because 127.0.0.1:443 is the HTTPS server)."""
from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path

from .connection import DemuxConnection

log = logging.getLogger("steep.demux")


def tls_context(cert_file: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_file))
    return ctx


async def start(services, host: str, port: int, cert_file: Path) -> asyncio.AbstractServer:
    async def on_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = DemuxConnection(services, reader, writer)
        log.info("demux connection from %s", conn.peer)
        try:
            await conn.run()
        except Exception:                                   # one bad client must not stop the server
            log.exception("demux connection %s failed", conn.peer)

    return await asyncio.start_server(on_client, host, port, ssl=tls_context(cert_file))
