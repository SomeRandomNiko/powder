"""The server's own endpoints (not Ubisoft's), under /_powder/. Used by the launcher; the web admin will
build on them. No authentication yet: they expose only player names and connection state, and the server
is meant for a LAN/VPN of friends. Add auth before exposing a server to the internet.

  GET /_powder/status   who is connected: Ubisoft Connect (demux) clients and game (NEX) sessions
  GET /_powder/ca.pem   the local CA certificate (public part), for clients that must trust it
"""
from __future__ import annotations

from aiohttp import web

from .keys import CA_PEM, HOW, SERVICES

SERVER_VERSION = "0.4"


async def status(request: web.Request) -> web.Response:
    services = request.app[SERVICES]
    connect = [{"player": c.player_name, "peer": c.peer, "services": sorted(set(c.channels.values()))}
               for c in services.connect_clients if c.profile_id]
    game = [{"player": s.player.name, "nex": s.nex is not None}
            for s in services.sessions.by_player.values() if s.nex is not None]
    request[HOW] = "steep-status"
    return web.json_response({"server": "powder", "version": SERVER_VERSION,
                              "players": len(services.repos.players.all()),
                              "connect": connect, "game": game})


async def ca_pem(request: web.Request) -> web.StreamResponse:
    path = request.app.get(CA_PEM)
    request[HOW] = "powder-ca"
    if path is None or not path.exists():
        return web.Response(status=404, text="no CA")
    return web.Response(body=path.read_bytes(), content_type="application/x-pem-file")


def add_routes(app: web.Application) -> None:
    app.router.add_get("/_powder/status", status)
    app.router.add_get("/_powder/ca.pem", ca_pem)
