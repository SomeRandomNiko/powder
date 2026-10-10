"""Matchmaking (NEX MatchmakingProtocol): puts players into one session, so their games meet peer-to-peer.

How Steep uses it (from recordings of Ubisoft's server, confirmed live with two PCs):
  1. Every game reserves a session id and creates its own small session (type 1, its group), with its
     station URLs (where other games reach it).
  2. It opens a match ticket (type 2 = the shared world) with search criteria (world_id, worldcell, ...)
     and refreshes it every ~10 s (UpdateTicket); a new ticket whenever its player rides into another cell.
  3. Once two tickets match, the server makes a type-2 session and picks a host: the host gets code 1,
     asks GetSession, then reports its station URLs (ReportHostSuccess). Every other member then gets
     code 2 with the host's URLs, connects to the host and reports ReportJoinSuccess (or
     ReportConnectionFailure, then a new ticket excluding that session).
  4. A ticket that matches an existing session (with its host's URLs known) joins it right away (code 2).
  5. When the host leaves, another member is asked to host (code 12); it answers MigrateHost with its
     URLs and everyone gets code 10 with the new host.
A ticket is used up by a match; the game opens a new one after LeaveSession.

Policy here: players meet when their tickets name the same world_id and worldcell (a game leaves a
session of another cell right after GetSession), at most `max_size` players per session (what the
ticket asks, 32). The game only acts on code 1 while it is searching (its SessionsFlowMachine is in
InMatchmaking.SearchingSession), so a code 1 or 2 without an answer is sent again; after MAX_ASKS tries
the session is given up and its members' tickets go back into the pool.

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
RESEND_AFTER_S = 15.0             # code 1 / 2 again when the game did not react within this time
MAX_ASKS = 3                      # tries per code 1 / 2; then the session is given up / the member left alone
ATTR_ORDER = ("worldcell", "language", "country", "connectivity_nat", "version", "world_id")


@dataclass
class Ticket:
    id: int                                          # increasing: a lower id is an older ticket
    profile_id: str
    own_session: int
    session_type: int
    criteria: dict[str, int]
    excluded: set[int]
    max_size: int
    created: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.UTC))

    @property
    def place(self) -> tuple:
        """Where the player is: world and world cell. Only tickets of one place are matched."""
        return self.criteria.get("world_id"), self.criteria.get("worldcell")


@dataclass
class Member:
    profile_id: str
    state: int = PENDING
    urls: list[str] = field(default_factory=list)
    ticket: Ticket | None = None                     # the ticket that brought the member in (world sessions)
    asked_at: float | None = None                    # code 1 / 2 last sent, while no answer came (clock)
    asked: int = 0                                   # how often


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

    @property
    def place(self) -> tuple:
        return self.attrs.get("world_id"), self.attrs.get("worldcell")


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
    def __init__(self, notify: Callable[[str, int, dict], None] | None = None, clock=time.monotonic,
                 resend_after: float = RESEND_AFTER_S):
        self.clock, self.resend_after = clock, resend_after
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
        if session_type == SESSION_WORLD:            # one world session per player: a new search ends the old
            for sid in [sid for sid, s in self.sessions.items()
                        if s.type == SESSION_WORLD and profile_id in s.members]:
                self.leave(profile_id, sid)
        self.tickets[t.id] = t
        log.info("ticket %d from %s: world %s, cell %s%s", t.id, profile_id, *t.place,
                 f", excluding sessions {sorted(t.excluded)}" if t.excluded else "")
        self._match(t)
        return t.id

    def update_ticket(self, profile_id: str, ticket_id: int, criteria: dict) -> None:
        t = self.tickets.get(ticket_id)
        if t is None or t.profile_id != profile_id:
            return
        before = t.place
        t.criteria.update(criteria)
        if t.place != before:
            log.info("ticket %d from %s: now world %s, cell %s", t.id, profile_id, *t.place)
        self._match(t)

    def cancel_all_tickets(self, profile_id: str) -> None:
        for tid in [tid for tid, t in self.tickets.items() if t.profile_id == profile_id]:
            del self.tickets[tid]

    # ---- matching -------------------------------------------------------------------------------
    def _match(self, t: Ticket) -> None:
        if t.session_type != SESSION_WORLD or t.id not in self.tickets:
            return
        # an existing session with a known host
        for s in sorted(self.sessions.values(), key=lambda s: s.id):
            if (s.type == SESSION_WORLD and s.host and s.host_urls and s.free and s.id not in t.excluded
                    and t.profile_id not in s.members and s.place == t.place):
                self._add(s, t)
                self._send_join(s, s.members[t.profile_id])
                return
        # another waiting ticket: a new session, the older ticket's player hosts
        for other in sorted(self.tickets.values(), key=lambda o: o.id):
            if (other is not t and other.profile_id != t.profile_id and other.session_type == SESSION_WORLD
                    and other.place == t.place):
                self._new_session(*sorted((other, t), key=lambda x: x.id))
                return

    def _add(self, s: Session, t: Ticket) -> None:
        s.members[t.profile_id] = Member(t.profile_id, PENDING, ticket=t)
        self.tickets.pop(t.id, None)

    def _new_session(self, host_ticket: Ticket, joiner: Ticket) -> None:
        crit = host_ticket.criteria
        attrs = {k: int(crit[k]) for k in ATTR_ORDER if k in crit}
        attrs["reputation"] = 1
        s = Session(next(self._session_ids), SESSION_WORLD, None, attrs, max_size=host_ticket.max_size)
        self.sessions[s.id] = s
        self._add(s, host_ticket)
        self._add(s, joiner)
        log.info("session %d: %s hosts, %s joins (world %s, cell %s)", s.id, host_ticket.profile_id,
                 joiner.profile_id, *s.place)
        self._ask_host(s, s.members[host_ticket.profile_id])

    def _ask_host(self, s: Session, m: Member) -> None:
        """Code 1: the member is to host the session (sent again by tick until it reports back)."""
        s.host_candidate = m.profile_id
        m.asked_at, m.asked = self.clock(), m.asked + 1
        if m.asked > 1:
            log.info("session %d: asking %s to host again (try %d)", s.id, m.profile_id, m.asked)
        created = m.ticket.created if m.ticket else s.created
        self.notify(m.profile_id, CODE_HOST, {
            "session_type": s.type, "ticket_id": m.ticket.id if m.ticket else None, "session_id": s.id,
            "ticket_creation_dates": json.dumps([iso(created)])})

    def _send_join(self, s: Session, m: Member) -> None:
        m.asked_at, m.asked = self.clock(), m.asked + 1
        log.info("session %d: %s joins host %s%s", s.id, m.profile_id, s.host,
                 f" (try {m.asked})" if m.asked > 1 else "")
        tickets = {pid: x.ticket.id for pid, x in s.members.items() if x.ticket}
        self.notify(m.profile_id, CODE_JOIN, {
            "session_type": s.type,
            "ticket_creation_dates": json.dumps([iso(m.ticket.created if m.ticket else s.created)]),
            "ticket_ids": json.dumps(tickets), "session_id": s.id, "host_profile_id": s.host,
            "host_station_urls": json.dumps(reachable_urls(s.host_urls))})

    def _give_up(self, s: Session) -> None:
        """Nobody took the session: dissolve it and put its members' tickets back, so they are matched anew."""
        log.warning("session %d: no member reported as host, giving it up", s.id)
        del self.sessions[s.id]
        for m in s.members.values():
            if m.ticket:
                self.tickets[m.ticket.id] = m.ticket

    # ---- reports from the games -----------------------------------------------------------------
    def report_host_success(self, profile_id: str, sid: int, urls: list[str]) -> None:
        s = self.sessions.get(sid)
        if s is None or profile_id not in s.members:
            log.warning("ReportHostSuccess from %s for unknown session %d", profile_id, sid)
            return
        s.host, s.host_urls, s.host_candidate = profile_id, list(urls), None
        host = s.members[profile_id]
        host.state, host.urls, host.asked_at = JOINED, list(urls), None
        log.info("session %d: %s hosts (%d station URLs)", sid, profile_id, len(urls))
        for m in list(s.members.values()):
            if m.profile_id != profile_id and m.state == PENDING:
                m.asked = 0
                self._send_join(s, m)
        for t in sorted(self.tickets.values(), key=lambda t: t.id):
            self._match(t)

    def report_join_success(self, profile_id: str, sid: int) -> None:
        s = self.sessions.get(sid)
        if s and profile_id in s.members:
            m = s.members[profile_id]
            m.state, m.asked_at = JOINED, None
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
        if s.type != SESSION_WORLD:
            return
        if profile_id == s.host:                     # a running session: another member takes over
            s.host, s.host_urls = None, []
            new = next((m for m in s.members.values() if m.state == JOINED), next(iter(s.members.values())))
            s.host_candidate, new.asked_at = new.profile_id, None
            log.info("session %d: host left, asking %s to host", sid, new.profile_id)
            self.notify(new.profile_id, CODE_BECOME_HOST, {"host_profile_id": new.profile_id, "session_id": sid,
                                                           "session_type": s.type})
        elif profile_id == s.host_candidate and s.host is None:    # it never hosted: ask the next one
            new = next(iter(s.members.values()))
            new.asked = 0
            self._ask_host(s, new)

    def on_disconnect(self, profile_id: str) -> None:
        """The player's game is gone (NEX connection closed): out of every session, tickets cancelled."""
        self.cancel_all_tickets(profile_id)
        for sid in [sid for sid, s in self.sessions.items() if profile_id in s.members]:
            self.leave(profile_id, sid)
        self.reserved.pop(profile_id, None)

    # ---- timer ----------------------------------------------------------------------------------
    def tick(self) -> None:
        """Every second (NEX server timers): ask again where a game did not react to code 1 or 2."""
        now = self.clock()
        for s in list(self.sessions.values()):
            if s.type != SESSION_WORLD:
                continue
            for m in list(s.members.values()):
                if m.asked_at is None or now - m.asked_at < self.resend_after:
                    continue
                if m.profile_id == s.host_candidate and s.host is None:
                    if m.asked < MAX_ASKS:
                        self._ask_host(s, m)
                    else:
                        self._give_up(s)
                        break
                elif s.host and m.state == PENDING:
                    if m.asked < MAX_ASKS:
                        self._send_join(s, m)
                    else:
                        log.warning("session %d: %s never reported joining", s.id, m.profile_id)
                        m.asked_at = None

    # ---- GetSession ----------------------------------------------------------------------------
    def get_session(self, sid: int) -> Session | None:
        return self.sessions.get(sid)
