"""Challenge leaderboards: the server's players ranked by their best score on a challenge.

The game names a board "public_challenge_<ascending|descending>[_ranked]_ldb:<challenge id>:<period>"
(period infinite, weekly or daily) and asks either for some players (its own player and friends,
GetLeaderboardForPopulations) or for a range of ranks (GetLeaderboard, e.g. rank 1 = the record).
Ascending: lower is better (times); descending: higher is better (points). Each player's best is kept
(services/stats.py), with when it was set, so a weekly or daily board shows the bests set in that period.
A row's associated_replay is the best's ghost on ranked boards: the game races the record holder's
ghost and, finishing a ranked run that beats a record, uploads its own (OnlineTrailService).
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

BOARD = re.compile(r"^public_challenge_(ascending|descending)(?:_ranked)?_ldb:(.+):(infinite|weekly|daily)$")


@dataclass(frozen=True)
class Row:
    profile_id: str
    rank: int
    score: float
    replay: str


@dataclass(frozen=True)
class Board:
    total: int                    # players on the board
    rows: list[Row]               # ranked, best first


def parse(name: str) -> tuple[str, str, str] | None:
    """Board name -> (direction, challenge id, period), None if it is not a challenge board."""
    m = BOARD.match(name)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _since(period: str, now: datetime.datetime) -> datetime.datetime | None:
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "weekly":
        start = now - datetime.timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def board(repos, name: str, now: datetime.datetime | None = None) -> Board:
    parsed = parse(name)
    if parsed is None:
        return Board(0, [])
    direction, challenge, period = parsed
    since = _since(period, now or datetime.datetime.now(datetime.UTC))
    scores = []
    for s in repos.challenge_scores.for_challenge(challenge):
        if since is not None:
            try:
                when = datetime.datetime.fromisoformat(s["updated"].replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if when < since:
                continue
        scores.append(s)
    scores.sort(key=lambda s: s["best_score"], reverse=direction == "descending")
    rows, rank, previous = [], 0, None
    for i, s in enumerate(scores, 1):
        if s["best_score"] != previous:                 # equal scores share a rank
            rank, previous = i, s["best_score"]
        rows.append(Row(s["profile_id"], rank, float(s["best_score"]), _ghost(repos, s.get("replay"))))
    return Board(len(rows), rows)


def _ghost(repos, replay: str | None) -> str:
    """A best's ghost (BEST_GHOST_* entity id), "none" once the game has deleted that entity: it
    replaces its ghost entities and would otherwise try to download a missing one."""
    if not replay or replay == "none" or repos.entities.get(replay) is None:
        return "none"
    return replay


def for_players(repos, name: str, profile_ids: list[str]) -> Board:
    """GetLeaderboardForPopulations: the rows of these players (with their global rank)."""
    b = board(repos, name)
    wanted = {p.lower() for p in profile_ids}
    return Board(b.total, [r for r in b.rows if r.profile_id in wanted])


def ranks(repos, name: str, first: int, count: int) -> Board:
    """GetLeaderboard: `count` rows from rank `first` (1-based)."""
    b = board(repos, name)
    start = max(1, first) - 1
    return Board(b.total, b.rows[start:start + max(0, count)])
