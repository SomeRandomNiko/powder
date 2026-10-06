"""Minimal async event bus: services emit, plugins (and later services) listen.

Events emitted today: "login" (session), "purchase" (profile_id, offer_id, changes),
"nex_connected" (session), "ws_connected" (session). Handlers may be sync or async; an exception in
one handler is logged and never breaks the game request that caused the event.
"""
from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from typing import Callable

log = logging.getLogger("powder.events")


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, fn: Callable) -> None:
        self._handlers[event].append(fn)

    async def emit(self, event: str, **kwargs) -> None:
        for fn in list(self._handlers.get(event, ())):
            try:
                result = fn(**kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                log.exception("handler %r for event %r failed", fn, event)
