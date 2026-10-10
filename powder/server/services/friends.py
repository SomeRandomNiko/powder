"""Friends: on a Powder server everyone is everyone's friend, so players can see and invite each other.

A player's friends are the other players with an account (on a server without accounts yet: every other
player). Ubisoft Connect gets them in its friends list (demux friends_service and its web calls) and Steep
in its own (UbiServices /v2/profiles/me/friends). Online: Ubisoft Connect or the game is connected now.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Friend:
    profile_id: str
    name: str
    user_id: str


def friends_of(services, profile_id: str | None) -> list[Friend]:
    repos = services.repos
    accounts = repos.accounts.all()
    if accounts:
        pids = {a.profile_id for a in accounts}
        players = [p for p in repos.players.all() if p.profile_id in pids]
    else:
        players = repos.players.all()
    me = (profile_id or "").lower()
    return sorted((Friend(p.profile_id, p.name, p.user_id) for p in players if p.profile_id != me),
                  key=lambda f: f.name.lower())


def online_profiles(services) -> set[str]:
    """Profile ids whose Ubisoft Connect (demux) or game (NEX) is connected right now."""
    connect = {c.profile_id for c in services.connect_clients if c.profile_id}
    game = {s.profile_id for s in services.sessions.by_player.values() if s.nex is not None}
    return {p.lower() for p in connect | game}
