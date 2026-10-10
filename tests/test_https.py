"""HTTPS (UbiServices) app through aiohttp's test client, with the game's real hostnames."""
import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from powder.server.constants import APP_ID, CREDITS_ITEM, SPACE_ID
from powder.server.transport.https.app import build_app

UBI = {"Host": syn.UBI}


@pytest.fixture
async def client(services, player):            # the made-up player, imported: the default player
    c = TestClient(TestServer(build_app(services, nex_public_key="TESTKEY")))
    await c.start_server()
    yield c
    await c.close()


async def login(client) -> dict:
    r = await client.post("/v3/profiles/sessions", headers={**UBI, "Authorization": "uplaypc_v1 t=connect-jwe"},
                          json={})
    assert r.status == 200
    return await r.json()


async def test_login_issues_our_ticket_for_the_default_player(client, services):
    body = await login(client)
    assert body["profileId"] == syn.PLAYER and body["nameOnPlatform"] == syn.PLAYER_NAME
    assert body["spaceId"] == SPACE_ID and body["ticket"] != "recorded-ticket"
    assert body["ticket"].startswith("powder.") and services.sessions.by_token(body["ticket"])


CONNECT_APP = "f68a4bb5-608a-4ff2-8123-be8ef797e0a6"


@pytest.mark.parametrize("auth", ["rm_v1 t=remember-me", "Basic dXNlckBleGFtcGxlLmNvbTpzZWNyZXQ=", "ubi_v1 t=x"])
async def test_ubisoft_connect_login(client, services, auth):
    """Connect signs in with a remember-me ticket, email:password or a refresh; all are accepted, and it
    gets a Ubisoft-shaped ticket plus a new remember-me ticket."""
    r = await client.post("/v3/profiles/sessions", json={"rememberMe": True},
                          headers={"Ubi-AppId": CONNECT_APP, "Authorization": auth})
    assert r.status == 200
    body = await r.json()
    assert body["profileId"] == syn.PLAYER
    header = json.loads(base64.b64decode(body["ticket"].split(".")[0]))
    assert header["aid"] == CONNECT_APP and header["sid"] == body["sessionId"]
    assert body["rememberMeTicket"] and body["twoFactorAuthenticationTicket"] is None
    assert services.sessions.by_token(body["ticket"]).profile_id == syn.PLAYER


async def test_cors_for_connects_web_ui(client):
    origin = "https://connect.cdn.ubisoft.com"
    r = await client.options("/v3/profiles/sessions", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "ubi-appid,authorization"})
    assert r.status == 200
    assert r.headers["Access-Control-Allow-Origin"] == origin
    assert r.headers["Access-Control-Allow-Headers"] == "ubi-appid,authorization"
    r = await client.get("/v3/users/me", headers={"Origin": origin})
    assert r.headers["Access-Control-Allow-Origin"] == origin
    r = await client.get("/v3/users/me")                     # the game sends no Origin: untouched
    assert "Access-Control-Allow-Origin" not in r.headers


async def test_no_cloud_save_metadata(client):
    """Connect must never be offered a replayed (stale) cloud save."""
    r = await client.get(f"/v1/profiles/{syn.PLAYER}/entities?name=upcCloudSaveUplayId_3280")
    assert (await r.json()) == {"entities": []}


async def test_connect_internet_check(client):
    for path in ("/health_check/test_internet.txt?cacheBuster=1", "/orbit/uplay_launcher_2_0/test_internet.txt"):
        r = await client.get(path, headers={"Host": "static3.cdn.ubi.com"})
        assert r.status == 200 and await r.text() == "I AM ALIVE!"


async def test_rendezvous_points_at_our_nex_server(client):
    r = await client.get(f"/v1/spaces/{SPACE_ID}/sandboxes/STEEP_PC_TU7_A/rendezvous/configuration",
                         headers={"Host": "msr-public-ubiservices.ubi.com"})
    body = await r.json()
    assert body["public_key"] == "TESTKEY" and body["prudp_loadbalancer_url"].startswith("prudp:/address=")


async def test_purchase_round_trip(client):
    s = await login(client)
    auth = {**UBI, "Authorization": f"Ubi_v1 t={s['ticket']}", "Ubi-SessionId": s["sessionId"]}
    inv_url = f"/v1/profiles/{s['profileId']}/inventory"

    async def credits():
        items = (await (await client.get(inv_url + f"?spaceId={SPACE_ID}", headers=auth)).json())["items"]
        return next(i for i in items if i["itemId"] == CREDITS_ITEM)["quantity"]

    assert await credits() == syn.SEEDED_CREDITS
    r = await client.post(inv_url, headers=auth, json={"spaceId": SPACE_ID, "offerId": syn.SKI_A, "offerRevision": 6})
    assert r.status == 200
    reply = await r.json()
    assert reply["transactionId"]
    assert await credits() == syn.SEEDED_CREDITS - syn.SKI_A_PRICE

    r = await client.post(inv_url, headers=auth, json={"spaceId": SPACE_ID, "offerId": "nope"})
    assert r.status == 404 and (await r.json())["errorCode"]


async def test_offers_from_the_pack_catalogue(client, services):
    r = await client.get(syn.SKI_PAGE, headers=UBI)
    assert {o["offerId"] for o in (await r.json())["offers"]} == {syn.SKI_A, syn.SKI_B}
    r = await client.get(f"/v1/spaces/{SPACE_ID}/offers?tags=SKI,DELUXE&offset=0&limit=5", headers=UBI)
    assert [o["offerId"] for o in (await r.json())["offers"]] == [syn.SKI_B]


async def test_pack_fallback_and_unmatched(client):
    r = await client.get(syn.CONFIG_PATH, headers=UBI)
    assert r.status == 200 and await r.json() == {"recorded": True}
    r = await client.get("/v9/nothing/here", headers=UBI)
    assert r.status == 200 and await r.json() == {}


async def test_connect_ui_settings_never_overwrite_the_browser_cache(client):
    """Without a pack reply: 304 if Connect's browser has a copy, else an error (never an empty 200)."""
    path = "/v4/spaces/global/ubiconnect/configcache/api/preauth?bundleEnv=prod"
    r = await client.get(path, headers={**UBI, "If-None-Match": '"abc"'})
    assert r.status == 304
    r = await client.get(path, headers=UBI)
    assert r.status == 503
    r = await client.get("/v1/spaces/x/parameters?parameterGroups=ubiConnect-deeplinkConfig", headers=UBI)
    assert r.status == 503


async def test_builtin_sdk_configuration_for_the_game(client):
    r = await client.get(f"/v1/applications/{APP_ID}/configuration", headers=UBI)
    config = (await r.json())["configuration"]
    assert config["platformConfig"]["applicationId"] == APP_ID and config["platformConfig"]["spaceId"] == SPACE_ID


async def test_websocket_hello_and_server_push(client, services):
    s = await login(client)
    ws = await client.ws_connect("/v2/websocket?NotificationTypes=US_CONNECTION_ESTABLISHED",
                                 headers={"Host": "public-ws-ubiservices.ubi.com",
                                          "Authorization": f"Ubi_v1 t={s['ticket']}"})
    hello = json.loads((await ws.receive()).data)
    assert hello["notificationType"] == "US_CONNECTION_ESTABLISHED" and hello["content"]["connectionId"]
    assert await services.sessions.push_ws(s["profileId"], "TEST_NOTIFICATION", {"x": 1}) == 1
    pushed = json.loads((await ws.receive()).data)
    assert pushed["notificationType"] == "TEST_NOTIFICATION"
    await ws.close()


async def test_personal_endpoints_are_generated_not_replayed(client):
    """Account details, friends and Club actions come from the player record: nothing of the recorded
    account (email, friends, action progress) is ever served."""
    me = await (await client.get("/v3/users/me", headers=UBI)).json()
    assert me["nameOnPlatform"] == syn.PLAYER_NAME and me["email"].endswith("@powder.invalid")
    assert me["email"] != syn.PLAYER_EMAIL and me["phone"]["number"] == ""
    friends = await (await client.get(f"/v3/profiles/{syn.PLAYER}/friends", headers=UBI)).json()
    assert friends["friends"] == []
    assert (await (await client.get("/v2/profiles/me/friends", headers=UBI)).json()) == {"friends": []}
    actions = (await (await client.get(f"/v1/profiles/{syn.PLAYER}/actions", headers=UBI)).json())["actions"]
    assert [a["id"] for a in actions] == [syn.ACTION["id"]] and actions[0]["value"] == 0
    looked_up = await (await client.get(f"/v3/profiles?profileIds={syn.PLAYER},{syn.FRIEND}", headers=UBI)).json()
    assert [p["profileId"] for p in looked_up["profiles"]] == [syn.PLAYER]     # only players we know
    assert (await (await client.get("/ugc/v1/?algo=Steep", headers={"Host": "steep-prod-pc.steep.olympus.ubi.com"})).json()) == []


async def test_connect_achievement_view(client, services):
    """Ubisoft Connect's UI asks for achievements over GraphQL (batched): titles, icons and the player's
    unlocks come from the pack and the database."""
    ticket = (await login(client))["ticket"]
    auth = {**UBI, "Authorization": f"Ubi_v1 t={ticket}"}
    batch = [{"operationName": "GetAchievementsCount", "variables": {"spaceId": SPACE_ID, "productId": 3279}},
             {"operationName": "GetAchievements", "variables": {"spaceId": SPACE_ID, "productId": 3279}},
             {"operationName": "GetFriends", "variables": {"spaceId": SPACE_ID}}]
    r = await client.post("/v1/profiles/me/uplay/graphql", headers=auth, json=batch)
    count, full, other = await r.json()
    assert count["data"]["game"]["viewer"]["meta"]["achievements"]["totalCount"] == len(syn.ACHIEVEMENT_NAMES)
    ach = full["data"]["game"]["viewer"]["meta"]["achievements"]
    assert ach["totalCount"] == 4 and ach["completedCount"] == 3              # 1, 4, 15 imported; 30 open
    by_id = {n["achievementId"]: n for n in ach["nodes"]}
    assert by_id[1]["title"] == "First steps" and by_id[1]["viewer"]["meta"]["isCompleted"]
    assert by_id[1]["viewer"]["meta"]["completionDate"] == "2017-07-14T02:40:00.000Z"
    assert not by_id[30]["viewer"]["meta"]["isCompleted"] and by_id[30]["viewer"]["meta"]["completionDate"] is None
    assert other == {"data": {"games": []}}                                   # one result per operation
    icon = await client.get(by_id[1]["icon"].split(syn.UBI, 1)[1], headers=UBI)
    assert icon.status == 200 and await icon.read() == syn.ICON
    # another game: nothing; a single (unbatched) operation gets a single result
    r = await client.post("/v1/profiles/me/uplay/graphql", headers=auth, json={
        "operationName": "GetAchievements", "variables": {"spaceId": "00000000-0000-0000-0000-000000000000"}})
    assert (await r.json())["data"]["game"]["viewer"]["meta"]["achievements"]["nodes"] == []


async def test_matchmaking_map_for_the_world(client):
    """Without a matchmaking map the game never opens a match ticket."""
    r = await client.get(f"/v2/spaces/{SPACE_ID}/entities?type=TITLE_MATCHMAKING_MAP&tags=WORLD_10af88fa9e4"
                         "&offset=0&limit=10", headers=UBI)
    (entity,) = (await r.json())["entities"]
    assert entity["type"] == "TITLE_MATCHMAKING_MAP" and entity["tags"] == ["WORLD_10af88fa9e4"]
    assert entity["obj"] == {"version": 3} and entity["spaceId"] == SPACE_ID
    r = await client.get(f"/v2/spaces/{SPACE_ID}/entities?tags=TRAIL,ACTIVATED&offset=0&limit=20", headers=UBI)
    assert (await r.json())["entities"] == []
