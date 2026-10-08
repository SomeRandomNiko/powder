"""HTTPS replies the server builds itself when no game-data pack answers (see builtin/__init__.py)."""
from __future__ import annotations

import hashlib
import re

from . import sdk

EMPTY_ITEMS = b"[]"
# version of the space's item list (the game checks it before using items; {} makes it log out again)
ITEMS_VERSION = {"lastModified": "2016-12-01T00:00:00.000Z",
                 "sha256": hashlib.sha256(EMPTY_ITEMS).hexdigest().upper(),
                 "md5": hashlib.md5(EMPTY_ITEMS).hexdigest().upper()}

# (method, path regex) -> fn(match) -> JSON-able reply (None: not ours after all)
ROUTES = [
    ("GET", re.compile(r"/v\d+/applications/([0-9a-fA-F-]{36})/configuration"),
     lambda m: sdk.configuration(m.group(1))),
    ("GET", re.compile(r"/v1/spaces/[0-9a-fA-F-]{36}/items/all"), lambda m: ITEMS_VERSION),
    ("GET", re.compile(r"/v2/spaces/global/ubiconnect/challenges/api/legacy/empty"), lambda m: []),
    # event types the server wants reported: none needed (the game still sends its events)
    ("GET", re.compile(r"/v4/spaces/[0-9a-fA-F-]{36}/configs/events"), lambda m: {"types": []}),
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
