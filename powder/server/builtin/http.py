"""HTTPS replies the server builds itself when no game-data pack answers (see builtin/__init__.py)."""
from __future__ import annotations

import re

from . import sdk

# (method, path regex) -> fn(match) -> JSON-able reply (None: not ours after all)
ROUTES = [
    ("GET", re.compile(r"/v\d+/applications/([0-9a-fA-F-]{36})/configuration"),
     lambda m: sdk.configuration(m.group(1))),
]


def lookup(method: str, path: str):
    for want, pattern, build in ROUTES:
        m = pattern.fullmatch(path) if want == method else None
        if m:
            reply = build(m)
            if reply is not None:
                return reply
    return None
