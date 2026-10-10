"""NEX calls answered without recordings: per-player profiles and challenge leaderboards (the server's
players, services/leaderboards.py). Formats from the replies of the real server."""
from __future__ import annotations

import struct
import uuid

from ....services import leaderboards
from ..rmc_codec import V_STRING, Reader, Writer, read_type_map, write_variant


def clans_player_profile(ctx, r: Reader) -> bytes:
    """ClansProtocol::GetPlayerProfile_V1: the player is in no clan (32 zero bytes, as the real server)."""
    return bytes(32)


def player_status_profile(ctx, r: Reader) -> bytes:
    """PlayerStatusProtocol::GetProfile_V1: GUID of the player (profile id) + 8 zero bytes."""
    pid = uuid.UUID(ctx.profile_id) if ctx.profile_id else uuid.UUID(int=0)
    return Writer().guid(pid).u64(0).bytes()


def tournament_player_info(ctx, r: Reader) -> bytes:
    """SteepTournamentProtocol::FetchPlayerProfileInfo_V1: no tournament progress (22 zero bytes)."""
    return bytes(22)


def _board(b: leaderboards.Board) -> bytes:
    """u32 total players, u32 n rows, then n x (String profile id, u32 rank, f64 score, u32 m,
    m x (String key, Variant)); the one value recorded is associated_replay (a replay id or "none")."""
    w = Writer().u32(b.total).u32(len(b.rows))
    for row in b.rows:
        w.string(row.profile_id).u32(row.rank).raw(struct.pack("<d", row.score)).u32(1).string("associated_replay")
        write_variant(w, V_STRING, row.replay)
    return w.bytes()


def leaderboard_for_populations(ctx, r: Reader) -> bytes:
    """GetLeaderboardForPopulations_V1: type map, String board, List<String> profile ids (the player and
    friends) -> their rows, with their rank among all the server's players (services/leaderboards.py)."""
    read_type_map(r)
    name = r.string()
    pids = [r.string() for _ in range(r.u32())]
    return _board(leaderboards.for_players(ctx.services.repos, name, pids))


def leaderboard(ctx, r: Reader) -> bytes:
    """GetLeaderboard_V1: type map, String board, u32 first rank, u32 count (1, 1: the record)."""
    read_type_map(r)
    name = r.string()
    first, count = r.u32(), r.u32()
    return _board(leaderboards.ranks(ctx.services.repos, name, first, count))


HANDLERS = {
    "ClansProtocol::GetPlayerProfile_V1": clans_player_profile,
    "PlayerStatusProtocol::GetProfile_V1": player_status_profile,
    "SteepTournamentProtocol::FetchPlayerProfileInfo_V1": tournament_player_info,
    "PlayerStats2Protocol::GetLeaderboard_V1": leaderboard,
    "PlayerStats2Protocol::GetLeaderboardForPopulations_V1": leaderboard_for_populations,
}
