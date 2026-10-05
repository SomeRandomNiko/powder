"""Messages inside a demux service connection (ownership_service, friends_service, ...).

Each service speaks its own protobuf, framed like the outer stream. Requests carry an id in one of
two places, and the reply echoes it in the same place:

  nested  {1: {1: request_id, <kind>: request}}      most services
  flat    {1: request_id, <kind>: request}            ach_frontend

`kind` (the field number of the request) says which request it is. Answer order:
  1. a handler registered for (service, kind)      code, for answers that depend on the player
  2. the recorded reply for (service, kind)         fixtures/demux.py
  3. nothing (logged as DEMUX UNHANDLED)
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from . import pb

log = logging.getLogger("steep.demux")

NESTED, FLAT = "nested", "flat"


@dataclass
class ServiceRequest:
    service: str
    style: str
    request_id: int
    kind: int
    body: bytes | int                    # the request message of that kind (raw)


def parse_request(service: str, msg: bytes) -> ServiceRequest | None:
    fields = pb.try_decode(msg)
    if not fields:
        return None
    top = pb.first(fields, 1)
    if isinstance(top, bytes):
        sub = pb.try_decode(top)
        if sub and isinstance(pb.first(sub, 1), int):
            kind = next((f for f in sub if f[0] != 1), None)
            if kind:
                return ServiceRequest(service, NESTED, pb.first(sub, 1), kind[0], kind[1])
    if isinstance(top, int):
        kind = next((f for f in fields if f[0] != 1), None)
        if kind:
            return ServiceRequest(service, FLAT, top, kind[0], kind[1])
    return None


def with_request_id(reply: bytes, request_id: int) -> bytes:
    """A recorded reply with its request id replaced (same place as in the request)."""
    fields = pb.decode(reply)
    top = pb.first(fields, 1)
    if isinstance(top, bytes):
        sub = pb.decode(top)
        return pb.encode(pb.replace(fields, 1, pb.encode(pb.replace(sub, 1, request_id))))
    return pb.encode(pb.replace(fields, 1, request_id))


def make_reply(req: ServiceRequest, kind: int, body) -> bytes:
    """A reply in the request's style: `body` is the reply message for field `kind`."""
    if req.style == NESTED:
        return pb.encode([(1, [(1, req.request_id), (kind, body)])])
    return pb.encode([(1, req.request_id), (kind, body)])


Handler = Callable[["ServiceContext", ServiceRequest], "bytes | None"]
HANDLERS: dict[tuple[str, int], Handler] = {}


def handler(service: str, kind: int):
    """Register code for one service request; return the full reply (see make_reply) or None to
    fall through to the recording."""
    def deco(fn: Handler) -> Handler:
        HANDLERS[(service, kind)] = fn
        return fn
    return deco


@dataclass
class ServiceContext:
    services: object                     # steepserver.services.Services
    profile_id: str | None
    seen: Counter                        # (service, kind) -> requests answered so far (per connection)


def answer(ctx: ServiceContext, service: str, msg: bytes) -> bytes | None:
    req = parse_request(service, msg)
    if req is None:
        log.warning("DEMUX UNPARSED %s message (%dB): %s", service, len(msg), msg[:48].hex())
        return None
    fn = HANDLERS.get((service, req.kind))
    if fn:
        out = fn(ctx, req)
        if out is not None:
            log.info("demux %s #%d kind %d -> handler (%dB)", service, req.request_id, req.kind, len(out))
            return out
    nth = ctx.seen[(service, req.kind)]
    ctx.seen[(service, req.kind)] += 1
    recorded = ctx.services.assets.demux.reply(service, req.kind, nth)
    if recorded is None:
        log.warning("DEMUX UNHANDLED %s #%d kind %d (%dB request)", service, req.request_id, req.kind, len(msg))
        return None
    log.info("demux %s #%d kind %d -> recorded (%dB)", service, req.request_id, req.kind, len(recorded))
    return with_request_id(recorded, req.request_id)
