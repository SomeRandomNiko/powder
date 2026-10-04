"""Matchmaking ids (NEX MatchmakingProtocol). Single-player needs only plausible, consistent ids:
the game reserves a session id, creates a session with it (the real server echoes it back) and
creates match tickets (fresh increasing u64s). Real sessions between players come with multiplayer.
"""
from __future__ import annotations

import itertools


class MatchmakingService:
    def __init__(self):
        self._session_ids = itertools.count(0x196001)
        self._ticket_ids = itertools.count(0x7D0000, 0x1234)
        self.reserved: dict[str, int] = {}          # profile id -> last reserved session id

    def reserve_session_id(self, profile_id: str) -> int:
        sid = next(self._session_ids)
        self.reserved[profile_id] = sid
        return sid

    def create_session_with_reserved_id(self, profile_id: str) -> int:
        return self.reserved.get(profile_id) or self.reserve_session_id(profile_id)

    def create_match_ticket(self, profile_id: str) -> int:
        return next(self._ticket_ids)
