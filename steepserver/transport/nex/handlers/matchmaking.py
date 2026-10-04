"""MatchmakingProtocol calls that hand out live ids (the rest are answered from recordings)."""
from __future__ import annotations

from ..rmc_codec import Reader, Writer


def reserve_session_id(ctx, r: Reader) -> bytes:
    """outputs (reader FUN_7ff661945900): u32 session id. The game retries every 5 s until it gets one."""
    return Writer().u32(ctx.services.matchmaking.reserve_session_id(ctx.profile_id or "")).bytes()


def create_session_with_reserved_id(ctx, r: Reader) -> bytes:
    """outputs: u32 = the reserved session id, echoed back (as the real server does)."""
    return Writer().u32(ctx.services.matchmaking.create_session_with_reserved_id(ctx.profile_id or "")).bytes()


def create_match_ticket(ctx, r: Reader) -> bytes:
    """outputs: u64 ticket id (fresh, increasing)."""
    return Writer().u64(ctx.services.matchmaking.create_match_ticket(ctx.profile_id or "")).bytes()


HANDLERS = {
    "MatchmakingProtocol::ReserveSessionId_V1": reserve_session_id,
    "MatchmakingProtocol::CreateSessionWithReservedId_V1": create_session_with_reserved_id,
    "MatchmakingProtocol::CreateMatchTicket_V1": create_match_ticket,
}
