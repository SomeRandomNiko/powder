"""NEX / Quazal RendezVous transport: PRUDP (classic V0) over UDP with the secure handshake.

Demultiplexes datagrams by client address into PrudpConnections, performs SYN / CONNECT (ECDH key
exchange signed with our ECDSA key, whose public half the HTTPS server publishes in
/rendezvous/configuration), and hands complete RMC requests to the RmcDispatcher.
See notes/nex-recon.md for the protocol.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time

from cryptography.hazmat.primitives.asymmetric import ec

from . import rmc_codec, secure
from .connection import PrudpConnection
from .dispatch import NexContext, RmcDispatcher
from .prudp import (CONNECT, DATA, DISCONNECT, FLAG_ACK, FLAG_KEY_EXCHANGE, FLAG_NEED_ACK, PING, SYN,
                    Packet)

log = logging.getLogger("powder.nex")
TICK_S = 0.25


class NexServer(asyncio.DatagramProtocol):
    def __init__(self, services, dispatcher: RmcDispatcher, signing_key: ec.EllipticCurvePrivateKey,
                 public_host: str, port: int):
        self.services, self.dispatcher, self.signing_key = services, dispatcher, signing_key
        self.public_host, self.port = public_host, port
        self.conns: dict[tuple, PrudpConnection] = {}
        self.transport: asyncio.DatagramTransport | None = None
        self._pids = itertools.count(0x1001)        # never reused, unlike the prototype's len(conns)
        self._mm_tick = 0.0
        self._tasks: set[asyncio.Task] = set()

    # ---- asyncio plumbing ------------------------------------------------------------------------
    def connection_made(self, transport):
        self.transport = transport

    def _sendto(self, raw: bytes, addr) -> None:
        if self.transport is not None:
            self.transport.sendto(raw, addr)

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            p = Packet.decode(data)
        except ValueError as e:
            log.debug("%s: undecodable datagram (%s)", addr, e)
            return
        c = self.conns.get(addr)
        if c is not None:
            c.last_seen = time.monotonic()
        try:
            if p.type == SYN and not p.flags & FLAG_ACK:
                self.on_syn(addr, p)
            elif p.type == CONNECT and not p.flags & FLAG_ACK:
                self.on_connect(addr, p)
            elif c is None:
                log.debug("%s: %s for unknown connection ignored", addr, p.type)
            elif p.flags & FLAG_ACK:
                c.on_ack(p)
            elif p.type == DATA:
                for message in c.receive_data(p):
                    self._spawn(self.on_message(c, message))
            elif p.type == PING:
                if p.flags & FLAG_NEED_ACK:
                    c.send(c.reply(type=PING, flags=FLAG_ACK, sequence_id=p.sequence_id))
            elif p.type == DISCONNECT:
                for _ in range(3):
                    c.send(c.reply(type=DISCONNECT, flags=FLAG_ACK, sequence_id=p.sequence_id))
                self.drop(c, "client disconnected")
        except Exception:  # noqa: BLE001 - keep serving other clients
            log.exception("%s: error handling %s packet", addr, p.type)

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- handshake ------------------------------------------------------------------------------
    def on_syn(self, addr, p: Packet) -> None:
        old = self.conns.get(addr)
        if old is not None:
            self.drop(old, "new SYN from the same address")
        c = PrudpConnection(addr, self._sendto, pid=next(self._pids), client_stream=p.source, server_stream=p.dest)
        self.conns[addr] = c
        c.send(c.reply(type=SYN, flags=FLAG_ACK, session_id=0, signature=b"\x00" * 4, sequence_id=0,
                       connection_signature=c.server_sig))
        log.info("%s:%d SYN -> connection pid 0x%x", addr[0], addr[1], c.pid)

    def on_connect(self, addr, p: Packet) -> None:
        c = self.conns.get(addr)
        if c is None:
            c = self.conns[addr] = PrudpConnection(addr, self._sendto, pid=next(self._pids),
                                                   client_stream=p.source, server_stream=p.dest)
        if c.connect_ack is not None:                 # retransmitted CONNECT -> same answer
            self._sendto(c.connect_ack, addr)
            return
        c.client_sig = p.connection_signature
        c.expected_seq = (p.sequence_id + 1) & 0xFFFF
        ack = c.reply(type=CONNECT, flags=FLAG_ACK | FLAG_KEY_EXCHANGE, sequence_id=p.sequence_id,
                      connection_signature=b"\x00" * 4)
        if p.flags & FLAG_KEY_EXCHANGE and p.key_blob:
            blob, sig, c.key = secure.server_handshake(self.signing_key, p.key_blob)
            ack.key_blob, ack.key_signature = blob, sig
            ack.payload = secure.encrypt_payload(c.key, p.sequence_id, b"")
        else:
            log.warning("%s: CONNECT without key exchange (unexpected for Steep)", addr)
            ack.flags = FLAG_ACK
        c.connect_ack = ack.encode()
        self._sendto(c.connect_ack, addr)
        log.info("%s:%d CONNECT, secure channel up", addr[0], addr[1])

    def station_url(self, c: PrudpConnection) -> str:
        """The server's station URL in the LoginWithToken reply, as Ubisoft's: CID=1 and a DID that differs
        per connection. The game keeps DID with the connection (FUN_7ff661b572df) and drops every
        server-sent notification (ProcessNotificationEvent, e.g. matchmaking) while it is 0."""
        return (f"prudp:/address={self.public_host};port={self.port};CID=1;DID={c.did};"
                f"sid=1;stream=3;type=2")

    # ---- messages -------------------------------------------------------------------------------
    async def on_message(self, c: PrudpConnection, data: bytes) -> None:
        try:
            msg = rmc_codec.parse(data)
        except ValueError as e:
            log.warning("%s: unparseable RMC (%s): %s", c.addr, e, data[:64].hex())
            return
        if not msg.is_request:
            log.info("%s: client answered our call #%d %s", c.addr, msg.call_id, msg.method or msg.error)
            return
        ctx = NexContext(self.services, c, self.station_url(c))
        reply = await self.dispatcher.dispatch(ctx, msg)
        c.send_message(reply)

    # ---- lifecycle ------------------------------------------------------------------------------
    def drop(self, c: PrudpConnection, why: str) -> None:
        c.closed = True
        if self.conns.get(c.addr) is c:
            del self.conns[c.addr]
        s = c.session
        if s is not None:
            # a later login of the same player (e.g. Ubisoft Connect refreshing its ticket) carries this
            # connection over to its own session object: clear it there too
            gone = False
            for sess in {s, self.services.sessions.for_player(s.profile_id)} - {None}:
                if getattr(sess, "nex", None) is c:
                    sess.nex = None
                    gone = True
            if gone:
                self.services.matchmaking.on_disconnect(s.profile_id)   # the game is gone: out of its sessions
        log.info("%s:%d connection closed (%s)", c.addr[0], c.addr[1], why)

    async def run_timers(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            now = time.monotonic()
            for c in list(self.conns.values()):
                if not c.tick(now):
                    self.drop(c, "timeout")
            if now - self._mm_tick >= 1.0:                 # matchmaking: ripe tickets, retries
                self._mm_tick = now
                try:
                    self.services.matchmaking.tick()
                except Exception:  # noqa: BLE001 - must not stop the timers
                    log.exception("matchmaking tick failed")


async def start(services, dispatcher: RmcDispatcher, signing_key, bind: str, public_host: str,
                port: int) -> tuple[NexServer, asyncio.DatagramTransport]:
    loop = asyncio.get_running_loop()
    server = NexServer(services, dispatcher, signing_key, public_host, port)
    transport, _ = await loop.create_datagram_endpoint(lambda: server, local_addr=(bind, port))
    return server, transport
