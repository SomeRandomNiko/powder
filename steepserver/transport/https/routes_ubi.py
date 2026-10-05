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
    # What the game got from Ubisoft Connect (its platform ticket) says a lot about whether Connect
    # still works, e.g. with Ubisoft's servers unreachable. Log its shape only, never its content.
    log.info("login credentials: scheme=%s, ticket %s, app=%s", scheme or "(none)",
             f"{len(value)} chars" if value else "MISSING", request.headers.get("Ubi-AppId", "(none)"))
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


# ---- profile entities (rider card, favorites) ------------------------------------------------------
def _int(q, name: str, default: int) -> int:
    try:
        return int(q.get(name, default))
    except ValueError:
        return default


async def get_profile_entities(request: web.Request) -> web.StreamResponse:
    """GET /v2/profiles/{pid}/entities?type=&tags=&offset=&limit= (own player from the DB)."""
    entities = request.app[SERVICES].entities
    pid = request.match_info["profile_id"]
    q = request.query
    if not entities.is_local(pid):
        return await request.app[FALLBACK](request)
    items = entities.for_profile(pid, q.get("type"), q.get("tags"), _int(q, "offset", 0), _int(q, "limit", 100))
    return json_response(request, {"entities": items}, f"entities({len(items)})")


async def get_entities_batch(request: web.Request) -> web.StreamResponse:
    """GET /v2/profiles/entities?profileIds=a,b&type=PROFILE (rider cards of friends and self)."""
    q = request.query
    ids = [p for p in q.get("profileIds", "").split(",") if p]
    if not ids:
        return await request.app[FALLBACK](request)
    items = request.app[SERVICES].entities.for_profiles(ids, q.get("type"), _int(q, "offset", 0),
                                                         _int(q, "limit", 100))
    return json_response(request, {"entities": items}, f"entities({len(items)})")


def _owner(request: web.Request) -> str | None:
    session = request.get(SESSION)
    return session.profile_id if session else None


async def get_entity(request: web.Request) -> web.StreamResponse:
    e = request.app[SERVICES].entities.get(request.match_info["entity_id"])
    return json_response(request, e, "entity") if e else await request.app[FALLBACK](request)


async def put_entity(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        e = request.app[SERVICES].entities.put(request.match_info["entity_id"], body, _owner(request))
    except ValueError as err:
        return json_response(request, {"errorCode": 1, "message": str(err), "httpCode": 400}, "bad-request", 400)
    return json_response(request, e, f"entity-saved(rev {e['revision']})")


async def post_entity(request: web.Request) -> web.Response:
    owner = _owner(request) or request.app[SERVICES].default_player_id()
    try:
        e = request.app[SERVICES].entities.create(await request.json(), owner)
    except ValueError as err:
        return json_response(request, {"errorCode": 1, "message": str(err), "httpCode": 400}, "bad-request", 400)
    return json_response(request, e, "entity-created")


async def delete_entity(request: web.Request) -> web.Response:
    deleted = request.app[SERVICES].entities.delete(request.match_info["entity_id"])
    return json_response(request, {}, "entity-deleted" if deleted else "entity-absent")


async def put_populations_data(request: web.Request) -> web.StreamResponse:
    """Keep what the game reports about the player's populations; answer like the recording."""
    session = request.get(SESSION)
    if session and request.can_read_body:
        try:
            request.app[SERVICES].repos.kv.set(session.profile_id, "populations_data", await request.json())
        except ValueError:
            pass
    return await request.app[FALLBACK](request)


def add_routes(app: web.Application) -> None:
    app.router.add_post("/v3/profiles/sessions", create_session)
    app.router.add_get("/v1/spaces/{space}/sandboxes/{sandbox}/rendezvous/configuration", rendezvous_configuration)
    app.router.add_get("/v1/profiles/{profile_id}/inventory", get_inventory)
    app.router.add_post("/v1/profiles/{profile_id}/inventory", post_inventory)
    app.router.add_get("/v1/spaces/{space}/offers", get_offers)
    app.router.add_get("/v2/profiles/entities", get_entities_batch)
    app.router.add_post("/v2/profiles/entities", post_entity)
    app.router.add_get("/v2/profiles/entities/{entity_id}", get_entity)
    app.router.add_put("/v2/profiles/entities/{entity_id}", put_entity)
    app.router.add_delete("/v2/profiles/entities/{entity_id}", delete_entity)
    app.router.add_get("/v2/profiles/{profile_id}/entities", get_profile_entities)
    app.router.add_put("/v1/profiles/me/populations/data", put_populations_data)
