"""Ubisoft Connect demux: wire codec, request-id handling, and a whole session over real TLS
(with made-up recorded replies)."""
import asyncio
import dataclasses
import json
import ssl

import pytest
from aiohttp import web

import synthetic
from steepserver import tls
from steepserver.fixtures.demux import DemuxFixtures
from steepserver.services import Services
from steepserver.transport import tls_router
from steepserver.transport.demux import pb
from steepserver.transport.demux import server as demux_server
from steepserver.transport.https.app import build_app
from steepserver.transport.https.keys import CA_PEM
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


def _product(pid: int, cloud: str) -> list:
    return [(1, pid), (17, f"version: 2.0\r\nroot:\r\n  name: l1\r\n  cloud_saves: {cloud}\r\n")]


def _owned(steep_cloud: str) -> bytes:
    return pb.encode([(1, [(1, 1), (2, [(1, 1), (2, [(1, _product(3280, steep_cloud)), (1, _product(9999, "no")),
                                                     (1, [(1, 4242)])]), (3, b"x")])])])


def test_owned_games_without_cloud_saves():
    from steepserver.transport.demux.ownership import without_cloud_saves
    out, changed = without_cloud_saves(_owned("yes"))
    assert changed == 1
    assert out == _owned("no")                   # only the flag changed, lengths re-encoded


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
    """The real listener: one TLS port, demux and HTTPS routed by hostname."""
    services = Services.open(config, dataclasses.replace(assets, demux=_fixtures()))
    services.accounts.player(synthetic.PLAYER)
    pki = tls.ensure_pki(tmp_path / "tls")
    app = build_app(services, nex_public_key="TESTKEY")
    app[CA_PEM] = pki.ca_pem
    runner = web.AppRunner(app)
    await runner.setup()
    server = await tls_router.start(runner, demux_server.handler(services), "127.0.0.1", 0, pki.server_pem)
    yield services, pki, server.sockets[0].getsockname()[1]
    server.close()
    await runner.cleanup()
    services.db.close()


async def _https_get(pki, port: int, host: str, path: str) -> tuple[int, bytes]:
    ctx = ssl.create_default_context(cafile=str(pki.ca_pem))
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname=host)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
    raw = await asyncio.wait_for(reader.read(), 3)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), body


async def test_https_and_demux_share_one_port(demux):
    services, pki, port = demux
    status, body = await _https_get(pki, port, "public-ubiservices.ubi.com", "/_steep/status")
    assert status == 200 and json.loads(body)["connect"] == []
    status, body = await _https_get(pki, port, "public-ubiservices.ubi.com", "/_steep/ca.pem")
    assert status == 200 and body == pki.ca_pem.read_bytes()

    ctx = ssl.create_default_context(cafile=str(pki.ca_pem))
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname="dmx.upc.ubisoft.com")
    c = Client(reader, writer)
    try:
        await c.request(2, 2, [(1, [(3, "any-ticket")]), (2, "uplay_pc")])     # demux login on the same port
        status, body = await _https_get(pki, port, "public-ubiservices.ubi.com", "/_steep/status")
        connect = json.loads(body)["connect"]
        assert [x["player"] for x in connect] == [synthetic.PLAYER_NAME]
        assert connect[0]["peer"].startswith("127.0.0.1:")                    # peer address kept
    finally:
        writer.close()


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
