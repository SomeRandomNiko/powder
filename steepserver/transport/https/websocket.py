"""UbiServices notification WebSocket (public-ws-ubiservices.ubi.com/v2/websocket).

The client's JobInitiateConnection waits for US_CONNECTION_ESTABLISHED with a connectionId; without
it NEX Register fails ("Failed to get the connection Id ..."). Envelope format from FUN_7ff661c884e0.
Open sockets are registered on the player's session, so services can push notifications later
(SessionRegistry.push_ws).
"""
from __future__ import annotations

import logging
import uuid

from aiohttp import WSMsgType, web

from ...core.identity import parse_authorization
from ...util import iso_now
from .keys import HOW, SERVICES
from .routes_ubi import client_ip

log = logging.getLogger("steep.https")


async def websocket(request: web.Request) -> web.StreamResponse:
    services = request.app[SERVICES]
    offered = request.headers.get("Sec-WebSocket-Protocol", "")
    first = offered.split(",")[0].strip()
    ws = web.WebSocketResponse(protocols=(first,) if first else (), heartbeat=None, autoping=True)
    check = ws.can_prepare(request)
    if not check.ok:
        log.warning("websocket handshake not acceptable; headers: %s", dict(request.headers))
        request[HOW] = "ws-rejected"
        return web.Response(status=400)
    await ws.prepare(request)
    request[HOW] = "websocket"

    _, ticket = parse_authorization(request.headers.get("Authorization"))
    session = services.accounts.session_for_ticket(ticket, client_ip(request))
    connection_id = str(uuid.uuid4())
    await ws.send_json({"notificationType": "US_CONNECTION_ESTABLISHED",
                        "content": {"connectionId": connection_id,
                                    "contactUrl": "wss://public-ws-ubiservices.ubi.com"},
                        "datePosted": iso_now()})
    if session is not None:
        session.websockets.add(ws)
        await services.events.emit("ws_connected", session=session)
    log.info("websocket open (%s) for %s", connection_id, session.player.name if session else "unknown")
    try:
        async for msg in ws:
            if msg.type in (WSMsgType.TEXT, WSMsgType.BINARY):
                log.info("websocket <- %r", msg.data[:200])
    finally:
        if session is not None:
            session.websockets.discard(ws)
        log.info("websocket closed (%s)", connection_id)
    return ws
