"""Login sessions and live connections per player.

A PlayerSession is created at UbiServices login and carries our own ticket. The game then sends
`Authorization: Ubi_v1 t=<ticket>` on HTTPS calls and the same ticket as the NEX LoginWithToken
token, so both transports find the same session. The SessionRegistry knows each player's open
WebSockets and NEX connection, which is what lets the server push messages to a player.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..util import iso_now
from .identity import Player

log = logging.getLogger("powder.sessions")
SESSION_TTL_S = 3 * 3600


def ubi_shaped_ticket(app_id: str, session_id: str) -> str:
    """A ticket shaped like Ubisoft's (base64 JSON header . payload . MAC), for Ubisoft Connect, in
    case it reads the header. The payload is random: only this server ever validates tickets."""
    header = json.dumps({"ver": "1", "aid": app_id, "env": "Prod", "sid": session_id, "typ": "JWE",
                         "enc": "A128CBC", "iv": secrets.token_urlsafe(16), "int": "HS256",
                         "kid": str(uuid.uuid4())}, indent=2)
    return ".".join((base64.b64encode(header.encode()).decode(), secrets.token_urlsafe(96),
                     secrets.token_urlsafe(32)))


class WsChannel(Protocol):
    async def send_json(self, data: Any) -> None: ...


class NexChannel(Protocol):
    def send_request(self, protocol: str, method: str, params: bytes, call_id: int | None = None) -> int: ...


@dataclass(eq=False)
class PlayerSession:
    player: Player
    ticket: str
    session_id: str
    client_ip: str
    created: float = field(default_factory=time.time)
    expires: float = field(default_factory=lambda: time.time() + SESSION_TTL_S)
    websockets: set = field(default_factory=set)        # WsChannel
    nex: NexChannel | None = None
    data: dict = field(default_factory=dict)            # per-session scratch (services/plugins)

    @property
    def profile_id(self) -> str:
        return self.player.profile_id


class SessionRegistry:
    def __init__(self):
        self.by_ticket: dict[str, PlayerSession] = {}
        self.by_session_id: dict[str, PlayerSession] = {}
        self.by_player: dict[str, PlayerSession] = {}     # most recent session per player

    def issue(self, player: Player, client_ip: str, app_id: str = "") -> PlayerSession:
        session_id = str(uuid.uuid4())
        s = PlayerSession(player, ticket=ubi_shaped_ticket(app_id, session_id) if app_id else
                          "powder." + secrets.token_urlsafe(48), session_id=session_id, client_ip=client_ip)
        old = self.by_player.get(player.profile_id)
        if old:                                  # keep live channels across a re-login
            s.websockets, s.nex = old.websockets, old.nex
        self.by_ticket[s.ticket] = s
        self.by_session_id[s.session_id] = s
        self.by_player[player.profile_id] = s
        self._expire()
        return s

    def by_token(self, ticket: str) -> PlayerSession | None:
        s = self.by_ticket.get(ticket)
        return s if s and s.expires > time.time() else None

    def for_player(self, profile_id: str) -> PlayerSession | None:
        return self.by_player.get(profile_id)

    def _expire(self) -> None:
        now = time.time()
        for t, s in list(self.by_ticket.items()):
            if s.expires < now:
                self.by_ticket.pop(t, None)
                self.by_session_id.pop(s.session_id, None)

    # ---- server push (the seam for notifications and multiplayer) ----------------------------
    async def push_ws(self, profile_id: str, notification_type: str, content: dict) -> int:
        """Send a UbiServices notification over the player's WebSocket(s); returns how many got it."""
        s = self.by_player.get(profile_id)
        sent = 0
        for ws in list(s.websockets if s else ()):
            try:
                await ws.send_json({"notificationType": notification_type, "content": content,
                                    "datePosted": iso_now()})
                sent += 1
            except Exception as e:  # noqa: BLE001 - a dead socket must not break the caller
                log.warning("push_ws to %s failed: %s", profile_id, e)
                s.websockets.discard(ws)
        return sent

    def push_nex(self, profile_id: str, protocol: str, method: str, params: bytes,
                 call_id: int | None = None) -> bool:
        """Send a server-initiated RMC request over the player's NEX connection."""
        s = self.by_player.get(profile_id)
        if not s or not s.nex:
            return False
        s.nex.send_request(protocol, method, params, call_id)
        return True
