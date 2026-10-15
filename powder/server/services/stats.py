"""Player statistics (NEX PlayerStats2): level, XP per sport, medals, unlocks.

Wire format of a GetStatisticGroup_V1 reply (client reader FUN_7ff661949d10), verified against a
recorded 4525-byte reply that it consumes exactly:
    String name (the profile id), u32 n, n x (String key, u8 type, 8-byte value)
    type 1 = int64 LE, type 2 = float64 LE.   e.g. "level_stat:6:infinite" = overall level.
Values are stored exactly as sent, so seeded data round-trips byte for byte.

The game computes progress itself and uploads it as stat events (PostEvents_V1 / PostEvent_V1);
EVENT_RULES maps each event to the stat it updates. The rules were derived by diffing recorded stat
snapshots against the events uploaded between them (every change explained). They are monotonic on
purpose: a game given stale stats uploads regressed values (seen live: levels 2-3, playtime 0), and
max / add / bitwise-or make such uploads harmless.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field

from ..db.repos import Repos, StatRow
from ..transport.nex.rmc_codec import V_DOUBLE, Reader, Writer

log = logging.getLogger("powder.stats")

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


def _to_int64(v: int) -> int:
    """Wrap to signed 64 bits (bitfields use all 64 bits; uint64 inputs may exceed int64)."""
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


def encode_value(value: int | float) -> tuple[int, bytes]:
    if isinstance(value, float):
        return STAT_DOUBLE, struct.pack("<d", value)
    return STAT_INT, struct.pack("<q", value)


@dataclass
class StatEvent:
    """One uploaded event: name + fields {key: (variant type, value)}."""
    name: str
    fields: dict = field(default_factory=dict)

    def value(self, key: str):
        return self.fields[key][1]

    def plain(self) -> dict:
        return {k: v for k, (_, v) in self.fields.items()}


SET, MAX, ADD, OR = "set", "max", "add", "or"

# event -> (stat key template filled from the event's fields, combine mode, field holding the value)
EVENT_RULES: dict[str, tuple[str, str, str]] = {
    "update_level": ("level_stat:{expertise_id}:infinite", MAX, "level_expertise"),
    "update_xp": ("xp_ratio_stat:{expertise_id}:infinite", SET, "xp_ratio_expertise"),
    "update_player_playtime": ("player_playtime:infinite", MAX, "playtime"),
    "complete_memorable_repeatable": ("memorable_repeatable:{memorable_id}:infinite", ADD, "count"),
    "complete_memorable_exclusive": ("memorable_exclusive:{type_id}:{mapping_id}:infinite", OR, "bitfield"),
    "unlock_exploration": ("unlockable_exploration:{type_id}:{mapping_id}:infinite", OR, "bitfield"),
    "unlock_challenge": ("unlockable_challenge:{mapping_id}:infinite", OR, "bitfield"),
}
# challenge results feed leaderboards, not player_profile: kept as a personal best per challenge.
# Only ranked challenges have ghosts (as on Ubisoft's servers): their result comes with the id of the
# BEST_GHOST_* entity the game made of the run, or "none" when it made none (the run beat no record).
# Player-made challenges (id "<creator profile>_<savename>") have their own: *_challenge_score_event.
SCORE_EVENTS = {f"{direction}_{kind}_score_event": direction       # descending: higher is better
                for direction in ("descending", "ascending")
                for kind in ("public_challenge", "public_challenge_ranked", "challenge", "challenge_ranked")}
NO_REPLAY = "none"


class StatsService:
    def __init__(self, repos: Repos):
        self.repos = repos
        self._unknown_logged: set[str] = set()

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

    # ---- uploads -------------------------------------------------------------------------------
    def apply_events(self, profile_id: str, events: list[StatEvent], group_name: str = PROFILE_GROUP) -> int:
        """Record and apply uploaded events; returns how many stats changed."""
        stale = self._stale_expertises(profile_id, events, group_name)
        changed = 0
        with self.repos.db.transaction():
            for ev in events:
                self.repos.stat_events.add(profile_id, ev.name, ev.plain())
                if ev.name in SCORE_EVENTS:
                    self._record_score(profile_id, ev)
                    continue
                rule = EVENT_RULES.get(ev.name)
                if rule is None:
                    if ev.name not in self._unknown_logged:
                        self._unknown_logged.add(ev.name)
                        log.warning("unknown stat event %r %s (stored, not applied)", ev.name, ev.plain())
                    continue
                template, mode, value_field = rule
                try:
                    key = template.format(**ev.plain())
                    vtype, value = ev.fields[value_field]
                except (KeyError, IndexError):
                    log.warning("stat event %s without the expected fields: %s", ev.name, ev.plain())
                    continue
                if ev.name == "update_xp" and ev.plain().get("expertise_id") in stale:
                    continue
                changed += self._combine(profile_id, group_name, key, mode, vtype, value)
        return changed

    def _stale_expertises(self, profile_id: str, events: list[StatEvent], group_name: str) -> set:
        """Sports whose uploaded level is below the stored one: the batch was computed from stale
        stats, so its xp ratios for those sports must not overwrite the real ones."""
        stale = set()
        for ev in events:
            if ev.name == "update_level":
                e, level = ev.plain().get("expertise_id"), ev.plain().get("level_expertise")
                row = self.repos.stats.get(profile_id, group_name, f"level_stat:{e}:infinite")
                if row is not None and level is not None and level < stat_value(row):
                    stale.add(e)
        return stale

    def _combine(self, profile_id: str, group_name: str, key: str, mode: str, vtype: int, value) -> int:
        row = self.repos.stats.get(profile_id, group_name, key)
        stat_type = row.type if row is not None else (STAT_DOUBLE if vtype == V_DOUBLE else STAT_INT)
        old = stat_value(row) if row is not None else None
        if stat_type == STAT_DOUBLE:
            value = float(value)
        else:
            value = _to_int64(int(value))
        if old is None:
            new = value
        elif mode == SET:
            new = value
        elif mode == MAX:
            new = max(old, value)
        elif mode == ADD:
            new = old + value
        else:  # OR
            new = _to_int64(int(old) | int(value))
        if row is not None and new == old:
            return 0
        raw = struct.pack("<d", new) if stat_type == STAT_DOUBLE else struct.pack("<q", new)
        self.repos.stats.set(profile_id, group_name, key, stat_type, raw)
        return 1

    def _record_score(self, profile_id: str, ev: StatEvent) -> None:
        f = ev.plain()
        challenge = f.get("challenge_id_to_use")
        try:
            score = float(f.get("score"))
        except (TypeError, ValueError):
            log.warning("challenge score event without a numeric score: %s", f)
            return
        if not challenge:
            return
        direction = SCORE_EVENTS[ev.name]
        best = self.repos.challenge_scores.get(profile_id, challenge)
        replay = f.get("replay") or NO_REPLAY
        better = (best is None or (score > best["best_score"] if direction == "descending"
                                   else score < best["best_score"]))
        ghost_of_best = best is not None and score == best["best_score"] and replay != NO_REPLAY
        if better or ghost_of_best:
            self.repos.challenge_scores.set(profile_id, challenge, direction, score, replay)
