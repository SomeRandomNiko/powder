"""NEX transport: reliability details and a full session over real UDP with a simulated game client."""
import asyncio
import os
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from powder.server.core.identity import LoginRequest
from powder.server.services.stats import decode_stat_group
from powder.server.transport.nex import rmc_codec as rmc
from powder.server.transport.nex import secure
from powder.server.transport.nex import server as nex_server
from powder.server.transport.nex.connection import PrudpConnection, seq_before
from powder.server.transport.nex.dispatch import RmcDispatcher
from powder.server.transport.nex.prudp import (CONNECT, DATA, FLAG_ACK, FLAG_KEY_EXCHANGE, FLAG_NEED_ACK,
                                             FLAG_RELIABLE, SYN, Packet)


def test_seq_before_is_wrap_safe():
    assert seq_before(1, 2) and not seq_before(2, 1)
    assert seq_before(65535, 0) and seq_before(65530, 5) and not seq_before(5, 65530)


def test_in_order_delivery_across_sequence_wrap():
    sent = []
    c = PrudpConnection(("127.0.0.1", 1), lambda raw, addr: sent.append(raw), expected_seq=65534)
    pkt = lambda seq, data: Packet(type=DATA, flags=FLAG_RELIABLE | FLAG_NEED_ACK, sequence_id=seq, payload=data)
    assert c.receive_data(pkt(65534, b"a")) == [b"a"]
    assert c.receive_data(pkt(0, b"c")) == []                 # early: buffered until 65535 arrives
    assert c.receive_data(pkt(65535, b"b")) == [b"b", b"c"]
    assert c.receive_data(pkt(65535, b"b")) == []             # duplicate after wrap
    assert c.receive_data(pkt(1, b"d")) == [b"d"]
    assert len(sent) == 5                                     # all 5 NEED_ACK packets acked (dup too)


class GameClient(asyncio.DatagramProtocol):
    """Speaks PRUDP + the secure handshake like the game does."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.sess, self.my_sig, self.seq = 0xF7, os.urandom(4), 2

    def datagram_received(self, data, addr):
        self.q.put_nowait(Packet.decode(data))

    async def recv(self, want) -> Packet:
        while True:
            p = await asyncio.wait_for(self.q.get(), 3)
            if want(p):
                return p

    def send(self, p: Packet):
        self.transport.sendto(p.encode())

    def connection_made(self, transport):
        self.transport = transport

    async def connect(self, cfg_pub):
        self.send(Packet(type=SYN, flags=FLAG_NEED_ACK))
        self.srv_sig = (await self.recv(lambda p: p.type == SYN)).connection_signature
        eph = ec.generate_private_key(ec.SECP256R1())
        self.send(Packet(type=CONNECT, flags=FLAG_RELIABLE | FLAG_NEED_ACK | FLAG_KEY_EXCHANGE,
                         session_id=self.sess, signature=self.srv_sig, sequence_id=1,
                         connection_signature=self.my_sig, key_blob=secure.to_blob(eph.public_key(), secure.ECK1)))
        ack = await self.recv(lambda p: p.type == CONNECT)
        assert ack.type_flags == 0x89 and ack.signature == self.my_sig
        assert secure.verify_blob(cfg_pub, ack.key_blob, ack.key_signature), "game would reject the signature"
        self.key = secure.session_key(eph, ack.key_blob)

    def call(self, protocol, call_id, method, params=b""):
        req = rmc.build_request(protocol, call_id, method, params)
        chunks = [req[i:i + 730] for i in range(0, len(req), 730)]
        for i, chunk in enumerate(chunks):
            self.send(Packet(type=DATA, flags=FLAG_RELIABLE | FLAG_NEED_ACK, session_id=self.sess,
                             signature=self.srv_sig, sequence_id=self.seq,
                             fragment_id=0 if i == len(chunks) - 1 else i + 1,
                             payload=secure.encrypt_payload(self.key, self.seq, chunk)))
            self.seq += 1

    async def read(self) -> rmc.Message:
        parts = []
        while True:
            p = await self.recv(lambda p: p.type == DATA and not p.flags & FLAG_ACK)
            self.send(Packet(type=DATA, flags=FLAG_ACK, session_id=self.sess, signature=self.srv_sig,
                             sequence_id=p.sequence_id, fragment_id=p.fragment_id))
            parts.append(secure.decrypt_payload(self.key, p.payload, p.sequence_id))
            if p.fragment_id == 0:
                return rmc.parse(b"".join(parts))


@pytest.fixture
async def nex(services):
    signing = ec.generate_private_key(ec.SECP256R1())
    server, transport = await nex_server.start(services, RmcDispatcher(services), signing,
                                               "127.0.0.1", "127.0.0.1", 0)
    timers = asyncio.ensure_future(server.run_timers())
    port = transport.get_extra_info("sockname")[1]
    loop = asyncio.get_running_loop()
    ct, client = await loop.create_datagram_endpoint(GameClient, remote_addr=("127.0.0.1", port))
    await client.connect(secure.from_blob(__import__("base64").b64decode(secure.config_public_key(signing))))
    yield server, client
    timers.cancel()
    ct.close()
    transport.close()


async def test_full_session(services, player, nex):
    server, client = nex
    session = services.accounts.login(LoginRequest("127.0.0.1"))

    # LoginWithToken with our HTTPS ticket (3 KB-ish params -> several fragments, like the game)
    params = (rmc.Writer().u32(1).string("ClientVersionInfo").u16(1).string(session.ticket)
              .u16(1).u16(0).u16(0).u32(0).string("x" * 2500).bytes())
    client.call("LoginProtocol", 5, "LoginProtocol::LoginWithToken_V1", params)
    resp = await client.read()
    assert resp.success and resp.method == "LoginProtocol::LoginWithToken_V1*"
    out = rmc.Reader(resp.body)
    assert out.guid() == uuid.UUID(player)
    assert out.string().startswith("prudp:/address=127.0.0.1;") and out.bool() is False
    assert session.nex is not None                           # connection registered for server push

    # player statistics come from the database: level 41
    client.call("PlayerStats2Protocol", 6, "PlayerStats2Protocol::GetStatisticGroup_V1",
                rmc.Writer().u32(1).string("StatisticsResult").u16(1).string("player_profile").bytes())
    _, rows = decode_stat_group((await client.read()).body)
    assert next(r for r in rows if r.key == "level_stat:6:infinite").value[0] == 41

    # a matchmaking call (empty reply)
    client.call("MatchmakingProtocol", 7, "MatchmakingProtocol::LeaveSession_V1", rmc.Writer().u32(0).u32(1).bytes())
    assert (await client.read()).success

    # unknown call -> RMC error, connection survives
    client.call("FooProtocol", 8, "FooProtocol::Bar_V1")
    err = await client.read()
    assert not err.success and err.error == ("Core", 2) and err.call_id == 8

    # server push: a request initiated by the server reaches the client
    assert services.sessions.push_nex(player, "NotificationProtocol",
                                      "NotificationProtocol::ProcessNotificationEvent_V1", b"\x00", call_id=0)
    pushed = await client.read()
    assert pushed.is_request and pushed.call_id == 0 and pushed.method.endswith("ProcessNotificationEvent_V1")

    await asyncio.sleep(0.3)
    conn = next(iter(server.conns.values()))
    assert not conn.unacked, "every server DATA packet must have been acknowledged"


async def test_unknown_ticket_is_attributed_in_lenient_mode(services, player, nex):
    _, client = nex
    params = rmc.Writer().u32(0).string("emu.not-a-ticket-we-issued").bytes()
    client.call("LoginProtocol", 5, "LoginProtocol::LoginWithToken_V1", params)
    out = rmc.Reader((await client.read()).body)
    assert out.guid() == uuid.UUID(player)


async def test_uploaded_progress_shows_in_the_next_stat_read(services, player, nex):
    from test_progression import encode_events
    from powder.server.transport.nex.rmc_codec import V_UINT64
    _, client = nex
    session = services.accounts.login(LoginRequest("127.0.0.1"))
    client.call("LoginProtocol", 5, "LoginProtocol::LoginWithToken_V1",
                rmc.Writer().u32(0).string(session.ticket).bytes())
    await client.read()
    client.call("PlayerStats2Protocol", 6, "PlayerStats2Protocol::PostEvents_V1", encode_events(
        [("update_level", {"expertise_id": (V_UINT64, 6), "level_expertise": (V_UINT64, 50)})]))
    assert (await client.read()).body == b""
    client.call("PlayerStats2Protocol", 7, "PlayerStats2Protocol::GetStatisticGroup_V1",
                rmc.Writer().u32(1).string("StatisticsResult").u16(1).string("player_profile").bytes())
    _, rows = decode_stat_group((await client.read()).body)
    assert next(r for r in rows if r.key == "level_stat:6:infinite").value[0] == 50
