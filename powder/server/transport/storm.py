"""Storm: Steep's peer-to-peer helpers, NAT detection and traversal (UDP), so the games find each other.

The game's configuration names them ("storm"/"punch": detectUrl1, detectUrl2, traversalUrl; see
https/app.py with_storm). Without them the game switches its multiplayer off. Layout from recordings
of Ubisoft's servers:

  message   u8 kind (0x10 request, 0x11 the game's answer, 0x12 the server's own, 0x13 the server's answer),
            u8 type, u16 sequence (big endian; an answer repeats the request's), then items:
  peer id   16 bytes, handed out by the traversal server ("04 03 01 03", u32 le, u32 le, u32 be counter)
  key       8 bytes "02 02 00 00" + 4 random bytes, handed out with the id
  address   "02 04" + u16 port + IPv4 (big endian); "02 06 ..." (IPv6, empty) passed on as it is

The game talks from small sockets of their own, and from its game socket (the one the other games
reach). From the game socket a message carries a fixed 32-byte trailer, and answers to it start with a
fixed 27-byte header (the frame of the game's peer packets, all zero for these).

Detection (detectUrl1, detectUrl2 and each one's port + 1): 0x1001 "which address do you see?" ->
0x1301 with it. 0x1002 / 0x1003 ask for an answer from the other port / other server; Ubisoft's never
arrived (the PC was behind NAT) and the game then worked, so they stay unanswered here as well.

Traversal (traversalUrl):
  0x1040 register        -> 0x1241 peer id, key, address seen    then 0x1141 (id, key) -> 0x1340 (key, id)
  0x1043 keep-alive      -> 0x1343 id, key (+ address seen, from the game socket); every ~8 s
  0x1044 "connect me"    (id, key, target id, the requester's addresses) -> 0x1244 to the target (requester id,
                         target id, the addresses): both then punch (0x1045/0x1145 directly between the games)
The relay ("routerProvisioningUrl") was never asked for.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field

log = logging.getLogger("powder.storm")

HEADER = bytes(22) + bytes.fromhex("8000000020")                 # before answers to the game socket
TRAILER = bytes.fromhex("08071b") + bytes(22) + bytes.fromhex("80000000200000")   # after its requests
REQUEST, ANSWER, PUSH, REPLY = 0x10, 0x11, 0x12, 0x13
DETECT, DETECT_OTHER_PORT, DETECT_OTHER_SERVER = 0x01, 0x02, 0x03
REGISTER, REGISTERED, KEEPALIVE, CONNECT = 0x40, 0x41, 0x43, 0x44
CONFIRM = 0x40                                                    # 0x1340: registration confirmed
ID_LEN, KEY_LEN = 16, 8
PEER_TTL_S = 120.0                                                # forgotten without keep-alives

# ports, from the base port (11000): detect 1 and its other port, detect 2 and its other port, traversal
DETECT_PORTS = (0, 1, 2, 3)
TRAVERSAL_PORT = 5

Address = tuple[str, int]


def message(kind: int, mtype: int, seq: int, *items: bytes) -> bytes:
    return struct.pack(">BBH", kind, mtype, seq) + b"".join(items)


def address_item(addr: Address) -> bytes:
    return b"\x02\x04" + struct.pack(">H", addr[1]) + socket.inet_aton(addr[0])


def parse_address(item: bytes) -> Address | None:
    if len(item) == 8 and item[:2] == b"\x02\x04":
        return socket.inet_ntoa(item[4:8]), struct.unpack(">H", item[2:4])[0]
    return None


def unframe(data: bytes) -> tuple[bytes, bool]:
    """-> (message, from the game socket)."""
    if len(data) > len(TRAILER) and data.endswith(TRAILER):
        return data[:-len(TRAILER)], True
    return data, False


@dataclass
class Peer:
    id: bytes
    key: bytes
    addr: Address                                   # the socket it registered from (pushes go there)
    game_addr: Address | None = None                # its game socket, once seen
    seen: float = field(default_factory=time.monotonic)
    connects: dict[bytes, float] = field(default_factory=dict)  # target id -> when its request was last logged


class Storm:
    """Detection and traversal state; transport-free (send(data, addr, port) is wired by start())."""

    def __init__(self, send=None, clock=time.monotonic):
        self.send = send or (lambda data, addr, port: None)
        self.clock = clock
        self.peers: dict[bytes, Peer] = {}
        self._ids = itertools.count(1 + secrets.randbelow(1 << 30))    # unlike ids games kept over a restart

    def new_id(self) -> bytes:
        return bytes.fromhex("04030103") + struct.pack("<II", 1, 1) + struct.pack(">I", next(self._ids))

    def received(self, port: int, data: bytes, addr: Address) -> None:
        """A datagram on one of our ports (port: offset from the base port)."""
        msg, framed = unframe(data)
        if len(msg) < 4 or msg[0] not in (REQUEST, ANSWER):
            log.debug("storm: %d bytes from %s on +%d ignored", len(data), addr, port)
            return
        kind, mtype, seq = struct.unpack(">BBH", msg[:4])
        body = msg[4:]

        def reply(*parts: bytes, kind_: int = REPLY, type_: int = mtype) -> None:
            out = message(kind_, type_, seq, *parts)
            self.send(HEADER + out if framed else out, addr, port)

        if port in DETECT_PORTS:
            if kind == REQUEST and mtype == DETECT:
                reply(address_item(addr))
            return
        if port != TRAVERSAL_PORT:
            return
        self._expire()
        if kind == REQUEST and mtype == REGISTER:
            peer = next((p for p in self.peers.values() if p.addr == addr), None)
            if peer is None:
                peer = Peer(self.new_id(), b"\x02\x02\x00\x00" + secrets.token_bytes(4), addr)
                self.peers[peer.id] = peer
                log.info("storm: %s registered as peer %s", addr[0], peer.id[-4:].hex())
            peer.seen = self.clock()
            reply(peer.id, peer.key, address_item(addr), kind_=PUSH, type_=REGISTERED)   # the game acks (0x1141)
            return
        if len(body) < ID_LEN + KEY_LEN:
            return
        peer = self._peer(body[:ID_LEN], body[ID_LEN:ID_LEN + KEY_LEN], addr, framed)
        if kind == ANSWER and mtype == REGISTERED:
            reply(peer.key, peer.id, type_=CONFIRM)
        elif kind == REQUEST and mtype == KEEPALIVE:
            reply(peer.id, peer.key, *((address_item(addr),) if framed else ()))
        elif kind == REQUEST and mtype == CONNECT and len(body) >= 2 * ID_LEN + KEY_LEN:
            target_id, addresses = body[ID_LEN + KEY_LEN:2 * ID_LEN + KEY_LEN], body[2 * ID_LEN + KEY_LEN:]
            target = self.peers.get(target_id)
            if target is None:
                log.warning("storm: peer %s asks for unknown peer %s", peer.id[-4:].hex(), target_id[-4:].hex())
                return
            now = self.clock()
            if now - peer.connects.get(target_id, -60.0) >= 30:    # not every retry (about two a second)
                peer.connects[target_id] = now
                log.info("storm: peer %s (%s) connects to peer %s (%s)", peer.id[-4:].hex(), addr[0],
                         target_id[-4:].hex(), target.addr[0])
            self.send(message(PUSH, CONNECT, seq, peer.id, target_id, addresses), target.addr, TRAVERSAL_PORT)
        else:
            log.debug("storm: message %02x%02x from %s ignored", kind, mtype, addr)

    def _peer(self, peer_id: bytes, key: bytes, addr: Address, framed: bool) -> Peer:
        """The registered peer; one we don't know (e.g. the server restarted) is taken as it says."""
        peer = self.peers.get(peer_id)
        if peer is None:
            peer = self.peers[peer_id] = Peer(peer_id, key, addr)
            log.info("storm: %s continues as peer %s", addr[0], peer_id[-4:].hex())
        if framed:
            peer.game_addr = addr
        elif peer.addr != addr:
            peer.addr = addr
        peer.seen = self.clock()
        return peer

    def _expire(self) -> None:
        now = self.clock()
        for pid in [pid for pid, p in self.peers.items() if now - p.seen > PEER_TTL_S]:
            del self.peers[pid]


class _Port(asyncio.DatagramProtocol):
    def __init__(self, storm: Storm, offset: int):
        self.storm, self.offset = storm, offset
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            self.storm.received(self.offset, data, (addr[0], addr[1]))
        except Exception:                                               # noqa: BLE001 - keep serving
            log.exception("storm: datagram from %s failed", addr)

    def error_received(self, exc) -> None:                              # e.g. ICMP port unreachable
        log.debug("storm: %s", exc)


async def start(bind: str, base_port: int) -> tuple[Storm, list[asyncio.DatagramTransport]]:
    loop = asyncio.get_running_loop()
    transports: dict[int, asyncio.DatagramTransport] = {}

    def send(data: bytes, addr: Address, offset: int) -> None:
        t = transports.get(offset)
        if t is not None:
            t.sendto(data, addr)
    storm = Storm(send)
    try:
        for offset in (*DETECT_PORTS, TRAVERSAL_PORT):
            transport, _ = await loop.create_datagram_endpoint(lambda o=offset: _Port(storm, o),
                                                               local_addr=(bind, base_port + offset))
            transports[offset] = transport
    except OSError:
        for t in transports.values():
            t.close()
        raise
    return storm, list(transports.values())
