"""The server without a game-data pack: the replies it builds itself (powder/server/builtin/)."""
import dataclasses
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from powder.server.builtin import products, sdk
from powder.server.constants import APP_ID, CONNECT_APP_ID, SPACE_ID
from powder.server.services import Assets, Services
from powder.server.transport.demux import ownership, pb  # noqa: F401  (registers the handler)
from powder.server.transport.demux.services import ServiceContext, answer
from powder.server.transport.https.app import build_app

UBI = {"Host": syn.UBI}


@pytest.fixture
def nopack(config, tmp_path):
    cfg = dataclasses.replace(config, server=dataclasses.replace(config.server, gamedata_dir=tmp_path / "none"))
    services = Services.open(cfg)
    assert services.assets.empty
    yield services
    services.db.close()


@pytest.fixture
async def client(nopack):
    c = TestClient(TestServer(build_app(nopack, nex_public_key="TESTKEY")))
    await c.start_server()
    yield c
    await c.close()


def _yaml(entry: bytes) -> str:
    return pb.first(pb.decode(entry), 17, b"").decode()


def test_owned_games_list_steep_for_both_versions(nopack):
    req = pb.encode([(1, [(1, 7), (2, [])])])
    reply = answer(ServiceContext(nopack, None), "ownership_service", req)
    body = pb.decode(pb.first(pb.decode(pb.first(pb.decode(reply), 1)), 2))
    entries = [v for n, v, _ in pb.decode(pb.first(body, 2)) if n == 1]
    by_id = {pb.first(pb.decode(e), 1): e for e in entries}
    assert len(entries) == 62 and {3279, 3280} <= set(by_id)
    connect, steam = _yaml(by_id[3279]), _yaml(by_id[3280])
    assert "relative: steep.exe" in connect and "Installs\\3279\\InstallDir" in connect
    assert "steam_app_id: 460920" in steam and "name: Steam" in steam
    for text in (connect, steam):
        assert "cloud_saves: no" in text and f"app_id: {APP_ID}" in text and f"space_id: {SPACE_ID}" in text
        assert "achievements:" not in text                      # no definitions file without a pack


def test_owned_games_keep_their_ids_and_offer_cloud_saves_when_asked():
    entries = products.entries(cloud_saves=True, achievements_file="defs.zip")
    first = pb.decode(entries[0])
    assert pb.first(first, 1) == 3279 and [v for n, v, _ in first if n == 16][:2] == [4001, 4002]
    assert "cloud_saves: yes" in _yaml(entries[0]) and "achievements: defs.zip" in _yaml(entries[0])
    assert all(_yaml(e).startswith("version: 2.0") or not _yaml(e) for e in entries)


async def test_game_sdk_configuration_switches_its_features_on(client):
    r = await client.get(f"/v2/applications/{APP_ID}/configuration", headers=UBI)
    config = (await r.json())["configuration"]
    switches = {f["name"] for f in config["featuresSwitches"] if f["value"]}
    assert {"Populations", "NotificationWebsocket", "PrimaryStore", "Entities"} <= switches
    urls = {g["name"]: g["url"] for g in config["gatewayResources"]}
    assert urls["configs/events"] == "https://msr-{env}public-ubiservices.ubi.com/{version}/spaces/{spaceId}/configs/events"
    assert urls["websocket/notifications"].startswith("wss://{env}public-ws-ubiservices.ubi.com")
    assert config["platformConfig"]["applicationId"] == APP_ID and config["storm"] == {}
    r = await client.get(f"/v1/applications/{CONNECT_APP_ID}/configuration", headers=UBI)
    assert (await r.json())["configuration"]["platformConfig"]["uplayGameCode"] == "AVA"
    r = await client.get("/v1/applications/00000000-0000-0000-0000-000000000000/configuration", headers=UBI)
    assert await r.json() == {}                                  # someone else's application: unanswered


async def test_game_start_replies(client):
    r = await client.get(f"/v1/spaces/{SPACE_ID}/items/all?fields=lastModified,sha256,md5", headers=UBI)
    assert await r.json() == sdk_items_version()
    r = await client.get(f"/v1/spaces/{SPACE_ID}/configs/primarystore", headers=UBI)
    assert await r.json() == {"mappings": []}
    r = await client.get(f"/v4/spaces/{SPACE_ID}/configs/events", headers=UBI)
    assert await r.json() == {"types": []}
    r = await client.get("/v2/spaces/global/ubiconnect/challenges/api/legacy/empty", headers=UBI)
    assert await r.json() == []


def sdk_items_version():
    from powder.server.builtin.http import ITEMS_VERSION
    return ITEMS_VERSION


def test_sdk_services_are_unique():
    names = [g["name"] for g in sdk.gateway_resources()]
    assert len(names) == len(set(names)) == 56


async def test_overlay_settings_without_ubisoft_parts_but_cache_first(client):
    path = "/v4/spaces/global/ubiconnect/configcache/api/postauth?bundleEnv=prod&applications=a,b"
    r = await client.get(path, headers={**UBI, "If-None-Match": '"x"'})
    assert r.status == 304                                       # Connect's main window keeps its copy
    r = await client.get(path, headers=UBI)
    body = await r.json()
    parts = body["a"]["microApplications"]
    assert set(body) == {"a", "b"} and [p["appName"] for p in parts] == ["powder"]
    assert parts[0]["default"] and parts[0]["parents"][0]["parentAppName"] is None
    assert "cdn.ubisoft.com" not in json.dumps(body)
    r = await client.get("/_powder/overlay/powder.js", headers=UBI)
    script = await r.text()
    assert r.headers["Access-Control-Allow-Origin"] == "*" and r.content_type == "application/javascript"
    assert 'Powder.apiVersion = "1.1.0"' in script and '_export("default", Powder)' in script
