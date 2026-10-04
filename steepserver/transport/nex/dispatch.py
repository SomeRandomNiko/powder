"""RMC request -> reply. Resolution order:
  1. plugin hook   (plugins.before_rmc)        may answer the call itself
  2. handler       (transport/nex/handlers/*)  stateful game logic; may raise Fallthrough
  3. recording     (assets/fixtures-rmc.json)  the real server's reply
  4. error         Core / 2                    logged as UNHANDLED so we learn what to add

Only calls whose answer depends on per-player state or live ids get a handler. Everything else is
answered from the recordings, which are the real server's bytes (hand-built guesses of these
structures were proven wrong by the captures).
"""
from __future__ import annotations

import inspect
import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import rmc_codec
from .connection import PrudpConnection
from .errors import Fallthrough, RmcError
from .handlers import HANDLERS
from .rmc_codec import Message, Reader

log = logging.getLogger("steep.rmc")


@dataclass
class NexContext:
    """What a handler gets besides the params: the services and the calling connection."""
    services: object              # services.Services
    conn: PrudpConnection
    station_url: str

    @property
    def session(self):
        return self.conn.session

    @property
    def profile_id(self) -> str | None:
        return self.conn.session.profile_id if self.conn.session else None

    @property
    def profile_guid(self) -> uuid.UUID:
        if not self.conn.session:
            raise Fallthrough()
        return uuid.UUID(self.conn.session.profile_id)


Handler = Callable[[NexContext, Reader], bytes | Awaitable[bytes]]   # sync or async


class RmcDispatcher:
    def __init__(self, services, plugins=None, handlers: dict[str, Handler] | None = None):
        self.services = services
        self.plugins = plugins
        self.handlers = dict(HANDLERS if handlers is None else handlers)

    async def dispatch(self, ctx: NexContext, msg: Message) -> bytes:
        method, params = msg.method, msg.body
        if self.plugins is not None:
            out = await self.plugins.run_before_rmc(method, ctx, params)
            if out is not None:
                return self._reply(msg, out, "plugin")

        fn = self.handlers.get(method)
        if fn is not None:
            try:
                out = fn(ctx, Reader(params))
                if inspect.isawaitable(out):
                    out = await out
                return self._reply(msg, out, "handler")
            except Fallthrough:
                pass
            except RmcError as e:
                log.warning("#%d %s -> error %s/%d", msg.call_id, method, e.name, e.code)
                return rmc_codec.build_error(msg.protocol, msg.call_id, e.name, e.code)
            except Exception:  # noqa: BLE001 - a buggy handler must not kill the connection
                log.exception("#%d %s: handler failed, falling back to the recording", msg.call_id, method)

        hit = self.services.assets.rmc.lookup(method, params)
        if hit is not None:
            out, how = hit
            return self._reply(msg, out, f"recorded:{how}")

        log.warning("#%d %s -> UNHANDLED (params %dB: %s)", msg.call_id, method, len(params), params[:64].hex())
        return rmc_codec.build_error(msg.protocol, msg.call_id, "Core", 2)

    @staticmethod
    def _reply(msg: Message, outputs: bytes, how: str) -> bytes:
        log.info("#%d %s -> %s (%dB)", msg.call_id, msg.method, how, len(outputs))
        return rmc_codec.build_response(msg.protocol, msg.call_id, msg.method, outputs)
