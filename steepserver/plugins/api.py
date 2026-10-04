"""The interface a plugin gets. Plugins are .py files in the server's plugins/ folder that define

    def setup(api):                       # api: PluginAPI
        api.on("purchase", on_purchase)
        api.before_rmc("PlayerStatusProtocol::GetProfile_V1", my_profile_reply)

Hooks may be plain functions or async functions. Exceptions are logged and never reach the game.
Keep plugins on this API (not on internal modules) so they survive server updates; API_VERSION is
bumped on incompatible changes.

Events (api.on): "login"(session), "ws_connected"(session), "nex_connected"(session),
                 "purchase"(profile_id, offer_id, changes)
before_http(method, path_glob, fn):  fn(request) -> aiohttp web.Response | None   (None = continue)
before_rmc(method, fn):              fn(ctx, params: bytes) -> output bytes | None (None = continue)
"""
from __future__ import annotations

import logging
from typing import Callable

API_VERSION = 1


class PluginAPI:
    def __init__(self, manager, name: str):
        self._manager = manager
        self.name = name
        self.services = manager.services          # accounts, economy, stats, sessions, repos, ...
        self.config = manager.services.config
        self.log = logging.getLogger(f"steep.plugin.{name}")
        self.version = API_VERSION

    def on(self, event: str, fn: Callable) -> None:
        self.services.events.on(event, fn)

    def before_http(self, method: str, path_glob: str, fn: Callable) -> None:
        """method: "GET"/"POST"/... or "*"; path_glob: fnmatch pattern on the URL path."""
        self._manager.http_hooks.append((method.upper(), path_glob, fn, self.name))

    def before_rmc(self, method: str, fn: Callable) -> None:
        """method: full NEX method name, e.g. "PlayerStats2Protocol::GetStatisticGroup_V1"."""
        self._manager.rmc_hooks.setdefault(method, []).append((fn, self.name))

    # per-plugin persistent storage (SQLite kv, scope "plugin:<name>")
    def load(self, key: str, default=None):
        return self.services.repos.kv.get(f"plugin:{self.name}", key, default)

    def save(self, key: str, value) -> None:
        self.services.repos.kv.set(f"plugin:{self.name}", key, value)
