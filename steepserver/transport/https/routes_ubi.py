"""UbiServices endpoints with real logic. Everything else is served from recordings (app.py fallback)."""
from __future__ import annotations

import json
import logging

from aiohttp import web

from ...constants import SPACE_ID
from ...core.identity import LoginRequest, parse_authorization
from ...services.accounts import AuthError
from ...services.economy import EconomyError
from ...services.seed import captured_session
from ...util import iso_now
from .keys import FALLBACK, HOW, NEX_PUBLIC_KEY, SERVICES, SESSION

log = logging.getLogger("steep.https")
JSON = "application/json; charset=utf-8"


def json_response(request: web.Request, data, how: str, status: int = 200) -> web.Response:
    request[HOW] = how
    return web.Response(body=json.dumps(data).encode(), status=status, headers={"Content-Type": JSON})


def client_ip(request: web.Request) -> str:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return peer[0] if peer else "127.0.0.1"


# ---- login --------------------------------------------------------------------------------------
async def create_session(request: web.Request) -> web.Response:
    """POST /v3/profiles/sessions. The game authenticates with `uplaypc_v1 t=<Connect ticket>`; we
    issue our own ticket, which it then sends as `Ubi_v1 t=` and as the NEX LoginWithToken token."""
    services = request.app[SERVICES]
    scheme, value = parse_authorization(request.headers.get("Authorization"))
    try:
        session = services.accounts.login(LoginRequest(client_ip(request), scheme, value, dict(request.headers)))
    except AuthError as e:
        return json_response(request, {"errorCode": 1, "message": str(e), "httpCode": 401}, "refused", 401)
    p = session.player
    body = dict(captured_session(services.assets.http) or {"platformType": "uplay", "environment": "Prod",
                                                             "sessionKey": "", "rememberMeTicket": None})
    body.update(profileId=p.profile_id, userId=p.user_id, nameOnPlatform=p.name, spaceId=SPACE_ID,
                ticket=session.ticket, sessionId=session.session_id, expiration=iso_now(3 * 3600),
                serverTime=iso_now(), clientIp=session.client_ip)
    await services.events.emit("login", session=session)
    return json_response(request, body, "login")


# ---- NEX discovery --------------------------------------------------------------------------------
async def rendezvous_configuration(request: web.Request) -> web.Response:
    """Points the game at our NEX server and publishes the key that signs its handshake."""
    cfg = request.app[SERVICES].config.server
    return json_response(request, {
        "http_urls": [],
        "prudp_loadbalancer_url": f"prudp:/address={cfg.public_host};port={cfg.nex_port}",
        "prudp_urls": [],
        "http_loadbalancer_url": "",
        "public_key": request.app[NEX_PUBLIC_KEY],
    }, "rendezvous")


# ---- inventory & store ------------------------------------------------------------------------------
def _known_player(request: web.Request) -> str | None:
    pid = request.match_info["profile_id"].lower()
    return pid if request.app[SERVICES].repos.players.get(pid) else None


async def get_inventory(request: web.Request) -> web.StreamResponse:
    pid = _known_player(request)
    if pid is None:                       # someone we have no record of -> recorded reply, if any
        return await request.app[FALLBACK](request)
    return json_response(request, request.app[SERVICES].economy.inventory_reply(pid), "inventory")


async def post_inventory(request: web.Request) -> web.Response:
    """Shop purchase or reward: {"spaceId", "offerId", "offerRevision"}."""
    services = request.app[SERVICES]
    pid = request.match_info["profile_id"].lower()
    session = request.get(SESSION)
    if session and session.profile_id != pid:
        log.warning("inventory POST for %s by session of %s", pid, session.profile_id)
    try:
        body = await request.json()
        services.accounts.player(pid)
        reply, changes = services.economy.apply_offer(pid, body["offerId"], body.get("offerRevision"))
    except EconomyError as e:
        log.warning("inventory POST %s refused: %s", pid, e.message)
        return json_response(request, e.body(), "economy-error", e.status)
    except (ValueError, KeyError) as e:
        return json_response(request, {"errorCode": 1, "message": f"bad request: {e}", "httpCode": 400},
                             "bad-request", 400)
    await services.events.emit("purchase", profile_id=pid, offer_id=body["offerId"], changes=changes)
    return json_response(request, reply, "economy")


async def get_offers(request: web.Request) -> web.StreamResponse:
    """Store catalogue. The exact recorded page if we have it, else filtered from the catalogue."""
    services = request.app[SERVICES]
    hit = services.assets.http.lookup("GET", request.host.split(":")[0], request.path_qs)
    if hit:
        return await request.app[FALLBACK](request)
    q = request.query
    offers = services.assets.catalog.query(tags=q.get("tags"), type=q.get("type"), item_ids=q.get("itemIds"),
                                           offset=int(q.get("offset", 0)), limit=int(q.get("limit", 100)))
    return json_response(request, {"offers": offers}, f"catalog({len(offers)})")


def add_routes(app: web.Application) -> None:
    app.router.add_post("/v3/profiles/sessions", create_session)
    app.router.add_get("/v1/spaces/{space}/sandboxes/{sandbox}/rendezvous/configuration", rendezvous_configuration)
    app.router.add_get("/v1/profiles/{profile_id}/inventory", get_inventory)
    app.router.add_post("/v1/profiles/{profile_id}/inventory", post_inventory)
    app.router.add_get("/v1/spaces/{space}/offers", get_offers)
