"""Player-related UbiServices endpoints answered from the player record (no recordings): the game's and
Ubisoft Connect's profile, social, news and telemetry calls. Most only need a well-formed answer: empty
lists for news and other people's content; neutral values for account details. Friends: every other player of
the server (services/friends.py).
"""
from __future__ import annotations

import datetime
import logging
import uuid

from aiohttp import web

from ...constants import APP_ID, SPACE_ID
from ...services import friends
from ...util import iso_now
from .keys import HOW, SERVICES, SESSION
from .routes_ubi import json_response

log = logging.getLogger("powder.https")


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


async def space_entities(request: web.Request) -> web.Response:
    """Game-wide entities: none. Player-made trail challenges are not kept. The world's matchmaking map
    (TITLE_MATCHMAKING_MAP) stays empty on purpose: with no entity the game matchmakes without a map
    (MatchmakingCellsService goes on), while an entity whose map file it can't download switches its
    matchmaking off for RETRY_MATCHMAKING_MAP_DOWNLOAD_SECONDS (30 min). The map files are not ours."""
    return json_response(request, {"entities": []}, "space-entities")


def _ok(payload, how):
    async def handler(request: web.Request) -> web.Response:
        return json_response(request, payload() if callable(payload) else payload, how)
    return handler


async def graphql(request: web.Request) -> web.Response:
    """Ubisoft Connect's UI queries (library, game pages, achievements) go to one GraphQL endpoint, as a
    batch: one result per operation, in order. Achievement queries are answered from the database and
    the pack; the others with no games (as before). Each operation's full query is logged once."""
    try:
        batch = await request.json()
    except ValueError:
        batch = []
    ops = batch if isinstance(batch, list) else [batch]
    results = []
    for op in ops:
        op = op if isinstance(op, dict) else {}
        name = op.get("operationName")
        query = " ".join(str(op.get("query", "")).split())
        log.info("graphql %s vars=%s query=%s", name, sorted((op.get("variables") or {}).keys()),
                 query if name not in _logged_queries else "(as above)")
        _logged_queries.add(name)
        answer = GRAPHQL_ANSWERS.get(name)
        results.append(answer(request, op.get("variables") or {}) if answer else {"data": {"games": []}})
    return json_response(request, results if isinstance(batch, list) else results[0], "graphql")


_logged_queries: set = set()                   # each operation's full query text is logged once
STEEP_ACHIEVEMENTS = "STP:PC"                  # the product key Connect uses for Steep's achievements


def _achievement_game(request: web.Request, variables: dict, connection: dict) -> dict:
    """{data: {game: {id, viewer: {meta: {id, achievements: connection}}}}} for Steep's space."""
    space = variables.get("spaceId") or ""
    me = _me(request) or ""
    return {"data": {"game": {
        "id": space, "__typename": "Game",
        "viewer": {"__typename": "GameUserEdge", "meta": {
            "id": f"{me}-{space}", "__typename": "UserGameMeta",
            "achievements": {**connection, "__typename": "AchievementConnection"}}}}}}


def _achievements(request: web.Request, variables: dict) -> tuple[dict, dict]:
    """(names, earned) of the player's achievements in this game; empty for games other than Steep."""
    if (variables.get("spaceId") or "").lower() != SPACE_ID:
        return {}, {}
    services, me = _services(request), _me(request)
    earned = services.repos.achievements.all(me, STEEP_ACHIEVEMENTS) if me else {}
    return services.assets.achievement_names(), earned


def gql_achievements_count(request: web.Request, variables: dict) -> dict:
    names, _ = _achievements(request, variables)
    return _achievement_game(request, variables, {"totalCount": len(names)})


def gql_achievements(request: web.Request, variables: dict) -> dict:
    """Every achievement with its title, description, icon (served by this server) and whether and when
    the player earned it."""
    names, earned = _achievements(request, variables)
    space, me = variables.get("spaceId") or "", _me(request) or ""
    host = request.headers.get("Host") or request.host
    nodes = []
    for ach_id, (title, description) in names.items():
        ts = earned.get(ach_id)
        nodes.append({
            "id": f"{space}-{ach_id}", "achievementId": ach_id, "title": title, "description": description,
            "icon": f"https://{host}/_powder/achievements/{ach_id}.png", "__typename": "Achievement",
            "viewer": {"__typename": "AchievementUserEdge", "meta": {
                "id": f"{me}-{space}-{ach_id}", "__typename": "UserAchievementMeta", "isCompleted": ts is not None,
                "completionDate": datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z") if ts is not None else None}}})
    return _achievement_game(request, variables, {
        "totalCount": len(nodes), "completedCount": sum(1 for n in nodes if n["viewer"]["meta"]["isCompleted"]),
        "nodes": nodes})


GRAPHQL_ANSWERS = {"GetAchievementsCount": gql_achievements_count, "GetAchievements": gql_achievements}


async def achievement_icon(request: web.Request) -> web.Response:
    """GET /_powder/achievements/{id}.png: an achievement's icon from the pack."""
    raw = request.match_info["ach_id"]
    icon = _services(request).assets.achievement_icon(int(raw)) if raw.isdigit() else None
    if icon is None:
        raise web.HTTPNotFound()
    request[HOW] = "achievement-icon"
    return web.Response(body=icon, content_type="image/png", headers={"Cache-Control": "max-age=86400"})


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
    # Every field of the real reply must be present (Ubisoft Connect drops the session otherwise), with
    # neutral values: no real email (reserved .invalid domain), no phone number, no names.
    return json_response(request, {
        "userId": p.user_id if p else _me(request), "username": name, "nameOnPlatform": name,
        "accountIssues": None, "communicationOptIn": False, "communicationThirdPartyOptIn": False,
        "country": cfg.country, "region": None, "dateCreated": p.created_at if p else iso_now(),
        "dateAnonymized": None, "dateOfBirth": "1990-01-01T00:00:00.0000000Z",
        "email": f"{name.lower()}@powder.invalid", "firstName": "", "gender": "", "lastName": "",
        "hasAcceptedLegalOptins": True, "preferredLanguage": "en",
        "status": {"ageVerification": "notVerified", "autoGeneratedUsername": False, "changeEmailPending": False,
                   "dateOfBirthApproximated": False, "generalStatus": "activated", "inactiveAccount": False,
                   "invalidEmail": False, "invalidPhone": False, "locked": False, "minor": False,
                   "missingRequiredInformation": False, "passwordUpdateRequired": False,
                   "pendingDeactivation": False, "phoneActivated": False, "phoneSanctioned": False,
                   "recoveringPassword": False, "reserved": False, "stolenAccount": False,
                   "suspiciousActivity": False, "targetDeactivationDate": None, "testAccount": False},
        "phone": {"countryCallingCode": "", "number": ""}, "accountType": "Ubisoft", "ageGroup": "adult"},
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
    now = friends.online_profiles(services)
    online = {p.user_id.lower() for p in services.repos.players.all() if p.profile_id in now}
    return json_response(request, {"onlineStatuses": [
        {"connections": [], "onlineStatus": "online" if u.lower() in online else "offline", "userId": u}
        for u in ids]}, "online-statuses")


def _friends(request: web.Request, pid: str | None) -> list:
    return friends.friends_of(_services(request), pid)


async def game_friends(request: web.Request) -> web.Response:
    """The game's friends list (its social tab): every other player of the server (services/friends.py)."""
    since = iso_now()
    return json_response(request, {"friends": [
        {"pid": f.profile_id, "nameOnPlatform": f.name, "state": "Friends", "lastModified": since}
        for f in _friends(request, _me(request))]}, "friends")


async def connect_friends(request: web.Request) -> web.Response:
    """Ubisoft Connect's friends list; the enriched one (harboursocial) also has each one's online status."""
    since, enriched = iso_now(), request.path.endswith("/enriched")
    online = friends.online_profiles(_services(request)) if enriched else set()
    out = []
    for f in _friends(request, _pid(request)):
        entry = {"isFavorite": False, "lastModified": since, "nickname": "", "pid": f.profile_id, "state": "Friends"}
        if enriched:
            entry["onlineStatus"] = "online" if f.profile_id in online else "offline"
        else:
            entry["friendshipDate"] = since
        out.append(entry)
    return json_response(request, {"groupId": str(uuid.uuid4()), "friends": out}, "friends")


# what the game's connection asks to be told (as recorded); the game only checks that a friend has one
STEEP_MESSAGE_TYPES = ["STEEP_LIVEREPLAY", "STEEP_SPECTATOR", "STEEP_MAINTENANCE", "US_APPLICATION_CONFIGURATION_UPDATE",
                       "US_NOTIFICATION_MAINTENANCE", "US_CLIENT_SECONDARY_STORE_UPDATE", "CLUB_BADGE_ACQUIRED",
                       "CLUB_CHALLENGE_COMPLETED", "CLUB_CHALLENGE_BANKED", "CLUB_CHALLENGE_PARTICIPATION",
                       "CLUB_CHALLENGE_THRESHOLD_REACHED", "CLUB_PROGRESSION_REACHED", "CLUB_REWARD_PURCHASED"]


async def profile_connections(request: web.Request) -> web.Response:
    """GET /v1/profiles/connections?profileIds=..&applications=<Steep>: which friends are in Steep now (the game
    asks every minute; only a friend with a connection is "online" in its social tab and can be invited)."""
    wanted = [p.lower() for p in request.query.get("profileIds", "").split(",") if p]
    apps = [a.lower() for a in request.query.get("applications", "").split(",") if a]
    if apps and APP_ID not in apps:
        return json_response(request, {"connections": []}, "connections")
    playing, now = friends.in_game(_services(request)), iso_now()
    return json_response(request, {"connections": [
        {"profileId": pid, "connectionId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"powder-connection/{pid}")),
         "applicationId": APP_ID, "contactProtocol": "HTTP Post",
         "contactUrl": f"https://public-im-ubiservices.ubi.com/v2/connections/powder/8180/{pid}/notifications",
         "messageTypes": STEEP_MESSAGE_TYPES, "spaceIndependentMessageTypes": [], "obj": {}, "createdDate": now,
         "lastModifiedDate": now, "processId": "powder", "spaceIds": [SPACE_ID], "stagingSpaceId": SPACE_ID,
         "ubiLocaleCode": "en-US"}
        for pid in wanted if pid in playing]}, "connections")


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
    r.add_get("/v2/profiles/me/friends", game_friends)
    r.add_get("/v3/profiles", profiles_lookup)
    r.add_get("/v1/profiles/me/news", _ok({"news": []}, "news"))
    r.add_get("/v1/profiles/connections", profile_connections)
    r.add_post("/v1/profiles/me/remotelog", remotelog)
    r.add_post("/v3/profiles/me/events", _ok({}, "events"))
    r.add_post("/v5/spaces/{space}/serviceEvents", _ok({}, "service-events"))
    r.add_get("/ugc/v1/", _ok([], "ugc"))
    r.add_get("/v2/spaces/{space}/entities", space_entities)
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
    r.add_get(f"/v3/profiles/{pid}/friends", connect_friends)
    r.add_get(f"/v3/profiles/{pid}/global/harboursocial/friends/core/enriched", connect_friends)
    r.add_get(f"/v1/profiles/{pid}/blocks", _ok(lambda: {"groupId": str(uuid.uuid4()), "profiles": []}, "blocks"))
    r.add_get(f"/v1/profiles/{pid}/connections", _ok({"connections": []}, "connections"))
    r.add_get(f"/v1/profiles/{pid}/avatars", _ok({"type": "default", "imageVariations": []}, "avatars"))
    r.add_get("/v1/profiles/me/gamesplayed", _ok({"gamesPlayed": []}, "games-played"))
    r.add_post("/v1/profiles/me/uplay/graphql", graphql)
    r.add_get("/_powder/achievements/{ach_id}.png", achievement_icon)
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
