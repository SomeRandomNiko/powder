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


async def test_a_photo_as_the_game_uploads_it(client, services):
    """Seen live: the JPEG comes inline (datas, base64); it is a file, never part of the listed item."""
    import base64
    r = await client.post("/ugc/v1/", headers=OLYMPUS, json={
        "typeId": 2, "worldId": WORLD, "metadata": {"worldId": WORLD}, "tags": ["Ski", "Selfie"],
        "datas": base64.b64encode(b"\xff\xd8JPEG").decode()})
    item = await r.json()
    assert "datas" not in item and item["tags"] == ["Ski", "Selfie"]
    for url in item["links"]["urls"].values():                     # Data and Thumbnail: the photo
        assert await (await client.get(url.split(".com", 1)[1], headers=OLYMPUS)).read() == b"\xff\xd8JPEG"
    listed = await (await client.get(f"/ugc/v1/?algo=Steep&type_id=2&world_id={WORLD}", headers=OLYMPUS)).json()
    assert "datas" not in listed[0]
    assert (await client.delete(f"/ugc/v1/{item['id']}", headers=OLYMPUS)).status == 204
    assert (await client.put(f"/ugc/v1/{item['id']}/hide", headers=OLYMPUS)).status == 204    # as the game does
    assert (await client.get(item["links"]["urls"]["Data"].split(".com", 1)[1], headers=OLYMPUS)).status == 404


async def test_a_saved_replay_gets_its_file_stored(client, services):
    """Saving a replay: an entity asking for extended storage gets a putUrl; reads get getUrl / headUrl."""
    ubi = {"Host": "public-ubiservices.ubi.com"}
    body = {"type": "FAVORITE_REPLAY", "name": "DefaultEntityName", "tags": ["TRAIL", "REPLAY"],
            "obj": {"savename": "TrailReport_1"}, "extendedStorage": {"provider": "s3", "fileExtension": ""}}
    created = await (await client.post("/v2/profiles/entities", headers=ubi, json=body)).json()
    storage = created["extendedStorage"]
    assert storage["provider"] == "s3" and storage["putUrl"].startswith("https://public-ubiservices.ubi.com/")
    path = storage["putUrl"].split(".com", 1)[1]
    assert (await client.put(path, data=b"REPLAYDATA", headers=ubi)).status == 200
    read = await (await client.get(f"/v2/profiles/entities/{created['entityId']}", headers=ubi)).json()
    assert "putUrl" not in read["extendedStorage"] and read["extendedStorage"]["getUrl"] == storage["putUrl"]
    assert await (await client.get(path, headers=ubi)).read() == b"REPLAYDATA"
    await client.delete(f"/v2/profiles/entities/{created['entityId']}", headers=ubi)
    assert (await client.get(path, headers=ubi)).status == 404


async def test_only_the_owner_hides_or_deletes(client, services):
    services.accounts.register("host@example.com", "correct horse", "")        # takes over the requester
    other = services.accounts.register("guest@example.com", "correct horse", "Guest")
    item = services.repos.ugc.create("00000000-0000-4000-8000-000000000001", 2, WORLD, other.profile_id, {}, [], {})
    assert (await client.put(f"/ugc/v1/{item['ugc_id']}/hide", headers=OLYMPUS)).status == 204
    assert (await client.delete(f"/ugc/v1/{item['ugc_id']}", headers=OLYMPUS)).status == 204
    assert services.repos.ugc.get(item["ugc_id"])["hidden"] is False          # not ours: untouched
