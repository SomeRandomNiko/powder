"""Player statistics (NEX PlayerStats2): level, XP per sport, medals, unlocks.

Wire format of a GetStatisticGroup_V1 reply (client reader FUN_7ff661949d10), verified against a
recorded 4525-byte reply that it consumes exactly:
    String name (the profile id), u32 n, n x (String key, u8 type, 8-byte value)
    type 1 = int64 LE, type 2 = float64 LE.   e.g. "level_stat:6:infinite" = overall level.
Values are stored exactly as sent, so seeded data round-trips byte for byte.

Phase 2 will apply PostEvents_V1 uploads (XP, medals) to these values.
"""
from __future__ import annotations

import struct

from ..db.repos import Repos, StatRow
from ..transport.nex.rmc_codec import Reader, Writer

STAT_INT, STAT_DOUBLE = 1, 2
PROFILE_GROUP = "player_profile"
LEVEL_KEY = "level_stat:6:infinite"


def decode_stat_group(body: bytes) -> tuple[str, list[StatRow]]:
    r = Reader(body)
    name = r.string()
    rows = [StatRow(r.string(), r.u8(), r.take(8)) for _ in range(r.u32())]
    if r.remaining:
        raise ValueError(f"{r.remaining} trailing bytes after stat group")
    return name, rows


def encode_stat_group(name: str, rows: list[StatRow]) -> bytes:
    w = Writer().string(name).u32(len(rows))
    for row in rows:
        w.string(row.key).u8(row.type).raw(row.value)
    return w.bytes()


def stat_value(row: StatRow) -> int | float:
    return struct.unpack("<d", row.value)[0] if row.type == STAT_DOUBLE else struct.unpack("<q", row.value)[0]


def encode_value(value: int | float) -> tuple[int, bytes]:
    if isinstance(value, float):
        return STAT_DOUBLE, struct.pack("<d", value)
    return STAT_INT, struct.pack("<q", value)


class StatsService:
    def __init__(self, repos: Repos):
        self.repos = repos

    def group(self, profile_id: str, group_name: str) -> list[StatRow]:
        return self.repos.stats.group(profile_id, group_name)

    def group_reply(self, profile_id: str, group_name: str) -> bytes:
        """GetStatisticGroup_V1 outputs. The reply names the group after the player's profile id."""
        return encode_stat_group(profile_id, self.group(profile_id, group_name))

    def get(self, profile_id: str, key: str, group_name: str = PROFILE_GROUP) -> int | float | None:
        for row in self.group(profile_id, group_name):
            if row.key == key:
                return stat_value(row)
        return None

    def set(self, profile_id: str, key: str, value: int | float, group_name: str = PROFILE_GROUP) -> None:
        type_, raw = encode_value(value)
        self.repos.stats.set(profile_id, group_name, key, type_, raw)

    def import_group(self, profile_id: str, group_name: str, reply_body: bytes) -> int:
        """Store a recorded GetStatisticGroup reply for a player; returns the number of stats."""
        _, rows = decode_stat_group(reply_body)
        with self.repos.db.transaction():
            for row in rows:
                self.repos.stats.set(profile_id, group_name, row.key, row.type, row.value)
        return len(rows)
