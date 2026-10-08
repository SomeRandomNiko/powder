"""HTTPS replies the server builds itself when no game-data pack answers (see builtin/__init__.py)."""
from __future__ import annotations

import hashlib
import json
import re

from . import sdk

# The space's item list. With ?fields=lastModified,sha256,md5 the game asks for its version, and downloads
# the list itself when the hashes differ from its cached copy; both answers must agree ({} for the
# version made the game log out again right after its NEX login).
ITEMS = json.dumps({"items": []}).encode()
ITEMS_VERSION = {"lastModified": "2016-12-01T00:00:00.000Z",
                 "sha256": hashlib.sha256(ITEMS).hexdigest().upper(),
                 "md5": hashlib.md5(ITEMS).hexdigest().upper()}


def items_all(m, query) -> dict | bytes:
    return ITEMS_VERSION if "fields" in query else ITEMS


# (method, path regex) -> fn(match, query) -> JSON-able reply or exact bytes (None: not ours after all)
ROUTES = [
    ("GET", re.compile(r"/v\d+/applications/([0-9a-fA-F-]{36})/configuration"),
     lambda m, q: sdk.configuration(m.group(1))),
    ("GET", re.compile(r"/v1/spaces/[0-9a-fA-F-]{36}/items/all"), items_all),
    ("GET", re.compile(r"/v2/spaces/global/ubiconnect/challenges/api/legacy/empty"), lambda m, q: []),
    # event types the server wants reported: none needed (the game still sends its events)
    ("GET", re.compile(r"/v4/spaces/[0-9a-fA-F-]{36}/configs/events"), lambda m, q: {"types": []}),
]


# Ubisoft Connect's UI settings (which UI parts to load from Ubisoft's CDN, feature flags). Connect's
# embedded browser caches them, and Ubisoft often answers "304 Not Modified" (the pack has no body for
# those either). Without a pack: 304 when the browser has a copy, else an error. Never an empty 200: the
# browser would cache it and the UI then fails on every start (dolphin-032) until Connect is online again.
CONNECT_UI_SETTINGS = re.compile(r"/v\d+/spaces/[^/]+/(global/)?(ubiconnect/configcache/.*|parameters)")


def keep_cached(path: str) -> bool:
    return CONNECT_UI_SETTINGS.fullmatch(path) is not None


def lookup(method: str, path: str, query=None):
    for want, pattern, build in ROUTES:
        m = pattern.fullmatch(path) if want == method else None
        if m:
            reply = build(m, query or {})
            if reply is not None:
                return reply
    return None


def body(reply) -> bytes:
    return reply if isinstance(reply, bytes) else json.dumps(reply).encode()
