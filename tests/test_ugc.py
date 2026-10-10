"""Steep's UGC service (photos, trails): created, uploaded, listed, liked, downloaded. Made-up content."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from powder.server.transport.https.app import build_app

OLYMPUS = {"Host": "steep-prod-pc.steep.olympus.ubi.com"}
WORLD = 1146631465444


@pytest.fixture
async def client(services, player):
    c = TestClient(TestServer(build_app(services, nex_public_key="TESTKEY")))
    await c.start_server()
    yield c
    await c.close()


async def test_a_photo_round_trip(client, services):
    r = await client.post("/ugc/v1/", headers=OLYMPUS, json={"typeId": 2, "worldId": WORLD, "tags": ["1080p"],
                                                             "metadata": {"worldId": WORLD, "TimeOfDay": 11}})
    item = await r.json()
    assert item["typeId"] == 2 and item["worldId"] == WORLD and item["metadata"]["TimeOfDay"] == 11
    data_url = item["links"]["urls"]["Data"]
    assert data_url.startswith("https://steep-prod-pc.steep.olympus.ubi.com/_powder/ugc/")
    path = data_url.split(".com", 1)[1]
    assert (await client.put(path, data=b"JPEGDATA", headers=OLYMPUS)).status == 200
    assert (await client.put(path + "_thumbnail", data=b"THUMB", headers=OLYMPUS)).status == 200
    listed = await (await client.get(f"/ugc/v1/?algo=Steep&type_id=2&world_id={WORLD}", headers=OLYMPUS)).json()
    assert [i["id"] for i in listed] == [item["id"]]
    assert await (await client.get(f"/ugc/v1/?algo=Steep&type_id=7&world_id={WORLD}", headers=OLYMPUS)).json() == []
    assert (await client.put(f"/ugc/v1/{item['id']}/like", headers=OLYMPUS)).status == 204
    assert (await client.put(f"/ugc/v1/{item['id']}/download", headers=OLYMPUS)).status == 204
    again = await (await client.get(f"/ugc/v1/{item['id']}", headers=OLYMPUS)).json()
    assert (again["likeCounter"], again["liked"], again["downloadCounter"]) == (1, True, 1)
    assert await (await client.get(path, headers=OLYMPUS)).read() == b"JPEGDATA"
    assert (await client.delete(f"/ugc/v1/{item['id']}/like", headers=OLYMPUS)).status == 204
    assert (await client.get("/_powder/ugc/not-an-item", headers=OLYMPUS)).status == 404


async def test_only_the_owner_hides_or_deletes(client, services):
    services.accounts.register("host@example.com", "correct horse", "")        # takes over the requester
    other = services.accounts.register("guest@example.com", "correct horse", "Guest")
    item = services.repos.ugc.create("00000000-0000-4000-8000-000000000001", 2, WORLD, other.profile_id, {}, [], {})
    assert (await client.put(f"/ugc/v1/{item['ugc_id']}/hide", headers=OLYMPUS)).status == 204
    assert (await client.delete(f"/ugc/v1/{item['ugc_id']}", headers=OLYMPUS)).status == 204
    assert services.repos.ugc.get(item["ugc_id"])["hidden"] is False          # not ours: untouched
