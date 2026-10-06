"""One TLS listener (port 443) for everything, routed by the hostname the client asked for (SNI).

Ubisoft Connect's demux (dmx.upc.ubisoft.com) is not HTTP, everything else is. Routing by name lets every
Ubisoft hostname point at one address, which a hosts file, a DNS server or a remote/docker server can all
provide. After the handshake the connection is handed to:
  demux    -> transport/demux (StreamReader/StreamWriter around the TLS transport)
  anything -> aiohttp's request handler (the HTTPS app), as if aiohttp had accepted it itself
The peer address is kept, so per-IP player binding still works.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from ..hostnames import DEMUX_HOST

log = logging.getLogger("powder.tls")

StreamHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def tls_context(cert_file: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_file))

    def remember_sni(ssl_object, server_name, _ctx):
        # server-side SSLObject.server_hostname is always None: keep what the client asked for
        ssl_object.steep_sni = (server_name or "").lower()
        # logged before the handshake completes: a hello without a matching "tls ok" = the client gave up
        # no SNI = a client connecting by address (the launcher's status checks): only at debug level
        (log.info if ssl_object.steep_sni else log.debug)("tls hello for %s", ssl_object.steep_sni or "(no SNI)")
    ctx.sni_callback = remember_sni
    return ctx


class _Router(asyncio.Protocol):
    def __init__(self, http_server: web.Server, demux: StreamHandler):
        self.http_server, self.demux = http_server, demux

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # called after the TLS handshake: hand the connection over and get out of the way
        sni = getattr(transport.get_extra_info("ssl_object"), "steep_sni", "")
        peer = transport.get_extra_info("peername") or ("?", 0)
        (log.info if sni else log.debug)("tls ok %s:%s for %s", peer[0], peer[1], sni or "(no SNI)")
        if sni == DEMUX_HOST:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            proto = asyncio.StreamReaderProtocol(reader)
            transport.set_protocol(proto)
            proto.connection_made(transport)
            writer = asyncio.StreamWriter(transport, proto, reader, loop)
            asyncio.ensure_future(self.demux(reader, writer))
        else:
            proto = self.http_server()
            transport.set_protocol(proto)
            proto.connection_made(transport)

    def data_received(self, data: bytes) -> None:          # never reached: the protocol was swapped
        raise RuntimeError("router received data after handover")


async def start(runner: web.AppRunner, demux: StreamHandler, host: str, port: int,
                cert_file: Path) -> asyncio.AbstractServer:
    """`runner` must be set up (AppRunner.setup()) but not started on a site of its own."""
    http_server = runner.server
    if http_server is None:
        raise RuntimeError("AppRunner.setup() first")
    loop = asyncio.get_running_loop()
    return await loop.create_server(lambda: _Router(http_server, demux), host, port,
                                    ssl=tls_context(cert_file))
