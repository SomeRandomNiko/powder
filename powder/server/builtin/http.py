"""HTTPS replies the server builds itself when no game-data pack answers (see builtin/__init__.py)."""
from __future__ import annotations

import json
import re

from . import connect_ui, sdk

# Version of the space's item list (GET items/all?fields=lastModified,sha256,md5). The game compares it with
# the list it ships with: when it matches, the game uses its own copy; otherwise it downloads the list and,
# with anything else than Ubisoft's list, logs out again right after going online (as with {}). These are
# the fingerprint of that shipped list (no list content); the full list is never needed.
ITEMS_VERSION = {"lastModified": "2025-03-24T15:53:59.194Z",
                 "sha256": "F8F0FBB89CC430475115C1B54243D3B3779C29032C7F00EA670DD4E078C85A2C",
                 "md5": "AAA894E0E01921DF01C683B64B10DF87"}


def items_all(m, query) -> dict | None:
    return ITEMS_VERSION if "fields" in query else None


# (method, path regex) -> fn(match, query) -> JSON-able reply or exact bytes (None: not ours after all)
ROUTES = [
    ("GET", re.compile(r"/v\d+/applications/([0-9a-fA-F-]{36})/configuration"),
     lambda m, q: sdk.configuration(m.group(1))),
    ("GET", re.compile(r"/v1/spaces/[0-9a-fA-F-]{36}/items/all"), items_all),
    ("GET", re.compile(r"/v2/spaces/global/ubiconnect/challenges/api/legacy/empty"), lambda m, q: []),
    # event types the server wants reported: none needed (the game still sends its events)
    ("GET", re.compile(r"/v4/spaces/[0-9a-fA-F-]{36}/configs/events"), lambda m, q: {"types": []}),
    # which items each owned DLC grants: none without a pack ({} makes the game log out)
    ("GET", re.compile(r"/v1/spaces/[0-9a-fA-F-]{36}/configs/primarystore"), lambda m, q: {"mappings": []}),
    # Connect's in-game overlay settings, when the overlay has no cached copy (it never has)
    ("GET", re.compile(r"/v4/spaces/global/ubiconnect/configcache/api/postauth"),
     lambda m, q: connect_ui.postauth(q)),
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
