"""Who is logging in? The seam between the game's login and our player records.

The game logs in with `Authorization: uplaypc_v1 t=<Ubisoft Connect ticket>` (a JWE we cannot and
need not decrypt). An AccountResolver turns the login request into a profile id. The default one
binds client IPs to players (config [players.bind]) and otherwise uses the configured default
player, which is enough for "friends on one host" on a LAN/VPN. A plugin can install a smarter
resolver later (e.g. per-machine tokens).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import Config


@dataclass(frozen=True)
class Player:
    profile_id: str
    user_id: str
    name: str


@dataclass(frozen=True)
class LoginRequest:
    client_ip: str
    auth_scheme: str = ""           # "uplaypc_v1", "Ubi_v1", "Basic", ...
    auth_value: str = ""
    headers: dict = field(default_factory=dict)


class AccountResolver(Protocol):
    def resolve(self, req: LoginRequest) -> str | None:
        """-> profile id of the player making this login, or None to refuse it."""


class DefaultResolver:
    def __init__(self, config: Config, default_profile_id: Callable[[], str]):
        self.config = config
        self.default_profile_id = default_profile_id

    def resolve(self, req: LoginRequest) -> str | None:
        return self.config.players.bind.get(req.client_ip) or self.default_profile_id()


def parse_authorization(header: str | None) -> tuple[str, str]:
    """'Ubi_v1 t=abc' -> ('Ubi_v1', 'abc'); 'Basic xyz' -> ('Basic', 'xyz')."""
    if not header:
        return "", ""
    scheme, _, rest = header.strip().partition(" ")
    rest = rest.strip()
    if rest.startswith("t="):
        rest = rest[2:]
    return scheme, rest
