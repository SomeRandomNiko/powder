"""Typed aiohttp application and request keys shared by the HTTPS modules."""
from pathlib import Path
from typing import Any, Callable

from aiohttp import web

SERVICES = web.AppKey("services", Any)          # services.Services
PLUGINS = web.AppKey("plugins", Any)            # plugins.loader.PluginManager | None
NEX_PUBLIC_KEY = web.AppKey("nex_public_key", str)
DATA_DIR = web.AppKey("data_dir", Path)
FALLBACK = web.AppKey("fallback", Callable)      # recorded-reply handler, usable from routes
CA_PEM = web.AppKey("ca_pem", Path)              # the local CA certificate (public part) for /_powder/ca.pem

# per-request values
HOW = web.RequestKey("how", str)                # how the request was answered (access log)
SESSION = web.RequestKey("session", Any)        # core.sessions.PlayerSession | None
