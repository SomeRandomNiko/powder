"""MatchmakingProtocol: the game's side of matchmaking (logic in services/matchmaking.py).

Params start with a type map; layouts from recordings of Ubisoft's server (types: u32 session id,
Map<String,Variant>, List<String> station URLs, SessionMember = GUID profile + u32 state).
"""
from __future__ import annotations

import datetime
import uuid

from ....services.matchmaking import SESSION_WORLD
from ..errors import Fallthrough, RmcError
from ..rmc_codec import (V_INT64, Reader, Writer, quazal_datetime, read_strings, read_type_map,
                         read_variant_map, write_variant)


def _mm(ctx):
    return ctx.services.matchmaking


def _pid(ctx) -> str:
    """The calling player; a call before LoginWithToken is not ours to answer."""
    if not ctx.profile_id:
        raise Fallthrough()
    return ctx.profile_id


def reserve_session_id(ctx, r: Reader) -> bytes:
    """outputs (reader FUN_7ff661945900): u32 session id. The game retries every 5 s until it gets one."""
    return Writer().u32(_mm(ctx).reserve_session_id(_pid(ctx))).bytes()


def create_session_with_reserved_id(ctx, r: Reader) -> bytes:
    """params: type map, u32 reserved id, u32 session type (1: the player's group), Map attributes,
    List<String> station URLs, List<SessionMember>, u32 max size. outputs: u32 = the id, echoed back."""
    read_type_map(r)
    try:
        sid, stype, attrs, urls = r.u32(), r.u32(), read_variant_map(r), read_strings(r)
        for _ in range(r.u32()):
            r.guid(), r.u32()
        max_size = r.u32()
    except ValueError:                                      # short params: just the id
        return Writer().u32(_mm(ctx).create_session_with_reserved_id(_pid(ctx))).bytes()
    return Writer().u32(_mm(ctx).create_session_with_reserved_id(
        _pid(ctx), sid, stype, attrs, urls, max_size)).bytes()


def create_match_ticket(ctx, r: Reader) -> bytes:
    """params: type map {SearchCriteria}, u32 own session id, u32 session type (2: the shared world),
    Map criteria (connectivity_nat, country, language, request, version, world_id, worldcell), List<u32>
    excluded session ids, u32 max size. outputs: u64 ticket id."""
    read_type_map(r)
    try:
        own, stype, criteria = r.u32(), r.u32(), read_variant_map(r)
        excluded = [r.u32() for _ in range(r.u32())]
        max_size = r.u32()
    except ValueError:
        return Writer().u64(_mm(ctx).create_match_ticket(_pid(ctx))).bytes()
    return Writer().u64(_mm(ctx).create_match_ticket(_pid(ctx), own, stype, criteria, excluded, max_size)).bytes()


def update_ticket(ctx, r: Reader) -> bytes:
    """params: type map, u32 ticket id, Map criteria."""
    read_type_map(r)
    _mm(ctx).update_ticket(_pid(ctx), r.u32(), read_variant_map(r))
    return b""


def cancel_all_ticket(ctx, r: Reader) -> bytes:
    _mm(ctx).cancel_all_tickets(_pid(ctx))
    return b""


def _sid(r: Reader) -> int:
    read_type_map(r)
    return r.u32()


def get_session(ctx, r: Reader) -> bytes:
    """params: type map {MatchmakingSession, SessionMember}, u32 session id.
    outputs: u32 id, u32 type, DateTime created, Map attributes (int64), GUID host (zero until it reported
    its URLs), u32 host state, List<String> host URLs, List<SessionMember>, u32 max size, u32 free slots,
    bool (false for world sessions)."""
    s = _mm(ctx).get_session(_sid(r))
    if s is None:
        raise Fallthrough()                         # not ours (e.g. from before a restart): the recorded reply
    w = Writer().u32(s.id).u32(s.type).u64(quazal_datetime(s.created.astimezone(datetime.UTC)))
    w.u32(len(s.attrs))
    for k, v in s.attrs.items():
        w.string(k)
        write_variant(w, V_INT64, int(v))
    host = s.members.get(s.host) if s.host else None
    w.guid(uuid.UUID(s.host) if host else uuid.UUID(int=0)).u32(host.state if host else 0)
    urls = s.host_urls if host else []
    w.u32(len(urls))
    for u in urls:
        w.string(u)
    w.u32(len(s.members))
    for m in s.members.values():
        w.guid(uuid.UUID(m.profile_id)).u32(m.state)
    return w.u32(s.max_size).u32(s.free).bool(s.type != SESSION_WORLD).bytes()


def join_session(ctx, r: Reader) -> bytes:
    _mm(ctx).join_session(_pid(ctx), _sid(r))
    return b""


def leave_session(ctx, r: Reader) -> bytes:
    _mm(ctx).leave(_pid(ctx), _sid(r))
    return b""


def report_host_success(ctx, r: Reader) -> bytes:
    """params: type map, u32 session id, List<String> station URLs, u32 (0)."""
    sid = _sid(r)
    _mm(ctx).report_host_success(_pid(ctx), sid, read_strings(r))
    return b""


def report_join_success(ctx, r: Reader) -> bytes:
    _mm(ctx).report_join_success(_pid(ctx), _sid(r))
    return b""


def report_connection_failure(ctx, r: Reader) -> bytes:
    """params: type map, u32 session id, u32 reason (0x0a and 0x1a seen), 3 more bytes."""
    sid = _sid(r)
    reason = r.u32() if r.remaining >= 4 else None
    _mm(ctx).report_connection_failure(_pid(ctx), sid, reason)
    return b""


def migrate_host(ctx, r: Reader) -> bytes:
    """params: type map, u32 session id, List<String> station URLs (answer to code 12)."""
    sid = _sid(r)
    _mm(ctx).migrate_host(_pid(ctx), sid, read_strings(r))
    return b""


def remove_members(ctx, r: Reader) -> bytes:
    """params: type map, u32 session id, List<GUID> players (the host removes them)."""
    sid = _sid(r)
    _mm(ctx).remove_members(_pid(ctx), sid, [str(r.guid()) for _ in range(r.u32())])
    return b""


def join_player(ctx, r: Reader) -> bytes:
    """params: type map, GUID the friend ("Join group" in the social tab). No outputs (the game's reply
    dispatcher reads none); the game is then told where to go with code 4 (services/matchmaking.py)."""
    read_type_map(r)
    if not _mm(ctx).join_player(_pid(ctx), str(r.guid())):
        raise RmcError("Core", 2)                       # the game shows "join has failed"
    return b""


def update_session(ctx, r: Reader) -> bytes:
    """params: type map, u32 session id, Map attributes."""
    sid = _sid(r)
    _mm(ctx).update_session(_pid(ctx), sid, read_variant_map(r))
    return b""


HANDLERS = {f"MatchmakingProtocol::{name}_V1": fn for name, fn in {
    "ReserveSessionId": reserve_session_id,
    "CreateSessionWithReservedId": create_session_with_reserved_id,
    "CreateMatchTicket": create_match_ticket,
    "UpdateTicket": update_ticket,
    "CancelAllTicket": cancel_all_ticket,
    "GetSession": get_session,
    "JoinSession": join_session,
    "JoinPlayer": join_player,
    "RemoveMembers": remove_members,
    "LeaveSession": leave_session,
    "ReportHostSuccess": report_host_success,
    "ReportJoinSuccess": report_join_success,
    "ReportConnectionFailure": report_connection_failure,
    "MigrateHost": migrate_host,
    "UpdateSession": update_session,
}.items()}
