"""Challenge leaderboards from the stored bests (services/leaderboards.py). Made-up players and scores."""
import datetime
import struct
from types import SimpleNamespace

from powder.server.services import leaderboards
from powder.server.transport.nex import rmc_codec as rmc
from powder.server.transport.nex.handlers.profile import leaderboard, leaderboard_for_populations

A, B, C = (f"{d * 8}-{d * 4}-{d * 4}-{d * 4}-{d * 12}" for d in "123")
NOW = datetime.datetime(2026, 10, 10, 12, tzinfo=datetime.UTC)


def repos(*scores):
    rows = [{"profile_id": p, "challenge_id": c, "direction": "x", "best_score": s, "replay": "none",
             "updated": when} for p, c, s, when in scores]
    return SimpleNamespace(challenge_scores=SimpleNamespace(
        for_challenge=lambda cid: [r for r in rows if r["challenge_id"] == cid]))


def test_ranks_by_direction_with_ties():
    r = repos((A, "Steep_A03", 1708.0, "2026-10-10T10:00:00Z"), (B, "Steep_A03", 2500.0, "2026-10-01T10:00:00Z"),
              (C, "Steep_A03", 1708.0, "2026-10-10T11:00:00Z"))
    points = leaderboards.board(r, "public_challenge_descending_ldb:Steep_A03:infinite", NOW)
    assert points.total == 3 and [(x.profile_id, x.rank) for x in points.rows] == [(B, 1), (A, 2), (C, 2)]
    times = leaderboards.board(r, "public_challenge_ascending_ranked_ldb:Steep_A03:infinite", NOW)
    assert times.rows[0].profile_id in (A, C) and times.rows[-1] == leaderboards.Row(B, 3, 2500.0, "none")
    week = leaderboards.board(r, "public_challenge_descending_ldb:Steep_A03:weekly", NOW)
    assert [x.profile_id for x in week.rows] == [A, C]                 # B's best is from last week
    assert leaderboards.board(r, "something_else", NOW).total == 0
    made = leaderboards.board(r, "challenge_descending_ldb:Steep_A03:infinite", NOW)    # a player-made one's
    assert made.total == 3
    assert leaderboards.board(r, "tournament_challenge_descending_ldb:Steep_A03:infinite", NOW).total == 0


def test_population_request_and_reply_as_recorded():
    r = repos((A, "Steep_A03", 1708.0, "2026-10-10T10:00:00Z"), (B, "Steep_A03", 2500.0, "2026-10-10T10:00:00Z"))
    params = (rmc.Writer().u32(2).string("Leaderboard").u16(1).string("LeaderboardRow").u16(1)
              .string("public_challenge_descending_ldb:Steep_A03:infinite").u32(2).string(A).string(C).bytes())
    out = rmc.Reader(leaderboard_for_populations(SimpleNamespace(services=SimpleNamespace(repos=r)), rmc.Reader(params)))
    assert (out.u32(), out.u32()) == (2, 1)                            # 2 on the board, A's row asked (C has none)
    assert out.string() == A and out.u32() == 2 and struct.unpack("<d", out.take(8))[0] == 1708.0
    assert out.u32() == 1 and out.string() == "associated_replay" and out.u8() == rmc.V_STRING
    assert out.string() == "none" and out.remaining == 0


def test_record_holder_request():
    r = repos((A, "Steep_A09", 61.5, "2026-10-10T10:00:00Z"), (B, "Steep_A09", 58.25, "2026-10-10T10:00:00Z"))
    params = (rmc.Writer().u32(2).string("Leaderboard").u16(1).string("LeaderboardRow").u16(1)
              .string("public_challenge_ascending_ranked_ldb:Steep_A09:infinite").u32(1).u32(1).bytes())
    out = rmc.Reader(leaderboard(SimpleNamespace(services=SimpleNamespace(repos=r)), rmc.Reader(params)))
    assert (out.u32(), out.u32(), out.string(), out.u32()) == (2, 1, B, 1)
