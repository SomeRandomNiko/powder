"""Login and session lookup: game requests -> Player -> PlayerSession.

Lenient mode (default, [server] strict_tickets = false): a ticket we do not know (e.g. the server
was restarted while the game kept running) is not rejected; the request is attributed via the
AccountResolver instead and a warning is logged. Strict mode refuses it.
"""
from __future__ import annotations

import logging
from typing import Callable

from ..config import Config
from ..constants import APP_ID
from ..core.identity import AccountResolver, DefaultResolver, LoginRequest, Player
from ..core.sessions import PlayerSession, SessionRegistry
from ..db.repos import Repos

log = logging.getLogger("powder.accounts")


class AuthError(Exception):
    pass


class AccountService:
    def __init__(self, config: Config, repos: Repos, sessions: SessionRegistry,
                 seed: Callable[[str], None], default_profile_id: Callable[[], str]):
        self.config, self.repos, self.sessions = config, repos, sessions
        self.resolver: AccountResolver = DefaultResolver(config, default_profile_id)
        self._seed = seed

    def player(self, profile_id: str) -> Player:
        """The player record, created (and seeded) on first use."""
        row = self.repos.players.get(profile_id)
        if row is None:
            self._seed(profile_id)
            row = self.repos.players.get(profile_id)
        return Player(row.profile_id, row.user_id, row.name)

    def login(self, req: LoginRequest) -> PlayerSession:
        profile_id = self.resolver.resolve(req)
        if not profile_id:
            raise AuthError("login refused by account resolver")
        app_id = {k.lower(): v for k, v in req.headers.items()}.get("ubi-appid", "").lower()
        connect = app_id if app_id and app_id != APP_ID else ""        # Ubisoft Connect and its parts
        session = self.sessions.issue(self.player(profile_id), req.client_ip, app_id=connect)
        log.info("login %s (%s) from %s%s", session.player.name, profile_id, req.client_ip,
                 " [Ubisoft Connect]" if connect else "")
        return session

    def session_for_ticket(self, ticket: str, client_ip: str) -> PlayerSession | None:
        """HTTPS `Ubi_v1 t=` ticket or NEX LoginWithToken token -> session (lenient fallback)."""
        s = self.sessions.by_token(ticket) if ticket else None
        if s or self.config.server.strict_tickets:
            return s
        req = LoginRequest(client_ip=client_ip)
        profile_id = self.resolver.resolve(req)
        if not profile_id:
            return None
        s = self.sessions.for_player(profile_id)
        if s is None:
            s = self.sessions.issue(self.player(profile_id), client_ip)
        if ticket:
            log.warning("unknown ticket from %s -> attributed to %s (lenient mode)", client_ip, profile_id)
        return s
