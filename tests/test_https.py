"""HTTPS (UbiServices) app through aiohttp's test client, with the game's real hostnames."""
import base64
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from steepserver.constants import CREDITS_ITEM, SPACE_ID
from steepserver.transport.https.app import build_app

UBI = {"Host": syn.UBI}


@pytest.fixture
async def client(services):
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
    assert body["ticket"].startswith("steep.") and services.sessions.by_token(body["ticket"])


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


async def test_offers_recorded_page_replayed_and_unseen_query_from_catalogue(client, services):
    recorded = services.assets.http.lookup("GET", syn.UBI, syn.SKI_PAGE)[0]
    r = await client.get(syn.SKI_PAGE, headers=UBI)
    assert await r.read() == recorded.body
    r = await client.get(f"/v1/spaces/{SPACE_ID}/offers?tags=SKI,DELUXE&offset=0&limit=5", headers=UBI)
    assert [o["offerId"] for o in (await r.json())["offers"]] == [syn.SKI_B]


async def test_recorded_fallback_and_unmatched(client):
    r = await client.get(syn.CONFIG_PATH, headers=UBI)
    assert r.status == 200 and await r.json() == {"recorded": True}
    r = await client.get("/v9/nothing/here", headers=UBI)
    assert r.status == 200 and await r.json() == {}


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
