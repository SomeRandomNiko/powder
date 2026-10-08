"""One Ubisoft Connect demux connection (TLS to dmx.upc.ubisoft.com:443).

Every message is a 4-byte big-endian length + protobuf. Client -> server ("upstream"):
  1 request  {1 request_id, 2 authenticate {1 {3 ticket}, 2 "uplay_pc"},
              3 open_connection {2 service name}, 5 patch_info, 6 service_request {1 name, 2 data}}
  2 push     {1 data {1 connection_id, 2 framed service message}, 2 connection_closed {1 id},
              3 keep_alive, 4 client_version {1 version}, 6 product_started {1 product_id},
              7 product_ended {1 product_id}}
Server -> client ("downstream"):
  1 response {1 request_id, 2 authenticate {1 success}, 3 open_connection {1 connection_id, 2 success},
              5 patch_info {...}, 6 service_request {1 success, 2 data}}
  2 push     {1 data {1 connection_id, 2 framed service message}}
Messages inside a service connection: services.py. Field names follow what the messages carry; the
numbers are what matters.
"""
from __future__ import annotations

import asyncio
import logging

from . import generated, ownership  # noqa: F401  (register their service handlers)
from . import pb
from .services import ServiceContext, answer

log = logging.getLogger("powder.demux")

MAX_MESSAGE = 16 << 20


class DemuxConnection:
    def __init__(self, services, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.services = services
        self.reader, self.writer = reader, writer
        peer = writer.get_extra_info("peername") or ("?", 0)
        self.client_ip, self.peer = peer[0], f"{peer[0]}:{peer[1]}"
        self.profile_id: str | None = None
        self.player_name: str | None = None
        self.channels: dict[int, str] = {}           # connection id -> service name
        self.next_channel = 1
        self.ctx = ServiceContext(services, None)
        self.client_version = 0

    # ---- io ---------------------------------------------------------------------------------------
    async def run(self) -> None:
        try:
            while True:
                head = await self.reader.readexactly(4)
                n = int.from_bytes(head, "big")
                if n > MAX_MESSAGE:
                    log.warning("%s: message of %d bytes, closing", self.peer, n)
                    return
                await self.handle(await self.reader.readexactly(n))
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            log.info("demux %s closed (%s)", self.peer, ", ".join(self.channels.values()) or "no services")
            self.writer.close()

    async def send(self, fields: list) -> None:
        self.writer.write(pb.frame(pb.encode(fields)))
        await self.writer.drain()

    # ---- dispatch ---------------------------------------------------------------------------------
    async def handle(self, msg: bytes) -> None:
        fields = pb.try_decode(msg)
        if fields is None:
            log.warning("%s: undecodable message %s", self.peer, msg[:32].hex())
            return
        req, push = pb.first(fields, 1), pb.first(fields, 2)
        if isinstance(req, bytes):
            await self.request(pb.decode(req))
        elif isinstance(push, bytes):
            await self.push(pb.decode(push))

    async def request(self, req: list) -> None:
        rid = pb.first(req, 1, 0)
        kind = next((f for f in req if f[0] != 1), None)
        if kind is None:
            return
        num, body = kind[0], pb.try_decode(kind[1]) if isinstance(kind[1], bytes) else []
        if num == 2:
            reply = self.authenticate(body or [])
        elif num == 3:
            reply = self.open_connection(pb.text(body or [], 2))
        elif num == 5:                                  # patch info: "your client is up to date"
            reply = self.patch_info()
        elif num == 6:
            reply = self.service_request(pb.text(body or [], 1), pb.first(body or [], 2, b""))
        else:
            log.warning("DEMUX UNHANDLED request kind %d from %s", num, self.peer)
            return
        await self.send([(1, [(1, rid), (num, reply)])])

    def patch_info(self) -> bytes:
        """The latest Ubisoft Connect version is always the one this client runs: a version from the
        recording (field 5) makes a newer Connect try to patch itself to it, fetch a file list the server
        doesn't have, and give up (on some PCs closing when a game starts)."""
        recorded = self.services.assets.products.patch_info
        if not recorded:
            return pb.encode([(1, 1), (3, 0), (5, self.client_version), (6, 0), (7, 0)])
        if not self.client_version:
            return recorded
        return pb.encode(pb.replace(pb.decode(recorded), 5, self.client_version))

    def authenticate(self, body: list) -> bytes:
        token = pb.first(body, 1)
        ticket = pb.text(pb.try_decode(token) or [], 3) if isinstance(token, bytes) else ""
        session = self.services.accounts.session_for_ticket(ticket, self.client_ip)
        self.profile_id = session.profile_id if session else None
        self.player_name = session.player.name if session else None
        self.ctx.profile_id = self.profile_id
        log.info("demux login %s from %s (%s)", session.player.name if session else "REFUSED",
                 self.peer, pb.text(body, 2) or "?")
        return pb.encode([(1, 1 if session else 0)])

    def open_connection(self, name: str) -> bytes:
        cid = self.next_channel
        self.next_channel += 1
        self.channels[cid] = name
        log.info("demux %s: open %s -> connection %d", self.peer, name, cid)
        return pb.encode([(1, cid), (2, 1)])

    def service_request(self, name: str, data: bytes) -> bytes:
        """One-off requests outside a service connection: {1 result, 2 data}."""
        if name == "utility_service":                   # where the player is
            cfg = self.services.config.server
            out = pb.encode([(1, [(1, [(1, cfg.country), (2, cfg.region)])])])
        elif name == "client_configuration_service":   # the client's version, echoed back
            out = data
        else:
            log.warning("DEMUX UNHANDLED service request %s (%dB)", name, len(data))
            return pb.encode([(1, 0)])
        log.info("demux %s: service request %s", self.peer, name)
        return pb.encode([(1, 1), (2, out)])

    async def push(self, push: list) -> None:
        for num, value, _ in push:
            if num == 1:
                d = pb.decode(value)
                cid, data = pb.first(d, 1), pb.first(d, 2, b"")
                service = self.channels.get(cid)
                if service is None:
                    log.warning("demux %s: data for unknown connection %s", self.peer, cid)
                    continue
                try:
                    msg = pb.unframe(data)
                except pb.DecodeError:
                    log.warning("demux %s: bad %s frame", self.peer, service)
                    continue
                reply = answer(self.ctx, service, msg)
                if reply is not None:
                    await self.send([(2, [(1, [(1, cid), (2, pb.frame(reply))])])])
            elif num == 2:
                cid = pb.first(pb.decode(value), 1)
                log.info("demux %s: %s closed by client", self.peer, self.channels.pop(cid, cid))
            elif num == 4:
                self.client_version = pb.first(pb.decode(value), 1, 0)
                log.info("demux %s: client version %s", self.peer, self.client_version)
            elif num in (6, 7):
                product = pb.first(pb.decode(value), 1)
                what = "started" if num == 6 else "ended"
                log.info("demux %s: product %s %s", self.peer, product, what)
                await self.services.events.emit(f"connect_product_{what}", profile_id=self.profile_id,
                                                product_id=product)
            elif num != 3:                                    # 3 = keep-alive
                log.warning("DEMUX UNHANDLED push kind %d from %s", num, self.peer)
