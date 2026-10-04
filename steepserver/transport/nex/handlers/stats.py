"""PlayerStats2Protocol: the player's statistics come from the database (seeded from the capture)."""
from __future__ import annotations

from ..errors import Fallthrough
from ..rmc_codec import Reader, read_type_map


def get_statistic_group(ctx, r: Reader) -> bytes:
    """params: type map {StatisticsResult: 1}, String group name (e.g. "player_profile").
    outputs: see services/stats.py."""
    read_type_map(r)
    group = r.string()
    if ctx.profile_id is None:
        raise Fallthrough()
    return ctx.services.stats.group_reply(ctx.profile_id, group)


def post_events(ctx, r: Reader) -> bytes:
    """Stat event uploads (XP, medals, unlocks). No outputs. Phase 2 applies them to the stats."""
    return b""


HANDLERS = {
    "PlayerStats2Protocol::GetStatisticGroup_V1": get_statistic_group,
    "PlayerStats2Protocol::PostEvents_V1": post_events,
    "PlayerStats2Protocol::PostEvent_V1": post_events,
}
