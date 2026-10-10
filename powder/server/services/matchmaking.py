"""Matchmaking (NEX MatchmakingProtocol): puts players into one session, so their games meet peer-to-peer.

How Steep uses it (from recordings of Ubisoft's server):
  1. Every game reserves a session id and creates its own small session (type 1, its group), with its
     station URLs (where other games reach it).
  2. It opens a match ticket (type 2 = the shared world) with search criteria (world_id, worldcell, ...)
     and refreshes it every ~10 s (UpdateTicket).
  3. Once two tickets match, the server makes a type-2 session and picks a host: the host gets code 1,
     asks GetSession, then reports its station URLs (ReportHostSuccess). Every other member then gets
     code 2 with the host's URLs, connects to the host and reports ReportJoinSuccess (or
     ReportConnectionFailure, then a new ticket excluding that session).
  4. A ticket that matches an existing session (with its host's URLs known) joins it right away (code 2).
  5. When the host leaves, another member is asked to host (code 12); it answers MigrateHost with its
     URLs and everyone gets code 10 with the new host.
A ticket is used up by a match; the game opens a new one after LeaveSession.

Policy here: one world for everyone with the same world_id (worldcell is ignored for now, so two
friends always meet), at most `max_size` players per session (what the ticket asks, 32).

The service is synchronous and transport-free: notifications go through `notify(profile_id, code,
params)`, which the server wires to NEX pushes (services/__init__.py).
"""
from __future__ import annotations

import datetime
import ipaddress
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("powder.matchmaking")

SESSION_GROUP, SESSION_WORLD = 1, 2                  # session types: the player's own group, the shared world
PENDING, JOINED = 1, 2                               # member states (GetSession)
CODE_HOST, CODE_JOIN, CODE_MIGRATE_DONE, CODE_BECOME_HOST = 1, 2, 10, 12
ATTR_ORDER = ("worldcell", "language", "country", "connectivity_nat", "version", "world_id")


@dataclass
class Member:
    profile_id: str
    state: int = PENDING
    urls: list[str] = field(default_factory=list)
    ticket_id: int | None = None
    ticket_created: datetime.datetime | None = None


@dataclass
class Session:
    id: int
    type: int
    host: str | None                                 # profile id, None until the host reported its URLs
    attrs: dict[str, int] = field(default_factory=dict)
    members: dict[str, Member] = field(default_factory=dict)
    max_size: int = 32
    created: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.UTC))
    host_urls: list[str] = field(default_factory=list)
    host_candidate: str | None = None                # asked to host (code 1 / 12), not confirmed yet

    @property
    def free(self) -> int:
        return max(0, self.max_size - len(self.members))


@dataclass
class Ticket:
    id: int
    profile_id: str
    own_session: int
    session_type: int
    criteria: dict[str, int]
    excluded: set[int]
    max_size: int
    created: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.UTC))
    monotonic: float = field(default_factory=time.monotonic)

    @property
    def world(self):
        return self.criteria.get("world_id")


def reachable_urls(urls: list[str]) -> list[str]:
    """Station URLs other PCs can use: loopback addresses dropped (unless that is all there is)."""
    def loopback(url: str) -> bool:
        for kv in url.split(":/", 1)[-1].split("#")[0].split(";"):
            k, _, v = kv.partition("=")
            if k == "address":
                try:
                    return ipaddress.ip_address(v).is_loopback
                except ValueError:
                    return False
        return False
    good = [u for u in urls if not loopback(u)]
    return good or list(urls)


def iso(t: datetime.datetime) -> str:
    return t.astimezone(datetime.UTC).replace(tzinfo=None).isoformat(timespec="microseconds")


class MatchmakingService:
    def __init__(self, notify: Callable[[str, int, dict], None] | None = None):
        self._session_ids = itertools.count(0x196001)
        self._ticket_ids = itertools.count(0x7D0000, 0x1234)
        self.notify = notify or (lambda pid, code, params: None)
        self.reserved: dict[str, int] = {}           # profile id -> last reserved session id
        self.sessions: dict[int, Session] = {}
        self.tickets: dict[int, Ticket] = {}

    # ---- the player's own session (type 1) ------------------------------------------------------
    def reserve_session_id(self, profile_id: str) -> int:
        sid = next(self._session_ids)
        self.reserved[profile_id] = sid
        return sid

    def create_session_with_reserved_id(self, profile_id: str, sid: int | None = None, session_type: int = 1,
                                        attrs: dict | None = None, urls: list[str] | None = None,
                                        max_size: int = 4) -> int:
        sid = sid or self.reserved.get(profile_id) or self.reserve_session_id(profile_id)
        s = Session(sid, session_type, profile_id, dict(attrs or {}), max_size=max_size,
                    host_urls=list(urls or []))
        s.members[profile_id] = Member(profile_id, JOINED, list(urls or []))
        self.sessions[sid] = s
        return sid

    # ---- tickets --------------------------------------------------------------------------------
    def create_match_ticket(self, profile_id: str, own_session: int = 0, session_type: int = SESSION_WORLD,
                            criteria: dict | None = None, excluded=(), max_size: int = 32) -> int:
        t = Ticket(next(self._ticket_ids), profile_id, own_session, session_type, dict(criteria or {}),
                   set(excluded), max_size)
        self.tickets[t.id] = t
        log.info("ticket %d from %s: world %s, cell %s%s", t.id, profile_id, t.world, t.criteria.get("worldcell"),
                 f", excluding sessions {sorted(t.excluded)}" if t.excluded else "")
        self._match(t)
        return t.id

    def update_ticket(self, profile_id: str, ticket_id: int, criteria: dict) -> None:
        t = self.tickets.get(ticket_id)
        if t is None or t.profile_id != profile_id:
            return
        t.criteria.update(criteria)
        self._match(t)

    def cancel_all_tickets(self, profile_id: str) -> None:
        for tid in [tid for tid, t in self.tickets.items() if t.profile_id == profile_id]:
            del self.tickets[tid]

    # ---- matching -------------------------------------------------------------------------------
    def _match(self, t: Ticket) -> None:
        if t.session_type != SESSION_WORLD or t.id not in self.tickets:
            return
        # an existing session with a known host
        for s in sorted(self.sessions.values(), key=lambda s: s.created):
            if (s.type == SESSION_WORLD and s.host and s.host_urls and s.free and s.id not in t.excluded
                    and t.profile_id not in s.members and s.attrs.get("world_id") == t.world):
                self._add(s, t)
                self._send_join(s, s.members[t.profile_id])
                return
        # another waiting ticket: a new session, the older ticket's player hosts
        for other in sorted(self.tickets.values(), key=lambda o: o.monotonic):
            if (other is not t and other.profile_id != t.profile_id and other.session_type == SESSION_WORLD
                    and other.world == t.world and not self._in_world_session(other.profile_id)):
                self._new_session(other, t)
                return

    def _in_world_session(self, profile_id: str) -> bool:
        return any(s.type == SESSION_WORLD and profile_id in s.members for s in self.sessions.values())

    def _add(self, s: Session, t: Ticket) -> None:
        s.members[t.profile_id] = Member(t.profile_id, PENDING, ticket_id=t.id, ticket_created=t.created)
        self.tickets.pop(t.id, None)

    def _new_session(self, host_ticket: Ticket, joiner: Ticket) -> None:
        crit = host_ticket.criteria
        attrs = {k: int(crit[k]) for k in ATTR_ORDER if k in crit}
        attrs["reputation"] = 1
        s = Session(next(self._session_ids), SESSION_WORLD, None, attrs, max_size=host_ticket.max_size,
                    host_candidate=host_ticket.profile_id)
        self.sessions[s.id] = s
        self._add(s, host_ticket)
        self._add(s, joiner)
        log.info("session %d: %s hosts, %s joins (world %s)", s.id, host_ticket.profile_id, joiner.profile_id,
                 host_ticket.world)
        self.notify(host_ticket.profile_id, CODE_HOST, {
            "session_type": SESSION_WORLD, "ticket_id": host_ticket.id, "session_id": s.id,
            "ticket_creation_dates": json.dumps([iso(host_ticket.created)])})

    def _send_join(self, s: Session, m: Member) -> None:
        log.info("session %d: %s joins host %s", s.id, m.profile_id, s.host)
        tickets = {pid: x.ticket_id for pid, x in s.members.items() if x.ticket_id is not None}
        self.notify(m.profile_id, CODE_JOIN, {
            "session_type": s.type,
            "ticket_creation_dates": json.dumps([iso(m.ticket_created or s.created)]),
            "ticket_ids": json.dumps(tickets), "session_id": s.id, "host_profile_id": s.host,
            "host_station_urls": json.dumps(reachable_urls(s.host_urls))})

    # ---- reports from the games -----------------------------------------------------------------
    def report_host_success(self, profile_id: str, sid: int, urls: list[str]) -> None:
        s = self.sessions.get(sid)
        if s is None or profile_id not in s.members:
            log.warning("ReportHostSuccess from %s for unknown session %d", profile_id, sid)
            return
        s.host, s.host_urls, s.host_candidate = profile_id, list(urls), None
        s.members[profile_id].state = JOINED
        s.members[profile_id].urls = list(urls)
        log.info("session %d: %s hosts (%d station URLs)", sid, profile_id, len(urls))
        for m in list(s.members.values()):
            if m.profile_id != profile_id and m.state == PENDING:
                self._send_join(s, m)
        for t in sorted(self.tickets.values(), key=lambda t: t.monotonic):
            self._match(t)

    def report_join_success(self, profile_id: str, sid: int) -> None:
        s = self.sessions.get(sid)
        if s and profile_id in s.members:
            s.members[profile_id].state = JOINED
            log.info("session %d: %s connected to host %s", sid, profile_id, s.host)

    def report_connection_failure(self, profile_id: str, sid: int, reason: int | None = None) -> None:
        log.warning("session %d: %s could NOT connect to host %s (reason %s)", sid, profile_id,
                    self.sessions[sid].host if sid in self.sessions else "?", reason)
        self.leave(profile_id, sid)

    def join_session(self, profile_id: str, sid: int) -> None:
        s = self.sessions.get(sid)
        if s and profile_id not in s.members and s.free:
            s.members[profile_id] = Member(profile_id, JOINED)

    def update_session(self, profile_id: str, sid: int, attrs: dict) -> None:
        s = self.sessions.get(sid)
        if s and s.host == profile_id:
            s.attrs.update({k: int(v) for k, v in attrs.items() if isinstance(v, int)})

    def migrate_host(self, profile_id: str, sid: int, urls: list[str]) -> None:
        s = self.sessions.get(sid)
        if s is None or profile_id not in s.members:
            return
        s.host, s.host_urls, s.host_candidate = profile_id, list(urls), None
        s.members[profile_id].urls = list(urls)
        log.info("session %d: %s is the new host", sid, profile_id)
        for pid in s.members:
            self.notify(pid, CODE_MIGRATE_DONE, {"session_id": sid, "host_profile_id": profile_id})

    def leave(self, profile_id: str, sid: int) -> None:
        s = self.sessions.get(sid)
        if s is None or profile_id not in s.members:
            return
        del s.members[profile_id]
        log.info("session %d: %s left (%d remain)", sid, profile_id, len(s.members))
        if not s.members or (s.type != SESSION_WORLD and s.host == profile_id):
            del self.sessions[sid]
            return
        if profile_id in (s.host, s.host_candidate):
            s.host, s.host_urls, s.host_candidate = None, [], None
            joined = [m for m in s.members.values() if m.state == JOINED] or list(s.members.values())
            new = joined[0].profile_id
            s.host_candidate = new
            log.info("session %d: host left, asking %s to host", sid, new)
            self.notify(new, CODE_BECOME_HOST, {"host_profile_id": new, "session_id": sid,
                                                "session_type": s.type})

    def on_disconnect(self, profile_id: str) -> None:
        """The player's game is gone (NEX connection closed): out of every session, tickets cancelled."""
        self.cancel_all_tickets(profile_id)
        for sid in [sid for sid, s in self.sessions.items() if profile_id in s.members]:
            self.leave(profile_id, sid)
        self.reserved.pop(profile_id, None)

    # ---- GetSession ----------------------------------------------------------------------------
    def get_session(self, sid: int) -> Session | None:
        return self.sessions.get(sid)
