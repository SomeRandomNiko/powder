"""PlayerStats2Protocol: statistics live in the database (seeded from the recordings) and the game's
uploads are applied to them (rules in services/stats.py)."""
from __future__ import annotations

from ....services.stats import StatEvent
from ..errors import Fallthrough
from ..rmc_codec import Reader, read_type_map, read_variant


def get_statistic_group(ctx, r: Reader) -> bytes:
    """params: type map {StatisticsResult: 1}, String group name (e.g. "player_profile").
    outputs: see services/stats.py."""
    read_type_map(r)
    group = r.string()
    if ctx.profile_id is None:
        raise Fallthrough()
    return ctx.services.stats.group_reply(ctx.profile_id, group)


def decode_events(r: Reader, single: bool) -> list[StatEvent]:
    """params: type map {StatisticEvent: 1}, then u32 n events (PostEvents) or one event (PostEvent).
    event = String name, u32 m, m x (String key, Variant value)."""
    read_type_map(r)
    events = []
    for _ in range(1 if single else r.u32()):
        name = r.string()
        events.append(StatEvent(name, {r.string(): read_variant(r) for _ in range(r.u32())}))
    return events


async def _post(ctx, r: Reader, single: bool) -> bytes:
    events = decode_events(r, single)
    if ctx.profile_id is None:          # not logged in: nothing to attribute the progress to
        return b""
    ctx.services.stats.apply_events(ctx.profile_id, events)
    await ctx.services.events.emit("stats_event", profile_id=ctx.profile_id, events=events)
    return b""                          # the client reads no outputs


async def post_events(ctx, r: Reader) -> bytes:
    return await _post(ctx, r, single=False)


async def post_event(ctx, r: Reader) -> bytes:
    return await _post(ctx, r, single=True)


HANDLERS = {
    "PlayerStats2Protocol::GetStatisticGroup_V1": get_statistic_group,
    "PlayerStats2Protocol::PostEvents_V1": post_events,
    "PlayerStats2Protocol::PostEvent_V1": post_event,
}
