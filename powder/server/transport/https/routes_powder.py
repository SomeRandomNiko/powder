"""The server's own endpoints (not Ubisoft's), under /_powder/. Used by the launcher; the web admin will
build on them. No authentication yet: they expose only player names and connection state, and the server
is meant for a LAN/VPN of friends. Add auth before exposing a server to the internet.

  GET /_powder/status   who is connected: Ubisoft Connect (demux) clients and game (NEX) sessions
  GET /_powder/ca.pem   the local CA certificate (public part), for clients that must trust it
  GET /_powder/overlay/powder.js   the Powder part of Ubisoft Connect's in-game overlay
"""
from __future__ import annotations

from aiohttp import web

from ....version import __version__
from ...builtin import connect_ui
from .keys import CA_PEM, HOW, SERVICES


async def status(request: web.Request) -> web.Response:
    services = request.app[SERVICES]
    connect = [{"player": c.player_name, "peer": c.peer, "services": sorted(set(c.channels.values()))}
               for c in services.connect_clients if c.profile_id]
    game = [{"player": s.player.name, "nex": s.nex is not None}
            for s in services.sessions.by_player.values() if s.nex is not None]
    request[HOW] = "powder-status"
    return web.json_response({"server": "powder", "version": __version__,
                              "players": len(services.repos.players.all()),
                              "gamedata": not services.assets.empty,
                              "connect": connect, "game": game})


async def ca_pem(request: web.Request) -> web.StreamResponse:
    path = request.app.get(CA_PEM)
    request[HOW] = "powder-ca"
    if path is None or not path.exists():
        return web.Response(status=404, text="no CA")
    return web.Response(body=path.read_bytes(), content_type="application/x-pem-file")


async def overlay_part(request: web.Request) -> web.Response:
    """The Powder part of Ubisoft Connect's in-game overlay (builtin/connect_ui.py); the overlay loads it
    cross-origin, like Ubisoft's parts from their CDN."""
    request[HOW] = "powder-overlay"
    return web.Response(text=connect_ui.POWDER_PART_JS, content_type="application/javascript",
                        headers={"Access-Control-Allow-Origin": "*"})


def add_routes(app: web.Application) -> None:
    app.router.add_get("/_powder/status", status)
    app.router.add_get("/_powder/ca.pem", ca_pem)
    app.router.add_get(connect_ui.POWDER_PART_PATH, overlay_part)
