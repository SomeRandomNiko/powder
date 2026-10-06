"""NEX method handlers, one module per protocol. Each module exports HANDLERS = {method: fn}.

A handler is fn(ctx: NexContext, params: Reader) -> output bytes. Add a module here and list it below.
"""
from . import gamestorage, login, matchmaking, profile, stats

HANDLERS = {}
for _module in (login, gamestorage, stats, matchmaking, profile):
    HANDLERS.update(_module.HANDLERS)
