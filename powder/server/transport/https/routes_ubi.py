"""UbiServices endpoints with real logic (login, inventory, store, entities). Player-related endpoints
that only need a plausible answer are in routes_profile.py; game-wide data comes from the game-data
pack (app.py fallback)."""
from __future__ import annotations

import json
import logging
import secrets

from aiohttp import web

from ...constants import APP_ID, CONNECT_SPACE_ID, SPACE_ID
from ...core.sessions import ubi_shaped_ticket
from ...core.identity import LoginRequest, parse_authorization
from ...services.accounts import AuthError
from ...services.economy import EconomyError
from ...util import iso_now
from .keys import HOW, NEX_PUBLIC_KEY, SERVICES, SESSION

log = logging.getLogger("powder.https")
JSON = "application/json; charset=utf-8"


def json_response(request: web.Request, data, how: str, status: int = 200) -> web.Response:
    request[HOW] = how
    return web.Response(body=json.dumps(data).encode(), status=status, headers={"Content-Type": JSON})


def client_ip(request: web.Request) -> str:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return peer[0] if peer else "127.0.0.1"


def auth_refused(request: web.Request, message: str) -> web.Response:
    """A refused sign-in, shaped like UbiServices' (Ubisoft Connect then asks for email and password)."""
    return json_response(request, {"message": message, "errorCode": 1, "httpCode": 401,
                                   "errorContext": "Profiles Client Facade Service", "moreInfo": "",
                                   "transactionTime": iso_now(), "transactionId": secrets.token_hex(16)},
                         "refused", 401)


# ---- login --------------------------------------------------------------------------------------
async def create_session(request: web.Request) -> web.Response:
    """POST /v3/profiles/sessions. The game authenticates with `uplaypc_v1 t=<Connect ticket>` (or
    `Basic` with empty credentials when Connect has none); we issue our own ticket, which it then
    sends as `Ubi_v1 t=` and as the NEX LoginWithToken token. Ubisoft Connect logs in here too
    (`rm_v1` remember-me ticket, `Ubi_v1` refresh, or `Basic` email:password on a fresh sign-in):
    the account service decides who it is (services/accounts.py). Passwords are never logged or
    stored in clear."""
    services = request.app[SERVICES]
    scheme, value = parse_authorization(request.headers.get("Authorization"))
    # What the game got from Ubisoft Connect (its platform ticket) says a lot about whether Connect
    # still works, e.g. with Ubisoft's servers unreachable. Log its shape only, never its content.
    log.info("login credentials: scheme=%s, ticket %s, app=%s", scheme or "(none)",
             f"{len(value)} chars" if value else "MISSING", request.headers.get("Ubi-AppId", "(none)"))
    ip = client_ip(request)
    if services.login_failures.blocked(ip):
        log.warning("login from %s refused: too many failed sign-ins, try again in a few minutes", ip)
        return auth_refused(request, "Too many failed sign-ins, try again in a few minutes")
    try:
        session = services.accounts.login(LoginRequest(ip, scheme, value, dict(request.headers)))
    except AuthError as e:
        if scheme.lower() == "basic":            # a password guess (tickets are not guessable)
            services.login_failures.failed(ip)
        log.warning("login from %s refused: %s", ip, e)
        return auth_refused(request, "Invalid credentials")
    if scheme.lower() == "basic":
        services.login_failures.succeeded(ip)
    p = session.player
    app_id = request.headers.get("Ubi-AppId", "").lower()
    connect = bool(app_id) and app_id != APP_ID
    body = {"platformType": "uplay", "environment": "Prod", "profileId": p.profile_id, "userId": p.user_id,
            "nameOnPlatform": p.name, "ticket": session.ticket, "sessionId": session.session_id,
            "sessionKey": secrets.token_urlsafe(48), "expiration": iso_now(3 * 3600), "serverTime": iso_now(),
            "clientIp": session.client_ip, "clientIpCountry": services.config.server.country,
            "twoFactorAuthenticationTicket": None}
    if connect:
        # Ubisoft Connect (any other app id) keeps the remember-me ticket and signs in with it next time
        # (`rm_v1`), which we accept like any other credential.
        remember = ubi_shaped_ticket(app_id, session.session_id)
        services.accounts.remember(session, remember)
        body.update(spaceId=CONNECT_SPACE_ID, rememberMeTicket=remember,
                    codeGenerationPreference=None, maskedPhone=None)
    else:
        body.update(spaceId=SPACE_ID, rememberMeTicket=None)
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
    if pid is None:                       # someone this server has no record of: owns nothing
        return json_response(request, {"items": []}, "inventory(unknown player)")
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
    """Store catalogue, filtered like the real service (see fixtures/catalog.py)."""
    services = request.app[SERVICES]
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
    items = entities.for_profile(pid, q.get("type"), q.get("tags"), _int(q, "offset", 0), _int(q, "limit", 100))
    return json_response(request, {"entities": items}, f"entities({len(items)})")


async def get_entities_batch(request: web.Request) -> web.StreamResponse:
    """GET /v2/profiles/entities?profileIds=a,b&type=PROFILE (rider cards of friends and self)."""
    q = request.query
    ids = [p for p in q.get("profileIds", "").split(",") if p]
    items = request.app[SERVICES].entities.for_profiles(ids, q.get("type"), _int(q, "offset", 0),
                                                         _int(q, "limit", 100))
    return json_response(request, {"entities": items}, f"entities({len(items)})")


def _owner(request: web.Request) -> str | None:
    session = request.get(SESSION)
    return session.profile_id if session else None


async def get_entity(request: web.Request) -> web.StreamResponse:
    e = request.app[SERVICES].entities.get(request.match_info["entity_id"])
    if e is None:
        return json_response(request, {"errorCode": 1002, "message": "entity not found", "httpCode": 404},
                             "entity-missing", 404)
    return json_response(request, e, "entity")


async def put_entity(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        e = request.app[SERVICES].entities.put(request.match_info["entity_id"], body, _owner(request))
    except ValueError as err:
        return json_response(request, {"errorCode": 1, "message": str(err), "httpCode": 400}, "bad-request", 400)
    return json_response(request, e, f"entity-saved(rev {e['revision']})")


async def post_entity(request: web.Request) -> web.Response:
    """POST /v2/profiles/entities and /v2/profiles/{pid}/entities (e.g. a new player's rider card)."""
    owner = (request.match_info.get("profile_id") or _owner(request)
             or request.app[SERVICES].default_player_id()).lower()
    try:
        e = request.app[SERVICES].entities.create(await request.json(), owner)
    except ValueError as err:
        return json_response(request, {"errorCode": 1, "message": str(err), "httpCode": 400}, "bad-request", 400)
    return json_response(request, e, "entity-created")


async def delete_entity(request: web.Request) -> web.Response:
    deleted = request.app[SERVICES].entities.delete(request.match_info["entity_id"])
    return json_response(request, {}, "entity-deleted" if deleted else "entity-absent")


# ---- Ubisoft Connect ----------------------------------------------------------------------------------
async def internet_check(request: web.Request) -> web.Response:
    """static3.cdn.ubi.com/.../test_internet.txt: Ubisoft Connect shows "no internet connection" unless
    this answers like the real CDN."""
    request[HOW] = "internet-check"
    return web.Response(text="I AM ALIVE!", content_type="text/plain")


async def get_entities_v1(request: web.Request) -> web.StreamResponse:
    """GET /v1/profiles/{pid}/entities?name=upcCloudSaveUplayId_<product>: Ubisoft Connect's cloud-save
    metadata. Saves are not stored on this server (yet), so there is no cloud save: replaying the
    recorded metadata would make Connect offer to "restore" a save it can never download."""
    if request.query.get("name", "").startswith("upcCloudSave"):
        return json_response(request, {"entities": []}, "no-cloud-save")
    return json_response(request, {"entities": []}, "entities-v1")


def add_routes(app: web.Application) -> None:
    app.router.add_get("/v1/profiles/{profile_id}/entities", get_entities_v1)
    app.router.add_get(r"/{prefix:.*}test_internet.txt", internet_check)
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
    app.router.add_post("/v2/profiles/{profile_id}/entities", post_entity)
