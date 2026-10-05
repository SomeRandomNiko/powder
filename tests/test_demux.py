"""Ubisoft Connect demux: wire codec, request-id handling, and a whole session over real TLS
(with made-up recorded replies)."""
import asyncio
import dataclasses
import ssl

import pytest

import synthetic
from steepserver import tls
from steepserver.fixtures.demux import DemuxFixtures
from steepserver.services import Services
from steepserver.transport.demux import pb
from steepserver.transport.demux import server as demux_server
from steepserver.transport.demux.services import FLAT, NESTED, parse_request, with_request_id

TOKEN = b"made-up-ownership-token"


def test_pb_round_trip():
    msg = pb.encode([(1, 300), (2, "text"), (3, [(1, 1), (2, b"\x00\xff")]), (4, b"\x01\x02\x03\x04", pb.FIXED32)])
    fields = pb.decode(msg)
    assert pb.first(fields, 1) == 300 and pb.text(fields, 2) == "text"
    assert pb.decode(pb.first(fields, 3)) == [(1, 1, pb.VARINT), (2, b"\x00\xff", pb.LEN)]
    assert pb.encode(fields) == msg                          # lossless, incl. fixed32
    assert pb.unframe(pb.frame(msg)) == msg
    with pytest.raises(pb.DecodeError):
        pb.decode(b"\x0a\x05ab")                             # truncated


def test_request_styles_and_id_rewrite():
    nested = pb.encode([(1, [(1, 7), (14, [(1, 3280)])])])
    req = parse_request("ownership_service", nested)
    assert (req.style, req.request_id, req.kind) == (NESTED, 7, 14)
    flat = pb.encode([(1, 5), (4, b"")])
    req = parse_request("ach_frontend", flat)
    assert (req.style, req.request_id, req.kind) == (FLAT, 5, 4)
    recorded = pb.encode([(1, [(1, 3), (13, [(1, 1), (2, TOKEN)])])])
    out = pb.decode(pb.decode(with_request_id(recorded, 42))[0][1])
    assert pb.first(out, 1) == 42 and pb.decode(pb.first(out, 13))[1][1] == TOKEN


def _fixtures() -> DemuxFixtures:
    return DemuxFixtures(
        client_version=13368,
        patch_info=pb.encode([(1, 1), (5, 13368)]),
        service_requests={"utility_service": [pb.encode([(1, 1), (2, b"geo")])]},
        services={
            "ownership_service": {14: [pb.encode([(1, [(1, 3), (13, [(1, 1), (2, TOKEN)])])])]},
            "ach_frontend": {4: [pb.encode([(1, 1), (4, b"")])]},
        })


@pytest.fixture
async def demux(config, assets, tmp_path):
    services = Services.open(config, dataclasses.replace(assets, demux=_fixtures()))
    services.accounts.player(synthetic.PLAYER)
    pki = tls.ensure_pki(tmp_path / "tls")
    server = await demux_server.start(services, "127.0.0.1", 0, pki.server_pem)
    yield services, pki, server.sockets[0].getsockname()[1]
    server.close()
    services.db.close()


class Client:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    async def send(self, fields):
        self.writer.write(pb.frame(pb.encode(fields)))
        await self.writer.drain()

    async def recv(self) -> list:
        n = int.from_bytes(await asyncio.wait_for(self.reader.readexactly(4), 3), "big")
        return pb.decode(await self.reader.readexactly(n))

    async def request(self, rid, kind, body) -> list:
        await self.send([(1, [(1, rid), (kind, body)])])
        rsp = pb.decode(pb.first(await self.recv(), 1))
        assert pb.first(rsp, 1) == rid
        return pb.decode(pb.first(rsp, kind))

    async def data(self, cid, msg: bytes) -> bytes:
        await self.send([(2, [(1, [(1, cid), (2, pb.frame(msg))])])])
        d = pb.decode(pb.first(pb.decode(pb.first(await self.recv(), 2)), 1))
        assert pb.first(d, 1) == cid
        return pb.unframe(pb.first(d, 2))


async def test_session_over_tls(demux):
    services, pki, port = demux
    ctx = ssl.create_default_context(cafile=str(pki.ca_pem))   # verifies the name like Connect does
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx,
                                                   server_hostname="dmx.upc.ubisoft.com")
    c = Client(reader, writer)
    try:
        await c.send([(2, [(4, [(1, 13368)])])])                       # client version push
        patch = await c.request(1, 5, [(1, 0)])
        assert pb.first(patch, 5) == 13368
        utility = await c.request(0, 6, [(1, "utility_service"), (2, b"")])
        assert pb.first(utility, 2) == b"geo"

        auth = await c.request(2, 2, [(1, [(3, "any-ticket")]), (2, "uplay_pc")])
        assert pb.first(auth, 1) == 1                                   # lenient: unknown ticket ok

        own = await c.request(3, 3, [(2, "ownership_service")])
        ach = await c.request(4, 3, [(2, "ach_frontend")])
        cid_own, cid_ach = pb.first(own, 1), pb.first(ach, 1)
        assert pb.first(own, 2) == 1 and cid_own != cid_ach

        # ownership token request (nested style): recorded reply, our request id
        reply = pb.decode(pb.first(pb.decode(await c.data(cid_own, pb.encode(
            [(1, [(1, 9), (14, [(1, 3280)])])]))), 1))
        assert pb.first(reply, 1) == 9
        assert pb.first(pb.decode(pb.first(reply, 13)), 2) == TOKEN

        # flat style
        reply = pb.decode(await c.data(cid_ach, pb.encode([(1, 6), (4, b"")])))
        assert pb.first(reply, 1) == 6

        await c.send([(2, [(6, [(1, 3280)])])])                        # product started: no reply
        await c.send([(2, [(3, b"")])])                                 # keep-alive: no reply
        own2 = await c.request(5, 3, [(2, "friends_service")])          # connection still healthy
        assert pb.first(own2, 2) == 1
    finally:
        writer.close()
