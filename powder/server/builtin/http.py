"""HTTPS replies the server builds itself when no game-data pack answers (see builtin/__init__.py)."""
from __future__ import annotations

import re

from . import sdk

# (method, path regex) -> fn(match) -> JSON-able reply (None: not ours after all)
ROUTES = [
    ("GET", re.compile(r"/v\d+/applications/([0-9a-fA-F-]{36})/configuration"),
     lambda m: sdk.configuration(m.group(1))),
]


# Ubisoft Connect's UI settings (which UI parts to load from Ubisoft's CDN, feature flags). Connect's
# embedded browser caches them, and Ubisoft often answers "304 Not Modified" (the pack has no body for
# those either). Without a pack: 304 when the browser has a copy, else an error. Never an empty 200: the
# browser would cache it and the UI then fails on every start (dolphin-032) until Connect is online again.
CONNECT_UI_SETTINGS = re.compile(r"/v\d+/spaces/[^/]+/(global/)?(ubiconnect/configcache/.*|parameters)")


def keep_cached(path: str) -> bool:
    return CONNECT_UI_SETTINGS.fullmatch(path) is not None


def lookup(method: str, path: str):
    for want, pattern, build in ROUTES:
        m = pattern.fullmatch(path) if want == method else None
        if m:
            reply = build(m)
            if reply is not None:
                return reply
    return None
