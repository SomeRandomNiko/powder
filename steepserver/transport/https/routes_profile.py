"""Player-related UbiServices endpoints answered from the player record (no recordings): the game's and
Ubisoft Connect's profile, social, news and telemetry calls. Most only need a well-formed answer: empty
lists for friends, news and other people's content; neutral values for account details.
"""
from __future__ import annotations

import uuid

from aiohttp import web

from ...constants import SPACE_ID
from ...util import iso_now
from .keys import HOW, SERVICES, SESSION
from .routes_ubi import json_response


def _services(request: web.Request):
    return request.app[SERVICES]


def _me(request: web.Request) -> str | None:
    """The requesting player's profile id (from the ticket, else the default player)."""
    session = request.get(SESSION)
    if session:
        return session.profile_id
    return _services(request).default_player_id()


def _pid(request: web.Request) -> str:
    pid = request.match_info.get("profile_id", "me")
    return _me(request) if pid == "me" else pid.lower()


def _player(request: web.Request, pid: str | None = None):
    pid = pid or _pid(request)
    return _services(request).repos.players.get(pid) if pid else None


def _for_player(build, how):
    """Handler answering build(profile_id) for the {profile_id} in the path ("me" = the requester)."""
    async def handler(request: web.Request) -> web.Response:
        return json_response(request, build(_pid(request)), how)
    return handler


def _ok(payload, how):
    async def handler(request: web.Request) -> web.Response:
        return json_response(request, payload() if callable(payload) else payload, how)
    return handler


# ---- game ---------------------------------------------------------------------------------------
async def populations(request: web.Request) -> web.Response:
    """GET /v1/profiles/me/populations and PUT .../populations/data (the game reports its populations)."""
    services, pid = _services(request), _me(request)
    if request.method == "PUT" and request.can_read_body:
        try:
            services.repos.kv.set(pid, "populations_data", await request.json())
        except ValueError:
            pass
    stored = services.repos.kv.get(pid, "populations_data") if pid else None
    pops = stored.get("populations") if isinstance(stored, dict) else None
    return json_response(request, {"populations": pops or []}, "populations")


async def actions(request: web.Request) -> web.Response:
    """Ubisoft Club actions: the definitions from the game-data pack, for this player (no progress)."""
    pid = _pid(request)
    defs = _services(request).assets.actions
    return json_response(request, {"actions": [{**a, "profileId": pid, "value": 0} for a in defs]},
                         f"actions({len(defs)})")


async def profiles_lookup(request: web.Request) -> web.Response:
    """GET /v3/profiles?profileIds=|userIds=|idOnPlatform=&platformType=: players this server knows."""
    q = request.query
    wanted = {x.lower() for key in ("profileIds", "userIds", "idOnPlatform", "idsOnPlatform", "nameOnPlatform")
              for x in q.get(key, "").split(",") if x}
    out = []
    for p in _services(request).repos.players.all():
        if not wanted or {p.profile_id, p.user_id.lower(), p.name.lower()} & wanted:
            out.append({"profileId": p.profile_id, "userId": p.user_id, "platformType": "uplay",
                        "idOnPlatform": p.user_id, "nameOnPlatform": p.name})
    return json_response(request, {"profiles": out}, f"profiles({len(out)})")


async def user_info(request: web.Request) -> web.Response:
    """GET /v3/users/{id}: country and language only."""
    cfg = _services(request).config.server
    return json_response(request, {"country": cfg.country, "preferredLanguage": "en"}, "user")


async def remotelog(request: web.Request) -> web.Response:
    """The game's error reports (log_middleware keeps the body); acknowledge like the real service."""
    return json_response(request, {"transactionId": str(uuid.uuid4()), "status": "OK",
                                   "statusMessage": "Success"}, "remotelog")


async def no_content(request: web.Request) -> web.Response:
    request[HOW] = "ok"
    return web.Response(status=204)


# ---- Ubisoft Connect ---------------------------------------------------------------------------------
async def users_me(request: web.Request) -> web.Response:
    p = _player(request, _me(request))
    cfg = _services(request).config.server
    name = p.name if p else "Player"
    return json_response(request, {
        "userId": p.user_id if p else _me(request), "username": name, "nameOnPlatform": name,
        "accountIssues": None, "communicationOptIn": False, "communicationThirdPartyOptIn": False,
        "country": cfg.country, "region": None, "dateCreated": p.created_at if p else iso_now(),
        "dateAnonymized": None, "dateOfBirth": "1990-01-01T00:00:00.0000000Z", "email": None,
        "firstName": None, "gender": None, "hasAcceptedLegalOptins": True, "lastName": None,
        "preferredLanguage": "en",
        "status": {"autoGeneratedUsername": False, "dateOfBirthApproximated": False, "invalidEmail": False,
                   "missingRequiredInformation": False, "pendingDeactivation": False,
                   "targetDeactivationDate": None, "recoveringPassword": False, "passwordUpdateRequired": False,
                   "reserved": False, "changeEmailPending": False, "inactiveAccount": False,
                   "generalStatus": "activated", "suspiciousActivity": False, "locked": False, "minor": False}},
        "users-me")


async def users_me_profiles(request: web.Request) -> web.Response:
    p = _player(request, _me(request))
    profiles = [{"profileId": p.profile_id, "userId": p.user_id, "platformType": "uplay",
                 "idOnPlatform": p.user_id, "nameOnPlatform": p.name}] if p else []
    return json_response(request, {"profiles": profiles}, "users-me-profiles")


async def iplocation(request: web.Request) -> web.Response:
    cfg = _services(request).config.server
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return json_response(request, {
        "city": None, "countryCode": cfg.country, "countryName": cfg.country, "ip": peer[0] if peer else "",
        "isp": "", "latitude": "0.000000", "longitude": "0.000000", "organization": "", "regionCode": "",
        "regionName": "", "timeZone": "UTC", "zipCode": None}, "iplocation")


async def online_statuses(request: web.Request) -> web.Response:
    ids = [u for u in request.query.get("UserIds", request.query.get("userIds", "")).split(",") if u]
    services = _services(request)
    online = {s.player.user_id.lower() for s in services.sessions.by_player.values()}
    return json_response(request, {"onlineStatuses": [
        {"connections": [], "onlineStatus": "online" if u.lower() in online else "offline", "userId": u}
        for u in ids]}, "online-statuses")


async def club_title(request: web.Request) -> web.Response:
    pid = _pid(request)
    return json_response(request, {
        "spaceId": request.match_info.get("space", SPACE_ID), "profileId": pid, "owned": False,
        "units": {"owned": 0, "available": 0}, "actions": {"owned": 0, "available": 0},
        "badges": {"owned": 0, "available": 0}, "rewards": {"new": 0, "owned": 0, "available": 0},
        "gameXp": {"owned": 0, "threshold": 6000, "breakdown": {"actions": [], "challenges": 0}},
        "challengesCompleted": 0}, "club-title")


async def playsession(request: web.Request) -> web.Response:
    """Connect's play session for a running game: just acknowledge (POST start, PUT refresh, DELETE end)."""
    if request.method == "DELETE":
        return await no_content(request)
    stamp = {"POST": "createdAt", "PUT": "updatedAt"}[request.method]
    request[HOW] = "playsession"
    return web.json_response({"expiresAt": iso_now(330), stamp: iso_now()}, status=201 if request.method == "POST" else 200)


def add_routes(app: web.Application) -> None:
    pid = "{profile_id}"
    r = app.router
    # game
    r.add_get("/v1/profiles/me/populations", populations)
    r.add_put("/v1/profiles/me/populations/data", populations)
    r.add_get(f"/v1/profiles/{pid}/actions", actions)
    r.add_get(f"/v1/profiles/{pid}/rewards", _ok({"rewards": []}, "rewards"))
    r.add_get("/v2/profiles/me/friends", _ok({"friends": []}, "friends"))
    r.add_get("/v3/profiles", profiles_lookup)
    r.add_get("/v1/profiles/me/news", _ok({"news": []}, "news"))
    r.add_get("/v1/profiles/connections", _ok({"connections": []}, "connections"))
    r.add_post("/v1/profiles/me/remotelog", remotelog)
    r.add_post("/v3/profiles/me/events", _ok({}, "events"))
    r.add_post("/v5/spaces/{space}/serviceEvents", _ok({}, "service-events"))
    r.add_get("/ugc/v1/", _ok([], "ugc"))
    r.add_route("*", "/ugc/v1/{ugc_id}/{action}", no_content)
    r.add_route("*", "/v2/connections/{tail:.*}", _ok({}, "notifications"))
    # Ubisoft Connect
    r.add_get("/v3/users/me", users_me)
    r.add_get("/v3/users/me/profiles", users_me_profiles)
    r.add_get("/v3/users/{user_id}", user_info)           # after /v3/users/me
    r.add_get("/v3/users/{user_id}/policies", _ok({"isAcceptanceMandatory": False,
                                                   "displayValues": {"reacceptanceReason": None},
                                                   "hasAcceptedLegalOptins": True}, "policies"))
    r.add_get("/v1/users/{user_id}/subscriptions", _ok({"subscriptions": []}, "subscriptions"))
    r.add_get("/v1/users/onlineStatuses", online_statuses)
    r.add_get("/v2/profiles/me/iplocation", iplocation)
    r.add_get(f"/v3/profiles/{pid}/friends", _ok(lambda: {"groupId": str(uuid.uuid4()), "friends": []}, "friends"))
    r.add_get(f"/v3/profiles/{pid}/global/harboursocial/friends/core/enriched",
              _ok(lambda: {"groupId": str(uuid.uuid4()), "friends": []}, "friends"))
    r.add_get(f"/v1/profiles/{pid}/blocks", _ok(lambda: {"groupId": str(uuid.uuid4()), "profiles": []}, "blocks"))
    r.add_get(f"/v1/profiles/{pid}/connections", _ok({"connections": []}, "connections"))
    r.add_get(f"/v1/profiles/{pid}/avatars", _ok({"type": "default", "imageVariations": []}, "avatars"))
    r.add_get("/v1/profiles/me/gamesplayed", _ok({"gamesPlayed": []}, "games-played"))
    r.add_post("/v1/profiles/me/uplay/graphql", _ok([{"data": {"games": []}}], "graphql"))
    r.add_get(f"/v1/profiles/{pid}/gamerecommendations", _ok({"gameRecommendations": []}, "recommendations"))
    r.add_get(f"/v1/profiles/{pid}/statsCard", _ok({"Statscards": []}, "stats-card"))
    r.add_get(f"/v2/profiles/{pid}/news", _ok({"pagination": {"total": 0, "count": 0, "offset": 0, "limit": 20},
                                                "news": []}, "news"))
    r.add_get("/v1/profiles/me/channels", _ok({"channels": [], "nextToken": ""}, "channels"))
    r.add_get("/v1/profiles/me/global/ubiconnect/entitlement/api/entitlements", _ok({"entitlements": []}, "entitlements"))
    r.add_get(f"/v1/profiles/{pid}/richpresences/subscriptions", _ok({}, "richpresence"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/challenges/api", _ok({"challenges": []}, "connect-challenges"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/rewards/api", _ok({"rewards": []}, "connect-rewards"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/economy/api/units",
              _for_player(lambda pid: {"profileId": pid, "units": 0}, "units"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/economy/api/metaprogression",
              _for_player(lambda pid: {"profileId": pid, "level": 1, "xp": 0}, "metaprogression"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/economy/api/ubiplussubscriptions",
              _ok({"ubiPlusSubscriptions": []}, "ubiplus"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/economy/api/ubiplusprogressions",
              _for_player(lambda pid: {"profileId": pid, "level": "0", "levelUpAt": None,
                                              "subscriptionDurationInMonths": 0}, "ubiplus"))
    r.add_get(f"/v1/profiles/{pid}/global/ubiconnect/contentGating/api/eligibility",
              _for_player(lambda pid: {"profileId": pid, "isEligible": True}, "eligibility"))
    r.add_get(f"/v2/profiles/{pid}/club/progression/title", club_title)
    r.add_route("*", f"/v1/profiles/{pid}/global/ubiconnect/playsession/api/sessions", playsession)
    r.add_post(f"/v1/profiles/{pid}/playerAchievements/registrations/steam",
               _for_player(lambda pid: {"spaceId": SPACE_ID, "profileId": pid,
                                               "status": "notapplicable"}, "achievements-registration"))
    for path in ("/v2/applications/{app}/global/sre/opentelemetry/api/{kind}",
                 "/v1/applications/{app}/global/sre/opentelemetry/anon/{kind}",
                 "/v1/spaces/{space}/global/sre/opentelemetry/api/{kind}"):
        r.add_post(path, _ok({"partialSuccess": {}}, "telemetry"))
