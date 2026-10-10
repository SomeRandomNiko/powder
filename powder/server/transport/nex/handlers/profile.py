"""NEX calls answered without recordings: per-player profiles and leaderboards (local players only, for
now empty). Formats from the replies of the real server."""
from __future__ import annotations

import uuid

from ..rmc_codec import Reader, Writer


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


def leaderboard(ctx, r: Reader) -> bytes:
    """GetLeaderboard_V1 / GetLeaderboardForPopulations_V1: u32 total players, u32 n rows, then n x
    (String profile id, u32 rank, f64 score, u32 m, m x (String key, Variant)). Other players' scores
    are not available offline: an empty board."""
    return Writer().u32(0).u32(0).bytes()


HANDLERS = {
    "ClansProtocol::GetPlayerProfile_V1": clans_player_profile,
    "PlayerStatusProtocol::GetProfile_V1": player_status_profile,
    "SteepTournamentProtocol::FetchPlayerProfileInfo_V1": tournament_player_info,
    "PlayerStats2Protocol::GetLeaderboard_V1": leaderboard,
    "PlayerStats2Protocol::GetLeaderboardForPopulations_V1": leaderboard,
}
