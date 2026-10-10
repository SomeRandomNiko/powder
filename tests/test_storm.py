"""Storm (peer-to-peer helpers): NAT detection and traversal, and the configuration that points the game at
them. Message layouts as recorded from Ubisoft's servers; addresses and ids here are made up."""
import asyncio
import dataclasses
import json
import socket
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from powder.server.constants import APP_ID
from powder.server.transport import storm
from powder.server.transport.https.app import build_app, storm_settings, with_storm

GAME_A, GAME_A_SOCKET = ("192.0.2.10", 49627), ("192.0.2.10", 49625)
GAME_B = ("192.0.2.20", 50001)


def make():
    sent = []
    s = storm.Storm(lambda data, addr, port: sent.append((data, addr, port)), clock=lambda: 0.0)
    return s, sent


def register(s, sent, addr):
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("1040000101010300"), addr)
    data, to, port = sent[-1]
    assert to == addr and port == storm.TRAVERSAL_PORT and data[:4] == bytes.fromhex("12410001")
    peer_id, key = data[4:20], data[20:28]
    assert data[28:] == storm.address_item(addr)
    return peer_id, key


def test_detection_tells_the_address_it_sees():
    s, sent = make()
    s.received(0, bytes.fromhex("10010000"), ("192.0.2.10", 54210))
    assert sent[-1] == (bytes.fromhex("13010000 0204 d3c2 c000020a".replace(" ", "")), ("192.0.2.10", 54210), 0)
    # from the game socket: the request has a trailer, the answer the peer-packet header
    s.received(3, bytes.fromhex("1001000b") + storm.TRAILER, GAME_A_SOCKET)
    assert sent[-1] == (storm.HEADER + bytes.fromhex("1301000b") + storm.address_item(GAME_A_SOCKET), GAME_A_SOCKET, 3)
    n = len(sent)
    s.received(0, bytes.fromhex("10020007"), ("192.0.2.10", 54210))      # other port / server: unanswered,
    s.received(0, bytes.fromhex("10030002"), ("192.0.2.10", 54210))      # as from Ubisoft's (game behind NAT)
    assert len(sent) == n


def test_registration_and_keepalives():
    s, sent = make()
    peer_id, key = register(s, sent, GAME_A)
    assert peer_id[:4] == bytes.fromhex("04030103") and key[:4] == bytes.fromhex("02020000")
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("11410002") + peer_id + key, GAME_A)
    assert sent[-1][0] == bytes.fromhex("13400002") + key + peer_id
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("10430003") + peer_id + key, GAME_A)
    assert sent[-1][0] == bytes.fromhex("13430003") + peer_id + key
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("10430001") + peer_id + key + storm.TRAILER, GAME_A_SOCKET)
    assert sent[-1] == (storm.HEADER + bytes.fromhex("13430001") + peer_id + key + storm.address_item(GAME_A_SOCKET),
                        GAME_A_SOCKET, storm.TRAVERSAL_PORT)
    assert s.peers[peer_id].addr == GAME_A and s.peers[peer_id].game_addr == GAME_A_SOCKET
    assert register(s, sent, GAME_A)[0] == peer_id                         # the same socket again: same peer


def test_connect_request_is_passed_to_the_target():
    s, sent = make()
    a_id, a_key = register(s, sent, GAME_A)
    b_id, _ = register(s, sent, GAME_B)
    addresses = storm.address_item(GAME_A_SOCKET) + storm.address_item(("10.0.0.5", 49625)) + \
        bytes.fromhex("0206007000000000")
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("10440005") + a_id + a_key + b_id + addresses, GAME_A)
    assert sent[-1] == (bytes.fromhex("12440005") + a_id + b_id + addresses, GAME_B, storm.TRAVERSAL_PORT)
    n = len(sent)
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("10440006") + a_id + a_key + bytes(16) + addresses, GAME_A)
    assert len(sent) == n                                                   # an unknown target: nothing


def test_a_peer_from_before_a_restart_is_taken_as_it_says():
    s, sent = make()
    old_id, old_key = bytes.fromhex("04030103") + bytes(12), bytes.fromhex("0202000011223344")
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("10430009") + old_id + old_key, GAME_B)
    assert sent[-1][0] == bytes.fromhex("13430009") + old_id + old_key and s.peers[old_id].addr == GAME_B


def test_peers_without_keepalives_are_forgotten():
    now = [0.0]
    s = storm.Storm(clock=lambda: now[0])
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("1040000101010300"), GAME_A)
    now[0] = storm.PEER_TTL_S + 1
    s.received(storm.TRAVERSAL_PORT, bytes.fromhex("1040000101010300"), GAME_B)
    assert [p.addr for p in s.peers.values()] == [GAME_B]


def test_game_socket_frames_as_recorded():
    assert storm.TRAILER.hex() == "08071b" + "00" * 22 + "80000000200000" and len(storm.TRAILER) == 32
    assert storm.HEADER.hex() == "00" * 22 + "8000000020" and len(storm.HEADER) == 27


def test_address_items():
    assert storm.parse_address(storm.address_item(("192.0.2.7", 11000))) == ("192.0.2.7", 11000)
    assert storm.parse_address(bytes.fromhex("0206007000000000")) is None


def test_configuration_names_this_server():
    settings = storm_settings(dataclasses.replace(syn_server(), public_host="192.0.2.10"))
    assert settings["detectUrl1"] == "192.0.2.10:11000" and settings["detectUrl2"] == "192.0.2.10:11002"
    assert settings["traversalUrl"] == "192.0.2.10:11005"
    recorded = {"configuration": {"storm": {"detectUrl1": "detect.example.invalid:11000"},
                                  "punch": {"traversalUrl": "traversal.example.invalid:11005"}, "other": 1}}
    cfg = json.loads(with_storm(json.dumps(recorded).encode(), settings))["configuration"]
    assert cfg["storm"] == cfg["punch"] == settings and cfg["other"] == 1
    off = json.loads(with_storm(json.dumps(recorded).encode(), None))["configuration"]
    assert off == {"storm": {}, "other": 1}
    assert with_storm(b"not json", settings) == b"not json"


def syn_server():
    from powder.server.config import ServerConfig
    return ServerConfig()


async def test_the_packs_configuration_gets_powders_storm(services, monkeypatch):
    """The pack's recorded configuration names Ubisoft's hosts; the game gets this server instead."""
    services.config = dataclasses.replace(services.config, server=dataclasses.replace(
        services.config.server, public_host="192.0.2.10"))
    recorded = {"configuration": {"storm": {"traversalUrl": "lb-traversal.ubisoft.com:11005"},
                                  "punch": {"traversalUrl": "lb-traversal.ubisoft.com:11005"}}}
    fixture = SimpleNamespace(body=json.dumps(recorded).encode(), status=200, content_type="application/json")
    monkeypatch.setattr(services.assets.http, "lookup", lambda method, host, path: (fixture, "exact"))
    client = TestClient(TestServer(build_app(services, nex_public_key="TESTKEY")))
    await client.start_server()
    try:
        r = await client.get(f"/v2/applications/{APP_ID}/configuration", headers={"Host": syn.UBI})
        cfg = (await r.json())["configuration"]
        assert cfg["storm"]["traversalUrl"] == "192.0.2.10:11005"
        assert "ubisoft.com" not in json.dumps(cfg.get("storm")) + json.dumps(cfg.get("punch"))
    finally:
        await client.close()


async def test_storm_over_udp():
    _, transports = await _start_free()
    loop = asyncio.get_running_loop()
    try:
        port = transports[0].get_extra_info("sockname")[1]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("127.0.0.1", 0))
        await loop.sock_sendto(sock, bytes.fromhex("10010000"), ("127.0.0.1", port))
        data = await asyncio.wait_for(loop.sock_recv(sock, 64), 2)
        assert data[:4] == bytes.fromhex("13010000") and storm.parse_address(data[4:]) == sock.getsockname()
        sock.close()
    finally:
        for t in transports:
            t.close()


async def _start_free():
    """storm.start on a free base port (all six ports must be free)."""
    for base in range(41000, 42000, 10):
        try:
            return await storm.start("127.0.0.1", base)
        except OSError:
            continue
    raise RuntimeError("no free ports")
