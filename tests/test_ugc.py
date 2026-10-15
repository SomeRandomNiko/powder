"""Steep's UGC service (photos, trails) and the entities with files (saved replays, friends' trails,
ranked ghosts): created, uploaded, listed, liked, downloaded. Made-up content."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from powder.server.services import leaderboards
from powder.server.services.stats import StatEvent
from powder.server.transport.https.app import build_app
from powder.server.transport.nex.rmc_codec import V_STRING

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
    """Seen live: the JPEG and a thumbnail come inline (datas, Thumbnail; base64); they are files, never part
    of the item."""
    import base64
    r = await client.post("/ugc/v1/", headers=OLYMPUS, json={
        "typeId": 2, "worldId": WORLD, "metadata": {"worldId": WORLD}, "tags": ["Ski", "Selfie"],
        "datas": base64.b64encode(b"\xff\xd8JPEG").decode(), "Thumbnail": base64.b64encode(b"\xff\xd8SMALL").decode()})
    item = await r.json()
    assert "datas" not in item and "Thumbnail" not in item and item["tags"] == ["Ski", "Selfie"]
    urls = item["links"]["urls"]
    assert await (await client.get(urls["Data"].split(".com", 1)[1], headers=OLYMPUS)).read() == b"\xff\xd8JPEG"
    assert await (await client.get(urls["Thumbnail"].split(".com", 1)[1], headers=OLYMPUS)).read() == b"\xff\xd8SMALL"
    listed = await (await client.get(f"/ugc/v1/?algo=Steep&type_id=2&world_id={WORLD}", headers=OLYMPUS)).json()
    assert not {"datas", "Thumbnail"} & set(listed[0])
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


async def test_a_ranked_ghost_is_offered_on_the_leaderboard(client, services, player):
    """A ranked run's ghost (a BEST_GHOST_* entity with its file) is the best's associated_replay,
    which other players download; once the game deletes the entity the board says "none" again."""
    ubi = {"Host": "public-ubiservices.ubi.com"}
    body = {"type": "BEST_GHOST_CHALLENGE_PUBLIC_ALLTIME", "name": "ScimitarEntity",
            "tags": ["TRAIL", "WORLD_10af88fa9e4", "SANDBOX_A", "BEST_GHOST"],
            "obj": {"leaderboard": "public_challenge_ascending_ranked_ldb", "challenge": "Steep_A09",
                    "score": 58250}, "extendedStorage": {"provider": "s3", "fileExtension": ""}}
    ghost = await (await client.post(f"/v2/profiles/{player}/entities", headers=ubi, json=body)).json()
    path = ghost["extendedStorage"]["putUrl"].split(".com", 1)[1]
    assert (await client.put(path, data=b"GHOST", headers=ubi)).status == 200
    services.stats.apply_events(player, [StatEvent("ascending_public_challenge_ranked_score_event", {
        "challenge_id_to_use": (V_STRING, "Steep_A09"), "replay": (V_STRING, ghost["entityId"]),
        "score": (V_STRING, "58250")})])
    board = "public_challenge_ascending_ranked_ldb:Steep_A09:infinite"
    (row,) = leaderboards.ranks(services.repos, board, 1, 1).rows
    assert row.replay == ghost["entityId"]
    read = await (await client.get(f"/v2/profiles/entities/{row.replay}", headers=ubi)).json()
    assert await (await client.get(read["extendedStorage"]["getUrl"].split(".com", 1)[1], headers=ubi)).read() == b"GHOST"
    await client.delete(f"/v2/profiles/entities/{ghost['entityId']}", headers=ubi)
    assert leaderboards.ranks(services.repos, board, 1, 1).rows[0].replay == "none"


async def test_friends_trails_by_world(client, services, player):
    """The friends' trails come in one request for several players, filtered by the world's tags."""
    ubi = {"Host": "public-ubiservices.ubi.com"}
    for world in ("WORLD_10af88fa9e4", "WORLD_7a954c6bd0"):
        services.entities.create({"type": "FAVORITE_CHALLENGE", "tags": ["TRAIL", world, "SANDBOX_A"],
                                  "obj": {"world": world}}, player)
    q = f"profileIds={player}&type=FAVORITE_CHALLENGE&tags=TRAIL,WORLD_7a954c6bd0,SANDBOX_A"
    found = (await (await client.get(f"/v2/profiles/entities?{q}", headers=ubi)).json())["entities"]
    assert [e["obj"]["world"] for e in found] == ["WORLD_7a954c6bd0"]


async def test_only_the_owner_hides_or_deletes(client, services):
    services.accounts.register("host@example.com", "correct horse", "")        # takes over the requester
    other = services.accounts.register("guest@example.com", "correct horse", "Guest")
    item = services.repos.ugc.create("00000000-0000-4000-8000-000000000001", 2, WORLD, other.profile_id, {}, [], {})
    assert (await client.put(f"/ugc/v1/{item['ugc_id']}/hide", headers=OLYMPUS)).status == 204
    assert (await client.delete(f"/ugc/v1/{item['ugc_id']}", headers=OLYMPUS)).status == 204
    assert services.repos.ugc.get(item["ugc_id"])["hidden"] is False          # not ours: untouched
