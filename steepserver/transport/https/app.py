"""HTTPS server for every UbiServices/Olympus hostname (the hosts file points them all at us).

Request pipeline (outermost first):
  log_middleware      access log + keeps uploaded remotelog/events bodies (they explain client errors)
  cors_middleware     CORS for Ubisoft Connect's web UI (preflights, Allow-Origin)
  plugin_middleware   plugins.before_http hooks may answer a request themselves
  session_middleware  `Authorization: Ubi_v1 t=<ticket>` -> request[SESSION]
  routes              routes_ubi.py (real logic), websocket.py
  fallback            recorded reply (assets/fixtures.json) or `{}` + UNMATCHED warning
Routes match on the path only; the host matters only for the recordings.
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path

from aiohttp import web

from ... import tls
from ...constants import APP_ID
from ...core.identity import parse_authorization
from . import routes_steep, routes_ubi
from .keys import DATA_DIR, FALLBACK, HOW, NEX_PUBLIC_KEY, PLUGINS, SERVICES, SESSION
from .routes_ubi import client_ip
from .websocket import websocket

log = logging.getLogger("steep.https")
SAVED_UPLOADS = ("/remotelog", "/profiles/me/events")


def host_of(request: web.Request) -> str:
    return (request.host or "").split(":")[0].lower()


@web.middleware
async def log_middleware(request: web.Request, handler):
    path = request.path
    if request.method == "POST" and path.endswith(SAVED_UPLOADS) and request.can_read_body:
        body = await request.read()
        kind = "remotelog" if path.endswith("/remotelog") else "events"
        bodies = request.app[DATA_DIR] / "bodies"
        bodies.mkdir(parents=True, exist_ok=True)
        (bodies / datetime.datetime.now().strftime(f"{kind}-%Y%m%d-%H%M%S-%f.txt")).write_bytes(body)
    response = await handler(request)
    how = request.get(HOW, "?")
    size = response.content_length if response.content_length is not None else "-"
    line = f"{request.method} {host_of(request)}{request.path_qs[:160]} -> {how} {response.status} ({size}B)"
    if how == "UNMATCHED":
        log.warning(line)
    elif path.startswith("/_steep/"):        # the launcher polls these every few seconds
        log.debug(line)
    else:
        log.info(line)
    return response


CORS_METHODS = "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS"
CORS_EXPOSE = "X-Total-Count, Ubi-TransactionId"


@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Ubisoft Connect's UI is a web page (origin https://connect.cdn.ubisoft.com) calling UbiServices
    from its embedded browser, which needs CORS: answer preflights and allow the origin, like Ubisoft."""
    origin = request.headers.get("Origin")
    if not origin:
        return await handler(request)
    if request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
        request[HOW] = "cors-preflight"
        response = web.Response(status=200)
        response.headers["Access-Control-Allow-Methods"] = CORS_METHODS
        response.headers["Access-Control-Allow-Headers"] = request.headers.get("Access-Control-Request-Headers", "")
    else:
        response = await handler(request)
        if response.prepared:                            # websocket upgrade: headers already sent
            return response
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = CORS_EXPOSE
    return response


@web.middleware
async def plugin_middleware(request: web.Request, handler):
    plugins = request.app.get(PLUGINS)
    if plugins is not None:
        response = await plugins.run_before_http(request)
        if response is not None:
            request[HOW] = "plugin"
            return response
    return await handler(request)


@web.middleware
async def session_middleware(request: web.Request, handler):
    scheme, ticket = parse_authorization(request.headers.get("Authorization"))
    if scheme.lower() == "ubi_v1" and ticket:
        request[SESSION] = request.app[SERVICES].accounts.session_for_ticket(ticket, client_ip(request))
    return await handler(request)


async def fallback(request: web.Request) -> web.Response:
    """Recorded reply for anything without real logic. Requests from Ubisoft Connect (any app id other
    than the game's) try Connect's own recordings first, everything else the game's."""
    assets = request.app[SERVICES].assets
    app_id = request.headers.get("Ubi-AppId", "").lower()
    order = (assets.connect_http, assets.http) if app_id and app_id != APP_ID else (assets.http, assets.connect_http)
    hit = None
    for fixtures in order:
        hit = fixtures.lookup(request.method, host_of(request), request.path_qs)
        if hit:
            break
    if hit is None:
        request[HOW] = "UNMATCHED"
        return web.Response(body=b"{}", headers={"Content-Type": routes_ubi.JSON})
    fixture, tier = hit
    request[HOW] = f"recorded:{tier}"
    return web.Response(body=fixture.body, status=fixture.status,
                        headers={"Content-Type": fixture.content_type})


def build_app(services, plugins=None, *, nex_public_key: str = "") -> web.Application:
    app = web.Application(middlewares=[log_middleware, cors_middleware, plugin_middleware, session_middleware],
                          client_max_size=16 * 1024 * 1024)
    app[SERVICES] = services
    app[PLUGINS] = plugins
    app[NEX_PUBLIC_KEY] = nex_public_key
    app[DATA_DIR] = Path(services.config.data_dir)
    app[FALLBACK] = fallback
    routes_steep.add_routes(app)
    routes_ubi.add_routes(app)
    app.router.add_get("/v2/websocket", websocket)
    app.router.add_route("*", "/{tail:.*}", fallback)      # last: everything else
    return app


async def start_crl(crl, host: str, port: int) -> web.AppRunner:
    """Plain HTTP: the local CA's revocation list (Windows checks it for Ubisoft Connect), reached as
    http://public-ubiservices.ubi.com/steep-ca.crl. `crl` is a callable returning the DER bytes."""
    async def serve(request: web.Request) -> web.Response:
        return web.Response(body=crl(), content_type="application/pkix-crl")
    app = web.Application()
    app.router.add_get(tls.CRL_PATH, serve)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return runner
