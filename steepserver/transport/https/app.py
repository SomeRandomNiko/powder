"""HTTPS server for every UbiServices/Olympus hostname (the hosts file points them all at us).

Request pipeline (outermost first):
  log_middleware      access log + keeps uploaded remotelog/events bodies (they explain client errors)
  plugin_middleware   plugins.before_http hooks may answer a request themselves
  session_middleware  `Authorization: Ubi_v1 t=<ticket>` -> request[SESSION]
  routes              routes_ubi.py (real logic), websocket.py
  fallback            recorded reply (assets/fixtures.json) or `{}` + UNMATCHED warning
Routes match on the path only; the host matters only for the recordings.
"""
from __future__ import annotations

import datetime
import logging
import ssl
from pathlib import Path

from aiohttp import web

from ... import tls
from ...core.identity import parse_authorization
from . import routes_ubi
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
    (log.warning if how == "UNMATCHED" else log.info)(line)
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
    """Recorded reply for anything without real logic."""
    hit = request.app[SERVICES].assets.http.lookup(request.method, host_of(request), request.path_qs)
    if hit is None:
        request[HOW] = "UNMATCHED"
        return web.Response(body=b"{}", headers={"Content-Type": routes_ubi.JSON})
    fixture, tier = hit
    request[HOW] = f"recorded:{tier}"
    return web.Response(body=fixture.body, status=fixture.status,
                        headers={"Content-Type": fixture.content_type})


def build_app(services, plugins=None, *, nex_public_key: str = "") -> web.Application:
    app = web.Application(middlewares=[log_middleware, plugin_middleware, session_middleware],
                          client_max_size=16 * 1024 * 1024)
    app[SERVICES] = services
    app[PLUGINS] = plugins
    app[NEX_PUBLIC_KEY] = nex_public_key
    app[DATA_DIR] = Path(services.config.data_dir)
    app[FALLBACK] = fallback
    routes_ubi.add_routes(app)
    app.router.add_get("/v2/websocket", websocket)
    app.router.add_route("*", "/{tail:.*}", fallback)      # last: everything else
    return app


def tls_context(cert_file: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_file))
    return ctx


async def start_crl(crl, port: int) -> web.AppRunner:
    """Plain HTTP on 127.0.0.1: the local CA's revocation list (Windows checks it for Ubisoft Connect).
    `crl` is a callable returning the DER bytes."""
    async def serve(request: web.Request) -> web.Response:
        return web.Response(body=crl(), content_type="application/pkix-crl")
    app = web.Application()
    app.router.add_get(tls.CRL_PATH, serve)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner


async def start(app: web.Application, bind: str, port: int, cert_file: Path | None) -> web.AppRunner:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, bind, port, ssl_context=tls_context(cert_file) if cert_file else None)
    await site.start()
    return runner
