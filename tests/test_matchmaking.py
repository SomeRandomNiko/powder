"""Matchmaking: two games end up in one session (made-up players, documentation addresses)."""
import asyncio
import base64
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from powder.server.core.identity import Player
from powder.server.services.matchmaking import (CODE_BECOME_HOST, CODE_HOST, CODE_JOIN, CODE_MIGRATE_DONE,
                                                JOINED, PENDING, MatchmakingService, reachable_urls)
from powder.server.transport.nex import rmc_codec as rmc
from powder.server.transport.nex import secure
from powder.server.transport.nex import server as nex_server
from powder.server.transport.nex.dispatch import RmcDispatcher
from test_nex import GameClient

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"
WORLD = 123456789
CRIT = {"connectivity_nat": 2, "country": 1, "language": 2, "request": 1, "version": 3, "world_id": WORLD,
        "worldcell": 18}


def urls(host: str, port: int = 3074) -> list[str]:
    tail = "#Nat=2;PunchGUID=v(1)key(1.0)u(1)s(1)pt(3);peerguidhigh=1;peerguidlow=2;sessionguid=0;versionguid=4"
    return [f"prudp:/address={host};port={port};type=2{tail}", f"prudp:/address={host};port={port};type=1{tail}"]


def make_mm(min_ticket_age=0.0):
    sent, now = [], [1000.0]
    service = MatchmakingService(lambda pid, code, params: sent.append((pid, code, params)),
                                 clock=lambda: now[0], min_ticket_age=min_ticket_age, resend_after=15.0)
    service.sent, service.now = sent, now
    return service


@pytest.fixture
def mm():
    return make_mm()


def ticket(mm, pid, world=WORLD, excluded=()):
    own = mm.create_session_with_reserved_id(pid, mm.reserve_session_id(pid), 1, {"version": 3}, urls("192.0.2.1"))
    return mm.create_match_ticket(pid, own, 2, {**CRIT, "world_id": world}, excluded, 32)


def test_a_lone_player_waits(mm):
    ticket(mm, A)
    assert mm.sent == []


def test_two_players_meet(mm):
    ta = ticket(mm, A)
    tb = ticket(mm, B)
    # the older ticket's player hosts
    (pid, code, params), = mm.sent
    assert (pid, code) == (A, CODE_HOST) and params["ticket_id"] == ta
    sid = params["session_id"]
    assert json.loads(params["ticket_creation_dates"])[0].count("T") == 1
    s = mm.get_session(sid)
    assert s.host is None and {m.state for m in s.members.values()} == {PENDING} and s.free == 30
    assert s.attrs["world_id"] == WORLD and s.attrs["reputation"] == 1
    # the host reports where it can be reached; then the other player is told to join
    mm.report_host_success(A, sid, urls("192.0.2.10") + urls("127.0.0.1"))
    pid, code, params = mm.sent[-1]
    assert (pid, code) == (B, CODE_JOIN) and params["host_profile_id"] == A and params["session_id"] == sid
    assert json.loads(params["ticket_ids"]) == {A: ta, B: tb}
    assert json.loads(params["host_station_urls"]) == urls("192.0.2.10")          # loopback dropped
    mm.report_join_success(B, sid)
    assert s.members[A].state == s.members[B].state == JOINED
    assert not mm.tickets                                                         # used up by the match


def test_a_third_player_joins_the_running_session(mm):
    ticket(mm, A), ticket(mm, B)
    sid = mm.sent[0][2]["session_id"]
    mm.report_host_success(A, sid, urls("192.0.2.10"))
    ticket(mm, C)
    pid, code, params = mm.sent[-1]
    assert (pid, code, params["session_id"]) == (C, CODE_JOIN, sid)


def test_other_worlds_and_excluded_sessions(mm):
    ticket(mm, A), ticket(mm, B, world=WORLD + 1)
    assert mm.sent == []                                    # different worlds never meet
    mm.on_disconnect(B)
    ticket(mm, B)
    sid = mm.sent[-1][2]["session_id"]
    mm.report_host_success(A, sid, urls("192.0.2.10"))
    mm.report_connection_failure(B, sid, 0x0A)
    assert B not in mm.get_session(sid).members
    n = len(mm.sent)
    ticket(mm, B, excluded=[sid])                           # the game avoids the session it failed with
    assert len(mm.sent) == n


def test_host_leaves_and_another_member_hosts(mm):
    ticket(mm, A), ticket(mm, B)
    sid = mm.sent[0][2]["session_id"]
    mm.report_host_success(A, sid, urls("192.0.2.10"))
    mm.report_join_success(B, sid)
    mm.on_disconnect(A)
    assert mm.sent[-1] == (B, CODE_BECOME_HOST, {"host_profile_id": B, "session_id": sid, "session_type": 2})
    mm.migrate_host(B, sid, urls("192.0.2.20"))
    assert mm.sent[-1] == (B, CODE_MIGRATE_DONE, {"session_id": sid, "host_profile_id": B})
    assert mm.get_session(sid).host_urls == urls("192.0.2.20")
    mm.leave(B, sid)
    assert mm.get_session(sid) is None


def test_only_players_in_the_same_cell_meet(mm):
    """A game leaves a session of another world cell right away (live): match per world and cell."""
    own = mm.create_session_with_reserved_id(A, mm.reserve_session_id(A), 1, {}, urls("192.0.2.1"))
    mm.create_match_ticket(A, own, 2, {**CRIT, "worldcell": 0}, (), 32)
    own = mm.create_session_with_reserved_id(B, mm.reserve_session_id(B), 1, {}, urls("192.0.2.2"))
    tb = mm.create_match_ticket(B, own, 2, {**CRIT, "worldcell": 1001}, (), 32)
    assert mm.sent == []
    mm.update_ticket(B, tb, {"worldcell": 0})               # B rides into A's cell
    (pid, code, params), = mm.sent
    assert (pid, code) == (A, CODE_HOST)
    assert mm.get_session(params["session_id"]).attrs["worldcell"] == 0


def test_tickets_ripen_before_they_are_matched():
    """A game drops a match proposal that arrives before it is searching: tickets are matched once they are
    old enough (Ubisoft's server took 110 s and more)."""
    mm = make_mm(min_ticket_age=20.0)
    ticket(mm, A)
    mm.now[0] += 8
    ticket(mm, B)
    mm.tick()
    assert mm.sent == []                                    # A's ticket is 8 s old, B's brand new
    mm.now[0] += 12
    mm.tick()
    assert mm.sent == []                                    # A: 20 s, B: 12 s
    mm.now[0] += 8
    mm.tick()
    (pid, code, _), = mm.sent
    assert (pid, code) == (A, CODE_HOST)


def test_code_1_is_sent_again_and_then_another_player_hosts(mm):
    ticket(mm, A), ticket(mm, B)
    sid = mm.sent[0][2]["session_id"]
    for _ in range(2):                                      # A's game does not react: asked again
        mm.now[0] += 15
        mm.tick()
    assert [(p, c) for p, c, _ in mm.sent] == [(A, CODE_HOST)] * 3
    mm.now[0] += 15
    mm.tick()                                               # 3 tries: B is asked instead
    assert mm.sent[-1][:2] == (B, CODE_HOST) and mm.sent[-1][2]["session_id"] == sid
    mm.report_host_success(B, sid, urls("192.0.2.20"))
    assert mm.sent[-1][:2] == (A, CODE_JOIN) and mm.sent[-1][2]["host_profile_id"] == B


def test_code_2_is_sent_again_until_the_joiner_reports(mm):
    ticket(mm, A), ticket(mm, B)
    sid = mm.sent[0][2]["session_id"]
    mm.report_host_success(A, sid, urls("192.0.2.10"))
    mm.now[0] += 15
    mm.tick()
    assert [(p, c) for p, c, _ in mm.sent] == [(A, CODE_HOST), (B, CODE_JOIN), (B, CODE_JOIN)]
    mm.report_join_success(B, sid)
    mm.now[0] += 60
    mm.tick()
    assert len(mm.sent) == 3                                # joined: nothing more


def test_reachable_urls():
    assert reachable_urls(urls("127.0.0.1") + urls("192.0.2.5")) == urls("192.0.2.5")
    assert reachable_urls(urls("127.0.0.1")) == urls("127.0.0.1")


def test_notification_event_layout():
    raw = rmc.notification_event("Matchmaking", 1, {"session_id": 5})
    r = rmc.Reader(raw)
    assert rmc.read_type_map(r) == {"NotificationEvent": 1}
    assert r.take(14) == bytes(14) and r.u16() == 2
    assert r.string() == '{"code": 1, "params": {"session_id": 5}, "facility": "Matchmaking"}'
    assert r.remaining == 0


# ---- over NEX: two simulated games ---------------------------------------------------------------
@pytest.fixture
async def two_games(services):
    services.matchmaking.min_ticket_age = 0.0               # match right away (ripening: tested above)
    signing = ec.generate_private_key(ec.SECP256R1())
    server, transport = await nex_server.start(services, RmcDispatcher(services), signing,
                                               "127.0.0.1", "127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    loop = asyncio.get_running_loop()
    pub = secure.from_blob(base64.b64decode(secure.config_public_key(signing)))
    games = []
    for pid, name in ((A, "Alice"), (B, "Bob")):
        services.seed(pid, name=name)
        session = services.sessions.issue(Player(pid, pid, name), "127.0.0.1")
        ct, client = await loop.create_datagram_endpoint(GameClient, remote_addr=("127.0.0.1", port))
        await client.connect(pub)
        client.call("LoginProtocol", 1, "LoginProtocol::LoginWithToken_V1",
                    rmc.Writer().u32(0).string(session.ticket).bytes())
        await client.read()
        client.ct, client.server = ct, server
        games.append(client)
    yield games
    for g in games:
        g.ct.close()
    transport.close()


def ticket_params(own: int) -> bytes:
    w = rmc.Writer().u32(1).string("SearchCriteria").u16(1).u32(own).u32(2).u32(len(CRIT))
    for k, v in CRIT.items():
        w.string(k)
        rmc.write_variant(w, rmc.V_UINT64 if k == "world_id" else rmc.V_INT64, v)
    return w.u32(0).u32(32).bytes()


async def open_ticket(game, call_id: int) -> int:
    game.call("MatchmakingProtocol", call_id, "MatchmakingProtocol::ReserveSessionId_V1", rmc.Writer().u32(0).bytes())
    own = rmc.Reader((await game.read()).body).u32()
    w = rmc.Writer().u32(1).string("SessionMember").u16(1).u32(own).u32(1).u32(0)
    w.u32(2).string(urls("192.0.2.1")[0]).string(urls("192.0.2.1")[1]).u32(0).u32(4)
    game.call("MatchmakingProtocol", call_id + 1, "MatchmakingProtocol::CreateSessionWithReservedId_V1", w.bytes())
    assert rmc.Reader((await game.read()).body).u32() == own
    game.call("MatchmakingProtocol", call_id + 2, "MatchmakingProtocol::CreateMatchTicket_V1", ticket_params(own))
    return rmc.Reader((await game.read()).body).u64()


def notification(msg: rmc.Message) -> dict:
    assert msg.is_request and msg.call_id == 0 and msg.method == rmc.NOTIFICATION_METHOD
    r = rmc.Reader(msg.body)
    rmc.read_type_map(r), r.take(14), r.u16()
    return json.loads(r.string())


async def test_two_games_meet_over_nex(services, two_games):
    alice, bob = two_games
    await open_ticket(alice, 10)
    await open_ticket(bob, 10)
    host = notification(await alice.read())                 # Alice's ticket is older: she hosts
    assert host["code"] == CODE_HOST and host["facility"] == "Matchmaking"
    sid = host["params"]["session_id"]

    alice.call("MatchmakingProtocol", 20, "MatchmakingProtocol::GetSession_V1",
               rmc.Writer().u32(0).u32(sid).bytes())
    r = rmc.Reader((await alice.read()).body)
    assert r.u32() == sid and r.u32() == 2
    r.u64()
    attrs = rmc.read_variant_map(r)
    assert attrs["world_id"] == WORLD and attrs["reputation"] == 1
    assert r.guid() == uuid.UUID(int=0) and r.u32() == 0 and rmc.read_strings(r) == []
    members = [(str(r.guid()), r.u32()) for _ in range(r.u32())]
    assert members == [(A, PENDING), (B, PENDING)]
    assert (r.u32(), r.u32(), r.bool()) == (32, 30, False)

    w = rmc.Writer().u32(0).u32(sid).u32(2).string(urls("192.0.2.10")[0]).string(urls("192.0.2.10")[1]).u32(0)
    alice.call("MatchmakingProtocol", 21, "MatchmakingProtocol::ReportHostSuccess_V1", w.bytes())
    assert (await alice.read()).success
    join = notification(await bob.read())
    assert join["code"] == CODE_JOIN and join["params"]["host_profile_id"] == A
    assert json.loads(join["params"]["host_station_urls"]) == urls("192.0.2.10")

    bob.call("MatchmakingProtocol", 20, "MatchmakingProtocol::ReportJoinSuccess_V1", rmc.Writer().u32(0).u32(sid).bytes())
    assert (await bob.read()).success
    bob.call("MatchmakingProtocol", 21, "MatchmakingProtocol::GetSession_V1", rmc.Writer().u32(0).u32(sid).bytes())
    r = rmc.Reader((await bob.read()).body)
    r.u32(), r.u32(), r.u64(), rmc.read_variant_map(r)
    assert str(r.guid()) == A and r.u32() == JOINED and rmc.read_strings(r) == urls("192.0.2.10")


async def test_a_closed_game_connection_leaves_matchmaking(services, two_games):
    """Also when a later login (Ubisoft Connect refreshing) carried the connection over to a newer session."""
    alice, _ = two_games
    await open_ticket(alice, 10)
    assert any(t.profile_id == A for t in services.matchmaking.tickets.values())
    services.sessions.issue(Player(A, A, "Alice"), "127.0.0.1", app_id="connect-part")    # a re-login
    conn = services.sessions.for_player(A).nex
    assert conn is not None
    alice.server.drop(conn, "test")
    assert services.sessions.for_player(A).nex is None
    assert not any(t.profile_id == A for t in services.matchmaking.tickets.values())


def test_register_from_the_servers_own_pc_gives_its_lan_address(services):
    """The host's own game connects from 127.0.0.1; other PCs must get an address they can reach."""
    import dataclasses
    from types import SimpleNamespace
    from powder.server.transport.nex.handlers.login import register
    services.config = dataclasses.replace(services.config, server=dataclasses.replace(
        services.config.server, public_host="192.0.2.10"))
    params = rmc.Writer().u32(0).u32(1).string("prudp:/address=192.0.2.10;port=9103;sid=15").bytes()
    for peer, expected in ((("127.0.0.1", 9103), "192.0.2.10"), (("192.0.2.20", 9103), "192.0.2.20")):
        ctx = SimpleNamespace(services=services, conn=SimpleNamespace(addr=peer, pid=7))
        r = rmc.Reader(register(ctx, rmc.Reader(params)))
        assert r.u32() == 7 and r.string() == f"prudp:/address={expected};port=9103;sid=15"


def test_storm_off_removes_the_recorded_p2p_servers():
    from powder.server.transport.https.app import without_storm
    recorded = {"configuration": {"storm": {"detectUrl1": "detect.example.invalid:11000"},
                                  "punch": {"traversalUrl": "traversal.example.invalid:11005"}, "other": 1}}
    assert json.loads(without_storm(json.dumps(recorded).encode())) == {"configuration": {"storm": {}, "other": 1}}
    assert without_storm(b'{"recorded": true}') == b'{"recorded": true}'
